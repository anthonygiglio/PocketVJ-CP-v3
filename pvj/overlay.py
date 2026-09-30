# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""A picture over the video: a logo, a watermark or a mask (the old panel's overlay.png).

The picture is a PNG from the media folder, with transparency. mpv itself turns it into raw premultiplied BGRA at the
size of the screen (fitted and centred, the rest transparent), once, when it is switched on; the player then draws
that bitmap over the video and the on-screen text. Decoding and scaling a full-screen PNG in Python would take many
seconds on a Pi; mpv's own decoder does it in about a second. Nothing needs installing.

Only a plain file name from the media folder reaches the converter, as an argument list (never a shell), with
`--no-config` so no user configuration or scripts are loaded.
"""

import os
import subprocess

OVERLAY_ID = 10
MAX_SIDE = 4096


class OverlayError(Exception):
    pass


def convert(png_path, width, height, out_path, mpv_bin="mpv", timeout=30):
    """Write `png_path` as width x height premultiplied BGRA to `out_path`. Raises OverlayError."""
    if not (isinstance(width, int) and isinstance(height, int) and 16 <= width <= MAX_SIDE and 16 <= height <= MAX_SIDE):
        raise OverlayError("screen size out of range")
    size = "%d:%d" % (width, height)
    graph = ("lavfi=[scale=%s:force_original_aspect_ratio=decrease,pad=%s:(ow-iw)/2:(oh-ih)/2:color=black@0,"
             "format=rgba,premultiply=inplace=1,format=bgra]" % (size, size))
    tmp = out_path + ".tmp"
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    args = [mpv_bin, "--no-config", "--really-quiet", "--no-audio", "--frames=1", "--of=rawvideo", "--ovc=rawvideo",
            "--vf=" + graph, "--o=" + tmp, "--", png_path]
    try:
        r = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OverlayError("could not convert the picture: %s" % e)
    try:
        got = os.stat(tmp).st_size
    except FileNotFoundError:
        raise OverlayError("the picture could not be read (%s)" % (r.stderr.decode(errors="replace").strip()[-120:] or "no output"))
    if got != width * height * 4:
        os.unlink(tmp)
        raise OverlayError("the picture came out the wrong size")
    os.chmod(tmp, 0o640)
    os.replace(tmp, out_path)
    return out_path
