# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional transport to the existing local Proposal provider.

One active request, one replaceable pending request, one response. No ranking,
credentials, native calls from the worker, or automatic network configuration.
"""
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 1024 * 1024

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class ProposalTransport:
    def __init__(self, url):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', '::1', 'localhost')
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError('Proposal URL must be an explicit local HTTP endpoint without credentials')
        self.url = url
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

    def _run(self):
        # No environment proxy or redirect can move captured input to another host.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed: return
                query, self.pending = self.pending, None
            try:
                body = json.dumps(query, ensure_ascii=False).encode('utf-8')
                if len(body) > LIMIT: raise ValueError('query limit')
                request = urllib.request.Request(self.url, data=body, headers={'Content-Type':'application/json'}, method='POST')
                with opener.open(request, timeout=2) as result:
                    payload = result.read(LIMIT+1)
                if len(payload) > LIMIT: raise ValueError('response limit')
                text = payload.decode('utf-8')
            except (OSError, ValueError, urllib.error.URLError):
                text = json.dumps({**query, 'proposals':[], 'error':'UNAVAILABLE'})
            with self.condition:
                if self.closed: return
                if self.latest == (query['generation'], query['raw']):
                    self.response = (query['generation'], text)
