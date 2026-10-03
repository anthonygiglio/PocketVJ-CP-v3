# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import hashlib
import ipaddress
import json
import os
import re
import socket
import socketserver
import tempfile
import threading
import time
import unittest
from unittest import mock

from pvj import osc, projector, scheduler
from tests.test_server import ServerBase


class FakeProjector:
    """A PJLink class 1 projector on 127.0.0.1, written from the standard ("PJLink Specifications" version 1.04,
    JBMIA, 2013-12-10), not from our client. Section numbers below are that document's.

    - 2.1, 2.2: a command is "%1" + four characters + a space + a parameter of up to 128 bytes + CR alone; the
      answer is "%1" + the same four characters + "=" + a parameter + CR. The command body is case-insensitive.
    - 2.3, 2.4: OK, ERR1 (undefined command), ERR2 (out of parameter), ERR3 (unavailable time), ERR4 (failure).
    - 4.1, 4.2 POWR: 0 standby, 1 on, 2 cooling, 3 warm-up; off => warm-up => on => cooling => off.
    - 4.3, 4.4 INPT: a type 1 to 5 (RGB, VIDEO, DIGITAL, STORAGE, NETWORK) and a number 1 to 9; ERR2 for an input
      that does not exist, ERR3 in standby and such.
    - 4.5, 4.6 AVMT: 11/10 video, 21/20 audio, 31/30 both; the query answers 11, 21, 31 or 30 only; ERR2 for a
      video-only or audio-only mute on a product without one; ERR3 in standby.
    - 4.7 ERST: six digits, fan, lamp, temperature, cover open, filter, other; 0 none, 1 warning, 2 error.
    - 4.8 LAMP: "hours on" per lamp, up to 8, hours 0 to 99999; ERR1 on a display without a lamp.
    - 4.9 INST: the inputs, separated by spaces. 4.10 NAME: UTF-8, 0 to 64 characters. 4.11 to 4.13 INF1, INF2,
      INFO: ASCII, 0 to 32 characters. 4.14 CLSS: "1".
    - 5: "PJLINK 0", or "PJLINK 1 <8 hex digits>" and then the first command is prefixed with the 32 character
      MD5 of the random number followed by the password; a wrong one is answered "PJLINK ERRA". Later commands
      on the same connection may leave the digest out (5.3). No command for 30 seconds ends the connection.
    - 6: a command that does not meet the format gets no answer at all.

    `slow`: power changes pass through warm-up and cooling and stay there until finish() is called (a test's
    stand-in for the minute a real projector takes). `standby_answers` False: INST, NAME and the other
    information queries are "unavailable" in standby, which 2.4 allows. `lowercase`: answers in lower case."""

    def __init__(self, password="", inputs=("11", "31", "32"), lamps=(1234,), separate_mute=True, slow=False,
                 standby_answers=True, lowercase=False, name="Fake projector"):
        self.password, self.inputs, self.lamps, self.separate_mute = password, list(inputs), list(lamps), separate_mute
        self.slow, self.standby_answers, self.lowercase, self.name = slow, standby_answers, lowercase, name
        self.maker, self.model, self.info = "NXLX Test Works", "FP-1", "fake, firmware 0.1"
        self.power, self.input, self.video_mute, self.audio_mute, self.errors, self.fault = "0", self.inputs[0], False, False, "000000", False
        self.received, self.raw, self.times, self.connections = [], [], [], 0
        self.guard = threading.Lock()
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def read_line(self):
                buf = b""
                while not buf.endswith(b"\r") and len(buf) < 300:
                    c = self.rfile.read(1)
                    if not c:
                        return None
                    buf += c
                outer.raw.append(buf)
                return buf[:-1].decode("utf-8", "replace")

            def handle(self):
                rnd = "498e4a67"
                self.request.settimeout(30)
                with outer.guard:
                    outer.connections += 1
                try:
                    self.wfile.write(("PJLINK 1 %s\r" % rnd if outer.password else "PJLINK 0\r").encode())
                    first = True
                    while True:
                        line = self.read_line()
                        if line is None:
                            return
                        outer.received.append(line)
                        outer.times.append(time.monotonic())
                        if outer.password:
                            want = hashlib.md5((rnd + outer.password).encode()).hexdigest()
                            if first and not line.startswith(want):
                                self.wfile.write(b"PJLINK ERRA\r")
                                continue
                            if line.startswith(want):
                                line = line[32:]
                        first = False
                        m = re.fullmatch(r"%1([A-Za-z0-9]{4}) (.{1,128})", line)
                        if not m:
                            continue                       # not a command line: no answer (chapter 6)
                        cmd = m.group(1).upper()
                        with outer.guard:
                            answer = "%%1%s=%s\r" % (cmd, outer.answer(cmd, m.group(2)))
                        self.wfile.write((answer.lower() if outer.lowercase and cmd in ("POWR", "CLSS", "INF1") else answer).encode("utf-8"))
                except OSError:
                    pass

        class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
            daemon_threads = True
            allow_reuse_address = True
        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def mute(self):
        """What AVMT ? answers (4.6)."""
        return "31" if self.video_mute and self.audio_mute else "11" if self.video_mute else "21" if self.audio_mute else "30"

    def finish(self):
        """The warm-up or the cooling is over."""
        with self.guard:
            self.power = {"3": "1", "2": "0"}.get(self.power, self.power)

    def answer(self, cmd, val):
        on = self.power == "1"
        if self.fault and cmd != "ERST":
            return "ERR4" if cmd in ("POWR", "INPT", "AVMT", "LAMP", "INST", "NAME", "INF1", "INF2", "INFO", "CLSS") else "ERR1"
        if cmd == "POWR":
            if val == "?":
                return self.power
            if val not in ("0", "1"):
                return "ERR2"
            if self.power in ("2", "3"):
                return "ERR3"                              # in transition
            if val != self.power:
                self.power = (("3" if val == "1" else "2") if self.slow else val)
                if val == "0":
                    self.video_mute = self.audio_mute = False
            return "OK"
        if cmd == "INPT":
            if not on:
                return "ERR3"
            if val == "?":
                return self.input
            if not re.fullmatch(r"[1-5][1-9]", val) or val not in self.inputs:
                return "ERR2"
            self.input = val
            return "OK"
        if cmd == "AVMT":
            if val != "?" and val not in ("10", "11", "20", "21", "30", "31"):
                return "ERR2"
            if not on:
                return "ERR3"
            if val == "?":
                return self.mute
            if val[0] in "12" and not self.separate_mute:
                return "ERR2"
            if val[0] in "13":
                self.video_mute = val[1] == "1"
            if val[0] in "23":
                self.audio_mute = val[1] == "1"
            return "OK"
        if cmd not in ("ERST", "LAMP", "INST", "NAME", "INF1", "INF2", "INFO", "CLSS"):
            return "ERR1"
        if val != "?":
            return "ERR2"
        if cmd == "ERST":
            return self.errors
        if cmd == "LAMP":
            return " ".join("%d %d" % (h, 1 if on else 0) for h in self.lamps) if self.lamps else "ERR1"
        if not on and not self.standby_answers:
            return "ERR3"
        return {"INST": " ".join(self.inputs), "NAME": self.name, "INF1": self.maker, "INF2": self.model,
                "INFO": self.info, "CLSS": "1"}[cmd]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return bool(cond())


