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

    # #19: the Core's suggestions come from a second session that is always cancelled.
    def query(self, word):
        return {'input': word, 'state': {}, 'focus': {}}

    def check_source(self, propose):
        """Right candidates, the same each time, and the typing session's composition untouched."""
        self.send(b'nihongo')
        first, second = propose(self.query('nihongo')), propose(self.query('nihongo'))
        self.assertTrue(first, 'no candidates')
        self.assertEqual(first[0]['representation'], '日本語')
        self.assertEqual(set(first[0]), {'id', 'representation', 'meaning', 'evidence'})
        self.assertEqual([p['representation'] for p in second], [p['representation'] for p in first])
        self.assertEqual(self.bridge.view['text'], '日本語')
        self.assertEqual(self.send(b'\r'), '日本語'.encode())
        self.assertIsNone(self.bridge.view)

    def check_no_learning(self, propose):
        """No preview result commits anything, and a later conversion is ranked exactly as before."""
        def candidates():
            self.send(b'kawa ')
            shown = list(self.bridge.view['candidates'])
            for _ in range(2):
                self.send(b'\x1b')
                self.assertEqual(self.bridge.flush_escape(), b'')
            self.assertIsNone(self.bridge.view)
            return shown
        before = candidates()
        results, original = [], self.native._result
        def spy(pointer):
            result = original(pointer); results.append(result); return result
        self.native._result = spy
        try:
            for _ in range(3): propose(self.query('kawa'))
        finally:
            self.native._result = original
        self.assertTrue(results)
        self.assertFalse([r for r in results if r.get('commits')], 'a preview committed text')
        self.assertEqual(candidates(), before)

    def broken_sources(self):
        """Deliberately broken sources: the checks above must fail on each of them."""
        lib, native = self.native.lib, self.native
        def handle(vk, char=0, shift=0):
            if not native.preview_session:
                native.preview_session = lib.meltype_create()
                lib.meltype_set_direct(native.preview_session, 0)
                lib.meltype_set_can_delete(native.preview_session, 0)
            return native._result(lib.meltype_handle_key(native.preview_session, vk, char, shift, None, None))
        def compose(word):
            view = None
            for code in word.encode(): view = handle(*m.virtual_key(code)).get('view')
            return view
        def proposals(view):
            return [{'id': 'b', 'representation': view['text'], 'meaning': {}, 'evidence': []}] if view else []
        def no_candidates(query): return []
        def no_cancel(query): return proposals(compose(query['input']))
        def commits(query):
            view = compose(query['input'])
            handle(0x20, 0x20); handle(0x28); handle(0x0D)  # convert, take the second candidate, commit
            return proposals(view)
        return {'no candidates': no_candidates, 'no cancel': no_cancel, 'commits': commits}

    def test_preview_source_leaves_the_typing_session_alone(self):
        self.check_source(m.core_proposals(self.native))

    def test_preview_source_check_fails_on_broken_sources(self):
        for name in ['no candidates', 'no cancel']:
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self.check_source(self.broken_sources()[name])
            self.tearDown()
            self.setUp()

    def test_preview_does_not_learn(self):
        self.check_no_learning(m.core_proposals(self.native))

    def test_no_learning_check_fails_on_a_committing_source(self):
        with self.assertRaises(AssertionError):
            self.check_no_learning(self.broken_sources()['commits'])

    def test_record_what_the_core_gives_for_a_trailing_nn(self):
        # #19 P19e: the Core's answer for "nihon" / "nihonn" / "nihonngo", as the preview and Ctrl+Space
        # get it. Recorded here (the Core is the same in 1.2.1.6 and 1.2.1.7); the bcon VM judges.
        found = {}
        for word in ['nihon', 'nihonn', 'nihonngo']:
            self.tearDown(); self.setUp()
            preview = [p['representation'] for p in m.core_proposals(self.native)(self.query(word))]
            self.bridge = m.InputBridge(self.native, direct=True)
            self.assertEqual(self.send(word.encode()), word.encode())
            self.bridge.observe(word.encode())  # the echo a line editor would print
            self.assertEqual(self.send(b'\x00'), b'\x7f' * len(word))
            view = dict(self.bridge.view or {})
            found[word] = {'preview': preview, 'composition': view.get('text'), 'candidates': view.get('candidates'),
                           'committed': self.send(b'\r').decode()}
        print(json.dumps({'trailing_nn': found}, ensure_ascii=False), flush=True)

    def test_preview_offers_conversion_candidates_for_a_trailing_nn(self):
        # #19 P19f: the composition of nihonn is only にほん; the preview must carry the conversion (kanji).
        kanji = lambda text: any(chr(0x4e00) <= ch <= chr(0x9fff) for ch in text)
        propose = m.core_proposals(self.native)
        for word in ['nihonn', 'nihonngo']:
            shown = [p['representation'] for p in propose(self.query(word))][:m.Preview(propose).limit]
            print(json.dumps({'preview': word, 'representations': shown}, ensure_ascii=False), flush=True)
            self.assertTrue(any(kanji(text) for text in shown), (word, shown))

    def test_ctrl_space_then_space_reaches_kanji_for_nihonn(self):
        # Unchanged path, checked only: Ctrl+Space composes にほん, Space converts it to kanji candidates.
        self.bridge = m.InputBridge(self.native, direct=True)
        self.assertEqual(self.send(b'nihonn'), b'nihonn')
        self.bridge.observe(b'nihonn')
        self.assertEqual(self.send(b'\x00'), b'\x7f' * 6)
        self.assertEqual(self.bridge.view['text'], 'にほん')
        self.assertEqual(self.send(b' '), b'')
        candidates = self.bridge.view['candidates']
        print(json.dumps({'ctrl_space_nihonn_candidates': candidates[:5]}, ensure_ascii=False), flush=True)
        self.assertIn('日本', candidates)

    def test_preview_query_time_is_measured_and_bounded(self):
        propose = m.core_proposals(self.native)
        for word in ['nihongo', 'nihongo' * 4 + 'niho']:  # The longest word a preview asks for: 32 letters.
            started = time.monotonic()
            self.assertTrue(propose(self.query(word)))
            elapsed = time.monotonic() - started
            print(json.dumps({'preview_query_seconds': round(elapsed, 4), 'letters': len(word)}), flush=True)
            self.assertLess(elapsed, m.Preview(propose).timeout)

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
