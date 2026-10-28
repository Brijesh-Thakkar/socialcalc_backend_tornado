"""Spreadsheet interop via the ``fastapi-interop`` sidecar.

Tornado never converts spreadsheets itself and never imports the sidecar's code:
it calls it over HTTP with ``AsyncHTTPClient``, like the meshkit sidecars.

Routes (registered in cloudmain.py AND cloudmain-dev.py with ``*interop.ROUTES``):

    POST /interop/import      multipart ``upload`` (xls, xlsx, csv, html)  -> JSON with the SocialCalc save string
    POST /interop/export      form ``type`` + ``content``                  -> JSON {key, url, ...}; the file is already in S3
                              (content = SocialCalc save string; for html/pdf it may also be rendered HTML)
    GET  /interop/export      ?fname=<key>                                 -> the file, streamed from S3
    GET  /interop/health                                                   -> sidecar health

Generated files are uploaded to S3 before the response is sent, because Nginx
balances requests across containers that do not share a filesystem.

Error mapping (same convention as the meshkit sidecar handlers, plus timeouts):
    sidecar unreachable (599, connection refused, DNS)  -> 502
    request/connect timeout                              -> 504
    sidecar 4xx (bad file, bad type, ...)                -> passed through with its error code
    anything else                                        -> 500
"""

import json
import logging
import os
import re
import secrets
import string
import uuid

import tornado.escape
import tornado.httpclient
import tornado.web
from tornado.simple_httpclient import HTTPTimeoutError

import cloud.storage.storage

FASTAPI_INTEROP_URL = os.getenv("FASTAPI_INTEROP_URL", "http://localhost:5053").rstrip("/")
INTEROP_SHARED_SECRET = os.getenv("INTEROP_SHARED_SECRET", "")
EXPORT_BUCKET = os.getenv("INTEROP_EXPORT_BUCKET") or os.getenv("PDF_S3_BUCKET", "aspiring-pdf-files")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
CONNECT_TIMEOUT = float(os.getenv("INTEROP_CONNECT_TIMEOUT", "5"))
HEALTH_TIMEOUT = float(os.getenv("INTEROP_HEALTH_TIMEOUT", "5"))
IMPORT_TIMEOUT = float(os.getenv("INTEROP_IMPORT_TIMEOUT", "60"))
EXPORT_TIMEOUT = float(os.getenv("INTEROP_EXPORT_TIMEOUT", "120"))
MAX_UPLOAD_BYTES = int(float(os.getenv("INTEROP_MAX_UPLOAD_MB", "20")) * 1024 * 1024)

CONTENT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "csv": "text/csv; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "pdf": "application/pdf",
}
EXPORT_TYPES = tuple(CONTENT_TYPES)
HTML_CAPABLE_TYPES = ("html", "pdf")        # ``content`` may be rendered HTML (legacy web UI) or a SocialCalc save string
KEY_RE = re.compile(r"^[A-Z0-9]{24}\.(%s)$" % "|".join(EXPORT_TYPES))
_KEY_ALPHABET = string.ascii_uppercase + string.digits


def _new_request_id():
    return uuid.uuid4().hex[:12]


def _map_sidecar_error(exc):
    """Return (http_status, error_code, message) for an exception raised by AsyncHTTPClient."""
    # Timeouts first: HTTPTimeoutError is an HTTPClientError with code 599 and must become 504, not 502.
    if isinstance(exc, HTTPTimeoutError):
        return 504, "sidecar_timeout", "the interop service did not answer in time"
    if isinstance(exc, tornado.httpclient.HTTPClientError):
        if exc.code == 599:
            return 502, "sidecar_unreachable", "the interop service is unreachable"
        if exc.code == 504:
            return 504, "sidecar_timeout", "the interop service timed out"
        if exc.response is not None and 400 <= exc.code < 500:
            try:
                body = json.loads(exc.response.body)
                return exc.code, str(body.get("error", "sidecar_rejected")), str(body.get("message", ""))[:300]
            except (ValueError, AttributeError):
                return exc.code, "sidecar_rejected", "the interop service rejected the request"
        return 500, "sidecar_error", "the interop service failed (HTTP %s)" % exc.code
    # AsyncHTTPClient re-raises low-level connection failures as-is (ConnectionRefusedError, socket.gaierror ...)
    if isinstance(exc, OSError):
        return 502, "sidecar_unreachable", "the interop service is unreachable"
    return 500, "sidecar_error", "unexpected error talking to the interop service"


def _multipart(field, filename, content, content_type="application/octet-stream"):
    safe = re.sub(r'[\r\n"\\/]+', "_", os.path.basename(filename or "upload"))[:200] or "upload"
    boundary = "----interop" + uuid.uuid4().hex
    head = ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
            % (boundary, field, safe, content_type)).encode("utf-8")
    tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    return "multipart/form-data; boundary=%s" % boundary, head + content + tail


class InteropBaseHandler(tornado.web.RequestHandler):
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

    def sidecar_headers(self, request_id, extra=None):
        headers = {"X-Interop-Token": INTEROP_SHARED_SECRET, "X-Request-Id": request_id}
        headers.update(extra or {})
        return headers

    async def call_sidecar(self, method, path, request_id, timeout, body=None, headers=None):
        """Return the HTTPResponse or None after writing an error response."""
        request = tornado.httpclient.HTTPRequest(
            FASTAPI_INTEROP_URL + path, method=method, body=body,
            headers=self.sidecar_headers(request_id, headers),
            connect_timeout=CONNECT_TIMEOUT, request_timeout=timeout,
        )
        try:
            return await tornado.httpclient.AsyncHTTPClient().fetch(request)
        except Exception as exc:  # noqa: BLE001 - every failure must map to a JSON error
            status, code, message = _map_sidecar_error(exc)
            logging.error("interop %s %s [%s] -> %s %s: %r", method, path, request_id, status, code, exc)
            self.fail(status, code, message)
            return None