LOOPBACK_OK = mock.patch.object(projector, "PRIVATE", projector.PRIVATE + [ipaddress.ip_network("127.0.0.0/8")])


class PrivateNetworkTest(unittest.TestCase):
    def test_only_private_addresses(self):
        for good in ("192.168.0.50", "10.1.2.3", "172.16.0.9", "169.254.10.20", "fe80::1"):
            self.assertEqual(projector.private_address(good), good)
        for bad in ("8.8.8.8", "127.0.0.1", "0.0.0.0", "172.32.0.1", "::1", "", None, "a b", "http://x", "projector;rm"):
            with self.assertRaises(projector.ProjectorError, msg=bad):
                projector.private_address(bad)

    def test_names_dns_cannot_encode_and_the_metadata_address_are_clear_errors(self):
        for bad in ("a..b", "..", "a" * 64 + ".lan", "169.254.169.254"):
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
            fake.lamps = []                                                             # a display without a lamp (4.8)
            for unknown in ("LAMP ?", "FREZ ?"):                                        # FREZ is class 2
                with self.assertRaises(projector.ProjectorError) as cm:
                    projector.PJLink("127.0.0.1", fake.port, "right").command(unknown)
                self.assertIn("does not know", str(cm.exception))
                self.assertEqual(cm.exception.code, "ERR1")
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

    def test_one_command_at_a_time_per_projector(self):
        with LOOPBACK_OK:
            fake = FakeProjector()
            self.addCleanup(fake.close)
            live, peak, guard = [0], [0], threading.Lock()
            real = socket.create_connection

            def counting_connect(addr, timeout=None):
                with guard:
                    live[0] += 1
                    peak[0] = max(peak[0], live[0])
                s = real(addr, timeout=timeout)

                class Counted:
                    def __getattr__(self, name):
                        return getattr(s, name)

                    def close(self):
                        with guard:
                            live[0] -= 1
                        s.close()
                return Counted()
            links = [projector.PJLink("127.0.0.1", fake.port, connect=counting_connect) for _ in range(6)]
            ts = [threading.Thread(target=l.state) for l in links]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(peak[0], 1)

    def test_connect_shares_the_deadline(self):
        seen = []

        def connect(addr, timeout=None):
            seen.append(timeout)
            raise OSError("refused")
        with self.assertRaises(projector.ProjectorError):
            projector.PJLink("192.168.0.9", timeout=3, connect=connect).power(True)
        self.assertLessEqual(seen[0], 3)

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


