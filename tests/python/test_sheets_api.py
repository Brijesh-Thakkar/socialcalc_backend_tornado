"""
Unit tests for handlers/sheets_api.py (no S3/MinIO needed: storage is faked
in memory). Run from the repo root:

    venv/bin/python -m unittest tests.python.test_sheets_api -v
"""
import datetime
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

import tornado.escape
import tornado.web
from tornado.testing import AsyncHTTPTestCase

from handlers import sheets_api  # noqa: E402

JSON = {"Content-Type": "application/json"}
ORIGIN_OK = "http://localhost:5173"


class FakeStorage:
    """In-memory stand-in for the storage layer sheets_api touches."""

    def __init__(self):
        self.items = {}          # json path -> raw dict ({"type","data",...})
        self.fail_writes = False
        self.dirs_created = []

    def read_raw(self, path):
        return self.items.get(json.dumps(path))

    def head(self, path):
        raw = self.items.get(json.dumps(path))
        if raw is None:
            return None
        return len(json.dumps(raw)), datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)

    def createDir(self, path):
        self.dirs_created.append(path)
        self.items.setdefault(json.dumps(path), {"type": "dir", "path": path, "data": "[]"})
        return True

    def createFile(self, path, data):
        if self.fail_writes:
            return False
        parent = self.items.get(json.dumps(path[:-1]))
        if parent is None:
            return False
        self.items[json.dumps(path)] = {"type": "file", "path": path, "data": data}
        names = json.loads(parent["data"])
        names.append(path[-1])
        parent["data"] = json.dumps(names)
        return True

    def updateFile(self, path, data):
        if self.fail_writes or json.dumps(path) not in self.items:
            return False
        self.items[json.dumps(path)]["data"] = data
        return True

    def deleteFile(self, path):
        parent = self.items[json.dumps(path[:-1])]
        parent["data"] = json.dumps([n for n in json.loads(parent["data"]) if n != path[-1]])
        del self.items[json.dumps(path)]
        return True


class ValidateNameTest(unittest.TestCase):
    def test_accepts_normal_names(self):
        for n in ["default", "e2e_ai", "My Sheet 2026", "budget-v1.2", "日本語", "a" * 100]:
            self.assertIsNone(sheets_api.validate_name(n), n)

    def test_rejects_traversal_and_bad_names(self):
        bad = [
            "", "../x", "..", "a/..", "a/b", "/etc/passwd", "a\\b", "..\\x", "x..y",
            ".hidden", ".", "a\x00b", "a\nb", "a\tb", "a\x7fb", "a" * 101,
            "x');alert(1);//", 'a"b', "<b>", "securestore", "SecureStore", None, 5,
        ]
        for n in bad:
            self.assertIsNotNone(sheets_api.validate_name(n), repr(n))


