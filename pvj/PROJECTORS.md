<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Projector control (PJLink)

The old panel had Beamer On and Beamer Off buttons. This does the same over the network with **PJLink**, the standard most network projectors speak (Epson, Panasonic, NEC, Sony, Hitachi, Christie, Optoma, Canon, BenQ and others). It is usually switched off in the projector's own network menu; switch it on there and note the password, if any.

## In the panel

Switch the **Projector control** module on under System > Modules (beta, off by default), then use System > Projectors. A full-access device adds a projector (a name, its IP address or name, the port, 4352 unless changed, and the PJLink password if one is set) and removes it. A presenter (play and mix) or full-access device can switch each projector on or off, mute or unmute its picture, and check its power state. With more than one projector there are also All on and All off buttons. A guest sees the list only.

Switching on takes the projector a minute or so to warm up, and switching off starts a cool-down; during those the projector may refuse other commands ("busy").

## Schedule and OSC

- The weekly schedule can switch every projector on or off (`projector_on`, `projector_off`), and can run a legacy start script such as `startlessonce05` (`preset`).
- The old OSC addresses `/beameron` and `/beameroff` switch every projector on or off (on press).
- From OSC and the schedule the commands are sent in the background, so a projector that is off the network never delays the next cue or entry. A failure is written to the log (OSC) or shown under the entry's "Last run" (schedule).

## Safety

- **Private networks only.** A projector must be on a private or link-local address (10.x, 172.16 to 31.x, 192.168.x, 169.254.x, or the IPv6 equivalents); a name must resolve to one. It is checked when the projector is added and again before every command, so the box cannot be used to reach the internet. Loopback and the cloud metadata address 169.254.169.254 are refused.
- **Passwords** are stored in the box's settings file (readable by the panel service only) and never sent back to the panel or the API; the list shows only "password set". PJLink sends an MD5 digest of the projector's random number and the password, never the password itself. PJLink security is weak by design, so keep the show network private.
- Each command (connect, greeting and answer) must finish within 10 seconds. Looking up a name is not bounded by the box, so use an IP address for a projector if you can. All projectors are asked at the same time, and the panel waits at most 30 seconds in all, so an unplugged projector does not hold up the others.
- One command at a time per projector: many projectors accept only one connection, so a second button press waits its turn instead of getting "busy".
- At most 8 projectors.

## API

- `GET /api/projectors` (view): `{"projectors": [{"id", "name", "host", "port", "has_password"}]}`.
- `POST /api/projectors` (full): `{"add": {"name", "host", "port"?, "password"?}}` or `{"remove": id}`.
- `POST /api/projector` (live): `{"id": id or "all", "action": "on|off|mute|unmute|state", "background"?: true}`. With `background` the answer is `{"started": true}` at once. Answers `{"results": {id: {"ok", "power"?, "error"?}}}`; a single projector that does not answer gives 502 with the reason.

## Not verified on real hardware

**No real projector has been tested.** The protocol code is tested against a small fake PJLink class 1 projector (with and without a password, a wrong password, an unknown command, a projector that is off the network, a slow sender, a device that is not a projector, and six commands at once to one projector). Class 2 features (input switching by name, lamp hours, filter) are not used.


## Plan: a general PJLink control system (D36)

Only the PJLink standard, so it works with any brand. Command details to be checked against the published PJLink specification before building; nothing is claimed about real projectors until tested on one.

**Phase 1, class 1 (every PJLink projector).**
- Identify on add: name (`NAME ?`), maker (`INF1 ?`), model (`INF2 ?`), other info (`INFO ?`), class (`CLSS ?`), inputs (`INST ?`); shown in the panel.
- Input selection (`INPT`) from the projector's own list, with a friendly label per input ("Matrix", "Box"). A change while warming up is answered "busy"; retry for up to about 90 seconds, then say so plainly.
- Picture and sound mute separately (`AVMT 11/10`, `21/20`) and together (`31/30`).
- Health: lamp hours (`LAMP ?`) and the projector's warnings (`ERST ?`: fan, lamp, temperature, cover, filter, other) in the Health card.
- Background status every 30 to 60 seconds, staggered, so the panel shows on, off, warming up or cooling down without asking each time.

**Phase 2, class 2 (used only when `CLSS ?` says 2).**
- Volume up and down (`SVOL`; class 2 has steps, not a level).
- Freeze (`FREZ`), the projector's input names (`INNM ?`), the incoming resolution (`IRES ?`).
- "Find projectors": the class 2 search on the private network, to add one with a tap.
- Status notices sent by the projector, so the panel updates at once.

**Phase 3, the room.**
- Groups ("Main wall", "Painting wall", "All").
- Scenes: power, input, mute and volume per group, together with what the box plays.
- A Room screen for staff (presenter and guest codes): per group on or off, source, volume, All off.
- Projector actions from the schedule, OSC, MIDI and DMX.

**Safety and tests.** The rules above stay (private networks only, passwords never shown, one command at a time per projector, time limits). The fake projector grows a class 2 mode, with tests for every refusal and timeout. Epson's own protocol (exact volume levels) is a possible later add-on, not part of this plan.
