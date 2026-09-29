# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from pvj import update

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "tools", "make-release.sh")


def version():
    with open(os.path.join(REPO, "pvj", "__init__.py")) as f:
        return re.search(r'^__version__ = "([^"]+)"', f.read(), re.M).group(1)


def build(*args):
    return subprocess.run([SCRIPT, *args], cwd=REPO, capture_output=True, text=True)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.out = os.path.join(REPO, "dist", "pvj-%s.tar.gz" % version())
        self.addCleanup(shutil.rmtree, os.path.join(REPO, "dist"), True)

    def test_bundle_is_valid_reproducible_and_installable_by_the_updater(self):
        r = build(version())
        self.assertEqual(r.returncode, 0, r.stderr)
        first = update.sha256_file(self.out)
        build(version())
        self.assertEqual(update.sha256_file(self.out), first, "same commit must give identical bytes")
        with open(self.out + ".sha256") as f:
            update.verify_sha256(self.out, f.read())
        work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, work, True)
        update.safe_extract(self.out, work)
        self.assertEqual(update.inspect_bundle(work)["version"], version())
        self.assertFalse(os.path.exists(os.path.join(work, "backend.php")), "legacy code must not ship")
        self.assertFalse(os.path.exists(os.path.join(work, "sync")))

    def test_wrong_or_missing_version_refused(self):
        self.assertNotEqual(build("9.9.9").returncode, 0)
        self.assertNotEqual(build("nonsense").returncode, 0)
        self.assertNotEqual(build().returncode, 0)

    @unittest.skipUnless(shutil.which("ssh-keygen"), "ssh-keygen not installed")
    def test_signing_round_trip(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        key = os.path.join(d, "k")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", key], check=True)
        with open(key + ".pub") as f:
            fields = f.read().split()
        allowed = os.path.join(d, "allowed")
        with open(allowed, "w") as f:
            f.write('pvj-release namespaces="pvj-release" %s %s\n' % (fields[0], fields[1]))
        r = build(version(), "--key", key)
        self.assertEqual(r.returncode, 0, r.stderr)
        update.verify_signature(self.out, self.out + ".sig", allowed)


if __name__ == "__main__":
    unittest.main()
