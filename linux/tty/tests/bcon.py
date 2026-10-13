# SPDX-License-Identifier: GPL-3.0-or-later
# Real NixOS test VM, unchanged bcon, virtual physical keyboard, real scanout.
import hashlib
import base64
import json
from pathlib import Path
import shlex
import time
from PIL import Image, ImageChops

start_all()
machine.wait_for_unit("meltype-test-console.service")
machine.succeed('test "$(cat /sys/class/tty/tty0/active)" = tty2')
machine.wait_until_succeeds("pgrep -x login")
machine.send_chars("root\n")
machine.wait_until_succeeds("pgrep -t pts/0 -x bash")
machine.succeed("mkdir -p /run/meltype-proof")

# Fixture setup is not text under test. Do not type a long shell program into
# an asynchronously redrawn terminal: launch a prepared script with one short
# physical command, then send only the specified product input via the keyboard.
def launch_trial(name, command):
    prefix = "/run/meltype-proof/" + name
    script = (f"stty -g > {prefix}-before\n"
        f"{command} 2>{prefix}-errors\nrc=$?\n"
        f"stty -g > {prefix}-after\nprintf '%s' \"$rc\" > {prefix}-exit\n")
    machine.succeed("printf %s " + shlex.quote(script) + " > " + prefix + "-trial")
    machine.send_chars("bash " + prefix + "-trial\n")
# The sink records the real child's input. It never supplies candidates,
# terminal replies, or injected Meltype input.
sink="""import os,tty
from pathlib import Path
tty.setraw(0)
os.write(1,b'\\x1b[10;4HREADY\\x1b[10;4H')
Path('/run/meltype-proof/ready').touch()
value=b''
Path('/run/meltype-proof/input').write_bytes(value)
while len(value)<12:
    value+=os.read(0,12-len(value))
    Path('/run/meltype-proof/input').write_bytes(value)
os.write(1,b'\\r\\nSINK:'+value.hex().encode()+b'\\r\\n')
"""
machine.succeed("printf %s "+shlex.quote(sink)+" > /run/meltype-proof/sink.py")
# Compare the same foreground-command phase. Readline changes termios while
# waiting at the prompt, so a remote pre-command snapshot is a false oracle.
launch_trial("native", "meltype-tty -- python3 /run/meltype-proof/sink.py")
machine.wait_until_succeeds("test -e /run/meltype-proof/ready")

def scanout(name):
    machine.screenshot(name)
    with Image.open(Path(machine.out_dir)/(name+".png")) as im:
        return im.convert("RGB").copy()

def panel_pixels(im):
    colors=im.getcolors(im.width*im.height)
    assert colors is not None
    return sum(count for count,rgb in colors if all(abs(a-b)<=2 for a,b in zip(rgb,(24,26,32))))

before=scanout("bcon-before-candidates")
machine.send_chars("nihongo ")
deadline=time.monotonic()+15
while True:
    shown=scanout("bcon-candidates")
    if panel_pixels(shown)>panel_pixels(before)+1000: break
    assert time.monotonic()<deadline, machine.succeed("cat /run/meltype-proof/native-errors")
    time.sleep(.2)
machine.succeed("test ! -s /run/meltype-proof/input")
machine.send_key("down")
time.sleep(.5)
chosen=scanout("bcon-selected")
assert ImageChops.difference(shown,chosen).getbbox(), "selection did not change actual scanout"
machine.send_key("ret")
machine.wait_until_succeeds("test -e /run/meltype-proof/native-exit")
observed=machine.succeed("od -An -tx1 -v /run/meltype-proof/input").split()
assert "".join(observed)=="ニホンゴ".encode().hex(), observed
assert machine.succeed("cat /run/meltype-proof/native-exit").strip()=="0"
original=machine.succeed("cat /run/meltype-proof/native-before").strip()
restored=machine.succeed("cat /run/meltype-proof/native-after").strip()
assert restored==original, {"before":original,"after":restored}
machine.fail("pgrep -f '[f]citx5|[i]bus-daemon|[t]tyskk'")
after=scanout("bcon-after-commit")
assert panel_pixels(after)<panel_pixels(shown), "candidate image survived completion"
print(json.dumps({"gate":"nixos-vm-bcon-native-pty-scanout","status":"PASS",
    "input":"nihongo / Down / Enter","child_text":"ニホンゴ",
    "bcon":"ca30c939f5ae48fff10eb8dc26ea05b179bef680","ime":False,
    "preedit_not_in_child":True,"candidate_scanout_pixels":panel_pixels(shown),
    "candidate_png_sha256":hashlib.sha256((Path(machine.out_dir)/"bcon-candidates.png").read_bytes()).hexdigest(),
    "exit":0,"terminal_restored":True,
    "target_laptop":"NOT_RUN","browser_dom":"NOT_RUN","independent_review":"NOT_RUN"}),flush=True)

