# SPDX-License-Identifier: GPL-3.0-or-later
"""Ctrl+U/I/P/O reach the Core only while composing; otherwise the child keeps them."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from meltype_tty import InputBridge

class Native:
    def __init__(self): self.calls=[];self.commits=0
    def key(self,*args):
        self.calls.append(args)
        return {'consumed':True,'commits':[],'view':{'text':'pending'}}
    def commit(self):
        self.commits+=1
        return {'consumed':True,'commits':[{'deleteBefore':0,'text':'確定'}],'view':None}

def composing():
    n=Native();b=InputBridge(n);b.view={'text':'pending'}
    return n,b

class CtrlWhileComposing(unittest.TestCase):
    def test_legacy_ctrl_u_p_o_switch_in_the_core(self):
        for raw,vk in [(b'\x15',0x55),(b'\x10',0x50),(b'\x0f',0x4F)]:
            n,b=composing()
            self.assertEqual(b.feed(raw),b'')
            self.assertEqual(n.calls,[(vk,0,2)])
            self.assertEqual(n.commits,0)
            self.assertEqual(b.view,{'text':'pending'})

    def test_kitty_ctrl_u_i_p_o_and_their_release(self):
        for letter,vk in [(b'117',0x55),(b'105',0x49),(b'112',0x50),(b'111',0x4F)]:
            n,b=composing()
            self.assertEqual(b.feed(b'\x1b['+letter+b';5u\x1b['+letter+b';5:3u'),b'')
            self.assertEqual(n.calls,[(vk,0,2)])
            self.assertEqual(b.consumed_keys,set())

    def test_modify_other_keys_ctrl_u(self):
        n,b=composing()
        self.assertEqual(b.feed(b'\x1b[27;5;117~'),b'')
        self.assertEqual(n.calls,[(0x55,0,2)])

    def test_legacy_ctrl_i_is_still_tab(self):
        n,b=composing()
        b.feed(b'\t')
        self.assertEqual(n.calls,[(0x09,0,0)])

    def test_other_ctrl_keys_commit_then_reach_the_child(self):
        for raw in [b'\x03',b'\x1b[117;7u',b'\x1b[117;6u',b'\x1b[99;5u']:
            n,b=composing()
            self.assertEqual(b.feed(raw),'確定'.encode()+raw)
            self.assertEqual(n.calls,[])
            self.assertIsNone(b.view)

class CtrlWithoutComposition(unittest.TestCase):
    def test_ctrl_u_p_o_i_reach_the_child_unchanged(self):
        for raw in [b'\x15',b'\x10',b'\x0f',b'\x1b[117;5u',b'\x1b[105;5u',b'\x1b[27;5;112~']:
            n=Native();b=InputBridge(n)
            self.assertEqual(b.feed(raw),raw)
            self.assertEqual(n.calls,[])
            self.assertEqual(n.commits,0)

    def test_without_capture_the_child_keeps_ctrl(self):
        n,b=composing();b.capturing=False
        self.assertEqual(b.feed(b'\x15'),b'\x15')
        self.assertEqual(n.calls,[])

if __name__=='__main__':unittest.main()
