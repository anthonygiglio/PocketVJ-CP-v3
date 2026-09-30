# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-sysd: the small root helper for reboot, power off and setting the clock, on behalf of the unprivileged panel.

The old panel had Reboot, Power off and Set time buttons (it ran `sudo reboot` and `date -s` from PHP). Here the panel
(user pvj-web) sends one JSON line over a Unix socket in /run/pvj; this daemon answers only root and pvj-web (checked
with SO_PEERCRED), and runs only these fixed commands, as argument lists, never a shell:

* reboot, poweroff: `systemctl reboot|poweroff`, one second after answering, so the panel gets its reply;
* set_time: only while the clock has NOT been set from the network (a Pi has no clock battery, so without a network
  it starts at the last shutdown time); `timedatectl set-time` with a validated epoch, then network time back on, so
  a network that appears later still corrects it;
* status: whether the clock is set from the network.

It holds no capabilities of its own (systemd and timedated do the work), and OSC, MIDI and DMX cannot reach it.
"""

import datetime
import json
import os
import subprocess
import threading
import time

from .netd import NetServer, peer_uid  # noqa: F401  (the same small, reviewed socket server)

MIN_EPOCH = 1735689600      # 2025-01-01: anything earlier is a wrong clock, not a date to set
MAX_EPOCH = 4102444800      # 2100-01-01


class SysService:
    def __init__(self, runner=subprocess.run, schedule=None, log=print):
        self.runner, self.log = runner, log
        self._schedule = schedule or (lambda delay, fn: threading.Timer(delay, fn).start())
        self.lock = threading.Lock()

    def _run(self, argv, timeout=15):
        assert isinstance(argv, list) and argv[0] in ("systemctl", "timedatectl")
        try:
            r = self.runner(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, str(e)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def synchronized(self):
        """True if the clock has been set from the network, None if that cannot be read."""
        code, out = self._run(["timedatectl", "show", "--property=NTPSynchronized", "--value"])
        if code != 0:
            return None
        return out.strip() == "yes"

    def status(self):
        return {"ok": True, "clock_from_network": self.synchronized(), "now": int(time.time())}

    def power(self, verb):
        def go():
            code, out = self._run(["systemctl", verb])
            if code != 0:
                self.log("pvj-sysd: systemctl %s failed: %s" % (verb, out.strip()[-200:]))
        self.log("pvj-sysd: %s requested by the panel" % verb)
        self._schedule(1.0, go)
        return {"ok": True, verb: True}

    def set_time(self, epoch):
        if isinstance(epoch, bool) or not isinstance(epoch, int) or not MIN_EPOCH <= epoch <= MAX_EPOCH:
            return {"ok": False, "error": "that is not a sensible date"}
        with self.lock:
            synced = self.synchronized()
            if synced:
                return {"ok": False, "error": "the clock is already set from the network"}
            stamp = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            self._run(["timedatectl", "set-ntp", "false"])
            code, out = self._run(["timedatectl", "--adjust-system-clock", "set-time", stamp + " UTC"])
            self._run(["timedatectl", "set-ntp", "true"])       # a network that appears later still corrects it
            if code != 0:
                return {"ok": False, "error": "the clock could not be set: %s" % out.strip()[-160:]}
            self.log("pvj-sysd: clock set to %s UTC by the panel" % stamp)
            return {"ok": True, "now": int(time.time())}

    def handle(self, message):
        if not isinstance(message, dict):
            return {"ok": False, "error": "bad request"}
        cmd = message.get("cmd")
        if cmd == "status":
            return self.status()
        if cmd in ("reboot", "poweroff"):
            return self.power(cmd)
        if cmd == "set_time":
            return self.set_time(message.get("epoch"))
        return {"ok": False, "error": "unknown command"}


class SysdClient:
    """Used by the panel: one request, one reply."""

    def __init__(self, path, timeout=30):
        self.path, self.timeout = path, timeout

    def request(self, message):
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.path)
            s.sendall(json.dumps(message).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n") and len(data) < 65536:
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
            return json.loads(data)
        except (OSError, ValueError):
            raise OSError("the system helper (pvj-sysd) is not running")
        finally:
            s.close()


def main(argv=None):
    import grp
    import pwd
    rundir = os.environ.get("PVJ_RUNTIME_DIR", "/run/pvj")
    os.makedirs(rundir, exist_ok=True)
    allowed = {0}
    try:
        allowed.add(pwd.getpwnam("pvj-web").pw_uid)
    except KeyError:
        pass
    service = SysService(log=lambda m: print(m, flush=True))
    server = NetServer(os.path.join(rundir, "sysd.sock"), service, lambda uid: uid in allowed)
    try:
        os.chown(server.server_address, 0, grp.getgrnam("pvj").gr_gid)
    except (KeyError, OSError):
        pass
    print("pvj-sysd: ready", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
