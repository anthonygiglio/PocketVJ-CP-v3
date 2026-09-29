import json
import os
import stat
import tempfile
import unittest

from pvj import settings
from pvj.settings import Settings, SettingsError


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "settings.json")

    def text(self):
        with open(self.path) as f:
            return f.read()

    def read(self, path=None):
        with open(path or self.path) as f:
            return json.load(f)

    def test_first_run_writes_defaults_private(self):
        s = Settings(self.path)
        data = s.load()
        self.assertEqual(data["schema"], settings.SCHEMA)
        self.assertEqual(len(data["pads"]["banks"]), 3)
        self.assertEqual(len(data["pads"]["banks"][0]["pads"]), 12)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_save_and_reload_keeps_backup(self):
        s = Settings(self.path)
        s.load()
        s.data["theme"]["name"] = "night-red"
        s.save()
        self.assertEqual(self.read()["theme"]["name"], "night-red")
        self.assertEqual(self.read(self.path + ".bak")["theme"]["name"], "dark-stage")
        self.assertEqual(Settings(self.path).load()["theme"]["name"], "night-red")

    def test_no_temp_files_left_behind(self):
        s = Settings(self.path)
        s.load()
        self.assertEqual(sorted(os.listdir(self.dir)), ["settings.json"])
        s.save()
        self.assertEqual(sorted(os.listdir(self.dir)), ["settings.json", "settings.json.bak"])

    def test_migration_runs_in_order_and_backs_up_original(self):
        def v0_to_v1(d):
            d["pads"] = {"banks": [{"name": "A", "pads": [{"label": f, "file": f} for f in d.pop("files")]}]}

        def v1_to_v2(d):
            d["theme"] = {"name": d.pop("skin")}

        with open(self.path, "w") as f:
            json.dump({"files": ["a.mp4", "b.mp4"], "skin": "light"}, f)
        s = Settings(self.path, migrations={0: v0_to_v1, 1: v1_to_v2}, current=2)
        data = s.load()
        self.assertEqual(data["schema"], 2)
        self.assertEqual(data["theme"], {"name": "light"})
        self.assertEqual(data["pads"]["banks"][0]["pads"][1]["file"], "b.mp4")
        original = self.read(self.path + ".bak-v0")
        self.assertEqual(original["files"], ["a.mp4", "b.mp4"])
        self.assertEqual(self.read()["schema"], 2)

    def test_newer_file_is_never_rewritten(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 99, "precious": True}, f)
        before = self.text()
        with self.assertRaises(SettingsError):
            Settings(self.path).load()
        self.assertEqual(self.text(), before)
        self.assertEqual(os.listdir(self.dir), ["settings.json"])

    def test_missing_migration_and_bad_files_are_errors(self):
        with open(self.path, "w") as f:
            json.dump({"schema": 0}, f)
        with self.assertRaises(SettingsError):
            Settings(self.path, migrations={}, current=1).load()
        for bad in ("not json", "[1, 2]", json.dumps({"schema": "x"})):
            with open(self.path, "w") as f:
                f.write(bad)
            with self.assertRaises(SettingsError, msg=bad):
                Settings(self.path).load()

    def test_corrupt_main_file_recovers_from_backup(self):
        s = Settings(self.path)
        s.load()
        s.data["theme"]["name"] = "light"
        s.save()
        s.data["theme"]["name"] = "night-red"
        s.save()  # main = night-red, .bak = light
        with open(self.path, "w") as f:
            f.write('{"schema": 1, "theme": {"na')  # torn write
        s2 = Settings(self.path)
        self.assertEqual(s2.load()["theme"]["name"], "light")
        self.assertTrue(s2.recovered_from_backup)
        self.assertEqual(self.read()["theme"]["name"], "light")

    def test_missing_main_with_backup_recovers_and_corrupt_main_never_replaces_good_backup(self):
        s = Settings(self.path)
        s.load()
        s.data["theme"]["name"] = "light"
        s.save()
        os.unlink(self.path)
        self.assertEqual(Settings(self.path).load()["theme"]["name"], "dark-stage")
        with open(self.path, "w") as f:
            f.write("garbage")
        good_bak = self.read(self.path + ".bak")
        s3 = Settings(self.path)
        s3.data = settings.default_settings()
        s3.save()
        self.assertEqual(self.read(self.path + ".bak"), good_bak)

    def test_failed_migration_leaves_original_untouched(self):
        def boom(d):
            raise RuntimeError("bug")

        with open(self.path, "w") as f:
            json.dump({"x": 1}, f)
        before = self.text()
        with self.assertRaises(RuntimeError):
            Settings(self.path, migrations={0: boom}, current=1).load()
        self.assertEqual(self.text(), before)


if __name__ == "__main__":
    unittest.main()
