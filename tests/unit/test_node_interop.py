"""Unit tests for handlers/node_interop.py against a fake sidecar (no Docker, no S3).

Run inside the app image:  docker compose exec -T app1 python3 -m unittest discover -s tests/unit -v
"""
import json
import sys
import unittest
from unittest import mock

import tornado.web
from tornado.testing import AsyncHTTPTestCase

sys.path.insert(0, ".")
from handlers import node_interop  # noqa: E402

SECRET = "unit-test-secret"


class FakeStorage:
    def __init__(self):
        self.items = {}

    def getFile(self, path):
        key = tuple(path)
        if key in self.items:
            return mock.Mock(data=self.items[key], spec=["data"])
        return mock.Mock(files=[]) if len(path) == 2 else None

    def createDir(self, path):
        return True

    def createFile(self, path, data):
        self.items[tuple(path)] = data
        return True

    def updateFile(self, path, data):
        self.items[tuple(path)] = data
        return True


class FakeSidecar(tornado.web.RequestHandler):
    seen = []
    store = {}
    mode = "ok"      # ok | reject | hang

    def prepare(self):
        FakeSidecar.seen.append((self.request.method, self.request.path, self.request.headers.get("X-Interop-Token")))
        if FakeSidecar.mode == "reject":
            self.set_status(401)
            self.finish({"error": "invalid_token"})

    def get(self, name=None):
        if name is None:
            return self.finish({"status": "ok", "storage": "s3"})
        owner = self.get_argument("owner")
        data = FakeSidecar.store.get((owner, name))
        if data is None:
            self.set_status(404)
            return self.finish({"error": "not_found", "message": "no such sheet"})
        self.finish({"savestr": data})

    def post(self, name=None):
        body = json.loads(self.request.body)
        FakeSidecar.store[(body["owner"], body["name"])] = body["savestr"]
        self.finish({"name": body["name"], "size": len(body["savestr"].encode("utf-8"))})


class NodeInteropTest(AsyncHTTPTestCase):
    def get_app(self):
        sidecar = tornado.web.Application([(r"/health", FakeSidecar), (r"/v1/sheets/(.+)", FakeSidecar), (r"/v1/sheets", FakeSidecar)])
        self.sidecar_server = tornado.httpserver.HTTPServer(sidecar)
        sock, port = tornado.testing.bind_unused_port()
        self.sidecar_server.add_sockets([sock])
        self.sidecar_url = "http://127.0.0.1:%d" % port
        return tornado.web.Application(node_interop.ROUTES, cookie_secret="x" * 32)

    def setUp(self):
        super().setUp()
        FakeSidecar.seen, FakeSidecar.store, FakeSidecar.mode = [], {}, "ok"
        self.storage = FakeStorage()
        self.patches = [
            mock.patch.object(node_interop, "NODE_INTEROP_URL", self.sidecar_url),
            mock.patch.object(node_interop, "NODE_INTEROP_SHARED_SECRET", SECRET),
            mock.patch.object(node_interop.InteropAuthedHandler, "get_current_user", lambda s: "u@x.com"),
            mock.patch.object(node_interop.cloud.storage, "storage", self.storage),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.sidecar_server.stop()
        super().tearDown()

    def post(self, path, **form):
        return self.fetch(path, method="POST", body="&".join("%s=%s" % (k, v) for k, v in form.items()))

    def test_health_does_not_send_the_token(self):
        r = self.fetch("/nodeinterop/health")
        self.assertEqual(r.code, 200)
        self.assertEqual(FakeSidecar.seen[-1][2], None)

    def test_publish_sends_the_token_and_the_sheet(self):
        self.storage.items[("home", "u@x.com", "s1")] = '{"a":"₹ हिंदी"}'
        r = self.post("/nodeinterop/publish", name="s1")
        self.assertEqual(r.code, 200, r.body)
        self.assertEqual(FakeSidecar.seen[-1][2], SECRET)
        self.assertEqual(FakeSidecar.store[("u@x.com", "s1")], '{"a":"₹ हिंदी"}')
        self.assertNotIn(SECRET, r.body.decode())

    def test_open_copies_back_and_refuses_overwrite(self):
        FakeSidecar.store[("u@x.com", "s1")] = "data-1"
        self.assertEqual(self.post("/nodeinterop/open", name="s1").code, 200)
        self.assertEqual(self.storage.items[("home", "u@x.com", "s1")], "data-1")
        self.assertEqual(self.post("/nodeinterop/open", name="s1").code, 409)
        FakeSidecar.store[("u@x.com", "s1")] = "data-2"
        self.assertEqual(self.post("/nodeinterop/open", name="s1", overwrite="yes").code, 200)
        self.assertEqual(self.storage.items[("home", "u@x.com", "s1")], "data-2")

    def test_open_name_with_spaces(self):
        FakeSidecar.store[("u@x.com", "GST% a b")] = "x"
        self.assertEqual(self.post("/nodeinterop/open", name="GST%25%20a%20b", **{"as": "copy"}).code, 200)

    def test_unknown_sheet_is_404(self):
        self.assertEqual(self.post("/nodeinterop/open", name="nope").code, 404)
        self.assertEqual(self.post("/nodeinterop/publish", name="nope").code, 404)

    def test_sidecar_rejecting_the_token_is_a_502_not_a_401(self):
        FakeSidecar.mode = "reject"
        self.storage.items[("home", "u@x.com", "s1")] = "x"
        r = self.post("/nodeinterop/publish", name="s1")
        self.assertEqual(r.code, 502)
        self.assertEqual(json.loads(r.body)["error"], "sidecar_auth_failed")

    def test_secret_not_configured_is_503_and_nothing_is_sent(self):
        with mock.patch.object(node_interop, "NODE_INTEROP_SHARED_SECRET", ""):
            self.assertEqual(self.post("/nodeinterop/publish", name="s1").code, 503)
            self.assertEqual(self.post("/nodeinterop/open", name="s1").code, 503)
        self.assertEqual(FakeSidecar.seen, [])

    def test_anonymous_is_401(self):
        with mock.patch.object(node_interop.InteropAuthedHandler, "get_current_user", lambda s: None):
            for path in ("/nodeinterop/health",):
                self.assertEqual(self.fetch(path).code, 401)
            self.assertEqual(self.post("/nodeinterop/publish", name="s1").code, 401)

    def test_unreachable_is_502(self):
        with mock.patch.object(node_interop, "NODE_INTEROP_URL", "http://127.0.0.1:1"):
            self.assertEqual(self.fetch("/nodeinterop/health").code, 502)


if __name__ == "__main__":
    unittest.main()
