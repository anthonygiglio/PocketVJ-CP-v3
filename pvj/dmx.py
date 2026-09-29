# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""DMX over the network (Art-Net and sACN / E1.31), receive only.

A lighting console or software (QLC+, Resolume, MadMapper, grandMA, ...) sends a DMX universe; the
box reads eight channels from a start address you choose and turns them into the same calls the
web panel makes. See DMX.md for the channel table.

Deliberate limits, as for OSC:
* Off until switched on. Only private networks may send, plus ranges you add.
* Nothing is ever sent back (no ArtPollReply, no discovery): tell the console the box's address.
* The first frame after switching on, or after the settings change, only sets a baseline. Nothing
  fires from it, so a console that happens to be sitting at zero cannot black out the screen.
* If the signal stops, the box holds its last state.
* Level channels are applied at most 20 times a second each; the next frame carries the change on.
* Only play, stop, pause, fade, blackout, opacity, size, position, speed and volume are reachable.
"""

import socket
import struct
import threading
import time

from .osc import RateLimiter, parse_networks, source_allowed

ARTNET_PORT = 6454
SACN_PORT = 5568
PROTOCOLS = ("artnet", "sacn")
CHANNELS = 8
DMX_DEVICE = {"id": "dmx", "name": "DMX", "role": "live"}
MIN_INTERVAL = 0.05
PAD_STEP = 6            # each pad owns six values on the pad channel: 6-11 is pad 1, 12-17 pad 2, ...
PADS = 36


class DmxError(Exception):
    pass


def parse_artnet(data):
    """(universe, channels) from an ArtDmx packet; None for anything else (ArtPoll, junk)."""
    if len(data) < 18 or data[:8] != b"Art-Net\x00":
        return None
    if struct.unpack("<H", data[8:10])[0] != 0x5000 or struct.unpack(">H", data[10:12])[0] < 14:
        return None
    universe = (data[15] << 8) | data[14]
    length = struct.unpack(">H", data[16:18])[0]
    if not 2 <= length <= 512 or 18 + length > len(data):
        return None
    return universe, data[18:18 + length]


def parse_sacn(data):
    """(universe, channels) from an E1.31 data packet; None for anything else, including preview
    data and 'stream terminated' packets."""
    if len(data) < 126 or data[0:2] != b"\x00\x10" or data[4:16] != b"ASC-E1.17\x00\x00\x00":
        return None
    if struct.unpack(">I", data[18:22])[0] != 4 or struct.unpack(">I", data[40:44])[0] != 2:
        return None
    if data[112] & 0xC0:    # preview (0x80) or terminated (0x40)
        return None
    universe = struct.unpack(">H", data[113:115])[0]
    if data[117] != 0x02 or data[118] != 0xA1 or data[125] != 0:   # DMP vector, address type, DMX start code
        return None
    count = struct.unpack(">H", data[123:125])[0] - 1
    if not 1 <= count <= 512 or 126 + count > len(data):
        return None
    return universe, data[126:126 + count]


def _scale(v, lo, hi):
    return lo + (hi - lo) * v / 255.0


class DmxMapper:
    """Turns frames into API calls. `do(path, body)` performs one call."""

    def __init__(self, do, start=1, mix=None, clock=time.monotonic):
        self.do, self.start, self.mix, self._clock = do, start, mix, clock
        self.applied = None      # last values acted on
        self.last_time = [0.0] * CHANNELS
        self.seen = None         # the eight raw values of the latest frame, for the panel

    def reset(self, start):
        self.start, self.applied, self.seen = start, None, None

    def frame(self, dmx):
        i = self.start - 1
        if len(dmx) < i + CHANNELS:
            return 0
        values = list(dmx[i:i + CHANNELS])
        self.seen = values
        if self.applied is None:
            self.applied = values      # baseline: no action
            return 0
        done = 0
        now = self._clock()
        for ch, v in enumerate(values):
            if v == self.applied[ch]:
                continue
            if ch <= 4 and now - self.last_time[ch] < MIN_INTERVAL:
                continue               # too soon; the next frame retries
            old, self.applied[ch] = self.applied[ch], v
            self.last_time[ch] = now
            if self._act(ch, v, old):
                done += 1
        return done

    def _act(self, ch, v, old):
        control = lambda action, value: self.do("/api/control", {"action": action, "value": value})
        if ch == 0:
            return control("opacity", round(_scale(v, 0, 100), 1))
        if ch == 1:
            return control("size", round(_scale(v, 1, 200), 1))
        if ch == 2:
            return control("position", round(_scale(v, -100, 100), 1))
        if ch == 3:
            return control("speed", round(_scale(v, 0.25, 2.0), 2))
        if ch == 4:
            return control("volume", round(_scale(v, 0, 100), 1))
        if ch == 5:
            on = v >= 128
            return self.do("/api/blackout", {"on": on}) if on != (old >= 128) else False
        if ch == 6:
            pad = v // PAD_STEP - 1
            if 0 <= pad < PADS:
                return self.do("/api/play", {"pad": [pad // 12, pad % 12]})
            return False
        if ch == 7:
            if 50 <= v < 100:
                return self.do("/api/control", {"action": "stop"})
            if 100 <= v < 150:
                return control("pause", True)
            if 150 <= v < 200:
                return control("pause", False)
            if v >= 200:
                return self.do("/api/fadeout", {"seconds": 2})
        return False


class DmxServer:
    def __init__(self, api, cfg, host="0.0.0.0", log=print, clock=time.monotonic):
        self.api, self.cfg, self.host, self.log = api, dict(cfg), host, log
        self.extra = parse_networks(cfg.get("allow", []))
        self.limiter = RateLimiter(clock, rate=200.0, burst=400.0)
        self.mapper = DmxMapper(self._do, cfg["start"], api.mix, clock)
        self._sock = None
        self._thread = None
        self._quiet_until = 0.0
        self._clock = clock
        self.stats = {"received": 0, "matched": 0, "dropped": 0, "handled": 0}
        self.port = ARTNET_PORT if cfg["protocol"] == "artnet" else SACN_PORT

    def _note(self, text):
        now = self._clock()
        if self._quiet_until <= now:
            self._quiet_until = now + 10
            self.log("dmx: " + text)

    def _do(self, path, body):
        status, payload = self.api.handle("POST", path, body, DMX_DEVICE, "dmx")
        if status != 200:
            self._note("%s -> %s %s" % (path, status, payload.get("error", "")))
            return False
        self.stats["handled"] += 1
        return True

    def handle_packet(self, data, source_ip):
        self.stats["received"] += 1
        if not source_allowed(source_ip, self.extra):
            self.stats["dropped"] += 1
            self._note("ignoring %s (not on an allowed network)" % source_ip)
            return 0
        if not self.limiter.allow(source_ip):
            self.stats["dropped"] += 1
            return 0
        parsed = (parse_artnet if self.cfg["protocol"] == "artnet" else parse_sacn)(data)
        if parsed is None or parsed[0] != self.cfg["universe"]:
            return 0
        self.stats["matched"] += 1
        return self.mapper.frame(parsed[1])

    def start(self):
        if self._sock:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
        except OSError as e:
            sock.close()
            raise DmxError("cannot listen on UDP %d: %s" % (self.port, e))
        if self.cfg["protocol"] == "sacn":
            u = self.cfg["universe"]
            group = "239.255.%d.%d" % (u >> 8, u & 0xFF)
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(group) + socket.inet_aton("0.0.0.0"))
            except OSError:
                self._note("could not join multicast group %s; unicast sACN still works" % group)
        sock.settimeout(0.5)
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._thread = threading.Thread(target=self._loop, daemon=True, name="dmx")
        self._thread.start()

    def _loop(self):
        sock = self._sock
        while self._sock is sock:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self.handle_packet(data, addr[0])
            except Exception as e:  # one bad packet must never kill the receiver
                self._note("internal error: %r" % (e,))

    def stop(self):
        sock, self._sock = self._sock, None
        if sock:
            sock.close()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    @property
    def listening(self):
        return self._sock is not None


def validate(body, current):
    """The new DMX settings from untrusted input, based on `current`."""
    new = dict(current)
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise DmxError("enabled must be true or false")
        new["enabled"] = body["enabled"]
    if "protocol" in body:
        if body["protocol"] not in PROTOCOLS:
            raise DmxError("protocol must be artnet or sacn")
        new["protocol"] = body["protocol"]
        if "universe" not in body and new["protocol"] == "sacn" and new["universe"] < 1:
            new["universe"] = 1
    if "universe" in body:
        u = body["universe"]
        if isinstance(u, bool) or not isinstance(u, int):
            raise DmxError("universe must be a whole number")
        new["universe"] = u
    if new["protocol"] == "artnet" and not 0 <= new["universe"] <= 32767:
        raise DmxError("Art-Net universe must be 0 to 32767")
    if new["protocol"] == "sacn" and not 1 <= new["universe"] <= 63999:
        raise DmxError("sACN universe must be 1 to 63999")
    if "start" in body:
        s = body["start"]
        if isinstance(s, bool) or not isinstance(s, int) or not 1 <= s <= 512 - CHANNELS + 1:
            raise DmxError("start channel must be 1 to %d" % (512 - CHANNELS + 1))
        new["start"] = s
    if "allow" in body:
        from .osc import OscError, validate_allow
        try:
            new["allow"] = validate_allow(body["allow"])
        except OscError as e:
            raise DmxError(str(e))
    return new


class DmxManager:
    """Starts and stops the receiver from settings["control"]["dmx"]."""

    def __init__(self, api, settings, host="0.0.0.0", log=print):
        self.api, self.settings, self.host, self.log = api, settings, host, log
        self.server = None
        self.error = None

    def apply(self):
        cfg = self.settings.data["control"]["dmx"]
        if self.server:
            self.server.stop()
            self.server = None
        self.error = None
        if not cfg["enabled"]:
            return
        server = DmxServer(self.api, cfg, host=self.host, log=self.log)
        try:
            server.start()
        except DmxError as e:
            self.error = str(e)
            raise
        self.server = server

    def status(self):
        cfg = self.settings.data["control"]["dmx"]
        s = self.server
        return dict(cfg, listening=bool(s and s.listening), error=self.error,
                    received=s.stats["matched"] if s else 0, channels=s.mapper.seen if s else None,
                    port=s.port if s else (ARTNET_PORT if cfg["protocol"] == "artnet" else SACN_PORT))

    def stop(self):
        if self.server:
            self.server.stop()
            self.server = None
