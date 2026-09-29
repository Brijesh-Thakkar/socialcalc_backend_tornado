"""Authenticated routes for the separate modern SocialCalc client."""

import tornado.escape
import tornado.web

import cloud.storage.storage
from handlers.zg_storage import AuthenticatedHandler

MAX_MODERN_SHEET_BYTES = 4 * 1024 * 1024


class ModernSheetDataHandler(AuthenticatedHandler):
    """Return one of the signed-in user's saved workbook strings as JSON."""

    def get(self):
        user = self.get_current_user()
        if not user:
            self.set_status(401)
            self.finish({"error": "Login required"})
            return

        fname = self.get_argument("fname", "").strip()
        if (not fname or fname in (".", "..") or "/" in fname or "\\" in fname
                or any(ord(char) < 32 for char in fname) or len(fname.encode("utf-8")) > 255):
            self.set_status(400)
            self.finish({"error": "Invalid sheet name"})
            return

        # Always scope the lookup to the authenticated identity, following the
        # same storage path convention used by SaveHandler and UserSheetHandler.
        fileobj = cloud.storage.storage.getFile(["home", user, fname])
        if fileobj is None or not isinstance(fileobj.data, (str, bytes)):
            self.set_status(404)
            self.finish({"error": "Sheet not found"})
            return

        try:
            sheet_data = fileobj.data.decode("utf-8") if isinstance(fileobj.data, bytes) else fileobj.data
            size = len(sheet_data.encode("utf-8"))
        except UnicodeDecodeError:
            self.set_status(422)
            self.finish({"error": "Sheet data is not valid UTF-8"})
            return
        if size > MAX_MODERN_SHEET_BYTES:
            self.set_status(413)
            self.finish({"error": "Sheet is too large for the modern editor"})
            return

        self.finish({"fname": fname, "data": sheet_data})


class ModernStaticFileHandler(tornado.web.StaticFileHandler):
    """Serve the built client only to users with the existing secure cookie."""

    def get_current_user(self):
        user_json = self.get_secure_cookie("user")
        return tornado.escape.json_decode(user_json) if user_json else None

    def prepare(self):
        if not self.get_current_user():
            self.redirect("/login")
            return
        super().prepare()
