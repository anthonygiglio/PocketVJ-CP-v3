<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# MIDI controller (beta)

Play pads, fade and mix from a class-compliant USB MIDI pad controller, keyboard or fader box. Switch on **MIDI controller (USB)** under System > Modules, then pick the device under System > MIDI controller. Off until you turn it on. Full-access devices only.

The box reads the raw ALSA device `/dev/snd/midiC<card>D<device>`: no MIDI libraries. Only paths of that form can be chosen, and the box opens only a real character device (no links). The web service (`pvj-web`) is in the `audio` group and its systemd unit allows read access to ALSA devices only (`DeviceAllow=char-alsa r`); a hand-made install needs the same. Unplug and replug is fine: the box keeps looking for the controller and reconnects. Receive only; nothing is written to the device.

## Mapping

| Message | Does |
| --- | --- |
| Note on 36 to 71 | Play pads 1 to 36 (A is 1 to 12, B 13 to 24, C 25 to 36) |
| Note on 72 | Stop |
| Note on 73 | Pause / resume |
| Note on 74 | Blackout on / off |
| Note on 75 | Fade out (2 s) |
| Note on 76 | Reset mix |
| Program change 0 to 35 | Play pads 1 to 36 |
| CC 20 | Opacity, 0 to 127 is 0 to 100 percent |
| CC 21 | Size, 1 to 200 percent |
| CC 22 | Position X, -100 to 100 |
| CC 23 | Speed, 0.25x to 2x |
| CC 24 | Volume, 0 to 100 |
| CC 25 | Blackout, 64 and up is on |

Note off, aftertouch, pitch bend, system exclusive and everything else are ignored. Choose a channel (1 to 16) or all channels. A fader sweep is thinned to 20 changes a second, and the final position always lands. At most 50 commands a second reach the player, so a faulty controller cannot flood it. Turning the module off in System stops the reader.

## Not verified

Tested with byte streams through a pipe. **Never run against a real USB controller or a real `/dev/snd/midi*` device**, on any board. Custom mappings ("MIDI learn") are not built; the table above is fixed.
