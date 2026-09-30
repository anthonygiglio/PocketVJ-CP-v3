# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Show the pairing PIN and the panel's address on the screen, until the first device has paired.

The panel and the manual say the PIN is on the projector; until this existed it was only in a file in RAM, so a
box with no keyboard could not be paired. It is drawn by mpv on its own idle screen (its on-screen text), and:

* only while NO device has ever paired, so it never appears again once the box is set up (a paired full-access
  device can make a new PIN, or guest links, in System);
* only while nothing is playing, so it can never draw over a show;
* only characters from a short safe set, because mpv would expand `${...}` in the text.
"""

import re
import socket
import threading

from .player import PlayerError

SAFE = re.compile(r"[^A-Za-z0-9 .:/_\-]")
SHOW_MS = 5000        # each draw lasts this long; it is repeated while conditions hold


def clean(text):
    return SAFE.sub("", text)


def lines(pin, hostname, addresses):
    out = ["nxlx.mastercontrol", "Open on your phone:"]
    if hostname:
        out.append("http://%s.local/" % clean(hostname))
    out += ["http://%s/" % clean(a) for a in addresses[:2]]
    out.append("PIN  %s" % clean(str(pin)))
    return out


class PinScreen:
    def __init__(self, api, auth, log=print, interval=3.0, hostname=None):
        self.api, self.auth, self.log = api, auth, log
        self.interval = interval
        self.hostname = hostname if hostname is not None else socket.gethostname()
        self._stop = threading.Event()
        self._thread = None
        self.shown = 0

    def addresses(self):
        out = []
        try:
            for entry in self.api._ip_json():
                if entry.get("ifname") == "lo":
                    continue
                for a in entry.get("addr_info", []):
                    if a.get("family") == "inet" and a.get("local"):
                        out.append(a["local"])
        except Exception:
            pass
        return out

    def wanted(self):
        """True when the PIN should be on the screen right now."""
        if self.auth.list_devices():
            return False
        try:
            status = self.api.player.status()
        except PlayerError:
            return False
        return bool(status.get("running")) and not status.get("path")

    def tick(self):
        if not self.wanted():
            return False
        text = "\n".join(lines(self.auth.current_pin, self.hostname, self.addresses()))
        try:
            self.api.player.ipc.request("show-text", text, SHOW_MS)
        except PlayerError:
            return False
        self.shown += 1
        return True

    def start(self):
        if self._thread:
            return
        self._stop.clear()

        def loop():
            while not self._stop.wait(self.interval):
                try:
                    self.tick()
                except Exception as e:
                    self.log("pvj-web: pin screen error: %s" % e)
        self._thread = threading.Thread(target=loop, name="pinscreen", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
