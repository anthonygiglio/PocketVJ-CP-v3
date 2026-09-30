<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# MIDI controllers (beta)

Play pads, fade and mix from USB MIDI controllers: pad grids, fader boxes, keyboards. Switch on **MIDI controller (USB)** under System > Modules, then use System > MIDI controllers (full-access devices only). Off until you turn it on.

**Every controller that is plugged in is read at once**, and a controller unplugged and replugged is picked up again within a couple of seconds. Nothing is written back to a controller (no lights or motor faders yet).

## Learn: assign a control to an action

1. Choose the action (and the pad, for "Play a pad").
2. Tap **Learn a control**, then move or press the control on any controller (you have 20 seconds). Nothing runs while it is listening.
3. It is saved and shown in the list, for example `nanoKONTROL2 · CC 0 → Opacity (fader)`.

A mapping belongs to one controller (by its ALSA card id, such as `nanoKONTROL2`, `Mix` or `Mini`, which stays the same when USB numbering changes between boots). Learning the same control again replaces its mapping. Up to 200 mappings. Your mappings are checked before the built-in map, so they win.

## Actions

| Action | Kind | Does |
| --- | --- | --- |
| Play a pad | trigger | Plays that pad (bank A to C, pads 1 to 12) |
| Stop, Pause / resume, Blackout on / off, Fade out, Reset mix | trigger | As in the panel |
| Opacity, Size, Position X, Speed, Volume | level | Follows the control, 0 to 127 spread over the range (opacity 0 to 100 percent, size 1 to 200, position -100 to 100, speed 0.25x to 2x, volume 0 to 100) |
| Blackout while held up | level | Black at 64 or more, shown below |

A trigger fires once per press (a note-on, or a CC that goes from below 64 to 64 or more), not on release or repeat. A fader sweep is thinned to 20 changes a second and the last position always lands.

## Built-in map

On unless you turn it off (System > MIDI controllers > Built-in map). It exists so a plain pad controller works with no setup: notes 36 to 71 (and program changes 0 to 35) play pads 1 to 36 (A is 1 to 12, B 13 to 24, C 25 to 36); notes 72 to 76 are stop, pause, blackout, fade out and reset; CC 20 to 24 are opacity, size, position, speed and volume; CC 25 is blackout while up. Real controllers rarely use these numbers (a Novation Launchpad Mini sends notes 20 to 103 and CC 104 to 111), so expect to learn your own.

## Safety

- Only paths of the form `/dev/snd/midiC<n>D<n>` are ever opened, and only if they are character devices (no links).
- Only the actions in the table are reachable: nothing shuts down, reboots or changes settings.
- At most 50 commands a second reach the player, whatever the controllers send.
- The web service reads the device through systemd: it needs the `audio` group and read access to ALSA devices, and the unit has both (`DeviceAllow=char-alsa r`).

## Not built yet

- **Controller profiles** (a ready-made layout for a known controller, like Ableton's control-surface scripts): the learn map is the base for them; a profile would be a shipped file of mappings for a named controller.
- Lights and motor-fader feedback to the controller (needs MIDI output).
- Mapping to pads by name, banks that follow the controller's own bank buttons, and relative (endless) encoders.

## Verified, and not

Verified on a real Raspberry Pi 4 (2026-09-30): the module reads a controller through the systemd sandbox and handled 112 messages in a few seconds from a Launchpad Mini. Three controllers (Korg nanoKONTROL2, Akai MIDI Mix, Novation Launchpad Mini) enumerate. **Learn, the multi-controller hub and the new map have only run against pipes standing in for controllers and the browser test, not yet against the real hardware.**
