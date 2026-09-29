#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Send a few Art-Net frames to a box, to test the DMX module without a console.

  tools/artnet-send.py HOST [--universe 0] [--start 1] blackout|show|opacity=50|pad=3|stop|fade

The first frame after switching DMX on only sets a baseline, so this sends a quiet baseline, waits a
moment, then sends the change for two seconds (frames repeat, like a console).
"""
import argparse
import socket
import struct
import time


def packet(universe, channels):
    data = bytes(channels).ljust(2, b"\0")
    return (b"Art-Net\x00" + struct.pack("<H", 0x5000) + struct.pack(">H", 14) + bytes([1, 0, universe & 0xFF, universe >> 8])
            + struct.pack(">H", len(data)) + data)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host")
    ap.add_argument("what", help="blackout, show, opacity=0..100, pad=1..36, stop or fade")
    ap.add_argument("--universe", type=int, default=0)
    ap.add_argument("--start", type=int, default=1)
    a = ap.parse_args()
    ch = [0] * 512
    i = a.start - 1
    name, _, value = a.what.partition("=")
    base = [0] * 8
    if name == "blackout":
        ch[i + 5] = 255
    elif name == "show":
        base[5] = 255                       # baseline has the screen dark, then it is shown
    elif name == "opacity":
        base[0] = 255
        ch[i] = round(float(value) * 2.55)
    elif name == "pad":
        ch[i + 6] = 6 * int(value)
    elif name == "stop":
        ch[i + 7] = 60
    elif name == "fade":
        ch[i + 7] = 220
    else:
        ap.error("unknown action")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    quiet = [0] * 512
    quiet[i:i + 8] = base
    for _ in range(10):
        sock.sendto(packet(a.universe, quiet), (a.host, 6454))
        time.sleep(0.05)
    time.sleep(0.5)
    end = time.time() + 2
    while time.time() < end:
        sock.sendto(packet(a.universe, ch), (a.host, 6454))
        time.sleep(0.05)
    print("sent %s to %s universe %d start %d" % (a.what, a.host, a.universe, a.start))


if __name__ == "__main__":
    main()
