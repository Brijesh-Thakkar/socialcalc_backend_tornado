"""Authenticated endpoints for opt-in 0G spreadsheet archives."""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone

import tornado.escape
import tornado.httpclient
import tornado.web

import cloud.storage.storage

logger = logging.getLogger(__name__)
_ROOT_HASH = re.compile(r"^0x[a-fA-F0-9]{64}$")
_MAX_ARCHIVE_BYTES = 1024 * 1024
_MAX_ARCHIVES_PER_USER = 500


def _metadata_key(user):
    user_digest = hashlib.sha256(str(user).encode("utf-8")).hexdigest()
    return f"0g-archives/{user_digest}.json"


def _load_archives(user):
    raw = cloud.storage.storage.getItem(_metadata_key(user))
    if not raw:
        return []
    try:
        parsed = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        archives = parsed.get("archives", []) if isinstance(parsed, dict) else []
        if not isinstance(archives, list):
            return []
        return [item for item in archives if isinstance(item, dict)]
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        logger.exception("Invalid 0G archive metadata for user digest")
        return []


def _save_archives(user, archives):
    encoded = json.dumps({"archives": archives[-_MAX_ARCHIVES_PER_USER:]})
    if not cloud.storage.storage.putItem(_metadata_key(user), encoded):
        raise OSError("Could not save archive metadata")


class AuthenticatedHandler(tornado.web.RequestHandler):
    """Shared secure-cookie authentication used by the application handlers."""

    def get_current_user(self):
        user_json = self.get_secure_cookie("user")
        return tornado.escape.json_decode(user_json) if user_json else None

    def _require_user(self):
        user = self.get_current_user()
        if not user:
            self.set_status(401)
            self.finish({"error": "Login required"})
            return None
        return user

    def _mode(self):
        mode = os.environ.get("ZG_MODE", "").strip().lower()
        if mode not in ("mock", "live"):
            self.set_status(503)
            self.finish({"error": "ZG_MODE must be explicitly set to mock or live"})
            return None
        return mode

    def _sidecar_error_detail(self, exc, fallback):
        detail = fallback
        response = getattr(exc, "response", None)
        if response is not None and response.body:
            try:
                payload = json.loads(response.body.decode("utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("error"), str):
                    detail = payload["error"]
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        private_key = os.environ.get("ZG_PRIVATE_KEY", "")
        if private_key:
            detail = detail.replace(private_key, "[redacted]")
        return detail[:500]

    async def _sidecar(self, path, method="GET", body=b""):
        base_url = os.environ.get("ZG_SIDECAR_URL", "http://localhost:5053").rstrip("/")
        timeout = float(os.environ.get("ZG_HTTP_TIMEOUT_SECONDS", "120"))
        request = tornado.httpclient.HTTPRequest(
            f"{base_url}{path}",
            method=method,
            body=body,
            headers={"Content-Type": "application/octet-stream"} if method == "POST" else None,
            request_timeout=timeout,
            connect_timeout=min(timeout, 5.0),
        )
        return await tornado.httpclient.AsyncHTTPClient().fetch(request)


class ZGArchiveHandler(AuthenticatedHandler):
    """POST creates an encrypted archive; GET lists this user's archives."""

    def prepare(self):
        content_length = self.request.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > _MAX_ARCHIVE_BYTES:
                    self.set_status(413)
                    self.finish({"error": "Archive payload is too large"})
            except ValueError:
                self.set_status(400)
                self.finish({"error": "Invalid Content-Length"})

    async def post(self):
        user = self._require_user()
        if user is None:
            return
        mode = self._mode()
        if mode is None:
            return
        if len(self.request.body) > _MAX_ARCHIVE_BYTES:
            self.set_status(413)
            self.finish({"error": "Archive payload is too large"})
            return
        try:
            payload = json.loads(self.request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.set_status(400)
            self.finish({"error": "Expected a JSON object containing fname and data"})
            return
        if not isinstance(payload, dict):
            self.set_status(400)
            self.finish({"error": "Expected a JSON object containing fname and data"})
            return
        fname, data = payload.get("fname"), payload.get("data")
        if (not isinstance(fname, str) or not fname or len(fname) > 255
                or fname in (".", "..") or "/" in fname or "\\" in fname):
            self.set_status(400)
            self.finish({"error": "Invalid sheet name"})
            return
        if not isinstance(data, str):
            self.set_status(400)
            self.finish({"error": "Sheet data must be a string"})
            return
        data_bytes = data.encode("utf-8")
        if len(data_bytes) > _MAX_ARCHIVE_BYTES:
            self.set_status(413)
            self.finish({"error": "Archive payload is too large"})
            return

        # Never accept a client-supplied sheet name unless it exists under this user.
        stored_sheet = cloud.storage.storage.getFile(["home", user, fname])
        if stored_sheet is None:
            self.set_status(404)
            self.finish({"error": "Sheet not found"})
            return
        try:
            response = await self._sidecar("/archive", method="POST", body=data_bytes)
            result = json.loads(response.body.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError("0G sidecar returned invalid archive metadata")
            root_hash, tx_hash = result.get("rootHash"), result.get("txHash")
            if not _ROOT_HASH.fullmatch(root_hash or "") or not isinstance(tx_hash, str):
                raise ValueError("0G sidecar returned invalid archive metadata")
            record = {
                "fname": fname,
                "rootHash": root_hash,
                "txHash": tx_hash,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "mock": bool(result.get("mock", mode == "mock")),
            }
            archives = _load_archives(user)
            archives.append(record)
            _save_archives(user, archives)
            self.finish({"rootHash": root_hash, "txHash": tx_hash, "mock": record["mock"]})
        except tornado.httpclient.HTTPError as exc:
            if exc.code == 599:
                self.set_status(503)
                self.finish({"error": "0G sidecar is unavailable"})
            else:
                logger.warning("0G archive request failed with HTTP %s", exc.code)
                self.set_status(502)
                self.finish({"error": self._sidecar_error_detail(exc, "0G archive failed")})
        except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("0G archive could not be recorded: %s", exc)
            self.set_status(502)
            self.finish({"error": "0G archive failed"})

    def get(self):
        user = self._require_user()
        if user is None:
            return
        mode = self._mode()
        if mode is None:
            return
        archives = _load_archives(user)
        self.finish({"archives": archives, "mock": mode == "mock"})


class ZGDownloadHandler(AuthenticatedHandler):
    async def get(self, root_hash):
        user = self._require_user()
        if user is None:
            return
        if not _ROOT_HASH.fullmatch(root_hash):
            self.set_status(400)
            self.finish({"error": "Invalid root hash"})
            return
        mode = self._mode()
        if mode is None:
            return
        if not any(item.get("rootHash") == root_hash for item in _load_archives(user)):
            self.set_status(404)
            self.finish({"error": "Archive not found"})
            return
        try:
            response = await self._sidecar(f"/download/{root_hash}")
            self.set_header("Content-Type", "application/octet-stream")
            self.set_header("X-ZG-Mock", response.headers.get("X-ZG-Mock", "true" if mode == "mock" else "false"))
            self.finish(response.body)
        except tornado.httpclient.HTTPError as exc:
            if exc.code == 599:
                self.set_status(503)
                self.finish({"error": "0G sidecar is unavailable"})
            else:
                logger.warning("0G download request failed with HTTP %s", exc.code)
                self.set_status(502)
                self.finish({"error": self._sidecar_error_detail(exc, "0G download or proof verification failed")})
