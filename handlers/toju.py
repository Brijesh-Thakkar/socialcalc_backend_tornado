"""Decentralized sheet storage via the ``toju-sidecar`` (Storacha-Solana-Sdk).

***  MOCK-BACKED  ***

This saves a SocialCalc sheet to IPFS and (in a real deployment) pays for the storage with
SOL, through the ``@toju.network/sol`` SDK. Tornado never imports the SDK or talks to Solana:
it calls the ``toju-sidecar`` over HTTP with ``AsyncHTTPClient``, exactly like the meshkit and
fastapi-interop sidecars (see handlers/interop.py).

In this integration the sidecar is **MOCK-BACKED**: it drives the SDK's real HTTP calls against
a local ``toju-mock`` instead of the real (suspended) toju.network backend, so there is NO real
IPFS pinning, NO real Solana RPC and NO money. See CONTRACT.md and the READMEs. When a real
backend exists, only the sidecar's TOJU_API_URL / TOJU_MODE change; these Tornado routes do not.

Routes (registered in cloudmain.py AND cloudmain-dev.py with ``*toju.ROUTES``):

    POST /toju/save            form ``content`` (SocialCalc save string) + ``fname``? + ``durationDays``?
                               -> JSON {result, cid, url, signature, mocked}
    GET  /toju/retrieve/<cid>  -> the stored bytes, byte-identical to what was saved
    GET  /toju/status/<cid>    -> JSON {result, cid, active, expiresAt, ...}
    GET  /toju/health          -> JSON sidecar health

Error mapping (same convention as handlers/interop.py and the meshkit handlers):
    sidecar unreachable (599, connection refused, DNS)  -> 502
    request/connect timeout                              -> 504
    sidecar 4xx (bad request, bad token, 404 ...)        -> passed through with its code
    anything else                                        -> 500
"""

import json
import logging
import os
import re

import tornado.escape
import tornado.httpclient
import tornado.web
from tornado.simple_httpclient import HTTPTimeoutError

TOJU_SIDECAR_URL = os.getenv("TOJU_SIDECAR_URL", "http://localhost:5056").rstrip("/")
TOJU_SHARED_SECRET = os.getenv("TOJU_SHARED_SECRET", "")
CONNECT_TIMEOUT = float(os.getenv("TOJU_CONNECT_TIMEOUT", "5"))
HEALTH_TIMEOUT = float(os.getenv("TOJU_HEALTH_TIMEOUT", "5"))
SAVE_TIMEOUT = float(os.getenv("TOJU_SAVE_TIMEOUT", "60"))
RETRIEVE_TIMEOUT = float(os.getenv("TOJU_RETRIEVE_TIMEOUT", "30"))
STATUS_TIMEOUT = float(os.getenv("TOJU_STATUS_TIMEOUT", "15"))
MAX_UPLOAD_BYTES = int(float(os.getenv("TOJU_MAX_UPLOAD_MB", "20")) * 1024 * 1024)

# IPFS CIDs: mock uses "bafkmock...", Pinata CIDv1 "baf...", Kubo CIDv0 "Qm...". Keep permissive but bounded.
CID_RE = re.compile(r"^[A-Za-z0-9]{1,128}$")


def _map_sidecar_error(exc):
    """Return (http_status, error_code, message) for an exception from AsyncHTTPClient."""
    # Timeouts first: HTTPTimeoutError is an HTTPClientError with code 599 and must become 504, not 502.
    if isinstance(exc, HTTPTimeoutError):
        return 504, "sidecar_timeout", "the toju sidecar did not answer in time"
    if isinstance(exc, tornado.httpclient.HTTPClientError):
        if exc.code == 599:
            return 502, "sidecar_unreachable", "the toju sidecar is unreachable"
        if exc.code == 504:
            return 504, "sidecar_timeout", "the toju sidecar timed out"
        if exc.response is not None and 400 <= exc.code < 500:
            try:
                body = json.loads(exc.response.body)
                return exc.code, str(body.get("error", "sidecar_rejected")), str(body.get("message", ""))[:300]
            except (ValueError, AttributeError):
                return exc.code, "sidecar_rejected", "the toju sidecar rejected the request"
        return 500, "sidecar_error", "the toju sidecar failed (HTTP %s)" % exc.code
    if isinstance(exc, OSError):
        return 502, "sidecar_unreachable", "the toju sidecar is unreachable"
    return 500, "sidecar_error", "unexpected error talking to the toju sidecar"


