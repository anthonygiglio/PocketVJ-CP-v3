# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest

from pvj import netcfg
from pvj.netcfg import NetError
from pvj.netd import NetdClient, NetServer, NetService


class FakeNm:
    """A tiny NetworkManager: profiles with properties, and which profile is active on eth0."""

    def __init__(self):
        self.profiles = {"Wired connection 1": {"ipv4.method": "auto", "connection.autoconnect": "yes",
                                                "connection.autoconnect-priority": "0"}}
        self.active = "Wired connection 1"
        self.calls = []
        self.fail_on = None

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        line = " ".join(argv)
        if self.fail_on and self.fail_on in line:
            return subprocess.CompletedProcess(argv, 4, "", "Error: connection activation failed")
        if argv[:5] == ["nmcli", "-t", "-f", "NAME,DEVICE", "connection"]:
            return subprocess.CompletedProcess(argv, 0, "%s:eth0\n" % self.active if self.active else "", "")
        if argv[1:3] == ["-t", "-f"] and "show" in argv:
            name = argv[-1]
            if name not in self.profiles:
                return subprocess.CompletedProcess(argv, 10, "", "Error: not found")
            fields = argv[3].split(",")
            body = "".join("%s:%s\n" % (f, self.profiles[name].get(f, "")) for f in fields)
            return subprocess.CompletedProcess(argv, 0, body, "")
        verb, name = argv[2], argv[3]
        if verb == "add":
            props = dict(zip(argv[9::2], argv[10::2]))
            self.profiles[argv[argv.index("con-name") + 1]] = props
        elif verb == "modify":
            self.profiles[name].update(dict(zip(argv[4::2], argv[5::2])))
        elif verb == "up":
            self.active = name
        elif verb == "down":
            if self.active == name:
                self.active = None
        elif verb == "delete":
            self.profiles.pop(name, None)
        return subprocess.CompletedProcess(argv, 0, "", "")


def make_sysfs():
    root = tempfile.mkdtemp()
    for name, wireless in (("eth0", False), ("wlan0", True)):
        d = os.path.join(root, name)
        os.makedirs(d)
        for fn, text in (("address", "aa:bb"), ("operstate", "up"), ("carrier", "1"), ("speed", "1000")):
            with open(os.path.join(d, fn), "w") as f:
                f.write(text)
        if wireless:
            os.makedirs(os.path.join(d, "wireless"))
    return root


