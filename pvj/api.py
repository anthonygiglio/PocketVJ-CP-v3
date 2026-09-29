"""The control API: pure request handling, no sockets.

`Api.handle(method, path, body, device, client)` returns (status, payload). The
HTTP layer (server.py) authenticates, enforces POST plus the CSRF header, and
calls this. Every input is validated here; nothing user-supplied reaches a shell
or a path unchecked.
"""

import os
import re
import threading
import time

from . import hardware, themes as themes_mod
from .auth import Auth, AuthError
from .modules import ModuleError
from .player import PlayerError, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
from .themes import ThemeError

MEDIA_EXTENSIONS = VIDEO_EXTENSIONS + IMAGE_EXTENSIONS
_NAME = re.compile(r"^[^\x00-\x1f/\\]{1,120}$")
_MODULE_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class ApiError(Exception):
    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def bad(message):
    return ApiError(400, message)


def number(body, key, lo, hi, integer=False):
    v = body.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not lo <= v <= hi:
        raise bad("%s must be a number from %s to %s" % (key, lo, hi))
    return int(v) if integer else float(v)


class Fader:
    """Ramps opacity in steps inside the player, on a background thread, so one
    request replaces the old one-process-per-step approach."""

    def __init__(self, apply):
        self._apply = apply
        self._token = 0
        self._lock = threading.Lock()

    def cancel(self):
        with self._lock:
            self._token += 1

    def ramp(self, start, end, seconds, then=None):
        with self._lock:
            self._token += 1
            token = self._token
        steps = max(1, int(seconds * 20))

        def run():
            for i in range(1, steps + 1):
                with self._lock:
                    if token != self._token:
                        return
                self._apply(start + (end - start) * i / steps)
                time.sleep(seconds / steps)
            if then:
                with self._lock:
                    if token != self._token:
                        return
                then()
        threading.Thread(target=run, daemon=True).start()


