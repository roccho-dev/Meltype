# SPDX-License-Identifier: GPL-3.0-or-later
"""Same-build NativeAOT/Core port and real local HTTP; no substitute library.

Target tests attest a fixed child PTY token, not an invisible GUI/DOM caret.
"""
import ctypes
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import threading
import time
import tty
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from meltype_tty import InputBridge,NativeSession,effect_bytes,write_all
PACKAGE=Path(sys.argv.pop(1)).resolve() if len(sys.argv)>1 else None
if PACKAGE is None: raise SystemExit('same-build Meltype-linux directory required')

class NativeProposalProof(unittest.TestCase):
    def setUp(self):
        # No request is sent unless poll_proposals is called. Most cases exercise
        # explicit out-of-order responses on the real native port directly.
        self.native=NativeSession(PACKAGE/'libMeltypeNative.so',PACKAGE/'mozc/meltype_mozc_helper','http://127.0.0.1:1/proposal')
        self.native.bind_target('fixed-child-A')
        self.bridge=InputBridge(self.native)
        self.master,self.slave=os.openpty();tty.setraw(self.slave)
    def tearDown(self):
        self.native.close();os.close(self.master);os.close(self.slave)
    def query(self):
        pointer=self.native.lib.meltype_proposals_request(self.native.proposal)
        self.assertTrue(pointer)
        return self.native._result(pointer)
    def candidate(self,id,text='同じ表示'):
        return {'id':id,'representation':text,'meaning':{'kind':'text','value':id},'evidence':['source',id]}
    def response(self,q,candidates=None,body=None):
        body=json.dumps({**q,'proposals':candidates or []},ensure_ascii=False) if body is None else body
        result=self.native._result(self.native.lib.meltype_proposals_receive(self.native.proposal,q['generation'],body.encode()))
        self.assertEqual(effect_bytes(result),b'')
        self.bridge.view=result['view']
    def send(self,raw):
        data=self.bridge.feed(raw)
        if data:
            write_all(self.master,data)
            self.assertTrue(select.select([self.slave],[],[],1)[0])
            self.assertEqual(os.read(self.slave,len(data)),data)
        else: self.assertFalse(select.select([self.slave],[],[],0)[0])
        return data
    def test_first_character_same_display_keeps_original_typed_id_once(self):
        self.assertEqual(self.send(b'u'),b'');q=self.query();self.assertEqual(q['raw'],'u')
        self.response(q,[self.candidate('A'),self.candidate('B')])
        self.assertEqual(self.bridge.view['candidates'],['同じ表示','同じ表示'])
        self.send(b'\x1b[B')
        self.assertEqual(self.send(b'\r'),'同じ表示'.encode())
        chosen=self.native.take_selection()
        self.assertEqual(chosen,{'generation':q['generation'],'raw':'u','proposal':self.candidate('B')})
        self.assertIsNone(self.native.take_selection())
        self.response(q,[self.candidate('old')]);self.assertIsNone(self.bridge.view)
    def test_evidence_is_available_before_choice_and_matches_selected_original(self):
        self.send(b'u');q=self.query()
        rows=[self.candidate('A'),self.candidate('B')]
        self.response(q,rows)
        self.assertEqual([json.loads(note) for note in self.bridge.view['notes']],
            [row['evidence'] for row in rows])
        self.assertEqual(json.loads(self.bridge.view['meaning']),rows[0]['meaning'])
        self.assertEqual(self.send(b'\x1b[B'),b'')
        self.assertEqual(json.loads(self.bridge.view['meaning']),rows[1]['meaning'])
        self.assertEqual(json.loads(self.bridge.view['notes'][self.bridge.view['selectedIndex']]),rows[1]['evidence'])
        self.assertEqual(self.send(b'\r'),'同じ表示'.encode())
        self.assertEqual(self.native.take_selection()['proposal'],rows[1])

    def test_latest_aba_cancel_retains_raw_and_rejects_old_response(self):
        self.send(b'u');q1=self.query();self.send(b'x');q2=self.query()
        self.response(q1,[self.candidate('old')]);self.assertFalse(self.bridge.view['candidates'])
        self.response(q2,[self.candidate('current')])
        self.send(b'\x1b');self.assertEqual(self.bridge.flush_escape(),b'')
        self.assertEqual(self.bridge.view['text'],'ux')
        self.response(q2,[self.candidate('cancelled')]);self.assertFalse(self.bridge.view['candidates'])
        self.send(b'\x7f');self.send(b'x');q3=self.query()
        self.assertEqual(q3['raw'],q2['raw']);self.assertGreater(q3['generation'],q2['generation'])
        self.response(q2,[self.candidate('old-aba')]);self.assertFalse(self.bridge.view['candidates'])
        self.response(q3,[self.candidate('new','新しい')]);self.assertEqual(self.send(b'\r'),'新しい'.encode())
    def test_target_aba_or_unknown_refuses_selection(self):
        self.send(b'u');q=self.query();self.response(q,[self.candidate('A')])
        self.native.bind_target('fixed-child-B');self.native.bind_target('fixed-child-A')
        self.assertEqual(self.send(b'\r'),b'');self.assertIsNone(self.native.take_selection())
        self.send(b'u');q=self.query();self.response(q,[self.candidate('B')])
        self.native.focus(False)
        self.assertEqual(self.send(b'\r'),b'');self.assertIsNone(self.native.take_selection())
    def test_duplicate_ids_or_invalid_json_produce_no_candidate_effect(self):
        self.send(b'u');q=self.query();self.response(q,[self.candidate('A'),self.candidate('A')])
        self.assertFalse(self.bridge.view['candidates']);self.assertIsNone(self.native.take_selection())
        self.send(b'x');q=self.query();self.response(q,body='{bad json')
        self.assertFalse(self.bridge.view['candidates']);self.assertEqual(self.bridge.view['text'],'ux')
    def test_control_character_candidate_is_not_a_command(self):
        self.send(b'u');q=self.query();self.response(q,[self.candidate('A','unexpected\ncommand')])
        self.assertFalse(self.bridge.view['candidates'],'unsafe text must not be offered as a selectable candidate')
        self.assertEqual(self.bridge.view['text'],'う','the existing native preedit must remain')
        self.assertIsNone(self.native.take_selection())
        self.send(b'\x1b[21~')  # F10 recovers the original raw spelling, not provider text.
        self.assertEqual(self.send(b'\r'),b'u')
        self.assertIsNone(self.native.take_selection())
    def test_unknown_target_does_not_publish_a_provider_query(self):
        self.native.focus(False)
        self.assertEqual(self.send(b'u'),b'')
        pointer=self.native.lib.meltype_proposals_request(self.native.proposal)
        if pointer: self.native._result(pointer)
        self.assertFalse(pointer,'unknown input target must not publish raw input')

    def test_target_change_discards_unsent_query_and_allows_fresh_input(self):
        self.send(b'u')
        self.native.focus(False)
        pointer=self.native.lib.meltype_proposals_request(self.native.proposal)
        if pointer: self.native._result(pointer)
        self.assertFalse(pointer,'target invalidation must discard an unsent query')
        self.native.focus(True)
        self.send(b'x');q=self.query()
        self.assertEqual(q['raw'],'ux')
        self.response(q,[self.candidate('fresh','新しい')])
        self.assertEqual(self.send(b'\r'),'新しい'.encode())
        self.assertEqual(self.native.take_selection()['proposal']['id'],'fresh')

    def test_mixed_text_and_action_refuses_the_whole_response_in_both_orders(self):
        text=self.candidate('text','must not be filtered through')
        action=self.candidate('action','not a terminal action')
        action['meaning']={'kind':'action','name':'some-action'}
        for rows in ([text,action],[action,text]):
            self.assertEqual(self.send(b'u'),b'')
            q=self.query()
            self.response(q,rows)
            self.assertFalse(self.bridge.view['candidates'],'mixed response must not be filtered or reordered')
            self.assertIsNone(self.native.take_selection())
            # Cancel the user's raw preedit, not a provider text or action.
            for _ in range(2):
                if self.bridge.view is None: break
                self.assertEqual(self.send(b'\x1b'),b'')
                self.assertEqual(self.bridge.flush_escape(),b'')
            self.assertIsNone(self.bridge.view)
        self.send(b'u');q=self.query()
        self.response(q,[self.candidate('fresh','新しい')])
        self.assertEqual(self.send(b'\r'),'新しい'.encode())
        self.assertEqual(self.native.take_selection()['proposal']['id'],'fresh')

    def test_nontext_proposal_cannot_become_a_terminal_commit(self):
        self.send(b'u');q=self.query()
        action=self.candidate('action','shown as harmless text')
        action['meaning']={'kind':'action','name':'some-action'}
        self.response(q,[action])
        self.assertFalse(self.bridge.view['candidates'],'an action is not a text insertion')
        self.assertIsNone(self.native.take_selection())
        self.send(b'\x1b[21~')
        self.assertEqual(self.send(b'\r'),b'u')
        self.assertIsNone(self.native.take_selection())
    def queued_query_ends(self, ending):
        # The first query has really reached localhost. Hold only that response
        # so a newer raw input is pending, not yet acquired by the HTTP worker.
        entered=threading.Event();release=threading.Event();seen=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(inner):
                q=json.loads(inner.rfile.read(int(inner.headers['Content-Length'])))
                seen.append(q)
                if len(seen)==1:
                    entered.set();release.wait(1.5)
                data=json.dumps({**q,'proposals':[self.candidate('live-local','確認候補')]}).encode()
                inner.send_response(200);inner.send_header('Content-Length',str(len(data)))
                inner.end_headers()
                try: inner.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError): pass
            def log_message(self,*_): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        self.native.close()
        try:
            self.native=NativeSession(PACKAGE/'libMeltypeNative.so',PACKAGE/'mozc/meltype_mozc_helper',
                f'http://127.0.0.1:{server.server_port}/proposal')
            self.native.bind_target('fixed-child-cancel');self.bridge=InputBridge(self.native)
            self.assertEqual(self.send(b'u'),b'');self.native.poll_proposals()
            self.assertTrue(entered.wait(1),'first query never reached the provider')
            self.assertEqual(self.send(b'x'),b'');self.native.poll_proposals()
            with self.native.transport.condition:
                pending=dict(self.native.transport.pending)
            self.assertEqual(pending['raw'],'ux')
            if ending=='escape':
                self.assertEqual(self.send(b'\x1b'),b'')
                self.assertEqual(self.bridge.flush_escape(),b'')
                self.assertIsNone(self.bridge.view)  # No returned candidate was displayed.
            elif ending=='enter':
                self.assertEqual(self.send(b'\r'),b'ux')
                self.assertIsNone(self.bridge.view)
            else:
                # InputBridge uses CommitPending before forwarding a command.
                self.assertEqual(self.send(b'\x01'),b'ux\x01')
                self.assertIsNone(self.bridge.view)
            self.assertIsNone(self.native.take_selection())
            with self.native.transport.condition:
                self.assertIsNone(self.native.transport.pending,'ended query was still queued for transmission')
                self.assertIsNone(self.native.transport.latest)
                self.assertIsNone(self.native.transport.response)
            release.set()
            deadline=time.monotonic()+.2
            while time.monotonic()<deadline:
                self.assertIsNone(self.native.poll_proposals(),'cancelled response reached native')
                time.sleep(.005)
            self.assertEqual(seen,[{'generation':pending['generation']-1,'raw':'u'}])
            self.assertFalse(select.select([self.slave],[],[],0)[0],'unselected text reached the child')
            # Cancellation does not close the reusable transport or reject a
            # genuinely new query. Its returned original ID remains selectable.
            self.assertEqual(self.send(b'z'),b'');self.native.poll_proposals()
            deadline=time.monotonic()+2
            while time.monotonic()<deadline:
                result=self.native.poll_proposals()
                if result is not None:
                    self.assertEqual(effect_bytes(result),b'');self.bridge.view=result['view'];break
                time.sleep(.005)
            self.assertEqual([q['raw'] for q in seen],['u','z'])
            self.assertEqual(self.bridge.view['candidates'],['確認候補'])
            self.assertEqual(self.send(b'\r'),'確認候補'.encode())
            self.assertEqual(self.native.take_selection()['proposal']['id'],'live-local')
            self.assertIsNone(self.native.take_selection())
        finally:
            release.set();server.shutdown();server.server_close();worker.join(1)

    def test_escape_discards_unsent_query_without_effect_and_next_input_works(self):
        self.queued_query_ends('escape')

    def test_enter_discards_unsent_query_without_a_second_commit(self):
        self.queued_query_ends('enter')

    def test_passthrough_commit_discards_unsent_query(self):
        self.queued_query_ends('passthrough')

    def test_real_http_response_reaches_core_without_blocking_key_thread(self):
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(inner):
                query=json.loads(inner.rfile.read(int(inner.headers['Content-Length'])))
                seen.append(query)
                data=json.dumps({**query,'proposals':[self.candidate('http','HTTP候補')]}).encode()
                inner.send_response(200);inner.end_headers();inner.wfile.write(data)
            def log_message(self,*_):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.native.close()
        try:
            self.native=NativeSession(PACKAGE/'libMeltypeNative.so',PACKAGE/'mozc/meltype_mozc_helper',f'http://127.0.0.1:{server.server_port}/proposal')
            self.native.bind_target('fixed-child-http');self.bridge=InputBridge(self.native)
            self.send(b'u');deadline=time.monotonic()+3
            while time.monotonic()<deadline:
                result=self.native.poll_proposals()
                if result is not None:
                    self.assertEqual(effect_bytes(result),b'');self.bridge.view=result['view'];break
                time.sleep(.005)
            self.assertEqual(self.bridge.view['candidates'],['HTTP候補'])
            self.assertEqual(self.send(b'\r'),'HTTP候補'.encode())
            self.assertEqual(self.native.take_selection()['proposal']['id'],'http')
            self.assertEqual(seen[0]['raw'],'u')
        finally: server.shutdown();server.server_close()

if __name__=='__main__':
    names=unittest.defaultTestLoader.getTestCaseNames(NativeProposalProof)
    if len(sys.argv)>1:
        case=sys.argv[1]
        if case not in names:raise SystemExit('unknown test')
        with tempfile.TemporaryDirectory(prefix='meltype-native-proposals-') as home:
            for key in ['HOME','XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME']:
                os.environ[key]=str(Path(home)/key.lower());Path(os.environ[key]).mkdir()
            result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([NativeProposalProof(case)]))
            sys.exit(0 if result.wasSuccessful() else 1)
    failures=[]
    for case in names:
        result=subprocess.run([sys.executable,str(Path(__file__).resolve()),str(PACKAGE),case],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=20)
        print(result.stdout,end='')
        if result.returncode:failures.append(case)
    print(json.dumps({'gate':'real-native-typed-proposals','tests':len(names),'failures':failures,'provider':'local HTTP test','scope':'fixed child PTY','external_models':'NOT_CALLED'}))
    sys.exit(1 if failures else 0)
