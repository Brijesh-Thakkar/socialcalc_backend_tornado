"""Node.js interop via the ``node-interop`` sidecar.

Tornado never imports Node code: it calls the sidecar over HTTP with ``AsyncHTTPClient``,
exactly like ``handlers/interop.py`` does for the FastAPI sidecar, and reuses its error mapping.

Routes (registered in cloudmain.py AND cloudmain-dev.py with ``*node_interop.ROUTES``):

    GET /nodeinterop/health      sidecar health (requires a Tornado login)

Error mapping (shared with handlers/interop.py):
    sidecar unreachable (599, connection refused, DNS)  -> 502
    request/connect timeout                              -> 504
    sidecar 4xx                                          -> passed through
    anything else                                        -> 500
"""

import json
import logging
import os

import tornado.httpclient

from handlers.interop import InteropAuthedHandler, _map_sidecar_error, _new_request_id

NODE_INTEROP_URL = os.getenv("NODE_INTEROP_URL", "http://localhost:5055").rstrip("/")
NODE_CONNECT_TIMEOUT = float(os.getenv("NODE_INTEROP_CONNECT_TIMEOUT", "5"))
NODE_HEALTH_TIMEOUT = float(os.getenv("NODE_INTEROP_HEALTH_TIMEOUT", "5"))


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
            logging.error("node-interop %s %s [%s] -> %s %s: %r", method, path, request_id, status, code, exc)
            self.fail(status, code, message)
            return None


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
]
