"""Unit tests for handlers/toju.py (the MOCK-BACKED toju Tornado routes).

These run WITHOUT docker, the real sidecar, the mock, or any network: a tiny in-process
"fake sidecar" (another Tornado app) stands in for toju-sidecar, so we can assert the
handler's behaviour in isolation — auth, token forwarding, byte-exact passthrough, and the
502 (unreachable) / 504 (timeout) error mapping.

Run:  cd ~/Desktop/tornado-toju && python3 -m unittest tests.unit.test_toju_handler -v
(The repo's main suite is Playwright/E2E; this is a focused Python unit layer for the new handler.)
"""
import json
import os
import sys
import unittest

import tornado.testing
import tornado.web
from tornado.escape import json_encode
from tornado.web import create_signed_value

# Import the handler module from the repo root (two levels up).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from handlers import toju  # noqa: E402

COOKIE_SECRET = "unit-test-cookie-secret"
SHARED_SECRET = "unit-test-toju-token"
SHEET = "version:1.5\ncell:A1:t:\u091a\u093e\u0932\u0928 \u20b9 GST%\ncell:B1:v:9374.68\n"


# ── Fake sidecar: records what it received, lets each test script the reply ──
class _FakeState:
    def __init__(self):
        self.last_token = None
        self.last_path = None
        self.last_body = None
        self.delay = 0.0


class _FakeUpload(tornado.web.RequestHandler):
    def initialize(self, state):
        self.state = state

    async def post(self):
        self.state.last_token = self.request.headers.get("X-Toju-Token")
        self.state.last_path = self.request.path
        self.state.last_body = json.loads(self.request.body)
        if self.state.delay:
            import asyncio
            await asyncio.sleep(self.state.delay)
        self.set_header("Content-Type", "application/json")
        self.finish({"cid": "bafkmockUNIT", "url": "http://mock/ipfs/bafkmockUNIT",
                     "signature": "MOCKSIG", "success": True, "mocked": True})


class _FakeRetrieve(tornado.web.RequestHandler):
    def initialize(self, state):
        self.state = state

    def get(self, cid):
        self.state.last_token = self.request.headers.get("X-Toju-Token")
        self.set_header("Content-Type", "text/plain; charset=utf-8")
        self.finish(SHEET.encode("utf-8"))   # byte-exact


class _FakeStatus(tornado.web.RequestHandler):
    def get(self, cid):
        self.finish({"cid": cid, "active": True, "mocked": True})


class TojuHandlerTest(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        self.fake_state = _FakeState()
        # One app serves BOTH the fake sidecar routes and the real toju routes under test,
        # but the handler talks to the fake over HTTP at this same server's base URL.
        fake = tornado.web.Application([
            (r"/upload", _FakeUpload, dict(state=self.fake_state)),
            (r"/retrieve/(.+)", _FakeRetrieve, dict(state=self.fake_state)),
            (r"/status/(.+)", _FakeStatus),
        ])
        self._fake = fake
        self._fake_sock, self._fake_port = tornado.testing.bind_unused_port()
        self._fake_server = tornado.httpserver.HTTPServer(fake)
        self._fake_server.add_socket(self._fake_sock)

        # Point the handler at the fake sidecar and shorten timeouts for the 504 test.
        toju.TOJU_SIDECAR_URL = "http://127.0.0.1:%d" % self._fake_port
        toju.TOJU_SHARED_SECRET = SHARED_SECRET
        toju.SAVE_TIMEOUT = 1.0
        toju.RETRIEVE_TIMEOUT = 1.0
        toju.CONNECT_TIMEOUT = 1.0

        return tornado.web.Application(toju.ROUTES, cookie_secret=COOKIE_SECRET)

    def _cookie(self, user="alice@example.com"):
        val = create_signed_value(COOKIE_SECRET, "user", json_encode(user)).decode()
        return {"Cookie": "user=%s" % val}

    # ── auth ──────────────────────────────────────────────────────────────
    def test_save_requires_login(self):
        r = self.fetch("/toju/save", method="POST", body="content=x")
        self.assertEqual(r.code, 401)
        self.assertEqual(json.loads(r.body)["error"], "authentication_required")

    def test_save_rejects_empty_content(self):
        r = self.fetch("/toju/save", method="POST", body="content=", headers=self._cookie())
        self.assertEqual(r.code, 400)
        self.assertEqual(json.loads(r.body)["error"], "no_content")

    # ── happy path + token forwarding ──────────────────────────────────────
    def test_save_forwards_token_and_returns_cid(self):
        import urllib.parse
        body = urllib.parse.urlencode({"content": SHEET, "fname": "inv.msc", "durationDays": "2"})
        r = self.fetch("/toju/save", method="POST", body=body, headers=self._cookie())
        self.assertEqual(r.code, 200)
        data = json.loads(r.body)
        self.assertEqual(data["result"], "ok")
        self.assertEqual(data["cid"], "bafkmockUNIT")
        self.assertTrue(data["mocked"])
        # Tornado must have forwarded the shared secret and the sheet verbatim.
        self.assertEqual(self.fake_state.last_token, SHARED_SECRET)
        self.assertEqual(self.fake_state.last_body["sheet"], SHEET)
        self.assertEqual(self.fake_state.last_body["durationDays"], 2)

    def test_retrieve_is_byte_exact(self):
        r = self.fetch("/toju/retrieve/bafkmockUNIT", headers=self._cookie())
        self.assertEqual(r.code, 200)
        self.assertEqual(r.body, SHEET.encode("utf-8"))

    def test_status_passthrough(self):
        r = self.fetch("/toju/status/bafkmockUNIT", headers=self._cookie())
        self.assertEqual(r.code, 200)
        self.assertEqual(json.loads(r.body)["cid"], "bafkmockUNIT")

    # ── error mapping ───────────────────────────────────────────────────────
    def test_timeout_maps_to_504(self):
        self.fake_state.delay = 3.0   # > SAVE_TIMEOUT (1.0s)
        import urllib.parse
        body = urllib.parse.urlencode({"content": SHEET})
        r = self.fetch("/toju/save", method="POST", body=body, headers=self._cookie())
        self.assertEqual(r.code, 504)
        self.assertEqual(json.loads(r.body)["error"], "sidecar_timeout")

    def test_unreachable_maps_to_502(self):
        # Point at a port where nothing listens.
        toju.TOJU_SIDECAR_URL = "http://127.0.0.1:1"
        r = self.fetch("/toju/retrieve/bafkmockUNIT", headers=self._cookie())
        self.assertEqual(r.code, 502)
        self.assertEqual(json.loads(r.body)["error"], "sidecar_unreachable")


if __name__ == "__main__":
    import tornado.httpserver  # noqa: F401  (used in get_app)
    unittest.main()
