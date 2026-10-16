#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental Direct TTY / PTY boundary for the existing Meltype NativeAOT core.

No Fcitx5, IBus, or SKK. This is a *transport proof*, not a production IME:
The Kitty image candidate overlay is opt-in via an existing CJK font.
The existing Proposal port is optional via an explicit local HTTP endpoint.
Surrounding-text editing remains unsupported without an observed capability.
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
import stat
import sys
import termios
import tty
import time
import errno
import re
from collections import deque
from pathlib import Path


class UnsafeEffect(RuntimeError):
    """No write is allowed when Meltype's effect cannot be represented safely."""


class NativeSession:
    """Thin binding to Meltype's existing C ABI (same exports as Linux IBus)."""

    def __init__(self, library: Path, mozc: Path, proposal_url=None):
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
        self.lib.meltype_set_can_delete.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.meltype_set_can_delete.restype = None
        if self.lib.meltype_init_mozc(os.fsencode(mozc.resolve()), None) != 1:
            raise UnsafeEffect("Meltype Mozc could not initialize")
        self.session = self.lib.meltype_create()
        if not self.session:
            raise UnsafeEffect("Meltype native session unavailable")
        self.lib.meltype_set_direct(self.session, 0)
        # The existing main API lets the Core avoid unsupported retroactive
        # edits. Expect metadata alone is not proof of the child editor's text.
        self.lib.meltype_set_can_delete(self.session, 0)
        self.proposal = None
        self.transport = None
        self.target = ""
        if proposal_url is not None:
            try:
                from proposal_transport import ProposalTransport
                self.lib.meltype_proposals_create.argtypes = [ctypes.c_void_p]
                self.lib.meltype_proposals_create.restype = ctypes.c_void_p
                self.lib.meltype_proposals_destroy.argtypes = [ctypes.c_void_p]
                self.lib.meltype_proposals_destroy.restype = None
                self.lib.meltype_proposals_target.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
                self.lib.meltype_proposals_target.restype = None
                self.lib.meltype_proposals_request.argtypes = [ctypes.c_void_p]
                self.lib.meltype_proposals_request.restype = ctypes.c_void_p
                self.lib.meltype_proposals_receive.argtypes = [ctypes.c_void_p, ctypes.c_longlong, ctypes.c_char_p]
                self.lib.meltype_proposals_receive.restype = ctypes.c_void_p
                self.lib.meltype_proposals_selected.argtypes = [ctypes.c_void_p]
                self.lib.meltype_proposals_selected.restype = ctypes.c_void_p
                self.proposal = self.lib.meltype_proposals_create(self.session)
                if not self.proposal: raise UnsafeEffect("native Proposal port unavailable")
                self.transport = ProposalTransport(proposal_url)
            except BaseException:
                self.close()
                raise

    def bind_target(self, token):
        self.target = token
        self.focus(True)

    def focus(self, active):
        if not active and self.transport is not None: self.transport.cancel()
        if self.proposal:
            self.lib.meltype_proposals_target(self.proposal, self.target.encode() if active else b"")

    def poll_proposals(self):
        if not self.proposal: return None
        pointer = self.lib.meltype_proposals_request(self.proposal)
        if pointer: self.transport.submit(self._result(pointer))
        response = self.transport.take()
        if response is None: return None
        generation, text = response
        return self._result(self.lib.meltype_proposals_receive(self.proposal, generation, text.encode()))

    def take_selection(self):
        if not self.proposal: return None
        pointer = self.lib.meltype_proposals_selected(self.proposal)
        return self._result(pointer) if pointer else None

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

    def cancel(self):
        # Escape cancels preedit without guessing edits to child-owned text.
        for _ in range(2):
            result = self.key(0x1B, 0)
            if result.get("commits"): raise UnsafeEffect("cancellation attempted a text effect")
            if result.get("view") is None: return
        raise UnsafeEffect("pending composition did not cancel")

    def key(self, vk, char, shift=0):
        # Native cancels the candidate generation; cancel its queued acquisition
        # too. A request already sent cannot be recalled, only made inert.
        if vk == 0x1B and self.transport is not None: self.transport.cancel()
        result = self._result(self.lib.meltype_handle_key(self.session, vk, char, shift, None, None))
        if result.get("view") is None and self.transport is not None: self.transport.cancel()
        return result

    def commit(self):
        if self.transport is not None: self.transport.cancel()
        return self._result(self.lib.meltype_commit(self.session))

    def close(self):
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        if self.proposal:
            self.lib.meltype_proposals_destroy(self.proposal)
            self.proposal = None
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
# Ctrl+U/I/P/O while composing: the Core's existing hiragana / katakana / full-width /
# half-width switch (F6/F7/F9/F10). It takes the letter's virtual key with Control.
CONTROL = 2  # meltype_handle_key modifiers: Shift = 1, Control = 2
CTRL_CONVERT = {ord("u"): 0x55, ord("i"): 0x49, ord("p"): 0x50, ord("o"): 0x4F}
# Legacy Ctrl bytes. 0x09 is also Tab, so Ctrl+I only counts when Kitty keys tell it apart.
LEGACY_CTRL = {0x15: 0x55, 0x10: 0x50, 0x0F: 0x4F}
# Direct Input (the default): keys go to the child one at a time, so its completion and history keep
# working. Ctrl+Space converts the ASCII word just typed: as many BackSpaces as its letters (0x7f, what
# bcon's BackSpace key sends), then the same letters into the Core's composition (upstream #679).
CONVERT_LEGACY = 0x00  # Ctrl+Space; Kitty CSI-u and modifyOtherKeys report Space (32) with Ctrl.
ERASE = b"\x7f"
# How a line editor echoes one BackSpace at the end of its line (Readline: "\b" then clear to end).
ERASE_ECHOES = (b"\x08\x1b[K", b"\x08\x1b[0K", b"\x08 \x08")
WORD = re.compile(rb"[A-Za-z][A-Za-z'-]*\Z")
SHADOW_LIMIT = 256
PASTE_BEGIN = b"\x1b[200~"
PASTE_END = b"\x1b[201~"


