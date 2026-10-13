# SPDX-License-Identifier: GPL-3.0-or-later
"""Graphics byte/PNG and input-order tests; not a physical bcon result."""
import base64
import io
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from kitty_display import Boundary,KittyDisplay
import meltype_tty as m

FONT=os.environ.get('MELTYPE_TTY_TEST_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
VIEW={'text':'今日はgoogleで検索','candidates':['日本語','ニホンゴ','にほんご'], 'selectedIndex':1,'meaning':'候補の意味'}

class DisplayTests(unittest.TestCase):
    def display(self): return KittyDisplay(FONT,24,80,987654)

    def test_query_must_finish_before_paint(self):
        d=self.display()
        self.assertEqual(d.update(VIEW),b'\x1b[6n')
        self.assertEqual(d.update(VIEW),b'')
        self.assertTrue(d.reply(b'\x1b[22;5R'))
        self.assertIn(b'a=T',d.update(VIEW))
        self.assertEqual(d.update(VIEW),b'')

    def test_child_report_is_not_stolen(self):
        d=self.display();raw=b'hello\x1b[6n'
        self.assertEqual(d.output(raw),raw)
        self.assertEqual(d.update(VIEW),b'')
        self.assertFalse(d.reply(b'\x1b[3;9R'))
        self.assertEqual(d.update(VIEW),b'\x1b[6n')

    def test_png_chunks_and_cursor_restore(self):
        from PIL import Image
        d=self.display();d.cursor=(22,5)
        output=d.update(VIEW)
        frames=re.findall(rb'\x1b_G([^;]*);(.*?)\x1b\\',output)
        self.assertTrue(frames)
        self.assertTrue(all(len(data)<=4096 for header,data in frames))
        image=Image.open(io.BytesIO(base64.b64decode(b''.join(data for header,data in frames))))
        self.assertGreater(image.width,0);self.assertGreater(image.height,0)
        self.assertTrue(output.endswith(b'\x1b[22;5H'))
        self.assertNotIn(b'\x1b7',output);self.assertNotIn(b'\x1b[s',output)
        self.assertIn(b'C=1',output)
        self.assertEqual(d.clear(),b'\x1b_Ga=d,d=I,i=987654,q=2\x1b\\')
        self.assertEqual(d.clear(),b'')

    def test_cursor_timeout_is_not_retried(self):
        d=self.display()
        with patch('kitty_display.time.monotonic',return_value=10): d.update(VIEW)
        with patch('kitty_display.time.monotonic',return_value=11.6), self.assertRaises(ValueError): d.update(VIEW)

    def test_cancelled_view_does_not_cancel_an_outstanding_query_deadline(self):
        d=self.display()
        with patch('kitty_display.time.monotonic',return_value=10):
            self.assertEqual(d.update(VIEW),b'\x1b[6n')
            self.assertEqual(d.update(None),b'')
        with patch('kitty_display.time.monotonic',return_value=11.6):
            with self.assertRaisesRegex(ValueError,'cursor'):
                d.update(None)

    def test_bad_cursor_and_origin_mode_rejected(self):
        d=self.display();d.update(VIEW)
        with self.assertRaises(ValueError): d.reply(b'\x1b[999;1R')
        d=self.display();d.output(b'\x1b[?6h')
        with self.assertRaises(ValueError): d.update(VIEW)

    def test_utf8_and_escape_boundaries(self):
        for payload in ['日本語'.encode(),b'\x1b[1;3H',b'\x1b]0;title\x07',b'\x1bPstuff\x1b\\']:
            b=Boundary()
            for ch in payload[:-1]: b.feed(bytes([ch]))
            self.assertFalse(b.safe)
            b.feed(payload[-1:]);self.assertTrue(b.safe)

    def test_no_overlay_between_graphics_chunks(self):
        b=Boundary();b.feed(b'\x1b_Ga=T,f=100,m=1;YWJj\x1b\\')
        self.assertFalse(b.safe)
        b.feed(b'\x1b_Gm=0;ZGVm\x1b\\')
        self.assertTrue(b.safe)

    def test_image_payload_is_not_retained(self):
        b=Boundary();b.feed(b'\x1b_Ga=T,m=1;'+b'a'*1000000)
        self.assertLess(len(b.header),256)
        self.assertFalse(b.safe)

    def test_resize_invalidates_coordinates(self):
        d=self.display();d.cursor=(20,3)
        d.resize(30,100)
        self.assertIsNone(d.cursor)
        self.assertEqual(d.update(VIEW),b'\x1b[6n')

    def test_query_reply_does_not_enter_child_or_native(self):
        class Native:
            def key(self,*args): raise AssertionError('native input was not paused')
        d=self.display();d.update(VIEW)
        b=m.InputBridge(Native());b.on_report=d.reply;b.paused=True
        self.assertEqual(b.feed(b'abc\x1b[22;5Rdef'),b'')
        self.assertEqual(b.deferred,b'abcdef')
        self.assertFalse(d.pending)

    def test_graphics_negotiation_retains_unrelated_typeahead(self):
        d=self.display(); r,w=os.pipe()
        try:
            os.write(w,b'abc\x1b_Gi=987654;OK\x1b\\xyz')
            writes=[]
            self.assertEqual(d.initialize(writes.append,r),b'abcxyz')
            self.assertEqual(len(writes),1)
            self.assertIn(b'a=q,i=987654',writes[0])
        finally: os.close(r);os.close(w)

    def test_graphics_rejection_is_not_invisible_fallback(self):
        d=self.display();r,w=os.pipe()
        try:
            os.write(w,b'\x1b_Gi=987654;ENOTSUP\x1b\\')
            with self.assertRaisesRegex(ValueError,'rejected'): d.initialize(lambda _:None,r)
        finally: os.close(r);os.close(w)

    def test_combined_origin_mode_and_private_cursor_report(self):
        d=self.display();d.output(b'\x1b[?1;6h')
        with self.assertRaises(ValueError):d.update(VIEW)
        d.output(b'\x1b[?1;6l\x1b[?6n')
        self.assertEqual(d.update(VIEW),b'')
        self.assertFalse(d.reply(b'\x1b[?2;3R'))
        self.assertEqual(d.update(VIEW),b'\x1b[6n')

    def test_cursor_wait_buffer_is_finite(self):
        b=m.InputBridge(None);b.paused=True
        with self.assertRaises(m.UnsafeEffect): b.feed(b'a'*(1024*1024+1))

    def test_selected_evidence_changes_actual_candidate_image(self):
        display=self.display();display.cursor=(22,5)
        original={**VIEW,'notes':['evidence-a','evidence-b','evidence-c']}
        changed={**original,'notes':['evidence-a','different selected evidence','evidence-c']}
        self.assertNotEqual(display.paint(original),display.paint(changed))

    def test_keyboard_hint_is_drawn_even_with_meaning_and_evidence(self):
        display=self.display();display.cursor=(22,5)
        view={**VIEW,'candidates':['a','b','c','d','e'],
            'notes':['one','two','three','four','five']}
        drawn=[]
        class Draw:
            def text(self,position,line,**kwargs):drawn.append(line)
        with patch('PIL.ImageDraw.Draw',return_value=Draw()):display.paint(view)
        self.assertLessEqual(len(drawn),8)
        self.assertIn('two',' '.join(drawn))
        self.assertIn('Enter',drawn[-1])
        self.assertIn('Esc',drawn[-1])

if __name__=='__main__': unittest.main()
