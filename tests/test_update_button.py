# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The update button: the updater's inbox and progress file, pvj-sysd's update command, the API and the units."""
import json
import os
import tempfile
import unittest
from unittest import mock

from pvj import sysd, update
from tests.test_server import ServerBase

REPO = os.path.join(os.path.dirname(__file__), "..")


class InboxTest(unittest.TestCase):
    def test_newest_first_links_ignored_and_cleared_after_one_try(self):
        root = tempfile.mkdtemp()
        inbox = os.path.join(root, "var/lib/pvj/update-inbox")
        os.makedirs(inbox)
        for n in ("pvj-0.2.0.tar.gz", "pvj-0.10.0.tar.gz", "pvj-0.10.0.tar.gz.sig", "notes.txt", "pvj-x.tar.gz"):
            open(os.path.join(inbox, n), "w").close()
        os.symlink("/etc/passwd", os.path.join(inbox, "pvj-9.9.9.tar.gz"))
        u = update.Updater(root=root)
        self.assertEqual([os.path.basename(p) for p in u.inbox_bundles()], ["pvj-0.10.0.tar.gz", "pvj-0.2.0.tar.gz"])
        u.clear_inbox()
        self.assertEqual(sorted(os.listdir(inbox)), ["notes.txt"])

    def test_the_result_file_says_what_happened(self):
        result = os.path.join(tempfile.mkdtemp(), "result.json")

        class Empty:
            def inbox_bundles(self):
                return []

            def clear_inbox(self):
                pass
        with mock.patch.object(update, "Updater", Empty), mock.patch("os.geteuid", return_value=0):
            self.assertEqual(update.main(["inbox", "--result", result]), 1)
        with open(result) as f:
            data = json.load(f)
        self.assertEqual(data["state"], "failed")
        self.assertIn("upload one first", data["message"])


class SysdUpdateTest(unittest.TestCase):
    def service(self, active="inactive"):
        calls = []

        class R:
            def __init__(self, out):
                self.returncode, self.stdout, self.stderr = 0, out, ""

        def run(argv, **kw):
            calls.append(argv)
            return R(active if argv[1] == "is-active" else "")
        return sysd.SysService(runner=run, log=lambda *_: None), calls

    def test_starts_one_of_two_fixed_units(self):
        s, calls = self.service()
        self.assertTrue(s.handle({"cmd": "update", "source": "inbox"})["ok"])
        self.assertEqual(calls[-1], ["systemctl", "start", "--no-block", "pvj-update-inbox.service"])
        for bad in ("../../x", "usb; reboot", None, 1):
            self.assertFalse(s.handle({"cmd": "update", "source": bad})["ok"])

    def test_refuses_a_second_update_while_one_runs(self):
        s, calls = self.service(active="active")
        r = s.handle({"cmd": "update", "source": "usb"})
        self.assertFalse(r["ok"])
        self.assertIn("already running", r["error"])
        self.assertFalse(any(c[1] == "start" for c in calls))


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.asked = []

        class Sysd:
            def request(inner, message):
                self.asked.append(message)
                return {"ok": True}
        self.api.sysd = Sysd()

    def upload(self, name, data, token=None):
        return self.call("POST", "/api/system/update/upload?name=" + name, raw=data, token=token or self.full,
                         headers={"Content-Type": "application/octet-stream"})

    def test_upload_status_and_start(self):
        self.assertEqual(self.upload("pvj-0.2.0.tar.gz", b"x" * 1000)[0], 200)
        self.assertEqual(self.upload("pvj-0.2.0.tar.gz.sig", b"sig")[0], 200)
        st, body, _ = self.call("GET", "/api/system/update", token=self.full)
        self.assertEqual(body["inbox"], [{"version": "0.2.0", "signed": True}])
        self.upload("pvj-0.3.0.tar.gz", b"y" * 10)                       # a newer one replaces what was waiting
        self.assertEqual(self.call("GET", "/api/system/update", token=self.full)[1]["inbox"], [{"version": "0.3.0", "signed": False}])
        self.assertEqual(self.call("POST", "/api/system/update", {"source": "inbox"}, token=self.full)[0], 400)
        self.assertEqual(self.call("POST", "/api/system/update", {"source": "web", "confirm": "update"}, token=self.full)[0], 400)
        self.assertEqual(self.call("POST", "/api/system/update", {"source": "inbox", "confirm": "update"}, token=self.full)[0], 200)
        self.assertEqual(self.asked, [{"cmd": "update", "source": "inbox"}])

    def test_bad_uploads_and_roles(self):
        for name in ("evil.sh", "pvj-1.0.tar.gz", "../pvj-1.0.0.tar.gz", "pvj-1.0.0.tar.gz.exe"):
            self.assertEqual(self.upload(name, b"x")[0], 400, name)
        self.assertEqual(self.upload("pvj-1.0.0.tar.gz.sig", b"x" * (70 * 1024))[0], 413)
        live = self.call("POST", "/api/devices/invite", {"name": "p", "role": "live"}, token=self.full)[1]["token"]
        self.assertEqual(self.upload("pvj-1.0.0.tar.gz", b"x", token=live)[0], 403)
        self.assertEqual(self.call("GET", "/api/system/update", token=live)[0], 403)
        self.assertEqual(self.call("POST", "/api/system/update", {"source": "usb", "confirm": "update"}, token=live)[0], 403)


class UnitsTest(unittest.TestCase):
    def test_update_units_run_the_updater_on_request_only(self):
        for src in ("usb", "inbox"):
            with open(os.path.join(REPO, "install", "pvj-update-%s.service" % src)) as f:
                text = f.read()
            self.assertIn("pvj-update %s --result /run/pvj-update/result.json" % src, text)
            self.assertNotIn("\n[Install]", text)                      # never started at boot
            self.assertIn("RuntimeDirectoryPreserve=yes", text)        # the panel reads the outcome after the restart
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            self.assertIn("pvj-update-inbox.service", f.read())


if __name__ == "__main__":
    unittest.main()
