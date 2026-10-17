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

def saved_editor(expected):
    machine.send_key("ctrl-o")
    machine.send_key("ret")
    # Compare bytes, including final newlines. Shell command substitution
    # strips trailing LF and can even accept a missing file for empty expected.
    code = ("from pathlib import Path; actual = Path('/run/meltype-proof/editor-text').read_bytes(); "
        f"expected = bytes.fromhex({expected.encode('utf-8').hex()!r}); "
        "assert actual == expected, (actual, expected)")
    machine.wait_until_succeeds("python3 -c " + shlex.quote(code))

# Default Direct Input (#16): keys reach Readline one at a time, so its Tab completion
# works, and Ctrl+Space converts only the word typed after it.
put("zz-direct-complete", "")
reader = put("read-direct", "touch /run/meltype-proof/direct-ready\n"
    "IFS= read -er -p 'DIRECT> ' value\nprintf %s \"$value\" > /run/meltype-proof/direct-text\n")
launch("direct", "meltype-tty --raw-text -- bash --noprofile --norc " + reader)
machine.wait_until_succeeds("test -e /run/meltype-proof/direct-ready")
before = scanout("B-direct-before")
machine.send_chars("/run/meltype-proof/zz-direct-c")
machine.send_key("tab")        # Readline completes the file name.
time.sleep(.5)
machine.send_chars("nihongo")
time.sleep(.5)                 # Readline's echo comes back before the conversion key.
machine.send_key("ctrl-spc")   # Converts at once (#19 P19g): the candidate image is up.
candidates("B-direct-candidates", before)
machine.fail("test -e /run/meltype-proof/direct-text")
machine.send_key("ret")        # Commit the conversion.
machine.send_key("ret")        # Readline accepts its line.
finished("direct")
assert contents("direct-text") in ("/run/meltype-proof/zz-direct-complete 日本語",
    "/run/meltype-proof/zz-direct-complete日本語"), contents("direct-text")

# The composition mode (--compose): every key goes through Meltype from the first one.
# A real shell's line editor, not a substitute conversion engine or CDP sink.
reader = put("read-line", "printf 'READLINE_READY\\n'\ntouch /run/meltype-proof/readline-ready\n"
    "IFS= read -er -p 'INPUT> ' value\nprintf %s \"$value\" > /run/meltype-proof/shell-text\n")
launch("shell", "meltype-tty --raw-text --compose -- bash --noprofile --norc " + reader)
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
launch("editor", "meltype-tty --raw-text --compose -- nano --ignorerc --nonewlines /run/meltype-proof/editor-text")
machine.wait_until_succeeds("pgrep -x nano")
before = scanout("B-editor-before")
machine.send_chars("nihongo ")
shown = candidates("B-editor-candidates", before)
machine.send_key("down")
time.sleep(.2)
assert ImageChops.difference(shown, scanout("B-editor-selected")).getbbox()
machine.send_key("up")
machine.send_key("ret")
saved_editor("日本語")
machine.send_key("backspace")  # nano removes one committed Japanese character.
saved_editor('日本')
machine.send_key("alt-u")      # nano Undo restores it.
saved_editor('日本語')
machine.send_key("alt-e")      # nano Redo removes it again.
saved_editor('日本')
machine.send_key("alt-u")      # Restore full committed text.
saved_editor('日本語')
machine.send_key("ret")        # A second line is an Editor capability.
machine.send_chars("hello")
machine.send_key("ret")        # Native English commit only.
saved_editor("日本語\nhello")
machine.send_key("ret")        # Preserve a real trailing newline, not a stripped shell result.
saved_editor("日本語\nhello\n")
machine.send_key("alt-u")      # Native Undo removes just that newline.
saved_editor("日本語\nhello")
machine.send_key("alt-e")      # Native Redo restores it.
saved_editor("日本語\nhello\n")
machine.send_key("ctrl-x")
finished("editor")

# Reopen the real Editor and cancel an uncommitted conversion. The previously
# saved bytes must survive candidate display, cancellation and process restart.
launch("editor-restart", "meltype-tty --raw-text --compose -- nano --ignorerc --nonewlines /run/meltype-proof/editor-text")
machine.wait_until_succeeds("pgrep -x nano")
before = scanout("B-editor-restart-before")
machine.send_chars("nihongo ")
candidates("B-editor-restart-candidates", before)
machine.send_key("esc")
time.sleep(.1)                 # Separate two standalone Esc keys from an Alt chord.
machine.send_key("esc")
time.sleep(.1)
saved_editor("日本語\nhello\n")
scanout("B-editor-cancelled")
machine.send_key("ctrl-x")
finished("editor-restart")

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
launch("typed", "MELTYPE_PROPOSAL_URL=http://127.0.0.1:8031/proposal meltype-tty --raw-text --compose -- bash --noprofile --norc " + reader)
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
launch("hidden", "MELTYPE_PROPOSAL_URL=http://127.0.0.1:8031/proposal meltype-tty --raw-text --compose -- bash --noprofile --norc " + reader)
machine.wait_until_succeeds("test -e /run/meltype-proof/hidden-ready")
hidden_before = scanout("B-hidden-before")
machine.send_chars("not-a-real-password\n")
finished("hidden")
assert contents("hidden-text") == "not-a-real-password"
assert contents("provider-requests") == requests_before
scanout("B-hidden-after")