class InputBridge:
    """One instance per child PTY. No global active-pane or IME state."""

    def __init__(self, native, direct=False):
        self.native = native
        self.direct = direct
        self.shadow = bytearray()    # printable ASCII just typed in Direct Input, as the child has it
        self.unechoed = bytearray()  # the part of it whose echo has not come back from the child yet
        self.erasing = 0             # BackSpaces whose echo has not come back yet
        self.source = None           # the word a conversion took back, until it is committed
        self.outbox = bytearray()
        self.view = None
        self.escape = bytearray()
        self.paste = bytearray()
        self.pasting = False
        self.escape_started = None
        self.paused = False
        self.deferred = bytearray()
        self.on_report = None
        self.consumed_keys = set()
        self.capture_permitted = True
        self.focused = True
        self.capturing = True

    def set_capture(self, allowed):
        # The child app's ECHO capability and the terminal focus are
        # independent. ECHO returning must not reactivate an unfocused target.
        self.capture_permitted = bool(allowed)
        self._refresh_capture()

    def set_focus(self, focused):
        self.focused = bool(focused)
        self._refresh_capture()

    def _refresh_capture(self):
        allowed = self.capture_permitted and self.focused
        if allowed == self.capturing: return
        self.capturing = allowed
        self.forget()
        focus = getattr(self.native, "focus", None)
        if focus is not None: focus(allowed)
        if not allowed:
            if self.view is not None:
                self.native.cancel()
                # A word taken back for conversion goes back as it was.
                if self.source is not None: self.outbox.extend(self.source)
            self.source = None
            self.view = None
            self.deferred.clear()
            self.consumed_keys.clear()

    def _defer(self, raw):
        if len(self.deferred)+len(raw)>1024*1024:
            raise UnsafeEffect("input exceeded the bounded cursor-wait buffer")
        self.deferred.extend(raw)
        return b""

    def _key(self, code, original, shifted=False):
        if not self.capturing: return original
        if self.paused: return self._defer(original)
        # Only while composing; otherwise the Ctrl byte keeps its meaning for the child.
        if code in LEGACY_CTRL and self.view is not None:
            return self._native_key(LEGACY_CTRL[code], 0, CONTROL, original)
        if self.direct and self.view is None:
            return self._convert(original) if code == CONVERT_LEGACY else self._direct(code, original)
        return self._compose(code, original, shifted)

    def forget(self):
        # Anything that may have moved the caret or rewritten the line: convert nothing rather than guess.
        self.shadow.clear()
        self.unechoed.clear()
        self.erasing = 0

    def _direct(self, code, original):
        # The child gets the key as it is. Only a run of printable ASCII is remembered, and only while
        # the child's echo shows exactly that run (see observe).
        if code in (0x7F, 0x08) and self.shadow and not self.unechoed:
            self.shadow.pop()
            self.erasing += 1
        elif 32 <= code < 127 and len(self.shadow) < SHADOW_LIMIT:
            self.shadow.append(code)
            self.unechoed.append(code)
        else:
            self.forget()
        return original

    def _convert(self, original):
        self.source = None
        match = WORD.search(bytes(self.shadow)) if not self.unechoed and not self.erasing else None
        self.forget()
        if match is None: return original  # Nothing known to convert: the key stays the child's.
        word = match[0]
        out = bytearray(ERASE * len(word))
        for code in word:
            out.extend(self._compose(code, bytes([code])))
        if self.view is not None: self.source = word
        return bytes(out)

    def observe(self, data):
        """Child output. Only the echo of the remembered keys keeps them; anything else forgets them."""
        index = 0
        while index < len(data) and (self.unechoed or self.erasing):
            if self.unechoed and data[index] == self.unechoed[0]:
                del self.unechoed[0]
                index += 1
                continue
            erase = next((e for e in ERASE_ECHOES if data.startswith(e, index)), None) if self.erasing else None
            if erase is None: break
            self.erasing -= 1
            index += len(erase)
        if index < len(data): self.forget()

    def drain(self):
        out = bytes(self.outbox)
        self.outbox.clear()
        return out

    def _compose(self, code, original, shifted=False):
        if 65 <= code <= 90:
            vk, char, shift = code, code, 1
        elif 97 <= code <= 122:
            vk, char, shift = code - 32, code - 32 if shifted else code, int(shifted)
        elif 48 <= code <= 57:
            vk, char, shift = code, code, 0
        elif code in SPECIAL:
            vk, char, shift = SPECIAL[code], (code if code == 32 else 0), 0
        elif 32 < code < 127:
            vk, char, shift = SYMBOL.get(code, 0x07), code, int(shifted)
        else:
            return self._passthrough(original)
        return self._native_key(vk, char, shift, original)

    def _step(self, byte):
        return self._key(byte, bytes([byte]))

    def _native_key(self, vk, char, modifiers, original):
        result = self.native.key(vk, char, modifiers)
        effect = effect_bytes(result, original)
        self.view = result.get("view")
        if self.view is None and self.source is not None:
            # Cancelled with Esc, nothing committed: the word taken back goes back as it was.
            if vk == 0x1B and not result["commits"]: effect = self.source + effect
            self.source = None
        return effect

    def _passthrough(self, raw):
        if not self.capturing: return raw
        if self.paused: return self._defer(raw)
        if self.view is not None:
            effect = effect_bytes(self.native.commit())
            self.view = None
            self.source = None
            return effect + raw
        self.forget()
        return raw

    def _sequence(self, raw):
        if raw in (b"\x1b[I", b"\x1b[O"):
            self.set_focus(raw == b"\x1b[I")
            return raw  # Focus changes never commit or replay prior preedit.
        # Cursor/device/graphics replies are not user commands. Never commit
        # composition just because a terminal answered the child's query.
        if re.fullmatch(rb"\x1b\[(?:\??[0-9;]*[Rcnt]|\?[0-9;]+u)", raw) or raw.startswith((b"\x1b]", b"\x1bP", b"\x1b_", b"\x1b^", b"\x1bX")):
            if self.on_report is not None and self.on_report(raw): return b""
            return raw
        if not self.capturing: return raw
        if self.paused: return self._defer(raw)
        # Kitty CSI-u and modifyOtherKeys use the same virtual-key boundary.
        # Original protocol bytes survive whenever Meltype did not consume a key.
        kitty = re.fullmatch(rb"\x1b\[([0-9]+)(?::([0-9]*)(?::[0-9]*)?)?(?:;([0-9]*)(?::([123]))?)?(?:;([0-9:]+))?u", raw)
        other = re.fullmatch(rb"\x1b\[27;([0-9]+);([0-9]+)~", raw)
        arrow = re.fullmatch(rb"\x1b(?:\[|O)(?:1(?:;([0-9]*)(?::([123]))?)?)?([ABCDHF])", raw)
        function = re.fullmatch(rb"\x1b\[([0-9]+)(?:;([0-9]*)(?::([123]))?)?~", raw)
        key_id = None
        event, modifiers, code, vk = 1, 0, None, None
        caps_shift = None
        if kitty:
            code=int(kitty[1]);key_id=("u",code)
            modifiers=int(kitty[3] or b"1")-1;event=int(kitty[4] or b"1")
            # Multiple associated codepoints are preserved intact for the child.
            if kitty[5] and b":" in kitty[5]: return self._passthrough(raw)
            if kitty[5]: code=int(kitty[5])
            elif modifiers & 1 and kitty[2]: code=int(kitty[2])
            elif 97 <= code <= 122: caps_shift=bool(modifiers & 1) ^ bool(modifiers & 64)
        elif other:
            modifiers=int(other[1])-1;code=int(other[2]);key_id=("other",code)
        elif arrow:
            vk=ARROWS[arrow[3]];key_id=("arrow",vk)
            modifiers=int(arrow[1] or b"1")-1;event=int(arrow[2] or b"1")
        elif function:
            code_number=int(function[1]);key_id=("function",code_number)
            vk=FUNCTIONS.get(code_number)
            modifiers=int(function[2] or b"1")-1;event=int(function[3] or b"1")
        if key_id is None: return self._passthrough(raw)
        if event == 3:
            if key_id in self.consumed_keys:
                self.consumed_keys.discard(key_id)
                return b""
            return raw
        if code in CTRL_CONVERT and modifiers >= 0 and modifiers & ~192 == 4 and self.view is not None:
            effect=self._native_key(CTRL_CONVERT[code], 0, CONTROL, raw)  # Ctrl alone, composing.
        elif code == 32 and modifiers >= 0 and modifiers & ~192 == 4 and self.direct and self.view is None:
            effect=self._convert(raw)  # Ctrl+Space in Direct Input.
        elif modifiers < 0 or modifiers & ~193:
            return self._passthrough(raw)  # Preserve Ctrl/Alt/Super command meanings.
        elif code is not None and (code in SPECIAL or 32 <= code < 127):
            effect=self._key(code, raw, bool(modifiers & 1) if caps_shift is None else caps_shift)
        elif vk is not None and self.view is not None:
            effect=self._native_key(vk, 0, modifiers & 1, raw)
        else:
            return self._passthrough(raw)
        # Only suppress the release of a key whose press was actually consumed.
        if not effect.endswith(raw): self.consumed_keys.add(key_id)
        else: self.consumed_keys.discard(key_id)
        return effect

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
        return bytes(out) + self.drain()

    def expire(self, now=None):
        now = time.monotonic() if now is None else now
        limit = .06 if self.escape == b"\x1b" else 1.5
        if self.escape and self.escape_started is not None and now - self.escape_started >= limit:
            return self.flush_escape() + self.drain()
        return self.drain()

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


