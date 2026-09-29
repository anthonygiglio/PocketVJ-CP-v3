# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
"""Wired network configuration: validation, the exact NetworkManager commands, and a safe-apply timer.

Nothing here runs a command. `plan()` turns a validated request into argument lists (never a shell string);
`pvj/netd.py` executes them as root. Changing the network of the box you are controlling over that network
can lock you out, so every change is *pending* until confirmed and reverts by itself when the timer runs out.

Modes (wired first, as on the approved Network wireframe):
  dhcp       take an address from a router
  static     a fixed address, optional gateway and DNS
  linklocal  a direct cable between laptop and box: 169.254.x.x, no router needed
  share      the box serves addresses to whatever is plugged in (NetworkManager "shared")
"""

import ipaddress
import os
import re

MODES = ("dhcp", "static", "linklocal", "share")
IFACE = re.compile(r"[a-z][a-z0-9_.-]{0,14}")
DEFAULT_SHARE = ("10.42.0.1", 24)
MIN_REVERT, MAX_REVERT, DEFAULT_REVERT = 20, 300, 60


class NetError(Exception):
    pass


def profile_name(iface):
    return "pvj-" + iface


def list_interfaces(sysfs="/sys/class/net"):
    """Wired interfaces first, then wireless, from /sys. No privileges needed."""
    out = []
    try:
        names = sorted(os.listdir(sysfs))
    except OSError:
        return out
    for name in names:
        if name == "lo" or not IFACE.fullmatch(name):
            continue
        base = os.path.join(sysfs, name)
        if not os.path.exists(os.path.join(base, "address")):
            continue  # not a real link-layer device (bridges without a MAC, tunnels, ...)
        if any(os.path.exists(os.path.join(base, x)) for x in ("bridge", "bonding", "tun_flags")) or \
                name.startswith(("docker", "veth", "br-", "virbr", "tap", "tun")):
            continue
        wireless = os.path.exists(os.path.join(base, "wireless")) or os.path.exists(os.path.join(base, "phy80211"))

        def read(p):
            try:
                with open(os.path.join(base, p)) as f:
                    return f.read().strip()
            except OSError:
                return None
        speed = read("speed")
        out.append({"name": name, "kind": "wifi" if wireless else "wired", "state": read("operstate") or "unknown",
                    "carrier": read("carrier") == "1", "mac": read("address"),
                    "speed_mbps": int(speed) if speed and speed.lstrip("-").isdigit() and int(speed) > 0 else None})
    out.sort(key=lambda i: (i["kind"] != "wired", i["name"]))
    return out


def _ipv4(text, what):
    try:
        addr = ipaddress.IPv4Address(text)
    except (ipaddress.AddressValueError, ValueError, TypeError):
        raise NetError("%s must be an IPv4 address like 192.168.1.50" % what)
    if str(addr) != text:
        raise NetError("%s must be written plainly (no leading zeros)" % what)
    return addr


def validate(request, interfaces):
    """Return a clean config dict or raise NetError. `interfaces` is list_interfaces()."""
    if not isinstance(request, dict):
        raise NetError("request must be an object")
    iface = request.get("iface")
    if not isinstance(iface, str) or not IFACE.fullmatch(iface):
        raise NetError("invalid interface name")
    match = [i for i in interfaces if i["name"] == iface]
    if not match:
        raise NetError("no such interface: %s" % iface)
    if match[0]["kind"] != "wired":
        raise NetError("only wired interfaces can be changed here")
    mode = request.get("mode")
    if mode not in MODES:
        raise NetError("mode must be one of: %s" % ", ".join(MODES))
    revert = request.get("revert_seconds", DEFAULT_REVERT)
    if isinstance(revert, bool) or not isinstance(revert, int) or not MIN_REVERT <= revert <= MAX_REVERT:
        raise NetError("revert_seconds must be %d to %d" % (MIN_REVERT, MAX_REVERT))
    cfg = {"iface": iface, "mode": mode, "revert_seconds": revert}
    if mode in ("static", "share"):
        default = DEFAULT_SHARE if mode == "share" else (None, None)
        text = request.get("address", default[0])
        addr = _ipv4(text, "address")
        prefix = request.get("prefix", default[1])
        if isinstance(prefix, bool) or not isinstance(prefix, int) or not 8 <= prefix <= 30:
            raise NetError("prefix must be a whole number from 8 to 30 (24 means 255.255.255.0)")
        if addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise NetError("address %s cannot be used on a network" % addr)
        net = ipaddress.ip_network("%s/%d" % (addr, prefix), strict=False)
        if addr == net.network_address or addr == net.broadcast_address:
            raise NetError("address %s is the network or broadcast address of %s" % (addr, net))
        if mode == "share" and not addr.is_private:
            raise NetError("the box may only serve addresses from a private range (10.x, 172.16-31.x, 192.168.x)")
        cfg.update(address=str(addr), prefix=prefix)
        if mode == "static":
            gw = request.get("gateway")
            if gw not in (None, ""):
                gwa = _ipv4(gw, "gateway")
                if gwa not in net or gwa == addr or gwa == net.network_address or gwa == net.broadcast_address:
                    raise NetError("gateway %s must be another address inside %s" % (gwa, net))
                cfg["gateway"] = str(gwa)
            dns = request.get("dns", [])
            if not isinstance(dns, list) or len(dns) > 3:
                raise NetError("dns must be a list of up to 3 addresses")
            servers = []
            for d in dns:
                da = _ipv4(d, "DNS server")
                if da.is_unspecified or da.is_multicast or da.is_loopback or da.is_reserved:
                    raise NetError("DNS server %s cannot be used" % da)
                servers.append(str(da))
            cfg["dns"] = servers
    return cfg


