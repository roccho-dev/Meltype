#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental Direct TTY / PTY boundary for the existing Meltype NativeAOT core.

No Fcitx5, IBus, or SKK. This is a *transport proof*, not a production IME:
there is no preedit overlay, Proposal endpoint, or surrounding-text editing.
Real NativeAOT, Mozc and bound-PTY tests are separate from visual bcon proof.
Unsupported edits stop before writing to the child. Use only in a disposable PTY.
"""

import argparse
import ctypes
import fcntl
import json
import os
import pty
import select
import signal
import sys
import termios
import tty
import time
import errno
import re
from pathlib import Path


class UnsafeEffect(RuntimeError):
    """No write is allowed when Meltype's effect cannot be represented safely."""


class NativeSession:
    """Thin binding to Meltype's existing C ABI (same exports as Linux IBus)."""

    def __init__(self, library: Path, mozc: Path):
        if not library.is_file() or not mozc.is_file():
            raise FileNotFoundError("MeltypeNative.so and Mozc helper must both exist")
        self.lib = ctypes.CDLL(str(library.resolve()))
        self.lib.meltype_init_mozc.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.lib.meltype_init_mozc.restype = ctypes.c_int
        self.lib.meltype_create.restype = ctypes.c_void_p
        self.lib.meltype_destroy.argtypes = [ctypes.c_void_p]
        self.lib.meltype_handle_key.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p]
        self.lib.meltype_handle_key.restype = ctypes.c_void_p
        self.lib.meltype_commit.argtypes = [ctypes.c_void_p]
        self.lib.meltype_commit.restype = ctypes.c_void_p
        self.lib.meltype_free.argtypes = [ctypes.c_void_p]
        self.lib.meltype_set_direct.argtypes = [ctypes.c_void_p, ctypes.c_int]
        if self.lib.meltype_init_mozc(os.fsencode(mozc.resolve()), None) != 1:
            raise UnsafeEffect("Meltype Mozc could not initialize")
        self.session = self.lib.meltype_create()
        if not self.session:
            raise UnsafeEffect("Meltype native session unavailable")
        self.lib.meltype_set_direct(self.session, 0)

    def _result(self, pointer):
        if not pointer:
            raise UnsafeEffect("NativeAOT returned NULL")
        try:
            value = json.loads(ctypes.string_at(pointer).decode("utf-8"))
        except (ValueError, UnicodeError) as error:
            raise UnsafeEffect("invalid NativeAOT result") from error
        finally:
            self.lib.meltype_free(pointer)
        return value

    def key(self, vk, char, shift=0):
        return self._result(self.lib.meltype_handle_key(self.session, vk, char, shift, None, None))

    def commit(self):
        return self._result(self.lib.meltype_commit(self.session))

    def close(self):
        if self.session:
            self.lib.meltype_destroy(self.session)
            self.session = None


def effect_bytes(result, original=b""):
    """Atomic validation before any child-PTY effect; no fake backspace edits."""
    if not isinstance(result, dict) or type(result.get("consumed")) is not bool or not isinstance(result.get("commits"), list):
        raise UnsafeEffect("invalid SessionResult")
    if result.get("view") is not None and not isinstance(result["view"], dict):
        raise UnsafeEffect("invalid view")
    out = bytearray()
    for edit in result["commits"]:
        if not isinstance(edit, dict) or type(edit.get("deleteBefore")) is not int or not isinstance(edit.get("text"), str):
            raise UnsafeEffect("invalid TextEdit")
        if edit["deleteBefore"] != 0:
            raise UnsafeEffect("nonzero DeleteBefore requires an observed editable target")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in edit["text"]):
            raise UnsafeEffect("control characters in an unconfirmed terminal commit")
        try:
            out.extend(edit["text"].encode("utf-8"))
        except UnicodeEncodeError as error:
            raise UnsafeEffect("invalid Unicode in native commit") from error
        if len(out) > 1024 * 1024:
            raise UnsafeEffect("commit exceeded the bounded 1 MiB limit")
    if not result["consumed"]:
        out.extend(original)
    return bytes(out)