STATIC = {"iface": "eth0", "mode": "static", "address": "192.168.50.20", "prefix": 24, "gateway": "192.168.50.1",
          "dns": ["1.1.1.1"]}


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.nm = FakeNm()
        self.now = [1000.0]
        self.state = os.path.join(tempfile.mkdtemp(), "pending.json")
        self.svc = NetService(runner=self.nm, clock=lambda: self.now[0], sysfs=make_sysfs(), state_file=self.state)

    def test_apply_leaves_a_pending_change_that_is_not_yet_permanent(self):
        st = self.svc.apply(dict(STATIC))
        self.assertEqual((st["pending"]["iface"], st["pending"]["seconds_left"]), ("eth0", 60))
        p = self.nm.profiles["pvj-eth0"]
        self.assertEqual((p["ipv4.method"], p["ipv4.addresses"]), ("manual", "192.168.50.20/24"))
        self.assertEqual(p["connection.autoconnect"], "no")  # a reboot now falls back to the old network
        self.assertEqual(self.nm.active, "pvj-eth0")
        self.assertTrue(os.path.exists(self.state))

    def test_confirm_makes_it_permanent(self):
        self.svc.apply(dict(STATIC))
        st = self.svc.confirm()
        self.assertIsNone(st["pending"])
        self.assertEqual(self.nm.profiles["pvj-eth0"]["connection.autoconnect"], "yes")
        self.assertFalse(os.path.exists(self.state))
        self.now[0] += 1000
        self.assertFalse(self.svc.tick())  # nothing left to revert

    def test_unconfirmed_change_reverts_when_the_timer_runs_out(self):
        self.svc.apply(dict(STATIC, revert_seconds=30))
        self.now[0] += 29
        self.assertFalse(self.svc.tick())
        self.assertEqual(self.nm.active, "pvj-eth0")
        self.now[0] += 2
        self.assertTrue(self.svc.tick())
        self.assertEqual(self.nm.active, "Wired connection 1")  # the old network is back
        self.assertNotIn("pvj-eth0", self.nm.profiles)
        self.assertIsNone(self.svc.status()["pending"])
        self.assertFalse(os.path.exists(self.state))

    def test_revert_restores_an_existing_profile_exactly(self):
        self.svc.apply(dict(STATIC))
        self.svc.confirm()
        before = dict(self.nm.profiles["pvj-eth0"])
        self.svc.apply({"iface": "eth0", "mode": "linklocal"})
        self.assertEqual(self.nm.profiles["pvj-eth0"]["ipv4.method"], "link-local")
        self.svc.revert()
        after = self.nm.profiles["pvj-eth0"]
        for key in ("ipv4.method", "ipv4.addresses", "ipv4.gateway", "ipv4.dns"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.nm.active, "pvj-eth0")

    def test_a_failing_command_undoes_everything_and_says_so(self):
        self.nm.fail_on = "connection up pvj-eth0"
        with self.assertRaises(NetError) as cm:
            self.svc.apply(dict(STATIC))
        self.assertIn("previous setup was restored", str(cm.exception))
        self.nm.fail_on = None
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertNotIn("pvj-eth0", self.nm.profiles)
        self.assertIsNone(self.svc.status()["pending"])

    def test_only_one_change_at_a_time(self):
        self.svc.apply(dict(STATIC))
        with self.assertRaises(NetError):
            self.svc.apply({"iface": "eth0", "mode": "dhcp"})
        with self.assertRaises(NetError):
            netcfg_service = NetService(runner=self.nm, sysfs=self.svc.sysfs)
            netcfg_service.confirm()  # nothing pending in a fresh service

    def test_invalid_requests_run_no_commands_at_all(self):
        for bad in ({"iface": "wlan0", "mode": "dhcp"}, {"iface": "eth0", "mode": "static", "address": "8.8.8.8; reboot",
                                                        "prefix": 24}, {"iface": "eth0; reboot", "mode": "dhcp"}, "x", None):
            with self.assertRaises(NetError):
                self.svc.apply(bad)
        self.assertEqual(self.nm.calls, [])

    def test_plan_is_a_dry_run(self):
        out = self.svc.plan(dict(STATIC))
        self.assertTrue(any("ipv4.addresses 192.168.50.20/24" in c for c in out["commands"]))
        self.assertFalse(any(c[2] in ("add", "modify", "up") for c in self.nm.calls if len(c) > 2 and c[1] == "connection"))
        self.assertNotIn("pvj-eth0", self.nm.profiles)

    def test_a_restarted_daemon_undoes_an_unconfirmed_change(self):
        self.svc.apply(dict(STATIC))
        reborn = NetService(runner=self.nm, clock=lambda: self.now[0], sysfs=self.svc.sysfs, state_file=self.state)
        self.assertTrue(reborn.recover())
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertNotIn("pvj-eth0", self.nm.profiles)
        self.assertFalse(os.path.exists(self.state))
        self.assertFalse(reborn.recover())  # nothing left to recover

    def test_a_corrupt_state_file_is_discarded_not_fatal(self):
        with open(self.state, "w") as f:
            f.write("{not json")
        self.assertFalse(self.svc.recover())
        self.assertFalse(os.path.exists(self.state))

    def test_missing_nmcli_is_a_clear_error(self):
        def gone(argv, **kw):
            raise FileNotFoundError()
        with self.assertRaises(NetError) as cm:
            NetService(runner=gone, sysfs=self.svc.sysfs).apply(dict(STATIC))
        self.assertIn("not installed", str(cm.exception))

    def test_handle_returns_errors_as_data(self):
        self.assertFalse(self.svc.handle({"cmd": "nope"})["ok"])
        self.assertFalse(self.svc.handle([1])["ok"])
        self.assertTrue(self.svc.handle({"cmd": "status"})["ok"])
        self.assertEqual(self.svc.handle({"cmd": "apply", "config": {"iface": "x"}})["ok"], False)


class SocketTest(unittest.TestCase):
    def setUp(self):
        self.nm = FakeNm()
        self.svc = NetService(runner=self.nm, sysfs=make_sysfs())
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "netd.sock")
        self.allowed = True
        self.server = NetServer(self.path, self.svc, lambda uid: self.allowed)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = NetdClient(self.path, timeout=5)

    def test_round_trip_and_socket_is_group_only(self):
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o660")
        st = self.client.request({"cmd": "status"})
        self.assertTrue(st["ok"])
        self.assertEqual([i["name"] for i in st["interfaces"]], ["eth0", "wlan0"])
        r = self.client.request({"cmd": "apply", "config": dict(STATIC)})
        self.assertEqual(r["pending"]["iface"], "eth0")
        self.assertTrue(self.client.request({"cmd": "confirm"})["ok"])

    def test_disallowed_caller_is_refused_before_anything_is_read(self):
        self.allowed = False
        self.assertEqual(self.client.request({"cmd": "status"}), {"ok": False, "error": "not allowed"})
        self.assertEqual(self.nm.calls, [])

    def test_garbage_and_oversized_requests(self):
        for raw in (b"not json\n", b"[1,2]\n", b"[" * 3000 + b"\n", b"x" * 5000 + b"\n"):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(self.path)
            s.sendall(raw)
            reply = json.loads(s.recv(4096))
            s.close()
            self.assertFalse(reply["ok"], raw[:20])
        self.assertEqual(self.nm.calls, [])
        self.assertTrue(self.client.request({"cmd": "status"})["ok"])  # still serving

    def test_a_stalled_client_does_not_block_others(self):
        stalled = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stalled.connect(self.path)
        self.addCleanup(stalled.close)
        import time as t
        started = t.time()
        self.assertTrue(self.client.request({"cmd": "status"})["ok"])
        self.assertLess(t.time() - started, 7)  # waited at most the 5 s per-connection timeout

    def test_no_daemon_is_a_clear_error(self):
        with self.assertRaises(NetError):
            NetdClient(os.path.join(self.dir, "missing.sock"), timeout=1).request({"cmd": "status"})


if __name__ == "__main__":
    unittest.main()
