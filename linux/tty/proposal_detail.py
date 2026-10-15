# SPDX-License-Identifier: GPL-3.0-or-later
"""Add selected detail inside the existing single owned Kitty image."""
import base64
import hashlib
import io
import re
from PIL import Image, ImageDraw


def compose_detail(image, view, font, rows, plain):
    detail = view['detail']
    if (not isinstance(detail, dict)
            or not re.fullmatch(r'[a-f0-9]{64}', str(view.get('requestId', '')))
            or detail.get('requestId') != view['requestId']
            or detail.get('id') != view.get('selectedId')
            or detail.get('index') != view.get('selectedIndex')):
        raise ValueError('STALE_DETAIL')
    source = detail.get('source')
    if not isinstance(source, str) or len(source.encode('utf-8')) > 16384:
        raise ValueError('INVALID_DETAIL_SOURCE')
    extra_rows = min(6, rows - image.height // 26)
    if extra_rows < 3 or image.width < 320:
        raise ValueError('DETAIL_VIEW_TOO_SMALL')
    preview = detail.get('preview')
    decoded = None
    if preview is not None:
        png = preview.get('png')
        if (not isinstance(png, str) or len(png) > 699052
                or preview.get('sourceSha256') != hashlib.sha256(source.encode('utf-8')).hexdigest()):
            raise ValueError('PREVIEW_SOURCE_MISMATCH')
        raw = base64.b64decode(png, validate=True)
        with Image.open(io.BytesIO(raw)) as candidate:
            if candidate.format != 'PNG' or max(candidate.size) > 2048 or candidate.width * candidate.height > 1048576:
                raise ValueError('INVALID_PNG_SIZE')
            candidate.verify()
        with Image.open(io.BytesIO(raw)) as candidate:
            decoded = candidate.convert('RGB')
    out = Image.new('RGB', (image.width, image.height + extra_rows * 26), (24, 26, 32))
    out.paste(image, (0, 0))
    draw = ImageDraw.Draw(out)
    draw.line((0, image.height, image.width, image.height), fill=(100, 110, 125))
    title = f"{detail.get('format', 'text')} | {detail['id']} | source excerpt"
    draw.text((4, image.height + 2), plain(title), font=font, fill=(240, 242, 246))
    text_width = image.width if decoded is None else image.width * 3 // 5
    # Explicit excerpt, while ui's source remains exact and rereadable.
    source_lines = source.splitlines()[:extra_rows - 2]
    for index, line in enumerate(source_lines):
        line = plain(line)
        while line and font.getlength(line + '…') > text_width - 8:
            line = line[:-1]
        draw.text((4, image.height + 28 + index * 26), line + '…', font=font, fill=(220, 225, 235))
    if decoded is not None:
        decoded.thumbnail((image.width - text_width - 8, (extra_rows - 1) * 26 - 8))
        out.paste(decoded, (text_width + 4, image.height + 30))
    return out
