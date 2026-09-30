# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import hashlib
import ipaddress
import socket
import socketserver
import threading
import unittest
from unittest import mock

from pvj import osc, projector, scheduler
from tests.test_server import ServerBase


class FakeProjector:
    """A PJLink class 1 projector on 127.0.0.1: answers POWR, AVMT; optional password."""

    def __init__(self, password=""):
        self.password, self.power, self.mute, self.received = password, "0", "30", []
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                rnd = "498e4a67"
                self.wfile.write(("PJLINK 1 %s\r" % rnd if outer.password else "PJLINK 0\r").encode())
                buf = b""
                while not buf.endswith(b"\r") and len(buf) < 300:
                    c = self.rfile.read(1)
                    if not c:
                        return
                    buf += c
                line = buf.decode().strip("\r\n")
                outer.received.append(line)
                if outer.password:
                    want = hashlib.md5((rnd + outer.password).encode()).hexdigest()
                    if not line.startswith(want):
                        self.wfile.write(b"PJLINK ERRA\r")
                        return
                    line = line[32:]
                cmd, _, val = line[2:].partition(" ")
                if cmd == "POWR":
                    if val == "?":
                        self.wfile.write(("%%1POWR=%s\r" % outer.power).encode())
                    elif val in ("0", "1"):
                        outer.power = val
                        self.wfile.write(b"%1POWR=OK\r")
                    else:
                        self.wfile.write(b"%1POWR=ERR2\r")
                elif cmd == "AVMT":
                    outer.mute = val
                    self.wfile.write(b"%1AVMT=OK\r")
                else:
                    self.wfile.write(("%%1%s=ERR1\r" % cmd).encode())

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True
        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


LOOPBACK_OK = mock.patch.object(projector, "PRIVATE", projector.PRIVATE + [ipaddress.ip_network("127.0.0.0/8")])


class PrivateNetworkTest(unittest.TestCase):
    def test_only_private_addresses(self):
        for good in ("192.168.0.50", "10.1.2.3", "172.16.0.9", "169.254.10.20", "fe80::1"):
            self.assertEqual(projector.private_address(good), good)
        for bad in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "172.32.0.1", "::1", "", None, "a b", "http://x", "projector;rm"):
            with self.assertRaises(projector.ProjectorError, msg=bad):
                projector.private_address(bad)

    def test_a_name_must_resolve_to_a_private_address(self):
        ok = lambda h, p, proto=0: [(0, 0, 0, "", ("192.168.0.60", p))]
        public = lambda h, p, proto=0: [(0, 0, 0, "", ("93.184.216.34", p))]
        self.assertEqual(projector.private_address("beamer.lan", ok), "192.168.0.60")
        with self.assertRaises(projector.ProjectorError):
            projector.private_address("example.com", public)

    def test_validate(self):
        e = projector.validate({"name": " Main ", "host": "192.168.0.50", "password": "JBMIAProjectorLink"})
        self.assertEqual((e["name"], e["port"], len(e["id"])), ("Main", 4352, 8))
        for bad in ({"host": "192.168.0.50", "name": ""}, {"host": "192.168.0.50", "port": 0}, {"host": "192.168.0.50", "password": "has space"},
                    {"host": "192.168.0.50", "password": "x" * 33}, {"host": 5}, [], {"host": "192.168.0.50", "port": True}):
            with self.assertRaises(projector.ProjectorError, msg=str(bad)):
                projector.validate(bad)


