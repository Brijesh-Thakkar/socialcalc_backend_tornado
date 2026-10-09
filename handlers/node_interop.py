"""Node.js interop via the ``node-interop`` sidecar.

Tornado never imports Node code: it calls the sidecar over HTTP with ``AsyncHTTPClient``,
exactly like ``handlers/interop.py`` does for the FastAPI sidecar, and reuses its error mapping.

Routes (registered in cloudmain.py AND cloudmain-dev.py with ``*node_interop.ROUTES``):

    GET  /nodeinterop/health     sidecar health (requires a Tornado login)
    POST /nodeinterop/publish    form ``name``                      copy the user's sheet to Node        -> JSON {name, size}
    POST /nodeinterop/open       form ``name`` [``as``, ``overwrite=yes``]  copy it back from Node into S3  -> JSON {name, size}

publish/open are copy-by-name through S3: Tornado reads/writes the user's sheet in its own storage and
sends/fetches the save string to/from the sidecar's ``/v1/sheets`` API, authenticated with the shared
secret (``X-Interop-Token``) which only these handlers send, after the Tornado cookie login.
TODO(D10/D11): content addressing (CID), libp2p replication and Storacha persistence are out of scope
until the scope decision and Storacha credentials exist.

Error mapping (shared with handlers/interop.py):
    sidecar unreachable (599, connection refused, DNS)  -> 502
    request/connect timeout                              -> 504
    sidecar 4xx                                          -> passed through
    anything else                                        -> 500
"""

import json
import logging
import os

import urllib.parse

import tornado.escape
import tornado.httpclient

import cloud.storage.storage
from handlers.interop import InteropAuthedHandler, _map_sidecar_error, _new_request_id

NODE_INTEROP_URL = os.getenv("NODE_INTEROP_URL", "http://localhost:5055").rstrip("/")
NODE_INTEROP_SHARED_SECRET = os.getenv("NODE_INTEROP_SHARED_SECRET", "")
NODE_CONNECT_TIMEOUT = float(os.getenv("NODE_INTEROP_CONNECT_TIMEOUT", "5"))
NODE_HEALTH_TIMEOUT = float(os.getenv("NODE_INTEROP_HEALTH_TIMEOUT", "5"))
NODE_REQUEST_TIMEOUT = float(os.getenv("NODE_INTEROP_REQUEST_TIMEOUT", "30"))


class NodeInteropBaseHandler(InteropAuthedHandler):
    async def call_node(self, method, path, request_id, timeout, body=None, headers=None):
        """Return the HTTPResponse or None after writing an error response."""
        request = tornado.httpclient.HTTPRequest(
            NODE_INTEROP_URL + path, method=method, body=body,
            headers=dict({"X-Request-Id": request_id}, **(headers or {})),
            connect_timeout=NODE_CONNECT_TIMEOUT, request_timeout=timeout,
        )
        try:
            return await tornado.httpclient.AsyncHTTPClient().fetch(request)
        except Exception as exc:  # noqa: BLE001 - every failure must map to a JSON error
            status, code, message = _map_sidecar_error(exc)
            if getattr(exc, "code", None) in (401, 403, 503):
                # The caller is logged in to Tornado; a rejected service token is our misconfiguration, not theirs.
                status, code, message = 502, "sidecar_auth_failed", "the node service rejected the interop credentials"
            logging.error("node-interop %s %s [%s] -> %s %s: %r", method, path, request_id, status, code, exc)
            self.fail(status, code, message)
            return None


class NodeSheetHandler(NodeInteropBaseHandler):
    """Base for publish/open: needs a login (prepare) and the shared secret."""

    def prepare(self):
        super().prepare()
        if self._finished:
            return
        if not NODE_INTEROP_SHARED_SECRET:
            self.fail(503, "interop_not_configured", "NODE_INTEROP_SHARED_SECRET is not set")

    def token_headers(self, extra=None):
        # Only authenticated handlers reach this point; the token is never logged or returned.
        return dict({"X-Interop-Token": NODE_INTEROP_SHARED_SECRET}, **(extra or {}))


class NodePublishHandler(NodeSheetHandler):
    async def post(self):
        name = self.get_argument("name", "")
        if not name:
            return self.fail(400, "no_name", "form field 'name' is required")
        user = self.get_current_user()
        sheet = cloud.storage.storage.getFile(["home", user, name])
        if sheet is None or not hasattr(sheet, "data"):
            return self.fail(404, "not_found", "no such sheet")
        payload = json.dumps({"name": name, "owner": user, "savestr": sheet.data})
        resp = await self.call_node("POST", "/v1/sheets", _new_request_id(), NODE_REQUEST_TIMEOUT, body=payload,
                                    headers=self.token_headers({"Content-Type": "application/json"}))
        if resp is None:
            return
        self.set_header("Content-Type", "application/json")
        self.finish({"result": "ok", "name": name, "size": json.loads(resp.body).get("size")})


class NodeOpenHandler(NodeSheetHandler):
    async def post(self):
        name = self.get_argument("name", "")
        if not name:
            return self.fail(400, "no_name", "form field 'name' is required")
        target = self.get_argument("as", "") or name
        user = self.get_current_user()
        path = ["home", user, target]
        existing = cloud.storage.storage.getFile(path)
        if existing is not None and self.get_argument("overwrite", "") != "yes":
            return self.fail(409, "exists", "a sheet named %r already exists; pass overwrite=yes or as=<other name>" % target)
        resp = await self.call_node("GET", "/v1/sheets/%s?owner=%s" % (urllib.parse.quote(name, safe=""), urllib.parse.quote(user, safe="")),
                                    _new_request_id(), NODE_REQUEST_TIMEOUT, headers=self.token_headers())
        if resp is None:
            return
        try:
            savestr = json.loads(resp.body)["savestr"]
        except (ValueError, KeyError, TypeError):
            return self.fail(502, "sidecar_bad_response", "the node service returned an invalid response")
        # Write to S3 before answering: the next request may be served by another app container.
        storage = cloud.storage.storage
        if existing is not None:
            ok = storage.updateFile(path, savestr)
        else:
            if not storage.getFile(["home", user]):
                storage.createDir(["home", user])
            ok = storage.createFile(path, savestr)
        if not ok:
            logging.error("node-interop open: S3 write of %s failed", path)
            return self.fail(500, "storage_failed", "could not store the sheet")
        self.set_header("Content-Type", "application/json")
        self.finish({"result": "ok", "name": target, "size": len(savestr.encode("utf-8"))})


class NodeInteropHealthHandler(NodeInteropBaseHandler):
    async def get(self):
        resp = await self.call_node("GET", "/health", _new_request_id(), NODE_HEALTH_TIMEOUT)
        if resp is None:
            return
        try:
            sidecar = json.loads(resp.body)
        except ValueError:
            return self.fail(502, "sidecar_bad_response", "the node service returned invalid JSON")
        self.set_header("Content-Type", "application/json")
        self.finish({"status": "ok", "sidecar": sidecar})


ROUTES = [
    (r"/nodeinterop/health", NodeInteropHealthHandler),
    (r"/nodeinterop/publish", NodePublishHandler),
    (r"/nodeinterop/open", NodeOpenHandler),
]
