# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Projector control over PJLink (class 1): the old panel's Beamer On and Beamer Off, and more.

PJLink is the common network control standard for projectors (Epson, NEC, Panasonic, Sony, Hitachi, Eiki, Christie
and most others): TCP port 4352, one short text command per connection. With a projector password set, the projector
sends a random number and every command is prefixed with MD5(random + password), as the standard requires (PJLink's
own design; it keeps the password off the wire, nothing more).

Commands used, all class 1 (PJLink Specifications 1.04, chapter 4): power on and off and the power state (POWR),
the input (INPT) from the projector's own list (INST), picture and sound mute apart or together (AVMT), the warnings
(ERST), the lamp hours (LAMP), and who the projector is (NAME, INF1, INF2, INFO, CLSS).

A Monitor asks each projector for its state now and then in the background, one small thread per projector with
its own stop signal, so the panel shows on, off, warming up or cooling down without asking; the same thread
retries an input change that the projector refused as "unavailable" (it does that while warming up).

Only projectors on a private network can be added (IP addresses in 10/8, 172.16/12, 192.168/16, 169.254/16 and their
IPv6 equivalents, or a name that resolves to one), so the panel cannot be used to make connections to the internet.
"""

import hashlib
import ipaddress
import re
import socket
import threading
import time
import uuid

PORT = 4352
MAX_PROJECTORS = 8
POWER = {"0": "off", "1": "on", "2": "cooling down", "3": "warming up"}
INPUT_KINDS = {"1": "RGB", "2": "Video", "3": "Digital", "4": "Storage", "5": "Network"}     # the first digit of an input
WARNINGS = ("fan", "lamp", "temperature", "cover", "filter", "other")                        # the order of ERST's six digits
MUTE = {"picture": "1", "sound": "2", "both": "3"}
POLL_EVERY = 45.0                # seconds between two status checks of one projector
POLL_CHANGING = 10.0             # while it warms up or cools down
STAGGER = 2.0                    # projector n starts n x this later, so they are never all asked at once
RETRY_FOR, RETRY_EVERY = 90.0, 5.0      # an input change refused as "unavailable" is tried again this long, this often
ERRORS = {"ERR1": "the projector does not know that command", "ERR2": "the projector refused that value",
          "ERR3": "the projector cannot do that right now", "ERR4": "the projector reports a fault",
          "ERRA": "wrong projector password"}
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                                             "fc00::/7", "fe80::/10")]
REFUSED = {ipaddress.ip_address("169.254.169.254")}      # the cloud metadata service on an x86 install in a VM
_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}|[0-9A-Fa-f:.]{2,45}")
_INPUT = re.compile(r"[1-5][1-9]")
SOFT = ("ERR1", "ERR3", "ERR4")  # an answer, but no value: a detail that is simply not known (yet)


class ProjectorError(Exception):
    """`code` says why, for code that must decide: ERR1 to ERR4 and ERRA from the projector, "busy" (another
    command of ours is still running), "unreachable" (no connection or no answer), or None."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def input_name(code):
    """ "31" -> "Digital 1": what the standard calls the input; the projector's own names are class 2."""
    return "%s %s" % (INPUT_KINDS.get(code[:1], "Input"), code[1:])


def clean_text(value, limit):
    """Text from a projector, safe to store and show: no control characters, cut to `limit`."""
    return re.sub("[\x00-\x1f\x7f-\x9f\ufffd]", "", value).strip()[:limit]


def validate_label(inputs, code, label):
    """A friendly label ("Matrix", "Box") for one of the projector's own inputs; "" takes the label away."""
    if not isinstance(code, str) or not _INPUT.fullmatch(code) or code not in inputs:
        raise ProjectorError("that is not one of this projector's inputs")
    if not isinstance(label, str) or len(label.strip()) > 24 or re.search(r"[\x00-\x1f\x7f]", label):
        raise ProjectorError("a label may have up to 24 characters")
    return label.strip()


def private_address(host, resolve=socket.getaddrinfo):
    """The IP address to connect to for `host`, only if it is on a private network. Raises ProjectorError."""
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        raise ProjectorError("enter the projector's IP address or name")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = resolve(host, PORT, proto=socket.IPPROTO_TCP)
        except (OSError, UnicodeError):          # a name that is not valid for DNS raises UnicodeError
            raise ProjectorError("cannot find %s on the network" % host)
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
        if not addrs:
            raise ProjectorError("cannot find %s on the network" % host)
        addr = addrs[0]
    if addr in REFUSED or not any(addr in n for n in PRIVATE):
        raise ProjectorError("only projectors on a private network (such as 192.168.x.x) can be added")
    return str(addr)


