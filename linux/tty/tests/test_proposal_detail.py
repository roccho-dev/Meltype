"""Prepared rich-detail proof; not a real bcon or Japanese-engine acceptance."""
import base64
import copy
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import re
import sys
import unittest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kitty_display import KittyDisplay

FONT = os.environ.get('MELTYPE_TEST_FONT', '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
SOURCE = 'flowchart TB\nA["Comment"]-->B["Proposal"]'


def fixture():
    stream = io.BytesIO()
    Image.new('RGB', (40, 30), (20, 170, 80)).save(stream, format='PNG')
    return {'requestId': 'a' * 64, 'selectedId': 'B', 'selectedIndex': 1,
            'text': '# 日本語のコメント', 'candidates': ['同じ表示', '同じ表示'],
            'meaning': 'text', 'notes': ['A', 'B'],
            'detail': {'requestId': 'a' * 64, 'id': 'B', 'index': 1, 'format': 'mermaid',
                       'source': SOURCE, 'preview': {'png': base64.b64encode(stream.getvalue()).decode(),
                       'sourceSha256': hashlib.sha256(SOURCE.encode()).hexdigest()}}}


def decode(data):
    chunks = re.findall(rb'\x1b_G([^;]*);([^\x1b]*)\x1b\\', data)
    raw = base64.b64decode(b''.join(payload for params, payload in chunks if b'a=T' in params or params.startswith(b'm=')), validate=True)
    image = Image.open(io.BytesIO(raw)); image.load()
    return raw, image


class DetailTests(unittest.TestCase):
    def display(self):
        display = KittyDisplay(FONT, 24, 80, image_id=77); display.cursor = (3, 4)
        return display

    def test_rich_image_is_one_owned_png(self):
        raw, image = decode(self.display().update(fixture()))
        self.assertEqual(image.size, (600, 312))
        self.assertEqual(image.getpixel((364, 186)), (20, 170, 80))
        self.assertTrue(raw.startswith(b'\x89PNG'))

    def test_cancel_deletes_only_owned_image(self):
        d = self.display(); d.update(fixture())
        self.assertEqual(d.update(None), b'\x1b_Ga=d,d=I,i=77,q=2\x1b\\')
        self.assertEqual(d.update(None), b'')

    def test_child_output_is_preserved(self):
        d = self.display(); d.update(fixture())
        data = '日本語\r\n'.encode()
        self.assertTrue(d.output(data).endswith(data)); self.assertIsNone(d.cursor)

    def test_no_interleaving_in_split_child_sequence(self):
        d = self.display(); d.output(b'\x1b[')
        self.assertEqual(d.update(fixture()), b'')
        d.output(b'0m'); self.assertEqual(d.update(fixture()), b'\x1b[6n')

    def test_resize_requeries_cursor(self):
        d = self.display(); d.update(fixture()); d.resize(20, 60)
        self.assertEqual(d.update(fixture()), b'\x1b[6n')

    def test_stale_request_id_index_each_refused(self):
        for key, value in [('requestId', 'b' * 64), ('id', 'A'), ('index', 0)]:
            with self.subTest(key=key):
                view = fixture(); view['detail'][key] = value
                with self.assertRaisesRegex(ValueError, 'STALE_DETAIL'): self.display().update(view)

    def test_image_source_mismatch_refused(self):
        view = fixture(); view['detail']['source'] += ' changed'
        with self.assertRaisesRegex(ValueError, 'PREVIEW_SOURCE_MISMATCH'): self.display().update(view)

    def test_corrupt_image_refused(self):
        view = fixture(); view['detail']['preview']['png'] = base64.b64encode(b'bad PNG').decode()
        with self.assertRaises(Exception): self.display().update(view)

    def test_refused_replacement_can_still_clear_old_image(self):
        d = self.display(); d.update(fixture()); invalid = fixture(); invalid['detail']['id'] = 'A'
        with self.assertRaises(ValueError): d.update(invalid)
        self.assertTrue(d.visible)
        self.assertIn(b'a=d,d=I,i=77', d.update(None))

    def test_small_view_refuses_invisible_details(self):
        d = self.display(); d.rows = 7
        with self.assertRaisesRegex(ValueError, 'DETAIL_VIEW_TOO_SMALL'): d.update(fixture())

    def test_no_detail_default_render_matches_exact_baseline(self):
        path = os.environ.get('MELTYPE_KITTY_BASELINE')
        if not path: self.skipTest('supply pinned baseline path for byte comparison')
        spec = importlib.util.spec_from_file_location('kitty_baseline', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        for candidates in [['漢字', '感じ'], ['abc'], []]:
            view = {'text': 'にほんご', 'candidates': candidates, 'selectedIndex': 0, 'meaning': '意味', 'notes': ['根拠て']}
            old = module.KittyDisplay(FONT, 24, 80, image_id=77); old.cursor = (3, 4)
            self.assertEqual(old.update(copy.deepcopy(view)), self.display().update(view))


if __name__ == '__main__':
    unittest.main()
