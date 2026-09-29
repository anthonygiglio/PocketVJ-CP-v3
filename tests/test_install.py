# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
"""Runs install/install.sh in --stage mode (no users, apt or systemctl)."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def copy_source(version):
    src = tempfile.mkdtemp()
    for d in ("pvj", "bin", "install"):
        shutil.copytree(os.path.join(REPO, d), os.path.join(src, d), ignore=shutil.ignore_patterns("__pycache__"))
    init = os.path.join(src, "pvj", "__init__.py")
    with open(init, "w") as f:
        f.write('__version__ = "%s"\n' % version)
    return src


def install(src, stage, *extra):
    return subprocess.run([os.path.join(src, "install", "install.sh"), "--stage", stage, "--user", "gigbox", *extra],
                          capture_output=True, text=True, timeout=60)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.stage = tempfile.mkdtemp()
        self.src = copy_source("9.9.1")

    @staticmethod
    def read(path):
        with open(path) as f:
            return f.read()

    def p(self, *parts):
        return os.path.join(self.stage, *parts)

    def test_fresh_install_layout(self):
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), "/opt/pvj/releases/9.9.1")
        self.assertTrue(os.path.isfile(self.p("opt/pvj/releases/9.9.1/pvj/player.py")))
        self.assertFalse(os.path.exists(self.p("opt/pvj/releases/9.9.1.new")))
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-player")), "/opt/pvj/current/bin/pvj-player")
        unit = self.read(self.p("etc/systemd/system/pvj-player.service"))
        self.assertIn("User=gigbox", unit)
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-player serve", unit)
        self.assertNotIn("@PVJ", unit)
        web = self.read(self.p("etc/systemd/system/pvj-web.service"))
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-web", web)
        self.assertIn("User=pvj-web", web)
        self.assertIn("NoNewPrivileges=yes", web)
        self.assertNotIn("@PVJ", web)
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-pin")), "/opt/pvj/current/bin/pvj-pin")
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-update")), "/opt/pvj/current/bin/pvj-update")
        self.assertIn("pvj-release", self.read(self.p("etc/pvj/allowed_signers")))
        self.assertIn("PVJ_MEDIA_DIR=/var/lib/pvj/video", self.read(self.p("etc/pvj/pvj.env")))
        self.assertIn("PVJ_USB_RW=0", self.read(self.p("etc/pvj/pvj.env")))
        usb_unit = self.read(self.p("etc/systemd/system/pvj-usb@.service"))
        self.assertIn("ExecStart=/opt/pvj/current/bin/pvj-usb mount /dev/%I", usb_unit)
        self.assertNotIn("@PVJ", usb_unit)
        self.assertIn("pvj-usb@%k.service", self.read(self.p("etc/udev/rules.d/99-pvj-usb.rules")))
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-usb")), "/opt/pvj/current/bin/pvj-usb")
        self.assertEqual(os.readlink(self.p("usr/local/bin/pvj-rootfs")), "/opt/pvj/current/bin/pvj-rootfs")
        self.assertEqual(json.loads(self.read(self.p("etc/pvj/install.json")))["version"], "9.9.1")
        self.assertTrue(os.path.isdir(self.p("var/lib/pvj/video")))

    def test_installed_copy_runs(self):
        install(self.src, self.stage)
        r = subprocess.run([self.p("opt/pvj/releases/9.9.1/bin/pvj-player"), "info"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("board", json.loads(r.stdout))

    def test_rerun_is_idempotent_and_keeps_edited_settings(self):
        install(self.src, self.stage)
        with open(self.p("etc/pvj/pvj.env"), "a") as f:
            f.write("PVJ_MEDIA_DIR=/mnt/mine\n")
        r = install(self.src, self.stage)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("keeping existing", r.stdout)
        self.assertIn("/mnt/mine", self.read(self.p("etc/pvj/pvj.env")))
        self.assertEqual(os.listdir(self.p("opt/pvj/releases")), ["9.9.1"])
        self.assertFalse(os.path.exists(self.p("opt/pvj/previous")))

    def test_upgrade_records_previous_release_for_rollback(self):
        install(self.src, self.stage)
        newer = copy_source("9.9.2")
        self.assertEqual(install(newer, self.stage).returncode, 0)
        self.assertEqual(os.readlink(self.p("opt/pvj/current")), "/opt/pvj/releases/9.9.2")
        self.assertEqual(self.read(self.p("opt/pvj/previous")).strip(), "/opt/pvj/releases/9.9.1")
        self.assertTrue(os.path.isdir(self.p("opt/pvj/releases/9.9.1")))

    def test_dry_run_changes_nothing(self):
        r = install(self.src, self.stage, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(os.listdir(self.stage), [])

    def test_uninstall_keeps_settings_unless_purged(self):
        install(self.src, self.stage)
        self.assertEqual(install(self.src, self.stage, "--uninstall").returncode, 0)
        self.assertFalse(os.path.exists(self.p("opt/pvj")))
        self.assertFalse(os.path.lexists(self.p("usr/local/bin/pvj-player")))
        self.assertFalse(os.path.lexists(self.p("usr/local/bin/pvj-rootfs")))
        self.assertFalse(os.path.lexists(self.p("usr/local/bin/pvj-update")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/pvj-player.service")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/pvj-web.service")))
        self.assertFalse(os.path.exists(self.p("etc/systemd/system/pvj-usb@.service")))
        self.assertFalse(os.path.exists(self.p("etc/udev/rules.d/99-pvj-usb.rules")))
        self.assertTrue(os.path.exists(self.p("etc/pvj/pvj.env")))
        install(self.src, self.stage, "--uninstall", "--purge")
        self.assertFalse(os.path.exists(self.p("etc/pvj")))

    def test_rejects_bad_input(self):
        for bad in (["--prefix", "relative"], ["--media", "/tmp/a b"], ["--prefix", "/opt/x;reboot"],
                    ["--web-user", "Bad User"], ["--bogus"],
                    ["--prefix", "/opt"], ["--prefix", "/"], ["--prefix", "/usr/local"]):
            r = install(self.src, self.stage, *bad)
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertEqual(os.listdir(self.stage), [])

    def test_requires_root_without_stage_or_dry_run(self):
        if os.getuid() == 0:
            self.skipTest("running as root")
        r = subprocess.run([os.path.join(self.src, "install", "install.sh")], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
