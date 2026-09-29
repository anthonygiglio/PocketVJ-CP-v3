# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import tempfile
import unittest

from pvj import netcfg
from pvj.netcfg import NetError

WIRED = {"name": "eth0", "kind": "wired", "state": "up", "carrier": True, "mac": "aa", "speed_mbps": 1000}
WIFI = {"name": "wlan0", "kind": "wifi", "state": "up", "carrier": True, "mac": "bb", "speed_mbps": None}
IFACES = [WIRED, WIFI]


def ok(**kw):
    return netcfg.validate(dict({"iface": "eth0", "mode": "dhcp"}, **kw), IFACES)


class ValidateTest(unittest.TestCase):
    def test_static_config_is_normalised(self):
        cfg = ok(mode="static", address="192.168.50.20", prefix=24, gateway="192.168.50.1", dns=["1.1.1.1", "8.8.8.8"])
        self.assertEqual(cfg, {"iface": "eth0", "mode": "static", "revert_seconds": 60, "address": "192.168.50.20",
                               "prefix": 24, "gateway": "192.168.50.1", "dns": ["1.1.1.1", "8.8.8.8"]})

    def test_simple_modes_take_no_address(self):
        for mode in ("dhcp", "linklocal"):
            self.assertEqual(set(ok(mode=mode)), {"iface", "mode", "revert_seconds"})

    def test_share_defaults_and_private_only(self):
        self.assertEqual((ok(mode="share")["address"], ok(mode="share")["prefix"]), ("10.42.0.1", 24))
        self.assertEqual(ok(mode="share", address="192.168.7.1")["address"], "192.168.7.1")
        with self.assertRaises(NetError):
            ok(mode="share", address="8.8.8.8")

    def test_bad_addresses_are_refused(self):
        base = dict(mode="static", prefix=24)
        for addr in ("", None, 5, "192.168.1", "192.168.1.256", "192.168.001.5", "0.0.0.0", "255.255.255.255",
                     "127.0.0.1", "224.0.0.1", "169.254.1.1", "240.0.0.1", "192.168.1.0", "192.168.1.255",
                     "192.168.1.5; reboot", "::1", " 192.168.1.5", "192.168.1.5\n"):
            with self.assertRaises(NetError, msg=repr(addr)):
                ok(address=addr, **base)

    def test_bad_prefix_gateway_dns_and_revert(self):
        base = dict(mode="static", address="192.168.1.20")
        for prefix in (None, 7, 31, 32, "24", True, 24.0):
            with self.assertRaises(NetError, msg=repr(prefix)):
                ok(prefix=prefix, **base)
        for gw in ("192.168.2.1", "192.168.1.20", "192.168.1.0", "192.168.1.255", "x", 5):
            with self.assertRaises(NetError, msg=repr(gw)):
                ok(prefix=24, gateway=gw, **base)
        for dns in ("1.1.1.1", ["1.1.1.1"] * 4, ["nope"], ["0.0.0.0"], ["127.0.0.1"], [5]):
            with self.assertRaises(NetError, msg=repr(dns)):
                ok(prefix=24, dns=dns, **base)
        for sec in (5, 301, True, "60", 60.5):
            with self.assertRaises(NetError, msg=repr(sec)):
                ok(revert_seconds=sec)

    def test_interface_and_mode_rules(self):
        for iface in ("wlan0", "eth9", "lo", "", None, "eth0; reboot", "../eth0", "ETH0", "eth0 ", "a" * 40):
            with self.assertRaises(NetError, msg=repr(iface)):
                netcfg.validate({"iface": iface, "mode": "dhcp"}, IFACES)
        for mode in ("", None, "DHCP", "bridge", 5):
            with self.assertRaises(NetError, msg=repr(mode)):
                ok(mode=mode)
        with self.assertRaises(NetError):
            netcfg.validate("eth0", IFACES)


