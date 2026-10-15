# SPDX-License-Identifier: GPL-3.0-or-later
"""Use the real process loop to check input received during initialization."""
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

class StartupInputTests(unittest.TestCase):
    def test_typeahead_is_not_flushed_when_entering_raw_mode(self):
        with tempfile.TemporaryDirectory() as td:
            marker = Path(td)/'initializing'
            output = Path(td)/'input'
            child = ("import os,tty,termios;from pathlib import Path;tty.setraw(0,termios.TCSANOW);v=b''\n"
                "while len(v)<6: v+=os.read(0,6-len(v))\n"
                f"Path({str(output)!r}).write_bytes(v)")
            code = ("import sys,time;from pathlib import Path;from meltype_tty import run\n"
                "class Native:\n"
                f" def __init__(self): Path({str(marker)!r}).touch();time.sleep(.3)\n"
                " def key(self,*a): return {'consumed':False,'commits':[],'view':None}\n"
                " def close(self): pass\n"
                f"sys.exit(run([sys.executable,'-c',{child!r}],Native))")
            master, slave = os.openpty()
            original = termios.tcgetattr(slave)
            env = {**os.environ,'PYTHONPATH':str(ROOT),'PYTHON_COLORS':'0'}
            proc = subprocess.Popen([sys.executable,'-c',code],stdin=slave,stdout=slave,
                stderr=slave,env=env,start_new_session=True)
            captured = bytearray()
            try:
                deadline=time.monotonic()+3
                while not marker.exists() and time.monotonic()<deadline:
                    time.sleep(.005)
                self.assertTrue(marker.exists())
                os.write(master,b'hello\n')
                while proc.poll() is None and time.monotonic()<deadline:
                    if select.select([master],[],[],.02)[0]: captured.extend(os.read(master,65536))
                self.assertTrue(output.exists(),f'startup input was discarded: {captured!r}')
                self.assertEqual(output.read_bytes(),b'hello\n')
                self.assertEqual(proc.wait(timeout=2),0)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=2)
                    except subprocess.TimeoutExpired: proc.kill();proc.wait(timeout=2)
                self.assertEqual(termios.tcgetattr(slave),original)
                os.close(master);os.close(slave)

if __name__=='__main__': unittest.main()
