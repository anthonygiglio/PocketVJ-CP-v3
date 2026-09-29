# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-netd: the small root helper that changes the wired network on behalf of the unprivileged panel.

The panel (user pvj-web) cannot and must not run nmcli. It sends one JSON line over a Unix socket in
/run/pvj (group pvj); this daemon re-validates everything with pvj.netcfg, runs only the fixed nmcli
commands that come out of it (argument lists, never a shell), and keeps a safety net: every change is
pending until confirmed and is reverted automatically when the timer runs out, when a command fails,
when this daemon restarts, or when the box reboots (the profile is not autoconnect until confirmed).
"""

"""pvj-netd: the small root helper that changes the wired network on behalf of the unprivileged panel.

The panel (user pvj-web) cannot and must not run nmcli. It sends one JSON line over a Unix socket in
/run/pvj (group pvj); this daemon re-validates everything with pvj.netcfg, runs only the fixed nmcli
commands that come out of it (argument lists, never a shell), and keeps a safety net:

* a change is built as a separate candidate profile; the confirmed profile is never edited in place
* it is pending until confirmed, and is undone when the timer runs out, when a command fails, when this
  daemon restarts, and when the box reboots (the candidate is not autoconnect until confirmed)
* an undo that fails is retried until it works, and never reported as done before it is
* the saved state lives in a root-only directory (never in /run/pvj, which group members can write)
"""

import ipaddress
import json
import os
import re
import socket
import socketserver
import struct
import subprocess
import threading
import time

from . import netcfg
from .netcfg import NetError

MAX_LINE = 4096
UP_TIMEOUT = 20          # `connection up` may wait for DHCP; the panel request must outlast the total below
OTHER_TIMEOUT = 10
MAX_REVERT_TRIES = 20
REVERT_RETRY_SECONDS = 3


class NetService:
    def __init__(self, runner=subprocess.run, clock=time.monotonic, sysfs="/sys/class/net", state_dir=None,
                 log=print):
        self.runner, self.clock, self.sysfs, self.log = runner, clock, sysfs, log
        self.state_file = os.path.join(state_dir, "net-pending.json") if state_dir else None
        self.pending = None
        self._revert_job = None
        self.lock = threading.RLock()

    # --- running commands ------------------------------------------------
    def _run(self, argv, timeout=None):
        assert isinstance(argv, list) and argv[0] in ("nmcli", "ip")
        if timeout is None:
            timeout = UP_TIMEOUT if argv[:3] == ["nmcli", "connection", "up"] else OTHER_TIMEOUT
        try:
            return self.runner(argv, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise NetError("%s is not installed on this system" % argv[0])
        except subprocess.TimeoutExpired:
            raise NetError("%s did not answer in %d seconds" % (argv[0], timeout))

    def _must(self, argv):
        r = self._run(argv)
        if r.returncode != 0:
            raise NetError((r.stderr or r.stdout or "nmcli failed").strip()[:300])
        return r.stdout

    # --- what NetworkManager and the kernel say ------------------------------
    def _active_uuid(self, iface):
        r = self._run(["nmcli", "-t", "-f", "UUID,DEVICE", "connection", "show", "--active"])
        if r.returncode != 0:
            return None
        for line in r.stdout.splitlines():
            uuid, _, dev = line.partition(":")
            if dev == iface and netcfg.UUID.fullmatch(uuid):
                return uuid
        return None

    def _exists(self, name):
        return self._run(["nmcli", "-t", "-f", "connection.id", "connection", "show", "id", name]).returncode == 0

    def _others(self):
        """(interface, network) for every address in use, so a new range cannot collide with one."""
        try:
            r = self._run(["ip", "-j", "-4", "addr", "show"])
            data = json.loads(r.stdout) if r.returncode == 0 else []
        except (NetError, ValueError):
            return []
        out = []
        for entry in data:
            name = entry.get("ifname")
            if name == "lo":
                continue
            for a in entry.get("addr_info", []):
                try:
                    out.append((name, ipaddress.ip_network("%s/%s" % (a["local"], a["prefixlen"]), strict=False)))
                except (KeyError, ValueError):
                    pass
        return out

    def _validated(self, request):
        return netcfg.validate(request, netcfg.list_interfaces(self.sysfs), self._others())

    # --- public operations ---------------------------------------------------------
    def status(self):
        with self.lock:
            p = self.pending
            return {"interfaces": netcfg.list_interfaces(self.sysfs), "reverting": self._revert_job is not None,
                    "pending": None if p is None else {"iface": p.cfg["iface"], "mode": p.cfg["mode"],
                                                       "seconds_left": p.seconds_left(self.clock())}}

    def plan(self, request):
        cfg = self._validated(request)
        return {"config": cfg, "commands": netcfg.preview(netcfg.plan(cfg))}

    def apply(self, request):
        with self.lock:
            if self.pending or self._revert_job:
                raise NetError("a change is already waiting for confirmation (or being undone); wait or revert it first")
            cfg = self._validated(request)
            iface = cfg["iface"]
            previous = self._active_uuid(iface)
            self.pending = netcfg.PendingChange(cfg, previous, self.clock(), cfg["revert_seconds"])
            self._save_state("pending")
            try:
                if self._exists(netcfg.candidate_name(iface)):  # a leftover from an earlier crash
                    self._run(["nmcli", "connection", "delete", "id", netcfg.candidate_name(iface)])
                for cmd in netcfg.plan(cfg):
                    self._must(cmd)
            except NetError as e:
                self._begin_revert()
                undone = self._attempt_revert()
                raise NetError("could not apply (%s); %s" % (e, "the previous setup was restored" if undone
                                                             else "restoring the previous setup is still being retried"))
            self.pending.restart(self.clock())  # the countdown starts now that the new network is up
            return self.status()

    def confirm(self):
        with self.lock:
            if not self.pending:
                raise NetError("nothing is waiting for confirmation")
            iface = self.pending.cfg["iface"]
            old_exists = self._exists(netcfg.profile_name(iface))
            for cmd in netcfg.confirm_plan(iface, old_exists):
                self._must(cmd)
            self.pending = None
            self._save_state(None)
            return self.status()

    def revert(self):
        with self.lock:
            if not self.pending:
                raise NetError("nothing to revert")
            self._begin_revert()
            self._attempt_revert()
            return self.status()

    # --- undoing, with retries ----------------------------------------------------------
    def _begin_revert(self):
        p, self.pending = self.pending, None
        self._revert_job = {"iface": p.cfg["iface"], "uuid": p.previous_uuid, "tries": 0, "next": 0}
        self._save_state("reverting")

    def _attempt_revert(self):
        """Run the undo. True only when every command really succeeded; otherwise it stays queued."""
        job = self._revert_job
        if job is None:
            return True
        ok = True
        try:
            cmds = netcfg.revert_plan(job["iface"], self._exists(netcfg.candidate_name(job["iface"])), job["uuid"])
        except NetError as e:
            self.log("pvj-netd: cannot plan the undo: %s" % e)
            self._revert_job = None
            self._save_state(None)
            return False
        for cmd in cmds:
            try:
                r = self._run(cmd)
            except NetError:
                ok = False
                continue
            if r.returncode != 0 and cmd[2] != "down":  # taking an already-down profile down may complain
                ok = False
        job["tries"] += 1
        job["next"] = self.clock() + REVERT_RETRY_SECONDS
        if ok:
            self._revert_job = None
            self._save_state(None)
            return True
        if job["tries"] >= MAX_REVERT_TRIES:
            self.log("pvj-netd: GAVE UP undoing a network change after %d tries; state kept for a restart" % job["tries"])
            self._revert_job = None  # the state file stays, so the next start tries again
        return False

    def tick(self):
        """About once a second. Returns True on the tick that finished undoing an unconfirmed change."""
        with self.lock:
            if self._revert_job is None:
                if self.pending and self.pending.expired(self.clock()):
                    self._begin_revert()
                else:
                    return False
            elif self.clock() < self._revert_job["next"]:
                return False
            return self._attempt_revert()

    # --- surviving a restart or a reboot -----------------------------------------------
    def _save_state(self, phase):
        if not self.state_file:
            return
        if phase is None:
            try:
                os.unlink(self.state_file)
            except OSError:
                pass
            return
        job = self._revert_job
        data = {"phase": phase, "iface": (job["iface"] if job else self.pending.cfg["iface"]),
                "previous_uuid": (job["uuid"] if job else self.pending.previous_uuid)}
        tmp = self.state_file + ".tmp"
        try:
            os.unlink(tmp)
        except OSError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_file)

    def recover(self):
        """At start-up (including after a reboot): undo whatever was left unconfirmed. The saved state is
        checked strictly first: only an interface name and a connection UUID are ever taken from it."""
        if not self.state_file or not os.path.exists(self.state_file):
            return False
        try:
            with open(self.state_file) as f:
                d = json.load(f)
            iface, uuid = d["iface"], d["previous_uuid"]
            if not isinstance(iface, str) or not netcfg.IFACE.fullmatch(iface):
                raise ValueError("bad interface")
            if uuid is not None and not (isinstance(uuid, str) and netcfg.UUID.fullmatch(uuid)):
                raise ValueError("bad connection id")
        except (OSError, ValueError, KeyError, TypeError):
            self.log("pvj-netd: ignoring an unreadable or invalid saved state")
            try:
                os.unlink(self.state_file)
            except OSError:
                pass
            return False
        with self.lock:
            self._revert_job = {"iface": iface, "uuid": uuid, "tries": 0, "next": 0}
            self._attempt_revert()
        return True

    # --- request dispatch --------------------------------------------------------------------
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

    def __init__(self, path, timeout=60):
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


def safe_state_dir():
    """A directory only root can touch, or None (then no restart safety net, said loudly)."""
    path = os.environ.get("STATE_DIRECTORY") or "/var/lib/pvj-netd"
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        st = os.stat(path)
        if st.st_uid == os.geteuid() and st.st_mode & 0o077 == 0 and not os.path.islink(path):
            return path
    except OSError:
        pass
    print("pvj-netd: WARNING: %s is not private; pending changes will not survive a restart" % path, flush=True)
    return None


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
    service = NetService(state_dir=safe_state_dir(), log=lambda m: print(m, flush=True))
    if service.recover():
        print("pvj-netd: found an unconfirmed network change from before the restart and undid it", flush=True)
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
