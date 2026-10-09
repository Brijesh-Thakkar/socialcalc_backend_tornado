"""Unit tests for handlers/cors.py that need different ALLOWED_ORIGINS values.

Run from the repo root:  python -m unittest tests.unit.test_cors -v
(no stack needed; the E2E spec tests/e2e/cors.spec.ts covers the running service)
"""
import os
import unittest
from unittest import mock

import tornado.web
from tornado.testing import AsyncHTTPTestCase

from handlers.cors import CorsMixin, allowed_origins, DEFAULT_ALLOWED_ORIGINS


class Creds(CorsMixin, tornado.web.RequestHandler):
    cors_allow_credentials = True

    def get(self):
        self.write("ok")


class Anon(CorsMixin, tornado.web.RequestHandler):
    def get(self):
        self.write("ok")


class Pdf(CorsMixin, tornado.web.RequestHandler):
    # Mirrors HtmlToPdfHandler's configuration.
    cors_env_var = "HTMLTOPDF_ALLOWED_ORIGINS"
    cors_default_origins = "*"

    def get(self):
        self.write("ok")


class Boom(CorsMixin, tornado.web.RequestHandler):
    def get(self):
        raise tornado.web.HTTPError(400)


class CorsTestBase(AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([(r"/creds", Creds), (r"/anon", Anon), (r"/pdf", Pdf), (r"/boom", Boom)])

    def fetch_with(self, path, origin=None, method="GET", env=None, **kw):
        headers = dict(kw.pop("headers", {}))
        if origin:
            headers["Origin"] = origin
        with mock.patch.dict(os.environ, env or {}, clear=False):
            return self.fetch(path, method=method, headers=headers, body=None, **kw)


class DefaultListTest(CorsTestBase):
    def setUp(self):
        super().setUp()
        os.environ.pop("ALLOWED_ORIGINS", None)

    def test_default_list_is_capacitor_origins(self):
        self.assertEqual(allowed_origins(), DEFAULT_ALLOWED_ORIGINS.split(","))
        self.assertIn("capacitor://localhost", allowed_origins())

    def test_allowed_origin_echoed_with_credentials(self):
        r = self.fetch_with("/creds", "capacitor://localhost")
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "capacitor://localhost")
        self.assertEqual(r.headers["Access-Control-Allow-Credentials"], "true")
        self.assertIn("Origin", r.headers["Vary"])

    def test_disallowed_origin_gets_nothing(self):
        r = self.fetch_with("/creds", "https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)
        self.assertNotIn("Access-Control-Allow-Credentials", r.headers)

    def test_anonymous_handler_never_sends_credentials(self):
        r = self.fetch_with("/anon", "http://localhost")
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "http://localhost")
        self.assertNotIn("Access-Control-Allow-Credentials", r.headers)

    def test_preflight_allowed_and_disallowed(self):
        ok = self.fetch_with("/creds", "http://localhost", method="OPTIONS",
                             headers={"Access-Control-Request-Method": "POST"})
        self.assertEqual(ok.code, 204)
        self.assertIn("POST", ok.headers["Access-Control-Allow-Methods"])
        bad = self.fetch_with("/creds", "https://evil.example", method="OPTIONS",
                              headers={"Access-Control-Request-Method": "POST"})
        self.assertEqual(bad.code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", bad.headers)

    def test_error_response_keeps_cors_headers(self):
        r = self.fetch_with("/boom", "http://localhost")
        self.assertEqual(r.code, 400)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "http://localhost")


class EnvConfigTest(CorsTestBase):
    def test_env_overrides_default_and_trims(self):
        env = {"ALLOWED_ORIGINS": " http://localhost:5173 , https://app.example "}
        self.assertEqual(self._origins(env), ["http://localhost:5173", "https://app.example"])
        r = self.fetch_with("/creds", "http://localhost:5173", env=env)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "http://localhost:5173")
        r = self.fetch_with("/creds", "capacitor://localhost", env=env)  # no longer listed
        self.assertNotIn("Access-Control-Allow-Origin", r.headers)

    def test_empty_env_falls_back_to_default(self):
        self.assertEqual(self._origins({"ALLOWED_ORIGINS": "  "}), DEFAULT_ALLOWED_ORIGINS.split(","))

    def test_explicit_wildcard_never_gets_credentials(self):
        env = {"ALLOWED_ORIGINS": "*"}
        r = self.fetch_with("/creds", "https://anything.example", env=env)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
        self.assertNotIn("Access-Control-Allow-Credentials", r.headers)

    def test_listed_origin_beats_wildcard_and_keeps_credentials(self):
        env = {"ALLOWED_ORIGINS": "*,http://localhost"}
        r = self.fetch_with("/creds", "http://localhost", env=env)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "http://localhost")
        self.assertEqual(r.headers["Access-Control-Allow-Credentials"], "true")

    @staticmethod
    def _origins(env):
        with mock.patch.dict(os.environ, env, clear=False):
            return allowed_origins()


class HtmlToPdfCompatTest(CorsTestBase):
    def setUp(self):
        super().setUp()
        for v in ("HTMLTOPDF_ALLOWED_ORIGINS", "ALLOWED_ORIGINS"):
            os.environ.pop(v, None)

    def test_default_is_open_star_without_credentials(self):
        for origin in ("https://anything.example", "capacitor://localhost", None):
            r = self.fetch_with("/pdf", origin)
            self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
            self.assertNotIn("Access-Control-Allow-Credentials", r.headers)

    def test_default_preflight_is_allowed_for_any_origin(self):
        r = self.fetch_with("/pdf", "https://anything.example", method="OPTIONS",
                            headers={"Access-Control-Request-Method": "POST"})
        self.assertEqual(r.code, 204)
        self.assertEqual(r.headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("POST", r.headers["Access-Control-Allow-Methods"])

    def test_webapp_allowlist_does_not_affect_htmltopdf_and_vice_versa(self):
        env = {"ALLOWED_ORIGINS": "http://localhost"}
        self.assertEqual(self.fetch_with("/pdf", "https://x.example", env=env).headers["Access-Control-Allow-Origin"], "*")
        self.assertNotIn("Access-Control-Allow-Origin", self.fetch_with("/creds", "https://x.example", env=env).headers)

    def test_restricting_htmltopdf_via_its_own_env_var(self):
        env = {"HTMLTOPDF_ALLOWED_ORIGINS": "http://localhost:3000"}
        ok = self.fetch_with("/pdf", "http://localhost:3000", env=env)
        self.assertEqual(ok.headers["Access-Control-Allow-Origin"], "http://localhost:3000")
        self.assertNotIn("Access-Control-Allow-Credentials", ok.headers)
        self.assertNotIn("Access-Control-Allow-Origin", self.fetch_with("/pdf", "https://evil.example", env=env).headers)
        self.assertEqual(self.fetch_with("/pdf", "https://evil.example", method="OPTIONS", env=env).code, 403)


if __name__ == "__main__":
    unittest.main()
