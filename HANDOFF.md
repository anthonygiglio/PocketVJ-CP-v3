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
- NDI, AES67/Dante and ST 2110 are separate optional modules, not built yet. SRT/RTSP/RTMP streams, DMX, MIDI, the schedule and autostart are built, off by default.
- Naming: the `pvj` package, `pvj-*` services and commands and install paths keep their names.
- Style: no em dashes in written text (commas, semicolons, new sentences). Default document font Inter.

## What exists

Merged to `master`: the security hotfix, the platform layer, the installer and services, the image definition, and the new core in Python 3 (API, panel, modules, themes, OSC receive, signed updates with rollback). On top of that, all built as separate pieces with their own notes in `pvj/`:

| Feature | Notes | Default |
| --- | --- | --- |
| Library: upload, rename, delete; play from a USB drive | this file, `pvj/README.md` | on |
| Sound output (HDMI chosen automatically on a Pi) | `docs/MANUAL.md` | on |
| Screen snapshot (one picture on request; live view was dropped, D20) | `docs/MANUAL.md` | on |
| PIN drawn on the box's screen until the first device pairs | `pvj/pinscreen.py` | on |
| Autostart (at power-up and after a player restart) | `pvj/AUTOSTART.md` | off |
| Weekly schedule | `pvj/SCHEDULE.md` | off (beta) |
| Streams (SRT, RTSP, RTMP) | `pvj/STREAMS.md` | off (beta) |
| DMX over the network (Art-Net, sACN) | `pvj/DMX.md` | off (beta) |
| MIDI controllers (every controller at once, Learn) | `pvj/MIDI.md` | off (beta) |
| Wired network settings with confirm-or-revert | `pvj/NETWORK.md` | off (beta) |

Docs for people: [docs/MANUAL.md](docs/MANUAL.md) and pictures in [docs/UI.md](docs/UI.md) (made by `tests/ui/screenshots.js`). Phase list and module manifests: [ROADMAP.md](ROADMAP.md), `pvj/modules.d`.

## What has been run on real hardware, and what has not

One test Raspberry Pi 4 (Model B Rev 1.5, Debian 13 trixie, wired Ethernet, a 2560x1440 monitor, a USB drive, three USB MIDI controllers, a USB HDMI capture adapter) has run the image built by CI since 2026-09-30. **Verified there:** the image boots; the panel and PIN pairing over the network; uploads; playback (720p and 1080p H.264 in software, smooth after the fixes); sound on HDMI; USB drive automount (exFAT, read-only) and playing from it; reboot brings all services up; a killed mpv is back in about 2 seconds; DMX over the LAN; the weekly schedule; autostart across a reboot and after a crash (a Stop from the panel stays stopped); MIDI reading through the systemd sandbox and all three controllers opened at once by stable names; the screen snapshot; the board self-test. Each of those is described in the journal with numbers.

**Not verified on hardware (do not claim it works):**
- MIDI Learn and the map (only against pipes and the browser test; needs a person at the controllers), and the PIN on screen (needs a fresh box or all devices removed).
- Streams with a real SRT or RTSP source; HEVC, 4K and 1080p60 decode; 24 fps film judder on a 75 Hz display (the monitor offers only 75, 60 and 50 Hz; there is no display-mode setting yet); the second HDMI port; a projector.
- The read-only root (`pvj-rootfs`), signed updates on a board, the Network module (must be tested with a keyboard and monitor on the box, never over SSH on the only link), TouchOSC.
- Pi 3, Pi 5 and x86 (nothing has run on them).

Not built: crossfade (needs a second player; only "Dip to black" and "Cut"), Wi-Fi/hotspot/VLAN, updates from the network or channels, a panel update button, shutdown and reboot buttons, a display-mode (resolution and refresh) setting, NDI, AES67/Dante, ST 2110, mapper, presenter, wall, controller profiles and lights/feedback for MIDI, custom DMX layouts, Art-Net discovery.

Known limits: the panel cannot restart a wedged mpv (it is unprivileged by design). GitHub Pages deploy of the old manual fails on every push because Pages is not enabled or not set to build from Actions (a repository setting, not code). Merged branches on GitHub have not been deleted (the tool that tried was blocked; delete them on github.com). The `legacy-v3` tag (commit `ed74df411b88b1a16dd80eecf52c3c9cf6d7768b`) is on GitHub.

## Testing

- `python3 -m unittest discover -s tests` (about a minute; needs mpv).
- Browser test: `node tests/ui/panel.test.js` (needs Playwright and Chromium).
- On a real board: **Option C** in [tools/DEVICE-TESTING.md](tools/DEVICE-TESTING.md) (flash the CI image, then the numbered checklist). `tools/device-test.sh` and the manual "Device test" workflow are Options A and B. `tools/artnet-send.py` sends Art-Net to test DMX from a laptop.
- Static checks of the systemd units run everywhere (`tests/test_units.py`): the ordering-cycle bug that stopped the player at boot could not be seen in a container.
- Risky features got an independent read-only review; every finding was fixed with a test that reproduces it. Keep doing that for anything touching root, the network, uploads or auth.

## Working agreements

- Open a PR per finished branch. When it is green and ready, add a short comment and merge. Do not rewrite git history.
- Ask before destructive or outward actions (deleting remote branches, moving the repository, overwriting a card). Read the check list for failures before merging: zero failures, not just "nothing pending".
- Anything that reads devices, takes network input or handles filenames from removable media gets an independent read-only review before merging, and every finding gets a test.
- Report outcomes faithfully. Do not claim something works on hardware it has not run on.

## Lessons and limits

- The panel browser test once failed only in CI. The cause was real: the Network form kept typed values only through input events and lost one on a redraw; it now reads the page before every rebuild. When a UI test fails only in CI, add a diagnostic that prints the state at failure and rerun until it shows; do not merge over a red check and do not loosen the assertion.
- The first boot on real hardware found two service bugs no container test could (a systemd ordering cycle that dropped the player's start job, and mpv's owner-only control socket) and four Pi-specific defaults (cheap scaling, HDMI sound, USB media, the cost of a live preview). Read `project-log/LESSONS.md` before trusting a green CI run for anything that touches devices.
- A/B measure performance claims on the real device (A, B, A, B, A) and trust the person watching over a counter.
- A claude session in the cloud could not push tags or delete branches (HTTP 403 from the proxy). Do those from a normal clone or on github.com.
- The session's GitHub scope is fixed to the repo name it started with; after a rename, start new sessions on the new name.
- A claude cloud session cannot reach devices on your LAN. Use the self-hosted runner in DEVICE-TESTING.md or run the test script by hand.
