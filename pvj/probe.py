# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""What is in a clip (codec, size, frame rate, length, sound), without playing it on the screen.

Replaces the old panel's "Movie Codec" and "Movie Resolution" buttons (mediainfo). mpv, which is already on the box,
opens the file with no video or audio output, decodes one frame and prints its properties; about half a second for a
clip on a Pi 4. Results are kept per file (path, size, modification time), so asking again is free.
"""

import os
import subprocess
import threading

MARK = "PVJINFO|"
FIELDS = ("codec", "width", "height", "fps", "duration", "audio", "container")
_TEMPLATE = MARK + "|".join(("${video-format}", "${width}", "${height}", "${=container-fps}", "${=duration}",
                              "${audio-codec-name}", "${file-format}"))
_cache = {}
_lock = threading.Lock()


class ProbeError(Exception):
    pass


def _number(text, kind):
    try:
        v = kind(text)
    except (TypeError, ValueError):
        return None
    return v if v == v and v >= 0 else None


def parse(line):
    """The fields of one PVJINFO line; missing values become None."""
    parts = line[len(MARK):].split("|")
    if len(parts) != len(FIELDS):
        raise ProbeError("unexpected answer from the player")
    raw = dict(zip(FIELDS, [None if p in ("", "(unavailable)") else p for p in parts]))
    fps = _number(raw["fps"], float)
    return {"codec": raw["codec"], "width": _number(raw["width"], int), "height": _number(raw["height"], int),
            "fps": round(fps, 3) if fps else None, "duration": round(_number(raw["duration"], float) or 0, 2) or None,
            "audio": raw["audio"], "container": raw["container"]}


def probe(path, mpv_bin="mpv", timeout=20):
    """A dict of FIELDS for the file at `path`. Raises ProbeError."""
    try:
        st = os.stat(path)
    except OSError:
        raise ProbeError("file not found")
    key = (path, st.st_size, int(st.st_mtime))
    with _lock:
        if key in _cache:
            return dict(_cache[key])
    args = [mpv_bin, "--no-config", "--vo=null", "--ao=null", "--frames=1", "--load-unsafe-playlists=no",
            "--access-references=no", "--ytdl=no", "--load-scripts=no", "--term-playing-msg=" + _TEMPLATE, "--", path]
    try:
        r = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                           env=dict(os.environ, HOME="/nonexistent"))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProbeError("could not read the file: %s" % e)
    lines = [l for l in r.stdout.splitlines() if l.startswith(MARK)]
    if not lines:
        raise ProbeError("the player could not open this file")
    info = parse(lines[0])
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = info
    return dict(info)
