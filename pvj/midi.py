# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""MIDI input from USB controllers, using the raw ALSA device files (no libraries).

A class-compliant USB MIDI controller (pad grid, keyboard, fader box) shows up as
`/dev/snd/midiC<card>D<device>`. Reading that file gives the plain MIDI byte stream. Messages are
turned into the same calls the web panel makes. See MIDI.md for the table.

Limits:
* Off until switched on. Only paths of the form /dev/snd/midiC<n>D<n> can be chosen, never an
  arbitrary file.
* Receive only; nothing is written to the device.
* Only play, stop, pause, fade, blackout, reset, opacity, size, position, speed and volume are reachable.
* The service account needs to be in the `audio` group to read the device.
* Unplugging the controller is fine: the box keeps looking for it and reconnects.
"""

import glob
import os
import re
import select
import stat
import threading
import time

from .osc import RateLimiter

MIDI_DEVICE = {"id": "midi", "name": "MIDI", "role": "live"}
DEVICE_PATH = re.compile(r"/dev/snd/midiC[0-9]{1,3}D[0-9]{1,3}")
FIRST_PAD_NOTE = 36     # notes 36 to 71 are pads 1 to 36
PADS = 36
MIN_INTERVAL = 0.05
MAX_CALLS_PER_SECOND = 50.0
CC_LEVELS = {20: ("opacity", 0, 100), 21: ("size", 1, 200), 22: ("position", -100, 100),
             23: ("speed", 0.25, 2.0), 24: ("volume", 0, 100)}
CC_BLACKOUT = 25


class MidiError(Exception):
    pass


def list_devices(pattern="/dev/snd/midiC*D*"):
    return sorted(p for p in glob.glob(pattern) if DEVICE_PATH.fullmatch(p))


class MidiParser:
    """Turns a MIDI byte stream into (kind, channel, data1, data2) tuples, kinds 'on', 'off', 'cc',
    'program'. Handles running status; skips system and real-time bytes and system-exclusive data."""

    def __init__(self):
        self.status = None
        self.data = []
        self.in_sysex = False

    def feed(self, chunk):
        out = []
        for b in chunk:
            if b >= 0xF8:                 # real-time: may appear anywhere, changes nothing
                continue
            if b & 0x80:
                if b == 0xF0:
                    self.in_sysex, self.status, self.data = True, None, []
                elif b == 0xF7:
                    self.in_sysex = False
                elif b >= 0xF0:           # other system common: cancels running status
                    self.in_sysex, self.status, self.data = False, None, []
                else:
                    self.in_sysex, self.status, self.data = False, b, []
                continue
            if self.in_sysex or self.status is None:
                continue
            self.data.append(b)
            kind = self.status & 0xF0
            need = 1 if kind in (0xC0, 0xD0) else 2
            if len(self.data) < need:
                continue
            channel = self.status & 0x0F
            d = self.data
            self.data = []
            if kind == 0x90:
                out.append(("on" if d[1] > 0 else "off", channel, d[0], d[1]))
            elif kind == 0x80:
                out.append(("off", channel, d[0], d[1]))
            elif kind == 0xB0:
                out.append(("cc", channel, d[0], d[1]))
            elif kind == 0xC0:
                out.append(("program", channel, d[0], 0))
        return out


class MidiMapper:
    def __init__(self, do, channel=0, mix=None, clock=time.monotonic):
        self.do, self.channel, self.mix, self._clock = do, channel, mix or {}, clock
        self._last = {}         # cc number -> time of the last applied value
        self.pending = {}       # cc number -> newest value not yet applied
        self.last_message = None

    def _control(self, action, value=None):
        body = {"action": action}
        if value is not None:
            body["value"] = value
        return self.do("/api/control", body)

    def _cc_apply(self, cc, value, now):
        self._last[cc] = now
        if cc == CC_BLACKOUT:
            return self.do("/api/blackout", {"on": value >= 64})
        action, lo, hi = CC_LEVELS[cc]
        return self._control(action, round(lo + (hi - lo) * value / 127.0, 2))

    def message(self, msg):
        """Handle one parsed message. Returns the number of calls made."""
        kind, channel, d1, d2 = msg
        if self.channel and channel != self.channel - 1:
            return 0
        self.last_message = "%s %d %d" % (kind, d1, d2) if kind != "program" else "program %d" % d1
        if kind == "on":
            if FIRST_PAD_NOTE <= d1 < FIRST_PAD_NOTE + PADS:
                pad = d1 - FIRST_PAD_NOTE
                return int(bool(self.do("/api/play", {"pad": [pad // 12, pad % 12]})))
            extra = {72: lambda: self._control("stop"), 73: lambda: self._control("pause"),
                     74: lambda: self.do("/api/blackout", {"on": not self.mix.get("blackout", False)}),
                     75: lambda: self.do("/api/fadeout", {"seconds": 2}), 76: lambda: self._control("reset")}
            fn = extra.get(d1)
            return int(bool(fn())) if fn else 0
        if kind == "program":
            if d1 < PADS:
                return int(bool(self.do("/api/play", {"pad": [d1 // 12, d1 % 12]})))
            return 0
        if kind == "cc" and (d1 in CC_LEVELS or d1 == CC_BLACKOUT):
            now = self._clock()
            if now - self._last.get(d1, 0.0) < MIN_INTERVAL:
                self.pending[d1] = d2      # a fader sweep: keep only the newest value
                return 0
            self.pending.pop(d1, None)
            return int(bool(self._cc_apply(d1, d2, now)))
        return 0

    def flush(self):
        """Apply held-back fader values whose wait is over, so the last position is never lost."""
        now, done = self._clock(), 0
        for cc in list(self.pending):
            if now - self._last.get(cc, 0.0) >= MIN_INTERVAL:
                done += int(bool(self._cc_apply(cc, self.pending.pop(cc), now)))
        return done


class MidiInput:
    def __init__(self, api, cfg, log=print, clock=time.monotonic, open_fn=None, retry=2.0):
        self.api, self.cfg, self.log = api, dict(cfg), log
        self.mapper = MidiMapper(self._do, cfg.get("channel", 0), api.mix, clock)
        self._open = open_fn or self._open_device
        self.calls = RateLimiter(clock, rate=MAX_CALLS_PER_SECOND, burst=MAX_CALLS_PER_SECOND)   # a faulty pad cannot flood the player
        self.retry = retry
        self._stop = threading.Event()
        self._thread = None
        self.connected = False
        self.stats = {"messages": 0, "handled": 0}
        self._quiet_until = 0.0
        self._clock = clock

    def _note(self, text):
        now = self._clock()
        if self._quiet_until <= now:
            self._quiet_until = now + 10
            self.log("midi: " + text)

    @staticmethod
    def _open_device(path):
        """Open a MIDI device file, but only a character device that really is one of /dev/snd/midi*."""
        if not isinstance(path, str) or not DEVICE_PATH.fullmatch(path):
            raise OSError("not a MIDI device path")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError("not a character device")
        return fd

    def _do(self, path, body):
        if not self.calls.allow("all"):
            self._note("too many commands a second; some were dropped")
            return False
        status, payload = self.api.handle("POST", path, body, MIDI_DEVICE, "midi")
        if status != 200:
            self._note("%s -> %s %s" % (path, status, payload.get("error", "")))
            return False
        self.stats["handled"] += 1
        return True

    def feed(self, parser, chunk):
        for msg in parser.feed(chunk):
            self.stats["messages"] += 1
            self.mapper.message(msg)

    def _run(self):
        while not self._stop.is_set():
            path = self.cfg.get("device", "")
            fd = None
            try:
                fd = self._open(path)
            except Exception:       # missing, busy, not a MIDI device, a hand-edited bad path: keep looking
                self.connected = False
                self._stop.wait(self.retry)
                continue
            self.connected = True
            parser = MidiParser()
            self.mapper.pending.clear()      # a fader value held from before the unplug is stale
            poller = select.poll()           # unlike select(), fine for any descriptor number
            poller.register(fd, select.POLLIN | select.POLLERR | select.POLLHUP)
            try:
                while not self._stop.is_set():
                    ready = poller.poll(100)
                    if ready:
                        chunk = os.read(fd, 256)
                        if not chunk:
                            break               # end of stream: device gone
                        self.feed(parser, chunk)
                    self.mapper.flush()
            except OSError:
                pass                            # unplugged mid-read: go back to looking for it
            except Exception as e:
                self._note("internal error: %r" % (e,))
            finally:
                self.connected = False
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._stop.wait(self.retry)

    def start(self):
        if self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="midi")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None


def validate(body, current):
    new = dict(current)
    if "enabled" in body:
        if not isinstance(body["enabled"], bool):
            raise MidiError("enabled must be true or false")
        new["enabled"] = body["enabled"]
    if "device" in body:
        d = body["device"]
        if d != "" and (not isinstance(d, str) or not DEVICE_PATH.fullmatch(d)):
            raise MidiError("choose one of the MIDI devices listed")
        new["device"] = d
    if "channel" in body:
        c = body["channel"]
        if isinstance(c, bool) or not isinstance(c, int) or not 0 <= c <= 16:
            raise MidiError("channel must be 0 (all) or 1 to 16")
        new["channel"] = c
    if new["enabled"] and not new["device"]:
        raise MidiError("choose a MIDI device first")
    return new


class MidiManager:
    def __init__(self, api, settings, log=print, open_fn=None, lister=list_devices):
        self.api, self.settings, self.log, self._open_fn, self._lister = api, settings, log, open_fn, lister
        self.input = None
        self._lock = threading.RLock()

    def apply(self):
        """Make reality match the settings and the module switch."""
        with self._lock:
            cfg = self.settings.data["control"]["midi"]
            self._stop_input()
            if cfg["enabled"] and self.api.registry.enabled("control-midi"):
                self.input = MidiInput(self.api, cfg, log=self.log, open_fn=self._open_fn)
                self.input.start()

    def status(self):
        with self._lock:
            cfg = self.settings.data["control"]["midi"]
            i = self.input
            return dict(cfg, devices=self._lister(), connected=bool(i and i.connected),
                        messages=i.stats["messages"] if i else 0, last=i.mapper.last_message if i else None)

    def _stop_input(self):
        if self.input:
            self.input.stop()
            self.input = None

    def stop(self):
        with self._lock:
            self._stop_input()
