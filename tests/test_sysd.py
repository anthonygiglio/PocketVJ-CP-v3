# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import subprocess
import unittest

from pvj import sysd
from tests.test_server import ServerBase


class FakeRun:
    def __init__(self, synced="no", fail_set=False):
        self.calls = []
        self.synced = synced
        self.fail_set = fail_set

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out = ""
        code = 0
        if argv[:2] == ["timedatectl", "show"]:
            out = self.synced + "\n"
        if "set-time" in argv and self.fail_set:
            code, out = 1, "Failed to set time: Automatic time synchronization is enabled"
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")


class ServiceTest(unittest.TestCase):
    def make(self, **kw):
        self.run = FakeRun(**kw)
        self.scheduled = []
        return sysd.SysService(runner=self.run, schedule=lambda d, fn: self.scheduled.append((d, fn)), log=lambda *_: None)

    def test_reboot_and_poweroff_answer_first_then_run_the_fixed_command(self):
        s = self.make()
        self.assertEqual(s.handle({"cmd": "reboot"}), {"ok": True, "reboot": True})
        self.assertEqual(self.run.calls, [])                               # nothing yet: the reply goes out first
        delay, fn = self.scheduled[0]
        self.assertGreaterEqual(delay, 1.0)
        fn()
        self.assertEqual(self.run.calls, [["systemctl", "reboot"]])
        s.handle({"cmd": "poweroff"})
        self.scheduled[-1][1]()
        self.assertEqual(self.run.calls[-1], ["systemctl", "poweroff"])

    def test_set_time_only_when_the_clock_is_not_from_the_network(self):
        s = self.make(synced="yes")
        r = s.handle({"cmd": "set_time", "epoch": 1790000000})
        self.assertFalse(r["ok"])
        self.assertNotIn("set-time", [a for c in self.run.calls for a in c])

    def test_set_time_sets_utc_and_turns_network_time_back_on(self):
        s = self.make(synced="no")
        self.assertTrue(s.handle({"cmd": "set_time", "epoch": 1790000000})["ok"])
        cmds = [c for c in self.run.calls if c[0] == "timedatectl" and c[1] != "show"]
        self.assertEqual(cmds[0], ["timedatectl", "set-ntp", "false"])
        self.assertEqual(cmds[1], ["timedatectl", "--adjust-system-clock", "set-time", "2026-09-21 14:13:20 UTC"])
        self.assertEqual(cmds[2], ["timedatectl", "set-ntp", "true"])

    def test_a_failed_set_is_reported_and_network_time_is_still_turned_back_on(self):
        s = self.make(fail_set=True)
        r = s.handle({"cmd": "set_time", "epoch": 1790000000})
        self.assertFalse(r["ok"])
        self.assertEqual(self.run.calls[-1], ["timedatectl", "set-ntp", "true"])

    def test_bad_requests(self):
        s = self.make()
        for m in ([], "reboot", {"cmd": "shell", "argv": ["rm", "-rf", "/"]}, {"cmd": "set_time", "epoch": 5},
                  {"cmd": "set_time", "epoch": 5_000_000_000}, {"cmd": "set_time", "epoch": "1790000000"},
                  {"cmd": "set_time", "epoch": True}, {"cmd": "set_time", "epoch": 1.79e9}, {"cmd": "halt"}):
            self.assertFalse(s.handle(m)["ok"], m)
        self.assertEqual([c for c in self.run.calls if c[0] == "systemctl"], [])
        self.assertEqual(self.scheduled, [])

    def test_only_the_two_fixed_programs_can_ever_run(self):
        s = self.make()
        with self.assertRaises(AssertionError):
            s._run(["sh", "-c", "id"])


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.sent = []

        class Client:
            def request(inner, message):
                self.sent.append(message)
                if message["cmd"] == "status":
                    return {"ok": True, "clock_from_network": False, "now": 1790000000}
                return {"ok": True}
        self.api.sysd = Client()

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def test_reboot_and_poweroff_need_a_typed_confirmation(self):
        self.assertEqual(self.post("/api/system/reboot", {})[0], 400)
        self.assertEqual(self.post("/api/system/reboot", {"confirm": "poweroff"})[0], 400)
        self.assertEqual(self.post("/api/system/reboot", {"confirm": "reboot"})[0], 200)
        self.assertEqual(self.post("/api/system/poweroff", {"confirm": "poweroff"})[0], 200)
        self.assertEqual([m["cmd"] for m in self.sent if m["cmd"] != "status"], ["reboot", "poweroff"])

    def test_only_full_devices(self):
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        for path, body in (("/api/system/reboot", {"confirm": "reboot"}), ("/api/system/poweroff", {"confirm": "poweroff"}),
                           ("/api/system/clock", {"epoch": 1790000000})):
            self.assertEqual(self.post(path, body, token=live)[0], 403, path)
        self.assertEqual(self.sent, [])

    def test_clock_and_system_info(self):
        self.assertEqual(self.post("/api/system/clock", {"epoch": "now"})[0], 400)
        self.assertEqual(self.post("/api/system/clock", {"epoch": 1790000000})[0], 200)
        self.assertIn({"cmd": "set_time", "epoch": 1790000000}, self.sent)
        body = self.call("GET", "/api/system", token=self.full)[1]
        self.assertEqual((body["clock"]["clock_from_network"], body["system_actions"]), (False, True))

    def test_no_helper(self):
        self.api.sysd = None
        self.assertEqual(self.post("/api/system/reboot", {"confirm": "reboot"})[0], 503)
        self.assertEqual(self.call("GET", "/api/system", token=self.full)[1]["system_actions"], False)

    def test_writeback_connectors_are_not_screens(self):
        from unittest import mock
        with mock.patch("pvj.api.hardware.drm_connectors", return_value=[
                {"connector": "HDMI-A-1", "status": "connected", "modes": []},
                {"connector": "Writeback-1", "status": "unknown", "modes": []}]):
            body = self.call("GET", "/api/system", token=self.full)[1]
        self.assertEqual([s["connector"] for s in body["screens"]], ["HDMI-A-1"])


if __name__ == "__main__":
    unittest.main()
