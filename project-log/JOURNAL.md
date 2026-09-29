<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Journal

Newest entry first. One entry per working session: what was done, what merged, what is open.

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
