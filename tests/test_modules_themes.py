# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
import json
import os
import tempfile
import unittest

from pvj import modules, themes
from pvj.settings import Settings


def registry(board="pi5"):
    d = tempfile.mkdtemp()
    s = Settings(os.path.join(d, "s.json"))
    s.load()
    return modules.Registry(s, board), s


class ManifestTest(unittest.TestCase):
    def test_shipped_manifests_are_valid_and_cover_requested_inputs(self):
        m = modules.load_manifests()
        for needed in ("core", "inputs-ndi", "inputs-srt", "inputs-audio-ip", "inputs-st2110", "mapper"):
            self.assertIn(needed, m)
        self.assertEqual(m["inputs-st2110"]["boards"], ["x86"])
        self.assertEqual(m["piwall"]["boards"], ["pi3"])

    def test_validation_reports_problems(self):
        good = modules.load_manifests()["mapper"]
        self.assertEqual(modules.validate_manifest(good), [])
        for key, bad in (("id", "Bad Id"), ("version", "1.x"), ("type", "weird"), ("boards", []),
                         ("channel", "gold"), ("requires", "core")):
            self.assertTrue(modules.validate_manifest(dict(good, **{key: bad})), key)
        self.assertTrue(modules.validate_manifest("nope"))

    def test_bad_directory_contents_fail_loudly(self):
        d = tempfile.mkdtemp()
        base = modules.load_manifests()["core"]
        with open(os.path.join(d, "a.json"), "w") as f:
            json.dump(dict(base, requires=["ghost"]), f)
        with self.assertRaises(modules.ModuleError):
            modules.load_manifests(d)
        with open(os.path.join(d, "a.json"), "w") as f:
            f.write("{")
        with self.assertRaises(modules.ModuleError):
            modules.load_manifests(d)


class RegistryTest(unittest.TestCase):
    def test_core_is_locked_on(self):
        reg, _ = registry()
        self.assertTrue(reg.enabled("core"))
        with self.assertRaises(modules.ModuleError):
            reg.set_enabled("core", False)
        self.assertTrue(next(m for m in reg.list() if m["id"] == "core")["locked"])

    def test_planned_modules_cannot_be_enabled_yet(self):
        reg, _ = registry()
        with self.assertRaises(modules.ModuleError):
            reg.set_enabled("inputs-ndi", True)
        self.assertFalse(reg.enabled("inputs-ndi"))

    def test_board_support_and_dependencies_enforced(self):
        reg, _ = registry("pi3")
        rows = {m["id"]: m for m in reg.list()}
        self.assertFalse(rows["inputs-st2110"]["supported"])
        self.assertTrue(rows["piwall"]["supported"])
        # a ready, supported module with an unmet dependency: fake manifests
        manifests = modules.load_manifests()
        for k in ("mapper", "layout-import"):
            manifests[k] = dict(manifests[k], status="ready", boards=["pi5"])
        d = tempfile.mkdtemp()
        s = Settings(os.path.join(d, "s.json"))
        s.load()
        reg = modules.Registry(s, "pi5", manifests)
        with self.assertRaises(modules.ModuleError):
            reg.set_enabled("layout-import", True)  # needs mapper first
        reg.set_enabled("mapper", True)
        reg.set_enabled("layout-import", True)
        with self.assertRaises(modules.ModuleError):
            reg.set_enabled("mapper", False)  # layout-import depends on it
        reg.set_enabled("layout-import", False)
        reg.set_enabled("mapper", False)
        self.assertFalse(reg.enabled("mapper"))

    def test_switches_persist_and_unknown_or_bad_values_rejected(self):
        manifests = modules.load_manifests()
        manifests["presenter"] = dict(manifests["presenter"], status="ready")
        d = tempfile.mkdtemp()
        s = Settings(os.path.join(d, "s.json"))
        s.load()
        reg = modules.Registry(s, "pi4", manifests)
        reg.set_enabled("presenter", False)
        s2 = Settings(s.path)
        s2.load()
        self.assertFalse(modules.Registry(s2, "pi4", manifests).enabled("presenter"))
        for args in (("ghost", True), ("presenter", "yes")):
            with self.assertRaises(modules.ModuleError):
                reg.set_enabled(*args)


class ThemeTest(unittest.TestCase):
    def test_builtin_themes_valid_and_readable(self):
        t = themes.load_themes()
        self.assertEqual(set(t), {"dark-stage", "light", "night-red", "high-contrast"})
        for theme in t.values():
            tk = theme["tokens"]
            self.assertGreaterEqual(themes.contrast(tk["fg"], tk["bg"]), 4.5, theme["id"])
            self.assertGreaterEqual(themes.contrast(tk["on"], tk["ac"]), 4.5, theme["id"])

    def test_css_and_accent_override_with_automatic_text_colour(self):
        t = themes.load_themes()["dark-stage"]
        self.assertIn("--bg:#121214", themes.css(t))
        out = themes.css(t, "#FFFFFF")
        self.assertIn("--ac:#ffffff", out)
        self.assertIn("--on:#000000", out)
        self.assertIn("--on:#ffffff", themes.css(t, "#101010"))
        for bad in ("red", "#12345", "#gggggg", "#ffffff;}body{display:none", 5):
            with self.assertRaises(themes.ThemeError):
                themes.css(t, bad)

    def test_addon_themes_load_but_cannot_replace_builtin_or_inject(self):
        addons = tempfile.mkdtemp()
        os.makedirs(os.path.join(addons, "themes"))
        tokens = dict(themes.load_themes()["light"]["tokens"])

        def put(name, obj):
            with open(os.path.join(addons, "themes", name), "w") as f:
                f.write(obj if isinstance(obj, str) else json.dumps(obj))
        put("mine.json", {"id": "my-theme", "name": "Mine", "tokens": tokens})
        put("hijack.json", {"id": "dark-stage", "name": "Evil", "tokens": tokens})
        put("inject.json", {"id": "inject", "name": "X", "tokens": dict(tokens, bg="#000;}body{display:none")})
        put("extra.json", {"id": "extra", "name": "X", "tokens": dict(tokens, zz="#000000")})
        put("broken.json", "{")
        loaded = themes.load_themes(addons)
        self.assertEqual(loaded["my-theme"]["source"], "addon")
        self.assertEqual(loaded["dark-stage"]["name"], "Dark stage")
        for missing in ("inject", "extra"):
            self.assertNotIn(missing, loaded)


if __name__ == "__main__":
    unittest.main()