class PJLinkTest(unittest.TestCase):
    def test_power_state_mute_without_and_with_password(self):
        with LOOPBACK_OK:
            for pw in ("", "secret1"):
                fake = FakeProjector(pw)
                self.addCleanup(fake.close)
                link = projector.PJLink("127.0.0.1", fake.port, pw)
                self.assertEqual(link.state(), "off")
                link.power(True)
                self.assertEqual((fake.power, link.state()), ("1", "on"))
                link.mute(True)
                self.assertEqual(fake.mute, "31")
                if pw:
                    self.assertTrue(all(len(r) > 32 for r in fake.received))            # every command carries the digest
                    self.assertNotIn(pw, "".join(fake.received))                        # the password itself never travels

    def test_wrong_password_unknown_command_and_nobody_home(self):
        with LOOPBACK_OK:
            fake = FakeProjector("right")
            self.addCleanup(fake.close)
            with self.assertRaises(projector.ProjectorError) as cm:
                projector.PJLink("127.0.0.1", fake.port, "wrong").power(True)
            self.assertIn("password", str(cm.exception))
            with self.assertRaises(projector.ProjectorError):
                projector.PJLink("127.0.0.1", fake.port, "").power(True)                # needs a password
            with self.assertRaises(projector.ProjectorError) as cm:
                projector.PJLink("127.0.0.1", fake.port, "right").command("LAMP ?")
            self.assertIn("does not know", str(cm.exception))
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            dead = s.getsockname()[1]
            s.close()
            with self.assertRaises(projector.ProjectorError):
                projector.PJLink("127.0.0.1", dead, "", timeout=1).power(True)

    def test_a_slow_trickle_is_cut_off(self):
        import time as _t

        class Slow(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    for c in b"PJLINK 0":
                        self.wfile.write(bytes([c]))
                        _t.sleep(0.3)
                except OSError:
                    pass
        srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Slow)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        start = _t.monotonic()
        with LOOPBACK_OK, self.assertRaises(projector.ProjectorError):
            projector.PJLink("127.0.0.1", srv.server_address[1], timeout=0.5).power(True)
        self.assertLess(_t.monotonic() - start, 2.0)                    # each byte was quick; the whole line was not

    def test_something_that_is_not_a_projector(self):
        class Web(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        srv = socketserver.TCPServer(("127.0.0.1", 0), Web)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with LOOPBACK_OK, self.assertRaises(projector.ProjectorError):
            projector.PJLink("127.0.0.1", srv.server_address[1]).power(True)


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.assertEqual(self.post("/api/projectors", {"add": {"host": "192.168.0.5"}})[0], 409)       # module off
        self.call("POST", "/api/modules/projector", {"enabled": True}, token=self.full)
        self.fake = FakeProjector("pw1")
        self.addCleanup(self.fake.close)
        p = LOOPBACK_OK
        p.start()
        self.addCleanup(p.stop)

    def post(self, path, body, token=None):
        return self.call("POST", path, body, token=token or self.full)

    def add(self, host="127.0.0.1"):
        return self.post("/api/projectors", {"add": {"name": "Main", "host": host, "port": self.fake.port, "password": "pw1"}})

    def test_add_list_hides_password_power_and_remove(self):
        st, body, _ = self.add()
        self.assertEqual(st, 200)
        p = body["projectors"][0]
        self.assertEqual((p["name"], p["has_password"]), ("Main", True))
        self.assertNotIn("pw1", str(self.call("GET", "/api/projectors", token=self.full)[1]))
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.post("/api/projector", {"id": p["id"], "action": "on"}, token=live)[0], 200)
        self.assertEqual(self.fake.power, "1")
        st, body, _ = self.post("/api/projector", {"id": "all", "action": "state"})
        self.assertEqual(body["results"][p["id"]]["power"], "on")
        self.assertEqual(self.post("/api/projectors", {"remove": p["id"]})[1]["projectors"], [])

    def test_public_addresses_and_bad_input_are_refused(self):
        self.assertEqual(self.post("/api/projectors", {"add": {"host": "8.8.8.8"}})[0], 400)
        self.assertEqual(self.post("/api/projectors", {"nonsense": 1})[0], 400)
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "explode"})[0], 400)
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "on"})[0], 404)          # none added

    def test_an_unreachable_projector_is_a_clear_error(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.fake.close()
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "on"})
        self.assertEqual(st, 502)
        self.assertIn("cannot reach", body["error"])

    def test_roles(self):
        view = self.post("/api/devices/invite", {"name": "g", "role": "view"})[1]["token"]
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.call("GET", "/api/projectors", token=view)[0], 200)
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "on"}, token=view)[0], 403)
        self.assertEqual(self.post("/api/projectors", {"add": {"host": "192.168.0.5"}}, token=live)[0], 403)


class ScheduleAndOscTest(unittest.TestCase):
    def test_schedule_accepts_presets_and_projector_power(self):
        s = scheduler.validate({"enabled": True, "entries": [
            {"time": "18:00", "days": [0], "action": "projector_on"},
            {"time": "18:01", "days": [0], "action": "preset", "preset": "startlessonce01"},
            {"time": "23:59", "days": [0], "action": "projector_off"}]})
        self.assertEqual([e["action"] for e in s["entries"]], ["projector_on", "preset", "projector_off"])
        for bad in ({"action": "preset"}, {"action": "preset", "preset": "rm -rf /"}, {"action": "preset", "preset": "startwifi01"}):
            with self.assertRaises(scheduler.ScheduleError):
                scheduler.validate({"entries": [dict({"time": "10:00", "days": [1]}, **bad)]})

    def test_legacy_osc_beamer_addresses(self):
        self.assertEqual(osc.translate("/beameron", [1.0]), ("/api/projector", {"id": "all", "action": "on"}))
        self.assertEqual(osc.translate("/beameroff", [1.0]), ("/api/projector", {"id": "all", "action": "off"}))
        self.assertIsNone(osc.translate("/beameron", [0.0]))                                       # the release does nothing


if __name__ == "__main__":
    unittest.main()
