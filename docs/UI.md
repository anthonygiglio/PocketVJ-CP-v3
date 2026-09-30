<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# The panel

The pictures on this page are made by `tests/ui/screenshots.js`, which drives the real panel against the same test harness as the browser test: a headless player, two-second test clips and a **fake network helper**. So the clip names, the addresses (`192.168.1.9`, `192.168.0.40`) and the network ports shown are made up for the picture; nothing here was taken from a real board. Regenerate them with:

```
SHOTS=docs/images/ui node tests/ui/screenshots.js     # needs playwright, Chromium and mpv, like tests/ui/panel.test.js
```

The CI job "panel-ui" also runs it and uploads the result as the `ui-screenshots` artifact, so a change that breaks a screen shows up there.

## Pairing

Enter the four digit PIN shown on the box (`sudo pvj-pin`, or the projector test screen). A device is remembered until it is removed.

![Connect screen with a four digit PIN](images/ui/connect.png)

## Live

Twelve pads per bank, three banks. The pad that is playing is lit. Fade out, Freeze and Blackout are always at the bottom.

![Live screen with six labelled pads and one playing](images/ui/live.png)

The wide layout on a laptop or tablet, and one of the four themes (Dark stage is the default; Night red keeps a dark room dark):

![Live screen at desktop width](images/ui/live-desktop.png)
![Live screen in the Night red theme](images/ui/live-night-red.png)

## Mix

![Mix screen: sliders, transition, rotate](images/ui/mix.png)

## Media

Upload from the phone, play, rename or delete. Only full-access devices can change files.

![Media screen with uploaded clips](images/ui/media.png)

## System

![Vitals card](images/ui/system-vitals.png)

Modules are switched on here. Beta modules are off until you turn them on; modules that are not built yet say so.

![Modules card](images/ui/system-modules.png)

### Beta modules

| Weekly schedule (see [SCHEDULE.md](../pvj/SCHEDULE.md)) | Streams (see [STREAMS.md](../pvj/STREAMS.md)) |
| --- | --- |
| ![Schedule card](images/ui/schedule.png) | ![Streams card](images/ui/streams.png) |

| DMX (see [DMX.md](../pvj/DMX.md)) | MIDI (see [MIDI.md](../pvj/MIDI.md)) |
| --- | --- |
| ![DMX card](images/ui/dmx.png) | ![MIDI card](images/ui/midi.png) |

Network settings (wired) always revert by themselves unless you confirm them. See [NETWORK.md](../pvj/NETWORK.md).

![Network card](images/ui/network.png)

### Control, appearance and access

| OSC (see [OSC.md](../pvj/OSC.md)) | Appearance | Access |
| --- | --- | --- |
| ![OSC card](images/ui/control-osc.png) | ![Appearance card](images/ui/appearance.png) | ![Access card](images/ui/access.png) |
