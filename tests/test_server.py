# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
import http.client
import json
import os
import struct
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from pvj import server, themes as themes_mod
from pvj.api import Api
from pvj.auth import Auth
from pvj.modules import Registry
from pvj.osc import OscManager
from pvj.settings import Settings


class FakePlayer:
    def __init__(self, rundir):
        self.rundir = rundir
        self.calls = []
        self.running = False

    def status(self):
        return {"running": self.running, "path": None}

    def play(self, paths, loop=True, audio_device=None, windowed=False, spawn=True):
        self.calls.append(("play", paths, loop, spawn))
        self.running = True

    def __getattr__(self, name):
        if name in ("pause", "seek", "speed", "volume", "opacity", "size", "position", "rotate", "loop", "mute", "clear", "volume_step"):
            def call(*args):
                self.calls.append((name,) + args)
                return True if name == "pause" else None
            return call
        raise AttributeError(name)

    class ipc:
        @staticmethod
        def request(*a):
            return None


class ServerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.media = os.path.join(self.tmp, "video")
        os.makedirs(self.media)
        for n in ("a.mp4", "b.mov", ".hidden.mp4", "notes.txt"):
            open(os.path.join(self.media, n), "w").close()
        outside = os.path.join(self.tmp, "secret.mp4")
        open(outside, "w").close()
        os.symlink(outside, os.path.join(self.media, "link.mp4"))
        self.web = os.path.join(self.tmp, "web")
        os.makedirs(self.web)
        for name, text in (("index.html", "<html>hi</html>"), ("app.js", "//js")):
            with open(os.path.join(self.web, name), "w") as f:
                f.write(text)
        self.settings = Settings(os.path.join(self.tmp, "settings.json"))
        self.settings.load()
        self.settings.data["mix"] = {"transition": "cut", "duration": 1.0}
        self.auth = Auth(self.settings, rotate_on_start=True)
        self.pin = self.auth.current_pin
        rundir = os.path.join(self.tmp, "run")
        os.makedirs(rundir, mode=0o700)
        self.rundir = rundir
        self.player = FakePlayer(rundir)
        board = {"kind": "x86", "model": "test", "arch": "x86_64"}
        self.api = Api(self.player, self.settings, self.auth, Registry(self.settings, "x86"),
                       themes_mod.load_themes(), self.media, board,
                       on_pin=lambda pin: server.write_pin_file(rundir, pin))
        self.api.osc = OscManager(self.api, self.settings, host="127.0.0.1", log=lambda *_: None)
        self.addCleanup(self.api.osc.stop)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.api, self.auth, self.web))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def call(self, method, path, body=None, headers=None, raw=None, token=None, csrf=True):
        h = {}
        if method == "POST":
            h["Content-Type"] = "application/json"
            if csrf:
                h["X-PVJ-Request"] = "1"
        if token:
            h["Authorization"] = "Bearer " + token
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        text = r.read()
        c.close()
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
        return r.status, payload, r

    def pair(self, name="phone"):
        st, body, r = self.call("POST", "/api/pair", {"pin": self.pin, "name": name})
        self.assertEqual(st, 200, body)
        return body["token"], r