class Api:
    def __init__(self, player, settings, auth, registry, themes, media_dir, board, addons_dir=None,
                 spawn=False, on_pin=None):
        self.player = player
        self.settings = settings
        self.auth = auth
        self.registry = registry
        self.themes = themes
        self.media_dir = media_dir
        self.board = board
        self.addons_dir = addons_dir
        self.spawn = spawn        # True only for development: start mpv ourselves
        self.on_pin = on_pin      # called with the new PIN so the box can show it
        self.mix = {"opacity": 100, "blackout": False, "size": 100, "position": 0, "rotate": 0}
        self.fader = Fader(self._apply_opacity)

    # --- helpers -------------------------------------------------------
    def _apply_opacity(self, percent):
        try:
            self.player.opacity(round(min(100, max(0, percent)) * 2.55))
        except PlayerError:
            pass

    def resolve_media(self, name):
        """A media file by name, guaranteed to live inside the media folder."""
        if not isinstance(name, str) or not _NAME.match(name) or name in (".", "..") or name.startswith("."):
            raise bad("invalid file name")
        if not name.lower().endswith(MEDIA_EXTENSIONS):
            raise bad("not a media file")
        root = os.path.realpath(self.media_dir)
        path = os.path.realpath(os.path.join(root, name))
        if os.path.dirname(path) != root or not os.path.isfile(path):
            raise ApiError(404, "file not found")
        return path

    def media_list(self):
        try:
            names = sorted(n for n in os.listdir(self.media_dir)
                           if not n.startswith(".") and n.lower().endswith(MEDIA_EXTENSIONS)
                           and os.path.isfile(os.path.join(self.media_dir, n)))
        except OSError:
            names = []
        return names

    def _player_call(self, fn, *args):
        try:
            return fn(*args)
        except PlayerError as e:
            raise ApiError(503, str(e))

    # --- handlers ------------------------------------------------------
    def hello(self, body, device, client):
        return {"name": "NXLX PocketVJ", "paired": bool(device), "board": self.board["kind"]}

    def pair(self, body, device, client):
        try:
            token, dev = self.auth.pair(str(body.get("pin", "")), str(body.get("name", "device")), client)
        except AuthError as e:
            raise ApiError(429 if e.retry_after else 403, str(e), e.retry_after)
        return {"device": dev, "token": token}

    def session(self, body, device, client):
        """Start a session from a token (a guest link): the server sets the cookie."""
        dev = self.auth.authenticate(body.get("token"))
        if dev is None:
            raise ApiError(403, "invalid or revoked token")
        return {"device": dev, "token": body["token"]}

    def status(self, body, device, client):
        temps = hardware.temperatures()
        return {"player": self.player.status(), "mix": dict(self.mix, **self.settings.data["mix"]),
                "system": {"board": self.board["kind"], "model": self.board["model"],
                           "temp_c": max((t["celsius"] for t in temps), default=None)},
                "device": device}

    def media(self, body, device, client):
        return {"files": self.media_list()}

    def get_pads(self, body, device, client):
        return {"banks": self.settings.data["pads"]["banks"]}

    def set_pad(self, body, device, client):
        bank = number(body, "bank", 0, len(self.settings.data["pads"]["banks"]) - 1, integer=True)
        index = number(body, "index", 0, 11, integer=True)
        label, file = body.get("label", ""), body.get("file", "")
        if not isinstance(label, str) or len(label) > 40 or re.search(r"[\x00-\x1f]", label):
            raise bad("invalid label")
        if file != "":
            if not isinstance(file, str) or not _NAME.match(file) or file.startswith(".") \
                    or not file.lower().endswith(MEDIA_EXTENSIONS):
                raise bad("invalid file name")
        self.settings.data["pads"]["banks"][bank]["pads"][index] = {"label": label, "file": file}
        self.settings.save()
        return {"banks": self.settings.data["pads"]["banks"]}

    def play(self, body, device, client):
        if "pad" in body:
            pad = body["pad"]
            if not (isinstance(pad, list) and len(pad) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in pad)):
                raise bad("pad must be [bank, index]")
            banks = self.settings.data["pads"]["banks"]
            if not (0 <= pad[0] < len(banks) and 0 <= pad[1] < 12):
                raise bad("no such pad")
            name = banks[pad[0]]["pads"][pad[1]]["file"]
            if not name:
                raise bad("pad is empty")
        else:
            name = body.get("file")
        path = self.resolve_media(name)
        loop = body.get("loop", True)
        if not isinstance(loop, bool):
            raise bad("loop must be true or false")
        transition = self.settings.data["mix"]
        playing = self._player_call(self.player.status).get("running")

        dip = transition["transition"] == "dip" and not self.mix["blackout"]

        def start():
            self._player_call(self.player.play, [path], loop, None, False, self.spawn)
            if self.mix["blackout"]:
                return
            if dip:
                self._apply_opacity(0)
                self.fader.ramp(0, self.mix["opacity"], transition["duration"] / 2)
            else:
                self._apply_opacity(self.mix["opacity"])

        if playing and dip:
            self.fader.ramp(self.mix["opacity"], 0, transition["duration"] / 2, then=start)
        else:
            start()
        return {"playing": name}

    def control(self, body, device, client):
        action = body.get("action")
        p = self.player
        if action == "pause":
            value = body.get("value")
            if value is not None and not isinstance(value, bool):
                raise bad("value must be true, false or null")
            return {"paused": self._player_call(p.pause, value)}
        if action == "seek":
            self._player_call(p.seek, number(body, "value", -3600, 3600))
        elif action == "speed":
            self._player_call(p.speed, number(body, "value", 0.1, 4))
        elif action == "volume":
            self._player_call(p.volume, number(body, "value", 0, 130))
        elif action == "opacity":
            self.mix["opacity"] = number(body, "value", 0, 100)
            if not self.mix["blackout"]:
                self.fader.cancel()
                self._player_call(p.opacity, round(self.mix["opacity"] * 2.55))
        elif action == "size":
            self.mix["size"] = number(body, "value", 1, 200)
            self._player_call(p.size, self.mix["size"])
        elif action == "position":
            self.mix["position"] = number(body, "value", -100, 100)
            self._player_call(p.position, self.mix["position"] * 10)
        elif action == "rotate":
            degrees = number(body, "value", 0, 270, integer=True)
            if degrees not in (0, 90, 180, 270):
                raise bad("rotation must be 0, 90, 180 or 270")
            self.mix["rotate"] = degrees
            self._player_call(p.rotate, degrees)
        elif action == "loop":
            if not isinstance(body.get("value"), bool):
                raise bad("value must be true or false")
            self._player_call(p.loop, body["value"])
        elif action == "mute":
            if not isinstance(body.get("value"), bool):
                raise bad("value must be true or false")
            self._player_call(p.mute, body["value"])
        elif action == "reset":
            self.mix.update(opacity=100, size=100, position=0, rotate=0)
            for fn, arg in ((p.opacity, 255), (p.size, 100), (p.position, 0), (p.speed, 1), (p.rotate, 0)):
                self._player_call(fn, arg)
        else:
            raise bad("unknown action")
        return {"ok": True}

    def blackout(self, body, device, client):
        on = body.get("on")
        if not isinstance(on, bool):
            raise bad("on must be true or false")
        self.fader.cancel()
        self.mix["blackout"] = on
        self._player_call(self.player.opacity, 0 if on else round(self.mix["opacity"] * 2.55))
        return {"blackout": on}

    def fadeout(self, body, device, client):
        seconds = number(body, "seconds", 0.1, 30)
        self._player_call(self.player.status)
        self.fader.ramp(self.mix["opacity"], 0, seconds)
        return {"ok": True}

    def set_mix(self, body, device, client):
        mode, duration = body.get("transition"), body.get("duration")
        if mode not in ("cut", "dip"):
            raise bad("transition must be cut or dip (crossfade is not built yet)")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0.1 <= duration <= 10:
            raise bad("duration must be 0.1 to 10 seconds")
        self.settings.data["mix"] = {"transition": mode, "duration": float(duration)}
        self.settings.save()
        return self.settings.data["mix"]

    def stop_player(self, body, device, client):
        # The systemd unit (Restart=always) brings the player straight back.
        self._player_call(self.player.ipc.request, "quit")
        return {"ok": True}

    def get_modules(self, body, device, client):
        return {"modules": self.registry.list()}

    def set_module(self, module_id, body, device, client):
        if not _MODULE_ID.match(module_id):
            raise ApiError(404, "unknown module")
        try:
            self.registry.set_enabled(module_id, body.get("enabled"))
        except ModuleError as e:
            raise ApiError(409, str(e))
        return {"modules": self.registry.list()}

    def get_theme(self, body, device, client):
        t = self.settings.data["theme"]
        return {"theme": t, "available": [{"id": k, "name": v["name"], "source": v["source"]}
                                          for k, v in self.themes.items()]}

    def set_theme(self, body, device, client):
        name, accent = body.get("name"), body.get("accent")
        if name not in self.themes:
            raise bad("unknown theme")
        try:
            themes_mod.css(self.themes[name], accent)
        except ThemeError as e:
            raise bad(str(e))
        self.settings.data["theme"] = {"name": name, "accent": accent}
        self.settings.save()
        return {"theme": self.settings.data["theme"]}

    def theme_css(self):
        t = self.settings.data["theme"]
        theme = self.themes.get(t["name"]) or self.themes["dark-stage"]
        try:
            return themes_mod.css(theme, t.get("accent"))
        except ThemeError:
            return themes_mod.css(theme)

    def devices(self, body, device, client):
        return {"devices": self.auth.list_devices()}

    def invite(self, body, device, client):
        try:
            token, dev = self.auth.invite(str(body.get("name", "guest"))[:40], body.get("role"))
        except AuthError as e:
            raise bad(str(e))
        return {"device": dev, "token": token, "note": "Share this token once; it is not shown again."}

    def revoke(self, body, device, client):
        did = body.get("id")
        if not isinstance(did, str) or not self.auth.revoke(did):
            raise ApiError(404, "no such device")
        return {"ok": True}

    def rotate_pin(self, body, device, client):
        pin = self.auth.rotate_pin()
        if self.on_pin:
            self.on_pin(pin)
        return {"ok": True, "pin": pin}

    # --- routing -------------------------------------------------------
    def routes(self):
        # (method, path) -> (minimum role or None, handler)
        return {
            ("GET", "/api/hello"): (None, self.hello),
            ("POST", "/api/pair"): (None, self.pair),
            ("POST", "/api/session"): (None, self.session),
            ("GET", "/api/status"): ("view", self.status),
            ("GET", "/api/media"): ("view", self.media),
            ("GET", "/api/pads"): ("view", self.get_pads),
            ("GET", "/api/modules"): ("view", self.get_modules),
            ("GET", "/api/theme"): ("view", self.get_theme),
            ("POST", "/api/play"): ("live", self.play),
            ("POST", "/api/control"): ("live", self.control),
            ("POST", "/api/blackout"): ("live", self.blackout),
            ("POST", "/api/fadeout"): ("live", self.fadeout),
            ("POST", "/api/mix"): ("live", self.set_mix),
            ("POST", "/api/pads"): ("full", self.set_pad),
            ("POST", "/api/theme"): ("full", self.set_theme),
            ("GET", "/api/devices"): ("full", self.devices),
            ("POST", "/api/devices/invite"): ("full", self.invite),
            ("POST", "/api/devices/revoke"): ("full", self.revoke),
            ("POST", "/api/pin/rotate"): ("full", self.rotate_pin),
            ("POST", "/api/player/restart"): ("full", self.stop_player),
        }

    def handle(self, method, path, body, device, client):
        try:
            m = re.match(r"^/api/modules/([^/]+)$", path)
            if m and method == "POST":
                need, handler = "full", lambda b, d, c: self.set_module(m.group(1), b, d, c)
            else:
                route = self.routes().get((method, path))
                if route is None:
                    known = any(p == path for (_, p) in self.routes()) or bool(m)
                    raise ApiError(405 if known else 404, "method not allowed" if known else "not found")
                need, handler = route
            if need is not None:
                if device is None:
                    raise ApiError(401, "pair this device first")
                if not Auth.allows(device, need):
                    raise ApiError(403, "this device may not do that (%s access needed)" % need)
            return 200, handler(body if isinstance(body, dict) else {}, device, client)
        except ApiError as e:
            payload = {"error": e.message}
            if e.retry_after:
                payload["retry_after"] = e.retry_after
            return e.status, payload
