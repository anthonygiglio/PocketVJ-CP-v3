"""HTTP front end for the control API and the web panel (standard library only).

Security model, in one place:
* every API call except hello, pair and session needs a paired device token
  (HttpOnly SameSite=Strict cookie, or Authorization: Bearer for scripts)
* every state-changing call is a POST with JSON, the X-PVJ-Request header and a
  matching Origin (if the browser sends one), so other websites cannot drive it
* the panel is served with a Content-Security-Policy that forbids inline script,
  external resources and framing
"""

import json
import os
import signal
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from . import hardware, themes as themes_mod
from .api import Api
from .auth import Auth
from .modules import Registry
from .player import Player
from .settings import Settings, SettingsError

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
MAX_BODY = 64 * 1024
COOKIE = "pvj_token"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".ico": "image/x-icon"}


def make_handler(api, auth, web_dir=WEB_DIR):
    static = {"/": "index.html"}
    if os.path.isdir(web_dir):
        for name in os.listdir(web_dir):
            static["/" + name] = name

    class Handler(BaseHTTPRequestHandler):
        server_version = "pvj"
        sys_version = ""
        timeout = 15

        def log_message(self, fmt, *args):
            sys.stderr.write("%s %s\n" % (self.client_address[0], fmt % args))

        # --- plumbing ---------------------------------------------------
        def _send(self, status, body, content_type, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, payload, extra=None):
            self._send(status, json.dumps(payload).encode(), "application/json", extra)

        def _token(self):
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                return header[7:].strip()
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie", ""))
            except Exception:
                return None
            return jar[COOKIE].value if COOKIE in jar else None

        def _csrf_ok(self):
            if self.headers.get("X-PVJ-Request") != "1":
                return False
            origin = self.headers.get("Origin")
            if origin:
                parts = urlsplit(origin)
                got = (parts.hostname or "") + (":%d" % parts.port if parts.port else "")
                if got.lower() != (self.headers.get("Host") or "").lower():
                    return False
            return True

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return None, (411, "Content-Length required")
            if length < 0 or length > MAX_BODY:
                return None, (413, "body too large")
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return None, (415, "send application/json")
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return None, (400, "invalid JSON")
            if not isinstance(data, dict):
                return None, (400, "JSON object expected")
            return data, None

        # --- routing ----------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                return self._api("GET", path, {})
            if path == "/theme.css":
                return self._send(200, api.theme_css().encode(), TYPES[".css"])
            name = static.get(path)
            if name is None:
                return self._json(404, {"error": "not found"})
            with open(os.path.join(web_dir, name), "rb") as f:
                body = f.read()
            self._send(200, body, TYPES.get(os.path.splitext(name)[1], "application/octet-stream"))

        def do_POST(self):
            path = urlsplit(self.path).path
            if not path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            if not self._csrf_ok():
                return self._json(403, {"error": "cross-site or missing request header"})
            body, err = self._body()
            if err:
                return self._json(err[0], {"error": err[1]})
            self._api("POST", path, body)

        def _method_not_allowed(self):
            self._json(405, {"error": "method not allowed"}, [("Allow", "GET, POST")])

        do_PUT = do_DELETE = do_PATCH = _method_not_allowed

        def _api(self, method, path, body):
            device = auth.authenticate(self._token())
            status, payload = api.handle(method, path, body, device, self.client_address[0])
            extra = []
            if status == 200 and path in ("/api/pair", "/api/session") and payload.get("token"):
                extra.append(("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
                              % (COOKIE, payload["token"])))
            if payload.get("retry_after"):
                extra.append(("Retry-After", str(payload["retry_after"])))
            self._json(status, payload, extra)

    return Handler


def write_pin_file(rundir, pin):
    """Show-the-PIN channel: a tmpfs file the display or an admin can read. Cleared on reboot."""
    path = os.path.join(rundir, "pin")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(fd, "w") as f:
        f.write(pin + "\n")
    os.chmod(path, 0o640)


def build(env=None, player=None):
    env = os.environ if env is None else env
    state = env.get("PVJ_STATE_DIR", "/var/lib/pvj")
    media = env.get("PVJ_MEDIA_DIR", os.path.join(state, "video"))
    settings = Settings(os.path.join(state, "settings.json"))
    settings.load()
    board = hardware.detect_board()
    auth = Auth(settings, rotate_on_start=True)
    registry = Registry(settings, board["kind"])
    themes = themes_mod.load_themes(os.path.join(state, "addons"))
    player = player or Player()
    rundir = player.rundir
    api = Api(player, settings, auth, registry, themes, media, board,
              spawn=env.get("PVJ_DEV_SPAWN") == "1", on_pin=lambda pin: write_pin_file(rundir, pin))
    write_pin_file(rundir, auth.current_pin)
    return api, auth, rundir


def main(argv=None):
    env = os.environ
    try:
        api, auth, rundir = build(env)
    except SettingsError as e:
        print("pvj-web: %s" % e, file=sys.stderr)
        return 1
    host, port = env.get("PVJ_BIND", "0.0.0.0"), int(env.get("PVJ_PORT", "8080"))
    httpd = ThreadingHTTPServer((host, port), make_handler(api, auth))
    httpd.daemon_threads = True
    print("pvj-web: listening on %s:%d; pairing PIN %s (also in %s/pin)" % (host, port, auth.current_pin, rundir),
          flush=True)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
