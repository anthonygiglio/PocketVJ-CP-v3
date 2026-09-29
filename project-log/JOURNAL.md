<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Journal

Newest entry first. One entry per working session: what was done, what merged, what is open.

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
