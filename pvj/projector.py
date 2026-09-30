# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Projector control over PJLink (class 1): the old panel's Beamer On and Beamer Off.

PJLink is the common network control standard for projectors (Epson, NEC, Panasonic, Sony, Hitachi, Eiki, Christie
and most others): TCP port 4352, one short text command per connection. With a projector password set, the projector
sends a random number and every command is prefixed with MD5(random + password), as the standard requires (PJLink's
own design; it keeps the password off the wire, nothing more).

Commands used: power on and off, power state, and A/V mute (the picture and sound off without the lamp cool-down).

Only projectors on a private network can be added (IP addresses in 10/8, 172.16/12, 192.168/16, 169.254/16 and their
IPv6 equivalents, or a name that resolves to one), so the panel cannot be used to make connections to the internet.
"""

import hashlib
import ipaddress
import re
import socket
import time
import uuid

PORT = 4352
MAX_PROJECTORS = 8
POWER = {"0": "off", "1": "on", "2": "cooling down", "3": "warming up"}
ERRORS = {"ERR1": "the projector does not know that command", "ERR2": "the projector refused that value",
          "ERR3": "the projector cannot do that right now", "ERR4": "the projector reports a fault",
          "ERRA": "wrong projector password"}
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                                             "fc00::/7", "fe80::/10")]
_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}|[0-9A-Fa-f:.]{2,45}")


class ProjectorError(Exception):
    pass


def private_address(host, resolve=socket.getaddrinfo):
    """The IP address to connect to for `host`, only if it is on a private network. Raises ProjectorError."""
    if not isinstance(host, str) or not _HOST.fullmatch(host):
        raise ProjectorError("enter the projector's IP address or name")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = resolve(host, PORT, proto=socket.IPPROTO_TCP)
        except OSError:
            raise ProjectorError("cannot find %s on the network" % host)
        addrs = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
        if not addrs:
            raise ProjectorError("cannot find %s on the network" % host)
        addr = addrs[0]
    if not any(addr in n for n in PRIVATE):
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
    return buf.decode("ascii", "replace").strip()


class PJLink:
    """One command per connection, as projectors expect."""

    def __init__(self, host, port=PORT, password="", timeout=5.0, connect=socket.create_connection, resolve=socket.getaddrinfo):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self._connect, self._resolve = connect, resolve

    def command(self, body):
        """Send "%1" + body (e.g. "POWR 1") and return the answer after "=". Raises ProjectorError."""
        addr = private_address(self.host, self._resolve)       # checked again at every use: a name may change
        try:
            s = self._connect((addr, self.port), timeout=self.timeout)
        except OSError as e:
            raise ProjectorError("cannot reach the projector at %s: %s" % (self.host, getattr(e, "strerror", None) or e))
        try:
            s.settimeout(self.timeout)
            deadline = time.monotonic() + 2 * self.timeout
            greeting = _read_line(s, 128, deadline)
            if greeting == "PJLINK ERRA":
                raise ProjectorError(ERRORS["ERRA"])
            if greeting == "PJLINK 0":
                prefix = ""
            elif re.fullmatch(r"PJLINK 1 [0-9A-Fa-f]{8}", greeting):
                if not self.password:
                    raise ProjectorError("this projector needs a password")
                prefix = hashlib.md5((greeting.split()[2] + self.password).encode()).hexdigest()
            else:
                raise ProjectorError("that does not answer like a PJLink projector")
            s.sendall((prefix + "%1" + body + "\r").encode("ascii"))
            answer = _read_line(s, 256, deadline)
        except (OSError, socket.timeout) as e:
            raise ProjectorError("the projector did not answer: %s" % e)
        finally:
            s.close()
        if answer == "PJLINK ERRA":
            raise ProjectorError(ERRORS["ERRA"])
        m = re.fullmatch(r"%1([A-Z]{4})=(.*)", answer)
        if not m or m.group(1) != body.split()[0]:
            raise ProjectorError("unexpected answer from the projector")
        value = m.group(2)
        if value in ERRORS:
            raise ProjectorError(ERRORS[value])
        return value

    def power(self, on):
        return self.command("POWR 1" if on else "POWR 0")

    def state(self):
        v = self.command("POWR ?")
        return POWER.get(v, "unknown")

    def mute(self, on):
        return self.command("AVMT 31" if on else "AVMT 30")