class Phase1Test(unittest.TestCase):
    """Input, the separate mutes, lamp hours, warnings and the details, against the fake from the standard."""

    def setUp(self):
        p = LOOPBACK_OK
        p.start()
        self.addCleanup(p.stop)

    def pair(self, **kw):
        fake = FakeProjector(**kw)
        self.addCleanup(fake.close)
        return fake, projector.PJLink("127.0.0.1", fake.port, kw.get("password", ""))

    def test_identify_reads_who_the_projector_is(self):
        fake, link = self.pair(name="Saal 1 \u00e9\u00df", lowercase=True, password="pw")
        self.assertEqual(link.identify(), {"class": "1", "name": "Saal 1 \u00e9\u00df", "maker": "nxlx test works", "model": "FP-1",
                                           "info": "fake, firmware 0.1", "inputs": ["11", "31", "32"]})
        fake.name = ""                                    # "enter CR directly after =" (4.10)
        self.assertEqual(link.identify()["name"], "")
        fake.name = "x" * 64 + "\x07tail"
        self.assertEqual(link.identify()["name"], "x" * 64)

    def test_identify_in_standby_keeps_what_it_got_and_an_unplugged_one_costs_one_try(self):
        fake, link = self.pair(standby_answers=False)
        self.assertEqual(link.identify(), {"class": None, "name": None, "maker": None, "model": None, "info": None, "inputs": None})
        tries = []

        def refused(addr, timeout=None):
            tries.append(addr)
            raise OSError("refused")
        with self.assertRaises(projector.ProjectorError) as cm:
            projector.PJLink("192.168.0.9", connect=refused).identify()
        self.assertEqual((len(tries), cm.exception.code), (1, "unreachable"))

    def test_input_from_the_projectors_own_list(self):
        fake, link = self.pair(slow=True)
        self.assertEqual(link.inputs(), ["11", "31", "32"])
        with self.assertRaises(projector.ProjectorError) as cm:
            link.set_input("31")                          # standby: unavailable time
        self.assertEqual(cm.exception.code, "ERR3")
        link.power(True)
        with self.assertRaises(projector.ProjectorError) as cm:
            link.set_input("31")                          # warming up
        self.assertEqual(cm.exception.code, "ERR3")
        fake.finish()
        link.set_input("31")
        self.assertEqual((fake.input, link.input()), ("31", "31"))
        with self.assertRaises(projector.ProjectorError) as cm:
            link.set_input("59")                          # a valid number this projector does not have
        self.assertEqual((cm.exception.code, fake.input), ("ERR2", "31"))
        before = len(fake.received)
        for bad in ("61", "3", "31\r%1POWR 0", 31, None, "1A"):
            with self.assertRaises(projector.ProjectorError, msg=str(bad)):
                link.set_input(bad)
        self.assertEqual(len(fake.received), before)      # never sent

    def test_picture_and_sound_mute_apart_and_together(self):
        fake, link = self.pair()
        with self.assertRaises(projector.ProjectorError) as cm:
            link.mute(True, "picture")                    # standby
        self.assertEqual(cm.exception.code, "ERR3")
        link.power(True)
        link.mute(True, "picture")
        self.assertEqual((fake.mute, link.mute_state()), ("11", {"picture": True, "sound": False}))
        link.mute(True, "sound")
        self.assertEqual((fake.mute, link.mute_state()), ("31", {"picture": True, "sound": True}))
        link.mute(False, "picture")
        self.assertEqual((fake.mute, link.mute_state()), ("21", {"picture": False, "sound": True}))
        link.mute(False)
        self.assertEqual((fake.mute, link.mute_state()), ("30", {"picture": False, "sound": False}))
        self.assertEqual([r[2:] for r in fake.received if r[2:].startswith("AVMT 1") or r[2:].startswith("AVMT 2")],
                         ["AVMT 11", "AVMT 11", "AVMT 21", "AVMT 10"])         # the first one was refused in standby
        fake.separate_mute = False
        for what in ("picture", "sound"):
            with self.assertRaises(projector.ProjectorError) as cm:
                link.mute(True, what)
            self.assertIn("separately", str(cm.exception))
        link.mute(True)
        self.assertEqual(fake.mute, "31")

    def test_lamp_hours_and_warnings(self):
        fake, link = self.pair()
        self.assertEqual(link.lamps(), [{"hours": 1234, "on": False}])
        link.power(True)
        fake.lamps = [0, 99999]
        self.assertEqual(link.lamps(), [{"hours": 0, "on": True}, {"hours": 99999, "on": True}])
        fake.lamps = []
        with self.assertRaises(projector.ProjectorError) as cm:
            link.lamps()
        self.assertEqual(cm.exception.code, "ERR1")
        self.assertEqual(set(link.warnings().values()), {"ok"})
        fake.errors = "012012"
        self.assertEqual(link.warnings(), {"fan": "ok", "lamp": "warning", "temperature": "error", "cover": "ok",
                                           "filter": "warning", "other": "error"})
        fake.fault = True
        with self.assertRaises(projector.ProjectorError) as cm:
            link.state()
        self.assertEqual(cm.exception.code, "ERR4")

    def test_every_line_ends_with_cr_alone_and_one_command_per_connection(self):
        fake, link = self.pair(password="pw")
        link.power(True)
        link.identify()
        link.set_input("32")
        link.mute(True, "sound")
        link.lamps(), link.warnings(), link.input(), link.mute_state()
        self.assertEqual(len(fake.raw), fake.connections)
        self.assertEqual(len(fake.raw), 13)
        for raw in fake.raw:
            self.assertTrue(raw.endswith(b"\r") and b"\n" not in raw and raw.count(b"\r") == 1, raw)
            self.assertRegex(raw.decode(), r"^[0-9a-f]{32}%1[A-Z0-9]{4} ")       # the digest on every one
        self.assertNotIn(b"pw", b"".join(fake.raw))                              # the password itself never travels

    def test_a_public_address_is_refused_before_every_new_command(self):
        def connect(addr, timeout=None):
            raise AssertionError("connected to %s" % (addr,))
        public = lambda h, p, proto=0: [(0, 0, 0, "", ("93.184.216.34", p))]
        for host, resolve in (("8.8.8.8", socket.getaddrinfo), ("beamer.example", public)):
            link = projector.PJLink(host, connect=connect, resolve=resolve)
            for call in (link.identify, link.inputs, link.input, lambda: link.set_input("31"), lambda: link.mute(True, "picture"),
                         link.mute_state, link.lamps, link.warnings):
                with self.assertRaises(projector.ProjectorError) as cm:
                    call()
                self.assertIn("private", str(cm.exception))

    def test_answers_that_are_not_in_the_standard_are_refused(self):
        fake, link = self.pair()
        link.power(True)
        for attr, value, call in (("errors", "00000", link.warnings), ("errors", "000003", link.warnings),
                                  ("lamps", [123456], link.lamps), ("input", "6A", link.input)):
            old = getattr(fake, attr)
            setattr(fake, attr, value)
            with self.assertRaises(projector.ProjectorError, msg=attr):
                call()
            setattr(fake, attr, old)
        fake.inputs = ["11", "zz", "31", "31", "6A"] + ["2%d" % (i % 9 + 1) for i in range(60)]
        got = link.inputs()
        self.assertEqual(got[:2], ["11", "31"])
        self.assertTrue(all(re.fullmatch(r"[1-5][1-9]", c) for c in got) and len(got) == len(set(got)) <= 50)

    def test_labels(self):
        self.assertEqual(projector.validate_label(["31"], "31", " Matrix "), "Matrix")
        self.assertEqual(projector.validate_label(["31"], "31", ""), "")
        for code, label in (("32", "Box"), ("31", "x" * 25), ("31", "a\nb"), ("31", 5), (31, "Box"), ("31\n", "Box")):
            with self.assertRaises(projector.ProjectorError, msg=str((code, label))):
                projector.validate_label(["31"], code, label)
        self.assertEqual(projector.input_name("31"), "Digital 1")


