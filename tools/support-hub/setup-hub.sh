#!/bin/bash
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
#
# Set up a support server (hub) for nxlx.mastercontrol remote support, on a small Debian or Ubuntu server with a
# fixed public IP address. Run as root, once:   ./setup-hub.sh [public-host-or-ip] [port]
#
# The hub is a WireGuard interface pvj0 on 10.77.0.1/24. Support laptops get 10.77.0.2 to .31, boxes .32 to .254.
# Its firewall lets a support laptop reach a box's panel (TCP 80) and ping it, and nothing else: boxes cannot reach
# each other or the laptops, and nothing is routed to the internet. Boxes connect only while someone at the studio
# has started a session. See docs/REMOTE-SUPPORT.md.
set -euo pipefail

HOST="${1:-$(hostname -f)}"
PORT="${2:-51820}"
DIR=/etc/wireguard
CONF="$DIR/pvj0.conf"

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
if [ -e "$CONF" ]; then echo "$CONF exists already; nothing changed" >&2; exit 1; fi
case "$PORT" in ''|*[!0-9]*) echo "the port must be a number" >&2; exit 1;; esac

apt-get install -y --no-install-recommends wireguard-tools nftables

umask 077
mkdir -p "$DIR"
wg genkey > "$DIR/pvj0.key"
wg pubkey < "$DIR/pvj0.key" > "$DIR/pvj0.pub"
cat > "$CONF" <<CONF
# nxlx.mastercontrol support hub. Peers are added with add-peer.sh.
[Interface]
Address = 10.77.0.1/24
ListenPort = $PORT
PostUp = wg set %i private-key $DIR/pvj0.key
PostUp = nft -f /etc/wireguard/pvj0.nft
PostDown = nft delete table inet pvj_hub
CONF

cat > "$DIR/pvj0.nft" <<'NFT'
table inet pvj_hub
delete table inet pvj_hub
table inet pvj_hub {
    set support { type ipv4_addr; flags interval; elements = { 10.77.0.2-10.77.0.31 } }
    set boxes { type ipv4_addr; flags interval; elements = { 10.77.0.32-10.77.0.254 } }
    chain forward {
        type filter hook forward priority 0; policy accept;
        iifname "pvj0" oifname "pvj0" ct state established,related accept
        iifname "pvj0" oifname "pvj0" ip saddr @support ip daddr @boxes tcp dport 80 accept
        iifname "pvj0" oifname "pvj0" ip saddr @support ip daddr @boxes icmp type echo-request accept
        iifname "pvj0" drop
        oifname "pvj0" drop
    }
    chain input {
        type filter hook input priority 0; policy accept;
        iifname "pvj0" ct state established,related accept
        iifname "pvj0" icmp type echo-request accept
        iifname "pvj0" drop
    }
}
NFT

echo 'net.ipv4.ip_forward = 1' > /etc/sysctl.d/90-pvj-hub.conf
sysctl -q -p /etc/sysctl.d/90-pvj-hub.conf
systemctl enable --now wg-quick@pvj0

cat <<DONE

The support hub is running. Settings for every box (System > Remote support):
  Support server      $HOST:$PORT
  Support server key  $(cat "$DIR/pvj0.pub")
  Support network     10.77.0.0/24
  This box            a free address from 10.77.0.32 to 10.77.0.254 (add-peer.sh box ... tells you)

Open UDP port $PORT in this server's firewall. Then add your support laptop:
  ./add-peer.sh support my-laptop
and each box:
  ./add-peer.sh box studio-a <the key shown in the box's panel>
DONE
