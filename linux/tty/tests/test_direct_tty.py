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

    def test_expected_text_does_not_authorize_a_pty_deletion(self):
        # main's Expect is useful evidence for a real editable target, not a
        # capability to delete text held by an arbitrary child application.
        with self.assertRaisesRegex(app.UnsafeEffect, "DeleteBefore"):
            app.effect_bytes({"consumed": True, "commits": [
                {"deleteBefore": 0, "text": "not yet written"},
                {"deleteBefore": 2, "text": "replace", "expect": "日本"}], "view": None})

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

    def test_current_native_event_abi_is_callable_without_ibus_or_fcitx(self):
        if not shutil.which("cc"):
            self.skipTest("C compiler unavailable")
        import json
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            mozc = t / "mozc" / "meltype_mozc_helper"
            mozc.parent.mkdir()
            mozc.write_bytes(b"test")
            empty = {"version": 1, "state": {}, "result": {"consumed": True, "commits": [], "view": None}}
            reply = {"version": 1, "state": {}, "result": {"consumed": True,
                     "commits": [{"removeBefore": None, "moveCaretBack": 0, "text": "日本語"}], "view": None}}
            c = t / "stub.c"
            c.write_text(r'''#include <stdlib.h>
#include <string.h>
#include <stdio.h>
int meltype_init(const char* directory, const void* check) { (void)directory; (void)check; return 1; }
void* meltype_ime_create(void) { return malloc(1); }
void meltype_ime_destroy(void* p) { free(p); }
void meltype_free(void* p) { free(p); }
void meltype_shutdown(void) {
  const char* marker = getenv("MELTYPE_TEST_SHUTDOWN_MARKER");
  if (!marker) return;
  FILE* file = fopen(marker, "a");
  if (file) { fputs("shutdown\n", file); fclose(file); }
}
void* meltype_ime_handle(void* p, const char* event) {
  (void)p;
  if (!strstr(event, "\"canRemove\": false") || !strstr(event, "\"instance\": \"tty\"")) return NULL;
  const char *json = strstr(event, "JapaneseModeSelected") ? EMPTY_JSON : REPLY_JSON;
  char *r = malloc(strlen(json)+1); strcpy(r,json); return r;
}
'''.replace('EMPTY_JSON', json.dumps(json.dumps(empty, ensure_ascii=False), ensure_ascii=False))
   .replace('REPLY_JSON', json.dumps(json.dumps(reply, ensure_ascii=False), ensure_ascii=False)))
            so = t / "MeltypeNative.so"
            subprocess.run(["cc", "-shared", "-fPIC", "-o", str(so), str(c)], check=True)
            native = app.NativeSession(so, mozc)
            try:
                self.assertEqual(app.InputBridge(native).feed(b"n"), "日本語".encode())
            finally:
                native.close()
            marker = t / "shutdown.txt"
            # Two closed hosts and a later new host share the process runtime.
            # Only process exit may flush learning/stop the conversion servers.
            probe = """import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from meltype_tty import NativeSession
for _ in range(3):
    native = NativeSession(Path(sys.argv[2]), Path(sys.argv[3]))
    native.close()
    assert not Path(sys.argv[4]).exists(), 'session close shut down the shared runtime'
"""
            environment = {**os.environ, "MELTYPE_TEST_SHUTDOWN_MARKER": str(marker)}
            subprocess.run([__import__('sys').executable, "-c", probe, str(SCRIPT.parent), str(so), str(mozc), str(marker)],
                           env=environment, check=True)
            self.assertEqual(marker.read_text(), "shutdown\n")

    def test_host_candidate_selection_and_meaning_reach_the_existing_panel(self):
        wire = {"consumed": True, "commits": [], "view": {"text": "日本語", "candidates": {
                "items": ["日本語", "ニホンゴ"], "notes": [None, "根拠"],
                "selected": 1, "selectedMeaning": "意味"}}}
        result = app.session_result(wire)
        self.assertEqual(app.effect_bytes(result, b"n"), b"")
        self.assertEqual(result["view"]["candidates"], ["日本語", "ニホンゴ"])
        self.assertEqual(result["view"]["selectedIndex"], 1)
        self.assertEqual(result["view"]["notes"], [None, "根拠"])
        self.assertEqual(result["view"]["meaning"], "意味")

    def test_host_reconversion_and_caret_edits_stop_before_any_child_effect(self):
        for unsupported in [{"removeBefore": "日本"}, {"moveCaretBack": 1}, {"deleteBefore": 2}]:
            with self.subTest(edit=unsupported), self.assertRaises(app.UnsafeEffect):
                app.session_result({"consumed": True, "commits": [
                    {"text": "safe", "removeBefore": None, "moveCaretBack": 0},
                    {"text": "replace", "removeBefore": None, "moveCaretBack": 0, **unsupported}], "view": None})


if __name__ == "__main__":
    unittest.main()
