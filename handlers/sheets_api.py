"""
JSON Sheets API (/api/*)

A small JSON facade over the same storage layout and the same signed ``user``
cookie that the legacy HTML handlers use, so a sheet saved here shows up in the
legacy UI and vice versa:

    sheet  -> cloud.storage.storage path ["home", <user>, <name>]
    auth   -> cloud.authenticate.user.authenticate_user + secure cookie "user"

Register with ``*sheets_api.ROUTES`` in BOTH cloudmain-dev.py and cloudmain.py.

Notes
-----
* The legacy handlers live inside the (hyphenated, script-style) main modules
  and cannot be imported, so the 3-line "read the signed user cookie" logic is
  repeated in ``ApiBaseHandler.get_current_user``; everything else (login
  verification, storage reads/writes) calls the shared helpers.
* Storage calls are synchronous, like the legacy handlers.
"""

import json
import logging
import os
from datetime import timezone

import tornado.escape
import tornado.web

import cloud.authenticate.user
import cloud.storage.storage as storage

MAX_NAME_LENGTH = 100
MAX_DATA_BYTES = 5 * 1024 * 1024  # 5 MB; real saves are ~34 KB

# Names that would collide with non-sheet entries under home/<user>/.
RESERVED_NAMES = {"securestore"}

# Characters rejected on top of the path-traversal rules: the legacy list page
# puts the name inside onclick="doedit('...')", so a quote would break out of
# that JS string.
_UNSAFE_CHARS = set("'\"<>")


class StorageError(Exception):
    """Storage backend failed (as opposed to 'not found')."""


# ── validation ───────────────────────────────────────────────────────────────

def validate_name(name):
    """Return None if `name` is acceptable, else a human-readable reason."""
    if not isinstance(name, str) or name == "":
        return "name must not be empty"
    if len(name) > MAX_NAME_LENGTH:
        return "name must be at most %d characters" % MAX_NAME_LENGTH
    if "/" in name or "\\" in name:
        return "name must not contain '/' or '\\'"
    if ".." in name:
        return "name must not contain '..'"
    if name.startswith("."):
        return "name must not start with '.'"
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return "name must not contain control characters"
    if any(c in _UNSAFE_CHARS for c in name):
        return "name must not contain quotes or angle brackets"
    if name.lower() in RESERVED_NAMES:
        return "name is reserved"
    return None


def _env_flag(key, default=False):
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _allowed_origins():
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def _cookie_attrs():
    samesite = os.environ.get("COOKIE_SAMESITE", "Lax").strip().capitalize()
    if samesite not in ("Lax", "Strict", "None"):
        logging.warning("COOKIE_SAMESITE=%r invalid, using Lax", samesite)
        samesite = "Lax"
    secure = _env_flag("COOKIE_SECURE", False)
    if samesite == "None" and not secure:
        logging.warning("COOKIE_SAMESITE=None requires COOKIE_SECURE=true; browsers will drop the cookie")
    return dict(samesite=samesite, secure=secure)


# ── storage helpers (thin wrappers over cloud.storage.storage) ───────────────

def _sheet_path(user, name):
    return ["home", user, name]


def _read_raw(path):
    """Like storage.getFileRaw, but a backend failure raises StorageError
    instead of being reported as 'not found'."""
    try:
        bucket = storage.getBucket(storage.AspiringStorageBucket)
        body = bucket.Object(storage.pathToString(path)).get()["Body"].read()
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return None
        raise StorageError(str(e))
    return json.loads(body)


def _head(path):
    """(size, modified) of the stored object, or None if it vanished."""
    try:
        obj = storage.getBucket(storage.AspiringStorageBucket).Object(storage.pathToString(path))
        obj.load()
        return obj.content_length, obj.last_modified
    except Exception as e:
        code = getattr(e, "response", {}).get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return None
        raise StorageError(str(e))