class ServerTest(ServerBase):
    # --- authentication ------------------------------------------------
    def test_public_and_protected(self):
        self.assertEqual(self.call("GET", "/api/hello")[0], 200)
        self.assertEqual(self.call("GET", "/api/status")[0], 401)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("POST", "/api/status", {})[0], 405)

    def test_pairing_sets_secure_cookie_and_cookie_authenticates(self):
        token, r = self.pair()
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        st, body, _ = self.call("GET", "/api/status", headers={"Cookie": "pvj_token=" + token})
        self.assertEqual(st, 200)
        self.assertEqual(body["device"]["role"], "full")
        self.assertEqual(self.call("GET", "/api/status", token=token)[0], 200)
        self.assertEqual(self.call("GET", "/api/status", token="wrong")[0], 401)

    def test_wrong_pin_then_rate_limit_with_retry_after(self):
        wrong = "0000" if self.pin != "0000" else "1111"
        codes = [self.call("POST", "/api/pair", {"pin": wrong})[0] for _ in range(5)]
        self.assertEqual(codes, [403] * 5)
        st, body, r = self.call("POST", "/api/pair", {"pin": self.pin})
        self.assertEqual(st, 429)
        self.assertGreater(int(r.getheader("Retry-After")), 0)

    # --- CSRF and request hygiene -------------------------------------
    def test_post_needs_header_matching_origin_json_and_sane_size(self):
        token, _ = self.pair()
        body = {"action": "pause"}
        self.assertEqual(self.call("POST", "/api/control", body, token=token, csrf=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/control", body, token=token,
                                   headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/control", body, token=token,
                                   headers={"Origin": "http://127.0.0.1:%d" % self.port})[0], 200)
        self.assertEqual(self.call("POST", "/api/control", token=token, raw=b"{bad")[0], 400)
        self.assertEqual(self.call("POST", "/api/control", token=token, raw=b"[1]")[0], 400)
        self.assertEqual(self.call("POST", "/api/control", token=token, raw=b"x" * 70000)[0], 413)
        self.assertEqual(self.call("POST", "/api/control", body, token=token,
                                   headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("PUT", "/api/control", token=token)[0], 405)

    def test_get_never_changes_state(self):
        token, _ = self.pair()
        self.call("GET", "/api/control?action=stop", token=token)
        self.call("GET", "/api/play?file=a.mp4", token=token)
        self.assertEqual(self.player.calls, [])

    # --- roles ---------------------------------------------------------
    def test_roles_are_enforced(self):
        full, _ = self.pair()
        st, body, _ = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=full)
        view = body["token"]
        st, body, _ = self.call("POST", "/api/devices/invite", {"name": "tech", "role": "live"}, token=full)
        live = body["token"]
        self.assertEqual(self.call("GET", "/api/status", token=view)[0], 200)
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=view)[0], 403)
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=live)[0], 200)
        for path, body in (("/api/pads", {"bank": 0, "index": 0, "label": "x", "file": "a.mp4"}),
                           ("/api/theme", {"name": "light"}), ("/api/modules/mapper", {"enabled": False}),
                           ("/api/devices/invite", {"name": "x", "role": "view"}), ("/api/pin/rotate", {})):
            self.assertEqual(self.call("POST", path, body, token=live)[0], 403, path)
        self.assertEqual(self.call("GET", "/api/devices", token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/devices/invite", {"name": "x", "role": "full"}, token=full)[0], 400)

    def test_guest_link_session_and_revoke(self):
        full, _ = self.pair()
        _, body, _ = self.call("POST", "/api/devices/invite", {"name": "guest", "role": "view"}, token=full)
        st, _, r = self.call("POST", "/api/session", {"token": body["token"]})
        self.assertEqual(st, 200)
        self.assertIn("pvj_token=", r.getheader("Set-Cookie"))
        self.assertEqual(self.call("POST", "/api/session", {"token": "nope"})[0], 403)
        self.call("POST", "/api/devices/revoke", {"id": body["device"]["id"]}, token=full)
        self.assertEqual(self.call("GET", "/api/status", token=body["token"])[0], 401)

    # --- input validation and paths -----------------------------------
    def test_play_rejects_paths_outside_media_folder(self):
        token, _ = self.pair()
        for name in ("../secret.mp4", "/etc/passwd", "..", ".hidden.mp4", "notes.txt", "a/b.mp4",
                     "a.mp4\x00", "", 5, None, "link.mp4"):
            st, _, _ = self.call("POST", "/api/play", {"file": name}, token=token)
            self.assertIn(st, (400, 404), repr(name))
        self.assertEqual(self.player.calls, [])
        st, body, _ = self.call("POST", "/api/play", {"file": "a.mp4"}, token=token)
        self.assertEqual((st, body["playing"]), (200, "a.mp4"))
        kind, paths, loop, spawn = next(c for c in self.player.calls if c[0] == "play")
        self.assertEqual(paths, [os.path.realpath(os.path.join(self.media, "a.mp4"))])
        self.assertFalse(spawn)

    def test_controls_validate_numbers(self):
        token, _ = self.pair()
        ok = [("opacity", 50), ("size", 100), ("position", -50), ("speed", 1.5), ("volume", 100), ("seek", -5),
              ("rotate", 90)]
        for action, value in ok:
            self.assertEqual(self.call("POST", "/api/control", {"action": action, "value": value}, token=token)[0],
                             200, action)
        bad = [("opacity", 101), ("opacity", "50"), ("opacity", True), ("opacity", None), ("size", 201),
               ("speed", 0), ("volume", -1), ("seek", 99999), ("rotate", 45), ("loop", 1), ("nonsense", 1)]
        for action, value in bad:
            st, _, _ = self.call("POST", "/api/control", {"action": action, "value": value}, token=token)
            self.assertEqual(st, 400, (action, value))
        st, _, _ = self.call("POST", "/api/control", token=token, raw=b'{"action":"opacity","value":NaN}')
        self.assertEqual(st, 400)

    def test_stop_volume_step_and_legacy_presets(self):
        token, _ = self.pair()
        self.assertEqual(self.call("POST", "/api/control", {"action": "stop"}, token=token)[0], 200)
        self.assertEqual(self.player.calls[-1], ("clear",))
        self.assertEqual(self.call("POST", "/api/control", {"action": "volume_step", "value": -10}, token=token)[0], 200)
        self.assertEqual(self.player.calls[-1], ("volume_step", -10.0))
        self.assertEqual(self.call("POST", "/api/control", {"action": "volume_step", "value": 99}, token=token)[0], 400)
        for n in ("05_intro.mp4", "05_outro.mov", "07_other.mp4"):
            open(os.path.join(self.media, n), "w").close()
        st, body, _ = self.call("POST", "/api/play", {"preset": "startlessonce05"}, token=token)
        self.assertEqual((st, body["files"]), (200, 2))
        kind, paths, loop, spawn = next(c for c in reversed(self.player.calls) if c[0] == "play")
        self.assertFalse(loop)  # "once" presets do not loop
        self.assertEqual([os.path.basename(p) for p in paths], ["05_intro.mp4", "05_outro.mov"])
        st, body, _ = self.call("POST", "/api/play", {"preset": "startless07"}, token=token)
        self.assertEqual(st, 200)
        self.assertTrue(next(c for c in reversed(self.player.calls) if c[0] == "play")[2])  # loops
        for bad in ("startless99", "startmaster05; reboot", "../startless01", "reboot", "startslave", 5, None, ""):
            self.assertEqual(self.call("POST", "/api/play", {"preset": bad}, token=token)[0], 400, repr(bad))

    def test_osc_settings_endpoint(self):
        import socket
        full, _ = self.pair()
        _, body, _ = self.call("POST", "/api/devices/invite", {"name": "tech", "role": "live"}, token=full)
        live = body["token"]
        st, body, _ = self.call("GET", "/api/osc", token=live)
        self.assertEqual((st, body["enabled"], body["listening"]), (200, False, False))
        self.assertEqual(self.call("POST", "/api/osc", {"enabled": True}, token=live)[0], 403)
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        st, body, _ = self.call("POST", "/api/osc", {"enabled": True, "port": port, "allow": ["10.20.0.0/16"]}, token=full)
        self.assertEqual((st, body["listening"], body["allow"]), (200, True, ["10.20.0.0/16"]))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"/pvj/speed\0\0,f\0\0" + struct.pack(">f", 3.0), ("127.0.0.1", port))
            deadline = time.time() + 5
            while time.time() < deadline and ("speed", 3.0) not in self.player.calls:
                time.sleep(0.05)
        self.assertIn(("speed", 3.0), self.player.calls)
        from pvj.settings import Settings as S2
        self.assertTrue(S2(self.settings.path).load()["osc"]["enabled"])  # persisted
        st, body, _ = self.call("POST", "/api/osc", {"enabled": False}, token=full)
        self.assertEqual((st, body["listening"]), (200, False))

    def test_osc_settings_validation_and_port_clash(self):
        import socket
        token, _ = self.pair()
        for body in ({"enabled": "yes"}, {"port": 80}, {"port": 70000}, {"port": "9876"}, {"port": True},
                     {"allow": ["0.0.0.0/0"]}, {"allow": ["::/0"]}, {"allow": ["8.0.0.0/7"]}, {"allow": "10.0.0.0/8"},
                     {"allow": ["nonsense"]}, {"allow": ["10.0.0.0/8"] * 17}):
            self.assertEqual(self.call("POST", "/api/osc", body, token=token)[0], 400, body)
        blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(blocker.close)
        blocker.bind(("127.0.0.1", 0))
        st, body, _ = self.call("POST", "/api/osc", {"enabled": True, "port": blocker.getsockname()[1]}, token=token)
        self.assertEqual(st, 409)
        self.assertFalse(self.settings.data["osc"]["enabled"])  # last working configuration kept
        self.assertFalse(self.call("GET", "/api/osc", token=token)[1]["listening"])

    def test_pads_validation_and_playing_a_pad(self):
        token, _ = self.pair()
        self.assertEqual(self.call("POST", "/api/pads", {"bank": 0, "index": 1, "label": "Tunnel", "file": "b.mov"},
                                   token=token)[0], 200)
        for body in ({"bank": 9, "index": 0}, {"bank": 0, "index": 12}, {"bank": 0, "index": 0, "file": "../x.mp4"},
                     {"bank": 0, "index": 0, "label": "x" * 41}, {"bank": 0, "index": 0, "file": "x.exe"}):
            self.assertEqual(self.call("POST", "/api/pads", body, token=token)[0], 400, body)
        self.assertEqual(self.call("POST", "/api/play", {"pad": [0, 1]}, token=token)[0], 200)
        self.assertEqual(self.call("POST", "/api/play", {"pad": [0, 5]}, token=token)[0], 400)  # empty pad
        self.assertEqual(self.call("POST", "/api/play", {"pad": [7, 1]}, token=token)[0], 400)
        self.assertEqual(self.call("POST", "/api/play", {"pad": "x"}, token=token)[0], 400)

    def test_blackout_and_opacity_interaction(self):
        token, _ = self.pair()
        self.call("POST", "/api/control", {"action": "opacity", "value": 40}, token=token)
        self.call("POST", "/api/blackout", {"on": True}, token=token)
        self.assertEqual(self.player.calls[-1], ("opacity", 0))
        self.call("POST", "/api/control", {"action": "opacity", "value": 80}, token=token)
        self.assertEqual(self.player.calls[-1], ("opacity", 0))  # blackout still holds
        self.call("POST", "/api/blackout", {"on": False}, token=token)
        self.assertEqual(self.player.calls[-1], ("opacity", 204))
        self.assertEqual(self.call("POST", "/api/blackout", {"on": "yes"}, token=token)[0], 400)

    def test_unexpected_error_is_a_clean_500_and_the_server_keeps_working(self):
        token, _ = self.pair()

        def boom(*a, **k):
            raise RuntimeError("bug")
        self.player.status = boom
        st, body, _ = self.call("GET", "/api/status", token=token)
        self.assertEqual((st, body), (500, {"error": "internal error"}))
        del self.player.status  # back to the class method
        self.assertEqual(self.call("GET", "/api/status", token=token)[0], 200)

    def test_player_down_is_503_not_a_crash(self):
        token, _ = self.pair()
        from pvj.player import PlayerError

        def boom(*a, **k):
            raise PlayerError("player service is not running")
        self.player.play = boom
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=token)[0], 503)

    # --- modules, themes, pin -----------------------------------------
    def test_modules_and_theme(self):
        token, _ = self.pair()
        st, body, _ = self.call("GET", "/api/modules", token=token)
        self.assertTrue(any(m["id"] == "inputs-ndi" for m in body["modules"]))
        self.assertEqual(self.call("POST", "/api/modules/core", {"enabled": False}, token=token)[0], 409)
        self.assertEqual(self.call("POST", "/api/modules/inputs-ndi", {"enabled": True}, token=token)[0], 409)
        self.assertEqual(self.call("POST", "/api/modules/Bad..Id", {"enabled": True}, token=token)[0], 404)
        self.assertEqual(self.call("POST", "/api/theme", {"name": "night-red", "accent": "#ffffff"}, token=token)[0], 200)
        st, css, r = self.call("GET", "/theme.css")
        self.assertIn(b"--ac:#ffffff", css)
        self.assertIn(b"--on:#000000", css)
        for body in ({"name": "nope"}, {"name": "light", "accent": "red"},
                     {"name": "light", "accent": "#fff;}*{display:none"}):
            self.assertEqual(self.call("POST", "/api/theme", body, token=token)[0], 400, body)

    def test_pin_rotation_writes_pin_file_and_invalidates_old_pin(self):
        token, _ = self.pair()
        old = self.pin
        st, body, _ = self.call("POST", "/api/pin/rotate", {}, token=token)
        self.assertEqual(st, 200)
        with open(os.path.join(self.rundir, "pin")) as f:
            self.assertEqual(f.read().strip(), body["pin"])
        self.assertEqual(oct(os.stat(os.path.join(self.rundir, "pin")).st_mode & 0o777), "0o640")
        if body["pin"] != old:
            self.assertEqual(self.call("POST", "/api/pair", {"pin": old})[0], 403)
        self.assertEqual(self.call("GET", "/api/status", token=token)[0], 200)  # paired device stays paired

    # --- static files and headers -------------------------------------
    def test_static_files_and_security_headers(self):
        st, body, r = self.call("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(self.call("GET", "/app.js")[0], 200)
        for path in ("/../settings.json", "/%2e%2e/settings.json", "/settings.json", "/etc/passwd", "/.env"):
            self.assertEqual(self.call("GET", path)[0], 404, path)
        st, _, r = self.call("GET", "/api/hello")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")

    def test_settings_file_holds_no_clear_tokens_or_pin(self):
        token, _ = self.pair()
        with open(self.settings.path) as f:
            raw = f.read()
        self.assertNotIn(token, raw)


if __name__ == "__main__":
    unittest.main()


def make_test_video(directory):
    """A 2 second clip made by mpv itself; None if this mpv cannot encode."""
    import shutil
    import subprocess
    if not shutil.which("mpv"):
        return None
    path = os.path.join(directory, "clip.mkv")
    subprocess.run(["mpv", "av://lavfi:testsrc=size=160x120:rate=25", "--length=2", "--o=" + path, "--no-terminal"],
                   capture_output=True, timeout=60)
    return path if os.path.isfile(path) and os.path.getsize(path) > 1000 else None


class EndToEndTest(ServerBase):
    """Same server, real headless mpv behind it, real video file."""

    def setUp(self):
        super().setUp()
        clip = make_test_video(self.media)
        if not clip:
            self.skipTest("cannot generate a test video with this mpv")
        from pvj.player import Player
        real = Player(extra_args=["--vo=null", "--ao=null"], rundir=self.rundir)
        self.addCleanup(real.stop)
        self.api.player = real
        self.api.spawn = True  # development mode: the API starts mpv itself

    def test_play_through_http_reaches_real_mpv(self):
        token, _ = self.pair()
        st, body, _ = self.call("POST", "/api/play", {"file": "clip.mkv"}, token=token)
        self.assertEqual(st, 200, body)
        st, status, _ = self.call("GET", "/api/status", token=token)
        self.assertTrue(status["player"]["running"])
        self.assertTrue(status["player"]["path"].endswith("clip.mkv"))
        self.call("POST", "/api/control", {"action": "speed", "value": 2}, token=token)
        self.call("POST", "/api/control", {"action": "mute", "value": True}, token=token)
        self.call("POST", "/api/blackout", {"on": True}, token=token)
        st, status, _ = self.call("GET", "/api/status", token=token)
        self.assertEqual(status["player"]["speed"], 2.0)
        self.assertTrue(status["player"]["muted"])
        self.assertEqual(self.api.player.ipc.request("get_property", "brightness"), -100)
        self.call("POST", "/api/blackout", {"on": False}, token=token)
        self.assertEqual(self.api.player.ipc.request("get_property", "brightness"), 0)
        self.assertEqual(self.call("POST", "/api/player/restart", {}, token=token)[0], 200)
