"""Safe updates and rollback.

A release is a signed tar.gz bundle. `apply` checks it (SHA-256, an OpenSSH
Ed25519 signature made with `ssh-keygen -Y sign`, safe extraction, version,
schema, disk space), backs up settings, installs it with install/install.sh
(new folder under /opt/pvj/releases, atomic switch of /opt/pvj/current),
restarts the services and checks that the panel answers. If it does not,
the previous release and the settings backup are put back automatically.

`rollback` does the same by hand. Settings migrations only go forward, so a
rollback restores the settings backup taken before the update; changes made
since then are set aside, not deleted.
"""

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request

PRINCIPAL = "pvj-release"
NAMESPACE = "pvj-release"
MAX_BUNDLE_BYTES = 300 * 1024 * 1024
MAX_FILES = 5000
KEEP_BACKUPS = 5
_VERSION = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")


class UpdateError(Exception):
    pass


def parse_version(text):
    m = _VERSION.match(text or "")
    if not m:
        raise UpdateError("bad version %r (expected N.N.N)" % (text,))
    return tuple(int(x) for x in m.groups())


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path, expected):
    expected = (expected or "").strip().split()[0].lower() if expected else ""
    if not re.match(r"^[0-9a-f]{64}$", expected):
        raise UpdateError("no valid SHA-256 given")
    if sha256_file(path) != expected:
        raise UpdateError("checksum does not match; the file is damaged or was changed")


def verify_signature(path, sig_path, allowed_signers, run=subprocess.run):
    try:
        with open(allowed_signers) as f:
            trusted = "".join(line for line in f if line.strip() and not line.lstrip().startswith("#"))
    except OSError:
        trusted = ""
    if not trusted:
        raise UpdateError("no trusted signing key installed (%s is missing or empty)" % allowed_signers)
    if not os.path.isfile(sig_path):
        raise UpdateError("signature file %s is missing" % sig_path)
    with open(path, "rb") as data:
        r = run(["ssh-keygen", "-Y", "verify", "-f", allowed_signers, "-I", PRINCIPAL, "-n", NAMESPACE, "-s", sig_path],
                stdin=data, capture_output=True, text=True)
    if r.returncode != 0:
        raise UpdateError("signature check failed: %s" % (r.stderr or r.stdout).strip())


