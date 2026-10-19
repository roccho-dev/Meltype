# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned child exit is independent of PTY EOF and candidate cursor replies."""
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


class LifecycleTests(unittest.TestCase):
    def invoke(self, extra, command, timeout=3):
        code = ("import os,sys;from meltype_tty import run\n"
                "class Native:\n def close(self): pass\n" + extra +
                f"\nsys.exit(run({command!r},Native,display_factory))\n")
        master, slave = os.openpty()
        before = termios.tcgetattr(slave)
        process = subprocess.Popen([sys.executable, '-c', code], stdin=slave,
            stdout=slave, stderr=slave, start_new_session=True,
            env={**os.environ, 'PYTHONPATH': str(ROOT), 'PYTHON_COLORS': '0'})
        output = bytearray()
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None and time.monotonic() < deadline:
                if select.select([master], [], [], .02)[0]:
                    output.extend(os.read(master, 65536))
            self.assertIsNotNone(process.poll(), 'owned child exit was hidden by cursor/PTY wait')
            while select.select([master], [], [], 0)[0]:
                output.extend(os.read(master, 65536))
            self.assertEqual(termios.tcgetattr(slave), before)
            return process.returncode, bytes(output)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=2)
            os.close(master); os.close(slave)

    def test_child_exit_during_pending_cursor_keeps_status_and_final_output(self):
        display = """
class Waiting:
 pending=True
 cursor=None
 class boundary:
  safe=True
 def initialize(self,write): return b''
 def update(self,view): return b''
 def clear(self): return b''
 def output(self,data): return data
 def reply(self,raw): return False
display_factory=Waiting
"""
        status, output = self.invoke(display, ['/bin/sh', '-c', 'printf CHILD_FINAL; exit 23'])
        self.assertEqual(status, 23)
        self.assertIn(b'CHILD_FINAL', output)

    def test_pty_master_cannot_leak_into_native_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory)/'master-inheritance'
            native = f"""
from pathlib import Path
class Native:
 def __init__(self):
  masters=[]
  for entry in Path('/proc/self/fd').iterdir():
   try:
    if os.readlink(entry).endswith('/ptmx'):
     masters.append(os.get_inheritable(int(entry.name)))
   except OSError: pass
  Path({str(marker)!r}).write_text(repr(masters))
 def close(self): pass
display_factory=None
"""
            status, _ = self.invoke(native, ['/bin/sh', '-c', 'exit 7'])
            self.assertEqual(status, 7)
            self.assertEqual(marker.read_text(), '[False]')

    def test_final_output_without_candidate_display_is_not_lost(self):
        status, output = self.invoke('display_factory=None\n',
            ['/bin/sh', '-c', 'printf FINAL_OUTPUT; exit 19'])
        self.assertEqual(status, 19)
        self.assertIn(b'FINAL_OUTPUT', output)


class PackageTests(unittest.TestCase):
    def test_no_supply_publisher_without_a_consumer(self):
        # resources pins only the release zip and the source tag; nothing reads meltype-supply.json.
        self.assertFalse((ROOT / 'supply.py').exists())


if __name__ == '__main__':
    unittest.main()
