# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
"""Runs the real mpv headless (null video and audio). Skipped if mpv is missing."""
import os
import shutil
import tempfile
import time
import unittest

from pvj.player import Player, PlayerError, expand_media

HEADLESS = ["--vo=null", "--ao=null"]
SRC = "av://lavfi:testsrc=size=160x120:rate=25"
SRC2 = "av://lavfi:smptebars=size=160x120:rate=25"


@unittest.skipUnless(shutil.which("mpv"), "mpv not installed")
class PlayerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.player = Player(extra_args=HEADLESS, rundir=self.dir)
        self.addCleanup(self.player.stop)

    def test_play_status_and_stop(self):
        self.assertFalse(self.player.status()["running"])
        self.player.play([SRC])
        st = self.player.status()
        self.assertTrue(st["running"])
        self.assertEqual(st["path"], SRC)
        self.player.stop()
        self.assertFalse(self.player.status()["running"])
        self.assertFalse(os.path.exists(self.player.socket_path))

    def test_clip_change_keeps_same_process(self):
        self.player.play([SRC])
        pid1 = self.player.ipc.request("get_property", "pid")
        self.player.play([SRC2])
        pid2 = self.player.ipc.request("get_property", "pid")
        self.assertEqual(pid1, pid2)
        self.assertEqual(self.player.status()["path"], SRC2)

    def test_pause_speed_volume(self):
        self.player.play([SRC])
        self.assertTrue(self.player.pause())
        self.assertTrue(self.player.status()["paused"])
        self.assertFalse(self.player.pause(False))
        self.player.speed(2)
        self.assertEqual(self.player.status()["speed"], 2.0)
        self.player.speed(99)
        self.assertEqual(self.player.status()["speed"], 4.0)
        self.player.volume(50)
        self.assertEqual(self.player.status()["volume"], 50.0)

    def test_picture_controls(self):
        self.player.play([SRC])
        self.player.opacity(255)
        self.assertEqual(self.player.ipc.request("get_property", "brightness"), 0)
        self.player.opacity(0)
        self.assertEqual(self.player.ipc.request("get_property", "brightness"), -100)
        self.player.size(200)
        self.assertAlmostEqual(self.player.ipc.request("get_property", "video-zoom"), 1.0)
        self.player.position(500, -250)
        self.assertAlmostEqual(self.player.ipc.request("get_property", "video-pan-x"), 0.5)
        self.assertAlmostEqual(self.player.ipc.request("get_property", "video-pan-y"), -0.25)

    def test_mute_rotate_loop(self):
        self.player.play([SRC])
        self.player.mute(True)
        self.assertTrue(self.player.status()["muted"])
        self.player.rotate(90)
        self.assertEqual(self.player.ipc.request("get_property", "video-rotate"), 90)
        with self.assertRaises(PlayerError):
            self.player.rotate(45)
        self.player.loop(False)
        self.assertEqual(self.player.ipc.request("get_property", "loop-file"), False)
        self.player.loop(True)
        self.assertEqual(self.player.ipc.request("get_property", "loop-file"), "inf")

    def test_loop_flags(self):
        self.player.play([SRC], loop=True)
        self.assertEqual(self.player.ipc.request("get_property", "loop-file"), "inf")
        self.player.play([SRC], loop=False)
        self.assertEqual(self.player.ipc.request("get_property", "loop-file"), False)

    def test_playlist_from_folder_and_option_like_names(self):
        media = tempfile.mkdtemp()
        for name in ("b.mp4", "a.mp4", "-evil.mp4", "notes.txt", ".hidden.mp4"):
            open(os.path.join(media, name), "w").close()
        files = [os.path.basename(f) for f in expand_media([media])]
        self.assertEqual(files, ["-evil.mp4", "a.mp4", "b.mp4"])

    def test_errors_when_not_running(self):
        with self.assertRaises(PlayerError):
            self.player.seek(5)
        with self.assertRaises(PlayerError):
            self.player.play([tempfile.mkdtemp()])  # empty folder

    def test_missing_mpv_binary(self):
        p = Player(mpv_bin="/nonexistent/mpv", rundir=self.dir)
        with self.assertRaises(PlayerError):
            p.play([SRC])

    def test_unsafe_runtime_dir_rejected(self):
        from pvj import player
        d = tempfile.mkdtemp()
        os.chmod(d, 0o777)
        os.environ["PVJ_RUNTIME_DIR"] = d
        self.addCleanup(os.environ.pop, "PVJ_RUNTIME_DIR")
        with self.assertRaises(PlayerError):
            player.runtime_dir()


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("mpv"), "mpv not installed")
class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        import subprocess
        import sys
        env = dict(os.environ, PVJ_RUNTIME_DIR=self.dir)
        return subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "bin", "pvj-player"),
                               "--mpv-arg=--vo=null", "--mpv-arg=--ao=null", *args],
                              capture_output=True, text=True, env=env, timeout=30)

    def test_cli_round_trip(self):
        import json
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o700)
        self.addCleanup(self.run_cli, "stop")
        self.assertEqual(json.loads(self.run_cli("status").stdout), {"running": False})
        r = self.run_cli("play", SRC)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.run_cli("speed", "1.5")
        self.run_cli("pause", "on")
        st = json.loads(self.run_cli("status").stdout)
        self.assertEqual((st["speed"], st["paused"]), (1.5, True))
        self.assertEqual(self.run_cli("stop").returncode, 0)
        self.assertEqual(self.run_cli("seek", "5").returncode, 1)

    def test_info_and_selftest(self):
        import json
        import subprocess
        import sys
        self.dir = tempfile.mkdtemp()
        info = json.loads(self.run_cli("info").stdout)
        self.assertIn("board", info)
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "bin", "pvj-selftest")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(all(c["ok"] for c in json.loads(r.stdout)["checks"]))


@unittest.skipUnless(shutil.which("mpv"), "mpv not installed")
class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.chmod(self.dir, 0o770)
        self.env = dict(os.environ, PVJ_RUNTIME_DIR=self.dir)
        self.cli = [__import__("sys").executable,
                    os.path.join(os.path.dirname(__file__), "..", "bin", "pvj-player"),
                    "--mpv-arg=--vo=null", "--mpv-arg=--ao=null"]

    def run_cli(self, *args):
        import subprocess
        return subprocess.run(self.cli + list(args), capture_output=True, text=True, env=self.env, timeout=30)

    def test_no_spawn_requires_service(self):
        r = self.run_cli("play", "--no-spawn", SRC)
        self.assertEqual(r.returncode, 1)
        self.assertIn("service is not running", r.stderr)

    def test_serve_then_control_without_spawning(self):
        import json
        import subprocess
        svc = subprocess.Popen(self.cli + ["serve"], env=self.env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
        self.addCleanup(svc.wait)
        self.addCleanup(svc.kill)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not json.loads(self.run_cli("status").stdout)["running"]:
            if svc.poll() is not None:
                self.fail("service exited")
            time.sleep(0.2)
        r = self.run_cli("play", "--no-spawn", SRC)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.run_cli("status").stdout)["path"], SRC)
        # the service is the process we started, not a child spawned by the CLI
        self.assertIsNone(svc.poll())

    def test_group_accessible_dir_ok_world_accessible_rejected(self):
        from pvj import player
        os.environ["PVJ_RUNTIME_DIR"] = self.dir
        self.addCleanup(os.environ.pop, "PVJ_RUNTIME_DIR")
        self.assertEqual(player.runtime_dir(), self.dir)
        os.chmod(self.dir, 0o775)
        with self.assertRaises(PlayerError):
            player.runtime_dir()