def list_sheets(user):
    """[{name, size, modified}] for home/<user>/. Never creates directories."""
    dirraw = _read_raw(["home", user])
    if not dirraw or dirraw.get("type") != "dir":
        return []
    out = []
    for name in sorted(json.loads(dirraw["data"])):
        if validate_name(name) is not None:
            continue  # listed by legacy code but not addressable via this API
        head = _head(_sheet_path(user, name))
        if head is None:
            continue
        size, modified = head
        out.append({
            "name": name,
            "size": size,
            "modified": modified.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return out


def read_sheet(user, name):
    """Raw save string, or None if there is no such sheet."""
    raw = _read_raw(_sheet_path(user, name))
    if not raw or raw.get("type") != "file":
        return None
    return raw["data"]


def write_sheet(user, name, data):
    """Create or update. Returns "created"/"updated"; raises StorageError on
    any failure (never reports success it did not achieve)."""
    # The user dir is created on first write only (never on reads).
    if _read_raw(["home", user]) is None:
        if _read_raw(["home"]) is None and not storage.createDir(["home"]):
            raise StorageError("could not create /home")
        if not storage.createDir(["home", user]):
            raise StorageError("could not create user directory")
    path = _sheet_path(user, name)
    existing = _read_raw(path)
    if existing is None:
        if not storage.createFile(path, data):
            raise StorageError("createFile failed")
        return "created"
    if existing.get("type") != "file":
        raise ValueError("a directory with that name exists")
    if not storage.updateFile(path, data):
        raise StorageError("updateFile failed")
    return "updated"


def delete_sheet(user, name):
    """True if deleted, False if there was no such sheet."""
    raw = _read_raw(_sheet_path(user, name))
    if not raw or raw.get("type") != "file":
        return False
    try:
        ok = storage.deleteFile(_sheet_path(user, name))
    except Exception as e:
        raise StorageError(str(e))
    if not ok:
        raise StorageError("deleteFile failed")
    return True


# ── handlers ─────────────────────────────────────────────────────────────────

class CorsMixin:
    """CORS for /api/* only. Exact-origin echo + credentials, never '*'."""

    def set_default_headers(self):
        # Also re-run by Tornado when an error resets headers, so error
        # responses carry CORS headers too.
        self.set_header("Vary", "Origin")
        origin = (self.request.headers.get("Origin") or "").rstrip("/")
        if origin and origin in _allowed_origins():
            self.set_header("Access-Control-Allow-Origin", origin)
            self.set_header("Access-Control-Allow-Credentials", "true")
            self.set_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.set_header("Access-Control-Allow-Headers", "Content-Type")
            self.set_header("Access-Control-Max-Age", "600")

    def options(self, *args, **kwargs):
        self.set_status(204)
        self.finish()


class ApiBaseHandler(CorsMixin, tornado.web.RequestHandler):
    def get_current_user(self):
        # same cookie, same encoding as the legacy BaseHandler
        user_json = self.get_secure_cookie("user")
        if not user_json:
            return None
        try:
            user = tornado.escape.json_decode(user_json)
        except ValueError:
            return None
        return user if isinstance(user, str) and user else None

    def send_error_json(self, status, message):
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.finish({"error": message})

    def write_error(self, status_code, **kwargs):
        self.set_header("Content-Type", "application/json")
        if "exc_info" in kwargs and status_code == 500:
            logging.error("api error", exc_info=kwargs["exc_info"])
        self.finish({"error": self._reason})

    def require_user(self):
        """The logged-in user, or None after sending 401."""
        user = self.get_current_user()
        if user is None:
            self.send_error_json(401, "authentication required")
        return user

    def require_json(self):
        """Mutating routes only accept application/json (forces a CORS
        preflight, which blocks cross-site form CSRF while xsrf is off)."""
        ctype = self.request.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self.send_error_json(415, "Content-Type must be application/json")
            return False
        return True

    def json_body(self):
        """Parsed JSON object, or None after sending an error."""
        if len(self.request.body) > MAX_DATA_BYTES + 4096:
            self.send_error_json(413, "payload too large")
            return None
        try:
            body = json.loads(self.request.body)
        except ValueError:
            self.send_error_json(400, "invalid JSON")
            return None
        if not isinstance(body, dict):
            self.send_error_json(400, "JSON body must be an object")
            return None
        return body


class ApiLoginHandler(ApiBaseHandler):
    def post(self):
        if not self.require_json():
            return
        body = self.json_body()
        if body is None:
            return
        email, password = body.get("email"), body.get("password")
        if not isinstance(email, str) or not isinstance(password, str) or not email:
            return self.send_error_json(400, "email and password are required")
        # same verification as the legacy /login
        if not cloud.authenticate.user.authenticate_user(email, password):
            return self.send_error_json(401, "invalid credentials")
        # same cookie as the legacy /login; attributes only differ via env
        self.set_secure_cookie("user", tornado.escape.json_encode(email), **_cookie_attrs())
        self.set_header("Cache-Control", "no-store")
        self.finish({"ok": True, "user": email})


class ApiLogoutHandler(ApiBaseHandler):
    def post(self):
        if not self.require_json():
            return
        self.clear_cookie("user", **_cookie_attrs())
        self.finish({"ok": True})


class SheetListHandler(ApiBaseHandler):
    def get(self):
        user = self.require_user()
        if user is None:
            return
        try:
            sheets = list_sheets(user)
        except StorageError as e:
            logging.error("list failed: %s", e)
            return self.send_error_json(500, "storage error")
        self.set_header("Cache-Control", "no-store")
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps(sheets))  # a bare JSON array, as specified


class SheetHandler(ApiBaseHandler):
    def _name(self, name):
        reason = validate_name(name)
        if reason:
            self.send_error_json(400, reason)
            return None
        return name

    def get(self, name):
        user = self.require_user()
        if user is None or self._name(name) is None:
            return
        try:
            data = read_sheet(user, name)
        except StorageError as e:
            logging.error("read failed: %s", e)
            return self.send_error_json(500, "storage error")
        if data is None:
            return self.send_error_json(404, "sheet not found")
        self.set_header("Cache-Control", "no-store")
        self.finish({"name": name, "data": data})

    def put(self, name):
        user = self.require_user()
        if user is None or not self.require_json() or self._name(name) is None:
            return
        body = self.json_body()
        if body is None:
            return
        data = body.get("data")
        if not isinstance(data, str):
            return self.send_error_json(400, "'data' (string) is required")
        if len(data.encode("utf-8")) > MAX_DATA_BYTES:
            return self.send_error_json(413, "payload too large")
        # TODO(mentor): legacy /save has no save quota, but /webapp savefile
        # enforces a 5-save "buy" quota. This endpoint mirrors /save (no quota).
        # Decide whether the new API should enforce the /webapp quota.
        try:
            result = write_sheet(user, name, data)
        except ValueError as e:
            return self.send_error_json(409, str(e))
        except StorageError as e:
            logging.error("write failed: %s", e)
            return self.send_error_json(500, "storage write failed")
        self.set_status(201 if result == "created" else 200)
        self.finish({"ok": True, "name": name})

    def delete(self, name):
        user = self.require_user()
        if user is None or not self.require_json_if_body() or self._name(name) is None:
            return
        try:
            deleted = delete_sheet(user, name)
        except StorageError as e:
            logging.error("delete failed: %s", e)
            return self.send_error_json(500, "storage delete failed")
        if not deleted:
            return self.send_error_json(404, "sheet not found")
        self.set_status(204)
        self.finish()

    def require_json_if_body(self):
        # DELETE has no body, but the spec rejects any non-JSON Content-Type on
        # mutating routes; a browser fetch() DELETE with no Content-Type is
        # already preflighted (DELETE is not a CORS-safelisted method).
        if self.request.headers.get("Content-Type") is None and not self.request.body:
            return True
        return self.require_json()


ROUTES = [
    (r"/api/login", ApiLoginHandler),
    (r"/api/logout", ApiLogoutHandler),
    (r"/api/sheets/?", SheetListHandler),
    (r"/api/sheets/(.+)", SheetHandler),
]