# #19 passive preview (--preview): keys still reach an interactive bash one at a time. The explicit
# observation file (--preview-fd) says what was shown and where; the screen is checked against it.
def preview_events():
    text = machine.succeed("cat /run/meltype-proof/preview-log 2>/dev/null || true")
    return [json.loads(line) for line in text.splitlines()]

def wait_shown(word=None):
    """The latest preview event shows current (not stale) suggestions, for `word` if given."""
    deadline = time.monotonic() + 15
    while True:
        events = preview_events()
        last = events[-1] if events else {}
        if last.get("event") in ("show", "update") and not last.get("stale") and word in (None, last.get("input")):
            time.sleep(.5)  # bcon's next frame carries the image
            return last
        assert time.monotonic() < deadline, (word, events, machine.succeed("cat /run/meltype-proof/*-errors"))
        time.sleep(.2)

def panel_mask(image):
    # The preview image's own background colour, exactly as paint() fills it (±2 for scaling).
    r, g, b = image.split()
    near = lambda channel, value: channel.point(lambda x: 255 if abs(x - value) <= 2 else 0)
    return ImageChops.multiply(ImageChops.multiply(near(r, 24), near(g, 26)), near(b, 32))

def new_boxes(after, before):
    """Bounding boxes of changed pixels: the preview (its colour), and bash's text (everything else
    outside that box; the preview's own glyphs and scaled edges stay inside it)."""
    moved = ImageChops.difference(after, before).convert("L").point(lambda x: 255 if x else 0)
    mask = panel_mask(after)
    panel = ImageChops.multiply(moved, mask).getbbox()
    rest = ImageChops.multiply(moved, ImageChops.invert(mask))
    if panel is not None:
        left, upper, right, lower = panel
        rest.paste(0, (max(0, left - 3), max(0, upper - 3), min(rest.width, right + 3), min(rest.height, lower + 3)))
    return panel, rest.getbbox()

# #19 P19e: a trailing "nn" (nihonn) against "nihon", typed one key at a time with --preview. It runs
# before the version-specific preview checks so the same script records 1.2.1.6 and 1.2.1.7. Everything
# is recorded first (preview events, scanouts, the Ctrl+Space + Enter result) and printed; the User's
# expectation (kanji for "nihonn") is checked last, so a failing run still carries the whole record.
def diag_events():
    text = machine.succeed("cat /run/meltype-proof/diag-log 2>/dev/null || true")
    return [json.loads(line) for line in text.splitlines()]

def has_kanji(text):
    return any(chr(0x4e00) <= ch <= chr(0x9fff) for ch in text)

launch("diag", "meltype-tty --raw-text --preview --preview-fd 3 -- bash --noprofile --norc -i "
    "3>>/run/meltype-proof/diag-log")
machine.wait_until_succeeds("pgrep -f '[b]ash --noprofile --norc -i'", timeout=30)
time.sleep(2)
for _ in range(4): machine.send_key("ret")
time.sleep(1)
diagnosis = {}
for word in ["nihonn", "nihon"]:
    before = scanout(f"B-diag-{word}-before")
    start = len(diag_events())
    machine.send_chars("echo ")
    keys = []
    for i, key in enumerate(word):
        machine.send_chars(key)
        time.sleep(.3)
        keys.append({"key": key, "panel": new_boxes(scanout(f"B-diag-{word}-{i}"), before)[0],
                     "last_event": (diag_events()[start:] or [None])[-1]})
    time.sleep(1.5)
    settled = scanout(f"B-diag-{word}-settled")
    events = diag_events()[start:]
    machine.send_key("ctrl-spc")
    time.sleep(1)
    scanout(f"B-diag-{word}-converting")
    machine.send_key("ret")
    machine.send_chars(f" > /run/meltype-proof/diag-{word}\n")
    machine.wait_until_succeeds(f"test -e /run/meltype-proof/diag-{word}")
    time.sleep(.5)
    diagnosis[word] = {"keys": keys, "events": events, "settled_panel": new_boxes(settled, before)[0],
                       "converted": contents(f"diag-{word}").strip()}
# Esc twice (conversion, then composition) puts the typed letters back: nothing erased for good.
machine.send_chars("echo nihonn")
time.sleep(1)
machine.send_key("ctrl-spc")
time.sleep(1)
for _ in range(2):
    machine.send_key("esc")
    time.sleep(.3)                           # separate standalone Esc keys from an Alt chord
