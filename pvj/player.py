# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""pvj-player core: one mpv process controlled over its JSON IPC socket.

This replaces omxplayer plus D-Bus. A single long-lived mpv is started idle and
clips are switched with `loadfile`, so changing clips does not tear down the
window (no black gap like the old stopall plus new process).
"""

import json
import math
import os
import signal
import socket
import stat
import subprocess
import time

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg", ".ts", ".wmv")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".opus")
ENDINGS = ("loop", "stop", "next", "hold")      # what happens when a clip (or the list) ends


class PlayerError(Exception):
    pass


def runtime_dir():
    """Private per-user directory for the IPC socket and pid file."""
    base = os.environ.get("PVJ_RUNTIME_DIR")
    if not base:
        xdg = os.environ.get("XDG_RUNTIME_DIR")
        base = os.path.join(xdg, "pvj") if xdg else "/tmp/pvj-%d" % os.getuid()
    os.makedirs(base, mode=0o700, exist_ok=True)
    st = os.stat(base)
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o007:
        raise PlayerError("unsafe runtime directory %s (world accessible)" % base)
    if st.st_uid != os.getuid():
        # Shared service directory (e.g. /run/pvj, mode 0770): allowed only
        # for members of its group.
        if not (mode & 0o070 and st.st_gid in os.getgroups()):
            raise PlayerError("unsafe runtime directory %s (not yours and not in its group)" % base)
    return base


def expand_media(paths, extensions=VIDEO_EXTENSIONS + IMAGE_EXTENSIONS):
    """Expand directories into sorted media files; keep URLs and files as given."""
    out = []
    for p in paths:
        if "://" in p:
            out.append(p)
        elif os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if name.lower().endswith(extensions) and not name.startswith("."):
                    out.append(os.path.join(p, name))
        else:
            out.append(p)
    return out


def open_socket_when_ready(path, mode=0o660, timeout=15.0, poll=0.05):
    """Wait for a unix socket to appear at `path` and give it `mode`. True if it did. The caller must have
    removed any old socket first, or a stale file would be mistaken for the new one."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if stat.S_ISSOCK(os.stat(path).st_mode):
                os.chmod(path, mode)
                return True
        except FileNotFoundError:
            pass
        except OSError:
            return False
        time.sleep(poll)
    return False


def detach_socket_opener(path, mode=0o660):
    """Start open_socket_when_ready in a fully detached grandchild, so the caller can exec mpv without leaving
    a zombie: the first child exits at once and is reaped here, and init adopts the grandchild."""
    pid = os.fork()
    if pid == 0:
        try:
            if os.fork() == 0:
                os.setsid()
                os._exit(0 if open_socket_when_ready(path, mode) else 1)
        finally:
            os._exit(0)
    os.waitpid(pid, 0)


class Ipc:
    def __init__(self, path, timeout=2.0):
        self.path = path
        self.timeout = timeout
        self._id = 0

    def request(self, *command):
        self._id += 1
        rid = self._id
        payload = json.dumps({"command": list(command), "request_id": rid}).encode() + b"\n"
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.path)
            s.sendall(payload)
            buf = b""
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("request_id") == rid:
                        if msg.get("error") != "success":
                            raise PlayerError("mpv: %s" % msg.get("error"))
                        return msg.get("data")
            raise PlayerError("no reply from mpv")
        except (ConnectionRefusedError, FileNotFoundError):
            raise PlayerError("player is not running")
        except OSError as e:
            raise PlayerError("ipc error: %s" % e)
        finally:
            s.close()


