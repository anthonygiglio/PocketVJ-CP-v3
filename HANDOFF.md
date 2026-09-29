<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Hand-off notes

For a new person or a new claude session picking this up cold. Read this, then the [project log](project-log/README.md) (decisions, lessons, journal), [README.md](README.md), [ROADMAP.md](ROADMAP.md) and [pvj/README.md](pvj/README.md).

## What this is

**nxlx.mastercontrol** by NXLX.Systems (Anthony Giglio, video artist; "NXLX" is his VJ name, now the whole practice). It is a fork of PocketVJ CP v3 by Marc-André Gasser (magdesign), whose upstream is unmaintained and unreachable. Goal: run on Raspberry Pi 3B/4/5, x86 mini PCs and old computers, on Pi OS Bookworm/Trixie and Debian/Ubuntu, and be rugged at gigs (direct Ethernet on a private network preferred).

The repository was renamed from `PocketVJ-CP-v3` to `nxlx.mastercontrol`.

## Decisions already made (do not reopen without a reason)

- Python 3, standard library only, replaces PHP. The legacy PHP and docs stay in the tree until feature parity.
- New code is Apache-2.0 (SPDX headers, `REUSE.toml`). Legacy code stays under the upstream `LICENSE.md`. `LICENSE.md` and `AUTHORS.md` are never edited. Copyright holder in headers: "NXLX.Systems and contributors".
- One long-lived mpv controlled over JSON IPC, supervised by systemd (`pvj-player.service`). The panel (`pvj-web.service`) is unprivileged; anything needing root goes through a small helper (`pvj-netd`) over a socket.
- Token auth (PIN pairing, roles view/live/full), CSRF header, strict CSP. Signed updates with rollback.
- NDI, SRT/RTSP/RTMP, AES67/Dante and ST 2110 are separate optional modules, not built yet.
- Naming: the `pvj` package, `pvj-*` services and commands and install paths keep their names.
- Style: no em dashes in written text (commas, semicolons, new sentences). Default document font Inter.

## What exists

Phases 0 to 4 and two feature screens are merged: security hotfix, platform layer, installer/services/image definition, the new core (API, panel, modules, themes, OSC receive, signed updates), the Library screen (upload, rename, delete), the wired Network screen (beta, off by default) and the weekly schedule (beta, off by default; see `pvj/SCHEDULE.md`). See ROADMAP.md for the phase list and the module manifests in `pvj/modules.d`.

## What has NOT been done or verified

- **Nothing has run on a real Pi, display, USB stick, TouchOSC or NetworkManager.** All testing was in a container with a real headless mpv and fakes. The Network feature in particular needs a test on a real device, with another way to reach the box.
- The OS image has never been built. The workflow exists (`.github/workflows/image.yml`).
- Not built: crossfade (needs a second player; only "Dip to black" and "Cut"), Wi-Fi/hotspot/VLAN, updates from the network or channels, a panel update button, the Inputs/NDI/SRT/Dante/ST 2110 screens, mapper, presenter, wall, MIDI/DMX.
- The panel cannot restart a wedged mpv (it is unprivileged by design).
- The `legacy-v3` tag (commit `ed74df411b88b1a16dd80eecf52c3c9cf6d7768b`) is on GitHub (checked 2026-09-29).

## Testing

- `python3 -m unittest discover -s tests` (about a minute; needs mpv).
- Browser test: `node tests/ui/panel.test.js` (needs Playwright and Chromium).
- On a real board: `tools/device-test.sh`, or the manual "Device test" workflow. Setup in [tools/DEVICE-TESTING.md](tools/DEVICE-TESTING.md).
- Risky features got an independent read-only review; every finding was fixed with a test that reproduces it. Keep doing that for anything touching root, the network, uploads or auth.

## Working agreements

- Open a PR per finished branch. When it is green and ready, add a short comment and merge. Do not rewrite git history.
- Ask before destructive or outward actions.
- Report outcomes faithfully. Do not claim something works on hardware it has not run on.

## Lessons and limits

- The panel browser test is timing sensitive on slow CI runners. The Network form used to be wiped by redraws; it now keeps its state. When a UI test fails only in CI, look for a redraw or load race before changing the assertions.
- A claude session in the cloud could not push tags or delete branches (HTTP 403 from the proxy). Do those from a normal clone or on github.com.
- The session's GitHub scope is fixed to the repo name it started with; after a rename, start new sessions on the new name.
- A claude cloud session cannot reach devices on your LAN. Use the self-hosted runner in DEVICE-TESTING.md or run the test script by hand.