def safe_extract(bundle, dest):
    """Unpack only plain files and folders, staying inside dest. Returns the file count."""
    count = total = 0
    with tarfile.open(bundle, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) > MAX_FILES:
            raise UpdateError("bundle has too many files")
        tops = {m.name.split("/")[0] for m in members if m.name not in (".", "")}
        strip = None
        if len(tops) == 1:
            only = next(iter(tops))
            # a single top-level folder (pvj-1.2.3/) is stripped; a lone file is not
            if any(m.name.startswith(only + "/") for m in members):
                strip = only
        for m in members:
            # Judge the name exactly as stored, before any folder is stripped from it.
            raw = m.name
            if os.path.isabs(raw) or ".." in raw.split("/") or "\\" in raw or "\0" in raw:
                raise UpdateError("unsafe path in bundle: %r" % m.name)
            name = raw
            if name in (".", ""):
                continue
            if strip and (name == strip or name.startswith(strip + "/")):
                name = name[len(strip):].lstrip("/")
                if not name:
                    continue
            if not (m.isfile() or m.isdir()):
                raise UpdateError("bundle contains a link or special file: %r" % m.name)
            target = os.path.join(dest, name)
            if m.isdir():
                os.makedirs(target, mode=0o755, exist_ok=True)
                continue
            total += m.size
            count += 1
            if total > MAX_BUNDLE_BYTES:
                raise UpdateError("bundle is too large when unpacked")
            os.makedirs(os.path.dirname(target), mode=0o755, exist_ok=True)
            with tar.extractfile(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            os.chmod(target, 0o755 if m.mode & 0o111 else 0o644)  # never setuid or group/world-writable
    return count


def inspect_bundle(directory):
    """Version and settings schema of an unpacked bundle, without importing its code."""
    def read(rel):
        try:
            with open(os.path.join(directory, rel)) as f:
                return f.read()
        except OSError:
            raise UpdateError("bundle is missing %s" % rel)
    version = re.search(r'^__version__ = "([^"]+)"', read("pvj/__init__.py"), re.M)
    schema = re.search(r"^SCHEMA = (\d+)", read("pvj/settings.py"), re.M)
    if not version or not schema:
        raise UpdateError("cannot read version or schema from the bundle")
    for rel in ("bin/pvj-player", "bin/pvj-web", "install/install.sh"):
        if not os.path.isfile(os.path.join(directory, rel)):
            raise UpdateError("bundle is missing %s" % rel)
    parse_version(version.group(1))
    return {"version": version.group(1), "schema": int(schema.group(1))}


class Updater:
    def __init__(self, root="/", prefix="/opt/pvj", state_dir="/var/lib/pvj", etc_dir="/etc/pvj",
                 run=subprocess.run, restart=None, health=None, install=None, now=time.time):
        self.root = root.rstrip("/")
        self.prefix = prefix
        self.state_dir = state_dir
        self.etc_dir = etc_dir
        self.run = run
        self._restart = restart or self._systemd_restart
        self._health = health or self._http_health
        self._install = install or self._run_installer
        self.now = now

    # --- paths ----------------------------------------------------------
    def real(self, p):
        return self.root + p

    @property
    def current_link(self):
        return self.real(self.prefix + "/current")

    def allowed_signers(self):
        return self.real(self.etc_dir + "/allowed_signers")

    def settings_path(self):
        return self.real(self.state_dir + "/settings.json")

    def backup_dir(self):
        return self.real(self.state_dir + "/backups")

    # --- state ----------------------------------------------------------
    def _link_version(self):
        try:
            return os.path.basename(os.readlink(self.current_link))
        except OSError:
            return None

    def status(self):
        rel = self.real(self.prefix + "/releases")
        releases = sorted((d for d in os.listdir(rel) if _VERSION.match(d)), key=parse_version) if os.path.isdir(rel) else []
        prev = None
        try:
            with open(self.real(self.prefix + "/previous")) as f:
                prev = os.path.basename(f.read().strip())
        except OSError:
            pass
        return {"current": self._link_version(), "previous": prev, "releases": releases}

    def _settings_schema(self):
        try:
            with open(self.settings_path()) as f:
                return int(json.load(f).get("schema", 0))
        except (OSError, ValueError):
            return None

    # --- backups --------------------------------------------------------
    def backup_settings(self, version):
        src = self.settings_path()
        if not os.path.isfile(src):
            return None
        os.makedirs(self.backup_dir(), mode=0o700, exist_ok=True)
        dst = os.path.join(self.backup_dir(), "settings-%s-%d.json" % (version, int(self.now())))
        shutil.copyfile(src, dst)
        os.chmod(dst, 0o600)
        old = sorted(glob.glob(os.path.join(self.backup_dir(), "settings-*.json")), key=os.path.getmtime)
        for path in old[:-KEEP_BACKUPS]:
            os.unlink(path)
        return dst

    def restore_settings(self, backup):
        current = self.settings_path()
        if os.path.isfile(current):
            shutil.move(current, "%s.rolled-back-%d" % (current, int(self.now())))
        shutil.copyfile(backup, current)
        os.chmod(current, 0o600)

    def latest_backup(self, version):
        found = sorted(glob.glob(os.path.join(self.backup_dir(), "settings-%s-*.json" % version)), key=os.path.getmtime)
        return found[-1] if found else None

    # --- actions --------------------------------------------------------
    def check(self, bundle, sha256=None, allow_unsigned=False, force=False):
        """Verify a bundle without installing it. Returns (info, extracted_dir)."""
        if not os.path.isfile(bundle):
            raise UpdateError("%s not found" % bundle)
        if os.path.getsize(bundle) > MAX_BUNDLE_BYTES:
            raise UpdateError("bundle is too large")
        if sha256 is None and os.path.isfile(bundle + ".sha256"):
            with open(bundle + ".sha256") as f:
                sha256 = f.read()
        verify_sha256(bundle, sha256)
        if allow_unsigned:
            pass
        else:
            verify_signature(bundle, bundle + ".sig", self.allowed_signers(), self.run)
        work = tempfile.mkdtemp(prefix=".update-", dir=self.real(self.prefix)) if os.path.isdir(self.real(self.prefix)) \
            else tempfile.mkdtemp(prefix=".update-")
        try:
            free = shutil.disk_usage(work).free
            if free < 3 * os.path.getsize(bundle):
                raise UpdateError("not enough free disk space")
            safe_extract(bundle, work)
            info = inspect_bundle(work)
            cur = self._link_version()
            if cur and not force:
                if parse_version(info["version"]) <= parse_version(cur):
                    raise UpdateError("version %s is not newer than the installed %s (use --force to reinstall)"
                                      % (info["version"], cur))
            schema = self._settings_schema()
            if schema is not None and info["schema"] < schema and not force:
                raise UpdateError("this bundle understands settings schema %d but yours is %d; refusing to downgrade"
                                  % (info["schema"], schema))
            return info, work
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise

    def apply(self, bundle, sha256=None, allow_unsigned=False, force=False, log=print):
        info, work = self.check(bundle, sha256, allow_unsigned, force)
        old_version = self._link_version()
        try:
            backup = self.backup_settings(old_version or "none")
            log("installing %s (was %s)" % (info["version"], old_version or "nothing"))
            self._install(work, info)
            self._restart()
            if not self._health():
                log("the new version did not come up; rolling back")
                self._switch_to(old_version, backup)
                raise UpdateError("update to %s failed its health check and was rolled back" % info["version"])
            log("update complete: %s" % info["version"])
            return info["version"]
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def rollback(self, log=print):
        st = self.status()
        target = st["previous"]
        if not target or not os.path.isdir(self.real("%s/releases/%s" % (self.prefix, target))):
            raise UpdateError("there is no previous release to go back to")
        log("rolling back %s -> %s" % (st["current"], target))
        self._switch_to(target, self.latest_backup(target), current=st["current"])
        if not self._health():
            raise UpdateError("rolled back, but the panel did not answer; check the services")
        return target

    def _switch_to(self, version, backup, current=None):
        if version is None:
            raise UpdateError("nothing to switch back to")
        tmp = self.current_link + ".tmp"
        if os.path.lexists(tmp):
            os.unlink(tmp)
        os.symlink("%s/releases/%s" % (self.prefix, version), tmp)
        os.replace(tmp, self.current_link)
        if backup:
            self.restore_settings(backup)
        if current:
            with open(self.real(self.prefix + "/previous"), "w") as f:
                f.write("%s/releases/%s\n" % (self.prefix, current))
        self._restart()

    # --- USB ------------------------------------------------------------
    def usb_bundles(self, base="/media/pvj"):
        found = []
        for path in glob.glob(os.path.join(base, "*", "pvj-update", "*.tar.gz")):
            m = re.search(r"pvj-(\d+\.\d+\.\d+)\.tar\.gz$", os.path.basename(path))
            if m:
                found.append((parse_version(m.group(1)), path))
        return [p for _, p in sorted(found, reverse=True)]

    # --- defaults -------------------------------------------------------
    def _run_installer(self, work, info):
        args = [os.path.join(work, "install", "install.sh"), "--offline", "--no-start", "--prefix", self.prefix]
        r = self.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise UpdateError("installer failed: %s" % (r.stderr or r.stdout).strip()[-500:])

    def _systemd_restart(self):
        if os.path.isdir("/run/systemd/system") and self.root == "":
            self.run(["systemctl", "restart", "pvj-player.service", "pvj-web.service"], capture_output=True)

    @staticmethod
    def _http_health(timeout=30):
        port = os.environ.get("PVJ_PORT", "80")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%s/api/hello" % port, timeout=2) as r:
                    if r.status == 200:
                        return True
            except OSError:
                time.sleep(1)
        return False


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="pvj-update", description="Update or roll back NXLX PocketVJ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("check", "apply"):
        p = sub.add_parser(name)
        p.add_argument("bundle")
        p.add_argument("--sha256")
        p.add_argument("--allow-unsigned", action="store_true", help="development only")
        p.add_argument("--force", action="store_true")
    sub.add_parser("usb", help="apply the newest bundle in <usb>/pvj-update/")
    sub.add_parser("rollback")
    args = ap.parse_args(argv)
    if args.cmd != "status" and os.geteuid() != 0:
        print("pvj-update: run as root (sudo)", file=sys.stderr)
        return 1
    u = Updater()
    try:
        if args.cmd == "status":
            print(json.dumps(u.status(), indent=2))
        elif args.cmd == "check":
            info, work = u.check(args.bundle, args.sha256, args.allow_unsigned, args.force)
            shutil.rmtree(work, ignore_errors=True)
            print("ok: version %s, settings schema %d" % (info["version"], info["schema"]))
        elif args.cmd == "apply":
            u.apply(args.bundle, args.sha256, args.allow_unsigned, args.force)
        elif args.cmd == "usb":
            bundles = u.usb_bundles()
            if not bundles:
                raise UpdateError("no update bundle found in /media/pvj/*/pvj-update/")
            print("using %s" % bundles[0])
            u.apply(bundles[0])
        elif args.cmd == "rollback":
            u.rollback()
    except UpdateError as e:
        print("pvj-update: %s" % e, file=sys.stderr)
        return 1
    return 0
