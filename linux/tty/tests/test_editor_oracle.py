# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the VM's exact saved-file oracle locally, without importing the VM test."""
import ast
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).with_name('bcon.py')

class EditorOracleTests(unittest.TestCase):
    def check(self, expected, actual):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'editor-text'
            if actual is not None:
                target.write_bytes(actual)
            class Recorder:
                def __init__(self): self.keys = []; self.command = None
                def send_key(self, key): self.keys.append(key)
                def wait_until_succeeds(self, command): self.command = command
            machine = Recorder()
            module = ast.parse(SOURCE.read_text())
            functions = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == 'saved_editor']
            self.assertEqual(len(functions), 1)
            scope = {'machine': machine, 'shlex': shlex}
            exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), 'exec'), scope)
            scope['saved_editor'](expected)
            self.assertEqual(machine.keys, ['ctrl-o', 'ret'])
            command = machine.command.replace('/run/meltype-proof/editor-text', str(target))
            return subprocess.run(['/bin/sh', '-c', command], capture_output=True,
                env={'PATH': '/usr/bin:/bin'}, timeout=3).returncode

    def test_exact_unicode_and_trailing_newline_are_accepted(self):
        self.assertEqual(self.check('日本語\nhello\n', '日本語\nhello\n'.encode()), 0)

    def test_extra_trailing_newline_is_rejected(self):
        self.assertNotEqual(self.check('日本語', '日本語\n'.encode()), 0)

    def test_missing_trailing_newline_is_rejected(self):
        self.assertNotEqual(self.check('日本語\n', '日本語'.encode()), 0)

    def test_empty_expected_does_not_accept_missing_file(self):
        self.assertNotEqual(self.check('', None), 0)

    def test_empty_existing_file_is_accepted(self):
        self.assertEqual(self.check('', b''), 0)

    def test_metacharacters_are_literal_bytes(self):
        value = 'a\'"$(printf wrong)\\\n'
        self.assertEqual(self.check(value, value.encode()), 0)

if __name__ == '__main__': unittest.main()
