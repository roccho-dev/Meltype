# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression cases for the real transport loop, not a bcon/visual proof."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import meltype_tty as m

class Native:
    def __init__(self): self.calls=[]
    def key(self,*args):
        self.calls.append(args)
        return {'consumed':False,'commits':[],'view':None}
    def commit(self):
        return {'consumed':True,'commits':[{'deleteBefore':0,'text':'日本語'}],'view':None}
    def close(self): pass

class LoopRegression(unittest.TestCase):
    def invoke(self,code):
        master,slave=os.openpty()
        before=termios.tcgetattr(slave)
        try:
            env={**os.environ,'PYTHONPATH':str(ROOT),'PYTHON_COLORS':'0'}
            result=subprocess.run([sys.executable,'-c',code],stdin=slave,stdout=slave,stderr=slave,env=env,timeout=5)
            self.assertEqual(termios.tcgetattr(slave),before,'terminal was not restored')
            return result.returncode
        finally:
            os.close(master); os.close(slave)

    def test_paste_cannot_overtake_preedit(self):
        b=m.InputBridge(Native()); b.view={'text':'日本語'}
        got=b.feed(m.PASTE_BEGIN+b'payload'+m.PASTE_END)
        self.assertEqual(got,'日本語'.encode()+m.PASTE_BEGIN+b'payload'+m.PASTE_END)
        self.assertIsNone(b.view)

    def test_failed_native_initialization_never_executes_child(self):
        with tempfile.TemporaryDirectory() as d:
            marker=Path(d)/'child-started'
            child=f'from pathlib import Path;Path({str(marker)!r}).touch()'
            code=f"import sys,time;from meltype_tty import run\ndef fail():\n deadline=time.monotonic()+1\n while not __import__('pathlib').Path({str(marker)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n raise RuntimeError('native unavailable')\nrun([sys.executable,'-c',{child!r}],fail)"
            self.assertNotEqual(self.invoke(code),0)
            self.assertFalse(marker.exists(),'child executed before native readiness')

    def test_successful_child_exit_status_is_preserved(self):
        code="import sys;from meltype_tty import run\nclass Native:\n def close(self): pass\nsys.exit(run([sys.executable,'-c','import sys;sys.exit(7)'],Native))"
        self.assertEqual(self.invoke(code),7)

    def test_cleanup_runs_after_native_close_failure(self):
        code="from meltype_tty import run\nimport sys\nclass Native:\n def close(self): raise RuntimeError('close failed')\nrun([sys.executable,'-c','pass'],Native)"
        self.assertNotEqual(self.invoke(code),0)

    def test_invalid_surrogate_is_bounded_failure(self):
        with self.assertRaises(m.UnsafeEffect):
            m.effect_bytes({'consumed':True,'commits':[{'deleteBefore':0,'text':'\ud800'}],'view':None})

    def test_no_open_paste_on_failure(self):
        b=m.InputBridge(Native()); b.view={'text':'pending'}
        self.assertEqual(b.feed(m.PASTE_BEGIN+b'payload'),b'')
        self.assertEqual(b.view,{'text':'pending'})

    def test_escape_deadline_is_not_idle_time(self):
        b=m.InputBridge(Native())
        with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
        self.assertEqual(b.expire(10.03),b'')
        self.assertEqual(b.expire(10.07),b'\x1b')
        self.assertEqual(b.expire(11),b'')

    def test_partial_control_deadline_is_separate(self):
        b=m.InputBridge(Native())
        with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b[')
        self.assertEqual(b.expire(10.07),b'')
        with self.assertRaises(m.UnsafeEffect): b.expire(11.6)

    def test_terminal_reply_preserves_composition(self):
        n=Native();b=m.InputBridge(n);b.view={'text':'pending'}
        for reply in [b'\x1b[10;3R',b'\x1b[?1;2c',b'\x1b[0n']:
            self.assertEqual(b.feed(reply),reply)
            self.assertEqual(b.view,{'text':'pending'})
        self.assertEqual(n.calls,[])

    def test_string_reply_is_opaque_across_chunks(self):
        for reply in [b'\x1b]52;c;cGFzdGU=\x07',b'\x1b_Gi=7;OK\x1b\\',b'\x1bP1$r0m\x1b\\']:
            n=Native(); b=m.InputBridge(n);b.view={'text':'pending'}
            got=b''.join(b.feed(bytes([x])) for x in reply)
            self.assertEqual(got,reply)
            self.assertEqual(b.view,{'text':'pending'})
            self.assertEqual(n.calls,[])

    def test_arrow_inside_composition_is_given_to_native(self):
        class Choosing(Native):
            def key(self,*args):
                self.calls.append(args)
                return {'consumed':True,'commits':[],'view':{'text':'next','selectedIndex':1}}
        n=Choosing();b=m.InputBridge(n);b.view={'text':'first'}
        self.assertEqual(b.feed(b'\x1b[B'),b'')
        self.assertEqual(n.calls,[(0x28,0,0)])
        self.assertEqual(b.view['selectedIndex'],1)

    def test_arrow_without_composition_is_byte_exact(self):
        n=Native();b=m.InputBridge(n)
        self.assertEqual(b.feed(b'\x1b[A'),b'\x1b[A')
        self.assertEqual(n.calls,[])

    def test_failed_commit_does_not_send_a_complete_paste(self):
        class Deleting(Native):
            def commit(self): return {'consumed':True,'commits':[{'deleteBefore':1,'text':'new'}],'view':None}
        b=m.InputBridge(Deleting());b.view={'text':'pending'}
        with self.assertRaises(m.UnsafeEffect): b.feed(m.PASTE_BEGIN+b'paste'+m.PASTE_END)

    def test_short_writes_resume_only_known_remaining_bytes(self):
        writes=[]
        def write(fd,data): writes.append(bytes(data));return min(2,len(data))
        with patch.object(m.os,'write',side_effect=write): m.write_all(9,b'abcde')
        self.assertEqual(writes,[b'abcde',b'cde',b'e'])

if __name__=='__main__': unittest.main()