class MonitorTest(unittest.TestCase):
    """The background status: per projector, staggered, stopped with the module, never more than one thread each."""

    def setUp(self):
        from pvj.settings import Settings
        from pvj.modules import Registry
        p = LOOPBACK_OK
        p.start()
        self.addCleanup(p.stop)
        self.path = os.path.join(tempfile.mkdtemp(), "settings.json")
        self.settings = Settings(self.path)
        self.settings.load()
        self.registry = Registry(self.settings, "x86")
        self.registry.set_enabled("projector", True)
        outer = self

        class Api:
            settings, registry = self.settings, self.registry

            def _pjlink(self, e):
                return projector.PJLink(e["host"], e["port"], e["password"], timeout=outer.timeout)
        self.timeout, self.logged = 1.0, []
        self.mon = projector.Monitor(Api(), interval=0.15, changing=0.05, stagger=0.0, retry_for=1.5, retry_every=0.1, log=self.logged.append)
        self.addCleanup(self.mon.stop, True)

    def add(self, fake=None, port=None, **kw):
        if fake is None and port is None:
            fake = FakeProjector(**kw)
        if fake is not None:
            self.addCleanup(fake.close)
        e = projector.validate({"name": "P%d" % len(self.settings.data["projectors"]), "host": "127.0.0.1", "port": port or fake.port,
                                "password": kw.get("password", "")})
        with self.settings.lock:
            self.settings.data["projectors"] = self.settings.data["projectors"] + [e]
            self.settings.save()
        return fake, e

    def entry(self, pid):
        return [p for p in self.settings.data["projectors"] if p["id"] == pid][0]

    def power(self, pid):
        return self.mon.status(pid).get("power")

    def test_the_status_follows_the_projector_without_being_asked(self):
        fake, e = self.add(slow=True, password="pw")
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off"))
        st = self.mon.status(e["id"])
        self.assertEqual((st["ok"], st["input"], st["mute"], st["lamps"], st["warnings"]["fan"]),
                         (True, None, None, [{"hours": 1234, "on": False}], "ok"))
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        d = self.entry(e["id"])["details"]
        self.assertEqual((d["maker"], d["model"], d["class"], d["inputs"]), ("NXLX Test Works", "FP-1", "1", ["11", "31", "32"]))
        self.mon.api._pjlink(e).power(True)
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "warming up"))
        fake.finish()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "on"))
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"])["input"] == "11"))
        self.assertEqual(self.mon.status(e["id"])["mute"], {"picture": False, "sound": False})
        self.mon.api._pjlink(e).power(False)
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "cooling down"))
        fake.finish()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off"))
        with open(self.path) as f:
            saved = f.read()
        self.assertNotIn("power", saved)                  # the status lives in memory only
        self.assertNotIn("warming", saved)

    def test_switching_the_module_on_and_off_never_piles_up_threads(self):
        fakes = [self.add()[0] for _ in range(3)]
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        self.add(port=dead.getsockname()[1])              # nobody listens there
        dead.close()
        peak = 0
        for i in range(20):
            self.registry.set_enabled("projector", i % 2 == 0)
            self.mon.apply()
            peak = max(peak, len(self.mon.threads()))
            time.sleep(0.01 * (i % 3))
        self.assertLessEqual(peak, 4)                     # one per projector, also while an old one is still ending
        self.registry.set_enabled("projector", True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: len(self.mon.threads()) == 4 and all(self.mon.status(p["id"]).get("ok") is not None
                                                                              for p in self.settings.data["projectors"])))
        self.assertLessEqual(len([t for t in threading.enumerate() if t.name == "projector-poll"]), 4)
        self.registry.set_enabled("projector", False)
        self.mon.apply()
        start = time.monotonic()
        self.assertTrue(wait_for(lambda: not [t for t in threading.enumerate() if t.name == "projector-poll"], 1.5))
        self.assertLess(time.monotonic() - start, 1.5)
        asked = [len(f.received) for f in fakes]
        time.sleep(0.5)
        self.assertEqual([len(f.received) for f in fakes], asked)         # and nothing is asked any more
        self.assertEqual(self.mon.status(self.settings.data["projectors"][0]["id"]).get("power"), None)

    def test_a_worker_that_stops_without_apply_also_ends(self):
        fake, e = self.add()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off"))
        self.registry.set_enabled("projector", False)     # switched off behind its back
        self.assertTrue(wait_for(lambda: not self.mon.threads(), 1.5))
        self.assertEqual(self.mon.status(e["id"]).get("power"), None)

    def test_a_removed_projector_is_let_go_and_details_do_not_bring_it_back(self):
        fake, e = self.add()
        fake2, e2 = self.add()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off" and self.power(e2["id"]) == "off"))
        with self.settings.lock:
            self.settings.data["projectors"] = [p for p in self.settings.data["projectors"] if p["id"] != e["id"]]
            self.settings.save()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: len(self.mon.threads()) == 1, 1.5))
        self.assertEqual(self.mon.status(e["id"]).get("power"), None)
        self.assertIsNone(self.mon.identify(e))           # answered, but there is nowhere to keep it
        self.assertEqual([p["id"] for p in self.settings.data["projectors"]], [e2["id"]])
        self.mon.stop(final=True)
        self.assertTrue(wait_for(lambda: not self.mon.threads(), 1.5))
        self.mon.apply()
        self.assertEqual(self.mon.threads(), [])          # closed for good

    def test_an_unreachable_projector_is_reported_not_guessed(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        _, e = self.add(port=s.getsockname()[1])
        s.close()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"]).get("ok") is False))
        self.assertIn("cannot reach", self.mon.status(e["id"])["error"])
        row = self.mon.health()[0]
        self.assertEqual((row["state"], row["name"]), ("unknown", "P0"))
        self.assertIn("No answer", row["text"])
        self.assertIsNone(self.entry(e["id"]).get("details"))

    def test_projectors_are_not_all_asked_at_once(self):
        self.mon.stagger = 0.4
        a, _ = self.add()
        b, _ = self.add()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: any("POWR ?" in r for r in b.received)))
        first = lambda f: [t for r, t in zip(f.received, f.times) if "POWR ?" in r][0]
        self.assertGreater(first(b) - first(a), 0.3)

    def test_the_input_list_is_read_once_the_projector_is_on(self):
        fake, e = self.add(standby_answers=False, slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.power(e["id"]) == "off" and self.entry(e["id"]).get("details")))
        self.assertIsNone(self.entry(e["id"])["details"]["inputs"])
        self.mon.api._pjlink(e).power(True)
        fake.finish()
        self.assertTrue(wait_for(lambda: self.entry(e["id"])["details"]["inputs"] == ["11", "31", "32"]))
        self.assertEqual(self.entry(e["id"])["details"]["maker"], "NXLX Test Works")
        n = len([r for r in fake.received if "INST" in r])
        time.sleep(0.5)
        self.assertEqual(len([r for r in fake.received if "INST" in r]), n)       # not asked again at every check

    def test_details_keep_older_values_and_drop_labels_of_inputs_that_went(self):
        fake, e = self.add()
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        with self.settings.lock:
            self.settings.data["projectors"] = [dict(self.entry(e["id"]), labels={"31": "Matrix", "32": "Box"})]
        fake.standby_answers = False
        d = self.mon.identify(self.entry(e["id"]))
        self.assertEqual((d["maker"], d["inputs"]), ("NXLX Test Works", ["11", "31", "32"]))
        fake.standby_answers, fake.inputs = True, ["11", "31"]
        self.mon.identify(self.entry(e["id"]))
        self.assertEqual(self.entry(e["id"])["labels"], {"31": "Matrix"})
        self.assertEqual(self.entry(e["id"])["password"], "")

    def test_an_input_change_while_warming_up_is_retried_until_it_works(self):
        fake, e = self.add(slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        self.mon.api._pjlink(e).power(True)               # now warming up
        self.assertEqual(self.mon.set_input(self.entry(e["id"]), "31"), {"pending": True})
        self.assertEqual(self.mon.status(e["id"])["pending_input"], "31")
        time.sleep(0.4)
        self.assertEqual(fake.input, "11")
        self.assertGreaterEqual(len([r for r in fake.received if r.endswith("INPT 31")]), 3)
        fake.finish()
        self.assertTrue(wait_for(lambda: fake.input == "31"))
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"])["notice"] == {"ok": True, "text": "Input switched to Digital 1."}))
        self.assertIsNone(self.mon.status(e["id"])["pending_input"])
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"])["input"] == "31"))
        self.assertEqual(self.mon.set_input(self.entry(e["id"]), "32"), {"pending": False})      # on: at once
        self.assertIsNone(self.mon.status(e["id"])["notice"])
        self.assertEqual(len(self.mon.threads()), 1)

    def test_the_retry_gives_up_and_says_so_plainly(self):
        fake, e = self.add(slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        with self.settings.lock:
            self.settings.data["projectors"] = [dict(self.entry(e["id"]), labels={"31": "Matrix"})]
        self.mon.api._pjlink(e).power(True)               # warming up, and it stays there
        start = time.monotonic()
        self.assertEqual(self.mon.set_input(self.entry(e["id"]), "31"), {"pending": True})
        self.assertTrue(wait_for(lambda: self.mon.status(e["id"])["notice"]))
        took = time.monotonic() - start
        self.assertTrue(1.0 < took < 2.5, took)           # retry_for is 1.5 here (90 in the panel)
        n = self.mon.status(e["id"])["notice"]
        self.assertEqual(n["ok"], False)
        self.assertIn("Could not switch to Matrix: it was still not ready after 1 seconds", n["text"])
        self.assertIn("Could not switch to Matrix", self.logged[-1])
        self.assertIsNone(self.mon.status(e["id"])["pending_input"])
        sent = len([r for r in fake.received if r.endswith("INPT 31")])
        self.assertLessEqual(sent, 17)
        fake.finish()
        time.sleep(0.4)
        self.assertEqual((fake.input, len([r for r in fake.received if r.endswith("INPT 31")])), ("11", sent))    # it really stopped

    def test_a_newer_choice_replaces_the_one_being_retried_and_a_refusal_ends_it(self):
        fake, e = self.add(slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        self.mon.api._pjlink(e).power(True)
        self.mon.set_input(self.entry(e["id"]), "31")
        self.assertEqual(self.mon.set_input(self.entry(e["id"]), "32"), {"pending": True})
        self.assertEqual(self.mon.status(e["id"])["pending_input"], "32")
        time.sleep(0.3)
        n31 = len([r for r in fake.received if r.endswith("INPT 31")])
        fake.finish()
        self.assertTrue(wait_for(lambda: fake.input == "32"))
        time.sleep(0.3)
        self.assertEqual((fake.input, len([r for r in fake.received if r.endswith("INPT 31")])), ("32", n31))
        self.assertLessEqual(n31, 2)
        with self.assertRaises(projector.ProjectorError):                 # a plain refusal is not retried
            self.mon.set_input(self.entry(e["id"]), "59")
        self.assertIsNone(self.mon.status(e["id"])["pending_input"])

    def test_switching_the_module_off_ends_a_retry(self):
        fake, e = self.add(slow=True)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.entry(e["id"]).get("details")))
        self.mon.api._pjlink(e).power(True)
        self.mon.set_input(self.entry(e["id"]), "31")
        self.registry.set_enabled("projector", False)
        self.mon.apply()
        self.assertTrue(wait_for(lambda: not self.mon.threads(), 1.5))
        fake.finish()
        time.sleep(0.4)
        self.assertEqual(fake.input, "11")

    def test_health_rows_show_lamp_hours_and_warnings(self):
        fake, e = self.add(lamps=(1200, 800))
        self.assertEqual(self.mon.health(), [{"id": e["id"], "name": "P0", "state": "unknown", "text": "Not checked yet."}])
        self.mon.apply()
        self.assertTrue(wait_for(lambda: self.mon.health()[0]["state"] == "ok"))
        self.assertEqual(self.mon.health()[0]["text"], "Off, lamps 1200, 800 h.")
        fake.errors = "100010"
        self.assertTrue(wait_for(lambda: self.mon.health()[0]["state"] == "warn"))
        self.assertEqual(self.mon.health()[0]["text"], "Off, lamps 1200, 800 h. Warning: fan, filter.")
        fake.errors = "102010"
        self.assertTrue(wait_for(lambda: self.mon.health()[0]["state"] == "bad"))
        self.assertEqual(self.mon.health()[0]["text"], "Off, lamps 1200, 800 h. Error: temperature. Warning: fan, filter.")
        self.registry.set_enabled("projector", False)
        self.assertEqual(self.mon.health(), [])


class ApiTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.full = self.call("POST", "/api/pair", {"pin": self.pin, "name": "t"})[1]["token"]
        self.assertEqual(self.post("/api/projectors", {"add": {"host": "192.168.0.5"}})[0], 409)       # module off
        self.call("POST", "/api/modules/projector", {"enabled": True}, token=self.full)
        self.addCleanup(self.api.projectors.stop, True)
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

    def test_background_answers_at_once_and_still_switches(self):
        self.add()
        st, body, _ = self.post("/api/projector", {"id": "all", "action": "on", "background": True})
        self.assertEqual((st, body), (200, {"started": True}))
        import time as _t
        end = _t.monotonic() + 5
        while self.fake.power != "1" and _t.monotonic() < end:
            _t.sleep(0.05)
        self.assertEqual(self.fake.power, "1")
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "on", "background": "yes"})[0], 400)

    def test_an_unexpected_error_is_a_502_not_a_crash(self):
        pid = self.add()[1]["projectors"][0]["id"]

        class Broken:
            def power(self, on):
                raise RuntimeError("boom")
        with mock.patch.object(self.api, "_pjlink", lambda p: Broken()):
            st, body, _ = self.post("/api/projector", {"id": pid, "action": "on"})
        self.assertEqual(st, 502)
        self.assertIn("boom", body["error"])

    def test_a_slow_name_lookup_does_not_hold_the_settings_lock(self):
        api = self.api
        held = []

        def check(host, resolve=None):
            got = []
            t = threading.Thread(target=lambda: got.append(api.settings.lock.acquire(timeout=1)) or (got[0] and api.settings.lock.release()))
            t.start()
            t.join()
            held.append(not got[0])
            return "192.168.0.7"
        with mock.patch.object(projector, "private_address", check):
            self.assertEqual(self.post("/api/projectors", {"add": {"host": "beamer.lan"}})[0], 200)
            self.api.projectors.stop(final=True)          # the background check looks the name up too, never under the lock either
        self.assertTrue(held)
        self.assertNotIn(True, held)

    def projector(self):
        return self.call("GET", "/api/projectors", token=self.full)[1]["projectors"][0]

    def test_details_inputs_labels_and_status_are_listed_without_the_password(self):
        st, body, _ = self.add()
        pid = body["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["details"] and self.projector()["status"].get("power") == "off"))
        p = self.projector()
        self.assertEqual((p["details"]["maker"], p["details"]["model"], p["details"]["class"]), ("NXLX Test Works", "FP-1", "1"))
        self.assertEqual(p["inputs"], [{"code": "11", "name": "RGB 1", "label": ""}, {"code": "31", "name": "Digital 1", "label": ""},
                                       {"code": "32", "name": "Digital 2", "label": ""}])
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.post("/api/projectors", {"label": {"id": pid, "input": "31", "label": "Matrix"}}, token=live)[0], 403)
        st, body, _ = self.post("/api/projectors", {"label": {"id": pid, "input": "31", "label": "Matrix"}})
        self.assertEqual((st, body["projectors"][0]["inputs"][1]), (200, {"code": "31", "name": "Digital 1", "label": "Matrix"}))
        for bad in ({"id": pid, "input": "59", "label": "x"}, {"id": pid, "input": "31", "label": "x" * 25}, {"id": pid, "input": "31"}, "x"):
            self.assertEqual(self.post("/api/projectors", {"label": bad})[0], 400 if isinstance(bad, dict) else 404, bad)
        self.assertEqual(self.post("/api/projectors", {"label": {"id": "nope", "input": "31", "label": "x"}})[0], 404)
        self.assertEqual(self.post("/api/projectors", {"label": {"id": pid, "input": "31", "label": ""}})[1]["projectors"][0]["inputs"][1]["label"], "")
        for path in ("/api/projectors", "/api/health"):
            self.assertNotIn("pw1", json.dumps(self.call("GET", path, token=self.full)[1]))
        self.assertNotIn("password", [k for k in self.projector() if k != "has_password"])

    def test_input_and_the_separate_mutes(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertEqual(self.post("/api/projector", {"id": "all", "action": "input", "input": "31"})[0], 400)
        self.assertTrue(wait_for(lambda: self.projector()["details"]))
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "on"})[0], 200)
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        view = self.post("/api/devices/invite", {"name": "g", "role": "view"})[1]["token"]
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "input", "input": "31"}, token=view)[0], 403)
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "input", "input": "31"}, token=live)
        self.assertEqual((st, body["results"][pid], self.fake.input), (200, {"ok": True, "pending": False}, "31"))
        for bad in ("59", "31\r", 31, None, ""):
            self.assertEqual(self.post("/api/projector", {"id": pid, "action": "input", "input": bad})[0], 400, bad)
        self.assertEqual(self.fake.input, "31")
        for action, want in (("mute_picture", "11"), ("mute_sound", "31"), ("unmute_picture", "21"), ("unmute_sound", "30"),
                             ("mute", "31"), ("unmute", "30")):
            self.assertEqual(self.post("/api/projector", {"id": pid, "action": action}, token=live)[0], 200, action)
            self.assertEqual(self.fake.mute, want, action)
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("input") == "31"))       # poked, not 45 seconds later
        self.assertEqual(self.projector()["status"]["mute"], {"picture": False, "sound": False})
        self.fake.separate_mute = False
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "mute_sound"})
        self.assertEqual(st, 502)
        self.assertIn("separately", body["error"])

    def test_an_input_change_refused_as_unavailable_goes_to_the_background(self):
        pid = self.add()[1]["projectors"][0]["id"]
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "input", "input": "31"})
        self.assertIn(st, (200, 409))                     # 409 if the details were not read yet
        self.assertTrue(wait_for(lambda: self.projector()["details"]))
        self.api.projectors.retry_every = 0.1
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "input", "input": "32"})       # the projector is off
        self.assertEqual((st, body["results"][pid]), (200, {"ok": True, "pending": True}))
        self.assertEqual(self.projector()["status"]["pending_input"], "32")
        self.post("/api/projector", {"id": pid, "action": "on"})
        self.assertTrue(wait_for(lambda: self.fake.input == "32"))
        self.assertTrue(wait_for(lambda: (self.projector()["status"]["notice"] or {}).get("ok") is True))

    def test_inputs_not_known_yet_is_a_clear_refusal(self):
        self.fake.standby_answers = False
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["details"]))
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "input", "input": "31"})
        self.assertEqual(st, 409)
        self.assertIn("not known yet", body["error"])
        self.assertEqual(self.projector()["inputs"], [])

    def test_refresh_details(self):
        pid = self.add()[1]["projectors"][0]["id"]
        self.assertTrue(wait_for(lambda: self.projector()["details"]))
        self.fake.model, self.fake.inputs = "FP-2", ["31", "51"]
        live = self.post("/api/devices/invite", {"name": "g", "role": "live"})[1]["token"]
        self.assertEqual(self.post("/api/projector", {"id": pid, "action": "identify"}, token=live)[0], 200)
        p = self.projector()
        self.assertEqual((p["details"]["model"], [i["name"] for i in p["inputs"]]), ("FP-2", ["Digital 1", "Network 1"]))
        self.fake.close()
        st, body, _ = self.post("/api/projector", {"id": pid, "action": "identify"})
        self.assertEqual(st, 502)
        self.assertEqual(self.projector()["details"]["model"], "FP-2")

    def test_adding_answers_at_once_even_if_the_projector_is_not_there(self):
        self.fake.close()
        start = time.monotonic()
        st, body, _ = self.add()
        self.assertEqual(st, 200)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual((body["projectors"][0]["details"], body["projectors"][0]["inputs"]), (None, []))

    def test_switching_the_module_off_stops_the_background_checks(self):
        self.add()
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
        self.assertEqual(len(self.api.projectors.threads()), 1)
        self.call("POST", "/api/modules/projector", {"enabled": False}, token=self.full)
        self.assertTrue(wait_for(lambda: not self.api.projectors.threads(), 1.5))
        self.assertEqual(self.projector()["status"], None)
        self.assertEqual(self.call("GET", "/api/health", token=self.full)[1]["projectors"], [])
        self.call("POST", "/api/modules/projector", {"enabled": True}, token=self.full)
        self.assertTrue(wait_for(lambda: self.projector()["status"].get("power") == "off"))
        self.assertEqual(len(self.api.projectors.threads()), 1)
        pid = self.projector()["id"]
        self.post("/api/projectors", {"remove": pid})
        self.assertTrue(wait_for(lambda: not self.api.projectors.threads(), 1.5))

    def test_health_shows_lamp_hours_and_a_projector_warning(self):
        self.add()
        health = lambda: self.call("GET", "/api/health", token=self.full)[1]
        self.assertTrue(wait_for(lambda: health()["projectors"] and health()["projectors"][0]["state"] == "ok"))
        self.assertEqual((health()["projectors"][0]["name"], health()["projectors"][0]["text"]), ("Main", "Off, lamp 1234 h."))
        before = health()["overall"]
        self.fake.errors = "020000"
        self.api.projectors.poke("all")
        self.assertTrue(wait_for(lambda: health()["projectors"][0]["state"] == "bad"))
        self.assertEqual((health()["projectors"][0]["text"], health()["overall"]), ("Off, lamp 1234 h. Error: lamp.", "bad"))
        self.fake.errors = "000000"
        self.api.projectors.poke("all")
        self.assertTrue(wait_for(lambda: health()["overall"] == before))

    def test_a_background_failure_is_logged(self):
        self.add()
        self.fake.close()
        lines = []
        with mock.patch.object(self.api, "log", lines.append):
            self.assertEqual(self.post("/api/projector", {"id": "all", "action": "on", "background": True})[1], {"started": True})
            self.assertTrue(wait_for(lambda: lines))
        self.assertIn("projector on", lines[0])
        self.assertIn("cannot reach", lines[0])

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
        self.assertEqual(osc.translate("/beameron", [1.0]), ("/api/projector", {"id": "all", "action": "on", "background": True}))
        self.assertEqual(osc.translate("/beameroff", [1.0]), ("/api/projector", {"id": "all", "action": "off", "background": True}))
        self.assertIsNone(osc.translate("/beameron", [0.0]))                                       # the release does nothing


class ScheduledProjectorTest(unittest.TestCase):
    def test_projector_entries_run_off_the_schedule_thread_and_record_the_result(self):
        import tempfile, os, time as _t
        from pvj.settings import Settings
        from pvj.modules import Registry
        gate = threading.Event()

        class Api:
            def projector_action(self, body, device, client):
                gate.wait(5)
                return {"results": {"p1": {"ok": False, "error": "cannot reach the projector"}}}
        st = Settings(os.path.join(tempfile.mkdtemp(), "settings.json"))
        st.load()
        s = scheduler.Scheduler(Api(), st, Registry(st, "x86"), log=lambda *_: None)
        e = {"id": "e1", "action": "projector_on"}
        start = _t.monotonic()
        s._run(e, __import__("datetime").datetime(2026, 9, 30, 18, 0))
        self.assertLess(_t.monotonic() - start, 0.5)                          # did not wait for the projector
        self.assertNotIn("e1", s.last)
        gate.set()
        end = _t.monotonic() + 5
        while "e1" not in s.last and _t.monotonic() < end:
            _t.sleep(0.02)
        self.assertEqual((s.last["e1"]["ok"], s.last["e1"]["message"]), (False, "cannot reach the projector"))


if __name__ == "__main__":
    unittest.main()
