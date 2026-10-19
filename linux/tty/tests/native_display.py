# SPDX-License-Identifier: GPL-3.0-or-later
"""Real NativeAOT/Mozc/CLI/PTY and PNG transport; controlled terminal replies.

Both the native converter and optional local typed provider use the real CLI.
This is not actual bcon rendering or physical key evidence. No fonts are shipped.
"""
import base64
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import io
import json
import os
from pathlib import Path
import re
import select
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from PIL import Image

root=Path(__file__).resolve().parents[1]
pkg=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else None
if pkg is None: raise SystemExit('required: same-build Meltype-linux package')
font=os.environ.get('MELTYPE_TTY_TEST_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')

def images(output):
    decoded=[]
    parts=[]
    for head,data in re.findall(rb'\x1b_G([^;\x1b]*);(.*?)\x1b\\',output):
        if b'a=T' in head: parts=[]
        parts.append(data)
        if b'm=0' in head:
            image=Image.open(io.BytesIO(base64.b64decode(b''.join(parts))))
            image.load()
            decoded.append(image.copy())
    return decoded

def run_case(expected, prefix, provider=None, release_provider=None):
    with tempfile.TemporaryDirectory(prefix='meltype-cli-proof-') as home:
        master,slave=os.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,800,624))
        original=termios.tcgetattr(slave)
        env={**os.environ,'HOME':home,'XDG_CONFIG_HOME':home+'/config',
            'XDG_DATA_HOME':home+'/data','XDG_CACHE_HOME':home+'/cache','PYTHON_COLORS':'0'}
        env.pop('MELTYPE_PROPOSAL_URL',None)
        if provider is not None: env['MELTYPE_PROPOSAL_URL']=provider
        length=len(expected.encode())
        child=("import os,tty,termios;tty.setraw(0,termios.TCSANOW);"
            "os.write(1,b'\\x1b[10;4HREADY\\x1b[10;4H');v=b''\n"
            f"while len(v)<{length}:v+=os.read(0,{length}-len(v))\n"
            "os.write(1,b'SINK:'+v.hex().encode())")
        # The composition mode: these cases prove preedit and candidate images from the first key.
        args=[sys.executable,str(root/'meltype_tty.py'),'--raw-text','--compose','--native',str(pkg/'libMeltypeNative.so'),
            '--mozc',str(pkg/'mozc/meltype_mozc_helper'),'--font',font,
            '--',sys.executable,'-c',child]
        process=subprocess.Popen(args,stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
        output=b''
        sent=selected=probed=False
        queries=0
        preedit_count=0
        candidate_count=0
        candidate_pixels=None
        confirmed=False
        termination_requested=False
        before_termination_status=None
        cleanup_output=b''
        deadline=time.monotonic()+15
        def preserve_failure():
            # This test uses only the controlled fixture above. Keep the real
            # PTY bytes and rendered frames through the existing failure artifact
            # path, without changing input order, deadlines or success criteria.
            directory=Path(os.environ.get('RUNNER_TEMP',tempfile.gettempdir()))/'meltype-bcon'
            directory.mkdir(exist_ok=True)
            record={'gate':'native-display-failure','provider':'controlled' if provider else 'native Mozc',
                'sent':sent,'selected':selected,'probed':probed,'confirmed':confirmed,
                'queries':queries,'returncode':process.poll(),
                'terminationRequested':termination_requested,'beforeTerminationStatus':before_termination_status,
                'cleanupOutputBase64':base64.b64encode(cleanup_output).decode(),
                'outputBytes':len(output),'outputSha256':hashlib.sha256(output).hexdigest(),
                'outputBase64':base64.b64encode(output).decode(),'external_models':'NOT_CALLED'}
            (directory.parent/'meltype-bcon.log').write_text(json.dumps(record)+'\n')
            try:
                frames=images(output)
                for index,image in enumerate(frames):
                    image.save(directory/f'native-display-failure-{index}.png')
            except (OSError, ValueError):
                pass  # The exact raw output above remains the primary evidence.
        try:
            while time.monotonic()<deadline:
                if select.select([master],[],[],.05)[0]:
                    output+=os.read(master,65536)
                    probe=re.search(rb'\x1b_Ga=q,i=([0-9]+),',output)
                    if probe and not probed:
                        os.write(master,b'\x1b_Gi='+probe[1]+b';OK\x1b\\')
                        probed=True
                    q=output.count(b'\x1b[6n')
                    if q>queries:
                        os.write(master,b'\x1b[10;4R'*(q-queries))
                        queries=q
                    if not sent and b'READY' in output:
                        os.write(master,prefix)
                        sent=True
                    if provider is not None:
                        rendered=images(output)
                        # This fixture deliberately supplies a delayed response:
                        # first observe real preedit PNG, then allow its HTTP
                        # response. Scheduling must not erase a required stage.
                        if not preedit_count and rendered:
                            preedit_count=len(rendered)
                            release_provider.set()
                        elif not selected and any(im.height>=104 for im in rendered[preedit_count:]):
                            assert b'SINK:' not in output
                            candidate_count=len(rendered)
                            candidate_pixels=(rendered[-1].size,rendered[-1].tobytes())
                            os.write(master,b'\x1b[B')
                            selected=True
                        elif selected and not confirmed and len(rendered)>candidate_count:
                            assert (rendered[-1].size,rendered[-1].tobytes())!=candidate_pixels
                            assert b'SINK:' not in output
                            os.write(master,b'\r')
                            confirmed=True
                if process.poll() is not None: break
            if process.poll() is None:
                before_termination_status=process.poll()
                termination_requested=True
                process.terminate();process.wait(2)
                raise RuntimeError('real CLI proof timeout')
            assert process.returncode==0,(process.returncode,output[-500:])
            while select.select([master],[],[],.01)[0]: output+=os.read(master,65536)
            assert b'SINK:'+expected.encode().hex().encode() in output,output[-500:]
            assert termios.tcgetattr(slave)==original
            if provider is not None:
                assert preedit_count>0 and candidate_count>preedit_count and confirmed
            frames=images(output)
            assert len(frames)>=3
            hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                [root/'meltype_tty.py',root/'kitty_display.py',pkg/'libMeltypeNative.so',pkg/'mozc/meltype_mozc_helper']}
            print(json.dumps({'hashes':hashes,'gate':'real-native-cli+pty+image-transport',
                'provider':'local typed HTTP' if provider else 'native Mozc',
                'images':len(frames),'cursor_queries':queries,'output_bytes':len(output),
                'preedit_then_candidates_then_selected':confirmed if provider else None,
                'child_text':expected,'exit_status':process.returncode,'terminal_restored':True,
                'bcon':'NOT_RUN','external_models':'NOT_CALLED'}),flush=True)
        except BaseException:
            try:
                # Evidence only after the original failure; no extra input or
                # pre-assert reads that would alter the scenario's timing.
                for _ in range(16):
                    if not select.select([master],[],[],0)[0]: break
                    try: part=os.read(master,65536)
                    except OSError: break
                    if not part: break
                    cleanup_output+=part
                preserve_failure()
            except BaseException as collector_error:
                print('native-display evidence collection failed: '+type(collector_error).__name__,flush=True)
            raise
        finally:
            if process.poll() is None: process.kill();process.wait()
            os.close(master);os.close(slave)

run_case('ニホンゴ',b'nihongo \x1b[B\r')
seen=[]
release_provider=threading.Event()
class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        query=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        seen.append(query)
        assert release_provider.wait(2), 'real preedit image was not observed'
        proposals=[{'id':id,'representation':text,'meaning':{'kind':'text','id':id},'evidence':['local-fixture']}
            for id,text in [('a','第一候補'),('b','第二候補')]]
        body=json.dumps({**query,'proposals':proposals}).encode()
        self.send_response(200);self.end_headers();self.wfile.write(body)
    def log_message(self,*_): pass
server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
threading.Thread(target=server.serve_forever,daemon=True).start()
try:
    run_case('第二候補',b'u',f'http://127.0.0.1:{server.server_port}/proposal',release_provider)
    assert seen and seen[0]['raw']=='u',seen
finally:
    server.shutdown();server.server_close()
