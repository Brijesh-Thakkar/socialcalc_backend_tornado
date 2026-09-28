import json
import unittest
from unittest.mock import patch

import tornado.escape
import tornado.httpclient
import tornado.testing
import tornado.web

from handlers import zg_storage


class FakeSheet:
    data = "legacy sheet bytes"


class FakeSidecarClient:
    response = None
    error = None
    requests = []

    async def fetch(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response


class ZGStorageHandlerTest(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r"/api/v1/0g/archive", zg_storage.ZGArchiveHandler),
            (r"/api/v1/0g/archives", zg_storage.ZGArchiveHandler),
            (r"/api/v1/0g/download/(.*)", zg_storage.ZGDownloadHandler),
        ], cookie_secret="test-cookie-secret")

    def setUp(self):
        super().setUp()
        self.user = "owner@example.test"
        self.archives = []
        self.client = FakeSidecarClient()
        self.client.requests = []
        self.client.error = None
        self.client.response = tornado.httpclient.HTTPResponse(
            tornado.httpclient.HTTPRequest("http://sidecar/archive"),
            200,
            headers={"Content-Type": "application/json"},
            buffer=__import__("io").BytesIO(json.dumps({
                "rootHash": "0x" + "a" * 64,
                "txHash": "0x" + "b" * 64,
                "mock": True,
            }).encode()),
        )
        self.patches = [
            patch.object(zg_storage.AuthenticatedHandler, "get_current_user", return_value=self.user),
            patch.object(zg_storage.tornado.httpclient, "AsyncHTTPClient", return_value=self.client),
            patch.object(zg_storage.cloud.storage.storage, "getFile", return_value=FakeSheet()),
            patch.object(zg_storage.cloud.storage.storage, "getItem", side_effect=lambda _key: json.dumps({"archives": self.archives}).encode() if self.archives else None),
            patch.object(zg_storage.cloud.storage.storage, "putItem", side_effect=self._put_item),
            patch.dict("os.environ", {"ZG_MODE": "mock", "ZG_SIDECAR_URL": "http://sidecar"}),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        super().tearDown()

    def _put_item(self, _key, value):
        self.archives = json.loads(value)["archives"]
        return True

    def _cookie(self, user="owner@example.test"):
        value = tornado.web.create_signed_value(
            "test-cookie-secret", "user", tornado.escape.json_encode(user)
        ).decode()
        return {"Cookie": f"user={value}"}

    def test_archive_checks_owned_sheet_and_persists_mock_root(self):
        response = self.fetch(
            "/api/v1/0g/archive", method="POST",
            headers={**self._cookie(), "Content-Type": "application/json"},
            body=json.dumps({"fname": "demo", "data": "sheet"}),
        )
        self.assertEqual(response.code, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["mock"])
        self.assertEqual(self.archives[0]["fname"], "demo")
        self.assertEqual(self.client.requests[0].body, b"sheet")

    def test_archive_rejects_sheet_not_owned_by_user(self):
        with patch.object(zg_storage.cloud.storage.storage, "getFile", return_value=None):
            response = self.fetch(
                "/api/v1/0g/archive", method="POST",
                headers={**self._cookie(), "Content-Type": "application/json"},
                body=json.dumps({"fname": "someone-elses-sheet", "data": "sheet"}),
            )
        self.assertEqual(response.code, 404)
        self.assertEqual(self.client.requests, [])

    def test_archive_requires_login_and_enforces_size_limit(self):
        with patch.object(zg_storage.AuthenticatedHandler, "get_current_user", return_value=None):
            unauthenticated = self.fetch(
                "/api/v1/0g/archive", method="POST", body="{}"
            )
        self.assertEqual(unauthenticated.code, 401)
        oversized = self.fetch(
            "/api/v1/0g/archive", method="POST",
            headers={**self._cookie(), "Content-Type": "application/json"},
            body=json.dumps({"fname": "demo", "data": "x" * (1024 * 1024 + 1)}),
        )
        self.assertEqual(oversized.code, 413)

    def test_sidecar_unavailable_returns_503(self):
        self.client.error = tornado.httpclient.HTTPError(599, "offline")
        response = self.fetch(
            "/api/v1/0g/archive", method="POST",
            headers={**self._cookie(), "Content-Type": "application/json"},
            body=json.dumps({"fname": "demo", "data": "sheet"}),
        )
        self.assertEqual(response.code, 503)

    def test_0g_failure_returns_502_with_sdk_error_text(self):
        error_response = tornado.httpclient.HTTPResponse(
            tornado.httpclient.HTTPRequest("http://sidecar/archive"),
            502,
            buffer=__import__("io").BytesIO(json.dumps({"error": "actual SDK upload error"}).encode()),
        )
        self.client.error = tornado.httpclient.HTTPError(502, "sidecar error", response=error_response)
        response = self.fetch(
            "/api/v1/0g/archive", method="POST",
            headers={**self._cookie(), "Content-Type": "application/json"},
            body=json.dumps({"fname": "demo", "data": "sheet"}),
        )
        self.assertEqual(response.code, 502)
        self.assertEqual(json.loads(response.body)["error"], "actual SDK upload error")

    def test_download_requires_root_to_be_in_this_users_archive_list(self):
        with patch.object(zg_storage.cloud.storage.storage, "getItem", return_value=None):
            response = self.fetch(
                "/api/v1/0g/download/0x" + "a" * 64,
                headers=self._cookie(),
            )
        self.assertEqual(response.code, 404)
        self.assertEqual(self.client.requests, [])

    def test_archive_list_returns_only_this_users_records(self):
        self.archives = [{"fname": "my-sheet", "rootHash": "0x" + "a" * 64, "mock": True}]
        response = self.fetch("/api/v1/0g/archives", headers=self._cookie())
        self.assertEqual(response.code, 200)
        result = json.loads(response.body)
        self.assertTrue(result["mock"])
        self.assertEqual(result["archives"], self.archives)

    def test_download_is_scoped_to_user_archive_and_returns_mock_marker(self):
        root = "0x" + "a" * 64
        self.archives = [{"fname": "demo", "rootHash": root, "mock": True}]
        self.client.response = tornado.httpclient.HTTPResponse(
            tornado.httpclient.HTTPRequest("http://sidecar/download"),
            200,
            headers={"Content-Type": "application/octet-stream", "X-ZG-Mock": "true"},
            buffer=__import__("io").BytesIO(b"sheet bytes"),
        )
        response = self.fetch(f"/api/v1/0g/download/{root}", headers=self._cookie())
        self.assertEqual(response.code, 200)
        self.assertEqual(response.body, b"sheet bytes")
        self.assertEqual(response.headers.get("X-ZG-Mock"), "true")


if __name__ == "__main__":
    unittest.main()
