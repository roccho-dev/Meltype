#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Required same-package real NativeAOT/Mozc gate. No C shim or model network.

Usage: python3 tty/tests/native_tty.py /absolute/Meltype-linux
Missing real artifacts fail; they are not skipped or replaced with test doubles.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import subprocess
import select
import tty
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import meltype_tty as m

PACKAGE = Path(sys.argv.pop(1)).resolve() if len(sys.argv) > 1 else None
if PACKAGE is None:
    raise SystemExit('required: real same-build Meltype-linux directory')
LIBRARY = PACKAGE/'libMeltypeNative.so'
MOZC = PACKAGE/'mozc/meltype_mozc_helper'
if not LIBRARY.is_file() or not MOZC.is_file() or not os.access(MOZC, os.X_OK):
    raise SystemExit('real native library and executable Mozc helper required')

class NativeProof(unittest.TestCase):
    def setUp(self):
        self.native = m.NativeSession(LIBRARY, MOZC)
        self.bridge = m.InputBridge(self.native)
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)

    def tearDown(self):
        self.native.close()
        os.close(self.master)
        os.close(self.slave)

    def send(self, raw):
        effect=self.bridge.feed(raw)
        if effect:
            m.write_all(self.master, effect)
            self.assertTrue(select.select([self.slave], [], [], 1)[0])
            observed=os.read(self.slave, len(effect))
            self.assertEqual(observed, effect)
            return observed
        self.assertEqual(select.select([self.slave], [], [], 0)[0], [])
        return b''

    def test_english_has_one_explicit_command_enter(self):
        self.assertEqual(self.send(b'git status\r\r'), b'git status\r')

    def test_unconfirmed_japanese_stays_out_of_child(self):
        self.assertEqual(self.send(b'nihongo '), b'')
        self.assertEqual(self.bridge.view['text'], '日本語')
        self.assertEqual(self.bridge.view['candidates'][0], '日本語')

    def test_candidate_arrow_selects_before_commit(self):
        self.send(b'nihongo ')
        self.assertEqual(self.send(b'\x1b[B'), b'')
        self.assertEqual(self.bridge.view['selectedIndex'], 1)
        self.assertEqual(self.send(b'\r'), 'ニホンゴ'.encode())
        self.assertIsNone(self.bridge.view)

    def test_f10_restores_original_latin_spelling(self):
        self.send(b'nihongo')
        self.assertEqual(self.send(b'\x1b[21~'), b'')
        self.assertEqual(self.send(b'\r'), b'nihongo')

    def test_mixed_sentence_uses_existing_conversion(self):
        self.assertEqual(self.send(b'kyouhagoogledekensaku \r'), '今日はgoogleで検索'.encode())

    def test_cancel_does_not_write_or_execute(self):
        self.send(b'nihongo ')
        for _ in range(2):
            self.assertEqual(self.send(b'\x1b'), b'')
            self.assertEqual(self.bridge.flush_escape(), b'')
        self.assertIsNone(self.bridge.view)
        self.assertEqual(self.send(b'\r'), b'\r')

    def test_paste_follows_real_pending_composition(self):
        self.send(b'nihongo')
        raw=m.PASTE_BEGIN+'後続'.encode()+m.PASTE_END
        self.assertEqual(self.send(raw), '日本語'.encode()+raw)
        self.assertIsNone(self.bridge.view)

    def test_kitty_encoded_keys_share_the_native_conversion(self):
        sequence=b''.join(f'\x1b[{c};1:1u\x1b[{c};1:3u'.encode() for c in b'nihongo ')
        self.assertEqual(self.send(sequence),b'')
        self.assertEqual(self.bridge.view['text'],'日本語')
        self.assertEqual(self.send(b'\x1b[13u'),'日本語'.encode())

    def test_terminal_report_does_not_commit_real_composition(self):
        self.send(b'nihongo')
        self.assertEqual(self.send(b'\x1b[12;34R'), b'\x1b[12;34R')
        self.assertEqual(self.bridge.view['text'], '日本語')
        self.assertEqual(self.send(b'\r'), '日本語'.encode())

    def test_ctrl_uipo_switch_real_composition_without_writing(self):
        # Windows Meltype's Ctrl+P/O/I/U: full-width, half-width, katakana, hiragana.
        self.send(b'aiueo')
        for raw,expected in [(b'\x10','ａｉｕｅｏ'),(b'\x0f','aiueo'),(b'\x1b[105;5u','アイウエオ'),(b'\x15','あいうえお')]:
            self.assertEqual(self.send(raw), b'')
            self.assertEqual(self.bridge.view['text'], expected)
        self.assertEqual(self.send(b'\r'), 'あいうえお'.encode())

    def test_direct_input_converts_only_the_word_just_typed(self):
        # #16: keys reach the child at once; Ctrl+Space erases the echoed word and composes it.
        self.bridge = m.InputBridge(self.native, direct=True)
        self.assertEqual(self.send(b'hello nihongo'), b'hello nihongo')
        self.bridge.observe(b'hello nihongo')  # The echo a line editor would print.
        self.assertEqual(self.send(b'\x00'), b'\x7f'*7)
        self.assertEqual(self.bridge.view['text'], '日本語')
        self.assertEqual(self.send(b'\r'), '日本語'.encode())
        self.assertIsNone(self.bridge.view)
        self.assertEqual(self.send(b'ls'), b'ls')

    def test_ctrl_without_composition_reaches_child(self):
        self.assertEqual(self.send(b'\x15\x10\x0f'), b'\x15\x10\x0f')
        self.assertIsNone(self.bridge.view)

if __name__=='__main__':
    names=unittest.defaultTestLoader.getTestCaseNames(NativeProof)
    if len(sys.argv)>1:
        case=sys.argv[1]
        if case not in names: raise SystemExit('unknown test case')
        with tempfile.TemporaryDirectory(prefix='meltype-native-proof-') as home:
            for key in ['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME']:
                os.environ[key]=str(Path(home)/key.lower())
                Path(os.environ[key]).mkdir()
            result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([NativeProof(case)]))
            sys.exit(0 if result.wasSuccessful() else 1)
    started=time.monotonic()
    failures=[]
    # Fresh process per case: Core caches paths and learns from candidate choices.
    # Cleaning files in a live process does not reset that in-memory state.
    for case in names:
        result=subprocess.run([sys.executable, str(Path(__file__).resolve()), str(PACKAGE), case],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30)
        print(result.stdout, end='')
        if result.returncode: failures.append(case)
    print(json.dumps({'gate':'real-native-bridge-posixpty','tests':len(names),'failures':failures,
        'elapsed_seconds':round(time.monotonic()-started,3),
        'wrapper_sha256':hashlib.sha256((ROOT/'meltype_tty.py').read_bytes()).hexdigest(),
        'native_sha256':hashlib.sha256(LIBRARY.read_bytes()).hexdigest(),
        'mozc_sha256':hashlib.sha256(MOZC.read_bytes()).hexdigest(),
        'bcon':'NOT_RUN','visual_candidates':'NOT_RUN','typed_proposals':'NOT_CONNECTED'}))
    sys.exit(1 if failures else 0)
