# SPDX-License-Identifier: GPL-3.0-or-later
"""Real NativeAOT/Mozc/CLI/PTY and PNG transport, controlled terminal replies.
Not an actual bcon rendering or physical key test. No font is copied or shipped.
"""
import base64,fcntl,io,json,os,re,select,struct,subprocess,sys,tempfile,termios,time
from pathlib import Path
from PIL import Image
root=Path(__file__).resolve().parents[1]
pkg=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else None
if pkg is None: raise SystemExit('required: same-build Meltype-linux package')
font=os.environ.get('MELTYPE_TTY_TEST_FONT','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')
with tempfile.TemporaryDirectory(prefix='meltype-cli-proof-') as home:
 master,slave=os.openpty()
 fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,800,624))
 original=termios.tcgetattr(slave)
 env={**os.environ,'HOME':home,'XDG_CONFIG_HOME':home+'/config','XDG_DATA_HOME':home+'/data','XDG_CACHE_HOME':home+'/cache','PYTHON_COLORS':'0'}
 child="import os,tty;tty.setraw(0);os.write(1,b'\\x1b[10;4HREADY\\x1b[10;4H');v=b''\nwhile len(v)<12:v+=os.read(0,12-len(v))\nos.write(1,b'SINK:'+v.hex().encode())"
 args=[sys.executable,str(root/'meltype_tty.py'),'--native',str(pkg/'libMeltypeNative.so'),'--mozc',str(pkg/'mozc/meltype_mozc_helper'),'--font',font,'--',sys.executable,'-c',child]
 p=subprocess.Popen(args,stdin=slave,stdout=slave,stderr=slave,env=env,start_new_session=True)
 out=b'';sent=False;queries=0;probed=False;deadline=time.monotonic()+15
 try:
  while time.monotonic()<deadline:
   ready=select.select([master],[],[],.05)[0]
   if ready:
    block=os.read(master,65536);out+=block
    probe=re.search(rb'\x1b_Ga=q,i=([0-9]+),',out)
    if probe and not probed:
     os.write(master,b'\x1b_Gi='+probe[1]+b';OK\x1b\\');probed=True
    q=out.count(b'\x1b[6n')
    if q>queries:
     os.write(master,b'\x1b[10;4R'*(q-queries));queries=q
    if not sent and b'READY' in out:
     os.write(master,b'nihongo \x1b[B\r');sent=True
   if p.poll() is not None:break
  if p.poll() is None:
   p.terminate();p.wait(2);raise RuntimeError('timeout')
  assert p.returncode==0, (p.returncode,out[-500:])
  # Drain final bytes without waiting for the still-open outer slave.
  while select.select([master],[],[],.01)[0]: out+=os.read(master,65536)
  assert b'SINK:'+ 'ニホンゴ'.encode().hex().encode() in out, out[-500:]
  assert termios.tcgetattr(slave)==original
  frames=re.findall(rb'\x1b_G([^;\x1b]*);(.*?)\x1b\\',out)
  images=[];parts=[]
  for head,data in frames:
   if b'a=T' in head: parts=[]
   parts.append(data)
   if b'm=0' in head:
    image=Image.open(io.BytesIO(base64.b64decode(b''.join(parts))));image.load();images.append(image.copy())
  assert len(images)>=3
  import hashlib
  hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [root/'meltype_tty.py',root/'kitty_display.py',pkg/'libMeltypeNative.so',pkg/'mozc/meltype_mozc_helper']}
  print(json.dumps({'hashes':hashes,'gate':'real-native-cli+pty+image-transport','images':len(images),'cursor_queries':queries,'output_bytes':len(out),'child_text':'ニホンゴ','exit_status':p.returncode,'terminal_restored':True,'bcon':'NOT_RUN'}))
 finally:
  if p.poll() is None:p.kill();p.wait()
  os.close(master);os.close(slave)