class Player:
    def __init__(self, mpv_bin="mpv", extra_args=None, rundir=None):
        self.mpv_bin = mpv_bin
        self.extra_args = list(extra_args or [])
        self.rundir = rundir or runtime_dir()
        self.socket_path = os.path.join(self.rundir, "player.sock")
        self.pid_path = os.path.join(self.rundir, "player.pid")
        self.ipc = Ipc(self.socket_path)
        self._proc = None

    # --- lifecycle -------------------------------------------------------
    def is_running(self):
        try:
            self.ipc.request("get_property", "pid")
            return True
        except PlayerError:
            return False

    def mpv_command(self, audio_device=None, windowed=False):
        args = [self.mpv_bin, "--idle=yes", "--input-ipc-server=" + self.socket_path,
                "--no-terminal", "--really-quiet", "--osd-level=0", "--no-osc",
                "--keep-open=no", "--force-window=yes",
                # A file is judged by its content, not its name: a hostile drive can hold an "mp4" that is really a
                # playlist or EDL naming other files or URLs, plus sidecar subtitle and audio files. Play media only.
                # (--load-unsafe-playlists=no alone did not stop a fake .mp4 that was really a playlist: tested on a Pi 4.
                # --access-references=no does: such a file is no longer followed to other files or URLs.)
                "--load-unsafe-playlists=no", "--access-references=no", "--sub-auto=no", "--audio-file-auto=no", "--ytdl=no",
                "--load-scripts=no", "--load-auto-profiles=no"]
        if not windowed:
            args.append("--fullscreen")
        if audio_device:
            args.append("--audio-device=" + audio_device)
        args += self.extra_args
        return args

    def serve(self, audio_device=None, windowed=False):
        """Run mpv in the foreground, replacing this process. For systemd: it
        supervises mpv and restarts it, and the socket is the control channel."""
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        args = self.mpv_command(audio_device, windowed)
        # mpv creates its socket owner-only (0600) whatever the umask, so the web panel (another user, same
        # group) could not reach it. A short-lived helper waits for the NEW socket and opens it to the group.
        detach_socket_opener(self.socket_path)
        try:
            os.execvp(args[0], args)
        except FileNotFoundError:
            raise PlayerError("mpv not found; install it (sudo apt install mpv)")

    def _spawn(self, audio_device=None, windowed=False):
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        args = self.mpv_command(audio_device, windowed)
        try:
            proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
        except FileNotFoundError:
            raise PlayerError("mpv not found; install it (sudo apt install mpv)")
        self._proc = proc
        with open(self.pid_path, "w") as f:
            f.write(str(proc.pid))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise PlayerError("mpv exited immediately (code %s)" % proc.returncode)
            if self.is_running():
                return
            time.sleep(0.1)
        proc.terminate()
        raise PlayerError("mpv did not become ready")

    def play(self, paths, loop=True, audio_device=None, windowed=False, spawn=True, ending=None, image_seconds=None):
        """Play `paths` (a list plays as a playlist). `ending` says what happens at the end: "loop" (the clip, or the
        whole list), "stop" (black, player idle), "next" (a list goes on and stops after the last clip) or "hold"
        (the last frame stays on screen). Without `ending`, `loop` picks "loop" or "stop" as before.
        `image_seconds` is how long each image stays up in a list of images (a slideshow); a single image stays until
        something else is played."""
        ending = ending or ("loop" if loop else "stop")
        if ending not in ENDINGS:
            raise PlayerError("ending must be one of " + ", ".join(ENDINGS))
        files = expand_media(paths)
        if not files:
            raise PlayerError("no playable files found")
        if not self.is_running():
            if not spawn:
                raise PlayerError("player service is not running (systemctl start pvj-player)")
            self._spawn(audio_device, windowed)
        elif audio_device:
            self.ipc.request("set_property", "audio-device", audio_device)
        single = len(files) == 1
        # Set before loading: a new file picks these up as it starts.
        self.ipc.request("set_property", "keep-open", "yes" if ending == "hold" else "no")
        self.ipc.request("set_property", "image-display-duration",
                         "inf" if single or image_seconds is None else max(0.1, min(3600.0, float(image_seconds))))
        first = True
        for f in files:
            # "--" style option injection is impossible here: the path is a
            # loadfile argument, never a command line option.
            self.ipc.request("loadfile", f, "replace" if first else "append")
            first = False
        self._wait_for_path(files[0])
        self.ipc.request("set_property", "pause", False)
        looping = ending == "loop"
        self.ipc.request("set_property", "loop-file", "inf" if (looping and single) else "no")
        self.ipc.request("set_property", "loop-playlist", "inf" if (looping and not single) else "no")

    def shuffle(self):
        """Put the current playlist in a random order (the old panel's random player)."""
        self.ipc.request("playlist-shuffle")

    def _wait_for_path(self, path, timeout=3.0):
        """loadfile returns before mpv switches; wait so status() is truthful."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.ipc.request("get_property", "path") == path:
                    return
            except PlayerError:
                pass
            time.sleep(0.05)

    def stop(self):
        """Stop playback and end the mpv process."""
        try:
            self.ipc.request("quit")
        except PlayerError:
            pass
        pid = self._pid()
        if pid:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and _alive(pid):
                time.sleep(0.05)
            if _alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        if self._proc is not None:
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            self._proc = None
        for p in (self.socket_path, self.pid_path):
            try:
                os.unlink(p)
            except OSError:
                pass

    def _pid(self):
        try:
            with open(self.pid_path) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    # --- controls --------------------------------------------------------
    def _set(self, name, value):
        self.ipc.request("set_property", name, value)

    def pause(self, paused=None):
        if paused is None:
            paused = not self.ipc.request("get_property", "pause")
        self._set("pause", bool(paused))
        return bool(paused)

    def seek(self, seconds):
        self.ipc.request("seek", float(seconds), "relative")

    def seek_to(self, seconds):
        """Jump to an absolute position in the current clip."""
        self.ipc.request("seek", max(0.0, float(seconds)), "absolute")

    def playlist_step(self, forward):
        """Next or previous clip in the playlist. Returns False when there is nothing in that direction."""
        count = self.ipc.request("get_property", "playlist-count") or 0
        pos = self.ipc.request("get_property", "playlist-pos")
        if count <= 1 or pos is None or pos < 0:
            return False
        looping = self.ipc.request("get_property", "loop-playlist") not in (False, "no", None)
        if forward and pos >= count - 1 and not looping:
            return False
        if not forward and pos <= 0 and not looping:
            return False
        self.ipc.request("playlist-next" if forward else "playlist-prev", "force")
        return True

    def speed(self, factor):
        self._set("speed", min(4.0, max(0.1, float(factor))))

    def volume(self, percent):
        self._set("volume", min(130.0, max(0.0, float(percent))))

    def clear(self):
        """Stop the current clip but keep the player service and window alive."""
        self.ipc.request("stop")

    def screenshot(self, path, quality=60, with_text=True):
        """Save what the player is showing right now as a JPEG. With `with_text` it is the whole window (brightness,
        size, on-screen text and pictures such as QR codes); without, only the video, so nothing drawn on top of it
        can be read from the snapshot. Takes about 0.7 s at 2560x1440 on a Pi 4."""
        self._set("screenshot-format", "jpg")
        self._set("screenshot-jpeg-quality", int(quality))
        self.ipc.request("screenshot-to-file", path, "window" if with_text else "video")

    def osd_size(self):
        """(width, height) of the picture the player is drawing on, in pixels, or None if unknown."""
        try:
            w, h = self.ipc.request("get_property", "osd-width"), self.ipc.request("get_property", "osd-height")
        except PlayerError:
            return None
        return (int(w), int(h)) if isinstance(w, (int, float)) and isinstance(h, (int, float)) and w > 0 and h > 0 else None

    def overlay(self, oid, x, y, width, height, pixels):
        """Draw a bitmap (raw BGRA, `width` x `height`) over the picture and the on-screen text, at x, y, until
        overlay_remove(oid). The pixels go through a file in the runtime folder that the player reads."""
        if not (isinstance(oid, int) and 0 <= oid < 64) or len(pixels) != width * height * 4:
            raise PlayerError("bad overlay")
        path = os.path.join(self.rundir, "overlay-%d.bgra" % oid)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o660)
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o660)          # on the open file: a name swapped for a link cannot redirect it
            f.write(pixels)
        self.ipc.request("overlay-add", oid, int(x), int(y), path, 0, "bgra", int(width), int(height), int(width) * 4)

    def overlay_remove(self, oid):
        self.ipc.request("overlay-remove", oid)

    def overlay_file(self, oid, path, width, height):
        """Draw a ready raw BGRA file (width x height, at 0, 0) over the picture until overlay_remove(oid)."""
        self.ipc.request("overlay-add", oid, 0, 0, path, 0, "bgra", int(width), int(height), int(width) * 4)

    def flip(self, horizontal, on):
        """Mirror the picture left-right or upside down (a video filter; about half a core more on a Pi 4 at 1080p)."""
        label = "@pvjfliph" if horizontal else "@pvjflipv"
        try:
            self.ipc.request("vf", "remove", label)
        except PlayerError:
            pass
        if on:
            self.ipc.request("vf", "add", "%s:%s" % (label, "hflip" if horizontal else "vflip"))

    def volume_step(self, delta):
        self.ipc.request("add", "volume", float(delta))

    def opacity(self, value):
        """0..255 like the old panel. mpv cannot blend against other layers, so
        this fades to black (correct for a black stage background)."""
        value = min(255, max(0, int(value)))
        self._set("brightness", int(round(-100 * (1 - value / 255.0))))

    def size(self, percent):
        """Scale the picture around the centre; 100 is fit-to-window."""
        self._set("video-zoom", math.log2(max(1, min(400, float(percent))) / 100.0))

    def position(self, x, y=0):
        """Shift the picture; units are thousandths of the picture width and height (y positive moves it down)."""
        self._set("video-pan-x", max(-3000, min(3000, float(x))) / 1000.0)
        self._set("video-pan-y", max(-3000, min(3000, float(y))) / 1000.0)

    def loop(self, enabled):
        """Loop the current clip (or the whole playlist when there are several)."""
        count = self.ipc.request("get_property", "playlist-count") or 0
        self._set("loop-file", "inf" if (enabled and count <= 1) else "no")
        self._set("loop-playlist", "inf" if (enabled and count > 1) else "no")

    def mute(self, muted):
        self._set("mute", bool(muted))

    def rotate(self, degrees):
        if degrees not in (0, 90, 180, 270):
            raise PlayerError("rotation must be 0, 90, 180 or 270")
        self._set("video-rotate", int(degrees))

    TEST_PATTERN = "av://lavfi:smptehdbars=size=1920x1080:rate=25"
    # 5 s of 440 Hz, on the left, the right or both channels (the old Settings tab's test tones)
    TEST_TONES = {"left": "av://lavfi:aevalsrc=0.5*sin(440*2*PI*t)|0:s=48000:d=5",
                  "right": "av://lavfi:aevalsrc=0|0.5*sin(440*2*PI*t):s=48000:d=5",
                  "both": "av://lavfi:aevalsrc=0.5*sin(440*2*PI*t)|0.5*sin(440*2*PI*t):s=48000:d=5"}

    def status(self):
        if not self.is_running():
            return {"running": False}
        out = {"running": True}
        for key, prop in (("path", "path"), ("position", "time-pos"), ("duration", "duration"),
                          ("paused", "pause"), ("speed", "speed"), ("volume", "volume"), ("muted", "mute"), ("loop_file", "loop-file"), ("loop_playlist", "loop-playlist"),
                          ("playlist_pos", "playlist-pos"), ("playlist_count", "playlist-count")):
            try:
                out[key] = self.ipc.request("get_property", prop)
            except PlayerError:
                out[key] = None
        return out


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(") ", 1)[1][0] != "Z"
    except (OSError, IndexError):
        return True
