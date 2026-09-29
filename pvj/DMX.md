<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# DMX over the network (beta)

Control the box from a lighting console or software (QLC+, Resolume, MadMapper, grandMA and others) over **Art-Net** (UDP 6454) or **sACN / E1.31** (UDP 5568). Switch on **DMX over the network** under System > Modules, then use System > DMX. Off until you turn it on. Full-access devices only.

Tell the console the box's address and universe; the box does not announce itself (no ArtPollReply, no discovery, nothing is ever sent back).

## Channels

Eight channels starting at the start address you choose (1 to 505):

| Ch | Does | Values |
| --- | --- | --- |
| 1 | Opacity | 0 to 255 is 0 to 100 percent |
| 2 | Size | 0 to 255 is 1 to 200 percent |
| 3 | Position X | 0 to 255 is -100 to 100 |
| 4 | Speed | 0 to 255 is 0.25x to 2x |
| 5 | Volume | 0 to 255 is 0 to 100 |
| 6 | Blackout | 128 and up is on |
| 7 | Pad | 0 to 5 idle; each pad owns six values: 6 to 11 is pad 1, 12 to 17 is pad 2, up to pad 36 (216 to 221). Bank A is pads 1 to 12, B 13 to 24, C 25 to 36. A pad plays when the channel moves into its range |
| 8 | Function | 50 to 99 stop, 100 to 149 pause, 150 to 199 resume, 200 to 255 fade out (2 s). 0 to 49 does nothing |

## Safety and behaviour

- Only private networks may send (loopback, 10/8, 172.16/12, 192.168/16, link-local and their IPv6 equivalents); add your show network under "Extra networks". Ranges wider than a /8 are refused. UDP sources can be forged, so this keeps the internet out, not a hostile device on your own network.
- **The first frame after turning it on only sets a baseline.** Nothing fires from it, so a console sitting at zero cannot black out the screen. The same happens after changing the settings.
- If the signal stops, the box holds its last state.
- Each level channel is applied at most 20 times a second; the next frame carries the change on.
- Only the actions in the table are reachable. Nothing that shuts down, reboots or reconfigures the box.
- Art-Net universe 0 to 32767; sACN universe 1 to 63999 (the box joins the multicast group `239.255.x.y` for its universe; unicast also works).

## Not verified

Tested with hand-built packets and a loopback UDP socket, never with a real console or on a real network. The sACN multicast join has not been tried on a real network. Merged or multiple sources (sACN priorities, merging two consoles) are not handled: the newest frame wins.