class TojuBaseHandler(tornado.web.RequestHandler):
    """JSON responses; requires the same signed ``user`` cookie as the legacy handlers."""

    def get_current_user(self):
        user_json = self.get_secure_cookie("user")
        if not user_json:
            return None
        try:
            user = tornado.escape.json_decode(user_json)
        except ValueError:
            return None
        return user if isinstance(user, str) and user else None

    def fail(self, status, code, message=""):
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.finish({"result": "fail", "error": code, "message": message})

    def sidecar_headers(self, extra=None):
        headers = {"X-Toju-Token": TOJU_SHARED_SECRET}
        headers.update(extra or {})
        return headers

    async def call_sidecar(self, method, path, timeout, body=None, headers=None):
        """Return the HTTPResponse, or None after writing an error response."""
        request = tornado.httpclient.HTTPRequest(
            TOJU_SIDECAR_URL + path, method=method, body=body,
            headers=self.sidecar_headers(headers),
            connect_timeout=CONNECT_TIMEOUT, request_timeout=timeout,
        )
        try:
            return await tornado.httpclient.AsyncHTTPClient().fetch(request)
        except Exception as exc:  # noqa: BLE001 - every failure must map to a JSON error
            status, code, message = _map_sidecar_error(exc)
            logging.error("toju %s %s -> %s %s: %r", method, path, status, code, exc)
            self.fail(status, code, message)
            return None


class TojuSaveHandler(TojuBaseHandler):
    async def post(self):
        if self.get_current_user() is None:
            return self.fail(401, "authentication_required", "log in first")
        content = self.get_argument("content", None, strip=False)   # never strip: this is sheet content
        if not content:
            return self.fail(400, "no_content", "form field 'content' is required")
        if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
            return self.fail(413, "too_large", "content exceeds %d MB" % (MAX_UPLOAD_BYTES // (1024 * 1024)))
        payload = {"sheet": content, "fname": self.get_argument("fname", "sheet.msc")}
        days = self.get_argument("durationDays", "")
        if days:
            try:
                payload["durationDays"] = int(days)
            except ValueError:
                return self.fail(400, "bad_duration", "durationDays must be an integer")
        resp = await self.call_sidecar("POST", "/upload", SAVE_TIMEOUT,
                                       body=json.dumps(payload), headers={"Content-Type": "application/json"})
        if resp is None:
            return
        try:
            data = json.loads(resp.body)
        except ValueError:
            return self.fail(502, "sidecar_bad_response", "the toju sidecar returned invalid JSON")
        self.set_header("Content-Type", "application/json")
        self.finish({"result": "ok", "cid": data.get("cid"), "url": data.get("url"),
                     "signature": data.get("signature"), "estimate": data.get("estimate"),
                     "mocked": bool(data.get("mocked"))})


class TojuRetrieveHandler(TojuBaseHandler):
    async def get(self, cid):
        if self.get_current_user() is None:
            return self.fail(401, "authentication_required", "log in first")
        if not CID_RE.match(cid or ""):
            return self.fail(400, "bad_cid", "invalid CID")
        resp = await self.call_sidecar("GET", "/retrieve/" + tornado.escape.url_escape(cid), RETRIEVE_TIMEOUT)
        if resp is None:
            return
        # Stream the stored bytes through unchanged (byte-identical round trip).
        self.set_header("Content-Type", resp.headers.get("Content-Type", "application/octet-stream"))
        self.set_header("X-Content-Type-Options", "nosniff")
        self.finish(resp.body)


class TojuStatusHandler(TojuBaseHandler):
    async def get(self, cid):
        if self.get_current_user() is None:
            return self.fail(401, "authentication_required", "log in first")
        if not CID_RE.match(cid or ""):
            return self.fail(400, "bad_cid", "invalid CID")
        resp = await self.call_sidecar("GET", "/status/" + tornado.escape.url_escape(cid), STATUS_TIMEOUT)
        if resp is None:
            return
        self.set_header("Content-Type", "application/json")
        self.finish(resp.body)


class TojuHealthHandler(TojuBaseHandler):
    async def get(self):
        resp = await self.call_sidecar("GET", "/health", HEALTH_TIMEOUT)
        if resp is None:
            return
        self.set_header("Content-Type", "application/json")
        self.finish({"status": "ok", "sidecar": json.loads(resp.body)})


ROUTES = [
    (r"/toju/save", TojuSaveHandler),
    (r"/toju/retrieve/(.+)", TojuRetrieveHandler),
    (r"/toju/status/(.+)", TojuStatusHandler),
    (r"/toju/health", TojuHealthHandler),
]
