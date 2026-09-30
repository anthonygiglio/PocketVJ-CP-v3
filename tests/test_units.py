# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Static checks on the systemd unit files. Both of these bugs were found only on a real Pi 4, which is why
they are guarded here: nothing in a container starts these units."""
import glob
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def parse(path):
    keys = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"^([A-Za-z]+)=(.*)$", line.strip())
            if m:
                keys.setdefault(m.group(1), []).append(m.group(2))
    return keys


def load_units(directory=None):
    directory = directory or os.path.join(REPO, "install")
    return {os.path.basename(p): parse(p) for p in glob.glob(os.path.join(directory, "*.service"))}


def words(keys, *names):
    return [w for n in names for v in keys.get(n, []) for w in v.split()]


def ordering_cycle(units):
    """A cycle in the start-order graph, as a list of names, or None."""
    edges = {}   # a -> b means "a must start before b"
    for name, keys in units.items():
        for other in words(keys, "After"):
            edges.setdefault(other, set()).add(name)
        for other in words(keys, "Before"):
            edges.setdefault(name, set()).add(other)
        for target in words(keys, "WantedBy", "RequiredBy"):
            edges.setdefault(name, set()).add(target)     # a target starts after what it wants

    def visit(node, path):
        if node in path:
            return path[path.index(node):] + [node]
        for nxt in sorted(edges.get(node, ())):
            found = visit(nxt, path + [node])
            if found:
                return found
        return None
    for node in sorted(edges):
        found = visit(node, [])
        if found:
            return found
    return None


class UnitOrderingTest(unittest.TestCase):
    def test_no_unit_is_ordered_after_a_target_that_wants_it(self):
        # A target waits for the units it wants, so "After=multi-user.target" plus "WantedBy=multi-user.target"
        # is a cycle, and systemd then deletes one of the start jobs without any error.
        for name, keys in load_units().items():
            clash = set(words(keys, "After")) & set(words(keys, "WantedBy", "RequiredBy"))
            self.assertEqual(clash, set(), "%s is ordered after a target that pulls it in: %s" % (name, sorted(clash)))

    def test_no_ordering_cycle_between_our_units(self):
        self.assertIsNone(ordering_cycle(load_units()))

    def test_the_check_would_have_caught_the_old_player_unit(self):
        old = {"pvj-player.service": {"After": ["multi-user.target systemd-udev-settle.service"], "WantedBy": ["multi-user.target"]},
               "pvj-web.service": {"After": ["network.target pvj-player.service"], "WantedBy": ["multi-user.target"]}}
        self.assertIsNotNone(ordering_cycle(old))


class PlayerUnitTest(unittest.TestCase):
    def test_player_unit_has_no_shell_hook_for_the_socket(self):
        # The socket is opened up by `pvj-player serve` (tests/test_player.py). A unit hook ran before mpv had made
        # its new socket and changed the stale one, so it did nothing (found on a real Pi 4).
        self.assertNotIn("ExecStartPost", load_units()["pvj-player.service"])

    def test_player_runs_in_the_pvj_group_so_the_panel_can_reach_the_socket(self):
        self.assertEqual(load_units()["pvj-player.service"]["Group"], ["pvj"])
        self.assertEqual(load_units()["pvj-web.service"]["Group"], ["pvj"])


class InstallerOwnershipTest(unittest.TestCase):
    """The player account must not be able to replace settings.json (found by the join-code review)."""

    def setUp(self):
        with open(os.path.join(REPO, "install", "install.sh")) as f:
            self.sh = f.read()

    def test_the_player_gets_its_own_home_and_old_installs_are_moved_without_moving_files(self):
        self.assertIn("--home-dir /var/lib/pvj-player", self.sh)
        self.assertNotIn("--home-dir /var/lib/pvj ", self.sh)
        self.assertRegex(self.sh, r"usermod -d /var/lib/pvj-player")
        self.assertNotRegex(self.sh, r"usermod[^\n]*\s-m\b")                     # -m would move settings and media

    def test_the_player_is_stopped_before_its_home_is_changed(self):
        # usermod fails while the account has a running process, and the installer stops at the first error
        stop = self.sh.index("systemctl stop pvj-player.service")
        change = self.sh.index("usermod -d /var/lib/pvj-player")
        self.assertLess(stop, change)

    def test_the_state_folder_belongs_to_the_web_user_and_is_not_group_writable(self):
        self.assertIn("chown pvj-web:pvj /var/lib/pvj;", self.sh)
        self.assertIn("chmod 2750 /var/lib/pvj", self.sh)
        self.assertNotIn("chmod 2775 /var/lib/pvj;", self.sh)

class WebUnitTest(unittest.TestCase):
    def test_the_panel_may_read_the_boxs_addresses(self):
        # `ip -j addr` needs a netlink socket; the sandbox refused it and the Network card showed no addresses on a Pi 4
        families = words(load_units()["pvj-web.service"], "RestrictAddressFamilies")
        self.assertIn("AF_NETLINK", families)
        self.assertNotIn("AF_PACKET", families)


if __name__ == "__main__":
    unittest.main()
