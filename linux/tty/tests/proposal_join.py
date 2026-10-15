"""Repeatable offline cross-repo proof, not live IME/bcon or model quality."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
from kitty_display import KittyDisplay


def blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def render(view_path, png_path, font):
    view = json.loads(Path(view_path).read_text())
    display = KittyDisplay(font, 24, 80, image_id=77)
    display.cursor = (3, 4)  # Explicit test coordinates, not a real terminal DSR.
    wire = display.update(view)
    chunks = re.findall(rb'\x1b_G([^;]*);([^\x1b]*)\x1b\\', wire)
    encoded = b''.join(data for params, data in chunks if b'a=T' in params or params.startswith(b'm='))
    png = base64.b64decode(encoded, validate=True)
    with Image.open(io.BytesIO(png)) as image:
        image.verify()
    assert display.update(None) == b'\x1b_Ga=d,d=I,i=77,q=2\x1b\\'
    Path(png_path).write_bytes(png)
    Path(str(png_path) + '.kitty').write_bytes(wire)


def quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ops-root', required=True, type=Path)
    parser.add_argument('--ui-root', required=True, type=Path)
    parser.add_argument('--font', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    roots = {'Meltype': ROOT, 'ops': args.ops_root.resolve(), 'ui': args.ui_root.resolve()}
    pins = json.loads((HERE / 'proposal_join.sources.json').read_text())
    for repo, files in pins.items():
        for path, expected in files.items():
            actual = blob(roots[repo] / path)
            if actual != expected:
                raise RuntimeError(f'SOURCE_MISMATCH {repo}/{path}: {actual}')
    out = args.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    # Prepared illustration: this is NOT a Mermaid compiler or fidelity proof.
    source = 'flowchart TB\nC["Comment"] --> P["Chosen proposal"]'
    image = Image.new('RGB', (280, 100), 'white'); draw = ImageDraw.Draw(image)
    draw.rectangle((5, 10, 105, 55), outline='black'); draw.text((15, 25), 'Comment', fill='black')
    draw.line((105, 33, 170, 33), fill='black', width=2)
    draw.polygon([(170, 33), (163, 29), (163, 37)], fill='black')
    draw.rectangle((170, 10, 275, 55), outline='black'); draw.text((178, 25), 'Selected', fill='black')
    draw.text((5, 75), 'Prepared fixture PNG', fill='black')
    stream = io.BytesIO(); image.save(stream, format='PNG')
    detail = {'format': 'mermaid', 'source': source, 'preview': {
        'png': base64.b64encode(stream.getvalue()).decode(),
        'sourceSha256': hashlib.sha256(source.encode()).hexdigest()}}
    records = [
        {'id': 'A', 'meaning': {'kind': 'text'}, 'representation': '共通処理を再利用する',
         'detail': {'format': 'jsonl', 'source': '{"id":"A","choice":"keep"}\n'}},
        {'id': 'B', 'meaning': {'kind': 'text'}, 'representation': '共通処理を再利用する', 'detail': detail},
    ]
    catalog = out / 'current.jsonl'
    catalog.write_text('\n'.join(json.dumps(row, ensure_ascii=False) for row in records) + '\n')
    results = []
    for case in ['append', 'cancel', 'edit', 'cursor', 'target']:
        view_path = out / f'{case}.view.json'; png_path = out / f'{case}.png'
        receipt_path = out / f'{case}.result.json'
        worker = shlex.join(['node', str(HERE / 'proposal_join.mjs'), str(roots['ops']), str(roots['ui']), str(catalog), 'B'])
        painter = shlex.join([sys.executable, str(Path(__file__).resolve()), '--render', str(view_path), str(png_path), str(args.font)])
        script = f'''scriptencoding utf-8
execute 'source ' . fnameescape({quoted(HERE.parent / 'editor_context.vim')})
enew!
call setline(1, ['# 日本語のコメント', '元の本文'])
call cursor(1, 3)
let q = MeltypeObserve(1, 1)
let raw = system({quoted(worker)}, json_encode(q))
if v:shell_error | call writefile([raw], '/dev/stderr') | cquit 2 | endif
let result = json_decode(raw)
call assert_equal(q, result.query.state.editor)
call assert_equal(q.target, result.query.focus.target)
call assert_equal('B', result.proposal.id)
call assert_equal('B', result.view.detail.id)
call writefile([json_encode(result.view)], {quoted(view_path)})
let rendered = system({quoted(painter)})
if v:shell_error | call writefile([rendered], '/dev/stderr') | cquit 3 | endif
let scenario = {quoted(case)}
if scenario ==# 'edit' | call setline(2, '自分による追加編集') | endif
if scenario ==# 'cursor' | call cursor(2, 1) | endif
if scenario ==# 'target'
  enew!
  call setline(1, q.lines)
  call cursor(1, 3)
endif
let before = getline(1, '$')
let status = 'CANCELLED'
if scenario ==# 'append'
  let receipt = MeltypeAppend(q, result.proposal)
  call assert_equal(['# 日本語のコメント', '', '共通処理を再利用する', '元の本文'], getline(1, '$'))
  call assert_equal('B', receipt.proposal.id)
  let status = receipt.status
  try
    call MeltypeAppend(q, result.proposal)
    call assert_report('duplicate append accepted')
  catch
    call assert_match('MELTYPE_STALE_CONTEXT', v:exception)
  endtry
elseif scenario !=# 'cancel'
  try
    call MeltypeAppend(q, result.proposal)
    call assert_report('stale append accepted')
  catch
    call assert_match('MELTYPE_STALE_CONTEXT', v:exception)
    let status = 'REFUSED_STALE'
  endtry
  call assert_equal(before, getline(1, '$'))
else
  call assert_equal(q.lines, getline(1, '$'))
endif
if len(v:errors) | call writefile(v:errors, '/dev/stderr') | cquit 4 | endif
call writefile([json_encode({{'case': scenario, 'status': status, 'id': result.proposal.id,
      \\ 'requestId': result.selection.requestId, 'lines': getline(1, '$')}})], {quoted(receipt_path)})
qa!
'''
        script_path = out / f'{case}.vim'; script_path.write_text(script)
        completed = subprocess.run(['vim', '-Nu', 'NONE', '-i', 'NONE', '-n', '-es', '-S', str(script_path)],
                                   capture_output=True, text=True, timeout=15)
        if completed.returncode:
            raise RuntimeError(f'{case}: exit {completed.returncode}\n{completed.stdout}\n{completed.stderr}')
        result = json.loads(receipt_path.read_text())
        result['pngSha256'] = hashlib.sha256(png_path.read_bytes()).hexdigest()
        results.append(result)
    proof = {'status': 'PASS', 'cases': results, 'sourceBlobs': pins,
             'scope': 'real Vim + real semcmp/review/rank + detailOf + Kitty PNG encoding + checked append/readback',
             'provider': 'controlled fixture, no network',
             'notProven': ['live Meltype key loop/NativeAOT', 'bcon/NixOS/physical device', 'Mermaid compilation',
                           'real backend/model quality', 'persistent selection history', 'independent review']}
    (out / 'proof.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'PASS', 'cases': len(results), 'provider': 'fixture', 'proof': str(out / 'proof.json')}))


if __name__ == '__main__':
    if sys.argv[1:2] == ['--render']:
        render(*sys.argv[2:])
    else:
        main()
