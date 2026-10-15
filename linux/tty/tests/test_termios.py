# SPDX-License-Identifier: GPL-3.0-or-later
"""The restore oracle must compare command mode, not Readline's prompt mode."""
import os
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(shutil.which('bash') and shutil.which('stty'), 'requires Bash and stty')
class TermiosOracleTests(unittest.TestCase):
    def snapshot(self, corrupt=False):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            master, slave = os.openpty()
            env = {**os.environ, 'PS1': 'TERMIOS> ', 'TERM': 'xterm', 'PYTHONPATH': str(ROOT)}
            proc = subprocess.Popen(['bash', '--noprofile', '--norc', '-i'],
                stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True)
            def drain_until(done):
                data = bytearray()
                deadline = time.monotonic() + 5
                while not done(data):
                    if time.monotonic() >= deadline:
                        self.fail(f'Bash phase did not finish: {bytes(data[-1024:])!r}')
                    if select.select([master], [], [], .02)[0]:
                        data.extend(os.read(master, 65536))
                return bytes(data)
            try:
                drain_until(lambda out: b'TERMIOS> ' in out)
                prompt = subprocess.check_output(['stty', '-F', os.ttyname(slave), '-g']).strip()
                # Exercise actual run()/fork/cleanup; this test is about the terminal,
                # not about conversion. The child has no input or side effects.
                code = "import sys;from meltype_tty import run\nclass Native:\n def close(self): pass\nsys.exit(run([sys.executable,'-c','pass'],Native))"
                fault = 'stty -echo; ' if corrupt else ''
                command = (f'stty -g > {td}/before; '
                    f'{shlex.quote(sys.executable)} -c {shlex.quote(code)}; '
                    f'printf %s $? > {td}/status; {fault}'
                    f'stty -g > {td}/after; touch {td}/done\n')
                os.write(master, command.encode())
                drain_until(lambda _: (directory/'done').exists())
                self.assertEqual((directory/'status').read_text(), '0')
                return prompt, (directory/'before').read_bytes().strip(), (directory/'after').read_bytes().strip()
            finally:
                # Interactive Bash ignores SIGTERM; end only the owned process.
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=2)
                os.close(master)
                os.close(slave)

    def test_same_phase_restoration_is_exact(self):
        prompt, before, after = self.snapshot()
        self.assertNotEqual(prompt, before, 'test did not enter Readline prompt mode')
        self.assertEqual(before, after, 'the actual wrapper failed to restore its input terminal')

    def test_same_phase_oracle_rejects_a_real_termios_change(self):
        _, before, after = self.snapshot(corrupt=True)
        self.assertNotEqual(before, after, 'the oracle accepted missing ECHO restoration')

if __name__ == '__main__':
    unittest.main()
