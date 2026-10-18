# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression cases for the real transport loop, not a bcon/visual proof."""
import os
import json
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

    def test_ready_split_reply_never_enters_native_after_escape_deadline(self):
        # Exercise run's actual select/read/expire order on a PTY. The clock
        # boundary is controlled; this is not evidence about natural latency.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)
            child_ready, tail_ready = path/'child-ready', path/'tail-ready'
            received, calls = path/'received', path/'calls'
            reply = b'\x1b_Gi=424242;OK\x1b\\'
            child = f'''import os,select,time,tty
from pathlib import Path
tty.setraw(0)
Path({str(child_ready)!r}).touch()
data=bytearray(); deadline=time.monotonic()+5
while len(data)<{len(reply)} and time.monotonic()<deadline:
 if select.select([0],[],[],.02)[0]: data.extend(os.read(0,4096))
Path({str(received)!r}).write_bytes(data)
'''
            code = f'''import json,select,sys
from pathlib import Path
import meltype_tty as m
state={{'bridge':None,'forced':False}}
original_init=m.InputBridge.__init__
original_expire=m.InputBridge.expire
original_select=select.select
def initialize(self,*args,**kwargs):
 original_init(self,*args,**kwargs); state['bridge']=self
def ready(readers,writers,errors,timeout=None):
 bridge=state['bridge']
 if 0 in readers and bridge is not None and bridge.escape==b'\x1b' and not state['forced']:
  Path({str(tail_ready)!r}).touch()
  result=original_select([0],[],[],3)
  if 0 not in result[0]: raise AssertionError('tail did not become readable')
  state['forced']=True
  return result
 return original_select(readers,writers,errors,timeout)
def expire(self,now=None,**kwargs):
 if state['forced'] and self.escape==b'\x1b': now=self.escape_started+.061
 return original_expire(self,now,**kwargs)
m.InputBridge.__init__=initialize
m.InputBridge.expire=expire
select.select=ready
class Native:
 def __init__(self): self.calls=[]
 def key(self,*args):
  self.calls.append(args)
  return {{'consumed':False,'commits':[],'view':None}}
 def close(self): Path({str(calls)!r}).write_text(json.dumps(self.calls))
sys.exit(m.run([sys.executable,'-c',{child!r}],Native,raw_text=True,direct=False))
'''
            master, slave = os.openpty()
            original = termios.tcgetattr(slave)
            proc = subprocess.Popen([sys.executable, '-c', code], stdin=slave,
                stdout=slave, stderr=slave, env={**os.environ, 'PYTHONPATH':str(ROOT)})
            def wait_for(marker):
                deadline = time.monotonic()+4
                while not marker.exists() and proc.poll() is None and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertTrue(marker.exists(), f'missing readiness: {marker.name}')
            try:
                wait_for(child_ready)
                os.write(master, reply[:1])
                wait_for(tail_ready)
                os.write(master, reply[1:])
                self.assertEqual(proc.wait(timeout=7), 0)
                self.assertEqual(received.read_bytes(), reply)
                self.assertEqual(json.loads(calls.read_text()), [], 'terminal reply entered Native')
                self.assertEqual(termios.tcgetattr(slave), original)
            finally:
                if proc.poll() is None: proc.terminate(); proc.wait(2)
                os.close(master); os.close(slave)

    def test_arrow_inside_composition_is_given_to_native(self):
        class Choosing(Native):
            def key(self,*args):
                self.calls.append(args)
                return {'consumed':True,'commits':[],'view':{'text':'next','selectedIndex':1}}
        n=Choosing();b=m.InputBridge(n);b.view={'text':'first'}
        self.assertEqual(b.feed(b'\x1b[B'),b'')
        self.assertEqual(n.calls,[(0x28,0,0)])
        self.assertEqual(b.view['selectedIndex'],1)

    def image_reply_pty(self, keys=False):
        # The real loop sees empty select cycles before the ACK tail. Its
        # original 60 ms boundary must not turn an owned reply into a key.
        import select
        with tempfile.TemporaryDirectory() as td:
            path=Path(td); received=path/'received'; calls=path/'calls'
            reply=b'\x1b_Gi=424242;OK\x1b\\'
            image=b'\x1b_Ga=T,i=424242,q=0,m=0;AAAA\x1b\\'
            expected=reply+(b'\x1bz' if keys else b'')
            child=f'''import os,select,time,tty
from pathlib import Path
tty.setraw(0); os.write(1,{image!r})
data=bytearray(); deadline=time.monotonic()+4
while len(data)<{len(expected)} and time.monotonic()<deadline:
 if select.select([0],[],[],.02)[0]: data.extend(os.read(0,4096))
Path({str(received)!r}).write_bytes(data)
'''
            code=f'''import json,sys
from pathlib import Path
import meltype_tty as m
from kitty_display import Boundary
class Display:
 def __init__(self): self.boundary=Boundary();self.pending=False;self.cursor=(10,4)
 def initialize(self,write): return b''
 def update(self,view): return b''
 def clear(self): return b''
 def output(self,data): self.boundary.feed(data);return data
 def reply(self,data): getattr(self.boundary,'reply',lambda _:False)(data);return False
 def image_replies(self): return frozenset(t for t,_ in self.boundary.graphics_replies)
class Native:
 def __init__(self): self.calls=[]
 def key(self,*args):
  self.calls.append(args);return {{'consumed':False,'commits':[],'view':None}}
 def close(self): Path({str(calls)!r}).write_text(json.dumps(self.calls))
sys.exit(m.run([sys.executable,'-c',{child!r}],Native,Display,raw_text=True,direct=False))
'''
            master,slave=os.openpty();original=termios.tcgetattr(slave)
            proc=subprocess.Popen([sys.executable,'-c',code],stdin=slave,stdout=slave,stderr=slave,
                env={**os.environ,'PYTHONPATH':str(ROOT)})
            try:
                output=bytearray();deadline=time.monotonic()+3
                while image not in output and proc.poll() is None and time.monotonic()<deadline:
                    if select.select([master],[],[],.02)[0]: output.extend(os.read(master,4096))
                self.assertIn(image,output,'child image was not forwarded')
                os.write(master,b'\x1b')
                time.sleep(.09)  # Actual empty cycles; no changed production clock/timeout.
                if keys:
                    os.write(master,b'z')
                    time.sleep(.03)
                    os.write(master,reply)
                else:
                    os.write(master,reply[1:])
                self.assertEqual(proc.wait(6),0)
                self.assertEqual(received.read_bytes(),expected)
                self.assertEqual(json.loads(calls.read_text()),[[27,0,0],[90,122,0]] if keys else [])
                self.assertEqual(termios.tcgetattr(slave),original)
            finally:
                if proc.poll() is None: proc.terminate();proc.wait(2)
                os.close(master);os.close(slave)

    def test_owned_image_ack_survives_empty_select_cycles(self):
        self.image_reply_pty()

    def test_pending_image_keeps_escape_then_text_as_separate_keys(self):
        self.image_reply_pty(keys=True)

    def test_ready_report_continuations_preserve_pending_composition(self):
        for reply in [b'\x1b_Gi=7;OK\x1b\\', b'\x1b[12;34R', b'\x1b]52;c;YWJj\x07']:
            for elapsed in [.059, .061]:
                with self.subTest(reply=reply, elapsed=elapsed):
                    n=Native(); b=m.InputBridge(n); b.view={'text':'pending'}
                    with patch.object(m.time,'monotonic',return_value=10): b.feed(reply[:1])
                    effect=b.expire(10+elapsed,continuation=reply[1:])
                    effect+=b.feed(reply[1:])
                    self.assertEqual(effect,reply)
                    self.assertEqual(n.calls,[])
                    self.assertEqual(b.view,{'text':'pending'})

    def test_ready_text_still_expires_the_real_escape_before_text(self):
        n=Native(); b=m.InputBridge(n)
        with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
        self.assertEqual(b.expire(10.061,continuation=b'z')+b.feed(b'z'),b'\x1bz')
        self.assertEqual(n.calls,[(27,0,0),(ord('Z'),ord('z'),0)])

    def test_real_escape_before_a_separate_report_is_not_swallowed(self):
        n=Native(); b=m.InputBridge(n)
        report=b'\x1b_Gi=7;OK\x1b\\'
        with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
        self.assertEqual(b.expire(10.061,continuation=report)+b.feed(report),b'\x1b'+report)
        self.assertEqual(n.calls,[(27,0,0)])

    def test_escape_then_a_fresh_report_keeps_both_introducers_before_deadline(self):
        for report in [b'\x1b_Gi=7;OK\x1b\\', b'\x1b[12;34R', b'\x1b]52;c;YWJj\x07']:
            for split in range(len(report) + 1):
                with self.subTest(report=report, split=split):
                    n=Native(); b=m.InputBridge(n)
                    with patch.object(m.time,'monotonic',return_value=10):
                        effect=b.feed(b'\x1b'+report[:split])+b.feed(report[split:])
                    self.assertEqual(effect,b'\x1b'+report)
                    self.assertEqual(n.calls,[(27,0,0)],'report tail entered Native')

    def test_two_real_escape_keys_keep_the_second_keys_own_deadline(self):
        n=Native(); b=m.InputBridge(n)
        with patch.object(m.time,'monotonic',return_value=10):
            self.assertEqual(b.feed(b'\x1b'),b'')
        with patch.object(m.time,'monotonic',return_value=10.04):
            self.assertEqual(b.feed(b'\x1b'),b'\x1b')
        self.assertEqual(b.expire(10.07),b'')
        self.assertEqual(b.expire(10.101),b'\x1b')
        self.assertEqual(n.calls,[(27,0,0),(27,0,0)])

    def test_ready_text_then_report_keeps_the_same_order(self):
        n=Native(); b=m.InputBridge(n); tail=b'z\x1b_Gi=7;OK\x1b\\'
        with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
        self.assertEqual(b.expire(10.061,continuation=tail)+b.feed(tail),b'\x1b'+tail)
        self.assertEqual(n.calls,[(27,0,0),(ord('Z'),ord('z'),0)])

    def test_ambiguous_late_controls_fail_before_any_native_effect(self):
        for tail in [b'[A', b'[31m', b'[200~payload\x1b[201~']:
            with self.subTest(tail=tail):
                n=Native(); b=m.InputBridge(n); b.view={'text':'pending'}
                with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
                self.assertEqual(b.expire(10.061,continuation=tail),b'')
                with self.assertRaisesRegex(m.UnsafeEffect,'ambiguous late'):
                    b.feed(tail)
                self.assertEqual(n.calls,[])
                self.assertEqual(b.view,{'text':'pending'})

    def test_late_prefix_does_not_extend_original_sequence_deadline(self):
        for prefix in [b'[', b'_']:
            n=Native(); b=m.InputBridge(n)
            with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
            self.assertEqual(b.expire(10.061,continuation=prefix),b'')
            self.assertEqual(b.feed(prefix),b'')
            with self.assertRaisesRegex(m.UnsafeEffect,'incomplete terminal'):
                b.expire(11.501,continuation=b'anything')
            self.assertEqual(n.calls,[])
        n=Native(); b=m.InputBridge(n)
        with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
        with self.assertRaisesRegex(m.UnsafeEffect,'incomplete terminal'):
            b.expire(11.501,continuation=b'_Gi=7;OK\x1b\\')
        self.assertEqual(n.calls,[])

    def test_late_report_keeps_existing_size_bound(self):
        n=Native(); b=m.InputBridge(n)
        with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
        tail=b'_'+b'x'*(1024*1024)
        b.expire(10.061,continuation=tail)
        with self.assertRaisesRegex(m.UnsafeEffect,'unbounded terminal'):
            b.feed(tail)
        self.assertEqual(n.calls,[])

    def test_capture_disabled_keeps_late_controls_and_paste_byte_exact(self):
        for tail in [b'[A', b'[31m', b'[200~private\x1b[201~']:
            with self.subTest(tail=tail):
                n=Native(); b=m.InputBridge(n); b.set_capture(False)
                with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
                effect=b.expire(10.061,continuation=tail)+b.feed(tail)
                self.assertEqual(effect,b'\x1b'+tail)
                self.assertEqual(n.calls,[])

    def test_capture_loss_during_late_prefix_does_not_poison_later_input(self):
        n=Native(); b=m.InputBridge(n)
        with patch.object(m.time,'monotonic',return_value=10): b.feed(b'\x1b')
        b.expire(10.061,continuation=b'[')
        self.assertEqual(b.feed(b'['),b'')
        b.set_capture(False)
        self.assertEqual(b.feed(b'A'),b'\x1b[A')
        self.assertEqual(n.calls,[])
        self.assertFalse(b.late_reply)
        b.set_capture(True)
        self.assertEqual(b.feed(b'\x1b[Bz'),b'\x1b[Bz')
        self.assertEqual(n.calls,[(ord('Z'),ord('z'),0)])

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