def settings_for(cfg, autoconnect="yes"):
    """NetworkManager property list for a validated config."""
    s = ["connection.autoconnect", autoconnect, "connection.autoconnect-priority", "100"]
    mode = cfg["mode"]
    if mode == "dhcp":
        s += ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", "", "ipv6.method", "auto"]
    elif mode == "static":
        s += ["ipv4.method", "manual", "ipv4.addresses", "%s/%d" % (cfg["address"], cfg["prefix"]),
              "ipv4.gateway", cfg.get("gateway", ""), "ipv4.dns", " ".join(cfg.get("dns", [])), "ipv6.method", "auto"]
    elif mode == "linklocal":
        s += ["ipv4.method", "link-local", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", "", "ipv6.method", "link-local"]
    else:  # share
        s += ["ipv4.method", "shared", "ipv4.addresses", "%s/%d" % (cfg["address"], cfg["prefix"]),
              "ipv4.gateway", "", "ipv4.dns", "", "ipv6.method", "link-local"]
    return s


def plan(cfg, profile_exists, previous_active=None):
    """The nmcli commands for a validated config, as argument lists."""
    name = profile_name(cfg["iface"])
    # autoconnect stays OFF until the change is confirmed: if the box reboots while a change is
    # waiting, it comes back on the old network instead of on one nobody has confirmed.
    if profile_exists:
        cmds = [["nmcli", "connection", "modify", name] + settings_for(cfg, autoconnect="no")]
    else:
        cmds = [["nmcli", "connection", "add", "type", "ethernet", "ifname", cfg["iface"], "con-name", name]
                + settings_for(cfg, autoconnect="no")]
    cmds.append(["nmcli", "connection", "up", name])
    return cmds


def confirm_plan(iface):
    """Make a confirmed change permanent: it now wins at boot."""
    return [["nmcli", "connection", "modify", profile_name(iface), "connection.autoconnect", "yes",
             "connection.autoconnect-priority", "100"]]


SNAPSHOT_FIELDS = ("ipv4.method", "ipv4.addresses", "ipv4.gateway", "ipv4.dns", "ipv6.method",
                   "connection.autoconnect", "connection.autoconnect-priority")


def parse_show(text):
    """`nmcli -t -f ... connection show NAME` prints KEY:VALUE lines; values may contain colons."""
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().replace("\\:", ":")
    return out


def revert_plan(iface, snapshot, previous_active):
    """Commands that undo an apply. `snapshot` is None if our profile did not exist before."""
    name = profile_name(iface)
    if snapshot is None:
        cmds = [["nmcli", "connection", "down", name], ["nmcli", "connection", "delete", name]]
        if previous_active and previous_active != name:
            cmds.append(["nmcli", "connection", "up", previous_active])
        return cmds
    args = []
    for key in SNAPSHOT_FIELDS:
        args += [key, snapshot.get(key, "")]
    return [["nmcli", "connection", "modify", name] + args, ["nmcli", "connection", "up", name]]


class PendingChange:
    """A change waiting for confirmation. `tick(now)` says whether the deadline has passed."""

    def __init__(self, cfg, snapshot, previous_active, started, seconds):
        self.cfg, self.snapshot, self.previous_active = cfg, snapshot, previous_active
        self.deadline = started + seconds

    def seconds_left(self, now):
        return max(0, int(self.deadline - now + 0.999))

    def expired(self, now):
        return now >= self.deadline
