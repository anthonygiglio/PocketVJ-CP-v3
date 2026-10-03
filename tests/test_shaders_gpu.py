# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""SPIKE (temporary): what a real mpv with a real (software) GPU does with user shaders. Prints facts."""
import json
import os
import shutil
import socket
import struct
import tempfile
import time
import unittest
import zlib

from pvj.player import Player, PlayerError

GPU = os.environ.get("PVJ_GPU_TEST") == "1"
W, H = 320, 180


def png_rows(path):
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, w = 8, b"", None
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if kind == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype in (2, 6), (depth, ctype)
            bpp = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat += body
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev = [], bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[x * bpp:x * bpp + 3]) for x in range(w)])
        prev = line
    return w, h, rows


class Tap:
    def __init__(self, path, level="error"):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.s.sendall(json.dumps({"command": ["request_log_messages", level]}).encode() + b"\n")
        self.buf = b""

    def drain(self, seconds):
        out = []
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.s.settimeout(max(0.01, end - time.monotonic()))
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            self.buf += chunk
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("event") == "log-message":
                    out.append((m.get("prefix"), m.get("level"), (m.get("text") or "").rstrip()))
        return out


GRAD = "vec4 hook() { return vec4(HOOKED_pos.x, HOOKED_pos.y, 0.25, 1.0); }\n"
RGB = "av://lavfi:color=c=black:size=%dx%d:rate=30,format=rgb0" % (W, H)
HEAD = "//!HOOK NATIVE\n//!BIND HOOKED\n//!DESC %s\n"
G = {
    "g1-all": "#define TIME (mod(float(frame), 1048576.0) / 30.0)\nconst float speed = 0.5;\nconst vec2 RENDERSIZE = vec2(320.0, 180.0);\n"
              "vec2 pvj_norm; vec4 pvj_color;\n#line 100\nvoid pvj_main() { pvj_color = vec4(pvj_norm, 0.5 + 0.5 * sin(TIME * speed), 1.0); }\n"
              "vec4 hook() { pvj_norm = vec2(HOOKED_pos.x, 1.0 - HOOKED_pos.y); pvj_main(); return pvj_color; }\n",
    "g2-noline": "#define TIME (mod(float(frame), 1048576.0) / 30.0)\nconst float speed = 0.5;\n"
                 "vec2 pvj_norm; vec4 pvj_color;\nvoid pvj_main() { pvj_color = vec4(pvj_norm, 0.5 + 0.5 * sin(TIME * speed), 1.0); }\n"
                 "vec4 hook() { pvj_norm = vec2(HOOKED_pos.x, 1.0 - HOOKED_pos.y); pvj_main(); return pvj_color; }\n",
    "g3-notime": "vec2 pvj_norm; vec4 pvj_color;\nvoid pvj_main() { pvj_color = vec4(pvj_norm, 0.5, 1.0); }\n"
                 "vec4 hook() { pvj_norm = vec2(HOOKED_pos.x, 1.0 - HOOKED_pos.y); pvj_main(); return pvj_color; }\n",
    "g4-locals": "#define TIME (mod(float(frame), 1048576.0) / 30.0)\nconst float speed = 0.5;\n"
                 "vec4 pvj_main(vec2 n) { return vec4(n, 0.5 + 0.5 * sin(TIME * speed), 1.0); }\n"
                 "vec4 hook() { return pvj_main(vec2(HOOKED_pos.x, 1.0 - HOOKED_pos.y)); }\n",
    "g5-constonly": "const float speed = 0.5;\nvec4 hook() { return vec4(HOOKED_pos.x, 1.0 - HOOKED_pos.y, speed, 1.0); }\n",
    "g6-define-only": "#define TIME (mod(float(frame), 1048576.0) / 30.0)\nvec4 hook() { return vec4(HOOKED_pos.x, 1.0 - HOOKED_pos.y, 0.5 + 0.5 * sin(TIME), 1.0); }\n",
    "g7-global-in-hook": "vec4 pvj_color;\nvec4 hook() { pvj_color = vec4(HOOKED_pos.x, 1.0 - HOOKED_pos.y, 0.5, 1.0); return pvj_color; }\n",
    "g8-inout": "void pvj_main(vec2 pvj_norm, inout vec4 pvj_color) { pvj_color = vec4(pvj_norm, 0.5, 1.0); }\n"
                "vec4 hook() { vec4 c = vec4(0.0, 0.0, 0.0, 1.0); pvj_main(vec2(HOOKED_pos.x, 1.0 - HOOKED_pos.y), c); return c; }\n",
    "g9-error": "#line 100\nvec4 hook() {\n    float x = 1;\n    return vec4(nonsense, 1.0);\n}\n",
    "g10-after-error": "vec4 hook() { return vec4(HOOKED_pos.x, HOOKED_pos.y, 0.25, 1.0); }\n",
}
CASES = [(k, RGB, HEAD % k.split("-")[0] + v, None) for k, v in G.items()]