class SheetsApiTest(AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application(sheets_api.ROUTES, cookie_secret="test-secret", xsrf_cookies=False)

    def setUp(self):
        super().setUp()
        self.fs = FakeStorage()
        self.fs.items['["home"]'] = {"type": "dir", "data": "[]"}
        patches = [
            mock.patch.object(sheets_api, "_read_raw", self.fs.read_raw),
            mock.patch.object(sheets_api, "_head", self.fs.head),
            mock.patch.object(sheets_api.storage, "createDir", self.fs.createDir),
            mock.patch.object(sheets_api.storage, "createFile", self.fs.createFile),
            mock.patch.object(sheets_api.storage, "updateFile", self.fs.updateFile),
            mock.patch.object(sheets_api.storage, "deleteFile", self.fs.deleteFile),
            mock.patch.object(sheets_api.cloud.authenticate.user, "authenticate_user",
                              lambda e, p: (e, p) == ("a@example.com", "pw")),
            mock.patch.dict(os.environ, {"ALLOWED_ORIGINS": ORIGIN_OK + ", https://app.example.com/"}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    # helpers
    def login(self, email="a@example.com", pw="pw"):
        r = self.fetch("/api/login", method="POST", headers=JSON, body=json.dumps({"email": email, "password": pw}))
        return r

    def cookie(self):
        r = self.login()
        self.assertEqual(r.code, 200)
        return r.headers["Set-Cookie"].split(";")[0]

    def req(self, path, method="GET", body=None, headers=None, authed=True):
        h = dict(headers or {})
        if authed:
            h["Cookie"] = self.cookie()
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        return self.fetch(path, method=method, body=body, headers=h, allow_nonstandard_methods=True)

    def put(self, name, data="x", **kw):
        return self.req("/api/sheets/" + name, "PUT", {"data": data}, JSON, **kw)

    # auth
    def test_login_ok_sets_same_cookie_as_legacy(self):
        r = self.login()
        self.assertEqual(r.code, 200)
        self.assertEqual(json.loads(r.body), {"ok": True, "user": "a@example.com"})
        sc = r.headers["Set-Cookie"]
        self.assertTrue(sc.startswith("user="))
        self.assertIn("SameSite=Lax", sc)
        self.assertNotIn("Secure", sc)

    def test_login_cookie_attrs_from_env(self):
        with mock.patch.dict(os.environ, {"COOKIE_SAMESITE": "None", "COOKIE_SECURE": "true"}):
            sc = self.login().headers["Set-Cookie"]
        self.assertIn("SameSite=None", sc)
        self.assertIn("Secure", sc)

    def test_login_bad_credentials_401(self):
        r = self.login(pw="nope")
        self.assertEqual(r.code, 401)
        self.assertNotIn("Set-Cookie", r.headers)

    def test_login_requires_json(self):
        r = self.fetch("/api/login", method="POST", body="email=a&password=b",
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(r.code, 415)

    def test_logout_clears_cookie(self):
        r = self.req("/api/logout", "POST", {}, JSON)
        self.assertEqual(r.code, 200)
        self.assertIn("user=;", r.headers["Set-Cookie"].replace('""', ""))

    def test_401_without_cookie(self):
        for method, path in [("GET", "/api/sheets"), ("GET", "/api/sheets/a"),
                             ("PUT", "/api/sheets/a"), ("DELETE", "/api/sheets/a")]:
            r = self.req(path, method, {"data": "x"} if method == "PUT" else None, JSON, authed=False)
            self.assertEqual(r.code, 401, (method, path))
            self.assertIn("error", json.loads(r.body))

    # CRUD
    def test_list_empty_does_not_create_dirs(self):
        r = self.req("/api/sheets")
        self.assertEqual(r.code, 200)
        self.assertEqual(json.loads(r.body), [])
        self.assertEqual(self.fs.dirs_created, [])

    def test_put_get_list_delete_roundtrip(self):
        raw = '{"numsheets":1,"sheetArr":{"sheet1":{"sheetstr":{"savestr":"version:1.5\\n"}}}}'
        self.assertEqual(self.put("e2e_ai", raw).code, 201)
        self.assertEqual(self.put("e2e_ai", raw + " ").code, 200)  # update
        r = self.req("/api/sheets/e2e_ai")
        self.assertEqual(json.loads(r.body), {"name": "e2e_ai", "data": raw + " "})
        lst = json.loads(self.req("/api/sheets").body)
        self.assertEqual([s["name"] for s in lst], ["e2e_ai"])
        self.assertEqual(set(lst[0]), {"name", "size", "modified"})
        self.assertEqual(lst[0]["modified"], "2026-01-02T03:04:05Z")
        # same layout as legacy /save: ["home", user, name]
        self.assertIn('["home", "a@example.com", "e2e_ai"]', self.fs.items)
        self.assertEqual(self.req("/api/sheets/e2e_ai", "DELETE").code, 204)
        self.assertEqual(self.req("/api/sheets/e2e_ai").code, 404)
        self.assertEqual(self.req("/api/sheets/e2e_ai", "DELETE").code, 404)

    def test_get_missing_404(self):
        self.assertEqual(self.req("/api/sheets/nope").code, 404)

    def test_storage_failure_is_500_not_success(self):
        self.fs.fail_writes = True
        r = self.put("s1")
        self.assertEqual(r.code, 500)
        self.assertNotIn("ok", json.loads(r.body))
        self.assertEqual(self.req("/api/sheets/s1").code, 404)

    def test_storage_unreachable_is_500(self):
        def boom(path):
            raise sheets_api.StorageError("down")
        with mock.patch.object(sheets_api, "_read_raw", boom):
            self.assertEqual(self.put("s1").code, 500)
            self.assertEqual(self.req("/api/sheets").code, 500)

    # validation
    def test_traversal_names_400(self):
        for enc in ["..%2Fx", "..%2F..%2Fetc%2Fpasswd", "a%2Fb", "a%5Cb", "..", ".hidden", "a%00b",
                    "a" * 101, "securestore", "x%27y"]:
            for method in ("GET", "PUT", "DELETE"):
                body = {"data": "x"} if method == "PUT" else None
                r = self.req("/api/sheets/" + enc, method, body, JSON)
                self.assertEqual(r.code, 400, (method, enc, r.body))
        self.assertEqual(self.fs.dirs_created, [])

    def test_put_rejects_non_json_content_type_415(self):
        for ctype in ["text/plain", "application/x-www-form-urlencoded"]:
            r = self.req("/api/sheets/s1", "PUT", '{"data":"x"}', {"Content-Type": ctype})
            self.assertEqual(r.code, 415, ctype)
        r = self.req("/api/sheets/s1", "PUT", '{"data":"x"}', {"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(r.code, 201)

    def test_delete_rejects_non_json_body_content_type(self):
        r = self.req("/api/sheets/s1", "DELETE", "x=1", {"Content-Type": "text/plain"})
        self.assertEqual(r.code, 415)

    def test_bad_json_bodies_400(self):
        for body in ["{", "[]", '{"nodata":1}', '{"data":5}']:
            r = self.req("/api/sheets/s1", "PUT", body, JSON)
            self.assertEqual(r.code, 400, body)

    def test_data_over_5mb_413(self):
        r = self.put("s1", "x" * (5 * 1024 * 1024 + 1))
        self.assertEqual(r.code, 413)
        self.assertEqual(self.put("s2", "x" * (200 * 1024)).code, 201)

    # CORS
    def test_preflight_allowed_origin(self):
        r = self.fetch("/api/sheets/a", method="OPTIONS", headers={
            "Origin": ORIGIN_OK, "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(r.code, 204)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], ORIGIN_OK)
        self.assertEqual(r.headers["Access-Control-Allow-Credentials"], "true")
        self.assertIn("PUT", r.headers["Access-Control-Allow-Methods"])
        self.assertIn("Content-Type", r.headers["Access-Control-Allow-Headers"])
        self.assertIn("Origin", r.headers["Vary"])

    def test_preflight_trailing_slash_in_env_matches(self):
        r = self.fetch("/api/sheets", method="OPTIONS", headers={"Origin": "https://app.example.com"})
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "https://app.example.com")

    def test_preflight_disallowed_origin_has_no_cors_headers(self):
        for origin in ["http://evil.example", "http://localhost:5174", ORIGIN_OK + ".evil.example", "null"]:
            r = self.fetch("/api/sheets/a", method="OPTIONS", headers={"Origin": origin})
            self.assertNotIn("Access-Control-Allow-Origin", r.headers, origin)
            self.assertNotIn("Access-Control-Allow-Credentials", r.headers, origin)

    def test_never_wildcard_and_errors_carry_cors(self):
        r = self.fetch("/api/sheets", headers={"Origin": ORIGIN_OK})  # 401
        self.assertEqual(r.code, 401)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], ORIGIN_OK)
        with mock.patch.dict(os.environ, {"ALLOWED_ORIGINS": "*"}):
            r = self.fetch("/api/sheets", headers={"Origin": "http://anything.example"})
            self.assertNotIn("Access-Control-Allow-Origin", r.headers)

    def test_no_cors_when_unconfigured(self):
        with mock.patch.dict(os.environ, {"ALLOWED_ORIGINS": ""}):
            r = self.fetch("/api/sheets", method="OPTIONS", headers={"Origin": ORIGIN_OK})
            self.assertNotIn("Access-Control-Allow-Origin", r.headers)


if __name__ == "__main__":
    unittest.main()
