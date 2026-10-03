"""Omamori x402 firewall integration: a paid SocialCalc sheet export.

Two handlers, both fail-closed:

* ``ExportChallengeHandler`` -- ``GET /x402/sheet/<id>/export`` answers with an
  x402 v2 ``402 Payment Required`` challenge. Settlement is not implemented, so
  a request that carries a payment header gets ``501`` and never any content.
* ``OmamoriSignHandler`` -- ``POST /omamori/sign`` is the only door to the
  internal omamori-firewall sidecar. It validates the agent's request, pins the
  resource URL to our own export path (SSRF guard), forwards only the
  ``Authorization`` header to the firewall's ``/sign`` and maps its verdict to
  an HTTP status. Any error or unexpected shape becomes ``refuse``, never
  ``pay``.

Tornado never imports the firewall's code; it talks to the sidecar over HTTP
with AsyncHTTPClient, like the meshkit/Kubo sidecar handlers.
"""

import base64
import json
import logging
import os
import re
from urllib.parse import urlsplit

import tornado.httpclient
import tornado.ioloop
import tornado.web

try:  # Tornado's own client-side timeout (code 599); see _forward_to_firewall.
    from tornado.simple_httpclient import HTTPTimeoutError
except ImportError:  # pragma: no cover
    HTTPTimeoutError = None

logger = logging.getLogger(__name__)

# Base Sepolia, Circle test USDC -- the only network/asset Omamori pays on.
NETWORK = "eip155:84532"
USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"

# No existing handler validates sheet names, so the export uses its own strict
# allow-list: it is also what keeps the SSRF guard below unambiguous.
SHEET_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
_EXPORT_PATH = re.compile(r"/x402/sheet/(" + SHEET_ID_PATTERN + r")/export")
_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
_ATOMIC_AMOUNT = re.compile(r"[1-9][0-9]{0,30}")

MAX_SIGN_BODY_BYTES = 64 * 1024
MAX_RESOURCE_URL_LEN = 2048
MAX_HEADER_VALUE_LEN = 16 * 1024
PAYMENT_HEADERS = ("PAYMENT-SIGNATURE", "X-PAYMENT")


def _config():
    """Read config per request so tests (and restarts) see env changes."""
    return {
        "firewall_url": os.getenv("OMAMORI_FIREWALL_URL", "http://omamori-firewall:4001").rstrip("/"),
        "timeout_s": float(os.getenv("OMAMORI_TIMEOUT_S", "15")),
        "pay_to": os.getenv("OMAMORI_PAYTO_ADDRESS", "").strip(),
        "seller": os.getenv("OMAMORI_SELLER_USER", "").strip(),
        "price": os.getenv("OMAMORI_EXPORT_PRICE", "10000").strip(),
        "resource_base_url": os.getenv("OMAMORI_RESOURCE_BASE_URL", "http://nginx").rstrip("/"),
        "public_base_url": os.getenv("OMAMORI_PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/"),
    }


def export_path(sheet_id):
    return "/x402/sheet/%s/export" % sheet_id


def build_payment_required(sheet_id, cfg):
    """The x402 v2 PaymentRequired object, in the field order @x402/express emits.

    Depends only on the sheet id and env -- never on the request -- so app1,
    app2, nginx, the firewall's internal self-fetch and a public tunnel all
    produce byte-identical headers.
    """
    return {
        "x402Version": 2,
        "error": "Payment required",
        "resource": {
            "url": cfg["public_base_url"] + export_path(sheet_id),
            "description": "SocialCalc sheet export",
            "mimeType": "",
        },
        "accepts": [{
            "scheme": "exact",
            "network": NETWORK,
            "amount": cfg["price"],
            "asset": USDC_BASE_SEPOLIA,
            "payTo": cfg["pay_to"],
            "maxTimeoutSeconds": 60,
            "extra": {"name": "USDC", "version": "2"},
        }],
        "extensions": {
            "payment-identifier": {
                "info": {"required": False},
                "schema": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {
                        "required": {"type": "boolean"},
                        "id": {"type": "string", "minLength": 16, "maxLength": 128, "pattern": "^[a-zA-Z0-9_-]+$"},
                    },
                    "required": ["required"],
                },
            },
        },
    }