@unittest.skipUnless(GPU and shutil.which("mpv"), "set PVJ_GPU_TEST=1 under a display with mpv")
class Spike(unittest.TestCase):
    def run_mode(self, extra):
        d = tempfile.mkdtemp()
        os.chmod(d, 0o700)
        p = Player(extra_args=["--vo=gpu", "--ao=null", "--geometry=%dx%d" % (W, H), "--no-border"] + extra, rundir=d)
        self.addCleanup(p.stop)
        try:
            p.play(["av://lavfi:color=c=black:size=%dx%d:rate=30" % (W, H)], windowed=True)
        except PlayerError as e:
            print("SPIKE %s: player failed: %s" % (extra, e))
            return
        tap = Tap(p.socket_path, "v")
        time.sleep(1.0)
        for name in ("mpv-version", "current-vo", "current-gpu-context", "osd-width", "osd-height"):
            try:
                print("SPIKE %s %s = %r" % (extra, name, p.ipc.request("get_property", name)))
            except PlayerError as e:
                print("SPIKE %s %s failed %s" % (extra, name, e))
        for pre, lvl, text in tap.drain(0.5):
            if "GL_" in text or "GLSL" in text or lvl in ("error", "fatal", "warn"):
                print("SPIKE init log [%s] %s: %s" % (pre, lvl, text[:200]))
        p.ipc.request("set_property", "screenshot-format", "png")
        p.ipc.request("set_property", "screenshot-high-bit-depth", False)
        for name, carrier, text, second in CASES:
            paths = []
            for i, t in enumerate([text, second]):
                if t:
                    path = os.path.join(d, "%s-%d.glsl" % (name, i))
                    with open(path, "w") as f:
                        f.write(t)
                    paths.append(path)
            tap.drain(0.05)
            p.ipc.request("set_property", "brightness", 0)
            p.ipc.request("set_property", "glsl-shaders", paths)
            p.ipc.request("loadfile", carrier, "replace")
            time.sleep(1.2)
            logs = tap.drain(0.3)
            errs = [(pre, lvl, t) for pre, lvl, t in logs if lvl in ("error", "fatal", "warn") or "user shader" in t.lower() or "hook" in t.lower()]
            print("SPIKE %s case %s: %d error/warn lines" % (extra, name, len(errs)))
            for pre, lvl, t in errs[:6] + errs[-14:]:
                print("SPIKE    [%s] %s: %s" % (pre, lvl, t[:220]))
            try:
                passes = p.ipc.request("get_property", "vo-passes")
                print("SPIKE    vo-passes fresh: %s" % [(x.get("desc"), x.get("avg")) for x in (passes or {}).get("fresh", [])])
            except PlayerError as e:
                print("SPIKE    vo-passes failed: %s" % e)
            for b in (0, -100):
                p.ipc.request("set_property", "brightness", b)
                time.sleep(0.3)
                shot = os.path.join(d, "shot.png")
                try:
                    os.unlink(shot)
                except OSError:
                    pass
                try:
                    p.ipc.request("screenshot-to-file", shot, "window")
                    w, h, rows = png_rows(shot)
                    pts = {"tl": rows[2][2], "tr": rows[2][w - 3], "bl": rows[h - 3][2], "br": rows[h - 3][w - 3], "mid": rows[h // 2][w // 2]}
                    flips = sum(1 for x in range(1, w) if (rows[h // 2][x][0] > 127) != (rows[h // 2][x - 1][0] > 127))
                    print("SPIKE    brightness %d: %dx%d %s red-flips-across-middle=%d" % (b, w, h, pts, flips))
                except Exception as e:
                    print("SPIKE    brightness %d: screenshot failed: %r" % (b, e))
            if name == "frame-uniform":
                time.sleep(0.2)
                p.ipc.request("set_property", "brightness", 0)
                time.sleep(0.2)
                p.ipc.request("screenshot-to-file", shot, "window")
                print("SPIKE    frame later: mid %s" % (png_rows(shot)[2][H // 2][W // 2],))
        p.stop()

    def test_gles(self):
        self.run_mode(["--gpu-context=x11egl", "--opengl-es=yes"])

    def test_desktop_gl(self):
        self.run_mode(["--gpu-context=x11egl", "--opengl-es=no"])


if __name__ == "__main__":
    unittest.main()
