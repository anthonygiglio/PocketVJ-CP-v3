#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
# NXLX PocketVJ installer. Idempotent: run it again to update or repair.
#
#   sudo install/install.sh [options]
#
# Options
#   --prefix DIR     install location (default /opt/pvj)
#   --user NAME      account that owns the screen and sound card (default: the
#                    user who ran sudo, else a new system user "pvj-player")
#   --web-user NAME  optional: add another account (a separate web app) to group "pvj"; NOT needed for
#                    the built-in panel, which has its own pvj-web account. Members can read the PIN.
#   --media DIR      video folder (default /var/lib/pvj/video)
#   --offline        never touch the network; fail if a dependency is missing
#   --no-start       install and enable the service but do not start it
#   --stage DIR      install into DIR without users, apt or systemctl (for tests and image builds)
#   --dry-run        print what would happen
#   --uninstall      remove the service and program files (keeps /etc/pvj and media)
#   --purge          with --uninstall, also remove /etc/pvj
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/pvj
PVJ_USER=""
WEB_USER=""
MEDIA=/var/lib/pvj/video
OFFLINE=0
START=1
STAGE=""
DRY=0
UNINSTALL=0
PURGE=0

log() { printf 'pvj-install: %s\n' "$*"; }
die() { printf 'pvj-install: error: %s\n' "$*" >&2; exit 1; }
run() {
	if [ "$DRY" = 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi
}

while [ $# -gt 0 ]; do
	case "$1" in
	--prefix) PREFIX="${2:?}"; shift 2 ;;
	--user) PVJ_USER="${2:?}"; shift 2 ;;
	--web-user) WEB_USER="${2:?}"; shift 2 ;;
	--media) MEDIA="${2:?}"; shift 2 ;;
	--offline) OFFLINE=1; shift ;;
	--no-start) START=0; shift ;;
	--stage) STAGE="${2:?}"; shift 2 ;;
	--dry-run) DRY=1; shift ;;
	--uninstall) UNINSTALL=1; shift ;;
	--purge) PURGE=1; shift ;;
	-h | --help) sed -n '4,19p' "${BASH_SOURCE[0]}"; exit 0 ;;
	*) die "unknown option $1 (try --help)" ;;
	esac
done

