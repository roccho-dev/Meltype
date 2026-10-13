# SPDX-License-Identifier: GPL-3.0-or-later
"""Known echo policy, explicit raw-text capability, and actual secret input."""
import os
from pathlib import Path
import select
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

class CaptureScopeTests(unittest.TestCase):
    def test_only_explicit_text_capability_enables_unknown_raw(self):
        for flags,raw,expected in [(termios.ECHO|termios.ICANON,False,True),
                (termios.ICANON,False,False),(termios.ICANON,True,False),
                (0,False,False),(0,True,True)]:
            with self.subTest(flags=flags,raw=raw),patch.object(m.termios,'tcgetattr',return_value=[0,0,0,flags,0,0,[]]):
                self.assertEqual(m.capture_allowed(7,raw),expected)
        with patch.object(m.termios,'tcgetattr',side_effect=OSError('gone')):
            self.assertFalse(m.capture_allowed(7,True))

    def test_loss_cancels_once_and_never_interprets_subsequent_input(self):
        class Native:
            def __init__(self):self.calls=[]
            def focus(self,value):self.calls.append(('focus',value))
            def cancel(self):self.calls.append(('cancel',))
            def key(self,*_):raise AssertionError('private input reached Core')
        n=Native();b=m.InputBridge(n);b.view={'text':'public pending'};b.deferred.extend(b'old')
        b.set_capture(False);b.set_capture(False)
        self.assertIsNone(b.view);self.assertEqual(b.deferred,b'')
        self.assertEqual(b.feed(b'private\x1b[A'),b'private\x1b[A')
        self.assertEqual(n.calls,[('focus',False),('cancel',)])
        b.set_capture(True);self.assertEqual(n.calls[-1],('focus',True))

    def test_focus_loss_cancels_old_preedit_and_echo_cannot_reenable_it(self):
        class Native:
            def __init__(self): self.calls = []
            def focus(self, value): self.calls.append(("focus", value))
            def cancel(self): self.calls.append(("cancel",))
            def key(self, *args):
                self.calls.append(("key", args))
                return {"consumed": False, "commits": [], "view": None}
        n = Native()
        bridge = m.InputBridge(n)
        bridge.view = {"text": "old preedit"}
        self.assertEqual(bridge.feed(b"\x1b[O"), b"\x1b[O")
        self.assertFalse(bridge.capturing)
        self.assertIsNone(bridge.view)
        bridge.set_capture(True)  # ECHO permission alone cannot override focus loss.
        self.assertFalse(bridge.capturing)
        self.assertEqual(bridge.feed(b"secret"), b"secret")
        self.assertEqual(n.calls, [("focus", False), ("cancel",)])
        self.assertEqual(bridge.feed(b"\x1b[I"), b"\x1b[I")
        self.assertTrue(bridge.capturing)
        self.assertEqual(n.calls[-1], ("focus", True))
        self.assertEqual(bridge.feed(b"a"), b"a")
        self.assertEqual(n.calls[-1][0], "key")

    def test_focus_regain_never_bypasses_canonical_no_echo(self):
        class Native:
            def __init__(self): self.calls = []
            def focus(self, value): self.calls.append(("focus", value))
            def key(self, *_): raise AssertionError("private key must not reach NativeAOT")
        n = Native()
        bridge = m.InputBridge(n)
        bridge.set_capture(False)
        self.assertEqual(bridge.feed(b"\x1b[O\x1b[Iprivate"), b"\x1b[O\x1b[Iprivate")
        self.assertFalse(bridge.capturing)
        self.assertEqual(n.calls, [("focus", False)])

    def test_actual_hidden_shell_input_bypasses_native_even_with_raw_text_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td);secret='not-a-real-password';marker=path/'value'
            command=['/bin/bash','--noprofile','--norc','-c',f'stty -echo; printf PRIVATE_READY; IFS= read -r value; printf %s "$value" > {marker}']
            code=("import sys;from meltype_tty import run\nclass Native:\n"
                " def key(self,*args): raise AssertionError('private input reached Core')\n"
                " def focus(self,*args):pass\n def close(self):pass\n"
                f"sys.exit(run({command!r},Native,raw_text=True))")
            master,slave=os.openpty();original=termios.tcgetattr(slave)
            proc=subprocess.Popen([sys.executable,'-c',code],stdin=slave,stdout=slave,stderr=slave,
                env={**os.environ,'PYTHONPATH':str(ROOT)},start_new_session=True)
            output=bytearray();sent=False;deadline=time.monotonic()+4
            try:
                while proc.poll() is None and time.monotonic()<deadline:
                    if select.select([master],[],[],.02)[0]:
                        output.extend(os.read(master,65536))
                        if b'PRIVATE_READY' in output and not sent:os.write(master,(secret+'\n').encode());sent=True
                self.assertIsNotNone(proc.poll(),'private-input session hung')
                self.assertEqual(proc.returncode,0,bytes(output[-600:]))
                self.assertEqual(marker.read_text(),secret)
                self.assertNotIn(secret.encode(),output)
                self.assertEqual(termios.tcgetattr(slave),original)
            finally:
                if proc.poll() is None:proc.terminate();proc.wait(2)
                os.close(master);os.close(slave)

if __name__=='__main__':unittest.main()
