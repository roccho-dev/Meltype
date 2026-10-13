# SPDX-License-Identifier: GPL-3.0-or-later
# B: real bcon + real NativeAOT/Mozc + Bash Readline / nano. No browser.
# Original Browser/DOM gates stay at PR2 d673872 and belong to C/resources#14.
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
machine.wait_until_succeeds("journalctl -u meltype-test-console.service --no-pager | grep -F 'All phases complete, entering main loop'")
machine.send_chars("root\n")
machine.wait_until_succeeds("pgrep -t pts/0 -x bash")
machine.succeed("mkdir -p /run/meltype-proof")
machine.fail("command -v cdp-tty")
machine.fail("command -v chromium")

def put(name, text):
    path = "/run/meltype-proof/" + name
    machine.succeed("printf %s " + shlex.quote(text) + " > " + path)
    return path

def launch(name, command):
    prefix = "/run/meltype-proof/" + name
    # Fresh learning/config state per scenario; never mutate a user's profile.
    script = (f"mkdir -p {prefix}-state\n"
        f"export HOME={prefix}-state XDG_CONFIG_HOME={prefix}-state/config "
        f"XDG_DATA_HOME={prefix}-state/data XDG_CACHE_HOME={prefix}-state/cache\n"
        f"stty -g > {prefix}-before\n{command} 2>{prefix}-errors\nrc=$?\n"
        f"stty -g > {prefix}-after\nprintf '%s' \"$rc\" > {prefix}-exit\n")
    path = put(name + "-trial", script)
    machine.send_chars("bash " + path + "\n")

def finished(name):
    prefix = "/run/meltype-proof/" + name
    machine.wait_until_succeeds("test -e " + prefix + "-exit")
    assert machine.succeed("cat " + prefix + "-exit").strip() == "0"
    assert machine.succeed("cat " + prefix + "-before") == machine.succeed("cat " + prefix + "-after")

def scanout(name):
    machine.screenshot(name)
    with Image.open(Path(machine.out_dir)/(name + ".png")) as image:
        return image.convert("RGB").copy()

def panels(image):
    colors = image.getcolors(image.width * image.height)
    assert colors is not None
    return sum(n for n, color in colors if all(abs(a-b) <= 2 for a,b in zip(color,(24,26,32))))

def candidates(name, before):
    deadline = time.monotonic() + 15
    while True:
        shown = scanout(name)
        if panels(shown) > panels(before) + 1000: return shown
        assert time.monotonic() < deadline, machine.succeed("cat /run/meltype-proof/*-errors")
        time.sleep(.2)

def contents(name):
    return machine.succeed("cat /run/meltype-proof/" + name)

# A real shell's line editor, not a substitute conversion engine or CDP sink.
reader = put("read-line", "printf 'READLINE_READY\\n'\ntouch /run/meltype-proof/readline-ready\n"
    "IFS= read -er -p 'INPUT> ' value\nprintf %s \"$value\" > /run/meltype-proof/shell-text\n")
launch("shell", "meltype-tty -- bash --noprofile --norc " + reader)
machine.wait_until_succeeds("test -e /run/meltype-proof/readline-ready")
before = scanout("B-shell-before")
machine.send_chars("hello nihongo ")
shown = candidates("B-shell-candidates", before)
machine.fail("test -e /run/meltype-proof/shell-text")
machine.send_key("ret")  # Explicit conversion commit, not shell execution.
machine.fail("test -e /run/meltype-proof/shell-text")
machine.send_key("ret")  # Now the shell accepts its line.
finished("shell")
assert contents("shell-text") == "hello 日本語"

