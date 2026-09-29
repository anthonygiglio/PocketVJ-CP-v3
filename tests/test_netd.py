# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
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


UUID_OLD = "0b3c2f57-7d0a-4a5e-9d6a-1f2e3d4c5b6a"


class FakeNm:
    """A tiny NetworkManager: profiles with properties and uuids, which one is active on eth0, and
    switches to make commands fail (always, or only the first N times) or take time."""

    def __init__(self):
        self.profiles = {"Wired connection 1": {"uuid": UUID_OLD, "ipv4.method": "auto",
                                                "connection.autoconnect": "yes", "connection.autoconnect-priority": "0"}}
        self.active = "Wired connection 1"
        self.calls = []
        self.fail_on = None
        self.fail_times = None
        self.on_up = None
        self.addrs = [{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
                      {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.9", "prefixlen": 24}]}]
        self._n = 0

    def nmcli_calls(self):
        return [c for c in self.calls if c[0] == "nmcli"]

    def _by_uuid(self, uuid):
        return next((n for n, p in self.profiles.items() if p.get("uuid") == uuid), None)

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        line = " ".join(argv)
        done = lambda rc=0, out="", err="": subprocess.CompletedProcess(argv, rc, out, err)
        if self.fail_on and self.fail_on in line and (self.fail_times is None or self.fail_times > 0):
            if self.fail_times is not None:
                self.fail_times -= 1
            return done(4, "", "Error: connection activation failed")
        if argv[0] == "ip":
            return done(0, json.dumps(self.addrs))
        if argv[:5] == ["nmcli", "-t", "-f", "UUID,DEVICE", "connection"]:
            if self.active in self.profiles:
                return done(0, "%s:eth0\n" % self.profiles[self.active]["uuid"])
            return done(0, "")
        if argv[1:4] == ["-t", "-f", "connection.id"]:
            return done(0 if argv[-1] in self.profiles else 10, argv[-1] + "\n" if argv[-1] in self.profiles else "")
        verb = argv[2]
        if verb == "add":
            self._n += 1
            props = dict(zip(argv[9::2], argv[10::2]))
            props["uuid"] = "00000000-0000-4000-8000-%012d" % self._n
            self.profiles[argv[argv.index("con-name") + 1]] = props
        elif verb == "modify":
            name = argv[4]
            if name not in self.profiles:
                return done(10, "", "Error: unknown connection")
            props = dict(zip(argv[5::2], argv[6::2]))
            new_id = props.pop("connection.id", None)
            self.profiles[name].update(props)
            if new_id:
                self.profiles[new_id] = self.profiles.pop(name)
                if self.active == name:
                    self.active = new_id
        elif verb == "up":
            name = self._by_uuid(argv[4]) if argv[3] == "uuid" else argv[4]
            if name not in self.profiles:
                return done(10, "", "Error: unknown connection")
            if self.on_up:
                self.on_up()
            self.active = name
        elif verb == "down":
            if self.active == argv[4]:
                self.active = None
        elif verb == "delete":
            if argv[4] not in self.profiles:
                return done(10, "", "Error: unknown connection")
            self.profiles.pop(argv[4])
            if self.active == argv[4]:
                self.active = None
        return done()


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
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.state = os.path.join(self.dir, "net-pending.json")
        self.logs = []
        self.sysfs = make_sysfs()
        self.svc = self.make_service()

    def make_service(self):
        return NetService(runner=self.nm, clock=lambda: self.now[0], sysfs=self.sysfs, state_dir=self.dir,
                          log=self.logs.append)

    # --- the basic flow -------------------------------------------------------
    def test_apply_builds_a_candidate_and_leaves_the_confirmed_setup_alone(self):
        before = dict(self.nm.profiles["Wired connection 1"])
        st = self.svc.apply(dict(STATIC))
        self.assertEqual((st["pending"]["iface"], st["pending"]["seconds_left"]), ("eth0", 60))
        cand = self.nm.profiles["pvj-eth0-try"]
        self.assertEqual((cand["ipv4.method"], cand["ipv4.addresses"], cand["connection.autoconnect"]),
                         ("manual", "192.168.50.20/24", "no"))
        self.assertEqual(self.nm.active, "pvj-eth0-try")
        self.assertEqual(self.nm.profiles["Wired connection 1"], before)
        self.assertNotIn("pvj-eth0", self.nm.profiles)
        self.assertTrue(os.path.exists(self.state))

    def test_confirm_makes_the_candidate_the_permanent_profile(self):
        self.svc.apply(dict(STATIC))
        self.assertIsNone(self.svc.confirm()["pending"])
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)
        final = self.nm.profiles["pvj-eth0"]
        self.assertEqual((final["connection.autoconnect"], final["connection.autoconnect-priority"], final["ipv4.method"]),
                         ("yes", "101", "manual"))
        self.assertEqual(self.nm.active, "pvj-eth0")
        self.assertFalse(os.path.exists(self.state))
        self.now[0] += 1000
        self.assertFalse(self.svc.tick())

    def test_a_second_confirmed_change_replaces_the_first(self):
        self.svc.apply(dict(STATIC))
        self.svc.confirm()
        self.svc.apply({"iface": "eth0", "mode": "linklocal"})
        self.svc.confirm()
        self.assertEqual(self.nm.profiles["pvj-eth0"]["ipv4.method"], "link-local")
        self.assertEqual([n for n in self.nm.profiles if n.startswith("pvj-")], ["pvj-eth0"])

    def test_unconfirmed_change_reverts_at_the_deadline(self):
        self.svc.apply(dict(STATIC, revert_seconds=30))
        self.now[0] += 29
        self.assertFalse(self.svc.tick())
        self.now[0] += 2
        self.assertTrue(self.svc.tick())
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)
        self.assertIsNone(self.svc.status()["pending"])
        self.assertFalse(os.path.exists(self.state))

    # --- review finding 2 and 5: an existing confirmed profile, and what to go back to -------------
    def test_reverting_a_change_to_a_confirmed_profile_restores_it_exactly_and_reactivates_it(self):
        self.svc.apply(dict(STATIC))
        self.svc.confirm()
        confirmed = dict(self.nm.profiles["pvj-eth0"])
        self.svc.apply({"iface": "eth0", "mode": "linklocal"})
        self.assertEqual(self.nm.profiles["pvj-eth0"], confirmed)  # never edited in place
        self.svc.revert()
        self.assertEqual(self.nm.profiles["pvj-eth0"], confirmed)
        self.assertEqual(self.nm.active, "pvj-eth0")  # and it is the one in use again

    def test_power_cut_while_pending_keeps_the_old_network_and_the_restart_undoes_the_candidate(self):
        self.svc.apply(dict(STATIC))
        self.svc.confirm()
        confirmed = dict(self.nm.profiles["pvj-eth0"])
        self.svc.apply({"iface": "eth0", "mode": "dhcp"})
        # ---- the box loses power here. What is on disk:
        self.assertEqual(self.nm.profiles["pvj-eth0"], confirmed)  # old profile untouched, still autoconnect
        self.assertEqual(self.nm.profiles["pvj-eth0-try"]["connection.autoconnect"], "no")
        # ---- boot: the helper starts and finds the saved state
        reborn = self.make_service()
        self.assertTrue(reborn.recover())
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)
        self.assertEqual(self.nm.active, "pvj-eth0")
        self.assertFalse(os.path.exists(self.state))
        self.assertFalse(reborn.recover())

    # --- review finding 3: an undo that fails must not claim success -----------------------------------
    def test_a_failing_undo_is_retried_until_it_works_and_only_then_reported(self):
        self.svc.apply(dict(STATIC))
        self.nm.fail_on, self.nm.fail_times = "connection up uuid", 2
        st = self.svc.revert()
        self.assertTrue(st["reverting"])
        self.assertEqual(self.nm.active, None)  # candidate gone, old one not back yet
        self.assertTrue(os.path.exists(self.state))  # kept until it really worked
        self.assertFalse(self.svc.tick())  # too soon
        self.now[0] += 3
        self.assertFalse(self.svc.tick())  # second try fails too
        self.assertTrue(self.svc.status()["reverting"])
        self.now[0] += 3
        self.assertTrue(self.svc.tick())  # third succeeds
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertFalse(self.svc.status()["reverting"])
        self.assertFalse(os.path.exists(self.state))

    def test_giving_up_keeps_the_state_so_a_restart_finishes_the_job(self):
        self.svc.apply(dict(STATIC))
        self.nm.fail_on, self.nm.fail_times = "connection up uuid", None  # always fails
        self.svc.revert()
        for _ in range(60):
            self.now[0] += 3
            self.svc.tick()
        self.assertTrue(any("GAVE UP" in m for m in self.logs))
        self.assertFalse(self.svc.status()["reverting"])
        self.assertTrue(os.path.exists(self.state))
        self.nm.fail_on = None
        reborn = self.make_service()
        self.assertTrue(reborn.recover())
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertFalse(os.path.exists(self.state))

    def test_a_failed_apply_undoes_itself_and_says_how_it_went(self):
        self.nm.fail_on = "connection up id pvj-eth0-try"
        with self.assertRaises(NetError) as cm:
            self.svc.apply(dict(STATIC))
        self.assertIn("previous setup was restored", str(cm.exception))
        self.nm.fail_on = None
        self.assertEqual(self.nm.active, "Wired connection 1")
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)
        self.assertIsNone(self.svc.status()["pending"])

    def test_a_failed_apply_whose_undo_also_fails_does_not_claim_a_restore(self):
        self.nm.fail_on, self.nm.fail_times = "connection up", 2  # the new one and the first undo attempt fail
        with self.assertRaises(NetError) as cm:
            self.svc.apply(dict(STATIC))
        self.assertIn("still being retried", str(cm.exception))
        self.assertTrue(self.svc.status()["reverting"])

    # --- review finding 4: the countdown starts when the new network is up ------------------------------------
    def test_a_slow_activation_does_not_eat_the_confirm_window(self):
        self.nm.on_up = lambda: self.now.__setitem__(0, self.now[0] + 25)  # DHCP takes 25 s to answer
        st = self.svc.apply(dict(STATIC, revert_seconds=20))
        self.assertEqual(st["pending"]["seconds_left"], 20)
        self.assertFalse(self.svc.tick())
        self.now[0] += 21
        self.assertTrue(self.svc.tick())

    # --- guard rails ----------------------------------------------------------------------------------------------
    def test_only_one_change_at_a_time_and_nothing_to_confirm_or_revert_when_idle(self):
        self.svc.apply(dict(STATIC))
        with self.assertRaises(NetError):
            self.svc.apply({"iface": "eth0", "mode": "dhcp"})
        fresh = NetService(runner=self.nm, sysfs=self.sysfs)
        for op in (fresh.confirm, fresh.revert):
            with self.assertRaises(NetError):
                op()

    def test_invalid_requests_run_no_nmcli_commands(self):
        for bad in ({"iface": "wlan0", "mode": "dhcp"}, {"iface": "eth0", "mode": "static", "address": "8.8.8.8; reboot",
                                                        "prefix": 24}, {"iface": "eth0; reboot", "mode": "dhcp"}, "x", None):
            with self.assertRaises(NetError):
                self.svc.apply(bad)
        self.assertEqual(self.nm.nmcli_calls(), [])

    def test_a_range_that_overlaps_another_port_is_refused_using_the_kernels_addresses(self):
        self.nm.addrs.append({"ifname": "wlan0", "addr_info": [{"family": "inet", "local": "10.5.0.2", "prefixlen": 24}]})
        with self.assertRaises(NetError) as cm:
            self.svc.apply(dict(STATIC, address="10.5.0.9", gateway="10.5.0.1"))
        self.assertIn("wlan0", str(cm.exception))
        self.assertEqual(self.nm.nmcli_calls(), [])

    def test_plan_is_a_dry_run(self):
        out = self.svc.plan(dict(STATIC))
        self.assertTrue(any("ipv4.addresses 192.168.50.20/24" in c for c in out["commands"]))
        self.assertEqual(self.nm.nmcli_calls(), [])
        self.assertNotIn("pvj-eth0-try", self.nm.profiles)

    def test_missing_nmcli_is_a_clear_error(self):
        def gone(argv, **kw):
            raise FileNotFoundError()
        with self.assertRaises(NetError) as cm:
            NetService(runner=gone, sysfs=self.sysfs).apply(dict(STATIC))
        self.assertIn("not installed", str(cm.exception))

    def test_handle_returns_errors_as_data(self):
        self.assertFalse(self.svc.handle({"cmd": "nope"})["ok"])
        self.assertFalse(self.svc.handle([1])["ok"])
        self.assertTrue(self.svc.handle({"cmd": "status"})["ok"])
        self.assertFalse(self.svc.handle({"cmd": "apply", "config": {"iface": "x"}})["ok"])

    # --- review finding 1: the saved state is never trusted --------------------------------------------------
    def test_planted_or_damaged_state_is_discarded_without_running_anything(self):
        bad_states = ['{not json', '[1]', '"x"', '{}', json.dumps({"iface": "eth0; reboot", "previous_uuid": None}),
                      json.dumps({"iface": "eth0", "previous_uuid": "--ask"}), json.dumps({"iface": "eth0", "previous_uuid": 5}),
                      json.dumps({"iface": "../eth0", "previous_uuid": None}), json.dumps({"iface": None}),
                      json.dumps({"cfg": "x", "iface": ["a"], "previous_uuid": None})]
        for text in bad_states:
            with open(self.state, "w") as f:
                f.write(text)
            self.assertFalse(self.svc.recover(), text)
            self.assertFalse(os.path.exists(self.state), text)
        self.assertEqual(self.nm.nmcli_calls(), [])

    def test_a_planted_valid_state_can_only_trigger_the_fixed_undo_commands(self):
        with open(self.state, "w") as f:
            json.dump({"iface": "eth0", "previous_uuid": UUID_OLD, "cfg": {"iface": "eth0", "gateway": "6.6.6.6"},
                       "extra": "ignored"}, f)
        self.assertTrue(self.svc.recover())
        verbs = {tuple(c[1:4]) for c in self.nm.nmcli_calls()}
        self.assertLessEqual(verbs, {("connection", "up", "uuid"), ("-t", "-f", "connection.id")})

    def test_the_temp_file_is_never_followed_through_a_link(self):
        victim = os.path.join(self.dir, "victim")
        with open(victim, "w") as f:
            f.write("do not touch")
        os.symlink(victim, self.state + ".tmp")
        self.svc.apply(dict(STATIC))
        with open(victim) as f:
            self.assertEqual(f.read(), "do not touch")
        with open(self.state) as f:
            self.assertEqual(json.load(f)["phase"], "pending")

    def test_the_state_file_holds_only_an_interface_and_a_connection_id(self):
        self.svc.apply(dict(STATIC))
        with open(self.state) as f:
            data = json.load(f)
        self.assertEqual(set(data), {"phase", "iface", "previous_uuid"})
        self.assertEqual(oct(os.stat(self.state).st_mode & 0o777), "0o600")

    def test_state_directory_must_be_private(self):
        from pvj import netd
        good = tempfile.mkdtemp()
        os.chmod(good, 0o700)
        loose = tempfile.mkdtemp()
        os.chmod(loose, 0o770)
        old = os.environ.get("STATE_DIRECTORY")
        self.addCleanup(lambda: os.environ.__setitem__("STATE_DIRECTORY", old) if old else os.environ.pop("STATE_DIRECTORY", None))
        os.environ["STATE_DIRECTORY"] = good
        self.assertEqual(netd.safe_state_dir(), good)
        os.environ["STATE_DIRECTORY"] = loose
        self.assertIsNone(netd.safe_state_dir())


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
