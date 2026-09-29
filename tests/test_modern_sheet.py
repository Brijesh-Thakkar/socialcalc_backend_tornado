import json
import os
import unittest
from unittest.mock import patch

import tornado.escape
import tornado.testing
import tornado.web

from handlers.modern import MAX_MODERN_SHEET_BYTES, ModernSheetDataHandler, ModernStaticFileHandler


class FakeSheet:
    def __init__(self, data):
        self.data = data


class ModernSheetDataHandlerTest(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application(
            [
                (r"/api/v1/modern/sheet", ModernSheetDataHandler),
                (r"/modern/?(.*)", ModernStaticFileHandler, {
                    "path": os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "modern")),
                    "default_filename": "index.html",
                }),
            ],
            cookie_secret="test-cookie-secret",
        )

    def request_as(self, user, path):
        headers = {}
        if user:
            cookie = tornado.web.create_signed_value(
                "test-cookie-secret", "user", tornado.escape.json_encode(user)
            )
            headers["Cookie"] = "user=" + cookie.decode("utf-8")
        return self.fetch(path, headers=headers)

    @patch("handlers.modern.cloud.storage.storage.getFile")
    def test_requires_login(self, get_file):
        response = self.request_as(None, "/api/v1/modern/sheet?fname=budget")
        self.assertEqual(response.code, 401)
        get_file.assert_not_called()

    def test_modern_page_requires_the_existing_login_cookie(self):
        response = self.fetch("/modern/", follow_redirects=False)
        self.assertEqual(response.code, 302)
        self.assertEqual(response.headers["Location"], "/login")

    def test_modern_page_serves_the_built_bundle_after_login(self):
        response = self.request_as("owner@example.test", "/modern/")
        self.assertEqual(response.code, 200)
        self.assertIn(b"Modern SocialCalc Editor", response.body)

    @patch("handlers.modern.cloud.storage.storage.getFile")
    def test_returns_json_for_sheet_in_signed_in_users_directory(self, get_file):
        get_file.return_value = FakeSheet('{"sheetArr":{}}')
        response = self.request_as("owner@example.test", "/api/v1/modern/sheet?fname=budget")
        self.assertEqual(response.code, 200)
        self.assertEqual(json.loads(response.body), {"fname": "budget", "data": '{"sheetArr":{}}'})
        get_file.assert_called_once_with(["home", "owner@example.test", "budget"])

    @patch("handlers.modern.cloud.storage.storage.getFile")
    def test_cannot_read_another_users_sheet(self, get_file):
        get_file.return_value = None
        response = self.request_as("attacker@example.test", "/api/v1/modern/sheet?fname=private")
        self.assertEqual(response.code, 404)
        get_file.assert_called_once_with(["home", "attacker@example.test", "private"])

    @patch("handlers.modern.cloud.storage.storage.getFile")
    def test_rejects_oversized_sheet(self, get_file):
        get_file.return_value = FakeSheet("x" * (MAX_MODERN_SHEET_BYTES + 1))
        response = self.request_as("owner@example.test", "/api/v1/modern/sheet?fname=large")
        self.assertEqual(response.code, 413)

    @patch("handlers.modern.cloud.storage.storage.getFile")
    def test_rejects_path_like_sheet_names(self, get_file):
        response = self.request_as("owner@example.test", "/api/v1/modern/sheet?fname=..%2Fother")
        self.assertEqual(response.code, 400)
        get_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
