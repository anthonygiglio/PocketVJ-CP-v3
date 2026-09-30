# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import unittest

from tests.test_server import ServerBase


class UsbMediaTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.usb = os.path.join(self.tmp, "usbroot")
        drive = os.path.join(self.usb, "NXLX-USB")
        os.makedirs(os.path.join(drive, "System Volume Information"))
        os.makedirs(os.path.join(self.usb, "OTHER"))
        for n, size in (("Film One.mp4", 5000), ("b.mkv", 10), (".hidden.mp4", 1), ("notes.txt", 3)):
            with open(os.path.join(drive, n), "wb") as f:
                f.write(b"x" * size)
        with open(os.path.join(drive, "System Volume Information", "in.mp4"), "wb") as f:
            f.write(b"x")
        outside = os.path.join(self.tmp, "secret.mp4")
        with open(outside, "wb") as f:
            f.write(b"secret")
        os.symlink(outside, os.path.join(drive, "link.mp4"))
        os.symlink(drive, os.path.join(self.usb, "LINKED"))
        self.api.usb_root = self.usb
        self.api.usb_link = os.path.join(self.tmp, "usb")
        os.symlink(drive, self.api.usb_link)
        self.token = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]

    def play(self, **body):
        return self.call("POST", "/api/play", body, token=self.token)

    def test_lists_only_real_media_at_the_top_of_real_drives(self):
        st, body, _ = self.call("GET", "/api/media", token=self.token)
        self.assertEqual(st, 200)
        self.assertEqual([d["drive"] for d in body["usb"]], ["NXLX-USB", "OTHER"])   # the LINKED alias is skipped
        first = body["usb"][0]["files"]
        self.assertEqual([(f["name"], f["size"]) for f in first], [("b.mkv", 10), ("Film One.mp4", 5000)])   # no hidden, txt, link or subfolder files
        self.assertEqual(body["usb"][1]["files"], [])

    def test_plays_a_file_straight_from_the_drive(self):
        st, body, _ = self.play(usb="NXLX-USB/Film One.mp4")
        self.assertEqual((st, body), (200, {"playing": "NXLX-USB/Film One.mp4"}))
        real = os.path.join(os.path.realpath(self.usb), "NXLX-USB", "Film One.mp4")
        self.assertIn(("play", [real], True, False), self.player.calls)

    def test_nothing_outside_the_drive_can_be_played(self):
        for ref in ("../secret.mp4", "NXLX-USB/../../secret.mp4", "NXLX-USB/link.mp4", "LINKED/b.mkv", "NXLX-USB/System Volume Information/in.mp4",
                    "NXLX-USB/notes.txt", "NXLX-USB/.hidden.mp4", "NXLX-USB/nope.mp4", "/etc/passwd", "NXLX-USB", "NXLX-USB/", "", "a/b/c.mp4",
                    "NXLX-USB\\b.mkv", "NXLX-USB/b.mkv\n", "..", ".", None, 5, ["NXLX-USB/b.mkv"], "NXLX-USB/../NXLX-USB/b.mkv"):
            before = len(self.player.calls)
            st, body, _ = self.play(usb=ref)
            self.assertIn(st, (400, 404), ref)
            self.assertEqual(len(self.player.calls), before, ref)

    def test_roles(self):
        view = self.call("POST", "/api/devices/invite", {"name": "g", "role": "view"}, token=self.token)[1]["token"]
        self.assertEqual(self.call("GET", "/api/media", token=view)[0], 200)
        self.assertEqual(self.call("POST", "/api/play", {"usb": "NXLX-USB/b.mkv"}, token=view)[0], 403)

    def test_old_usb_presets_now_play_from_the_drive(self):
        st, body, _ = self.play(preset="startmasterusb")
        self.assertEqual((st, body["files"]), (200, 2))                      # b.mkv and Film One.mp4; not the link or the txt
        st, body, _ = self.play(preset="startmasterusb")
        self.assertEqual(st, 200)

    def test_the_usb_link_must_point_at_a_mounted_drive(self):
        os.unlink(self.api.usb_link)
        os.symlink(self.tmp, self.api.usb_link)                              # a link to somewhere else entirely
        before = len(self.player.calls)
        st, _, _ = self.play(preset="startmasterusb")
        self.assertEqual(st, 404)
        self.assertEqual(len(self.player.calls), before)

    def test_no_drive_is_an_empty_list_not_an_error(self):
        self.api.usb_root = os.path.join(self.tmp, "nowhere")
        st, body, _ = self.call("GET", "/api/media", token=self.token)
        self.assertEqual((st, body["usb"]), (200, []))
        self.assertEqual(self.play(usb="NXLX-USB/b.mkv")[0], 404)


if __name__ == "__main__":
    unittest.main()
