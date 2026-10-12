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
original=machine.succeed("stty -F /dev/pts/0 -g").strip()
# The sink records the real child's input. It never supplies candidates,
# terminal replies, or injected Meltype input.
sink="""import os,tty
from pathlib import Path
tty.setraw(0)
os.write(1,b'\\x1b[10;4HREADY\\x1b[10;4H')
Path('/run/meltype-proof/ready').touch()
value=b''
while len(value)<12: value+=os.read(0,12-len(value))
Path('/run/meltype-proof/input').write_bytes(value)
os.write(1,b'\\r\\nSINK:'+value.hex().encode()+b'\\r\\n')
"""
machine.succeed("printf %s "+shlex.quote(sink)+" > /run/meltype-proof/sink.py")
machine.send_chars("meltype-tty -- python3 /run/meltype-proof/sink.py 2>/run/meltype-proof/errors; printf '%s' $? > /run/meltype-proof/exit\n")
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
machine.fail("test -e /run/meltype-proof/input")
machine.send_key("down")
time.sleep(.5)
chosen=scanout("bcon-selected")
assert ImageChops.difference(shown,chosen).getbbox(), "selection did not change actual scanout"
machine.send_key("ret")
machine.wait_until_succeeds("test -e /run/meltype-proof/exit")
observed=machine.succeed("od -An -tx1 -v /run/meltype-proof/input").split()
assert "".join(observed)=="ニホンゴ".encode().hex(), observed
assert machine.succeed("cat /run/meltype-proof/exit").strip()=="0"
assert machine.succeed("stty -F /dev/pts/0 -g").strip()==original
machine.fail("pgrep -f '[f]citx5|[i]bus-daemon|[t]tyskk'")
after=scanout("bcon-after-commit")
assert panel_pixels(after)<panel_pixels(shown), "candidate image survived completion"
print(json.dumps({"gate":"nixos-vm-bcon-native-pty-scanout","status":"PASS",
    "input":"nihongo / Down / Enter","child_text":"ニホンゴ",
    "bcon":"ca30c939f5ae48fff10eb8dc26ea05b179bef680","ime":False,
    "preedit_not_in_child":True,"candidate_scanout_pixels":panel_pixels(shown),
    "exit":0,"terminal_restored":True,
    "target_laptop":"NOT_RUN","browser_dom":"NOT_RUN","independent_review":"NOT_RUN"}),flush=True)
