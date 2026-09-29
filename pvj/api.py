# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The control API: pure request handling, no sockets.

`Api.handle(method, path, body, device, client)` returns (status, payload). The
HTTP layer (server.py) authenticates, enforces POST plus the CSRF header, and
calls this. Every input is validated here; nothing user-supplied reaches a shell
or a path unchecked.
"""

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unicodedata

from . import hardware, netcfg, osc as osc_mod, presets, streams as streams_mod, themes as themes_mod
from .auth import Auth, AuthError
from .modules import ModuleError
from .player import PlayerError, VIDEO_EXTENSIONS, IMAGE_EXTENSIONS
from .themes import ThemeError

MEDIA_EXTENSIONS = VIDEO_EXTENSIONS + IMAGE_EXTENSIONS
_NAME = re.compile(r"[^\x00-\x1f/\\]{1,120}")
_MODULE_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


def valid_name(name):
    """A plain file name: no path parts, no control, bidi or other invisible characters, short enough for
    the filesystem in bytes (ext4 limit 255), not a dotfile."""
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name in (".", "..") or name.startswith("."):
        return False
    if len(name.encode("utf-8", "surrogatepass")) > 200:
        return False
    # U+FFFD is what invalid UTF-8 from a client turns into: a name that is not really a name
    return "\ufffd" not in name and not any(unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn") for ch in name)


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


MAX_UPLOAD_BYTES = int(os.environ.get("PVJ_MAX_UPLOAD_MB", "8192")) * 1024 * 1024
FREE_SPACE_RESERVE = 200 * 1024 * 1024  # never fill the disk completely: the system needs room to work
CHUNK = 256 * 1024
MIN_UPLOAD_RATE = 20 * 1024   # bytes/second: slower than this after the grace period is abandoned
RATE_GRACE_SECONDS = 30.0


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
                 spawn=False, on_pin=None, osc=None, free_space=None, net=None, ip_json=None, net_sysfs="/sys/class/net"):
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
        self.osc = osc            # OscManager or None
        self._free_space = free_space or self._statvfs_free
        self.net = net            # NetdClient or None
        self._sysfs = net_sysfs
        self._ip_json = ip_json or self._run_ip
        self._upload_lock = threading.Lock()  # one upload at a time: protects the SD card and the threads
        self._media_lock = threading.Lock()   # rename, delete and publishing an upload never interleave
        self.mix = {"opacity": 100, "blackout": False, "size": 100, "position": 0, "rotate": 0}
        self.fader = Fader(self._apply_opacity)
        self.scheduler = None     # Scheduler or None

    # --- helpers -------------------------------------------------------
    def _apply_opacity(self, percent):
        try:
            self.player.opacity(round(min(100, max(0, percent)) * 2.55))
        except PlayerError:
            pass

    def resolve_media(self, name):
        """A media file by name, guaranteed to live inside the media folder."""
        if not valid_name(name):
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
        return {"name": "nxlx.mastercontrol", "paired": bool(device), "board": self.board["kind"]}

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
        return {"player": self._public_player_status(), "mix": dict(self.mix, **self.settings.data["mix"]),
                "system": {"board": self.board["kind"], "model": self.board["model"],
                           "temp_c": max((t["celsius"] for t in temps), default=None)},
                "device": device}

    def _public_player_status(self):
        """Player status with stream passwords hidden and the saved stream's name added."""
        status = dict(self.player.status())
        path = status.get("path")
        if isinstance(path, str) and "://" in path:
            for st in self.settings.data.get("streams", []):
                if st["url"] == path:
                    status["stream"] = st["name"]
            status["path"] = streams_mod.redact(path)
        return status

    def _statvfs_free(self):
        path = self.media_dir
        while path and not os.path.exists(path):  # a media folder not created yet: ask its parent
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
        try:
            st = os.statvfs(path)
        except OSError:
            return 0
        return st.f_bavail * st.f_frsize

    def sweep_stale_uploads(self):
        """Delete .upload-* temp files. They are hidden, can be gigabytes, and are left behind by a
        power cut or a killed service. Safe whenever no upload is running (we hold the lock or are starting)."""
        removed = 0
        try:
            names = os.listdir(self.media_dir)
        except OSError:
            return 0
        for n in names:
            if n.startswith(".upload-"):
                try:
                    os.unlink(os.path.join(self.media_dir, n))
                    removed += 1
                except OSError:
                    pass
        return removed

    def media(self, body, device, client):
        details = []
        for name in self.media_list():
            try:
                st = os.stat(os.path.join(self.media_dir, name))
                details.append({"name": name, "size": st.st_size, "modified": int(st.st_mtime)})
            except OSError:
                pass
        return {"files": [d["name"] for d in details], "details": details, "free": self._free_space(),
                "max_upload": MAX_UPLOAD_BYTES}

    def _safe_new_name(self, name):
        if not valid_name(name):
            raise bad("invalid file name")
        if not name.lower().endswith(MEDIA_EXTENSIONS):
            raise bad("only video and image files: " + ", ".join(MEDIA_EXTENSIONS))
        return name

    def upload(self, name, length, read, replace=False, check=None, clock=time.monotonic):
        """Store a file from a stream. `read(n)` returns up to n bytes (b'' at the end); `check()` is
        called between chunks and may raise ApiError to abandon the upload (device revoked)."""
        name = self._safe_new_name(name)
        if not isinstance(length, int) or length <= 0:
            raise ApiError(411, "Content-Length required")
        if length > MAX_UPLOAD_BYTES:
            raise ApiError(413, "file is larger than the %d MB limit" % (MAX_UPLOAD_BYTES // (1024 * 1024)))
        root = os.path.realpath(self.media_dir)
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            raise ApiError(500, "media folder is not writable: %s" % (e.strerror or e))
        if not self._upload_lock.acquire(blocking=False):
            raise ApiError(409, "another upload is in progress")
        tmp = None
        try:
            self.sweep_stale_uploads()  # any .upload-* file now is left over from a crash: no upload is running
            if length + FREE_SPACE_RESERVE > self._free_space():
                raise ApiError(507, "not enough free space on the box")
            final = os.path.join(root, name)
            if os.path.lexists(final) and not replace:
                raise ApiError(409, "a file with that name already exists")
            if os.path.islink(final):
                raise ApiError(409, "refusing to replace a link")
            fd, tmp = tempfile.mkstemp(prefix=".upload-", dir=root)  # hidden, so it never shows in the list
            got, started = 0, clock()
            with os.fdopen(fd, "wb") as out:
                while got < length:
                    if check:
                        check()
                    chunk = read(min(CHUNK, length - got))
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    elapsed = clock() - started
                    if elapsed > RATE_GRACE_SECONDS and got / elapsed < MIN_UPLOAD_RATE:
                        raise ApiError(408, "upload too slow (under %d KB/s); try again on a better connection"
                                       % (MIN_UPLOAD_RATE // 1024))
                out.flush()
                os.fsync(out.fileno())
            if got != length:
                raise ApiError(400, "upload was cut short (%d of %d bytes)" % (got, length))
            os.chmod(tmp, 0o664)
            with self._media_lock:
                if replace:
                    os.replace(tmp, final)
                else:
                    try:
                        os.link(tmp, final)  # fails if the name appeared meanwhile: never overwrites silently
                    except FileExistsError:
                        raise ApiError(409, "a file with that name appeared during the upload")
                    os.unlink(tmp)
                tmp = None
            self._fsync_dir(root)
            return {"name": name, "size": length}
        except (TimeoutError, ConnectionError):
            raise ApiError(400, "upload interrupted")
        except OSError as e:
            raise ApiError(500, "could not store the file: %s" % (e.strerror or e))
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            self._upload_lock.release()

    @staticmethod
    def _fsync_dir(path):
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass  # not every filesystem supports it (some USB mounts); the file itself was synced

    def _real_media_file(self, name):
        """The path of a real (non-link) media file called `name`; links are refused so a delete or rename
        can never act on something other than the name the user saw."""
        path = self.resolve_media(name)
        plain = os.path.join(os.path.realpath(self.media_dir), name)
        if os.path.islink(plain):
            raise ApiError(409, "%s is a link; change it on the box, not from here" % name)
        return plain

    def _pads_using(self, name):
        return [(b, i) for b, bank in enumerate(self.settings.data["pads"]["banks"])
                for i, p in enumerate(bank["pads"]) if p.get("file") == name]

    def delete_media(self, body, device, client):
        name = body.get("name")
        with self._media_lock:
            path = self._real_media_file(name)
            try:
                os.unlink(path)
            except OSError as e:
                raise ApiError(500, "could not delete: %s" % (e.strerror or e))
        return {"deleted": name, "pads_using": len(self._pads_using(name))}

    def rename_media(self, body, device, client):
        name = body.get("name")
        new = self._safe_new_name(body.get("new"))
        with self._media_lock:
            src = self._real_media_file(name)
            dst = os.path.join(os.path.realpath(self.media_dir), new)
            try:
                os.link(src, dst)  # fails if `new` exists: a rename must never overwrite
            except FileExistsError:
                raise ApiError(409, "a file with that name already exists")
            except OSError as e:
                raise ApiError(500, "could not rename: %s" % (e.strerror or e))
            os.unlink(src)
        with self.settings.lock:  # pads that used the old name follow the file
            for b, i in self._pads_using(name):
                self.settings.data["pads"]["banks"][b]["pads"][i]["file"] = new
            self.settings.save()
        return {"name": new}

    def get_pads(self, body, device, client):
        return {"banks": self.settings.data["pads"]["banks"]}

    def set_pad(self, body, device, client):
        bank = number(body, "bank", 0, len(self.settings.data["pads"]["banks"]) - 1, integer=True)
        index = number(body, "index", 0, 11, integer=True)
        label, file = body.get("label", ""), body.get("file", "")
        if not isinstance(label, str) or len(label) > 40 or re.search(r"[\x00-\x1f]", label):
            raise bad("invalid label")
        if file != "":
            if not valid_name(file) or not file.lower().endswith(MEDIA_EXTENSIONS):
                raise bad("invalid file name")
        with self.settings.lock:
            self.settings.data["pads"]["banks"][bank]["pads"][index] = {"label": label, "file": file}
            self.settings.save()
        return {"banks": self.settings.data["pads"]["banks"]}

    def play_preset(self, body, name):
        """A legacy start script name (startlessonce05 ...) played from the media folder."""
        try:
            preset = presets.parse_legacy_name(name if isinstance(name, str) else "")
            files = presets.resolve_files(preset, self.media_dir)
        except PlayerError as e:
            raise bad(str(e))
        root = os.path.realpath(self.media_dir)
        paths = [f for f in (os.path.realpath(f) for f in files)
                 if os.path.dirname(f) == root and f.lower().endswith(MEDIA_EXTENSIONS)]
        if not paths:
            raise ApiError(404, "no playable files for that preset")
        self._player_call(self.player.play, paths, preset["loop"], None, False, self.spawn)
        self._apply_opacity(0 if self.mix["blackout"] else self.mix["opacity"])
        return {"playing": name, "files": len(paths)}

    def play(self, body, device, client):
        if "preset" in body:
            return self.play_preset(body, body["preset"])
        if "stream" in body:
            return self.play_stream(body)
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
        self.fader.cancel()  # a fade still running from an earlier action must not darken the new clip

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

    def _need_streams(self):
        if not self.registry.enabled("inputs-srt"):
            raise ApiError(409, "turn on the Streams module in System first")

    def play_stream(self, body):
        self._need_streams()
        sid = body.get("stream")
        match = [s for s in self.settings.data["streams"] if s["id"] == sid]
        if not match:
            raise ApiError(404, "no such stream")
        self.fader.cancel()
        self._player_call(self.player.play, [match[0]["url"]], False, None, False, self.spawn)
        self._apply_opacity(0 if self.mix["blackout"] else self.mix["opacity"])
        return {"playing": match[0]["name"]}

    def get_streams(self, body, device, client):
        self._need_streams()
        return {"streams": [{"id": s["id"], "name": s["name"], "url": streams_mod.redact(s["url"]),
                             "has_login": s["url"] != streams_mod.redact(s["url"])}
                            for s in self.settings.data["streams"]],
                "schemes": list(streams_mod.SCHEMES)}

    def set_streams(self, body, device, client):
        """Add or remove one stream. Saved addresses are never sent back to the panel, so an edit
        cannot round-trip a hidden password."""
        self._need_streams()
        action = body.get("action")
        with self.settings.lock:
            items = list(self.settings.data["streams"])
            try:
                if action == "add":
                    if len(items) >= streams_mod.MAX_STREAMS:
                        raise bad("at most %d streams" % streams_mod.MAX_STREAMS)
                    items.append(streams_mod.new_entry(body.get("name"), body.get("url")))
                elif action == "remove":
                    if not any(s["id"] == body.get("id") for s in items):
                        raise ApiError(404, "no such stream")
                    items = [s for s in items if s["id"] != body.get("id")]
                else:
                    raise bad("action must be add or remove")
            except streams_mod.StreamError as e:
                raise bad(str(e))
            self.settings.data["streams"] = items
            self.settings.save()
        return self.get_streams({}, device, client)

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
        elif action == "stop":
            self._player_call(p.clear)
        elif action == "volume_step":
            self._player_call(p.volume_step, number(body, "value", -50, 50))
        elif action == "reset":
            self.mix.update(opacity=100, size=100, position=0, rotate=0)
            self.fader.cancel()
            # During a blackout the screen must stay dark: reset changes the stored mix, not the picture.
            shown = 0 if self.mix["blackout"] else 255
            for fn, arg in ((p.opacity, shown), (p.size, 100), (p.position, 0), (p.speed, 1), (p.rotate, 0)):
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
        with self.settings.lock:
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
        with self.settings.lock:
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

    def get_osc(self, body, device, client):
        if self.osc is None:
            raise ApiError(404, "OSC is not available")
        return self.osc.status()

    def set_osc(self, body, device, client):
        if self.osc is None:
            raise ApiError(404, "OSC is not available")
        cfg = self.settings.data["osc"]
        new = dict(cfg)
        if "enabled" in body:
            if not isinstance(body["enabled"], bool):
                raise bad("enabled must be true or false")
            new["enabled"] = body["enabled"]
        if "port" in body:
            new["port"] = number(body, "port", 1024, 65535, integer=True)
        if "allow" in body:
            try:
                new["allow"] = osc_mod.validate_allow(body["allow"])
            except osc_mod.OscError as e:
                raise bad(str(e))
        with self.settings.lock:
            self.settings.data["osc"] = new
        try:
            self.osc.apply()
        except osc_mod.OscError as e:
            with self.settings.lock:
                self.settings.data["osc"] = cfg  # keep the last working configuration
            try:
                self.osc.apply()
            except osc_mod.OscError:
                pass
            raise ApiError(409, str(e))
        self.settings.save()
        return self.osc.status()

    # --- schedule ------------------------------------------------------
    def _need_scheduler(self):
        if self.scheduler is None or not self.registry.enabled("scheduler"):
            raise ApiError(409, "turn on the Scheduler module in System first")

    def get_schedule(self, body, device, client):
        self._need_scheduler()
        return self.scheduler.status()

    def set_schedule(self, body, device, client):
        from . import scheduler as scheduler_mod
        self._need_scheduler()
        try:
            clean = scheduler_mod.validate(body)
        except scheduler_mod.ScheduleError as e:
            raise bad(str(e))
        with self.settings.lock:
            self.settings.data["schedule"] = clean
            self.settings.save()
        return self.scheduler.status()

    # --- network (wired) -----------------------------------------------
    @staticmethod
    def _run_ip():
        try:
            r = subprocess.run(["ip", "-j", "-4", "addr", "show"], capture_output=True, text=True, timeout=5)
            return json.loads(r.stdout) if r.returncode == 0 else []
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []

    def _need_network_module(self):
        if not self.registry.enabled("network"):
            raise ApiError(409, "turn on the Network module in System first")

    def _netd(self, message):
        if self.net is None:
            raise ApiError(503, "network settings are not available on this system")
        try:
            reply = self.net.request(message)
        except netcfg.NetError as e:
            raise ApiError(503, str(e))
        if not reply.get("ok"):
            raise ApiError(409, reply.get("error", "the network helper refused"))
        return reply

    def _local_status(self):
        addrs = {}
        for entry in self._ip_json():
            addrs[entry.get("ifname")] = ["%s/%s" % (a.get("local"), a.get("prefixlen")) for a in entry.get("addr_info", [])
                                          if a.get("family") == "inet"]
        interfaces = netcfg.list_interfaces(self._sysfs)
        for i in interfaces:
            i["addresses"] = addrs.get(i["name"], [])
        return interfaces

    def get_network(self, body, device, client):
        self._need_network_module()
        out = {"interfaces": self._local_status(), "pending": None, "helper": False, "modes": list(netcfg.MODES)}
        if self.net is not None:
            try:
                reply = self.net.request({"cmd": "status"})
                if reply.get("ok"):
                    out["pending"], out["helper"] = reply.get("pending"), True
                    out["reverting"] = bool(reply.get("reverting"))
            except netcfg.NetError:
                pass
        return out

    def _networks_in_use(self):
        import ipaddress
        out = []
        for entry in self._ip_json():
            if entry.get("ifname") == "lo":
                continue
            for a in entry.get("addr_info", []):
                try:
                    out.append((entry.get("ifname"), ipaddress.ip_network("%s/%s" % (a["local"], a["prefixlen"]), strict=False)))
                except (KeyError, ValueError):
                    pass
        return out

    def _checked_config(self, body):
        try:
            return netcfg.validate(body, netcfg.list_interfaces(self._sysfs), self._networks_in_use())
        except netcfg.NetError as e:
            raise bad(str(e))

    def plan_network(self, body, device, client):
        self._need_network_module()
        cfg = self._checked_config(body)
        if self.net is not None:
            try:
                reply = self.net.request({"cmd": "plan", "config": cfg})
                if reply.get("ok"):
                    return {"config": reply["config"], "commands": reply["commands"]}
            except netcfg.NetError:
                pass
        return {"config": cfg, "commands": netcfg.preview(netcfg.plan(cfg))}

    def apply_network(self, body, device, client):
        self._need_network_module()
        cfg = self._checked_config(body)  # the helper validates again; refusing early gives a clear 400
        reply = self._netd({"cmd": "apply", "config": cfg})
        return {"pending": reply.get("pending"), "config": cfg}

    def confirm_network(self, body, device, client):
        # not gated on the module: a pending change must always be confirmable or revertable
        return {"pending": self._netd({"cmd": "confirm"}).get("pending")}

    def revert_network(self, body, device, client):
        return {"pending": self._netd({"cmd": "revert"}).get("pending")}

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
            ("GET", "/api/osc"): ("view", self.get_osc),
            ("POST", "/api/osc"): ("full", self.set_osc),
            ("POST", "/api/play"): ("live", self.play),
            ("POST", "/api/control"): ("live", self.control),
            ("POST", "/api/blackout"): ("live", self.blackout),
            ("POST", "/api/fadeout"): ("live", self.fadeout),
            ("POST", "/api/mix"): ("live", self.set_mix),
            ("GET", "/api/streams"): ("view", self.get_streams),
            ("POST", "/api/streams"): ("full", self.set_streams),
            ("GET", "/api/schedule"): ("view", self.get_schedule),
            ("POST", "/api/schedule"): ("full", self.set_schedule),
            ("GET", "/api/network"): ("full", self.get_network),
            ("POST", "/api/network/plan"): ("full", self.plan_network),
            ("POST", "/api/network/apply"): ("full", self.apply_network),
            ("POST", "/api/network/confirm"): ("full", self.confirm_network),
            ("POST", "/api/network/revert"): ("full", self.revert_network),
            ("POST", "/api/media/delete"): ("full", self.delete_media),
            ("POST", "/api/media/rename"): ("full", self.rename_media),
            ("POST", "/api/pads"): ("full", self.set_pad),
            ("POST", "/api/theme"): ("full", self.set_theme),
            ("GET", "/api/devices"): ("full", self.devices),
            ("POST", "/api/devices/invite"): ("full", self.invite),
            ("POST", "/api/devices/revoke"): ("full", self.revoke),
            ("POST", "/api/pin/rotate"): ("full", self.rotate_pin),
            ("POST", "/api/player/restart"): ("full", self.stop_player),
        }

    @staticmethod
    def require(device, role):
        if device is None:
            raise ApiError(401, "pair this device first")
        if not Auth.allows(device, role):
            raise ApiError(403, "this device may not do that (%s access needed)" % role)

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
