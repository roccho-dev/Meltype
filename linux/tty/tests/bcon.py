# SPDX-License-Identifier: GPL-3.0-or-later
# Real NixOS test VM, unchanged bcon, virtual physical keyboard, real scanout.
import hashlib
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
machine.send_chars("stty -g > /run/meltype-proof/termios-before; meltype-tty -- python3 /run/meltype-proof/sink.py 2>/run/meltype-proof/errors; rc=$?; stty -g > /run/meltype-proof/termios-after; printf '%s' \"$rc\" > /run/meltype-proof/exit\n")
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
    assert time.monotonic()<deadline, machine.succeed("cat /run/meltype-proof/errors")
    time.sleep(.2)
machine.succeed("test ! -s /run/meltype-proof/input")
machine.send_key("down")
time.sleep(.5)
chosen=scanout("bcon-selected")
assert ImageChops.difference(shown,chosen).getbbox(), "selection did not change actual scanout"
machine.send_key("ret")
machine.wait_until_succeeds("test -e /run/meltype-proof/exit")
observed=machine.succeed("od -An -tx1 -v /run/meltype-proof/input").split()
assert "".join(observed)=="ニホンゴ".encode().hex(), observed
assert machine.succeed("cat /run/meltype-proof/exit").strip()=="0"
original=machine.succeed("cat /run/meltype-proof/termios-before").strip()
restored=machine.succeed("cat /run/meltype-proof/termios-after").strip()
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
machine.send_chars('stty -g > /run/meltype-proof/browser-before; meltype-tty -- cdp-tty "$(cat /run/meltype-proof/browser-ws)" 2>/run/meltype-proof/browser-errors; rc=$?; stty -g > /run/meltype-proof/browser-after; printf %s "$rc" > /run/meltype-proof/browser-exit\n')

def color_pixels(im,color):
    return sum(count for count,rgb in im.getcolors(im.width*im.height)
        if all(abs(a-b)<=2 for a,b in zip(rgb,color)))

def wait_scanout(name,check):
    deadline=time.monotonic()+15
    while True:
        image=scanout(name)
        if check(image): return image
        assert time.monotonic()<deadline, machine.succeed('cat /run/meltype-proof/browser-errors')
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
