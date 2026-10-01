# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from pvj import probe
from tests.test_server import ServerBase, make_test_video


class ParseTest(unittest.TestCase):
    def test_a_film_and_a_file_with_missing_values(self):
        self.assertEqual(probe.parse("PVJINFO|h264|1920|1080|23.976025|5833.642|aac|mov,mp4,m4a,3gp,3g2,mj2"),
                         {"codec": "h264", "width": 1920, "height": 1080, "fps": 23.976, "duration": 5833.64, "audio": "aac",
                          "container": "mov,mp4,m4a,3gp,3g2,mj2"})
        self.assertEqual(probe.parse("PVJINFO|||||2.5|opus|ogg"),
                         {"codec": None, "width": None, "height": None, "fps": None, "duration": 2.5, "audio": "opus", "container": "ogg"})
        self.assertEqual(probe.parse("PVJINFO|h264|1280|720|(unavailable)|19|(unavailable)|mkv")["audio"], None)
        with self.assertRaises(probe.ProbeError):
            probe.parse("PVJINFO|too|few")


@unittest.skipUnless(shutil.which("mpv"), "needs mpv")
class RealProbeTest(unittest.TestCase):
    def test_reads_a_real_clip_and_caches_it(self):
        clip = make_test_video(tempfile.mkdtemp())
        if not clip:
            self.skipTest("this mpv cannot encode a test clip")
        info = probe.probe(clip)
        self.assertEqual((info["width"], info["height"], info["container"]), (160, 120, "mkv"))
        self.assertTrue(1.5 <= info["duration"] <= 2.5)
        with mock.patch("pvj.probe.subprocess.run", side_effect=AssertionError("must come from the cache")):
            self.assertEqual(probe.probe(clip), info)

    def test_a_text_file_named_like_a_clip_is_refused(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "fake.mp4")
        with open(p, "w") as f:
            f.write("#EXTM3U\n/etc/passwd\n")
        with self.assertRaises(probe.ProbeError):
            probe.probe(p)


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.full)[1]["token"]

    def test_clip_info_for_a_view_device_and_only_media_files(self):
        with mock.patch("pvj.probe.probe", return_value={"codec": "h264", "width": 1, "height": 1, "fps": None, "duration": 1.0, "audio": None, "container": "mp4"}) as p:
            st, body, _ = self.call("POST", "/api/media/info", {"name": "a.mp4"}, token=self.view)
        self.assertEqual((st, body["codec"]), (200, "h264"))
        self.assertTrue(p.call_args[0][0].endswith("/a.mp4"))
        for bad in ({"name": "../secret.mp4"}, {"name": "notes.txt"}, {"name": "link.mp4"}, {"usb": "../x/y.mp4"}):
            self.assertIn(self.call("POST", "/api/media/info", bad, token=self.view)[0], (400, 404, 409), bad)
        with mock.patch("pvj.probe.probe", side_effect=probe.ProbeError("the player could not open this file")):
            self.assertEqual(self.call("POST", "/api/media/info", {"name": "a.mp4"}, token=self.view)[0], 422)

    def test_system_info(self):
        with mock.patch("pvj.api.hardware.drm_connectors", return_value=[
                {"connector": "HDMI-A-1", "status": "connected", "modes": ["2560x1440", "2560x1440", "1920x1080"]},
                {"connector": "HDMI-A-2", "status": "disconnected", "modes": []}]):
            st, body, _ = self.call("GET", "/api/system", token=self.view)
        self.assertEqual(st, 200)
        self.assertEqual(body["screens"][0], {"connector": "HDMI-A-1", "connected": True, "modes": ["2560x1440", "1920x1080"]})
        self.assertTrue(body["disk"]["total"] > 0 and body["version"])
        self.assertEqual(body["output"], {"width": 1920, "height": 1080, "refresh": None})

    def test_test_tones(self):
        for ch in ("left", "right", "both"):
            self.assertEqual(self.call("POST", "/api/testtone", {"channel": ch}, token=self.full)[0], 200)
        self.assertTrue(self.player.plays[-1]["paths"][0].startswith("av://lavfi:aevalsrc="))
        self.assertEqual(self.player.plays[-1]["ending"], "stop")
        self.assertEqual(self.call("POST", "/api/testtone", {"channel": "center"}, token=self.full)[0], 400)
        self.assertEqual(self.call("POST", "/api/testtone", {"channel": "left"}, token=self.view)[0], 403)
        self.player.status = lambda: {"running": True, "path": self.player.TEST_TONES["right"]}
        p = self.call("GET", "/api/status", token=self.view)[1]["player"]
        self.assertEqual((p["path"], p["test_tone"]), (None, "right"))

class ClipAdviceTest(unittest.TestCase):
    """Warnings only where measured or plainly beyond the board; untested cases say so."""

    def test_the_measured_case_has_no_warning(self):
        from pvj import probe
        self.assertEqual(probe.advice({"codec": "h264", "width": 1920, "height": 1080, "fps": 23.976}, "pi4"), [])

    def test_too_heavy_or_untested_cases_say_so(self):
        from pvj import probe
        a = probe.advice({"codec": "h264", "width": 3840, "height": 2160, "fps": 30}, "pi4")
        self.assertIn("will very likely stutter", a[0])
        self.assertIn("not tested", probe.advice({"codec": "h264", "width": 1920, "height": 1080, "fps": 59.94}, "pi4")[0])
        self.assertIn("not tested", probe.advice({"codec": "hevc", "width": 3840, "height": 2160, "fps": 30}, "pi4")[0])
        self.assertIn("editing format", probe.advice({"codec": "prores", "width": 1920, "height": 1080, "fps": 25}, "pi4")[0])
        self.assertIn("Pi 3", probe.advice({"codec": "h264", "width": 1920, "height": 1080, "fps": 25}, "pi3")[0])
        self.assertEqual(probe.advice({"codec": None}, "pi4"), [])


if __name__ == "__main__":
    unittest.main()
