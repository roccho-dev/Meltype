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

def candidates(name, before, changed_from=None):
    deadline = time.monotonic() + 15
    while True:
        shown = scanout(name)
        changed = True
        if changed_from is not None:
            # Geometry changes before the next rendered scanout. An existing
            # old panel must not satisfy the resize observation immediately.
            # Count actual changed pixels, not a bounding-box area or RGB sum.
            red, green, blue = ImageChops.difference(shown, changed_from).split()
            mask = ImageChops.lighter(ImageChops.lighter(red, green), blue)
            changed = mask.point(lambda value: 255 if value else 0).histogram()[255] > 1000
        if panels(shown) > panels(before) + 1000 and changed: return shown
        assert time.monotonic() < deadline, machine.succeed("cat /run/meltype-proof/*-errors")
        time.sleep(.2)

def contents(name):
    return machine.succeed("cat /run/meltype-proof/" + name)

def saved_editor(expected, name="editor-text"):
    machine.send_key("ctrl-o")
    machine.send_key("ret")
    # Compare bytes, including final newlines. Shell command substitution
    # strips trailing LF and can even accept a missing file for empty expected.
    path = "/run/meltype-proof/" + name
    code = (f"from pathlib import Path; actual = Path({path!r}).read_bytes(); "
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
reference_source = {"repository":"roccho-dev/adrs", "revision":"f" * 40,
    "path":"decisions/synthetic-reference.jsonl", "blob":"e" * 40,
    "sha256":"d" * 64, "rawId":"synthetic-reference", "status":"proposed"}
reference_text = ("https://github.com/roccho-dev/adrs/blob/" + reference_source["revision"]
    + "/" + reference_source["path"] + "#L1")
reference_row = {"id":"reference-input:synthetic-display", "representation":reference_text,
    "meaning":{"kind":"text", "text":reference_text, "source":reference_source, "authority":"reference-only"},
    "evidence":{"theme":"intent-fit", "noul":0.9}}
# Public synthetic display data, not a real ADR locator or source read receipt.
put("reference-proposal.json", json.dumps(reference_row))
provider = put("provider.py", """from http.server import BaseHTTPRequestHandler,HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  q=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  with open('/run/meltype-proof/provider-requests','a') as log: log.write(json.dumps(q)+'\\n')
  rows=[{'id':id,'representation':text,'meaning':{'kind':'text'},'evidence':['local-test']} for id,text in [('a','甲候補'),('b','乙候補')]]
  if self.path == '/reference':
   with open('/run/meltype-proof/reference-proposal.json') as source: rows=[json.load(source)]
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

# A realistic citation-length typed reference goes through the same bcon
# candidate painter. The separately required Apps/Native test proves acquisition;
# this fixture observes the panel and exact selection/child effect, not live use
# or a promise that every URL/meaning JSON glyph is simultaneously readable.
reference_binding = '"\\C-]": printf %s "$READLINE_LINE" > /run/meltype-proof/reference-buffer'
reader = put("read-reference", "bind -x " + shlex.quote(reference_binding)
    + "\ntouch /run/meltype-proof/reference-ready\n"
    "IFS= read -er -p 'REFERENCE> ' value\nprintf %s \"$value\" > /run/meltype-proof/reference-text\nexit 0\n")
launch("reference", "MELTYPE_PROPOSAL_URL=http://127.0.0.1:8031/reference "
    "meltype-tty --raw-text --compose --selection-fd 3 -- bash --noprofile --rcfile "
    + reader + " -i 3>>/run/meltype-proof/reference-selection")
machine.wait_until_succeeds("test -e /run/meltype-proof/reference-ready")
before = scanout("B-reference-before")
reference_queries_before = len(contents("provider-requests").splitlines())
machine.send_chars("u")
reference_panel = candidates("B-reference-candidate", before)
machine.fail("test -s /run/meltype-proof/reference-selection")
machine.fail("test -e /run/meltype-proof/reference-text")
machine.send_key("ret")
machine.wait_until_succeeds("test -s /run/meltype-proof/reference-selection")
machine.fail("test -e /run/meltype-proof/reference-text")
deadline = time.monotonic() + 15
while panels(scanout("B-reference-committed")) > panels(before) + 1000:
    assert time.monotonic() < deadline, "reference panel did not clear after selection"
    time.sleep(.2)
machine.send_key("ctrl-0x1B")  # Observe the same child's unsubmitted Readline buffer.
machine.wait_until_succeeds("test -e /run/meltype-proof/reference-buffer")
assert contents("reference-buffer") == reference_text
machine.fail("test -e /run/meltype-proof/reference-text")
machine.send_key("ret")
finished("reference")
assert contents("reference-text") == reference_text
reference_selection = [json.loads(line) for line in contents("reference-selection").splitlines()]
assert len(reference_selection) == 1, reference_selection
reference_queries = [json.loads(line) for line in contents("provider-requests").splitlines()[reference_queries_before:]]
assert len(reference_queries) == 1 and reference_queries[0]["raw"] == "u", reference_queries
assert reference_selection[0] == {**reference_queries[0], "proposal":reference_row}
scanout("B-reference-after")
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

# #19 P19e-g: a trailing "nn" (nihonn) with --preview. The preview offers kanji, Ctrl+Space + Enter commits
# the preview's first candidate (日本), and Esc twice puts the typed letters back.
def nn_events():
    text = machine.succeed("cat /run/meltype-proof/nn-log 2>/dev/null || true")
    return [json.loads(line) for line in text.splitlines()]

def has_kanji(text):
    return any(chr(0x4e00) <= ch <= chr(0x9fff) for ch in text)

launch("nn", "meltype-tty --raw-text --preview --preview-fd 3 -- bash --noprofile --norc -i "
    "3>>/run/meltype-proof/nn-log")
machine.wait_until_succeeds("pgrep -f '[b]ash --noprofile --norc -i'", timeout=30)
time.sleep(2)
for _ in range(4): machine.send_key("ret")
time.sleep(1)
machine.send_chars("echo nihonn")
time.sleep(2)
fresh = [e for e in nn_events() if e["event"] in ("show", "update") and not e.get("stale") and e.get("input") == "nihonn"]
machine.send_key("ctrl-spc")
time.sleep(1)
machine.send_key("ret")
machine.send_chars(" > /run/meltype-proof/nn-converted\n")
machine.wait_until_succeeds("test -e /run/meltype-proof/nn-converted")
time.sleep(.5)
machine.send_chars("echo nihonn")
time.sleep(1)
machine.send_key("ctrl-spc")
time.sleep(1)
for _ in range(2):
    machine.send_key("esc")
    time.sleep(.3)                           # separate standalone Esc keys from an Alt chord
machine.send_chars(" > /run/meltype-proof/nn-esc\n")
machine.wait_until_succeeds("test -e /run/meltype-proof/nn-esc")
time.sleep(.5)
trailing = {"preview": fresh[-1]["representations"] if fresh else None,
            "converted": contents("nn-converted").strip(), "esc": contents("nn-esc").strip()}
machine.send_chars("exit\n")
finished("nn")
assert fresh and any(has_kanji(r) for r in fresh[-1]["representations"]), ("nihonn: no kanji preview", trailing)
assert trailing["converted"] == "日本", ("nihonn: Ctrl+Space + Enter is not 日本", trailing)
assert trailing["converted"] == fresh[-1]["representations"][0], ("Ctrl+Space differs from the preview's first", trailing)
assert trailing["esc"] == "nihonn", ("Esc did not put nihonn back", trailing)

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

# PV: after a fresh login the prompt is at the top of the screen. The preview is drawn below the cursor
# line there (cursor rows 1-7 showed nothing before), and bash's line and its output stay intact.
def top_events():
    text = machine.succeed("cat /run/meltype-proof/top-log 2>/dev/null || true")
    return [json.loads(line) for line in text.splitlines()]

def top_shown():
    return [e for e in top_events() if e["event"] in ("show", "update") and not e.get("stale") and e.get("input") == "nihongo"]

launch("top", "meltype-tty --raw-text --preview --preview-fd 3 -- bash --noprofile --norc -i "
    "3>>/run/meltype-proof/top-log")
machine.wait_until_succeeds("pgrep -f '[b]ash --noprofile --norc -i'", timeout=30)
time.sleep(2)
machine.send_chars("printf '\\033[H\\033[2J'\n")    # The prompt on the first row, as after a fresh login.
time.sleep(1)
top_before = scanout("B-top-before")
machine.send_chars("echo nihongo")
deadline = time.monotonic() + 15
while not top_shown():
    assert time.monotonic() < deadline, (top_events(), machine.succeed("cat /run/meltype-proof/*-errors"))
    time.sleep(.2)
time.sleep(.5)                               # bcon's next frame carries the image
top_event = top_shown()[-1]
assert top_event["cursor"][0] == 1, top_event
assert top_event["rect"][0] > top_event["cursor"][0], ("not below the cursor line", top_event)
assert top_event["representations"][0] == "日本語", top_event
panel, text = new_boxes(scanout("B-top-shown"), top_before)
assert panel is not None and text is not None, (panel, text)
assert text[3] <= panel[1], ("the preview covers the line bash echoed", panel, text)
machine.send_key("ctrl-spc")                 # Converts to the preview's first candidate.
time.sleep(1)
machine.send_key("ret")                      # Commits it on the first row.
machine.send_chars(" | tee /run/meltype-proof/top-conv\n")   # bash prints 日本語 on the rows the preview used.
machine.wait_until_succeeds("test -s /run/meltype-proof/top-conv")
time.sleep(1)
assert contents("top-conv").strip() == "日本語", contents("top-conv")
top_after = top_events()
assert top_after[-1]["event"] == "hide", top_after
panel, text = new_boxes(scanout("B-top-after"), top_before)
assert panel is None and text is not None, ("preview pixels left over bash's output", panel, text)
top_result = {"cursor": top_event["cursor"], "rect": top_event["rect"], "conversion": contents("top-conv").strip()}
machine.send_chars("exit\n")
finished("top")

# Original B2: use bcon's actual clipboard key and font/grid/PTY resize path.
# Supplying the clipboard is preparation; only child bytes and Editor saves
# establish paste effects. Geometry is read from the fixed child's own fd0,
# never the test driver's separate shell or the outer Linux VT.
def clipboard(text):
    bcon_pid = machine.succeed("systemctl show -p MainPID --value meltype-test-console.service").strip()
    assert bcon_pid.isdecimal() and int(bcon_pid) > 1, bcon_pid
    path = "/run/meltype-test-console/bcon_clipboard_" + bcon_pid
    machine.succeed("printf %s " + shlex.quote(text) + " > " + path)
    machine.send_key("ctrl-shift-v")


def geometry(pid):
    code = ("import fcntl,json,os,struct,termios; "
        f"pid={pid}; fd=os.open(f'/proc/{pid}/fd/0',os.O_RDONLY|os.O_NOCTTY); "
        "print(json.dumps({'pid':pid,'tty':os.ttyname(fd),"
        "'size':struct.unpack('HHHH',fcntl.ioctl(fd,termios.TIOCGWINSZ,b'\\0'*8))})); os.close(fd)")
    return json.loads(machine.succeed("python3 -c " + shlex.quote(code)))


def font_resize(pid, original, reset=False):
    # QEMU keycode 0x0D is '=' in the pinned test driver's CHAR_TO_KEY.
    machine.send_key("ctrl-0" if reset else "ctrl-0x0D")
    deadline = time.monotonic() + 15
    while True:
        current = geometry(pid)
        assert current["pid"] == original["pid"] and current["tty"] == original["tty"], current
        changed = current["size"][:2] != original["size"][:2]
        if changed != reset:
            return current
        assert time.monotonic() < deadline, (original, current, reset)
        time.sleep(.2)


paste_text = "貼付け 日本語\nsecond\tline"
# `read -e` still stops at its first LF, even when Readline returned a complete
# bracketed-paste buffer (Bash builtins/read.def). Observe the actual interactive
# Shell's editing buffer before submitting anything instead of misusing read.
binding = '"\\C-]": printf %s "$READLINE_LINE" > /run/meltype-proof/paste-text'
reader = put("paste-bashrc", "bind 'set enable-bracketed-paste on'\n"
    + "bind -x " + shlex.quote(binding) + "\nPS1='PASTE> '\n"
    "touch /run/meltype-proof/paste-ready\n")
launch("paste", "meltype-tty --raw-text -- bash --noprofile --rcfile " + reader + " -i")
machine.wait_until_succeeds("test -e /run/meltype-proof/paste-ready")
scanout("B-paste-shell-before")
clipboard(paste_text)
scanout("B-paste-shell-inserted")
machine.fail("test -e /run/meltype-proof/paste-text")
machine.send_key("ctrl-0x1B")  # Ctrl+] runs the existing Bash bind -x observer.
machine.wait_until_succeeds("test -e /run/meltype-proof/paste-text")
assert contents("paste-text") == paste_text, contents("paste-text")
machine.send_key("ctrl-c")      # Discard the unsubmitted buffer, never execute it.
machine.send_chars("exit 0\n")
finished("paste")

reader = put("read-resize", "printf %s \"$$\" > /run/meltype-proof/resize-pid\n"
    "tty > /run/meltype-proof/resize-tty\ntouch /run/meltype-proof/resize-ready\n"
    "IFS= read -er -p 'RESIZE> ' value\nprintf %s \"$value\" > /run/meltype-proof/resize-text\n")
launch("resize", "MELTYPE_PROPOSAL_URL=http://127.0.0.1:8031/proposal "
    "meltype-tty --raw-text --compose --selection-fd 3 -- bash --noprofile --norc "
    + reader + " 3>>/run/meltype-proof/resize-selection")
machine.wait_until_succeeds("test -e /run/meltype-proof/resize-ready")
shell_pid = int(contents("resize-pid"))
shell_original = geometry(shell_pid)
assert shell_original["tty"] == contents("resize-tty").strip(), shell_original
before = scanout("B-resize-shell-before")
machine.send_chars("u")
shown = candidates("B-resize-shell-candidates", before)
shell_smaller = font_resize(shell_pid, shell_original)
resized = candidates("B-resize-shell-smaller", before, changed_from=shown)
assert ImageChops.difference(shown, resized).getbbox()
shell_restored = font_resize(shell_pid, shell_original, reset=True)
restored = candidates("B-resize-shell-restored", before, changed_from=resized)
machine.send_key("down")
time.sleep(.2)
assert ImageChops.difference(restored, scanout("B-resize-shell-selected")).getbbox()
machine.send_key("ret")
machine.fail("test -e /run/meltype-proof/resize-text")
machine.send_key("ret")
finished("resize")
assert contents("resize-text") == "乙候補", contents("resize-text")
resize_selection = [json.loads(line) for line in contents("resize-selection").splitlines()]
assert len(resize_selection) == 1, resize_selection
assert resize_selection[0]["proposal"]["id"] == "b", resize_selection
assert resize_selection[0]["proposal"]["representation"] == "乙候補", resize_selection
# Without the actual Down effect the first candidate would be 甲候補. The
# existing one-shot observation and the fixed child's bytes must agree.

paste_editor = "貼付け 日本語\nsecond\tline\n"
launch("paste-editor", "meltype-tty --raw-text --compose -- nano --ignorerc --nonewlines /run/meltype-proof/paste-editor-text")
machine.wait_until_succeeds("pgrep -x nano")
editor_pid = int(machine.succeed("pgrep -x nano").strip())
editor_original = geometry(editor_pid)
# A distinct, initially absent file preserves the earlier Editor evidence.
machine.fail("test -e /run/meltype-proof/paste-editor-text")
clipboard(paste_editor)
scanout("B-paste-editor-inserted")
saved_editor(paste_editor, "paste-editor-text")
editor_paste_actual = contents("paste-editor-text")
before = scanout("B-resize-editor-before")
machine.send_chars("nihongo ")
shown = candidates("B-resize-editor-candidates", before)
editor_smaller = font_resize(editor_pid, editor_original)
resized = candidates("B-resize-editor-smaller", before, changed_from=shown)
assert ImageChops.difference(shown, resized).getbbox()
editor_restored = font_resize(editor_pid, editor_original, reset=True)
candidates("B-resize-editor-restored", before, changed_from=resized)
machine.send_key("ret")
saved_editor(paste_editor + "日本語", "paste-editor-text")
machine.send_key("ctrl-x")
finished("paste-editor")
paste_resize = {"shell_paste":contents("paste-text"),
    "editor_paste_expected":paste_editor,"editor_paste_actual":editor_paste_actual,
    "shell_geometry":[shell_original,shell_smaller,shell_restored],
    "editor_geometry":[editor_original,editor_smaller,editor_restored],
    "shell_after_resize":contents("resize-text"),
    "shell_selected":resize_selection,
    "editor_after_resize":contents("paste-editor-text"),
    "resize_mechanism":"existing bcon font/grid/PTY; not DRM hotplug",
    "paste_mechanism":"existing bcon shared clipboard + CtrlShiftV"}
print("B-PASTE-RESIZE " + json.dumps(paste_resize, ensure_ascii=False), flush=True)
machine.fail("pgrep -f '[f]citx5|[i]bus-daemon|[t]tyskk|[c]dp-tty|[c]hromium'")
print(json.dumps({"gate":"B-bcon-shell-editor","status":"PASS",
    "direct":contents("direct-text"),"preview":{"shown":event,"conversion":"日本語","tab":contents("preview-tab"),"list_clear":True,"ctrl_r":2},
    "trailing_nn":trailing,"top":top_result,
    "paste_resize":paste_resize,
    "reference_display":{"source":"SYNTHETIC_DISPLAY_FIXTURE", "selected":reference_selection,
        "child_text":contents("reference-text"), "full_label_readability":"INDEPENDENT_IMAGE_REVIEW_REQUIRED"},
    "shell":"hello 日本語","editor":"日本語\nhello\n","typed":"乙候補",
    "editor_delete_undo_redo":True,"editor_trailing_newline":True,"editor_restart_cancel":True,"terminal_restored":True,"hidden_input_provider_requests":0,
    "bcon":"ca30c939f5ae48fff10eb8dc26ea05b179bef680","ime":False,
    "provider":"LOCAL_FIXTURE","physical_host":"NOT_RUN","independent_review":"NOT_RUN",
    "screenshots":{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(machine.out_dir).glob('B-*.png')}},ensure_ascii=False),flush=True)