def validate(entry):
    """A clean projector entry {"id", "name", "host", "port", "password"} from untrusted input."""
    if not isinstance(entry, dict):
        raise ProjectorError("a projector must be an object")
    name = entry.get("name", "Projector")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or re.search(r"[\x00-\x1f]", name):
        raise ProjectorError("give the projector a name of up to 40 characters")
    host = entry.get("host")
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        raise ProjectorError("enter the projector's IP address or name")
    port = entry.get("port", PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ProjectorError("port must be 1 to 65535")
    password = entry.get("password", "")
    if not isinstance(password, str) or len(password) > 32 or re.search(r"[^\x21-\x7e]", password):
        raise ProjectorError("the password may have up to 32 plain characters")
    return {"id": uuid.uuid4().hex[:8], "name": name.strip(), "host": host, "port": port, "password": password}


def _read_line(sock, limit, deadline):
    """One PJLink line: it ends with a carriage return alone (not a line feed), so readline() would wait forever.
    `deadline` (time.monotonic) bounds the whole line, so a device that sends a byte now and then cannot hold us."""
    buf = b""
    while len(buf) < limit:
        left = deadline - time.monotonic()
        if left <= 0:
            raise socket.timeout("timed out")
        sock.settimeout(left)
        c = sock.recv(1)
        if not c:
            break
        if c in (b"\r", b"\n"):
            if buf:
                break
            continue
        buf += c
    return buf.decode("utf-8", "replace").strip()      # ASCII, except the projector's name, which is UTF-8


_busy = {}                       # (host, port) -> Lock: one command at a time per projector (many take one connection)
_busy_guard = threading.Lock()


def _lock_for(host, port):
    with _busy_guard:
        return _busy.setdefault((host, port), threading.Lock())


class PJLink:
    """One command per connection, as projectors expect. The connect and both reads share one deadline of
    2 x timeout; a name lookup is not bounded by us (use an IP address to avoid it)."""

    def __init__(self, host, port=PORT, password="", timeout=5.0, connect=socket.create_connection, resolve=socket.getaddrinfo):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self._connect, self._resolve = connect, resolve

    def command(self, body):
        """Send "%1" + body (e.g. "POWR 1") and return the answer after "=". Raises ProjectorError."""
        lock = _lock_for(self.host, self.port)
        if not lock.acquire(timeout=2 * self.timeout):
            raise ProjectorError("the projector is busy with another command", "busy")
        try:
            return self._command(body)
        finally:
            lock.release()

    def _command(self, body):
        addr = private_address(self.host, self._resolve)       # checked again at every use: a name may change
        deadline = time.monotonic() + 2 * self.timeout
        try:
            s = self._connect((addr, self.port), timeout=min(self.timeout, max(0.1, deadline - time.monotonic())))
        except OSError as e:
            raise ProjectorError("cannot reach the projector at %s: %s" % (self.host, getattr(e, "strerror", None) or e), "unreachable")
        try:
            greeting = _read_line(s, 128, deadline)
            if greeting == "PJLINK ERRA":
                raise ProjectorError(ERRORS["ERRA"], "ERRA")
            if greeting == "PJLINK 0":
                prefix = ""
            elif re.fullmatch(r"PJLINK 1 [0-9A-Fa-f]{8}", greeting):
                if not self.password:
                    raise ProjectorError("this projector needs a password")
                prefix = hashlib.md5((greeting.split()[2] + self.password).encode()).hexdigest()
            else:
                raise ProjectorError("that does not answer like a PJLink projector")
            s.sendall((prefix + "%1" + body + "\r").encode("ascii"))
            answer = _read_line(s, 300, deadline)
        except (OSError, socket.timeout) as e:
            raise ProjectorError("the projector did not answer: %s" % e, "unreachable")
        finally:
            s.close()
        if answer == "PJLINK ERRA":
            raise ProjectorError(ERRORS["ERRA"], "ERRA")
        m = re.fullmatch(r"%1([A-Za-z0-9]{4})=(.*)", answer)       # INF1 and INF2 have a digit; the case is free
        if not m or m.group(1).upper() != body.split()[0]:
            raise ProjectorError("unexpected answer from the projector")
        value = m.group(2)
        if value.upper() in ERRORS:
            raise ProjectorError(ERRORS[value.upper()], value.upper())
        return value

    def power(self, on):
        return self.command("POWR 1" if on else "POWR 0")

    def state(self):
        v = self.command("POWR ?")
        return POWER.get(v, "unknown")

    def mute(self, on, what="both"):
        """Mute or unmute the "picture", the "sound" or "both". A projector without separate mutes refuses those."""
        try:
            return self.command("AVMT %s%d" % (MUTE[what], 1 if on else 0))
        except ProjectorError as e:
            if e.code == "ERR2" and what != "both":
                raise ProjectorError("this projector cannot mute the picture and the sound separately", "ERR2")
            raise

    def mute_state(self):
        """{"picture": bool, "sound": bool}. The standard has four answers: 11, 21, 31 (both) and 30 (neither)."""
        v = self.command("AVMT ?")
        if v not in ("11", "21", "31", "30"):
            raise ProjectorError("unexpected answer from the projector")
        return {"picture": v in ("11", "31"), "sound": v in ("21", "31")}

    def input(self):
        """The input in use, such as "31"."""
        v = self.command("INPT ?")
        if not _INPUT.fullmatch(v):
            raise ProjectorError("unexpected answer from the projector")
        return v

    def set_input(self, code):
        if not isinstance(code, str) or not _INPUT.fullmatch(code):
            raise ProjectorError("the projector has no such input", "ERR2")
        try:
            return self.command("INPT " + code)
        except ProjectorError as e:
            if e.code == "ERR2":
                raise ProjectorError("the projector has no such input", "ERR2")
            raise

    def inputs(self):
        """The projector's own list of inputs, such as ["11", "31", "32"] (at most 50, says the standard)."""
        out = []
        for c in self.command("INST ?").split()[:50]:
            if _INPUT.fullmatch(c) and c not in out:
                out.append(c)
        return out

    def lamps(self):
        """[{"hours": int, "on": bool}] for up to 8 lamps. A display without a lamp answers ERR1."""
        parts = self.command("LAMP ?").split()
        if not parts or len(parts) % 2 or len(parts) > 16 or not all(re.fullmatch(r"[0-9]{1,5}", h) and o in ("0", "1")
                                                                     for h, o in zip(parts[::2], parts[1::2])):
            raise ProjectorError("unexpected answer from the projector")
        return [{"hours": int(h), "on": o == "1"} for h, o in zip(parts[::2], parts[1::2])]

    def warnings(self):
        """{"fan": "ok" | "warning" | "error", "lamp": ..., "temperature", "cover", "filter", "other"}."""
        v = self.command("ERST ?")
        if not re.fullmatch(r"[0-2]{6}", v):
            raise ProjectorError("unexpected answer from the projector")
        return {name: ("ok", "warning", "error")[int(d)] for name, d in zip(WARNINGS, v)}

    def identify(self):
        """Who the projector is: {"name", "maker", "model", "info", "class", "inputs"}. A value the projector
        would not give right now (many refuse some of these in standby) is None; a projector that cannot be
        reached at all raises at the first question, so an unplugged one costs one timeout, not six."""
        def ask(fn):
            try:
                return fn()
            except ProjectorError as e:
                if e.code in SOFT:
                    return None
                raise
        out = {"class": ask(lambda: clean_text(self.command("CLSS ?"), 1))}
        if out["class"] is not None and not re.fullmatch(r"[1-9]", out["class"]):
            out["class"] = None
        for key, cmd, limit in (("name", "NAME ?", 64), ("maker", "INF1 ?", 32), ("model", "INF2 ?", 32), ("info", "INFO ?", 32)):
            out[key] = ask(lambda: clean_text(self.command(cmd), limit))
        out["inputs"] = ask(self.inputs)
        return out


class _Worker:
    def __init__(self, pid):
        self.pid = pid
        self.stop = threading.Event()         # this thread's own: a newer worker never revives an old one
        self.wake = threading.Event()
        self.thread = None
        self.due = 0.0                        # time.monotonic() of the next status check
        self.identify = False                 # read the details at the next turn
        self.pending = None                   # {"input", "until", "next"}: an input change being retried
        self.asked_inputs = False             # asked for the input list since it was last seen switched on


class Monitor:
    """The background status of every projector, and the retry of a refused input change.

    One thread per projector (so at most MAX_PROJECTORS), each with its own stop signal; apply() starts and stops
    them to match the settings and the module switch, and never starts a second thread for a projector whose old
    one is still finishing a command. The status lives in memory only. Nothing here holds a lock while it talks
    to a projector, and nothing joins a thread."""

    def __init__(self, api, interval=POLL_EVERY, changing=POLL_CHANGING, stagger=STAGGER, retry_for=RETRY_FOR,
                 retry_every=RETRY_EVERY, log=print):
        self.api, self.log = api, log
        self.interval, self.changing, self.stagger, self.retry_for, self.retry_every = interval, changing, stagger, retry_for, retry_every
        self.lock = threading.Lock()
        self._workers = {}       # projector id -> _Worker
        self._leaving = []       # stopped workers whose thread has not ended yet
        self._status = {}        # projector id -> the last answer
        self._notice = {}        # projector id -> {"ok", "text"}: how the last input change ended
        self._closed = False

    def _entries(self):
        if self._closed or not self.api.registry.enabled("projector"):
            return []
        return list(self.api.settings.data.get("projectors") or [])

    def _entry(self, pid):
        return next((p for p in self._entries() if p["id"] == pid), None)

    def apply(self):
        """Match the threads to the projectors in the settings; none at all while the module is off."""
        want = {p["id"]: (i, p) for i, p in enumerate(self._entries())}
        with self.lock:
            for pid in [k for k in self._workers if k not in want]:
                self._retire(self._workers.pop(pid))
            self._leaving = [w for w in self._leaving if w.thread.is_alive()]
            waiting = {w.pid for w in self._leaving}
            for pid, (i, p) in want.items():
                if pid in self._workers or pid in waiting:      # the old thread calls apply() again when it ends
                    continue
                w = self._workers[pid] = _Worker(pid)
                w.identify = not p.get("details")
                w.due = time.monotonic() + i * self.stagger
                w.thread = threading.Thread(target=self._loop, args=(w,), name="projector-poll", daemon=True)
                w.thread.start()

    def _retire(self, w):
        """With self.lock held."""
        w.stop.set()
        w.wake.set()
        self._status.pop(w.pid, None)
        self._notice.pop(w.pid, None)
        self._leaving.append(w)

    def stop(self, final=False):
        """Stop every thread. `final`: the panel is closing, start none again."""
        with self.lock:
            self._closed = self._closed or final
            for pid in list(self._workers):
                self._retire(self._workers.pop(pid))

    def threads(self):
        with self.lock:
            return [w.thread for w in list(self._workers.values()) + self._leaving if w.thread.is_alive()]

    # -- what the panel reads --
    def status(self, pid):
        with self.lock:
            st = dict(self._status.get(pid) or {})
            w = self._workers.get(pid)
            st["pending_input"] = w.pending["input"] if w and w.pending else None
            st["notice"] = self._notice.get(pid)
        return st

    def poke(self, pid):
        """Check this projector (or "all") again now: something was just changed."""
        with self.lock:
            for w in self._workers.values():
                if pid in ("all", w.pid):
                    w.due = 0.0
                    w.wake.set()

    def health(self):
        """For the Health card: [{"id", "name", "state", "text"}] from the last answers; asks nothing."""
        out = []
        for p in self._entries():
            st = self.status(p["id"])
            row = {"id": p["id"], "name": p["name"], "state": "unknown"}
            out.append(row)
            if "ok" not in st:
                row["text"] = "Not checked yet."
                continue
            if not st["ok"]:
                row["text"] = "No answer: %s." % st["error"]
                continue
            words = [st["power"].capitalize()]
            lamps = st.get("lamps")
            if lamps:
                words.append(("lamp %s h" if len(lamps) == 1 else "lamps %s h") % ", ".join(str(l["hours"]) for l in lamps))
            warn = st.get("warnings")
            errors = [k for k in WARNINGS if (warn or {}).get(k) == "error"]
            warns = [k for k in WARNINGS if (warn or {}).get(k) == "warning"]
            row["text"] = ", ".join(words) + "."
            if errors:
                row["text"] += " Error: %s." % ", ".join(errors)
            if warns:
                row["text"] += " Warning: %s." % ", ".join(warns)
            if warn is None:
                row["text"] += " Warnings not read."
            row["state"] = "bad" if errors else ("warn" if warns else "ok")
        return out

    # -- details --
    def identify(self, entry):
        """Ask the projector who it is and keep the answer in the settings. What it would not say this time
        keeps its older value. Raises ProjectorError if it cannot be reached."""
        got = self.api._pjlink(entry).identify()          # no lock held: this is the slow part
        settings = self.api.settings
        with settings.lock:
            items = list(settings.data.get("projectors") or [])
            for i, p in enumerate(items):
                if p["id"] == entry["id"]:                # still there; a removed projector is not brought back
                    details = dict(p.get("details") or {})
                    details.update({k: v for k, v in got.items() if v is not None or k not in details})
                    details["read"] = int(time.time())
                    labels = {c: l for c, l in (p.get("labels") or {}).items() if c in (details.get("inputs") or [])}
                    items[i] = dict(p, details=details, labels=labels)
                    settings.data["projectors"] = items
                    settings.save()
                    return details
        return None

    # -- input, with the retry --
    def set_input(self, entry, code):
        """Switch the input now. If the projector says "unavailable" (warming up, mostly), keep trying in the
        background for retry_for seconds; {"pending": True} then. Other refusals raise ProjectorError."""
        pid = entry["id"]
        with self.lock:
            w = self._workers.get(pid)
            if w:
                w.pending = None                       # a newer choice replaces one still being retried
            self._notice.pop(pid, None)
        try:
            self.api._pjlink(entry).set_input(code)
        except ProjectorError as e:
            if e.code != "ERR3":
                raise
            now = time.monotonic()
            with self.lock:
                w = self._workers.get(pid)
                if w is None or w.stop.is_set():
                    raise
                w.pending = {"input": code, "until": now + self.retry_for, "next": now + self.retry_every}
                w.wake.set()
            return {"pending": True}
        self.poke(pid)
        return {"pending": False}

    def _retry(self, w, entry, pending):
        label = (entry.get("labels") or {}).get(pending["input"]) or input_name(pending["input"])
        try:
            self.api._pjlink(entry).set_input(pending["input"])
            notice = {"ok": True, "text": "Input switched to %s." % label}
        except ProjectorError as e:
            if e.code in ("ERR3", "busy", "unreachable") and time.monotonic() + self.retry_every <= pending["until"]:
                pending["next"] = time.monotonic() + self.retry_every
                return
            why = "it was still not ready after %d seconds (is it switched on?)" % self.retry_for if e.code == "ERR3" else str(e)
            notice = {"ok": False, "text": "Could not switch to %s: %s." % (label, why)}
        except Exception as e:
            notice = {"ok": False, "text": "Could not switch to %s: error: %s." % (label, e)}
        with self.lock:
            if w.pending is not pending or w.stop.is_set():      # replaced by a newer choice, or switched off
                return
            w.pending = None
            w.due = 0.0
            self._notice[w.pid] = notice
        if not notice["ok"]:
            self.log("pvj-web: projector %s: %s" % (entry["name"], notice["text"]))

    # -- the status --
    def _poll(self, w, entry):
        link = self.api._pjlink(entry)

        def ask(fn):
            try:
                return fn()
            except ProjectorError as e:
                if e.code in SOFT:
                    return None
                raise
        st = {"ok": True, "checked": int(time.time()), "input": None, "mute": None}
        try:
            st["power"] = link.state()
            st["warnings"] = ask(link.warnings)
            st["lamps"] = ask(link.lamps)
            if st["power"] == "on":                 # in standby these two are "unavailable" by the standard
                st["input"] = ask(link.input)
                st["mute"] = ask(link.mute_state)
        except ProjectorError as e:
            if e.code == "busy":                    # our own command is in the way; the last answer stands
                return None
            st = {"ok": False, "checked": int(time.time()), "error": str(e)}
        except Exception as e:
            st = {"ok": False, "checked": int(time.time()), "error": "error: %s" % e}
        with self.lock:
            if w.stop.is_set():
                return None
            self._status[w.pid] = st
        return st

    def _loop(self, w):
        try:
            while not w.stop.is_set():
                entry = self._entry(w.pid)
                if entry is None:
                    break
                with self.lock:
                    identify, w.identify = w.identify, False
                    pending = w.pending
                if identify:
                    try:
                        self.identify(entry)
                    except Exception:               # the status check below says what is wrong
                        pass
                    entry = self._entry(w.pid) or entry
                if pending and time.monotonic() >= pending["next"] and not w.stop.is_set():
                    self._retry(w, entry, pending)
                if time.monotonic() >= w.due and not w.stop.is_set():
                    st = self._poll(w, entry)
                    power = (st or {}).get("power")
                    with self.lock:
                        w.due = time.monotonic() + (self.changing if power in ("warming up", "cooling down") else self.interval)
                        if power != "on":
                            w.asked_inputs = False
                        elif not (entry.get("details") or {}).get("inputs") and not w.asked_inputs:
                            w.asked_inputs = w.identify = True      # it would not list its inputs in standby
                with self.lock:
                    nxt = min(w.due, w.pending["next"]) if w.pending else w.due
                    if w.identify:
                        nxt = 0.0
                w.wake.wait(max(0.0, nxt - time.monotonic()))
                w.wake.clear()
        finally:
            with self.lock:
                if self._workers.get(w.pid) is w:
                    del self._workers[w.pid]
                    self._status.pop(w.pid, None)
                    self._notice.pop(w.pid, None)
                self._leaving = [x for x in self._leaving if x is not w]
                again = w.stop.is_set() and not self._closed
            if again:
                try:
                    self.apply()                    # a projector that came back while this thread was ending
                except Exception:
                    pass