machine.send_chars(" > /run/meltype-proof/diag-esc\n")
machine.wait_until_succeeds("test -e /run/meltype-proof/diag-esc")
time.sleep(.5)
diagnosis["esc"] = contents("diag-esc").strip()
machine.send_chars("exit\n")
finished("diag")
print("DIAG-P19E " + json.dumps(diagnosis, ensure_ascii=False), flush=True)
got = diagnosis["nihonn"]
fresh = [e for e in got["events"] if e["event"] in ("show", "update") and not e.get("stale") and e.get("input") == "nihonn"]
assert fresh and any(has_kanji(r) for r in fresh[-1]["representations"]), ("nihonn: no kanji preview", got)
assert got["converted"] == "日本", ("nihonn: Ctrl+Space + Enter is not 日本", got)
assert got["converted"] == fresh[-1]["representations"][0], ("Ctrl+Space differs from the preview's first", got)
assert diagnosis["esc"] == "nihonn", ("Esc did not put nihonn back", diagnosis["esc"])

machine.succeed("touch /run/meltype-proof/nihongo-a /run/meltype-proof/nihongo-b")
launch("preview", "meltype-tty --raw-text --preview --preview-fd 3 -- bash --noprofile --norc -i "
    "3>>/run/meltype-proof/preview-log")
machine.wait_until_succeeds("pgrep -f '[b]ash --noprofile --norc -i'", timeout=30)
time.sleep(2)
for _ in range(4): machine.send_key("ret")  # Leave rows above the prompt for the preview.
time.sleep(1)
before = scanout("B-preview-before")
machine.send_chars("echo ni")
wait_shown()
first = len(preview_events()) - 1
for i, key in enumerate("hongo"):            # #19: typing on, the suggestions stay on the screen
    machine.send_chars(key)
    time.sleep(.1)                           # shorter than the 150 ms wait: the old suggestions are up
    panel, _ = new_boxes(scanout(f"B-preview-typing-{i}"), before)
    assert panel is not None, ("the preview disappeared while typing", key, preview_events()[first:])
event = wait_shown("nihongo")
kept = preview_events()[first:]
assert kept[0]["event"] in ("show", "update") and not [e for e in kept if e["event"] == "hide"], kept
assert len({e["rect"][1] for e in kept}) == 1, kept   # replaced where it was
assert event["representations"][0] == "日本語", event          # the content, not only pixels
top, left, rows, cols = event["rect"]
assert top + rows <= event["cursor"][0], event                  # above the cursor line by its own account
shown = scanout("B-preview-shown")
panel, text = new_boxes(shown, before)
assert panel is not None and text is not None, (panel, text)
assert panel[3] <= text[1], ("the preview covers the line bash echoed", panel, text)
machine.send_key("ctrl-spc")                # Adoption: Ctrl+Space converts to the preview's first candidate.
machine.send_key("ret")
machine.send_chars(" > /run/meltype-proof/preview-conv\n")
machine.wait_until_succeeds("test -s /run/meltype-proof/preview-conv")
assert contents("preview-conv").strip() == "日本語", contents("preview-conv")
list_before = scanout("B-preview-list-before")
machine.send_chars("printf %s /run/meltype-proof/nihongo")
wait_shown("nihongo")
shown_events = len(preview_events())
machine.send_key("tab")                     # Readline completes the common prefix "nihongo-".
time.sleep(.5)
machine.send_key("tab")                     # Readline lists both files below the prompt.
time.sleep(1.5)
listed = scanout("B-preview-listed")
after = preview_events()[shown_events:]
# Hidden on the Tab key itself ("update") or, at the latest, just before bash's output ("child output").
assert after and after[0]["event"] == "hide" and after[0]["reason"] in ("update", "child output"), after
assert not [e for e in after if e["event"] == "show"], after
panel, text = new_boxes(listed, list_before)
assert panel is None and text is not None, ("preview pixels next to bash's completion list", panel, text)
machine.send_chars("a > /run/meltype-proof/preview-tab\n")
machine.wait_until_succeeds("test -s /run/meltype-proof/preview-tab")
assert contents("preview-tab") == "/run/meltype-proof/nihongo-a", contents("preview-tab")
machine.send_chars("echo r-marker >> /run/meltype-proof/preview-r\n")
time.sleep(1)
machine.send_key("ctrl-r")
machine.send_chars("r-marker")
time.sleep(.5)
machine.send_key("ret")
machine.wait_until_succeeds("test \"$(grep -c r-marker /run/meltype-proof/preview-r)\" = 2")
machine.send_chars("exit\n")
finished("preview")
machine.fail("pgrep -f '[f]citx5|[i]bus-daemon|[t]tyskk|[c]dp-tty|[c]hromium'")
print(json.dumps({"gate":"B-bcon-shell-editor","status":"PASS",
    "direct":contents("direct-text"),"preview":{"shown":event,"conversion":"日本語","tab":contents("preview-tab"),"list_clear":True,"ctrl_r":2},
    "diag_p19e":diagnosis,
    "shell":"hello 日本語","editor":"日本語\nhello\n","typed":"乙候補",
    "editor_delete_undo_redo":True,"editor_trailing_newline":True,"editor_restart_cancel":True,"terminal_restored":True,"hidden_input_provider_requests":0,
    "bcon":"ca30c939f5ae48fff10eb8dc26ea05b179bef680","ime":False,
    "provider":"LOCAL_FIXTURE","physical_host":"NOT_RUN","independent_review":"NOT_RUN",
    "screenshots":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(machine.out_dir).glob('B-*.png')}},ensure_ascii=False),flush=True)