case "$PREFIX" in /*) ;; *) die "--prefix must be an absolute path" ;; esac
case "$MEDIA" in /*) ;; *) die "--media must be an absolute path" ;; esac
for name in "$PVJ_USER" "$WEB_USER"; do
	[[ "$name" =~ ^([a-z_][a-z0-9_-]*)?$ ]] || die "user names may only use a-z, 0-9, _ and -"
done
# Refuse prefixes that would make uninstall dangerous (/, /opt, /usr, ...).
[[ "$PREFIX" =~ ^/[^/]+/[^/]+ ]] || die "--prefix must be at least two levels deep, e.g. /opt/pvj"
case "$PREFIX" in /usr/* | /etc/* | /bin/* | /sbin/* | /lib/* | /boot/* | /var/lib/dpkg*) die "--prefix must not be inside a system directory" ;; esac
for path in "$PREFIX" "$MEDIA"; do
	[[ "$path" =~ ^[A-Za-z0-9/_.-]+$ ]] || die "paths may only use letters, digits, / _ . and -"
	[[ "$path" =~ (^|/)\.\.(/|$) ]] && die "paths may not contain .. components"
done

if [ -n "$STAGE" ]; then
	ROOT="${STAGE%/}"
else
	ROOT=""
	if [ "$DRY" = 0 ] && [ "$(id -u)" -ne 0 ]; then die "run as root (sudo), or use --dry-run or --stage"; fi
fi
REAL=$([ -z "$STAGE" ] && echo 1 || echo 0)

ETC="$ROOT/etc/pvj"
UNIT="$ROOT/etc/systemd/system/pvj-player.service"
USB_UNIT="$ROOT/etc/systemd/system/pvj-usb@.service"
WEB_UNIT="$ROOT/etc/systemd/system/pvj-web.service"
USB_RULE="$ROOT/etc/udev/rules.d/99-pvj-usb.rules"
BIN_LINKS="$ROOT/usr/local/bin"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/pvj/__init__.py")"
[ -n "$VERSION" ] || die "cannot read version from pvj/__init__.py"
RELEASE="$ROOT$PREFIX/releases/$VERSION"

uninstall() {
	log "uninstalling"
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
		systemctl disable --now pvj-player.service 2>/dev/null || true
	fi
	run rm -f "$UNIT" "$WEB_UNIT" "$USB_UNIT" "$USB_RULE" "$BIN_LINKS/pvj-player" "$BIN_LINKS/pvj-selftest" "$BIN_LINKS/pvj-usb" "$BIN_LINKS/pvj-rootfs" "$BIN_LINKS/pvj-pin" "$BIN_LINKS/pvj-update"
	run rm -rf "${ROOT}${PREFIX:?}"
	[ "$PURGE" = 1 ] && run rm -rf "$ETC"
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then systemctl daemon-reload; fi
	if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && command -v udevadm >/dev/null; then udevadm control --reload || true; fi
	log "done. Kept ${ETC} and media unless --purge; users and group pvj are left in place."
}

if [ "$UNINSTALL" = 1 ]; then uninstall; exit 0; fi

# --- dependencies -------------------------------------------------------
need=()
command -v mpv >/dev/null || need+=(mpv)
command -v python3 >/dev/null || need+=(python3)
if command -v python3 >/dev/null && ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
	die "Python 3.9 or newer is required"
fi
if [ ${#need[@]} -gt 0 ]; then
	if [ "$OFFLINE" = 1 ]; then die "offline mode and missing: ${need[*]}"; fi
	if [ "$REAL" = 0 ]; then log "stage mode: not installing ${need[*]}"
	elif command -v apt-get >/dev/null; then
		log "installing ${need[*]}"
		run apt-get install -y --no-install-recommends "${need[@]}"
	else
		die "unsupported system (no apt-get). Install manually: ${need[*]}"
	fi
fi

# --- accounts -----------------------------------------------------------
if [ -z "$PVJ_USER" ] && [ -f "$ETC/install.json" ]; then
	PVJ_USER="$(sed -n 's/.*"user": "\([a-z_][a-z0-9_-]*\)".*/\1/p' "$ETC/install.json" | head -n 1)"
fi
if [ -z "$PVJ_USER" ]; then
	if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then PVJ_USER="$SUDO_USER"; else PVJ_USER=pvj-player; fi
fi

if [ "$REAL" = 1 ]; then
	getent group pvj >/dev/null || run groupadd --system pvj
	if ! id "$PVJ_USER" >/dev/null 2>&1; then
		log "creating system user $PVJ_USER"
		run useradd --system --create-home --home-dir /var/lib/pvj --shell /usr/sbin/nologin --gid pvj "$PVJ_USER"
	fi
	for g in pvj video render audio input; do
		getent group "$g" >/dev/null && run usermod -aG "$g" "$PVJ_USER"
	done
	if [ -n "$WEB_USER" ]; then run usermod -aG pvj "$WEB_USER"; fi
	# The web panel runs as its own account with no screen or sound access.
	if ! id pvj-web >/dev/null 2>&1; then
		log "creating system user pvj-web"
		run useradd --system --no-create-home --shell /usr/sbin/nologin --gid pvj pvj-web
	fi
fi

# --- program files: releases/<version>, "current" points at the active one --
log "installing version $VERSION to $PREFIX"
run mkdir -p "$ROOT$PREFIX/releases"
run rm -rf "$RELEASE.new"
run mkdir -p "$RELEASE.new"
if [ "$DRY" = 0 ]; then
	cp -a "$SRC/pvj" "$SRC/bin" "$RELEASE.new/"
	find "$RELEASE.new" -name __pycache__ -type d -prune -exec rm -rf {} +
fi
previous=""
[ -L "$ROOT$PREFIX/current" ] && previous="$(readlink "$ROOT$PREFIX/current")"
# Same version installed again: move the old folder aside and swap in the new one, so the folder
# "current" points at is never deleted first.
if [ -e "$RELEASE" ]; then run mv "$RELEASE" "$RELEASE.old.$$"; fi
run mv "$RELEASE.new" "$RELEASE"
run rm -rf "$RELEASE.old.$$"
# Atomic switch: rename a fresh symlink over "current".
run ln -sfn "$PREFIX/releases/$VERSION" "$ROOT$PREFIX/current.tmp"
run mv -T "$ROOT$PREFIX/current.tmp" "$ROOT$PREFIX/current"
if [ -n "$previous" ] && [ "$previous" != "$PREFIX/releases/$VERSION" ]; then
	[ "$DRY" = 0 ] && printf '%s\n' "$previous" > "$ROOT$PREFIX/previous"
