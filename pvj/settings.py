# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Versioned settings store with automatic migration.

One JSON file holds pads, paired devices, module switches and the theme. It is
written atomically (a pulled plug never leaves half a file) with a backup of the
previous version kept alongside. When the schema changes, `load()` backs the file
up as settings.json.bak-v<old>, applies the migrations in order and saves.

A file from a NEWER version is never rewritten: the store refuses to load it, so
rolling back the program cannot silently destroy settings.
"""

import copy
import json
import os
import tempfile
import threading

SCHEMA = 5


class SettingsError(Exception):
    pass


def default_control():
    return {"dmx": {"enabled": False, "protocol": "artnet", "universe": 0, "start": 1, "allow": []},
            "midi": {"enabled": False, "device": "", "channel": 0}}


def default_settings():
    return {
        "schema": SCHEMA,
        "auth": {"pin_hash": None, "pin_salt": None},
        "devices": [],
        "pads": {"banks": [{"name": "Bank %s" % c, "pads": [{"label": "", "file": ""} for _ in range(12)]}
                           for c in "ABC"]},
        "modules": {"enabled": {}},
        "theme": {"name": "dark-stage", "accent": None},
        "mix": {"transition": "dip", "duration": 1.0},
        "osc": {"enabled": False, "port": 9876, "allow": []},
        "schedule": {"enabled": False, "entries": []},
        "streams": [],
        "control": default_control(),
    }


# version -> function that upgrades a settings dict FROM that version to the next.
def _v1_to_v2(data):
    """2: OSC settings. Off by default; only private networks may send, plus any extra ranges listed."""
    data.setdefault("osc", {"enabled": False, "port": 9876, "allow": []})


def _v2_to_v3(data):
    """3: weekly schedule. Off, and empty."""
    data.setdefault("schedule", {"enabled": False, "entries": []})


def _v3_to_v4(data):
    """4: saved network streams (SRT, RTSP, RTMP). None yet."""
    data.setdefault("streams", [])


def _v4_to_v5(data):
    """5: DMX (Art-Net, sACN) and MIDI input. Both off."""
    data.setdefault("control", default_control())


MIGRATIONS = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4, 4: _v4_to_v5}


def migrate(data, migrations=None, current=SCHEMA):
    """Upgrade `data` in place to `current`. Returns the list of versions applied."""
    migrations = MIGRATIONS if migrations is None else migrations
    version = data.get("schema", 0)
    if not isinstance(version, int) or version < 0:
        raise SettingsError("bad schema value %r" % (version,))
    if version > current:
        raise SettingsError("settings are from a newer version (schema %d, this program knows %d); "
                            "not touching them" % (version, current))
    applied = []
    while version < current:
        step = migrations.get(version)
        if step is None:
            raise SettingsError("no migration from schema %d" % version)
        step(data)
        applied.append(version)
        version += 1
        data["schema"] = version
    return applied


class Settings:
    def __init__(self, path, migrations=None, current=SCHEMA):
        self.path = path
        self._migrations = migrations
        self._current = current
        self.data = None
        # Web threads mutate and save concurrently. Holding this around a mutation AND its
        # save keeps the snapshot and the write in order, so an older snapshot can never
        # land on disk after a newer one.
        self.lock = threading.RLock()

    def load(self):
        if not os.path.exists(self.path) and not os.path.exists(self.path + ".bak"):
            self.data = default_settings()
            self.data["schema"] = self._current
            self.save()
            return self.data
        try:
            data = self._read(self.path)
        except SettingsError:
            # A cut power supply can corrupt the main file; the backup is written first.
            if not os.path.exists(self.path + ".bak"):
                raise
            data = self._read(self.path + ".bak")
            self.recovered_from_backup = True
        old = data.get("schema", 0)
        if old != self._current or getattr(self, "recovered_from_backup", False):
            original = copy.deepcopy(data)
            migrate(data, self._migrations, self._current)  # raises for newer files
            if old != self._current:
                self._write("%s.bak-v%s" % (self.path, old), original)
            self.data = data
            self.save()
        else:
            self.data = data
        return self.data

    @staticmethod
    def _read(path):
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise SettingsError("cannot read %s: %s" % (path, e))
        if not isinstance(data, dict):
            raise SettingsError("%s does not contain a settings object" % path)
        return data

    def save(self):
        if self.data is None:
            raise SettingsError("nothing loaded")
        with self.lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path) as f:
                        previous = f.read()
                    json.loads(previous)  # never overwrite a good backup with a corrupt file
                    self._write_text(self.path + ".bak", previous)
                except (OSError, ValueError):
                    pass
            self._write(self.path, self.data)

    def _write(self, path, data):
        self._write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _write_text(path, text):
        directory = os.path.dirname(path) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".settings-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
