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
