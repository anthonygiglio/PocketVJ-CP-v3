# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-netd: the small root helper that changes the wired network on behalf of the unprivileged panel.

The panel (user pvj-web) cannot and must not run nmcli. It sends one JSON line over a Unix socket in
/run/pvj (group pvj); this daemon re-validates everything with pvj.netcfg, runs only the fixed nmcli
commands that come out of it (argument lists, never a shell), and keeps a safety net: every change is
pending until confirmed and is reverted automatically when the timer runs out, when a command fails,
when this daemon restarts, or when the box reboots (the profile is not autoconnect until confirmed).
"""

import json
import os
import socket
import socketserver
import struct
import subprocess
import threading
import time

from . import netcfg
from .netcfg import NetError

MAX_LINE = 4096
COMMAND_TIMEOUT = 30


class NetService:
    def __init__(self, runner=subprocess.run, clock=time.monotonic, sysfs="/sys/class/net", state_file=None,
                 wall=time.time):
        self.runner, self.clock, self.sysfs, self.state_file, self.wall = runner, clock, sysfs, state_file, wall
        self.pending = None
        self.lock = threading.RLock()

    # --- running commands ------------------------------------------------
    def _run(self, argv):
        assert isinstance(argv, list) and argv[0] == "nmcli"
        try:
            r = self.runner(argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT)
        except FileNotFoundError:
            raise NetError("NetworkManager (nmcli) is not installed on this system")
        except subprocess.TimeoutExpired:
            raise NetError("nmcli did not answer in %d seconds" % COMMAND_TIMEOUT)
        return r

    def _must(self, argv):
        r = self._run(argv)
        if r.returncode != 0:
            raise NetError((r.stderr or r.stdout or "nmcli failed").strip()[:300])
        return r.stdout

    # --- state -------------------------------------------------------------
    def _active_connection(self, iface):
        r = self._run(["nmcli", "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"])
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            name, _, dev = line.rpartition(":")
            if dev == iface:
                return name.replace("\\:", ":")
        return None

    def _snapshot(self, iface):
        name = netcfg.profile_name(iface)
        r = self._run(["nmcli", "-t", "-f", ",".join(netcfg.SNAPSHOT_FIELDS), "connection", "show", name])
        return netcfg.parse_show(r.stdout) if r.returncode == 0 else None

    def status(self):
        with self.lock:
            p = self.pending
            return {"interfaces": netcfg.list_interfaces(self.sysfs),
                    "pending": None if p is None else {"iface": p.cfg["iface"], "mode": p.cfg["mode"],
                                                       "seconds_left": p.seconds_left(self.clock())}}

    def plan(self, request):
        cfg = netcfg.validate(request, netcfg.list_interfaces(self.sysfs))
        exists = self._snapshot(cfg["iface"]) is not None
        return {"config": cfg, "commands": [" ".join(c) for c in netcfg.plan(cfg, exists)]}

    def apply(self, request):
        with self.lock:
            if self.pending:
                raise NetError("a change is already waiting for confirmation; confirm or revert it first")
            cfg = netcfg.validate(request, netcfg.list_interfaces(self.sysfs))
            active = self._active_connection(cfg["iface"])
            snapshot = self._snapshot(cfg["iface"])
            self.pending = netcfg.PendingChange(cfg, snapshot, active, self.clock(), cfg["revert_seconds"])
            self._save_state()
            try:
                for cmd in netcfg.plan(cfg, snapshot is not None):
                    self._must(cmd)
            except NetError as e:
                self._revert_locked()
                raise NetError("could not apply (%s); the previous setup was restored" % e)
            return self.status()

    def confirm(self):
        with self.lock:
            if not self.pending:
                raise NetError("nothing is waiting for confirmation")
            for cmd in netcfg.confirm_plan(self.pending.cfg["iface"]):
                self._must(cmd)
            self.pending = None
            self._save_state()
            return self.status()

    def revert(self):
        with self.lock:
            if not self.pending:
                raise NetError("nothing to revert")
            self._revert_locked()
            return self.status()

    def _revert_locked(self):
        p, self.pending = self.pending, None
        self._save_state()
        if p is None:
            return
        for cmd in netcfg.revert_plan(p.cfg["iface"], p.snapshot, p.previous_active):
            try:
                self._run(cmd)  # keep going: bring back as much as possible
            except NetError:
                pass

    def tick(self):
        """Called about once a second. Returns True if it reverted an unconfirmed change."""
        with self.lock:
            if self.pending and self.pending.expired(self.clock()):
                self._revert_locked()
                return True
            return False

    # --- surviving a restart ---------------------------------------------
    def _save_state(self):
        if not self.state_file:
            return
        if self.pending is None:
            try:
                os.unlink(self.state_file)
            except OSError:
                pass
            return
        p = self.pending
        data = {"cfg": p.cfg, "snapshot": p.snapshot, "previous_active": p.previous_active}
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_file)

    def recover(self):
        """At start-up: an unconfirmed change left by a crash is undone, because nobody confirmed it."""
        if not self.state_file or not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file) as f:
                d = json.load(f)
            self.pending = netcfg.PendingChange(d["cfg"], d["snapshot"], d["previous_active"], self.clock(), 0)
        except (OSError, ValueError, KeyError, TypeError):
            try:
                os.unlink(self.state_file)
            except OSError:
                pass
            return False
        with self.lock:
            self._revert_locked()
        return True

    # --- request dispatch --------------------------------------------------
    def handle(self, message):
        try:
            if not isinstance(message, dict):
                raise NetError("request must be an object")
            cmd = message.get("cmd")
            if cmd == "status":
                return {"ok": True, **self.status()}
            if cmd == "plan":
                return {"ok": True, **self.plan(message.get("config"))}
            if cmd == "apply":
                return {"ok": True, **self.apply(message.get("config"))}
            if cmd == "confirm":
                return {"ok": True, **self.confirm()}
            if cmd == "revert":
                return {"ok": True, **self.revert()}
            raise NetError("unknown command")
        except NetError as e:
            return {"ok": False, "error": str(e)}


# --- the socket -----------------------------------------------------------------
def peer_uid(sock):
    _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid


class NetServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """One thread per connection, so a stalled client only holds its own; the service serialises the real work."""

    daemon_threads = True

    def __init__(self, path, service, is_allowed):
        self.service, self.is_allowed = service, is_allowed
        if os.path.exists(path):
            os.unlink(path)
        super().__init__(path, _Handler)
        os.chmod(path, 0o660)

    def get_request(self):
        req, addr = super().get_request()
        req.settimeout(5)  # a stalled client must not block the others
        return req, addr


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            if not self.server.is_allowed(peer_uid(self.request)):
                self.wfile.write(b'{"ok": false, "error": "not allowed"}\n')
                return
            line = self.rfile.readline(MAX_LINE + 1)
            if len(line) > MAX_LINE:
                reply = {"ok": False, "error": "request too large"}
            else:
                try:
                    reply = self.server.service.handle(json.loads(line))
                except (ValueError, RecursionError):
                    reply = {"ok": False, "error": "invalid JSON"}
            self.wfile.write(json.dumps(reply).encode() + b"\n")
        except (OSError, socket.timeout):
            pass


class NetdClient:
    """Used by the panel: one request, one reply."""

    def __init__(self, path, timeout=40):
        self.path, self.timeout = path, timeout

    def request(self, message):
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
            raise NetError("the network helper (pvj-netd) is not running")
        finally:
            s.close()


def main(argv=None):
    import grp
    import pwd
    import sys
    rundir = os.environ.get("PVJ_RUNTIME_DIR", "/run/pvj")
    os.makedirs(rundir, exist_ok=True)
    allowed = {0}
    try:
        allowed.add(pwd.getpwnam("pvj-web").pw_uid)
    except KeyError:
        pass
    service = NetService(state_file=os.path.join(rundir, "net-pending.json"))
    if service.recover():
        print("pvj-netd: undid a network change that was never confirmed", flush=True)
    server = NetServer(os.path.join(rundir, "netd.sock"), service, lambda uid: uid in allowed)
    try:
        os.chown(server.server_address, 0, grp.getgrnam("pvj").gr_gid)
    except (KeyError, OSError):
        pass

    def ticker():
        while True:
            time.sleep(1)
            try:
                if service.tick():
                    print("pvj-netd: no confirmation arrived; the previous network setup was restored", flush=True)
            except Exception as e:  # never let the safety timer die
                print("pvj-netd: timer error: %r" % (e,), file=sys.stderr, flush=True)
    threading.Thread(target=ticker, daemon=True).start()
    print("pvj-netd: ready", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