# Real nano owns edits to already committed text and its native Undo/Redo.
# Meltype owns preedit/reconversion only; no guessed deleteBefore/backspaces.
launch("editor", "meltype-tty -- nano --ignorerc --nonewlines /run/meltype-proof/editor-text")
machine.wait_until_succeeds("pgrep -x nano")
before = scanout("B-editor-before")
machine.send_chars("nihongo ")
shown = candidates("B-editor-candidates", before)
machine.send_key("down")
time.sleep(.2)
assert ImageChops.difference(shown, scanout("B-editor-selected")).getbbox()
machine.send_key("up")
machine.send_key("ret")
machine.send_key("backspace")  # nano removes one committed Japanese character.
machine.send_key("alt-u")      # nano Undo restores it.
machine.send_key("alt-e")      # nano Redo removes it again.
machine.send_key("alt-u")      # Restore full committed text.
machine.send_key("ret")        # A second line is an Editor capability.
machine.send_chars("hello")
machine.send_key("ret")        # Native English commit only.
machine.send_key("ctrl-o")
machine.send_key("ret")
machine.send_key("ctrl-x")
finished("editor")
assert contents("editor-text") == "日本語\nhello"

# Typed candidate transport: original IDs/types are checked by the separately
# required actual NativeAOT test; this local provider is explicitly a fixture.
provider = put("provider.py", """from http.server import BaseHTTPRequestHandler,HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  q=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  with open('/run/meltype-proof/provider-requests','a') as log: log.write(json.dumps(q)+'\\n')
  rows=[{'id':id,'representation':text,'meaning':{'kind':'text'},'evidence':['local-test']} for id,text in [('a','甲候補'),('b','乙候補')]]
  payload=json.dumps({**q,'proposals':rows}).encode()
  self.send_response(200);self.end_headers();self.wfile.write(payload)
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',8031),Handler).serve_forever()
""")
machine.succeed("systemd-run --unit=meltype-test-provider -- python3 " + provider)
machine.wait_until_succeeds("ss -ltn | grep ':8031'")
reader = put("read-typed", "touch /run/meltype-proof/typed-ready\nIFS= read -er -p 'TYPED> ' value\nprintf %s \"$value\" > /run/meltype-proof/typed-text\n")
launch("typed", "MELTYPE_PROPOSAL_URL=http://127.0.0.1:8031/proposal meltype-tty -- bash --noprofile --norc " + reader)
machine.wait_until_succeeds("test -e /run/meltype-proof/typed-ready")
before = scanout("B-typed-before")
machine.send_chars("u")
shown = candidates("B-typed-candidates", before)
machine.send_key("down")
machine.send_key("ret")
machine.fail("test -e /run/meltype-proof/typed-text")
machine.send_key("ret")
finished("typed")
assert contents("typed-text") == "乙候補"
# Canonical no-echo input is observed by the product, not declared safe by
# the fixture. Even the known-raw-text test wrapper must bypass this input.
requests_before = contents("provider-requests")
reader = put("read-hidden", "stty -echo\n touch /run/meltype-proof/hidden-ready\n"
    "IFS= read -r value\nprintf %s \"$value\" > /run/meltype-proof/hidden-text\n")
launch("hidden", "MELTYPE_PROPOSAL_URL=http://127.0.0.1:8031/proposal meltype-tty -- bash --noprofile --norc " + reader)
machine.wait_until_succeeds("test -e /run/meltype-proof/hidden-ready")
hidden_before = scanout("B-hidden-before")
machine.send_chars("not-a-real-password\n")
finished("hidden")
assert contents("hidden-text") == "not-a-real-password"
assert contents("provider-requests") == requests_before
scanout("B-hidden-after")
machine.fail("pgrep -f '[f]citx5|[i]bus-daemon|[t]tyskk|[c]dp-tty|[c]hromium'")
print(json.dumps({"gate":"B-bcon-shell-editor","status":"PASS",
    "shell":"hello 日本語","editor":"日本語\\nhello","typed":"乙候補",
    "editor_delete_undo_redo":True,"terminal_restored":True,"hidden_input_provider_requests":0,
    "bcon":"ca30c939f5ae48fff10eb8dc26ea05b179bef680","ime":False,
    "provider":"LOCAL_FIXTURE","physical_host":"NOT_RUN","independent_review":"NOT_RUN",
    "screenshots":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(machine.out_dir).glob('B-*.png')}},ensure_ascii=False),flush=True)