def encode_payment_required(payment_required):
    # Compact separators match JS JSON.stringify, which @x402/core uses.
    raw = json.dumps(payment_required, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _config_problem(cfg):
    if not _EVM_ADDRESS.fullmatch(cfg["pay_to"]):
        return "OMAMORI_PAYTO_ADDRESS unset or not an address"
    if not cfg["seller"]:
        return "OMAMORI_SELLER_USER unset"
    if not _ATOMIC_AMOUNT.fullmatch(cfg["price"]):
        return "OMAMORI_EXPORT_PRICE must be a positive integer string (USDC atomic units)"
    return None


def _storage_sheet_exists(seller, sheet_id):
    import cloud.storage.storage  # imported lazily so unit tests need no S3
    return cloud.storage.storage.getFile(["home", seller, sheet_id]) is not None


def allowed_resource_path(resource_url):
    """SSRF guard: return the export path for ``resource_url`` or None.

    Only the path is ever used -- scheme, host, port, userinfo, query and
    fragment are ignored -- and it must be exactly /x402/sheet/<id>/export in
    its raw (undecoded) form. Any percent-encoding, backslash, dot segment or
    other character is rejected rather than normalised.
    """
    if not isinstance(resource_url, str) or not resource_url or len(resource_url) > MAX_RESOURCE_URL_LEN:
        return None
    if any(c in resource_url for c in "%\\") or any(ord(c) < 0x21 or ord(c) > 0x7e for c in resource_url):
        return None
    try:
        path = urlsplit(resource_url).path
    except ValueError:
        return None
    match = _EXPORT_PATH.fullmatch(path)
    return export_path(match.group(1)) if match else None


class ExportChallengeHandler(tornado.web.RequestHandler):
    """GET /x402/sheet/<id>/export -> 402 challenge (or 404/501/503)."""

    def initialize(self, sheet_exists=None):
        self._sheet_exists = sheet_exists or _storage_sheet_exists

    def check_xsrf_cookie(self):
        pass

    def _json(self, status, body):
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.set_header("Cache-Control", "no-store")
        self.finish(json.dumps(body))

    async def get(self, sheet_id):
        cfg = _config()
        problem = _config_problem(cfg)
        if problem:
            logger.error("omamori export disabled: %s", problem)
            return self._json(503, {"error": "omamori_not_configured"})
        try:
            exists = await tornado.ioloop.IOLoop.current().run_in_executor(
                None, self._sheet_exists, cfg["seller"], sheet_id)
        except Exception as exc:  # storage down -> no challenge, no content
            logger.error("omamori export: sheet lookup failed: %s", exc)
            return self._json(503, {"error": "storage_unavailable"})
        if not exists:
            return self._json(404, {"error": "sheet_not_found"})
        if any(self.request.headers.get(h) for h in PAYMENT_HEADERS):
            return self._json(501, {"error": "settlement_disabled",
                                    "message": "x402 settlement is not implemented; no content is served"})
        payment_required = build_payment_required(sheet_id, cfg)
        self.set_header("PAYMENT-REQUIRED", encode_payment_required(payment_required))
        return self._json(402, payment_required)


class OmamoriSignHandler(tornado.web.RequestHandler):
    """POST /omamori/sign -> forwards to the internal firewall's /sign only."""

    def check_xsrf_cookie(self):
        pass

    def _refuse(self, status, reason, **extra):
        body = {"verdict": "refuse", "reason": reason}
        body.update(extra)
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.set_header("Cache-Control", "no-store")
        self.finish(json.dumps(body))

    def _parse_request(self):
        """Returns (forward_body, None) or (None, (status, reason))."""
        if len(self.request.body or b"") > MAX_SIGN_BODY_BYTES:
            return None, (413, "request_too_large")
        content_type = self.request.headers.get("Content-Type", "")
        if content_type.split(";")[0].strip().lower() != "application/json":
            return None, (400, "content_type_must_be_json")
        try:
            body = json.loads(self.request.body or b"")
        except ValueError:
            return None, (400, "invalid_json")
        if not isinstance(body, dict):
            return None, (400, "invalid_json")
        if not self.request.headers.get("Authorization"):
            return None, (401, "missing_authorization")

        path = allowed_resource_path(body.get("resourceUrl"))
        if path is None:
            return None, (400, "resource_not_allowed")

        header = body.get("paymentRequiredHeader")
        if not isinstance(header, str) or not header or len(header) > MAX_HEADER_VALUE_LEN:
            return None, (400, "invalid_payment_required_header")

        forward = {
            "paymentRequiredHeader": header,
            "resourceUrl": _config()["resource_base_url"] + path,
        }
        if "intentId" in body:
            if not isinstance(body["intentId"], str) or not body["intentId"]:
                return None, (400, "invalid_intent_id")
            forward["intentId"] = body["intentId"]
        if "context" in body:
            if not isinstance(body["context"], dict):
                return None, (400, "invalid_context")
            forward["context"] = body["context"]
        if "purchaseRef" in body:
            if not isinstance(body["purchaseRef"], str):
                return None, (400, "invalid_purchase_ref")
            forward["purchaseRef"] = body["purchaseRef"]
        return forward, None

    async def post(self):
        forward, problem = self._parse_request()
        if problem:
            return self._refuse(*problem)

        cfg = _config()
        request = tornado.httpclient.HTTPRequest(
            cfg["firewall_url"] + "/sign",
            method="POST",
            # Only Authorization crosses over -- never the client's other
            # headers (e.g. x-yakusoku-admin), so no admin route is reachable.
            headers={"Authorization": self.request.headers["Authorization"],
                     "Content-Type": "application/json"},
            body=json.dumps(forward),
            request_timeout=cfg["timeout_s"],
            connect_timeout=min(cfg["timeout_s"], 5.0),
            follow_redirects=False,
            user_agent="socialcalc-omamori",
        )
        try:
            response = await tornado.httpclient.AsyncHTTPClient().fetch(request, raise_error=False)
        except Exception as exc:
            if HTTPTimeoutError is not None and isinstance(exc, HTTPTimeoutError):
                logger.error("omamori /sign timed out after %ss", cfg["timeout_s"])
                return self._refuse(504, "firewall_timeout")
            # 599 / connection refused / DNS failure: the sidecar is unreachable.
            logger.error("omamori /sign unreachable: %s", type(exc).__name__)
            return self._refuse(502, "firewall_unreachable")
        self._map_response(response)

    def _map_response(self, response):
        if response.code == 599:
            if HTTPTimeoutError is not None and isinstance(response.error, HTTPTimeoutError):
                return self._refuse(504, "firewall_timeout")
            return self._refuse(502, "firewall_unreachable")
        try:
            data = json.loads(response.body or b"")
        except ValueError:
            data = None

        if response.code in (400, 401, 403):
            error = data.get("error") if isinstance(data, dict) else None
            reason = error if isinstance(error, str) and error else "firewall_rejected_request"
            logger.info("omamori /sign rejected: status=%s reason=%s", response.code, reason[:200])
            return self._refuse(response.code, reason[:200])
        if response.code != 200 or not isinstance(data, dict):
            logger.error("omamori /sign unexpected response: status=%s", response.code)
            return self._refuse(502, "firewall_error")

        verdict, reason, receipt_id = data.get("verdict"), data.get("reason"), data.get("receiptId")
        if not isinstance(reason, str) or not isinstance(receipt_id, str):
            return self._refuse(502, "malformed_firewall_response")
        # Never log the signature or anything the agent authenticated with.
        logger.info("omamori /sign verdict=%s receiptId=%s reason=%s", verdict, receipt_id, reason[:200])

        if verdict == "pay":
            signature = data.get("paymentSignature")
            if not isinstance(signature, str) or not signature:
                return self._refuse(502, "malformed_firewall_response")
            return self._send(200, {"verdict": "pay", "reason": reason, "receiptId": receipt_id,
                                    "paymentSignature": signature})
        if verdict == "ask_human":
            body = {"verdict": "ask_human", "reason": reason, "receiptId": receipt_id}
            approval = data.get("approval")
            if isinstance(approval, dict):
                body["approval"] = {k: approval[k] for k in ("verificationUri", "userCode", "expiresAt")
                                    if isinstance(approval.get(k), str)}
            return self._send(202, body)
        if verdict == "refuse":
            return self._send(403, {"verdict": "refuse", "reason": reason, "receiptId": receipt_id})
        return self._refuse(502, "malformed_firewall_response")

    def _send(self, status, body):
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.set_header("Cache-Control", "no-store")
        self.finish(json.dumps(body))
