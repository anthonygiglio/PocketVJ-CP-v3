# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Wired network configuration: validation, the exact NetworkManager commands, and a safe-apply timer.

Nothing here runs a command. `plan()` turns a validated request into argument lists (never a shell string);
`pvj/netd.py` executes them as root. Changing the network of the box you are controlling over that network
can lock you out, so every change is *pending* until confirmed and reverts by itself when the timer runs out.

A change never edits the confirmed profile in place. It is built as a separate *candidate* profile
(`pvj-<iface>-try`, not autoconnect) and brought up; the old profile is untouched, so a power cut, a
crash or a timeout always leaves the old network intact. Only Confirm swaps the candidate in.

Modes (wired first, as on the approved Network wireframe):
  dhcp       take an address from a router
  static     a fixed address, optional gateway and DNS
  linklocal  a direct cable between laptop and box: 169.254.x.x, no router needed
  share      the box serves addresses to whatever is plugged in (NetworkManager "shared")
"""

import ipaddress
import os
import re
import shlex

MODES = ("dhcp", "static", "linklocal", "share")
IFACE = re.compile(r"[a-z][a-z0-9_.-]{0,14}")
DEFAULT_SHARE = ("10.42.0.1", 24)
MIN_REVERT, MAX_REVERT, DEFAULT_REVERT = 20, 300, 60
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_ZERO_NET = ipaddress.ip_network("0.0.0.0/8")
_RFC1918 = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]


class NetError(Exception):
    pass


def profile_name(iface):
    return "pvj-" + iface


def candidate_name(iface):
    return "pvj-%s-try" % iface


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


def _reject_odd_unicast(addr, what):
    if (addr.is_unspecified or addr.is_multicast or addr.is_loopback or addr.is_link_local or addr.is_reserved
            or addr in _ZERO_NET):
        raise NetError("%s %s cannot be used on a network" % (what, addr))


def validate(request, interfaces, others=()):
    """Return a clean config dict or raise NetError.

    `interfaces` is list_interfaces(); `others` is [(iface, ip_network), ...] for the subnets already in use
    on this box, so a new address cannot collide with another port's network."""
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
        addr = _ipv4(request.get("address", default[0]), "address")
        prefix = request.get("prefix", default[1])
        low = 16 if mode == "share" else 8  # a served pool bigger than a /16 would flood a whole network
        if isinstance(prefix, bool) or not isinstance(prefix, int) or not low <= prefix <= 30:
            raise NetError("prefix must be a whole number from %d to 30 (24 means 255.255.255.0)" % low)
        _reject_odd_unicast(addr, "address")
        net = ipaddress.ip_network("%s/%d" % (addr, prefix), strict=False)
        if addr == net.network_address or addr == net.broadcast_address:
            raise NetError("address %s is the network or broadcast address of %s" % (addr, net))
        if mode == "share" and not any(addr in n for n in _RFC1918):
            raise NetError("the box may only serve addresses from 10.x, 172.16-31.x or 192.168.x")
        for other_iface, other_net in others:
            if other_iface != iface and net.overlaps(other_net):
                raise NetError("%s overlaps %s, which %s already uses; pick a different range" % (net, other_net, other_iface))
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
                _reject_odd_unicast(da, "DNS server")
                if str(da) in servers or da == addr:
                    raise NetError("DNS server %s is listed twice or is this box's own address" % da)
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


def plan(cfg):
    """Commands that build the CANDIDATE profile and bring it up. The confirmed profile is not touched."""
    name = candidate_name(cfg["iface"])
    # autoconnect stays OFF until confirmed: a reboot now falls back to the old network.
    return [["nmcli", "connection", "add", "type", "ethernet", "ifname", cfg["iface"], "con-name", name]
            + settings_for(cfg, autoconnect="no"),
            ["nmcli", "connection", "up", "id", name]]


def confirm_plan(iface, old_exists):
    """Swap the candidate in. Order matters: it first becomes the preferred autoconnect profile, and only
    then is the old one removed, so at no moment is there no autoconnect profile."""
    cand, final = candidate_name(iface), profile_name(iface)
    cmds = [["nmcli", "connection", "modify", "id", cand, "connection.autoconnect", "yes",
             "connection.autoconnect-priority", "101"]]
    if old_exists:
        cmds.append(["nmcli", "connection", "delete", "id", final])
    cmds.append(["nmcli", "connection", "modify", "id", cand, "connection.id", final])
    return cmds


def revert_plan(iface, candidate_exists, previous_uuid):
    """Undo an unconfirmed change: drop the candidate and re-activate what was active before."""
    cmds = []
    if candidate_exists:
        name = candidate_name(iface)
        cmds += [["nmcli", "connection", "down", "id", name], ["nmcli", "connection", "delete", "id", name]]
    if previous_uuid:
        if not UUID.fullmatch(previous_uuid):
            raise NetError("bad connection id in the saved state")
        cmds.append(["nmcli", "connection", "up", "uuid", previous_uuid])
    return cmds


def preview(cmds):
    """Human-readable commands: shell-quoted so empty and spaced arguments stay visible."""
    return [shlex.join(c) for c in cmds]


class PendingChange:
    """A change waiting for confirmation."""

    def __init__(self, cfg, previous_uuid, started, seconds):
        self.cfg, self.previous_uuid, self.seconds = cfg, previous_uuid, seconds
        self.deadline = started + seconds

    def restart(self, now):
        """The countdown starts when the new network is up, not when the commands began."""
        self.deadline = now + self.seconds

    def seconds_left(self, now):
        return max(0, int(self.deadline - now + 0.999))

    def expired(self, now):
        return now >= self.deadline
