"""CORS for the endpoints the Android/Capacitor app (and a local dev server) call.

Mixed into ``WebAppHandler`` and ``HtmlToPdfHandler`` in cloudmain.py AND cloudmain-dev.py.

Origins come from ``ALLOWED_ORIGINS`` (comma separated, exact match). Unset, it defaults
to the Capacitor WebView origins. A request whose ``Origin`` is not listed gets no CORS
headers at all (and a 403 on preflight), so the browser blocks it.

Rules:
  * the allowed origin is echoed back (never ``*`` together with credentials) and
    ``Vary: Origin`` is set so caches do not mix origins;
  * ``Access-Control-Allow-Credentials: true`` is only sent when the handler sets
    ``cors_allow_credentials`` (``/webapp``, which uses the signed session cookie);
  * ``/htmltopdf`` reads ``HTMLTOPDF_ALLOWED_ORIGINS`` instead, defaulting to ``*`` (it used to
    send a blanket ``Access-Control-Allow-Origin: *``, so existing clients keep working);
  * an explicit ``*`` entry in ``ALLOWED_ORIGINS`` answers ``*`` and is never combined
    with credentials, even on a handler that would otherwise send them.
"""

import os

DEFAULT_ALLOWED_ORIGINS = "capacitor://localhost,http://localhost,https://localhost"
PREFLIGHT_MAX_AGE = "600"


def allowed_origins(env_var="ALLOWED_ORIGINS", default=DEFAULT_ALLOWED_ORIGINS):
    raw = os.environ.get(env_var)
    if raw is None or not raw.strip():
        raw = default
    return [o.strip() for o in raw.split(",") if o.strip()]


class CorsMixin:
    cors_methods = "GET, POST, OPTIONS"
    cors_headers = "Content-Type"
    cors_allow_credentials = False
    # Which env var holds this handler's allowlist, and its default when unset/empty.
    cors_env_var = "ALLOWED_ORIGINS"
    cors_default_origins = DEFAULT_ALLOWED_ORIGINS

    def _cors_origin(self):
        """The value for Access-Control-Allow-Origin, or None if this request gets no CORS."""
        origin = self.request.headers.get("Origin")
        allowed = allowed_origins(self.cors_env_var, self.cors_default_origins)
        if not origin:
            # Same as the old blanket header: a public "*" is sent even without an Origin.
            return "*" if "*" in allowed else None
        if origin in allowed:
            return origin
        if "*" in allowed:
            return "*"
        return None

    def set_default_headers(self):
        # Called again by Tornado when it resets headers for an error response, so
        # 4xx/5xx replies stay readable to an allowed origin.
        super().set_default_headers()
        self.add_header("Vary", "Origin")
        origin = self._cors_origin()
        if origin is None:
            return
        self.set_header("Access-Control-Allow-Origin", origin)
        if origin != "*" and self.cors_allow_credentials:
            self.set_header("Access-Control-Allow-Credentials", "true")

    def options(self, *args, **kwargs):
        origin = self._cors_origin()
        if self.request.headers.get("Origin") and origin is None:
            self.set_status(403)
            self.finish()
            return
        if origin is not None:
            self.set_header("Access-Control-Allow-Methods", self.cors_methods)
            self.set_header("Access-Control-Allow-Headers", self.cors_headers)
            self.set_header("Access-Control-Max-Age", PREFLIGHT_MAX_AGE)
        self.set_status(204)
        self.finish()