def capture_allowed(master, raw_text=False):
    """No-echo canonical input is private. Raw input needs explicit capability.

    A PTY cannot distinguish a raw password field from a text editor. The
    caller may authorize a known text application, never infer that from PID.
    """
    try: flags = termios.tcgetattr(master)[3]
    except OSError: return False
    return bool(flags & termios.ECHO) or bool(raw_text and not flags & termios.ICANON)


def validate_selection_fd(fd):
    """Explicit caller-owned append-only file; never a terminal or blocking pipe."""
    if fd is None: return
    if type(fd) is not int or fd < 3:
        raise ValueError("selection fd must be separate from terminal stdio")
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    if (not stat.S_ISREG(os.fstat(fd).st_mode) or
            flags & os.O_ACCMODE == os.O_RDONLY or not flags & os.O_APPEND):
        raise ValueError("selection fd must be a writable append-only regular file")
    os.set_inheritable(fd, False)


def publish_selection(native, fd):
    """Expose the existing one-shot selection, NOT a child/application receipt.

    Called after a complete child-PTY write. On observation-write failure the
    input may already have applied; stop without retrying that input.
    Disabled by default: no file, ordinary keystroke log, or provider call.
    """
    if fd is None: return
    selection = native.take_selection()
    if selection is None: return
    data = (json.dumps(selection, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > 1024 * 1024:
        raise UnsafeEffect("selection observation exceeds 1 MiB; input may have applied")
    try:
        write_all(fd, data)
    except OSError as error:
        raise UnsafeEffect("selection observation failed; input may have applied; no replay") from error


def run(argv, make_native, make_display=None, raw_text=False, selection_fd=None, direct=True):
    """Fork before NativeAOT, but exec only after initialization succeeds.

    The pipe gates only startup. No credentials, input replay, global daemon,
    process registry, or command rewriting. The child inherits the original TTY.
    """
    if not os.isatty(0) or not os.isatty(1):
        raise UnsafeEffect("requires an interactive TTY")
    validate_selection_fd(selection_fd)
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
            if selection_fd is not None: os.close(selection_fd)
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
    display = None
    status = None
    def interrupted(signum, _frame):
        raise InterruptedError(f"signal {signum}")
    try:
        # forkpty() does not guarantee FD_CLOEXEC. Native helpers do not own
        # this PTY master and must never inherit another input connection.
        os.set_inheritable(master, False)
        native = make_native()
        bridge = InputBridge(native, direct)
        bind = getattr(native, "bind_target", None)
        if bind is not None:
            stat = os.fstat(master)
            bind(f"pty:{stat.st_dev}:{stat.st_ino}:child:{pid}")
        if make_display is not None:
            display = make_display()
            bridge.on_report = display.reply

        def refresh():
            poll = getattr(native, "poll_proposals", None)
            result = poll() if poll is not None else None
            if result is not None:
                if effect_bytes(result): raise UnsafeEffect("provider response attempted an unselected effect")
                bridge.view = result.get("view")
            if display is None: return
            write_all(1, display.update(bridge.view))
            bridge.paused = bool(bridge.view is not None and (display.cursor is None or not display.boundary.safe))

        def accept(data):
            bridge.set_capture(capture_allowed(master, raw_text))
            pending = deque(data)
            while pending:
                effect = bridge.feed(bytes([pending.popleft()]))
                if effect:
                    write_all(master, effect)
                    publish_selection(native, selection_fd)
                refresh()
                if not bridge.paused and bridge.deferred:
                    pending.extendleft(reversed(bridge.deferred))
                    bridge.deferred.clear()

        def resize(*_):
            bridge.forget()  # The child may redraw its line.
            _size_to_child(master)
            if display is not None:
                size = os.get_terminal_size(0)
                display.resize(size.lines, size.columns)
        for sig in (signal.SIGWINCH, signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, resize if sig == signal.SIGWINCH else interrupted)
        _size_to_child(master)
        # Preserve keystrokes already queued while NativeAOT initialized.
        tty.setraw(0, termios.TCSANOW)
        typeahead=display.initialize(lambda data: write_all(1,data)) if display is not None else b""
        os.write(writer, b"1")
        os.close(writer)
        writer = None
        if typeahead: accept(typeahead)
        while True:
            # Process lifetime is independent of EOF: a cursor query can pause
            # master reads, and another process can retain a slave descriptor.
            # Stop native/provider input work once this one child has exited.
            found, child_status = os.waitpid(pid, os.WNOHANG)
            if found:
                status = child_status
                focus = getattr(native, "focus", None)
                if focus is not None: focus(False)
                # Deliver already-buffered final output without requesting a
                # new cursor or evaluating another candidate. Bound a retained
                # slave's output rather than waiting for it to close forever.
                for _ in range(16):
                    if not select.select([master], [], [], 0)[0]: break
                    try:
                        data = os.read(master, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO: raise
                        break
                    if not data: break
                    write_all(1, display.output(data) if display is not None else data)
                break
            # While our cursor query is pending, do not forward child output.
            # Thus a child DSR cannot be sent after ours but answered before it.
            readers = [0] if display is not None and display.pending else [0, master]
            ready, _, _ = select.select(readers, [], [], .02)
            bridge.set_capture(capture_allowed(master, raw_text))
            # The escape deadline is independent of child-output activity.
            effect = bridge.expire()
            if effect:
                write_all(master, effect)
            refresh()
            if master in ready:
                try:
                    data = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    break
                if not data:
                    break
                bridge.observe(data)
                write_all(1, display.output(data) if display is not None else data)
                refresh()
            if 0 in ready:
                data = os.read(0, 4096)
                if not data:
                    break
                accept(data)
    finally:
        # Restoration and reap are not skipped even if another cleanup fails.
        if writer is not None:
            os.close(writer)
        try:
            if display is not None: write_all(1, display.clear())
        finally:
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
                    if status is None: status = _reap(pid)
    return os.waitstatus_to_exitcode(status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--mozc", type=Path, required=True)
    parser.add_argument("--font", type=Path, help="existing CJK font for Kitty candidate images")
    parser.add_argument("--selection-fd", type=int, help="explicit append-only regular file descriptor for original selected proposals; not an effect receipt")
    parser.add_argument("--raw-text", action="store_true", help="authorize a known raw text application, not an unknown/password field")
    parser.add_argument("--compose", action="store_true", help="every key goes through a composition (no Direct Input)")
    parser.add_argument("--transport-proof", action="store_true", help="explicit nonvisual test mode only")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.font is None and not args.transport_proof:
        parser.error("--font is required; invisible composition is only allowed by --transport-proof")
    argv = args.command[1:] if args.command[:1] == ["--"] else args.command
    def make_display():
        from kitty_display import KittyDisplay
        size=os.get_terminal_size(0)
        return KittyDisplay(args.font, size.lines, size.columns)
    return run(argv or [os.environ.get("SHELL", "/bin/sh")],
               lambda: NativeSession(args.native, args.mozc, os.environ.get("MELTYPE_PROPOSAL_URL")),
               make_display if args.font is not None else None, raw_text=args.raw_text, selection_fd=args.selection_fd,
               direct=not args.compose)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (UnsafeEffect, OSError, ValueError) as error:
        print(f"meltype-tty: {error}", file=sys.stderr)
        sys.exit(1)