SPECIAL = {13: 0x0D, 10: 0x0D, 9: 0x09, 32: 0x20, 127: 0x08, 8: 0x08, 27: 0x1B}
SYMBOL = {ord(","): 0xBC, ord("."): 0xBE, ord("-"): 0xBD, ord("/"): 0xBF, ord("["): 0xDB, ord("]"): 0xDD}
ARROWS = {b"A": 0x26, b"B": 0x28, b"C": 0x27, b"D": 0x25, b"H": 0x24, b"F": 0x23}
FUNCTIONS = {17: 0x75, 18: 0x76, 19: 0x77, 20: 0x78, 21: 0x79}
PASTE_BEGIN = b"\x1b[200~"
PASTE_END = b"\x1b[201~"


class InputBridge:
    """One instance per child PTY. No global active-pane or IME state."""

    def __init__(self, native):
        self.native = native
        self.view = None
        self.escape = bytearray()
        self.paste = bytearray()
        self.pasting = False
        self.escape_started = None

    def _step(self, byte):
        if 65 <= byte <= 90:
            vk, char, shift = byte, byte, 1
        elif 97 <= byte <= 122:
            vk, char, shift = byte - 32, byte, 0
        elif 48 <= byte <= 57:
            vk, char, shift = byte, byte, 0
        elif byte in SPECIAL:
            vk, char, shift = SPECIAL[byte], (byte if byte == 32 else 0), 0
        elif 32 < byte < 127:
            vk, char, shift = SYMBOL.get(byte, 0x07), byte, 0
        else:
            return self._passthrough(bytes([byte]))
        result = self.native.key(vk, char, shift)
        effect = effect_bytes(result, bytes([byte]))
        self.view = result.get("view")
        return effect

    def _passthrough(self, raw):
        if self.view is not None:
            effect = effect_bytes(self.native.commit())
            self.view = None
            return effect + raw
        return raw

    def _sequence(self, raw):
        # Cursor/device/graphics replies are not user commands. Never commit
        # composition just because a terminal answered the child's query.
        if re.fullmatch(rb"\x1b\[\??[0-9;]*[Rcn]", raw) or raw.startswith((b"\x1b]", b"\x1bP", b"\x1b_", b"\x1b^")):
            return raw
        key = re.fullmatch(rb"\x1b(?:\[|O)(?:1;([2-8]))?([ABCDHF])", raw)
        function = re.fullmatch(rb"\x1b\[([0-9]+)~", raw)
        vk = ARROWS.get(key[2]) if key else FUNCTIONS.get(int(function[1])) if function else None
        if self.view is not None and vk is not None:
            modifiers = int(key[1] or b"1") - 1 if key else 0
            # Only Shift is representable by the current native binding.
            if modifiers & ~1:
                return self._passthrough(raw)
            result = self.native.key(vk, 0, modifiers)
            effect = effect_bytes(result, raw)
            self.view = result.get("view")
            return effect
        return self._passthrough(raw)

    def feed(self, data):
        out = bytearray()
        for byte in data:
            if self.pasting:
                self.paste.append(byte)
                if len(self.paste) > 1024 * 1024:
                    raise UnsafeEffect("paste exceeded the bounded 1 MiB limit")
                if self.paste.endswith(PASTE_END):
                    out.extend(self._passthrough(bytes(self.paste)))
                    self.paste.clear()
                    self.pasting = False
                continue
            if self.escape:
                self.escape.append(byte)
                string_sequence = self.escape[:2] in (b"\x1b]", b"\x1bP", b"\x1b_", b"\x1b^", b"\x1bX")
                if len(self.escape) > (1024 * 1024 if string_sequence else 64):
                    raise UnsafeEffect("unbounded terminal escape sequence")
                if string_sequence:
                    if self.escape.endswith(b"\x1b\\") or (self.escape[:2] == b"\x1b]" and byte == 7):
                        out.extend(self._sequence(bytes(self.escape)))
                        self.escape.clear()
                    continue
                if self.escape == b"\x1b[" or self.escape == b"\x1bO":
                    continue
                if len(self.escape) == 2 and byte not in (ord("["), ord("O")):
                    out.extend(self._passthrough(bytes(self.escape)))
                    self.escape.clear()
                    continue
                if len(self.escape) >= 3 and 0x40 <= byte <= 0x7E:
                    sequence = bytes(self.escape)
                    self.escape.clear()
                    if sequence == PASTE_BEGIN:
                        self.paste.extend(sequence)
                        self.pasting = True
                    else:
                        out.extend(self._sequence(sequence))
                continue
            if byte == 27:
                self.escape.append(byte)
                self.escape_started = time.monotonic()
            else:
                out.extend(self._step(byte))
        return bytes(out)

    def expire(self, now=None):
        now = time.monotonic() if now is None else now
        limit = .06 if self.escape == b"\x1b" else 1.5
        if self.escape and self.escape_started is not None and now - self.escape_started >= limit:
            return self.flush_escape()
        return b""

    def flush_escape(self):
        if not self.escape:
            return b""
        pending = bytes(self.escape)
        self.escape.clear()
        if pending != b"\x1b":
            raise UnsafeEffect("incomplete terminal escape sequence")
        return self._step(27)


