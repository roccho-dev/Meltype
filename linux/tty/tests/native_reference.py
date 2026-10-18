# SPDX-License-Identifier: GPL-3.0-or-later
"""Real same-package Native/PTY + fixed installed Apps, controlled sources only.

The diagnostic latch delays worker dispatch AFTER the real HTTP response has
arrived. It changes no request deadline and injects no response into Native.
It proves one finite source/session handoff, not actual private/provider use.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tty

PACKAGE = Path(sys.argv[1]).resolve()
TOOLS = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(PACKAGE / "tty"))
from meltype_tty import NativeSession, InputBridge, effect_bytes, write_all


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def wait_until(predicate, seconds, message):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError(message)


def group_active(group):
    # Only this test's separately-created host process groups are inspected.
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] != "Z" and int(fields[2]) == group:
                return True
        except (OSError, ValueError, IndexError):
            pass
    return False


def stop_host(child):
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    child.wait(timeout=3)
    wait_until(lambda: not group_active(child.pid), 3, "host/semcmp child still active")


release = threading.Event()
arrived = threading.Event()
dispatched = threading.Event()
dispatch_failures = []
sessions, hosts, descriptors, calls = [], [], [], []
captured, queries = [], []


class Controlled(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        require(b"SYNTHETIC_BODY_MUST_NOT_ESCAPE" not in raw, "record body escaped")
        require(self.headers.get("Authorization") == "Bearer owned-controlled-only", "wrong credential")
        value = json.loads(raw)
        calls.append(value)
        body = json.dumps({"model": value["model"], "answers": {
            key: {"type": "noul", "noul": .9} for key in value["questions"]}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Controlled)
worker = threading.Thread(target=server.serve_forever, daemon=True)
worker.start()

try:
    with tempfile.TemporaryDirectory(prefix="meltype-reference-") as tmp:
        root = Path(tmp)
        git_env = {"PATH": os.environ["PATH"], "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_AUTHOR_NAME": "Synthetic", "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic", "GIT_COMMITTER_EMAIL": "synthetic@example.invalid"}

        def git(*args):
            return subprocess.check_output([str(TOOLS / "bin/git"), "-C", tmp, *args],
                env=git_env, stderr=subprocess.PIPE).decode().strip()

        git("init", "--quiet")
        (root / "decisions").mkdir()
        source_path = "decisions/synthetic-reference.jsonl"

        def version(status):
            body = (json.dumps({"schema": "adr_raw_event.v1", "raw_id": "synthetic-reference",
                "status": status, "decision": {"body": "SYNTHETIC_BODY_MUST_NOT_ESCAPE"}}) + "\n").encode()
            (root / source_path).write_bytes(body)
            git("add", "--", source_path)
            git("commit", "--quiet", "-m", "synthetic source version")
            return {"repository": "roccho-dev/adrs", "directory": tmp,
                "revision": git("rev-parse", "HEAD"), "path": source_path,
                "blob": git("rev-parse", "HEAD:" + source_path),
                "sha256": hashlib.sha256(body).hexdigest(), "rawId": "synthetic-reference"}

        def host(binding):
            # A minimal launch never borrows ambient provider credentials.
            env = {"PATH": os.environ["PATH"], "HOME": str(root), "PORT": "0", "HOST": "127.0.0.1",
                "VOICE_UI_PROPOSAL_MODULE": str(TOOLS / "propose.mjs"),
                "VOICE_UI_PROPOSAL_B": json.dumps(binding),
                "VOICE_UI_PROPOSAL_JEV_URL": f"http://127.0.0.1:{server.server_port}/controlled",
                "VOICE_UI_PROPOSAL_TEST_KEY": "owned-controlled-only", "VOICE_UI_MELTYPE_PROPOSALS": "1"}
            child = subprocess.Popen([str(TOOLS / "bin/voice-ui-dev")], env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True)
            hosts.append(child)
            until = time.monotonic() + 10
            while time.monotonic() < until:
                require(child.poll() is None, "installed host exited before ready")
                if select.select([child.stdout], [], [], .05)[0]:
                    line = child.stdout.readline().decode()
                    match = re.search(r"voice-ui dev: listening on 127\.0\.0\.1:(\d+)", line)
                    if match:
                        return f"http://127.0.0.1:{match[1]}/api/meltype-proposals"
            raise AssertionError("installed host did not become ready")

        def native(url, name):
            session = NativeSession(PACKAGE / "libMeltypeNative.so",
                PACKAGE / "mozc/meltype_mozc_helper", url)
            sessions.append(session)
            master, slave = os.openpty()
            descriptors.extend([master, slave])
            tty.setraw(slave)
            session.bind_target(name)
            return session, InputBridge(session), master, slave

        r0 = version("proposed")
        old_url = host(r0)
        old, old_bridge, old_master, old_slave = native(old_url, "source-r0-child")
        transport = old.transport
        original_request = transport._request

        def delayed_dispatch(query):
            try:
                require(not captured, "old request ran more than once")
                text = original_request(query)  # Real Apps -> installed semcmp -> fixed proposer.
                captured.append(json.loads(text))
                queries.append(query)
                arrived.set()
                require(release.wait(10), "diagnostic dispatch was not released")
                dispatched.set()
                return text
            except BaseException as error:
                dispatch_failures.append(type(error).__name__)
                raise

        transport._request = delayed_dispatch
        require(old_bridge.feed(b"u") == b"", "preedit leaked to old child")
        old.poll_proposals()
        require(arrived.wait(3), "real Apps response did not arrive within unchanged HTTP deadline")
        require(len(captured[0]["proposals"]) == 1, "old installed proposal missing")
        require(not old_bridge.view["candidates"], "held response already reached Native")
        old.focus(False)
        old.close()
        stop_host(hosts[0])  # Normal source rebind ends the old host and its children.
        require(old.poll_proposals() is None and old.take_selection() is None, "closed session retained activity")

        r1 = version("accepted_for_proposal")
        current_url = host(r1)
        current, bridge, master, slave = native(current_url, "source-r1-child")
        original_current_request = current.transport._request
        current_replies, current_queries = [], []

        def observe_current(query):
            text = original_current_request(query)
            current_queries.append(query)
            current_replies.append(json.loads(text))
            return text

        current.transport._request = observe_current
        require(bridge.feed(b"u") == b"", "preedit leaked to new child")

        def receive():
            result = current.poll_proposals()
            if result is None:
                return False
            require(effect_bytes(result) == b"", "response alone wrote to child")
            bridge.view = result["view"]
            return bool(bridge.view and bridge.view["candidates"])

        wait_until(receive, 3, "fresh reference did not reach real Native")
        require(queries[0] == current_queries[0], "same raw/generation ABA was not exercised")
        before, after = captured[0]["proposals"][0], current_replies[0]["proposals"][0]
        require(before["id"] != after["id"], "revision did not change original Proposal identity")
        require(after["meaning"]["source"]["revision"] == r1["revision"], "wrong source revision")
        require(after["meaning"]["source"]["status"] == "accepted_for_proposal", "status provenance lost")
        require(after["meaning"]["authority"] == "reference-only", "status promoted to adoption")
        require(bridge.view["candidates"] == [after["representation"]], "original ordering/text changed")
        require(json.loads(bridge.view["meaning"]) == after["meaning"], "typed meaning changed")
        require([json.loads(note) for note in bridge.view["notes"]] == [after["evidence"]], "evidence changed")
        require(not select.select([old_slave, slave], [], [], 0)[0], "unselected response reached a PTY")
        committed = bridge.feed(b"\r")
        require(committed == after["representation"].encode(), "new reference commit bytes differ")
        write_all(master, committed)
        require(select.select([slave], [], [], 1)[0], "PTY did not receive reference")
        require(os.read(slave, len(committed)) == committed, "PTY reference differs")
        require(current.take_selection() == {**current_queries[0], "proposal": after}, "original selection changed")
        require(current.take_selection() is None, "selection was published twice")
        require(bridge.feed(b"\r") == b"\r", "second Enter recommitted the reference")
        require(current.take_selection() is None, "second Enter selected a duplicate")
        release.set()
        transport.worker.join(timeout=1)
        require(not transport.worker.is_alive(), "old worker did not finish")
        require(dispatched.is_set() and not dispatch_failures and len(captured) == 1,
            "old worker failed instead of returning its actual response once")
        with transport.condition:
            require(transport.closed and transport.pending is None and transport.latest is None
                and transport.response is None, "old response survived closed transport")
        require(current.poll_proposals() is None and current.take_selection() is None,
            "old dispatch changed the new committed session")
        require(not select.select([old_slave, slave], [], [], 0)[0], "late dispatch added a PTY effect")
        require(len(calls) == 2, "unexpected provider calls or retry")
        current.close()
        stop_host(hosts[1])
        print(json.dumps({"gate": "B-reference-source-session", "status": "PASS",
            "apps": "8e0fe4403779b13a4699d3cbf1ed12c10dbfb3c0",
            "provider": "CONTROLLED_LOOPBACK", "source": "SYNTHETIC_GIT_ONLY",
            "same_raw_generation": True, "old_effects": 0, "selected": 1,
            "source_revision_changed": True, "host_children_terminated": True,
            "diagnostic": "dispatch-latch-after-real-HTTP", "real_provider": "NOT_RUN",
            "native_sha256": hashlib.sha256((PACKAGE / "libMeltypeNative.so").read_bytes()).hexdigest(),
            "mozc_sha256": hashlib.sha256((PACKAGE / "mozc/meltype_mozc_helper").read_bytes()).hexdigest()}))
finally:
    release.set()
    for session in sessions:
        session.close()
    for child in hosts:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=3)
    for descriptor in descriptors:
        os.close(descriptor)
    server.shutdown()
    server.server_close()
    worker.join(timeout=1)
