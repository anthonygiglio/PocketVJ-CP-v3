<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Journal

Newest entry first. One entry per working session: what was done, what merged, what is open.

## 2026-09-30 (remote support)

Done:
- Remote support sessions (D29, `docs/REMOTE-SUPPORT.md`): `pvj/support.py`, the root helper `pvj-supportd` (`pvj/supportd.py`, unit, installer and image), System > Remote support card, a support sign-in screen for people arriving through the tunnel, a banner on every device, settings schema 12, `tools/support-hub/` for the owner's server. The installer installs `wireguard-tools` and `nftables` when it can and otherwise reports remote support as unavailable.
- Chosen after research (agent report): WireGuard out to the owner's server, started at the studio, panel only, time-limited. Tailscale and RustDesk were considered and not used (see D29).

Verified on the Pi 4 against a stand-in support server in a network namespace: tunnel up with a handshake in 3 seconds; through it `/api/hello` said remote, the status needed a login, a wrong code was refused, the code signed in with the chosen role, PIN change, invites, session start, support settings and power off answered 403, SSH was blocked, the snapshot route passed the checks; restarting the helper mid-session removed the interface and the firewall table and the panel ended the session; Stop removed everything. Afterwards remote support was switched off and cleared on the box, and the stand-in removed.

Not verified: a real server on the internet, a studio network, NAT, the hub scripts on a VPS.

## 2026-09-30 (projection mapper)

