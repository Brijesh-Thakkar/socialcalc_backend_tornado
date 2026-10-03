"""Unit tests for handlers/omamori.py.

The firewall sidecar is replaced by a fake Hono-like app served from the same
test server (real HTTP, so forwarded headers/paths/timeouts are exercised for
real); "sidecar absent" uses a closed port. Run: python -m pytest
"""

import asyncio
import base64
import json
import logging
import os
import socket
import unittest

import tornado.web
from tornado.testing import AsyncHTTPTestCase

from handlers.omamori import (
    SHEET_ID_PATTERN,
    ExportChallengeHandler,
    OmamoriSignHandler,
)

PAY_TO = "0x" + "ab" * 20
SELLER = "seller@example.com"
SHEETS = {"budget", "Q3-report_v2"}
AUTH = "Bearer yk_test_not_a_real_key_0123456789"
SIGNATURE = "sig_test_value_never_logged_abcdef"
CHALLENGE_HEADER = "eyJ0ZXN0IjoidmFsdWUifQ=="


class FakeFirewall(tornado.web.RequestHandler):
    """Stands in for omamori-firewall: records each call, replies as told."""

    def initialize(self, state):
        self.state = state

    async def _handle(self, path):
        self.state["calls"].append({
            "method": self.request.method,
            "path": "/" + path,
            "headers": dict(self.request.headers),
            "body": self.request.body,
        })
        reply = self.state["reply"]
        if reply.get("sleep"):
            await asyncio.sleep(reply["sleep"])
        self.set_status(reply.get("status", 200))
        raw = reply.get("raw")
        self.finish(raw if raw is not None else json.dumps(reply.get("json", {})))

    async def post(self, path):
        await self._handle(path)

    async def get(self, path):
        await self._handle(path)


def _sheet_exists(seller, sheet_id):
    assert seller == SELLER
    return sheet_id in SHEETS


def _sheet_lookup_fails(seller, sheet_id):
    raise RuntimeError("storage down")


def _closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class OmamoriTestBase(AsyncHTTPTestCase):
    ENV = {
        "OMAMORI_PAYTO_ADDRESS": PAY_TO,
        "OMAMORI_SELLER_USER": SELLER,
        "OMAMORI_EXPORT_PRICE": "10000",
        "OMAMORI_RESOURCE_BASE_URL": "http://nginx",
        "OMAMORI_PUBLIC_BASE_URL": "https://sheets.example.org",
        "OMAMORI_TIMEOUT_S": "2",
    }

    def setUp(self):
        self.state = {"calls": [], "reply": {"json": {}}}
        self._saved_env = {k: os.environ.get(k) for k in list(self.ENV) + ["OMAMORI_FIREWALL_URL"]}
        os.environ.update(self.ENV)
        super().setUp()
        os.environ["OMAMORI_FIREWALL_URL"] = self.get_url("/fake-fw")

    def tearDown(self):
        super().tearDown()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def get_app(self):
        return tornado.web.Application([
            (r"/x402/sheet/(" + SHEET_ID_PATTERN + r")/export", ExportChallengeHandler,
             {"sheet_exists": _sheet_exists}),
            (r"/broken/x402/sheet/(" + SHEET_ID_PATTERN + r")/export", ExportChallengeHandler,
             {"sheet_exists": _sheet_lookup_fails}),
            (r"/omamori/sign", OmamoriSignHandler),
            (r"/fake-fw/(.*)", FakeFirewall, {"state": self.state}),
        ])