# Attach the unchanged viewer to the already running, test-owned browser target.
# CDP below is read-only observation; the only text input is virtual keyboard →
# bcon → Meltype → cdp-tty. The test never calls Input.insertText or dispatchKeyEvent.
machine.wait_for_unit('meltype-test-browser.service')
probe = r'''import json,sys,urllib.request,websocket
from pathlib import Path
with urllib.request.urlopen('http://127.0.0.1:9222/json/list',timeout=2) as response:
    pages=[p for p in json.load(response) if p.get('type')=='page']
assert len(pages)==1,pages
page=pages[0]
identity=Path('/run/meltype-proof/browser-target')
if identity.exists(): assert identity.read_text()==page['id']
else: identity.write_text(page['id'])
Path('/run/meltype-proof/browser-ws').write_text(page['webSocketDebuggerUrl'])
ws=websocket.create_connection(page['webSocketDebuggerUrl'],timeout=2,suppress_origin=True)
try:
    ws.send(json.dumps({'id':1,'method':'Runtime.evaluate','params':{
        'expression':"JSON.stringify({text:document.getElementById('editor').value,focus:document.activeElement.id})",
        'returnByValue':True}}))
    while True:
        data=json.loads(ws.recv())
        if data.get('id')==1: break
    assert 'error' not in data and 'exceptionDetails' not in data['result'],data
    value=json.loads(data['result']['result']['value'])
    assert value=={'text':sys.argv[1],'focus':'editor'},value
    print(json.dumps({'target':page['id'],**value},ensure_ascii=False))
finally: ws.close()
'''
machine.succeed('printf %s '+shlex.quote(probe)+' > /run/meltype-proof/browser-probe.py')
def browser_value(expected):
    return 'python3 /run/meltype-proof/browser-probe.py '+shlex.quote(expected)
machine.wait_until_succeeds(browser_value(''))
browser_pid=machine.succeed('systemctl show -p MainPID --value meltype-test-browser').strip()
launch_trial("browser", 'meltype-tty -- cdp-tty "$(cat /run/meltype-proof/browser-ws)"')

def color_pixels(im,color):
    return sum(count for count,rgb in im.getcolors(im.width*im.height)
        if all(abs(a-b)<=2 for a,b in zip(rgb,color)))

def wait_scanout(name,check):
    deadline=time.monotonic()+15
    while True:
        image=scanout(name)
        if check(image): return image
        if time.monotonic()>=deadline:
            # A failed Nix derivation cannot publish its $out images. Keep one
            # bounded fixture-only image in the existing test log for diagnosis.
            png=(Path(machine.out_dir)/(name+'.png')).read_bytes()
            assert len(png)<=1024*1024
            print('MELTYPE_FAILURE_PNG '+name+' '+base64.b64encode(png).decode(),flush=True)
            print(machine.succeed("ps -eo pid,ppid,tty,args; cat /run/meltype-proof/*-errors; cat /run/meltype-proof/*-exit; cat /run/meltype-proof/native-trace 2>/dev/null || true; cat /run/meltype-proof/proposal-query 2>/dev/null || true"),flush=True)
            raise AssertionError('actual browser scanout not observed: '+name)
        time.sleep(.2)

base=wait_scanout('browser-before-input',lambda im:color_pixels(im,(215,225,240))>2000)
machine.send_chars('nihongo ')
wait_scanout('browser-native-candidates',lambda im:panel_pixels(im)>panel_pixels(base)+1000)
machine.succeed(browser_value(''))
machine.send_key('ret')
machine.wait_until_succeeds(browser_value('日本語'))
scanout('browser-after-commit')
machine.send_key('ctrl-q')
machine.wait_until_succeeds('test -e /run/meltype-proof/browser-exit')
assert machine.succeed('cat /run/meltype-proof/browser-exit').strip()=='0'
assert machine.succeed('cat /run/meltype-proof/browser-before')==machine.succeed('cat /run/meltype-proof/browser-after')
assert machine.succeed('systemctl show -p MainPID --value meltype-test-browser').strip()==browser_pid
readback=json.loads(machine.succeed(browser_value('日本語')))
print(json.dumps({'gate':'bcon-meltype-cdp-tty-existing-browser-dom','status':'PASS',
    'readback':readback,'browser_pid_preserved':True,'viewer_exit':0,
    'terminal_restored':True,'text_input':'virtual keyboard only',
    'production_browser':'NOT_TOUCHED','target_laptop':'NOT_RUN',
    'independent_review':'NOT_RUN'},ensure_ascii=False),flush=True)

