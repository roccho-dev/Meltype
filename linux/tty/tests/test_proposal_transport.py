# SPDX-License-Identifier: GPL-3.0-or-later
"""Real localhost HTTP only; no model, credentials or arbitrary endpoint."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from proposal_transport import ProposalTransport, LIMIT

class TransportTests(unittest.TestCase):
    def server(self, respond):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                query=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                status, headers, body=respond(query)
                self.send_response(status)
                for key,value in headers.items(): self.send_header(key,value)
                self.end_headers()
                try: self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError): pass
            def log_message(self,*_): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}/proposal'

    def result(self, transport):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            result=transport.take()
            if result is not None: return result
            time.sleep(.005)
        self.fail('bounded localhost response missing')

    def test_original_query_and_typed_response_survive(self):
        seen=[]
        def respond(query):
            seen.append(query)
            body={**query,'proposals':[{'id':'opaque','representation':'表示','meaning':{'n':1},'evidence':['原本']}]}
            return 200,{},json.dumps(body).encode()
        t=ProposalTransport(self.server(respond));self.addCleanup(t.close)
        t.submit({'generation':1,'raw':'u'})
        generation,body=self.result(t)
        self.assertEqual(generation,1)
        self.assertEqual(seen,[{'generation':1,'raw':'u'}])
        self.assertEqual(json.loads(body)['proposals'][0]['evidence'],['原本'])
        self.assertIsNone(t.take())

    def test_old_inflight_and_superseded_pending_are_not_delivered(self):
        entered=threading.Event();release=threading.Event();seen=[]
        self.addCleanup(release.set)
        def respond(query):
            seen.append(query['generation'])
            if query['generation']==1: entered.set();release.wait(2)
            return 200,{},json.dumps({**query,'proposals':[]}).encode()
        t=ProposalTransport(self.server(respond));self.addCleanup(t.close)
        t.submit({'generation':1,'raw':'u'});self.assertTrue(entered.wait(1))
        t.submit({'generation':2,'raw':'ux'});t.submit({'generation':3,'raw':'u'})
        release.set()
        generation,body=self.result(t)
        self.assertEqual(generation,3)
        self.assertEqual(seen,[1,3])
        self.assertEqual(json.loads(body)['raw'],'u')

    def test_redirect_and_oversized_response_become_failure(self):
        for reply in [(302,{'Location':'http://127.0.0.1:1/elsewhere'},b''),(200,{},b'x'*(LIMIT+1))]:
            with self.subTest(status=reply[0]):
                t=ProposalTransport(self.server(lambda q:reply));self.addCleanup(t.close)
                t.submit({'generation':7,'raw':'raw'})
                _,body=self.result(t)
                self.assertEqual(json.loads(body)['error'],'UNAVAILABLE')

    def test_nonlocal_or_credentialed_urls_never_start(self):
        for url in ['https://example.org/','http://example.org','http://a:b@127.0.0.1/','file:///tmp/data','http://127.0.0.1/#secret']:
            with self.subTest(url=url),self.assertRaises(ValueError): ProposalTransport(url)

if __name__=='__main__': unittest.main()
