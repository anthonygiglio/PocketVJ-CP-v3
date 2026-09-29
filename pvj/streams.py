# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Saved network streams (SRT, RTSP, RTMP) that mpv can play like a clip.

Only these URL schemes are accepted. mpv understands many more (`file://`, `edl://`, `lavf://`,
`ytdl://`, ...), some of which read local files or run helper programs; a stream URL is never
passed on unless its scheme is in the list below. A URL may carry a user name and password
(`rtsp://user:pass@camera/stream`); it is stored in settings (readable only by the service
account) and hidden everywhere it is shown, including the player status.
"""

import re
import uuid
from urllib.parse import urlsplit

SCHEMES = ("srt", "rtsp", "rtsps", "rtmp", "rtmps")
MAX_STREAMS = 24
MAX_URL = 500
_ID = re.compile(r"^[0-9a-f]{8}$")
_HOST = re.compile(r"^[A-Za-z0-9._-]{1,253}$|^[0-9A-Fa-f:.]{2,45}$")


class StreamError(Exception):
    pass


def valid_url(url):
    """The URL if it is an acceptable stream address, else raises StreamError."""
    if not isinstance(url, str) or not 1 <= len(url) <= MAX_URL:
        raise StreamError("enter the stream address")
    if re.search(r"[\x00-\x20\x7f-\x9f\\]", url):
        raise StreamError("the address may not contain spaces or control characters")
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise StreamError("that is not a valid address")
    if parts.scheme.lower() not in SCHEMES:
        raise StreamError("the address must start with %s" % ", ".join(s + "://" for s in SCHEMES))
    if not host or not _HOST.match(host):
        raise StreamError("the address needs a host name or IP address")
    if port is not None and not 1 <= port <= 65535:
        raise StreamError("bad port number")
    return url


def clean_name(name):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or re.search(r"[\x00-\x1f\x7f]", name):
        raise StreamError("give the stream a name of up to 40 characters")
    return name.strip()


def new_entry(name, url):
    return {"id": uuid.uuid4().hex[:8], "name": clean_name(name), "url": valid_url(url)}


def redact(url):
    """The URL with any user name and password replaced by ***."""
    if not isinstance(url, str) or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    authority, sep, tail = rest.partition("/")
    if "@" in authority:
        authority = "***@" + authority.rsplit("@", 1)[1]
    return scheme + "://" + authority + sep + tail


def validate_saved(items):
    """Check the stored list (settings are user-editable files)."""
    if not isinstance(items, list) or len(items) > MAX_STREAMS:
        raise StreamError("streams must be a list of at most %d" % MAX_STREAMS)
    seen = set()
    for it in items:
        if not isinstance(it, dict) or not _ID.match(str(it.get("id", ""))) or it["id"] in seen:
            raise StreamError("bad stream entry")
        seen.add(it["id"])
        clean_name(it.get("name"))
        valid_url(it.get("url"))
    return items
