# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional transport to the existing local Proposal provider.

One active request, one replaceable pending request, one response. No ranking,
credentials, native calls from the worker, or automatic network configuration.
"""
import json
import threading
import http.client
import socket
import time
import urllib.parse

LIMIT = 1024 * 1024

class ProposalTransport:
    def __init__(self, url):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1', 'localhost')
                or parsed.username or parsed.password or parsed.fragment
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError('Proposal URL must be an explicit local HTTP endpoint without credentials')
        self.url = parsed
        self.condition = threading.Condition()
        self.pending = self.response = self.latest = None
        self.closed = False
        self.worker = threading.Thread(target=self._run, name='meltype-proposal-http', daemon=True)
        self.worker.start()

    def submit(self, query):
        with self.condition:
            if self.closed: return
            self.latest = (query['generation'], query['raw'])
            self.pending = dict(query)
            self.response = None
            self.condition.notify()

    def take(self):
        with self.condition:
            response, self.response = self.response, None
            return response

    def close(self):
        with self.condition:
            self.closed = True
            self.pending = self.response = self.latest = None
            self.condition.notify()
        self.worker.join(timeout=.1)

    def _request(self, query):
        # A socket idle timeout alone can be extended indefinitely by trickle
        # bytes. End this one connection at the total request deadline instead.
        parsed=self.url
        host='127.0.0.1' if parsed.hostname=='localhost' else parsed.hostname
        body=json.dumps(query,ensure_ascii=False).encode('utf-8')
        if len(body)>LIMIT: raise ValueError('query limit')
        connection=http.client.HTTPConnection(host,parsed.port or 80,timeout=2)
        deadline=time.monotonic()+2
        timer=None
        try:
            connection.connect()
            sock=connection.sock
            def expire():
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            timer=threading.Timer(max(0,deadline-time.monotonic()),expire)
            timer.daemon=True
            timer.start()
            path=(parsed.path or '/')+('?' + parsed.query if parsed.query else '')
            connection.request('POST',path,body,{'Content-Type':'application/json','Host':parsed.netloc})
            with connection.getresponse() as response:
                if not 200<=response.status<300: raise ValueError('HTTP failure or redirect')
                payload=response.read(LIMIT+1)
            if time.monotonic()>=deadline: raise TimeoutError('request deadline')
            if len(payload)>LIMIT: raise ValueError('response limit')
            return payload.decode('utf-8')
        finally:
            if timer is not None: timer.cancel()
            connection.close()

    def _run(self):
        # Direct local HTTP has no environment proxy or redirect path.
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed: return
                query, self.pending = self.pending, None
            try:
                text=self._request(query)
            except (OSError,ValueError,http.client.HTTPException):
                text=json.dumps({**query,'proposals':[],'error':'UNAVAILABLE'})
            with self.condition:
                if self.closed: return
                if self.latest == (query['generation'], query['raw']):
                    self.response = (query['generation'], text)
