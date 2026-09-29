# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import unittest

from pvj import streams
from pvj.settings import Settings
from tests.test_server import ServerBase


class StreamUrlTest(unittest.TestCase):
    def test_good_addresses(self):
        for url in ("srt://192.168.1.20:9000", "srt://host.local:9000?mode=caller&latency=120", "rtsp://cam/stream1",
                    "rtsp://admin:s3cret@10.0.0.5:554/h264", "rtmp://[::1]/live/key", "RTMPS://example.com/app/x", "rtsps://cam.lan"):
            self.assertEqual(streams.valid_url(url), url)

    def test_bad_addresses(self):
        bad = ["", None, 5, "http://example.com/a.m3u8", "https://example.com", "file:///etc/passwd", "edl://a.mp4", "ytdl://x",
               "lavf://x", "udp://1.2.3.4:5", "rtsp://", "rtsp:///path", "srt://host:99999", "srt://host:0", "rtsp://ho st/x",
               "rtsp://host/a b", "rtsp://host/\nfile:///etc/passwd", "rtsp://host\\@evil/x", "rtsp://exa;mple.com/x",
               "rtsp://" + "a" * 600, "/tmp/video.mp4", "rtsp:host", "srt://host:abc"]
        for url in bad:
            with self.assertRaises(streams.StreamError, msg=repr(url)[:60]):
                streams.valid_url(url)

    def test_redact_hides_login_everywhere(self):
        self.assertEqual(streams.redact("rtsp://admin:s3cret@10.0.0.5:554/h264"), "rtsp://***@10.0.0.5:554/h264")
        self.assertEqual(streams.redact("rtsp://a@b:c@host/x"), "rtsp://***@host/x")
        self.assertEqual(streams.redact("rtsp://cam/stream"), "rtsp://cam/stream")
        self.assertEqual(streams.redact("/media/usb/a.mp4"), "/media/usb/a.mp4")
        self.assertEqual(streams.redact(None), None)

    def test_names(self):
        self.assertEqual(streams.clean_name("  Stage cam "), "Stage cam")
        for bad in ("", "   ", "x" * 41, "a\nb", None, 7):
            with self.assertRaises(streams.StreamError):
                streams.clean_name(bad)

    def test_validate_saved(self):
        e = streams.new_entry("A", "srt://h:1")
        self.assertEqual(streams.validate_saved([e]), [e])
        for bad in ("x", [e, e], [{"id": "zz", "name": "A", "url": "srt://h:1"}], [dict(e, id="abcd1234\n")], [dict(e, url="file:///x")],
                    [streams.new_entry("A", "srt://h:1")] * 25):
            with self.assertRaises(streams.StreamError):
                streams.validate_saved(bad)


class StreamApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def invite(self, role):
        return self.call("POST", "/api/devices/invite", {"name": "g", "role": role}, token=self.full)[1]["token"]

    def enable(self):
        self.call("POST", "/api/modules/inputs-srt", {"enabled": True}, token=self.full)

    def add(self, name="Cam", url="rtsp://admin:pw@10.0.0.5/live"):
        return self.call("POST", "/api/streams", {"action": "add", "name": name, "url": url}, token=self.full)

    def test_module_gate(self):
        self.assertEqual(self.call("GET", "/api/streams", token=self.full)[0], 409)
        self.assertEqual(self.add()[0], 409)
        self.assertEqual(self.call("POST", "/api/play", {"stream": "abcd1234"}, token=self.full)[0], 409)

    def test_add_list_hides_login_and_persists(self):
        self.enable()
        st, body, _ = self.add()
        self.assertEqual(st, 200)
        s = body["streams"][0]
        self.assertEqual((s["url"], s["has_login"]), ("rtsp://***@10.0.0.5/live", True))
        self.assertNotIn("pw", str(self.call("GET", "/api/streams", token=self.invite("view"))[1]))
        self.assertEqual(Settings(self.settings.path).load()["streams"][0]["url"], "rtsp://admin:pw@10.0.0.5/live")

    def test_bad_add_and_remove(self):
        self.enable()
        for body in ({"action": "add", "name": "x", "url": "file:///etc/passwd"}, {"action": "add", "name": "", "url": "srt://h:1"},
                     {"action": "nope"}, {}):
            self.assertEqual(self.call("POST", "/api/streams", body, token=self.full)[0], 400, body)
        self.assertEqual(self.call("POST", "/api/streams", {"action": "remove", "id": "abcd1234"}, token=self.full)[0], 404)
        sid = self.add()[1]["streams"][0]["id"]
        st, body, _ = self.call("POST", "/api/streams", {"action": "remove", "id": sid}, token=self.full)
        self.assertEqual((st, body["streams"]), (200, []))
        self.assertEqual(self.settings.data["streams"], [])

    def test_limit(self):
        self.enable()
        for i in range(streams.MAX_STREAMS):
            self.assertEqual(self.add("s%d" % i, "srt://h:%d" % (1000 + i))[0], 200)
        self.assertEqual(self.add("one more", "srt://h:9")[0], 400)

    def test_play_uses_the_saved_url_only_and_status_hides_login(self):
        self.enable()
        sid = self.add()[1]["streams"][0]["id"]
        live = self.invite("live")
        st, body, _ = self.call("POST", "/api/play", {"stream": sid}, token=live)
        self.assertEqual((st, body), (200, {"playing": "Cam"}))
        self.assertIn(("play", ["rtsp://admin:pw@10.0.0.5/live"], False, False), self.player.calls)
        before = len(self.player.calls)
        self.assertEqual(self.call("POST", "/api/play", {"stream": "rtsp://evil/x"}, token=live)[0], 404)  # no free-form URL
        self.assertEqual(self.call("POST", "/api/play", {"stream": ["a"]}, token=live)[0], 404)
        self.assertEqual(len(self.player.calls), before)
        self.player.status = lambda: {"running": True, "path": "rtsp://admin:pw@10.0.0.5/live"}
        p = self.call("GET", "/api/status", token=self.invite("view"))[1]["player"]
        self.assertEqual((p["path"], p["stream"]), ("rtsp://***@10.0.0.5/live", "Cam"))

    def test_roles(self):
        self.enable()
        view, live = self.invite("view"), self.invite("live")
        for token in (view, live):
            self.assertEqual(self.call("POST", "/api/streams", {"action": "add", "name": "x", "url": "srt://h:1"}, token=token)[0], 403)
        sid = self.add()[1]["streams"][0]["id"]
        self.assertEqual(self.call("POST", "/api/play", {"stream": sid}, token=view)[0], 403)


if __name__ == "__main__":
    unittest.main()