# The optional provider remains a test fixture. This proves the real input and
# rendering path, not the quality of a production ranker or shared source binding.
provider = r'''from http.server import BaseHTTPRequestHandler,HTTPServer
import json
from pathlib import Path
class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        query=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Path('/run/meltype-proof/proposal-query').write_text(json.dumps(query))
        body=json.dumps({**query,'proposals':[
            {'id':'typed-A','representation':'甲候補','meaning':{'kind':'text','value':'A'},'evidence':['test-source','A']},
            {'id':'typed-B','representation':'乙候補','meaning':{'kind':'text','value':'B'},'evidence':['test-source','B']}]}).encode()
        self.send_response(200);self.end_headers();self.wfile.write(body)
    def log_message(self,*_): pass
server=HTTPServer(('127.0.0.1',8031),Provider)
Path('/run/meltype-proof/provider-ready').touch()
server.serve_forever()
'''
machine.succeed('printf %s '+shlex.quote(provider)+' > /run/meltype-proof/provider.py')
machine.succeed('systemd-run --unit=meltype-test-proposals --service-type=exec /run/current-system/sw/bin/python3 /run/meltype-proof/provider.py')
machine.wait_until_succeeds('test -e /run/meltype-proof/provider-ready')
# Read-only trace of the fixture's real CLI. It does not alter keys or native
# results and is never installed in the product or enabled outside this VM.
trace = r'''import json,sys,os
out=open('/run/meltype-proof/native-trace','a',buffering=1)
out.write(json.dumps({'pid':os.getpid(),'argv':sys.argv})+'\n')
count=0
def profile(frame,event,result):
    global count
    if count>=100 or not frame.f_code.co_filename.endswith('meltype_tty.py'): return
    name=frame.f_code.co_name
    data=None
    if event=='call' and name=='run': data={'run':frame.f_locals.get('argv')}
    if event=='call' and name=='_native_key': data={'key':{k:repr(frame.f_locals.get(k)) for k in ['vk','char','modifiers','original']}}
    if event=='return' and name=='poll_proposals' and result is not None: data={'response':result}
    if data is not None:
        count+=1;out.write(json.dumps(data,ensure_ascii=False)+'\n')
sys.setprofile(profile)
'''
machine.succeed('mkdir -p /run/meltype-proof/trace; printf %s '+shlex.quote(trace)+' > /run/meltype-proof/trace/sitecustomize.py')
launch_trial("typed", 'PYTHONPATH=/run/meltype-proof/trace MELTYPE_PROPOSAL_URL=http://127.0.0.1:8031/proposal meltype-tty -- cdp-tty "$(cat /run/meltype-proof/browser-ws)"')
base=wait_scanout('typed-browser-before',lambda im:color_pixels(im,(215,225,240))>2000)
machine.send_chars('u')
# Native "u" preedit alone is one 600x52 image. Require the taller real
# provider-candidate panel, not merely any different or dark screen pixels.
typed_panel=wait_scanout('typed-browser-candidates',lambda im:panel_pixels(im)>panel_pixels(base)+45000)
query=json.loads(machine.succeed('cat /run/meltype-proof/proposal-query'))
assert query['raw']=='u' and query['generation']>0,query
machine.succeed(browser_value('日本語'))
machine.send_key('down')
time.sleep(.3)
scanout('typed-browser-selected')
machine.send_key('ret')
machine.wait_until_succeeds(browser_value('日本語乙候補'))
scanout('typed-browser-committed')
machine.send_key('ctrl-q')
machine.wait_until_succeeds('test -e /run/meltype-proof/typed-exit')
assert machine.succeed('cat /run/meltype-proof/typed-exit').strip()=='0'
assert machine.succeed('cat /run/meltype-proof/typed-before')==machine.succeed('cat /run/meltype-proof/typed-after')
assert machine.succeed('systemctl show -p MainPID --value meltype-test-browser').strip()==browser_pid
readback=json.loads(machine.succeed(browser_value('日本語乙候補')))
machine.succeed('systemctl stop meltype-test-proposals')
print(json.dumps({'gate':'bcon-local-typed-proposal-cli-cdp-dom','status':'PASS',
    'readback':readback,'raw':query['raw'],'provider':'local test fixture',
    'displayed_candidates':2,'selected_text':'乙候補','viewer_exit':0,
    'terminal_restored':True,'browser_pid_preserved':True,
    'text_input':'virtual keyboard only','external_models':'NOT_CALLED',
    'shared_source_binding':'NOT_RUN','independent_review':'NOT_RUN'},ensure_ascii=False),flush=True)
