# SPDX-License-Identifier: GPL-3.0-or-later
"""Published Kitty key wire, with original bytes retained for passthrough."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from meltype_tty import InputBridge

class Native:
    def __init__(self,consume=True): self.calls=[];self.consume=consume
    def key(self,*args):
        self.calls.append(args)
        return {'consumed':self.consume,'commits':[], 'view':{'text':'pending'} if self.consume else None}
    def commit(self): return {'consumed':True,'commits':[],'view':None}

class ExtendedKeys(unittest.TestCase):
    def test_kitty_letters_enter_the_same_core(self):
        n=Native();b=InputBridge(n)
        self.assertEqual(b.feed(b'\x1b[110u'),b'')
        self.assertEqual(n.calls,[(78,110,0)])

    def test_shift_and_alternate_text(self):
        for raw in [b'\x1b[97;2u',b'\x1b[97:65;2u',b'\x1b[97;2;65u']:
            n=Native();b=InputBridge(n)
            self.assertEqual(b.feed(raw),b'')
            self.assertEqual(n.calls,[(65,65,1)])

    def test_consumed_release_does_not_reach_child(self):
        n=Native();b=InputBridge(n)
        self.assertEqual(b.feed(b'\x1b[110;1:1u\x1b[110;1:3u'),b'')
        self.assertEqual(len(n.calls),1)
        self.assertEqual(b.consumed_keys,set())

    def test_unconsumed_press_and_release_are_byte_exact(self):
        n=Native(False);b=InputBridge(n)
        raw=b'\x1b[110;1:1u\x1b[110;1:3u'
        self.assertEqual(b.feed(raw),raw)
        self.assertEqual(len(n.calls),1)

    def test_modified_command_is_never_reinterpreted_as_text(self):
        n=Native();b=InputBridge(n)
        raw=b'\x1b[99;5u\x1b[99;5:3u'
        self.assertEqual(b.feed(raw),raw)
        self.assertEqual(n.calls,[])

    def test_modify_other_keys_uses_core(self):
        n=Native();b=InputBridge(n)
        self.assertEqual(b.feed(b'\x1b[27;1;110~'),b'')
        self.assertEqual(n.calls,[(78,110,0)])

    def test_repeated_candidate_arrow_and_its_release(self):
        n=Native();b=InputBridge(n);b.view={'text':'pending'}
        self.assertEqual(b.feed(b'\x1b[1;1:2B\x1b[1;1:3B'),b'')
        self.assertEqual(n.calls,[(40,0,0)])

    def test_astral_or_multiple_codepoints_preserve_original(self):
        n=Native();b=InputBridge(n)
        for raw in [b'\x1b[128512u',b'\x1b[0;;128512:65039u']:
            self.assertEqual(b.feed(raw),raw)
        self.assertEqual(n.calls,[])

    def test_caps_lock_preserves_reported_case(self):
        for raw,expected in [(b'\x1b[97;65u',(65,65,1)),(b'\x1b[97;66u',(65,97,0))]:
            n=Native();b=InputBridge(n);b.feed(raw)
            self.assertEqual(n.calls,[expected])

    def test_keyboard_and_window_reports_never_commit(self):
        n=Native();b=InputBridge(n);b.view={'text':'pending'}
        for raw in [b'\x1b[?31u',b'\x1b[4;600;800t']:
            self.assertEqual(b.feed(raw),raw)
            self.assertEqual(b.view,{'text':'pending'})
        self.assertEqual(n.calls,[])

if __name__=='__main__':unittest.main()
