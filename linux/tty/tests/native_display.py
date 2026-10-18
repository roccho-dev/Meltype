# SPDX-License-Identifier: GPL-3.0-or-later
"""Real NativeAOT/Mozc/CLI/PTY and PNG transport; controlled terminal replies.

The native converter uses the real CLI.
This is not actual bcon rendering or physical key evidence. No fonts are shipped.
"""
import base64
import fcntl
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

def run_case(expected, prefix):
    with tempfile.TemporaryDirectory(prefix='meltype-cli-proof-') as home:
        master,slave=os.openpty()
        fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,800,624))
        original=termios.tcgetattr(slave)
        env={**os.environ,'HOME':home,'XDG_CONFIG_HOME':home+'/config',
            'XDG_DATA_HOME':home+'/data','XDG_CACHE_HOME':home+'/cache','PYTHON_COLORS':'0'}
        length=len(expected.encode())
        child=("import os,tty,termios;tty.setraw(0,termios.TCSANOW);"
            "os.write(1,b'\\x1b[10;4HREADY\\x1b[10;4H');v=b''\n"
            f"while len(v)<{length}:v+=os.read(0,{length}-len(v))\n"
            "os.write(1,b'SINK:'+v.hex().encode())")
        # The composition mode: these cases prove preedit and candidate images from the first key.
        args=[sys.executable,str(root/'meltype_tty.py'),'--raw-text','--compose','--native',str(pkg/'libMeltypeNative.so'),
            '--mozc',str(pkg/'mozc/meltype_mozc_helper'),'--font',font,'--',sys.executable,'-c',child]
        process=subprocess.Popen(args,stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
        output=b''
        sent=probed=False
        queries=0
        deadline=time.monotonic()+15
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
                if process.poll() is not None: break
            if process.poll() is None:
                process.terminate();process.wait(2)
                raise RuntimeError('real CLI proof timeout')
            assert process.returncode==0,(process.returncode,output[-500:])
            while select.select([master],[],[],.01)[0]: output+=os.read(master,65536)
            assert b'SINK:'+expected.encode().hex().encode() in output,output[-500:]
            assert termios.tcgetattr(slave)==original
            frames=images(output)
            assert len(frames)>=3
            hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                [root/'meltype_tty.py',root/'kitty_display.py',pkg/'libMeltypeNative.so',pkg/'mozc/meltype_mozc_helper']}
            print(json.dumps({'hashes':hashes,'gate':'real-native-cli+pty+image-transport',
                'provider':'native Mozc','images':len(frames),'cursor_queries':queries,'output_bytes':len(output),
                'child_text':expected,'exit_status':process.returncode,'terminal_restored':True,
                'bcon':'NOT_RUN','external_models':'NOT_CALLED'}),flush=True)
        finally:
            if process.poll() is None: process.kill();process.wait()
            os.close(master);os.close(slave)

run_case('ニホンゴ',b'nihongo \x1b[B\r')