Done:
- Projection mapping, a beta module off by default (`pvj/mapper.py`, the Mapping card on Mix, `/api/mapper`, settings schema 11), replacing the old ofxPiMapper tab: quads with perspective, triangles and grids (bilinear, up to 8x8), up to 16 surfaces; drag or nudge corners from the phone, screen and picture corners, layer order, hide, rename, 8 saved mappings that follow a change of screen size. Outlines on the display while editing. See D28 and `pvj/MAPPER.md`.
- While a mapping is shown, the player uses 8-bit GPU buffers and stretches the picture to the screen; both go back to normal when it is off.
- Naming: multi-box sync will use server and client (the owner's choice).

Verified on the Pi 4 (screenshots of the player's output and mpv's error log; nobody watched the monitor): edit and show views of a quad, a 2x2 grid over a quad and a 64-cell grid; edges and overlaps smooth; picture corners; saved mapping; no stale shader files. Dropped frames: 0 at 1920x1080 with any mapping, about 3 a second at 2560x1440. The table takes 1.3 s (1080p, 64 cells) to 1.8 s (1440p) to build.

Found and fixed on the way (see LESSONS): mpv silently refused a half-float table, which made the first benchmark meaningless; per-cell perspective broke grids at inner lines; an osd-overlay did not show in screenshots.

Independent review: one high (a build thread per change: 25 at once in a drag), two medium (an older switch could delete a newer shader file; letterboxed clips moved every surface, confirmed on the Pi with a 720x576 test pattern), and small ones. All fixed with tests: one coalescing build worker that stops early when overtaken, cleanup that never touches newer files, the picture stretched while mapping, statuses that an older change cannot overwrite, folded grids refused, non-finite numbers refused, saved mappings that stay editable on a much larger screen, drag positions sent one at a time. The browser test failed first because its wait for the word "Modules" now also matched the Mix card; it waits for the System heading now.

Not verified: a projector, the owner at the screen, Pi 5 and x86.

## 2026-09-30 (projectors and a broader schedule)

Done:
- Projector control over PJLink class 1, a beta module off by default (`pvj/projector.py`, System > Projectors, `/api/projectors`, `/api/projector`, settings schema 10): on, off, picture mute, state; All on and All off. Private addresses only, checked at add time and before every command; passwords never returned (D27). See `pvj/PROJECTORS.md`.
- The schedule can now run a legacy start script (`preset`) and switch every projector on or off. The old OSC `/beameron` and `/beameroff` work again.
- The fake projector in the tests found a real bug before it shipped: PJLink ends lines with a carriage return alone, and the client read with `readline()`, so it would have hung on every real projector. Also added an overall deadline per command and asked all projectors at once.
- Independent review: no high findings. Fixed with tests: OSC and the schedule no longer wait for projectors (background); the name lookup when adding runs outside the settings lock; names DNS cannot encode give a 400, not a 500; an unexpected error gives a 502; one command at a time per projector; the connect shares the deadline; 169.254.169.254 refused; a typed password is no longer written into the page HTML; the docs state the real limits.

Not verified: no real projector. The owner has none on the test network.

## 2026-09-30 (PIN on screen)

Done: `pvj/pinscreen.py`. The pairing PIN and the panel's addresses are drawn by mpv on its idle screen every 3 seconds, only while no device has ever paired and nothing is playing, using a whitelist of characters (mpv expands `${...}`). Tests use a fake player.

Hardware: mpv's on-screen text on the idle Pi 4 was confirmed by the owner (top left, readable). The PinScreen thread itself was NOT run on the board, because the box already has paired devices and the feature deliberately stays silent then. To test: System > Access, remove every device (or a fresh SD card), reboot, look at the monitor.

## 2026-09-30 (USB, MIDI, audio, playback on the real Pi 4)

Verified on the board:
- USB drive (exFAT, 58 GB, three 3 to 5 GB films): auto-mounted read-only with `nosuid,nodev,noexec` at `/media/pvj/NXLX-USB`, `/media/usb` link made. Three MIDI controllers (Korg nanoKONTROL2, Akai MIDI Mix, Novation Launchpad Mini) and a USB HDMI capture adapter all enumerate. The MIDI module reads a controller through the systemd sandbox (`connected: true`), which confirms the unit fix from the earlier PR; 112 messages in a few seconds were handled.
- 1080p23.976 H.264 plays through the panel from the USB drive: software decode, about 109 percent of one of four cores, 41 C, no throttling, zero decoder drops, A/V sync steady.

Found on the board and fixed in this PR:
1. **Nothing on a USB drive could be played from the panel** (only the media folder was allowed). Added a USB list on the Media screen, `POST /api/play {"usb": "LABEL/name"}` with strict path checks, and made the old `startmasterusb` presets work.
2. **Choppy video**: 15 dropped frames a second scaling 1080p to 2560x1440. `--profile=fast` on Pi 3 and 4 by default cured it (0.0 a second).
3. **No sound on an HDMI monitor**: mpv's default output on a Pi is the headphone jack. New Sound output setting; "Automatic" picks the HDMI port with the screen on it; re-applied whenever the player restarts. Owner confirmed audio is good.
4. **The live screen preview hurt playback** (4.7 dropped frames a second with it open, 1.3 with a snapshot every 5 s). Replaced by a snapshot on request (D20).

Not verified: HEVC or 4K decode, 24 fps judder on a 75 Hz screen (the monitor offers 75, 60 and 50 Hz only), the second and third MIDI controllers' messages, the read-only root, streams, the Network module (needs a keyboard on the box).

## 2026-09-30 (screen viewer, more hardware results)

Done, on the real Pi 4 (Debian 13):
- Merged the two boot fixes (#24). A full reboot brings the services up; `kill -9` on mpv is recovered in about 2 s.
- DMX (Art-Net) over the real LAN from the owner's Mac: blackout on and off and opacity 40 percent all took effect, 143 frames counted. Weekly schedule: a `stop` entry and a `play` entry two and three minutes ahead both fired on time and reported `done`. Board self-test passes.
- Built the screen viewer (D19): `GET /api/preview.jpg`, a Screen card on Live, tests. The owner confirmed on the Mac that it looks right.

Not verified yet: PIN on the projector (still missing), USB drive, MIDI, streams, autostart across a reboot, HDMI audio, 1080p and higher decode load, read-only root, Network module.

## 2026-09-30 (first boot on a real Pi 4)

Hardware: Raspberry Pi 4 Model B Rev 1.5, image built by CI from master `0d7ca86`, flashed by the owner, wired Ethernet, a 2560x1440 75 Hz monitor. Debian 13 (trixie), kernel 6.18 aarch64, mpv 0.40.

Verified on the real board:
- The image boots; `pvj-web` and `pvj-netd` start on their own; the panel answers on port 80 and reports `"board": "pi4"` and the right model; temperature 32 to 38 C, no throttling (`get_throttled=0x0`).
- Pairing with the PIN over the network works; an upload of a 7.5 MB clip over the LAN works; playing through the panel works and the owner confirmed the picture on the monitor is smooth (720p30 H.264, software decode, about 18 percent of one core, 75 Hz display).
- After the fixes below: a full reboot brings all three services up by themselves, and `kill -9` on mpv is recovered by systemd in about 2 seconds with the panel still working.

Found on the board (none of these could show in a container), fixed in the same PR:
1. **The player never started at boot.** `pvj-player.service` had `After=multi-user.target` and is `WantedBy=multi-user.target`, and `pvj-web` is ordered after it: an ordering cycle. systemd deleted the player's start job with one journal line and no error. Fix: drop the ordering, and udev-settle (deprecated). A static test now builds the start-order graph of `install/*.service` and fails on a cycle (and proves it catches the old unit).
2. **The panel could not reach the player.** mpv creates its control socket owner-only (0600) whatever the `UMask`, and the panel runs as another user in group `pvj`. My first fix, a shell `ExecStartPost=` in the unit, did nothing: it ran before mpv had made the NEW socket and changed the stale one from the previous run. Real fix: `pvj-player serve` removes the stale socket, then a detached helper waits for the new socket and sets it to 0660. Tested with the real `serve()` and a stand-in mpv.
3. Not fixed yet: nothing shows the pairing PIN on the projector (the panel text and the manual say it does); H.264 uses software decode (`hwdec-current = no`); a 30 fps clip on a 75 Hz display was smooth here but refresh matching is not configured.

Not verified: HDMI audio, USB drive, MIDI, DMX, streams, schedule, autostart across a reboot, 1080p and higher decode load, the read-only root, the Network module (must be tested with a keyboard on the box).

## 2026-09-30 (manual)

Done:
- Merged autostart (#20). Wrote the user manual `docs/MANUAL.md` (get it running, pair, clips, play, modules, keeping it safe, troubleshooting, what is not built), added the autostart picture to `docs/UI.md`, and brought the stale panel section of `pvj/README.md` up to date (screens, API table, built and not-built lists).
- The manual says at the top that nothing has been booted on a real board.

## 2026-09-30 (autostart)

Done:
- Merged the screenshots (#19). Reran the browser test on master to catch the intermittent network-form failure; results in the next entry if any.
- Built Autostart (legacy tab 1): `pvj/autostart.py`, `/api/autostart` (+ `/test`), System > Autostart card, settings schema 6, `pvj/AUTOSTART.md`, tests. See D18.
- A test found a circular import that only shows when `pvj.api` is the first module loaded; fixed with a lazy import, and a test now imports each module first in a fresh interpreter.
- Fixed a latent bug: legacy preset names were matched with `$`, so `startless\n` passed; now `fullmatch`.

Not verified: never run through a real reboot or an mpv crash on a board.

## 2026-09-30 (screenshots)

Done:
- Added `tests/ui/screenshots.js` (cropped element shots of each screen and card, phone and desktop, from the real panel and the test harness), a non-blocking CI step that uploads them, `docs/images/ui/` (16 images, about 1.4 MB), `docs/UI.md` and a "What it looks like" section in the README. The pictures use test clips and a fake network; the docs say so.
- Taking the pictures showed two real layout bugs at phone width and both are fixed: the buttons in a list row wrapped mid-word ("Pla / y"), and stream addresses were shown in the small-caps label style (mangling them). Also shortened the network "revert" option, which was cut off.
- The inline `<style>` a screenshot script tried to add was refused by the panel's strict CSP, which is the CSP doing its job.

## 2026-09-29 (device plan)

Done:
- Reviewed the device test plan against what is actually in the image and rewrote it: new "Option C" in `tools/DEVICE-TESTING.md` (test the built image on a Pi: flash, first login without a keyboard, a numbered checklist, one test per beta module, what to send back). Option A and B were written for a Pi that already had Raspberry Pi OS, git, mpv and a clone; the image has no `git` and no `tests/`, so `tools/device-test.sh` needs a copy of the repository.
- Found by reading the unit file: `pvj-web.service` used `PrivateDevices=yes` and had no `audio` group, so the MIDI module could never see `/dev/snd/midi*` on the image. Now `SupplementaryGroups=audio`, `DevicePolicy=closed` and read-only ALSA access. Unit text checked by a test; not run under systemd.
- Added `tools/artnet-send.py` to test DMX from a laptop.
- Fixed the stale `image/README.md` (it said the panel was not in the image and the build took an hour).

Open:
- Pi 4 not yet reachable: moved to 192.168.0.0/24; no Raspberry Pi hardware address seen yet, and the SD card is not flashed. The earlier Debian host on 172.16.1.95 could not be logged into.

## 2026-09-29 (CI notes)

- I merged #16 (docs only) while its `panel-ui` check had failed, after commenting "checks green" without reading the result. The failure was the network form step (`panel.test.js` line 110, "typed values survive the redraw", 8 s timeout). The same code on master passed `panel-ui` on the next run, so it is a flake, not reproduced and not root-caused (Playwright is not installed on the dev Mac). Rule from now: read the check list for failures before merging, every time.
- "Deploy manual to Pages" has failed on every master push (`Get Pages site failed ... Pages enabled?`): Pages is not enabled for the repository, or not set to build from GitHub Actions. That is a repository setting for the owner; nothing in the code is wrong.
- A second image build was dispatched from master `2546726` (includes schedule, streams, DMX, MIDI) for the first hardware test. Still never booted.

## 2026-09-29 (first image build)

Done:
- The first run of `image.yml` (workflow dispatch on master, run 36627808613) built successfully in about 34 minutes. Artifact `nxlx-mastercontrol-image`, 717 MB, `image_2026-09-29-nxlx-mastercontrol.img.xz`, sha256 `b270f92c2e021cd78373b961bcc5810d4d29c0eb0b8af08ed619f8953bb779f9`. It kept for 90 days.
- That build predates the schedule, streams, DMX and MIDI modules (it ran on the commit before them); rebuild before flashing for a test.

Not verified:
- The image was **never booted**, on a Pi or in an emulator. "Built" means the pi-gen stage ran to the end and produced a file; it says nothing about whether it boots, brings up `pvj-player` and `pvj-web`, or shows the pairing PIN.

Open:
- Flash it on a Pi 4 and run `tools/device-test.sh`.

## 2026-09-29 (DMX and MIDI)

Done:
- Merged the streams module (#14).
- Added DMX over the network (Art-Net, sACN) and USB MIDI input: `pvj/dmx.py`, `pvj/midi.py`, `/api/dmx`, `/api/midi`, System cards, settings schema 5, `pvj/DMX.md`, `pvj/MIDI.md`, tests. See D17.
- A test found a real bug: regexes ending in `$` accepted a trailing newline (`"/dev/snd/midiC1D0\n"`, `"09:00\n"`). All new validation now uses `fullmatch`. Lesson recorded.
- Independent read-only review (agent) found: no way to reach shutdown, files or other routes, but real defects, all fixed with tests: unlocked apply/stop could leak a second receiver; DMX pad and function channels fired on every value change; the per-source rate limit is defeated by forged sources (added global packet and command caps); turning the module off left the receiver running; a returning source was not a new baseline; a failed level was never retried; MIDI path checks (ASCII digits, character device, no links) and a non-OSError killing the reader.
- Not fixed, by choice: sACN sequence numbers are ignored (documented).
- Never run against a real console, network or USB controller.

Open:
- The image build had not finished when this was written.
- DMX and MIDI need a test with real gear (console or QLC+ on a laptop; a USB pad controller on a Pi, with the service user in the `audio` group).

## 2026-09-29 (streams)

Done:
- Merged the weekly schedule (#13).
- Added the Streams module (SRT, RTSP, RTMP): `pvj/streams.py`, `/api/streams`, `{"stream": id}` on `/api/play`, System > Streams card, settings schema 4, `pvj/STREAMS.md`, tests. See D16.
- Never played a real stream. mpv is not installed on the dev Mac, so nothing here ran against mpv; CI runs the browser test with a headless mpv.

Open:
- First image build was still running when this was written.
- Streams need a test with a real SRT/RTSP source on a Pi.

## 2026-09-29 (scheduler)

Done:
- Dispatched the first image build (`image.yml`) by hand; result in the next entry or the Actions tab.
- Added the weekly schedule module (`pvj/scheduler.py`, `/api/schedule`, System > Schedule card, settings schema 3 with a migration, `pvj/SCHEDULE.md`), with unit tests on a fake clock and API tests. See D15.
- The browser test has a new schedule step. Playwright is not installed on the dev Mac, so that step has only been syntax-checked locally; CI runs it.
- On macOS, 10 `tests/test_update.py` tests fail with `mv: illegal option -- T` (GNU only). They fail the same way on master; they pass on Linux CI.

Open:
- Scheduler not run on real hardware or across a daylight-saving change.

## 2026-09-29 (later)

Done:
- Confirmed the local `docs/html/_images/01_Hdmi_connect.jpg` deletion was a side effect of #9 on a case-insensitive disk; restored it from git, tree clean. The file is still referenced by `docs/html/01_first_steps.html`.
- Confirmed the `legacy-v3` tag is on GitHub at the right commit.

Open:
- Deleting the 11 merged remote branches was blocked by the permission classifier; the owner should delete them (all 11 PRs are merged).
- Still nothing verified on real hardware; the image has never been built.

## 2026-09-29

Done:
- Merged: #4 (phase 4 core), #5 (Library: upload, rename, delete), #6 (wired Network settings, beta), #7 (copyright holder NXLX.Systems), #8 (rename to nxlx.mastercontrol), #9 (removed a case-colliding image).
- Repository renamed to `nxlx.mastercontrol` by the owner.
- Fixed a panel bug found by CI: redraws wiped the Network form; upload errors were wiped by a redraw; added an inline favicon so the browser test sees no 404.
- Added the device test (`tools/device-test.sh`, manual-only runner workflow, setup guide) in #10, and this log and hand-off notes in #11 (both open when written).

Open:
- `legacy-v3` tag not on GitHub (push refused for the session); recreate from commit `ed74df411b88b1a16dd80eecf52c3c9cf6d7768b`.
- Merged branches on GitHub can be deleted by the owner.
- Nothing verified on real hardware. Next useful step: run `tools/device-test.sh` on a Pi 4, then try the Network module on a box that can be reached another way.
- Unbuilt: crossfade, Wi-Fi/hotspot/VLAN, network updates, panel update button, Inputs/NDI/SRT/Dante/ST 2110 screens, mapper, presenter, wall, scheduler, MIDI/DMX. The image has never been built.