fi
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ]; then chown -R root:root "$ROOT$PREFIX"; fi
run chmod -R go-w "$ROOT$PREFIX"

run mkdir -p "$BIN_LINKS"
run ln -sfn "$PREFIX/current/bin/pvj-player" "$BIN_LINKS/pvj-player"
run ln -sfn "$PREFIX/current/bin/pvj-selftest" "$BIN_LINKS/pvj-selftest"
run ln -sfn "$PREFIX/current/bin/pvj-usb" "$BIN_LINKS/pvj-usb"
run ln -sfn "$PREFIX/current/bin/pvj-rootfs" "$BIN_LINKS/pvj-rootfs"
run ln -sfn "$PREFIX/current/bin/pvj-pin" "$BIN_LINKS/pvj-pin"
run ln -sfn "$PREFIX/current/bin/pvj-update" "$BIN_LINKS/pvj-update"

# --- settings and media (never overwritten if they exist) -----------------
run mkdir -p "$ETC"
if [ ! -e "$ETC/pvj.env" ]; then
	if [ "$DRY" = 0 ]; then
		cat > "$ETC/pvj.env" <<ENV
# Settings for the pvj-player service. Edit, then: sudo systemctl restart pvj-player
PVJ_MEDIA_DIR=$MEDIA
PVJ_USB_DIR=/media/usb
# USB drives are mounted read-only under /media/pvj/<label>. Set to 1 to allow writing.
PVJ_USB_RW=0
# Web panel: port and address. The panel is for a private network; do not expose it to the internet.
# PVJ_PORT=80
# PVJ_BIND=0.0.0.0
ENV
	fi
else
	log "keeping existing $ETC/pvj.env"
fi
# Public keys whose signatures pvj-update accepts (one line per key, see install/README.md).
if [ ! -e "$ETC/allowed_signers" ] && [ "$DRY" = 0 ]; then
	cat > "$ETC/allowed_signers" <<KEYS
# Add your release signing key here. Format:
#   pvj-release namespaces="pvj-release" ssh-ed25519 AAAA... comment
# Until a key is listed, pvj-update refuses every bundle.
KEYS
	chmod 644 "$ETC/allowed_signers"
fi
run mkdir -p "$ROOT$MEDIA"
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ]; then
	chown "$PVJ_USER":pvj "$MEDIA"; chmod 2775 "$MEDIA"
	# settings.json lives in /var/lib/pvj and is written by the web panel (group pvj)
	mkdir -p /var/lib/pvj; chgrp pvj /var/lib/pvj; chmod 2775 /var/lib/pvj
fi
if [ "$DRY" = 0 ]; then
	printf '{"version": "%s", "prefix": "%s", "user": "%s", "installed": "%s"}\n' \
		"$VERSION" "$PREFIX" "$PVJ_USER" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$ETC/install.json"
fi

# --- service ------------------------------------------------------------
run mkdir -p "$(dirname "$UNIT")"
if [ "$DRY" = 0 ]; then
	sed -e "s|@PVJ_USER@|$PVJ_USER|g" -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-player.service" > "$UNIT"
	sed -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-web.service" > "$WEB_UNIT"
fi
# USB automount: udev starts pvj-usb@<partition>.service, which mounts by label.
run mkdir -p "$(dirname "$USB_RULE")"
if [ "$DRY" = 0 ]; then
	sed -e "s|@PVJ_DIR@|$PREFIX/current|g" "$SRC/install/pvj-usb@.service" > "$USB_UNIT"
	cp "$SRC/install/99-pvj-usb.rules" "$USB_RULE"
fi
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && command -v udevadm >/dev/null; then udevadm control --reload || true; fi
if [ "$REAL" = 1 ] && [ "$DRY" = 0 ] && [ -d /run/systemd/system ]; then
	systemctl daemon-reload
	systemctl enable pvj-player.service pvj-web.service
	if [ "$START" = 1 ]; then systemctl restart pvj-player.service pvj-web.service; fi
fi

log "installed. Check the device with: pvj-selftest --play"