class ChallengeTests(OmamoriTestBase):
    def _decoded(self, response):
        return json.loads(base64.b64decode(response.headers["PAYMENT-REQUIRED"]))

    def test_402_with_x402_v2_header_in_store_field_order(self):
        response = self.fetch("/x402/sheet/budget/export")
        self.assertEqual(response.code, 402)
        decoded = self._decoded(response)
        self.assertEqual(list(decoded), ["x402Version", "error", "resource", "accepts", "extensions"])
        self.assertEqual(decoded["x402Version"], 2)
        self.assertEqual(list(decoded["resource"]), ["url", "description", "mimeType"])
        self.assertEqual(decoded["resource"]["url"], "https://sheets.example.org/x402/sheet/budget/export")
        self.assertEqual(len(decoded["accepts"]), 1)
        accept = decoded["accepts"][0]
        self.assertEqual(list(accept), ["scheme", "network", "amount", "asset", "payTo", "maxTimeoutSeconds", "extra"])
        self.assertEqual(accept["scheme"], "exact")
        self.assertEqual(accept["network"], "eip155:84532")
        self.assertEqual(accept["amount"], "10000")
        self.assertEqual(accept["asset"], "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
        self.assertEqual(accept["payTo"], PAY_TO)
        self.assertEqual(accept["extra"], {"name": "USDC", "version": "2"})
        self.assertIn("payment-identifier", decoded["extensions"])
        self.assertEqual(json.loads(response.body), decoded)

    def test_header_is_compact_json_like_json_stringify(self):
        raw = base64.b64decode(self.fetch("/x402/sheet/budget/export").headers["PAYMENT-REQUIRED"])
        self.assertNotIn(b": ", raw)
        self.assertNotIn(b", ", raw)

    def test_challenge_is_byte_identical_across_requests_and_hosts(self):
        headers = set()
        for host in ("localhost:8080", "nginx", "app1:8888", "evil.example", "sheets.example.org"):
            response = self.fetch("/x402/sheet/budget/export", headers={"Host": host})
            self.assertEqual(response.code, 402)
            headers.add(response.headers["PAYMENT-REQUIRED"])
        self.assertEqual(len(headers), 1)

    def test_price_comes_from_env_not_the_client(self):
        response = self.fetch("/x402/sheet/budget/export?amount=1&price=1",
                              headers={"X-Price": "1"})
        self.assertEqual(self._decoded(response)["accepts"][0]["amount"], "10000")

    def test_missing_sheet_is_404_before_any_challenge(self):
        response = self.fetch("/x402/sheet/nope/export")
        self.assertEqual(response.code, 404)
        self.assertNotIn("PAYMENT-REQUIRED", response.headers)
        self.assertEqual(json.loads(response.body), {"error": "sheet_not_found"})

    def test_invalid_sheet_id_does_not_route(self):
        for path in ("/x402/sheet/bad.id/export", "/x402/sheet/-lead/export", "/x402/sheet/" + "a" * 65 + "/export"):
            response = self.fetch(path)
            self.assertEqual(response.code, 404, path)
            self.assertNotIn("PAYMENT-REQUIRED", response.headers)

    def test_payment_header_gets_501_and_no_content(self):
        for header in ("PAYMENT-SIGNATURE", "X-PAYMENT"):
            response = self.fetch("/x402/sheet/budget/export", headers={header: "anything"})
            self.assertEqual(response.code, 501, header)
            self.assertEqual(json.loads(response.body)["error"], "settlement_disabled")
            self.assertNotIn("PAYMENT-REQUIRED", response.headers)

    def test_unconfigured_is_503(self):
        for key, value in (("OMAMORI_PAYTO_ADDRESS", ""), ("OMAMORI_PAYTO_ADDRESS", "0x123"),
                           ("OMAMORI_SELLER_USER", ""), ("OMAMORI_EXPORT_PRICE", "0"),
                           ("OMAMORI_EXPORT_PRICE", "1.5"), ("OMAMORI_EXPORT_PRICE", "-5")):
            os.environ[key] = value
            response = self.fetch("/x402/sheet/budget/export")
            self.assertEqual(response.code, 503, (key, value))
            self.assertEqual(json.loads(response.body), {"error": "omamori_not_configured"})
            self.assertNotIn("PAYMENT-REQUIRED", response.headers)
            os.environ.update(self.ENV)

    def test_storage_failure_is_503_not_a_challenge(self):
        response = self.fetch("/broken/x402/sheet/budget/export")
        self.assertEqual(response.code, 503)
        self.assertNotIn("PAYMENT-REQUIRED", response.headers)


class SignTestBase(OmamoriTestBase):
    def sign(self, body=None, headers=None, raw=None):
        payload = raw if raw is not None else json.dumps(body if body is not None else self.valid_body())
        all_headers = {"Content-Type": "application/json", "Authorization": AUTH}
        all_headers.update(headers or {})
        all_headers = {k: v for k, v in all_headers.items() if v is not None}
        response = self.fetch("/omamori/sign", method="POST", body=payload, headers=all_headers)
        return response, (json.loads(response.body) if response.body else None)

    @staticmethod
    def valid_body(**overrides):
        body = {
            "intentId": "intent_123",
            "resourceUrl": "https://sheets.example.org/x402/sheet/budget/export",
            "paymentRequiredHeader": CHALLENGE_HEADER,
            "context": {"task": "export my budget sheet"},
        }
        body.update(overrides)
        return body

    def reply(self, **reply):
        self.state["reply"] = reply


class SignMappingTests(SignTestBase):
    def test_pay_with_signature_is_200(self):
        self.reply(json={"verdict": "pay", "reason": "all pipeline checks passed",
                         "receiptId": "r1", "paymentSignature": SIGNATURE, "payer": "0xabc"})
        response, body = self.sign()
        self.assertEqual(response.code, 200)
        self.assertEqual(body, {"verdict": "pay", "reason": "all pipeline checks passed",
                                "receiptId": "r1", "paymentSignature": SIGNATURE})

    def test_pay_without_signature_is_502_refuse(self):
        for extra in ({}, {"paymentSignature": ""}, {"paymentSignature": 42}):
            self.reply(json=dict({"verdict": "pay", "reason": "ok", "receiptId": "r1"}, **extra))
            response, body = self.sign()
            self.assertEqual(response.code, 502, extra)
            self.assertEqual(body, {"verdict": "refuse", "reason": "malformed_firewall_response"})

    def test_ask_human_is_202_with_only_safe_approval_fields(self):
        self.reply(json={"verdict": "ask_human", "reason": "awaiting World ID approval", "receiptId": "r2",
                         "approval": {"verificationUri": "https://example/device", "userCode": "ABCD",
                                      "expiresAt": "2026-10-03T00:00:00Z", "deviceCode": "secret-device-code"}})
        response, body = self.sign()
        self.assertEqual(response.code, 202)
        self.assertEqual(body["verdict"], "ask_human")
        self.assertEqual(body["approval"], {"verificationUri": "https://example/device", "userCode": "ABCD",
                                            "expiresAt": "2026-10-03T00:00:00Z"})

    def test_refuse_is_403(self):
        self.reply(json={"verdict": "refuse", "reason": "merchant: payee_mismatch", "receiptId": "r3"})
        response, body = self.sign()
        self.assertEqual(response.code, 403)
        self.assertEqual(body, {"verdict": "refuse", "reason": "merchant: payee_mismatch", "receiptId": "r3"})

    def test_firewall_400_401_403_pass_through_as_refuse(self):
        for status, error in ((400, "invalid_sign_request"), (401, "unauthorized"), (403, "forbidden")):
            self.reply(status=status, json={"error": error, "issues": ["detail"]})
            response, body = self.sign()
            self.assertEqual(response.code, status)
            self.assertEqual(body, {"verdict": "refuse", "reason": error})

    def test_firewall_4xx_without_json_still_refuses(self):
        self.reply(status=401, raw="nope")
        response, body = self.sign()
        self.assertEqual(response.code, 401)
        self.assertEqual(body, {"verdict": "refuse", "reason": "firewall_rejected_request"})

    def test_other_firewall_statuses_are_502(self):
        for status in (404, 429, 500, 503):
            self.reply(status=status, json={"verdict": "pay", "reason": "x", "receiptId": "r",
                                            "paymentSignature": SIGNATURE})
            response, body = self.sign()
            self.assertEqual(response.code, 502, status)
            self.assertEqual(body, {"verdict": "refuse", "reason": "firewall_error"})

    def test_malformed_200_responses_are_502_refuse(self):
        cases = [
            {"raw": "not json"},
            {"raw": "[]"},
            {"json": {"verdict": "maybe", "reason": "x", "receiptId": "r"}},
            {"json": {"verdict": "PAY", "reason": "x", "receiptId": "r", "paymentSignature": SIGNATURE}},
            {"json": {"verdict": "pay", "receiptId": "r", "paymentSignature": SIGNATURE}},
            {"json": {"verdict": "pay", "reason": "x", "paymentSignature": SIGNATURE}},
            {"json": {"verdict": "refuse", "reason": None, "receiptId": "r"}},
            {"json": {}},
        ]
        for reply in cases:
            self.reply(**reply)
            response, body = self.sign()
            self.assertEqual(response.code, 502, reply)
            self.assertEqual(body["verdict"], "refuse", reply)

    def test_timeout_is_504(self):
        os.environ["OMAMORI_TIMEOUT_S"] = "0.3"
        self.reply(sleep=1.5, json={"verdict": "pay", "reason": "late", "receiptId": "r",
                                    "paymentSignature": SIGNATURE})
        response, body = self.sign()
        self.assertEqual(response.code, 504)
        self.assertEqual(body, {"verdict": "refuse", "reason": "firewall_timeout"})

    def test_sidecar_absent_connection_refused_is_502(self):
        os.environ["OMAMORI_FIREWALL_URL"] = "http://127.0.0.1:%d" % _closed_port()
        response, body = self.sign()
        self.assertEqual(response.code, 502)
        self.assertEqual(body, {"verdict": "refuse", "reason": "firewall_unreachable"})

    def test_sidecar_absent_unresolvable_host_is_502(self):
        os.environ["OMAMORI_FIREWALL_URL"] = "http://omamori-firewall.invalid:4001"
        response, body = self.sign()
        self.assertEqual(response.code, 502)
        self.assertEqual(body, {"verdict": "refuse", "reason": "firewall_unreachable"})


class ProxyHygieneTests(SignTestBase):
    def test_only_authorization_is_forwarded_and_only_to_sign(self):
        self.reply(json={"verdict": "refuse", "reason": "x", "receiptId": "r"})
        self.sign(headers={"x-yakusoku-admin": "1", "Cookie": "user=abc", "X-Forwarded-For": "127.0.0.1",
                           "X-Real-IP": "127.0.0.1", "PAYMENT-SIGNATURE": "x", "X-Custom": "y",
                           "User-Agent": "evil-agent"})
        self.assertEqual(len(self.state["calls"]), 1)
        call = self.state["calls"][0]
        self.assertEqual((call["method"], call["path"]), ("POST", "/sign"))
        sent = {k.lower(): v for k, v in call["headers"].items()}
        self.assertEqual(sent["authorization"], AUTH)
        self.assertEqual(sent["content-type"], "application/json")
        self.assertEqual(sent["user-agent"], "socialcalc-omamori")
        # Only transport headers Tornado's client adds on its own may appear.
        self.assertLessEqual(set(sent), {"authorization", "content-type", "user-agent", "host",
                                         "content-length", "accept-encoding", "connection"})

    def test_forwarded_body_is_rebuilt_and_allow_listed(self):
        self.reply(json={"verdict": "refuse", "reason": "x", "receiptId": "r"})
        self.sign(body=self.valid_body(
            resourceUrl="http://169.254.169.254:9999/x402/sheet/budget/export?evil=1#frag",
            purchaseRef="p-1", extraField="dropped", paymentRequired={"x402Version": 2}))
        forwarded = json.loads(self.state["calls"][0]["body"])
        self.assertEqual(forwarded, {
            "paymentRequiredHeader": CHALLENGE_HEADER,
            "resourceUrl": "http://nginx/x402/sheet/budget/export",
            "intentId": "intent_123",
            "context": {"task": "export my budget sheet"},
            "purchaseRef": "p-1",
        })

    def test_body_over_64kb_is_413_without_calling_firewall(self):
        body = self.valid_body(context={"blob": "x" * (64 * 1024)})
        response, data = self.sign(body=body)
        self.assertEqual(response.code, 413)
        self.assertEqual(data, {"verdict": "refuse", "reason": "request_too_large"})
        self.assertEqual(self.state["calls"], [])

    def test_wrong_content_type_bad_json_and_bad_fields_are_400(self):
        cases = [
            ({"Content-Type": "text/plain"}, json.dumps(self.valid_body()), "content_type_must_be_json"),
            ({"Content-Type": "application/x-www-form-urlencoded"}, "a=b", "content_type_must_be_json"),
            ({}, "{not json", "invalid_json"),
            ({}, "[1, 2]", "invalid_json"),
            ({}, json.dumps(self.valid_body(paymentRequiredHeader="")), "invalid_payment_required_header"),
            ({}, json.dumps({"resourceUrl": "/x402/sheet/budget/export"}), "invalid_payment_required_header"),
            ({}, json.dumps(self.valid_body(intentId=7)), "invalid_intent_id"),
            ({}, json.dumps(self.valid_body(context="string")), "invalid_context"),
        ]
        for headers, raw, reason in cases:
            response, data = self.sign(headers=headers, raw=raw)
            self.assertEqual(response.code, 400, reason)
            self.assertEqual(data, {"verdict": "refuse", "reason": reason})
        self.assertEqual(self.state["calls"], [])

    def test_json_content_type_with_charset_is_accepted(self):
        self.reply(json={"verdict": "refuse", "reason": "x", "receiptId": "r"})
        response, _ = self.sign(headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(response.code, 403)

    def test_missing_authorization_is_401_without_calling_firewall(self):
        response, data = self.sign(headers={"Authorization": None})
        self.assertEqual(response.code, 401)
        self.assertEqual(data, {"verdict": "refuse", "reason": "missing_authorization"})
        self.assertEqual(self.state["calls"], [])

    def test_get_and_other_methods_are_not_proxied(self):
        for method in ("GET", "PUT", "DELETE"):
            response = self.fetch("/omamori/sign", method=method,
                                  body=None if method in ("GET", "DELETE") else "{}",
                                  headers={"Authorization": AUTH, "Content-Type": "application/json"})
            self.assertEqual(response.code, 405, method)
        self.assertEqual(self.state["calls"], [])

    def test_auth_and_signature_never_logged(self):
        self.reply(json={"verdict": "pay", "reason": "ok", "receiptId": "r1", "paymentSignature": SIGNATURE})
        with self.assertLogs("handlers.omamori", level="DEBUG") as captured:
            response, _ = self.sign()
            self.reply(status=401, json={"error": "unauthorized"})
            self.sign()
        self.assertEqual(response.code, 200)
        logged = "\n".join(captured.output)
        self.assertNotIn(AUTH.split()[1], logged)
        self.assertNotIn(SIGNATURE, logged)


class SsrfGuardTests(SignTestBase):
    REJECTED = [
        # other paths, on our own host or anywhere else
        "https://sheets.example.org/x402/sheet/budget",
        "https://sheets.example.org/x402/sheet/budget/export/",
        "https://sheets.example.org/x402/sheet/budget/export/extra",
        "https://sheets.example.org/save",
        "/omamori/sign",
        "/control",
        # path traversal
        "http://x/x402/sheet/../export",
        "http://x/x402/sheet/budget/../../control",
        "http://x/x402/sheet/./export",
        "http://x/x402/sheet/%2e%2e/export",
        "http://x/x402/sheet/%2E%2E/export",
        "http://x/x402/sheet/budget%2F..%2F..%2Fcontrol/export",
        "http://x/x402/sheet/budget\\..\\control/export",
        # double encoding
        "http://x/x402/sheet/%252e%252e/export",
        "http://x/x402/sheet/budget%252F..%252Fcontrol/export",
        "http://x/%78402/sheet/budget/export",
        # internal hosts
        "http://minio:9000/mc2-app-storage-useast1/",
        "http://kubo:5001/api/v0/cat?arg=x",
        "http://127.0.0.1:4001/control",
        "http://127.0.0.1:4001/dev/promises/1/approve",
        "http://omamori-firewall:4001/events?admin=1",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8888/save",
        # userinfo tricks
        "http://a@b/",
        "http://sheets.example.org@minio:9000/",
        "http://user:pass@127.0.0.1:4001/control",
        # odd schemes, whitespace, case, non-strings
        "file:///etc/passwd",
        "gopher://127.0.0.1:11211/_stats",
        "http://x/X402/sheet/budget/export",
        "http://x/x402/sheet/budget/export\n",
        "http://x/x402/sheet/bud get/export",
        "http://x/x402/sheet//export",
        "",
        "x" * 3000,
        None,
        42,
        ["/x402/sheet/budget/export"],
    ]

    def test_every_disallowed_resource_is_400_with_zero_firewall_calls(self):
        for url in self.REJECTED:
            body = self.valid_body(resourceUrl=url)
            if url is None:
                body.pop("resourceUrl")
            response, data = self.sign(body=body)
            self.assertEqual(response.code, 400, repr(url))
            self.assertEqual(data, {"verdict": "refuse", "reason": "resource_not_allowed"}, repr(url))
        self.assertEqual(self.state["calls"], [])

    def test_allowed_path_is_rebuilt_on_the_resource_base_whatever_the_host(self):
        self.reply(json={"verdict": "refuse", "reason": "x", "receiptId": "r"})
        for url in ("https://sheets.example.org/x402/sheet/budget/export",
                    "/x402/sheet/budget/export",
                    "http://a@127.0.0.1:4001/x402/sheet/Q3-report_v2/export?x=1#y",
                    "http://minio:9000/x402/sheet/budget/export"):
            self.sign(body=self.valid_body(resourceUrl=url))
        sent = [json.loads(c["body"])["resourceUrl"] for c in self.state["calls"]]
        self.assertEqual(sent, ["http://nginx/x402/sheet/budget/export",
                                "http://nginx/x402/sheet/budget/export",
                                "http://nginx/x402/sheet/Q3-report_v2/export",
                                "http://nginx/x402/sheet/budget/export"])


if __name__ == "__main__":
    unittest.main()
