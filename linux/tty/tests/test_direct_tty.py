# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded Direct TTY transport tests; no claim of real bcon or UX proof."""

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tty
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "meltype_tty.py"
spec = importlib.util.spec_from_file_location("meltype_tty", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class FakeNative:
    def __init__(self):
        self.calls = []
        self.responses = []
        self.commit_result = {"consumed": True, "commits": [], "view": None}

    def key(self, vk, char, shift=0):
        self.calls.append((vk, char, shift))
        return self.responses.pop(0) if self.responses else {"consumed": False, "commits": [], "view": None}

    def commit(self):
        return self.commit_result


class BridgeTests(unittest.TestCase):
    def test_direct_ascii_never_uses_fcitx_and_is_unchanged(self):
        native = FakeNative()
        proxy = app.InputBridge(native)
        self.assertEqual(proxy.feed(b"git status "), b"git status ")
        self.assertEqual(native.calls[0], (ord("G"), ord("g"), 0))
        self.assertEqual(len(native.calls), len(b"git status "))

    def test_consumed_japanese_commit_not_doubled(self):
        native = FakeNative()
        native.responses = [{"consumed": True, "commits": [{"deleteBefore": 0, "text": "日本語"}], "view": None}]
        self.assertEqual(app.InputBridge(native).feed(b"n"), "日本語".encode())

    def test_preedit_never_leaks_to_child(self):
        native = FakeNative()
        native.responses = [{"consumed": True, "commits": [], "view": {"text": "かな", "candidates": ["仮名"]}}]
        bridge = app.InputBridge(native)
        self.assertEqual(bridge.feed(b"k"), b"")
        self.assertEqual(bridge.view["text"], "かな")

    def test_refuse_backspace_reconversion_before_any_effect(self):
        native = FakeNative()
        native.responses = [{"consumed": True, "commits": [
            {"deleteBefore": 0, "text": "safe"},
            {"deleteBefore": 3, "text": "unsafe"}], "view": None}]
        with self.assertRaisesRegex(app.UnsafeEffect, "DeleteBefore"):
            app.InputBridge(native).feed(b"k")

    def test_newline_and_ansi_in_unconfirmed_commit_refused(self):
        for payload in ["rm -rf /\n", "\x1b[2J", "\x00"]:
            with self.subTest(payload=payload), self.assertRaises(app.UnsafeEffect):
                app.effect_bytes({"consumed": True, "commits": [{"deleteBefore": 0, "text": payload}], "view": None})

    def test_invalid_shape_refused(self):
        for bad in [None, {}, {"consumed": "true", "commits": []},
                    {"consumed": True, "commits": [{"deleteBefore": False, "text": "a"}]},
                    {"consumed": True, "commits": [], "view": "bad"}]:
            with self.subTest(bad=bad), self.assertRaises(app.UnsafeEffect):
                app.effect_bytes(bad, b"z")

    def test_arrows_and_alt_chords_do_not_turn_into_text(self):
        native = FakeNative()
        bridge = app.InputBridge(native)
        self.assertEqual(bridge.feed(b"\x1b["), b"")
        self.assertEqual(bridge.feed(b"A"), b"\x1b[A")
        self.assertEqual(bridge.feed(b"\x1bx"), b"\x1bx")
        self.assertEqual(native.calls, [])

    def test_escape_only_is_a_key_after_timeout(self):
        native = FakeNative()
        proxy = app.InputBridge(native)
        self.assertEqual(proxy.feed(b"\x1b"), b"")
        self.assertEqual(proxy.flush_escape(), b"\x1b")
        self.assertEqual(native.calls, [(0x1B, 0, 0)])

    def test_cancel_unsupported_control_sequence_no_partial_output(self):
        native = FakeNative()
        bridge = app.InputBridge(native)
        self.assertEqual(bridge.feed(b"\x1b[12"), b"")
        with self.assertRaisesRegex(app.UnsafeEffect, "incomplete terminal escape"):
            bridge.flush_escape()
        self.assertEqual(native.calls, [])

    def test_bracketed_paste_is_opaque_and_byte_exact(self):
        native = FakeNative()
        bridge = app.InputBridge(native)
        self.assertEqual(bridge.feed(b"\x1b[200~git"), b"")
        self.assertEqual(bridge.feed("日本".encode()), b"")
        self.assertEqual(bridge.feed(b"\x1b[201~"), b"\x1b[200~git" + "日本".encode() + b"\x1b[201~")
        self.assertEqual(native.calls, [])

    def test_paste_is_bounded(self):
        bridge = app.InputBridge(FakeNative())
        bridge.feed(app.PASTE_BEGIN)
        with self.assertRaisesRegex(app.UnsafeEffect, "1 MiB"):
            bridge.feed(b"x" * (1024 * 1024 + 1))

    def test_result_goes_only_to_its_bound_pty(self):
        a_master, a_slave = os.openpty()
        b_master, b_slave = os.openpty()
        try:
            tty.setraw(a_slave)
            tty.setraw(b_slave)
            data = app.effect_bytes({"consumed": True, "commits": [{"deleteBefore": 0, "text": "日本語"}], "view": None})
            os.write(a_master, data)
            self.assertEqual(os.read(a_slave, 32), data)
            import select
            self.assertEqual(select.select([b_slave], [], [], 0.03)[0], [])
        finally:
            for fd in [a_master, a_slave, b_master, b_slave]:
                os.close(fd)

    def test_existing_native_c_abi_is_callable_without_ibus_or_fcitx(self):
        if not shutil.which("cc"):
            self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            mozc = t / "mozc"
            mozc.write_bytes(b"test")
            c = t / "stub.c"
            c.write_text('''#include <stdlib.h>
#include <string.h>
int meltype_init_mozc(const char* h, const char* p) { (void)h; (void)p; return 1; }
void* meltype_create(void) { return malloc(1); }
void meltype_destroy(void* p) { free(p); }
void meltype_set_direct(void* p, int n) { (void)p; (void)n; }
void meltype_free(void* p) { free(p); }
void* meltype_handle_key(void* p, int vk, int ch, int m, const char* b, const char* a) {
  (void)p; (void)vk; (void)ch; (void)m; (void)b; (void)a;
  const char *json = "{\\\"consumed\\\":true,\\\"commits\\\":[{\\\"deleteBefore\\\":0,\\\"text\\\":\\\"日本語\\\"}],\\\"view\\\":null}";
  char *r = malloc(strlen(json)+1); strcpy(r,json); return r;
}
void* meltype_commit(void* p) { return meltype_handle_key(p,0,0,0,NULL,NULL); }
''')
            so = t / "MeltypeNative.so"
            subprocess.run(["cc", "-shared", "-fPIC", "-o", str(so), str(c)], check=True)
            native = app.NativeSession(so, mozc)
            try:
                self.assertEqual(app.InputBridge(native).feed(b"n"), "日本語".encode())
            finally:
                native.close()


if __name__ == "__main__":
    unittest.main()