class InteropAuthedHandler(InteropBaseHandler):
    def prepare(self):
        if self.get_current_user() is None:
            self.fail(401, "authentication_required", "log in first")


class InteropImportHandler(InteropAuthedHandler):
    async def post(self):
        uploads = self.request.files.get("upload")
        if not uploads:
            return self.fail(400, "no_file", "multipart field 'upload' is required")
        upload = uploads[0]
        if len(upload["body"]) > MAX_UPLOAD_BYTES:
            return self.fail(413, "too_large", "upload exceeds %d MB" % (MAX_UPLOAD_BYTES // (1024 * 1024)))
        request_id = _new_request_id()
        ctype, body = _multipart("file", upload["filename"], upload["body"], upload.get("content_type") or "application/octet-stream")
        ext = os.path.splitext(upload["filename"] or "")[1].lstrip(".").lower()
        resp = await self.call_sidecar("POST", "/v1/import?hint=%s" % tornado.escape.url_escape(ext), request_id,
                                       IMPORT_TIMEOUT, body=body, headers={"Content-Type": ctype})
        if resp is None:
            return
        try:
            data = json.loads(resp.body)
        except ValueError:
            return self.fail(502, "sidecar_bad_response", "the interop service returned invalid JSON")
        self.set_header("Content-Type", "application/json")
        self.finish({"result": "ok", "format": data.get("format"), "sheets": data.get("sheets", []),
                     "savestr": data.get("savestr", ""), "warnings": data.get("warnings", [])})


class InteropExportHandler(InteropBaseHandler):
    async def post(self):
        if self.get_current_user() is None:
            return self.fail(401, "authentication_required", "log in first")
        export_type = self.get_argument("type", "").lower()
        if export_type not in EXPORT_TYPES:
            return self.fail(400, "bad_type", "type must be one of %s" % ", ".join(EXPORT_TYPES))
        content = self.get_argument("content", None, strip=False)     # never strip: this is file content
        if not content:
            return self.fail(400, "no_content", "form field 'content' is required")
        if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
            return self.fail(413, "too_large", "content exceeds %d MB" % (MAX_UPLOAD_BYTES // (1024 * 1024)))
        payload = {"type": export_type}
        # A save string is JSON and starts with "{"; the web UI's rendered HTML never does. html/pdf accept both.
        is_html = export_type in HTML_CAPABLE_TYPES and not content.lstrip().startswith("{")
        payload["html" if is_html else "savestr"] = content
        request_id = _new_request_id()
        resp = await self.call_sidecar("POST", "/v1/export", request_id, EXPORT_TIMEOUT,
                                       body=json.dumps(payload), headers={"Content-Type": "application/json"})
        if resp is None:
            return
        # Upload to S3 BEFORE answering: the next request may be served by another app container.
        key = "".join(secrets.choice(_KEY_ALPHABET) for _ in range(24)) + "." + export_type
        if not cloud.storage.storage.putItem(key, resp.body, EXPORT_BUCKET):
            logging.error("interop export [%s]: S3 upload of %s failed", request_id, key)
            return self.fail(500, "storage_failed", "could not store the generated file")
        base = PUBLIC_BASE_URL if PUBLIC_BASE_URL else "%s://%s" % (self.request.protocol, self.request.host)
        self.set_header("Content-Type", "application/json")
        self.finish({"result": "ok", "key": key, "url": "%s/interop/export?fname=%s" % (base, key),
                     "content_type": CONTENT_TYPES[export_type], "size": len(resp.body)})

    def get(self):
        # The key is a 24-character random token (a capability URL), like /htmltopdf?fname=
        fname = self.get_argument("fname", "")
        if not KEY_RE.match(fname):
            return self.fail(400, "bad_key", "invalid file key")
        data = cloud.storage.storage.getItem(fname, EXPORT_BUCKET)
        if data is None:
            return self.fail(404, "not_found", "no such file")
        ext = fname.rsplit(".", 1)[1]
        name = re.sub(r'[^A-Za-z0-9._ -]+', "_", self.get_argument("name", "export"))[:80] or "export"
        self.set_header("Content-Type", CONTENT_TYPES[ext])
        self.set_header("Content-Disposition", 'attachment; filename="%s.%s"' % (name, ext))
        self.set_header("Cache-Control", "private, max-age=0")
        self.set_header("X-Content-Type-Options", "nosniff")
        self.finish(data)


class InteropHealthHandler(InteropBaseHandler):
    async def get(self):
        resp = await self.call_sidecar("GET", "/health", _new_request_id(), HEALTH_TIMEOUT)
        if resp is None:
            return
        self.set_header("Content-Type", "application/json")
        self.finish({"status": "ok", "sidecar": json.loads(resp.body)})


ROUTES = [
    (r"/interop/import", InteropImportHandler),
    (r"/interop/export", InteropExportHandler),
    (r"/interop/health", InteropHealthHandler),
]
