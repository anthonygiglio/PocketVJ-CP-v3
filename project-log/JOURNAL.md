<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Journal

Newest entry first. One entry per working session: what was done, what merged, what is open.

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
