# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""Every file of the new code carries its licence; the licence text is verbatim."""
import fnmatch
import hashlib
import os
import re
import subprocess
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
NEW_DIRS = ("pvj/", "bin/", "install/", "image/", "tests/", "tools/")
NEW_FILES = {"security.php", ".github/workflows/ci.yml", ".github/workflows/pvj.yml",
             ".github/workflows/image.yml", ".github/workflows/security-tests.yml"}
APACHE_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    return out.split()


def covered_paths():
    """Path patterns listed in REUSE.toml annotations."""
    with open(os.path.join(REPO, "REUSE.toml")) as f:
        text = f.read()
    block = re.search(r"path = \[(.*?)\]", text, re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


class LicenseTest(unittest.TestCase):
    def test_licence_text_is_the_official_apache_2_0(self):
        with open(os.path.join(REPO, "LICENSES", "Apache-2.0.txt"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), APACHE_SHA256)

    def test_every_new_file_is_licensed(self):
        patterns = covered_paths()
        missing = []
        for path in tracked():
            if not (path.startswith(NEW_DIRS) or path in NEW_FILES):
                continue
            if any(fnmatch.fnmatch(path, p.replace("**", "*")) for p in patterns):
                continue
            with open(os.path.join(REPO, path), errors="replace") as f:
                head = f.read(600)
            if "SPDX-License-Identifier: Apache-2.0" not in head:
                missing.append(path)
        self.assertEqual(missing, [], "add an SPDX header, or list the file in REUSE.toml")

    def test_no_compiled_or_cache_files_are_tracked(self):
        junk = [p for p in tracked() if "__pycache__" in p or p.endswith((".pyc", ".DS_Store"))
                and not p.startswith("sync/")]
        self.assertEqual(junk, [], "these would also be packed into release bundles")

    def test_legacy_files_are_not_relabelled(self):
        for path in ("backend.php", "index.html", "submit_opacity.php", "LICENSE.md"):
            with open(os.path.join(REPO, path), errors="replace") as f:
                self.assertNotIn("Apache-2.0", f.read(), path + " is upstream code")


if __name__ == "__main__":
    unittest.main()
