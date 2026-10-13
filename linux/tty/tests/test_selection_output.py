# SPDX-License-Identifier: GPL-3.0-or-later
"""Original native selection observation is opt-in and never a DOM receipt."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import meltype_tty as m

class SelectionOutputTests(unittest.TestCase):
    def test_disabled_never_reads_selection(self):
        native=Mock()
        m.validate_selection_fd(None)
        m.publish_selection(native,None)
        native.take_selection.assert_not_called()

    def test_original_identity_kept_once_in_separate_append_file(self):
        value={'generation':17,'raw':'u','proposal':{'id':'B','representation':'同じ表示',
            'meaning':{'kind':'text','value':'opaque-B'},'evidence':['source','B']}}
        native=Mock()
        native.take_selection.side_effect=[value,None]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'selected'
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_APPEND,0o600)
            try:
                m.validate_selection_fd(fd)
                self.assertFalse(os.get_inheritable(fd))
                m.publish_selection(native,fd);m.publish_selection(native,fd)
                self.assertEqual([json.loads(x) for x in path.read_text().splitlines()],[value])
            finally: os.close(fd)

    def test_stdio_pipe_and_readonly_are_rejected(self):
        for fd in [0,1,2,-1]:
            with self.subTest(fd=fd),self.assertRaises(ValueError): m.validate_selection_fd(fd)
        r,w=os.pipe()
        try:
            with self.assertRaises(ValueError): m.validate_selection_fd(w)
        finally: os.close(r);os.close(w)
        with tempfile.TemporaryFile() as file:
            with self.assertRaises(ValueError): m.validate_selection_fd(file.fileno())
            readonly=os.open('/dev/null',os.O_RDONLY)
            try:
                with self.assertRaises(ValueError): m.validate_selection_fd(readonly)
            finally: os.close(readonly)

    def test_invalid_observer_never_starts_child(self):
        with patch.object(m.os, 'isatty', return_value=True), patch.object(m.pty, 'fork') as fork:
            with self.assertRaises(ValueError): m.run(['/bin/true'], Mock(), selection_fd=1)
            fork.assert_not_called()

    def test_output_failure_reports_possible_effect_and_never_retries(self):
        native=Mock()
        native.take_selection.return_value={'generation':1,'raw':'u','proposal':{}}
        with patch.object(m,'write_all',side_effect=OSError('disk full')) as write:
            with self.assertRaisesRegex(m.UnsafeEffect,'input may have applied; no replay'):
                m.publish_selection(native,7)
            self.assertEqual(write.call_count,1)
            self.assertEqual(native.take_selection.call_count,1)

if __name__=='__main__':unittest.main()