def _size_to_child(master):
    try:
        fcntl.ioctl(master, termios.TIOCSWINSZ, fcntl.ioctl(0, termios.TIOCGWINSZ, b"\x00" * 8))
    except OSError:
        pass


def write_all(fd, value):
    offset = 0
    while offset < len(value):
        written = os.write(fd, value[offset:])
        if written <= 0:
            raise UnsafeEffect("PTY write did not make progress")
        offset += written


def _reap(pid):
    """Only this owned child, bounded waits, never retry a user input effect."""
    for sig in (None, signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
        try:
            found, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return 0
        if found:
            return status
        if sig is not None:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + .2
        while time.monotonic() < deadline:
            found, status = os.waitpid(pid, os.WNOHANG)
            if found:
                return status
            time.sleep(.005)
    raise UnsafeEffect("owned child did not exit after SIGKILL")


def run(argv, make_native):
    """Fork before NativeAOT, but exec only after initialization succeeds.

    The pipe gates only startup. No credentials, input replay, global daemon,
    process registry, or command rewriting. The child inherits the original TTY.
    """
    if not os.isatty(0) or not os.isatty(1):
        raise UnsafeEffect("requires an interactive TTY")
    original = termios.tcgetattr(0)
    reader, writer = os.pipe()
    try:
        pid, master = pty.fork()
    except BaseException:
        os.close(reader)
        os.close(writer)
        raise
    if pid == 0:
        try:
            os.close(writer)
            if os.read(reader, 1) != b"1":
                os._exit(125)
            os.close(reader)
            os.execvp(argv[0], argv)
        except BaseException:
            os._exit(127)
    os.close(reader)
    native = None
    old_handlers = {}
    status = 0
    def interrupted(signum, _frame):
        raise InterruptedError(f"signal {signum}")
    try:
        native = make_native()
        bridge = InputBridge(native)
        for sig in (signal.SIGWINCH, signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, (lambda *_: _size_to_child(master)) if sig == signal.SIGWINCH else interrupted)
        _size_to_child(master)
        tty.setraw(0)
        os.write(writer, b"1")
        os.close(writer)
        writer = None
        while True:
            ready, _, _ = select.select([0, master], [], [], .02)
            # The escape deadline is independent of child-output activity.
            effect = bridge.expire()
            if effect:
                write_all(master, effect)
            if master in ready:
                try:
                    data = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not data:
                    break
                write_all(1, data)
            if 0 in ready:
                data = os.read(0, 4096)
                if not data:
                    break
                effect = bridge.feed(data)
                if effect:
                    write_all(master, effect)
    finally:
        # Restoration and reap are not skipped even if another cleanup fails.
        if writer is not None:
            os.close(writer)
        try:
            termios.tcsetattr(0, termios.TCSANOW, original)
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
            os.close(master)
            try:
                if native is not None:
                    native.close()
            finally:
                status = _reap(pid)
    return os.waitstatus_to_exitcode(status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--mozc", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    return run(argv or [os.environ.get("SHELL", "/bin/sh")],
               lambda: NativeSession(args.native, args.mozc))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (UnsafeEffect, OSError) as error:
        print(f"meltype-tty: {error}", file=sys.stderr)
        sys.exit(1)