class PlanTest(unittest.TestCase):
    def props(self, cmd):
        i = cmd.index("con-name") + 2 if "con-name" in cmd else 4
        rest = cmd[i:]
        return dict(zip(rest[::2], rest[1::2]))

    def test_new_static_profile_commands(self):
        cfg = ok(mode="static", address="192.168.50.20", prefix=24, gateway="192.168.50.1", dns=["1.1.1.1"])
        cmds = netcfg.plan(cfg, profile_exists=False)
        self.assertEqual(cmds[0][:9], ["nmcli", "connection", "add", "type", "ethernet", "ifname", "eth0",
                                       "con-name", "pvj-eth0"])
        p = self.props(cmds[0])
        self.assertEqual((p["ipv4.method"], p["ipv4.addresses"], p["ipv4.gateway"], p["ipv4.dns"]),
                         ("manual", "192.168.50.20/24", "192.168.50.1", "1.1.1.1"))
        self.assertEqual(p["connection.autoconnect-priority"], "100")
        self.assertEqual(p["connection.autoconnect"], "no")  # not permanent until confirmed
        self.assertEqual(cmds[1], ["nmcli", "connection", "up", "pvj-eth0"])
        self.assertEqual(netcfg.confirm_plan("eth0"), [["nmcli", "connection", "modify", "pvj-eth0",
                                                        "connection.autoconnect", "yes",
                                                        "connection.autoconnect-priority", "100"]])

    def test_modify_existing_profile_and_each_mode(self):
        expect = {"dhcp": "auto", "linklocal": "link-local", "share": "shared"}
        for mode, method in expect.items():
            cmds = netcfg.plan(ok(mode=mode), profile_exists=True)
            self.assertEqual(cmds[0][:4], ["nmcli", "connection", "modify", "pvj-eth0"])
            self.assertEqual(self.props(cmds[0])["ipv4.method"], method)

    def test_no_shell_and_nothing_user_supplied_outside_validated_fields(self):
        cfg = ok(mode="static", address="192.168.50.20", prefix=24)
        for cmd in netcfg.plan(cfg, False):
            self.assertIsInstance(cmd, list)
            self.assertTrue(all(isinstance(a, str) and "\n" not in a and ";" not in a for a in cmd), cmd)

    def test_revert_plans(self):
        fresh = netcfg.revert_plan("eth0", None, "Wired connection 1")
        self.assertEqual(fresh, [["nmcli", "connection", "down", "pvj-eth0"], ["nmcli", "connection", "delete", "pvj-eth0"],
                                 ["nmcli", "connection", "up", "Wired connection 1"]])
        snap = {"ipv4.method": "manual", "ipv4.addresses": "10.0.0.5/24", "ipv4.gateway": "", "ipv4.dns": "",
                "ipv6.method": "auto", "connection.autoconnect": "yes", "connection.autoconnect-priority": "100"}
        back = netcfg.revert_plan("eth0", snap, None)
        self.assertEqual(back[0][:4], ["nmcli", "connection", "modify", "pvj-eth0"])
        self.assertIn("10.0.0.5/24", back[0])
        self.assertEqual(back[1], ["nmcli", "connection", "up", "pvj-eth0"])

    def test_parse_show_keeps_colons_in_values(self):
        text = "ipv4.method:manual\nipv4.dns:1.1.1.1,8.8.8.8\nipv6.addresses:fe80\\:\\:1/64\n"
        got = netcfg.parse_show(text)
        self.assertEqual(got["ipv4.method"], "manual")
        self.assertEqual(got["ipv4.dns"], "1.1.1.1,8.8.8.8")
        self.assertEqual(got["ipv6.addresses"], "fe80::1/64")


class InterfaceListTest(unittest.TestCase):
    def make(self, name, wireless=False, extra=(), speed="1000", state="up"):
        d = os.path.join(self.root, name)
        os.makedirs(d)
        for fn, text in (("address", "aa:bb"), ("operstate", state), ("carrier", "1" if state == "up" else "0"),
                         ("speed", speed)):
            with open(os.path.join(d, fn), "w") as f:
                f.write(text)
        if wireless:
            os.makedirs(os.path.join(d, "wireless"))
        for x in extra:
            os.makedirs(os.path.join(d, x))

    def test_wired_first_and_virtual_interfaces_hidden(self):
        self.root = tempfile.mkdtemp()
        self.make("wlan0", wireless=True)
        self.make("enp3s0")
        self.make("eth0", speed="-1", state="down")
        self.make("docker0")
        self.make("br0", extra=("bridge",))
        self.make("veth123")
        os.makedirs(os.path.join(self.root, "lo"))
        got = netcfg.list_interfaces(self.root)
        self.assertEqual([i["name"] for i in got], ["enp3s0", "eth0", "wlan0"])
        self.assertEqual(got[0]["speed_mbps"], 1000)
        self.assertIsNone(got[1]["speed_mbps"])  # -1 means unknown
        self.assertFalse(got[1]["carrier"])
        self.assertEqual(got[2]["kind"], "wifi")

    def test_missing_sysfs_is_empty(self):
        self.assertEqual(netcfg.list_interfaces("/nonexistent"), [])


class PendingTest(unittest.TestCase):
    def test_deadline(self):
        p = netcfg.PendingChange({}, None, None, started=100.0, seconds=60)
        self.assertEqual(p.seconds_left(100.0), 60)
        self.assertEqual(p.seconds_left(159.2), 1)
        self.assertFalse(p.expired(159.9))
        self.assertTrue(p.expired(160.0))


if __name__ == "__main__":
    unittest.main()
