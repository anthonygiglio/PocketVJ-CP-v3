# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest

from pvj import auth as auth_mod, pinscreen
from pvj.auth import Auth, AuthError
from pvj.player import PlayerError
from pvj.settings import Settings
from tests.test_pinscreen import Api as PinApi, Auth as PinAuth, Player as PinPlayer
from tests.test_server import ServerBase


class JoinCodeTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        self.t = [1000.0]
        self.a = Auth(self.settings, clock=lambda: self.t[0], rotate_on_start=True)

    def test_a_code_pairs_a_guest_or_presenter_but_never_full_access(self):
        view, live = self.a.create_join("view"), self.a.create_join("live")
        self.assertEqual(len(view), 6)
        _, d1 = self.a.pair(view, "guest phone", "c1")
        _, d2 = self.a.pair(live, "presenter", "c2")
        self.assertEqual((d1["role"], d2["role"]), ("view", "live"))
        _, d3 = self.a.pair(self.a.current_pin, "owner", "c3")
        self.assertEqual(d3["role"], "full")                                  # the box's own PIN still gives full access

    def test_only_view_and_live_codes_exist(self):
        for role in ("full", "admin", "", None, 5):
            with self.assertRaises(AuthError):
                self.a.create_join(role)

    def test_codes_expire(self):
        code = self.a.create_join("view", minutes=2)
        self.t[0] += 119
        self.assertEqual(self.a.pair(code, "x", "c")[1]["role"], "view")
        self.t[0] += 2
        with self.assertRaises(AuthError):
            self.a.pair(code, "late", "c2")
        self.assertEqual(self.a.list_joins(), [])

    def test_codes_have_a_use_limit(self):
        code = self.a.create_join("live", uses=2)
        self.a.pair(code, "a", "c1")
        self.a.pair(code, "b", "c2")
        with self.assertRaises(AuthError):
            self.a.pair(code, "c", "c3")

    def test_a_new_code_for_a_role_replaces_the_old_one_and_the_total_is_capped(self):
        first = self.a.create_join("view")
        second = self.a.create_join("view")
        with self.assertRaises(AuthError):
            self.a.pair(first, "x", "c")
        self.assertEqual(self.a.pair(second, "x", "c2")[1]["role"], "view")
        self.assertEqual(len(self.a.list_joins()), 1)

    def test_wrong_codes_are_throttled_like_wrong_pins(self):
        self.a.create_join("view")
        for _ in range(auth_mod.PER_CLIENT_FAILS):
            with self.assertRaises(AuthError):
                self.a.pair("000000" if self.a.list_joins()[0]["code"] != "000000" else "000001", "x", "guesser")
        with self.assertRaises(AuthError) as cm:
            self.a.pair(self.a.list_joins()[0]["code"], "x", "guesser")       # even the right code, from a locked-out client
        self.assertTrue(cm.exception.retry_after)

    def test_bad_input(self):
        for kw in ({"minutes": 0}, {"minutes": 121}, {"minutes": True}, {"minutes": "5"}, {"minutes": 1.5}, {"uses": 0}, {"uses": 51}, {"uses": True}):
            with self.assertRaises(AuthError, msg=str(kw)):
                self.a.create_join("view", **kw)
        for given in (None, 5, "12345", "1234567", "abcdef", "12345\n", " 123456"):
            with self.assertRaises(AuthError):
                self.a.pair(given, "x", "c")

    def test_cancel_one_or_all_and_they_never_reach_the_settings_file(self):
        v, l = self.a.create_join("view"), self.a.create_join("live")
        self.assertTrue(self.a.cancel_join(v))
        self.assertFalse(self.a.cancel_join(v))
        with open(self.settings.path) as f:
            text = f.read()
        self.assertNotIn(l, text)                                              # memory only
        self.assertTrue(self.a.cancel_join(None))
        self.assertEqual(self.a.list_joins(), [])


class ManualDisplayTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        self.settings.load()
        self.t = [50.0]
        self.auth = Auth(self.settings, clock=lambda: self.t[0], rotate_on_start=True)
        self.api = PinApi()
        self.p = pinscreen.PinScreen(self.api, self.auth, log=lambda *_: None, hostname="nxlx-mastercontrol", clock=lambda: self.t[0])

    def text(self):
        return self.api.player.shown[-1][1]

    def test_shows_the_chosen_items_even_over_a_playing_clip(self):
        self.api.player.st = {"running": True, "path": "/media/a.mp4"}
        st = self.p.show(["pin", "view", "live"], 60)
        self.assertTrue(st["showing"])
        text = self.text()
        self.assertIn("Full access PIN  " + self.auth.current_pin, text)
        codes = {j["role"]: j["code"] for j in self.auth.list_joins()}
        self.assertIn("Guest, watch only, code  " + codes["view"], text)
        self.assertIn("Presenter, play and mix, code  " + codes["live"], text)
        self.assertIn("http://nxlx-mastercontrol.local/", text)
        self.assertIn("Hides in 60 s", text)
        self.assertIn("guest QR on the left, presenter QR on the right", text)

    def test_only_what_was_asked_for_is_shown(self):
        self.p.show(["view"], 30)
        text = self.text()
        self.assertIn("Guest", text)
        self.assertNotIn("Full access PIN", text)
        self.assertNotIn("Presenter", text)

    def test_it_hides_by_itself_and_clears_the_text(self):
        self.auth._add_device("owner", "full")            # a paired box: the automatic first-run PIN is off, so nothing redraws it
        self.p.show(["pin"], 20)
        self.t[0] += 21
        self.p.tick()
        self.assertEqual(self.api.player.shown[-1][:2], ("show-text", ""))
        self.assertFalse(self.p.status()["showing"])

    def test_hide_now(self):
        self.auth._add_device("owner", "full")
        self.p.show(["pin"], 600)
        self.p.hide()
        self.assertFalse(self.p.status()["showing"])
        self.assertEqual(self.api.player.shown[-1][:2], ("show-text", ""))

    def test_bad_requests(self):
        for items in ([], None, "pin", ["full"], ["pin", "pin"], ["pin", "view", "live", "pin"], [1]):
            with self.assertRaises(ValueError, msg=str(items)):
                self.p.show(items, 60)
        for seconds in (0, 9, 3601, True, "60", 1.5):
            with self.assertRaises(ValueError, msg=str(seconds)):
                self.p.show(["pin"], seconds)

    def test_a_dead_player_does_not_raise(self):
        self.api.player.down = True
        self.p.show(["pin"], 60)                                                # must not raise
        self.assertTrue(self.p.status()["showing"])


class AccessApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.shown = []

        class Ipc:
            def request(inner, *cmd):
                self.shown.append(cmd)
        self.player.ipc = Ipc()
        self.api.pinscreen = pinscreen.PinScreen(self.api, self.auth, log=lambda *_: None, hostname="box")

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def test_make_show_cancel(self):
        st, body, _ = self.post("/api/access/code", {"role": "view", "minutes": 30})
        self.assertEqual((st, body["codes"][0]["role"], body["codes"][0]["seconds_left"] > 1700), (200, "view", True))
        code = body["codes"][0]["code"]
        st, body, _ = self.post("/api/access/screen", {"show": True, "items": ["pin", "view", "live"], "seconds": 90})
        self.assertEqual((st, body["screen"]["showing"], sorted(c["role"] for c in body["codes"])), (200, True, ["live", "view"]))
        self.assertIn("show-text", self.shown[-1][0])
        st, body, _ = self.post("/api/access/screen", {"show": False})
        self.assertEqual((st, body["screen"]["showing"]), (200, False))
        self.assertEqual(self.post("/api/access/cancel", {"code": code})[0], 200)
        self.assertEqual(self.post("/api/access/cancel", {"code": code})[0], 404)
        self.assertEqual(self.post("/api/access/cancel", {"all": True})[1]["codes"], [])

    def test_a_guest_can_join_with_the_code_and_is_view_only(self):
        code = self.post("/api/access/code", {"role": "view"})[1]["codes"][0]["code"]
        st, body, _ = self.call("POST", "/api/pair", {"pin": code, "name": "guest"})
        self.assertEqual((st, body["device"]["role"]), (200, "view"))
        self.assertEqual(self.call("POST", "/api/play", {"file": "a.mp4"}, token=body["token"])[0], 403)
        self.assertEqual(self.call("GET", "/api/access", token=body["token"])[0], 403)

    def test_bad_input_and_only_full_devices(self):
        for path, body in (("/api/access/code", {"role": "full"}), ("/api/access/code", {"role": "view", "minutes": 999}),
                           ("/api/access/screen", {"show": "yes"}), ("/api/access/screen", {"show": True, "items": ["x"]}),
                           ("/api/access/screen", {"show": True, "items": ["pin"], "seconds": 1})):
            self.assertEqual(self.post(path, body)[0], 400, (path, body))
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        for path, body in (("/api/access/code", {"role": "view"}), ("/api/access/screen", {"show": False}), ("/api/access/cancel", {"all": True})):
            self.assertEqual(self.post(path, body, token=live)[0], 403, path)
        self.assertEqual(self.call("GET", "/api/access", token=live)[0], 403)
        self.assertEqual(self.call("GET", "/api/access")[0], 401)


class QrEndpointTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def test_panel_and_code_qr_codes_are_svg_for_full_devices_only(self):
        st, body, resp = self.call("GET", "/api/qr.svg?for=panel", token=self.full)
        self.assertEqual((st, resp.getheader("Content-Type")), (200, "image/svg+xml"))
        self.assertTrue(body.startswith(b"<svg"))
        self.assertEqual(self.call("GET", "/api/qr.svg?for=view", token=self.full)[0], 404)          # no code yet
        self.call("POST", "/api/access/code", {"role": "view"}, token=self.full)
        self.assertEqual(self.call("GET", "/api/qr.svg?for=view", token=self.full)[0], 200)
        for target in ("pin", "full", "", "../x"):
            self.assertEqual(self.call("GET", "/api/qr.svg?for=" + target, token=self.full)[0], 400, target)
        live = self.call("POST", "/api/devices/invite", {"name": "g", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.call("GET", "/api/qr.svg?for=panel", token=live)[0], 403)
        self.assertEqual(self.call("GET", "/api/qr.svg?for=panel")[0], 401)

    def test_a_bad_host_header_cannot_be_put_into_a_qr_code(self):
        st, _, _ = self.call("GET", "/api/qr.svg?for=panel", token=self.full, headers={"Host": "evil.example/<script>"})
        self.assertEqual(st, 400)

    @unittest.skipUnless(__import__("shutil").which("zbarimg"), "needs zbar")
    def test_the_code_qr_decodes_to_the_join_link(self):
        import subprocess
        from pvj import qr
        code = self.call("POST", "/api/access/code", {"role": "live"}, token=self.full)[1]["codes"][0]["code"]
        text = "http://127.0.0.1:%d/#code=%s" % (self.port, code)
        path = os.path.join(self.tmp, "c.png")
        with open(path, "wb") as f:
            f.write(qr.png(qr.encode(text)))
        out = subprocess.run(["zbarimg", "--quiet", "--raw", path], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, text)

    def test_a_new_guest_link_comes_with_its_qr_code(self):
        st, body, _ = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view", "origin": "http://192.168.0.5"}, token=self.full)
        self.assertEqual(st, 200)
        self.assertTrue(body["qr_svg"].startswith("<svg"))
        st, body, _ = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view", "origin": "javascript:alert(1)"}, token=self.full)
        self.assertNotIn("qr_svg", body)


class OverlayTest(unittest.TestCase):
    def test_the_qr_code_is_drawn_in_the_top_right_and_removed_when_the_first_device_pairs(self):
        settings = Settings(os.path.join(tempfile.mkdtemp(), "s.json"))
        settings.load()
        a = Auth(settings, rotate_on_start=True)
        api = PinApi()
        drawn = []
        api.player.osd_size = lambda: (2560, 1440)
        api.player.overlay = lambda oid, x, y, w, h, px: drawn.append(("add", oid, x, y, w, h, len(px)))
        api.player.overlay_remove = lambda oid: drawn.append(("remove", oid))
        p = pinscreen.PinScreen(api, a, log=lambda *_: None, hostname="box")
        self.assertTrue(p.tick())
        adds = [d for d in drawn if d[0] == "add"]
        self.assertEqual(len(adds), 1)
        _, oid, x, y, w, h, n = adds[0]
        self.assertEqual((oid, y, w, h, n), (pinscreen.QR_IDS["pin"], 40, w, w, w * w * 4))
        self.assertEqual(x + w, 2560 - 40)                       # right edge, with a margin
        self.assertGreater(w, 200)                                 # big enough to scan from across a room
        p.tick()
        self.assertEqual(len([d for d in drawn if d[0] == "add"]), 1)    # unchanged: not redrawn every tick
        a._add_device("owner", "full")
        p.tick()
        self.assertIn(("remove", pinscreen.QR_IDS["pin"]), drawn)


if __name__ == "__main__":
    unittest.main()
