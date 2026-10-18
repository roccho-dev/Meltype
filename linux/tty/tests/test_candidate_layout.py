# SPDX-License-Identifier: GPL-3.0-or-later
"""Actual font/PNG candidate layout; the bcon VM separately checks scanout."""
import base64
import copy
import io
import os
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kitty_display import KittyDisplay

FONT = os.environ.get('MELTYPE_TTY_TEST_FONT', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
REFERENCE = 'https://github.com/roccho-dev/adrs/blob/' + 'f' * 40 + '/decisions/synthetic-reference.jsonl#L1'


class CandidateLayoutTests(unittest.TestCase):
    def render(self, view, cols=80, rows=24, cursor=22, passive=False):
        display = KittyDisplay(FONT, rows, cols, 987654)
        display.cursor = (cursor, 5)
        original = copy.deepcopy(view)
        drawn = []
        real_draw = ImageDraw.Draw

        class Draw:
            def __init__(self, image):
                self.draw = real_draw(image)

            def text(self, position, text, **options):
                drawn.append((position, text))
                self.draw.text(position, text, **options)

        with patch('PIL.ImageDraw.Draw', side_effect=Draw):
            output = display.update(view, passive=passive)
        frames = re.findall(rb'\x1b_G[^;]*;(.*?)\x1b\\', output)
        image = Image.open(io.BytesIO(base64.b64decode(b''.join(frames))))
        self.assertEqual(view, original, 'layout must not mutate the native view or Proposal')
        self.assertLessEqual(len(drawn), 8)
        top, left, rows, columns = display.placed
        self.assertGreaterEqual(top, 1)
        self.assertGreaterEqual(left, 1)
        self.assertTrue(top + rows <= cursor or top > cursor, 'the panel cannot cover the active prompt line')
        self.assertLessEqual(top + rows - 1, display.rows)
        self.assertLessEqual(left + columns - 1, cols)
        for (x, y), text in drawn:
            left_ink, top_ink, right, bottom = display.font.getbbox(text)
            self.assertGreaterEqual(x + left_ink, 0, text)
            self.assertGreaterEqual(y + top_ink, 0, text)
            self.assertLessEqual(x + right, image.width, text)
            self.assertLessEqual(y + bottom, image.height, text)
        return [text for _, text in drawn]

    def test_reference_identity_is_visible_without_losing_hint_or_details(self):
        lines = self.render({'text': 'う', 'candidates': [REFERENCE], 'selectedIndex': 0,
            'meaning': 'text reference', 'notes': ['source evidence']})
        self.assertEqual(lines[0], 'う')
        self.assertEqual(''.join(line[2:] for line in lines[1:-3]), REFERENCE,
            'the whole identifying path and line survive, not only a shared URL prefix')
        self.assertEqual(lines[-3:-1], ['text reference', 'evidence: source evidence'])
        self.assertIn('Enter', lines[-1])
        self.assertIn('Esc', lines[-1])

    def test_short_candidates_keep_their_order_and_selection(self):
        lines = self.render({'text': 'う', 'candidates': ['甲候補', '乙候補', '丙候補'],
            'selectedIndex': 1, 'meaning': 'text', 'notes': ['a', 'b', 'c']})
        self.assertEqual(lines[1:4], ['  甲候補', '▶ 乙候補', '  丙候補'])
        self.assertIn('evidence: b', lines)

    def test_narrow_panel_marks_overflow_and_keeps_all_paint_inside(self):
        lines = self.render({'text': 'う', 'candidates': [REFERENCE], 'selectedIndex': 0,
            'meaning': 'text', 'notes': ['source evidence']}, cols=8)
        self.assertTrue(any('…' in line for line in lines))

    def test_taller_reference_stays_below_a_prompt_near_the_top(self):
        lines = self.render({'text': 'う', 'candidates': [REFERENCE], 'selectedIndex': 0,
            'meaning': 'text', 'notes': ['source evidence']}, rows=10, cursor=4)
        self.assertLessEqual(len(lines), 6)
        self.assertIn('Enter', lines[-1])

    def test_insufficient_space_cannot_offer_an_invisible_confirmation(self):
        display = KittyDisplay(FONT, 3, 80, 987654)
        display.cursor = (2, 5)
        view = {'text': 'う', 'candidates': [REFERENCE], 'selectedIndex': 0}
        with self.assertRaisesRegex(ValueError, 'covering the active cursor'):
            display.update(view)
        self.assertEqual(display.update(view, passive=True), b'')


    def test_two_row_passive_reference_has_bounded_paint_without_mutating_the_offer(self):
        lines = self.render({'text': 'sourcereference', 'candidates': [REFERENCE],
            'selectedIndex': -1, 'hint': 'Ctrl+Space: convert'}, rows=5, cursor=3, passive=True)
        self.assertEqual(len(lines), 2)
        self.assertIn('…', lines[1])

    def test_active_painter_with_no_space_refuses_instead_of_hiding(self):
        display = KittyDisplay(FONT, 1, 80, 987654)
        display.cursor = (1, 5)
        with self.assertRaisesRegex(ValueError, 'covering the active cursor'):
            display.paint({'text': 'u', 'candidates': [REFERENCE], 'selectedIndex': 0})


if __name__ == '__main__':
    unittest.main()
