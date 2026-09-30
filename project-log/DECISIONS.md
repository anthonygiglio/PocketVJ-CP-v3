<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Decisions

Newest at the bottom. Format: **what**, **why**, **cost / rules out**. Never rewrite an entry; add a new one that supersedes it.

## D1. Python 3 (standard library only) replaces PHP for the new core
Why: the legacy PHP had serious security problems and targets an old OS; a small stdlib-only service installs anywhere and has nothing to keep patched. Cost: a rewrite; legacy PHP and docs stay in the tree until feature parity.

## D2. Fork without upstream permission; upstream credited, licence files untouched
Why: the upstream author is unreachable and the project is unmaintained. `LICENSE.md` and `AUTHORS.md` are never edited; the README and NOTICE credit the original. Cost: none technical; keep the credit visible.

## D3. New code is Apache-2.0; legacy code stays under the upstream licence
Why: a permissive licence with a patent grant suits an art and tools practice. Every new file has an SPDX header; `REUSE.toml` covers the rest. Copyright holder: "NXLX.Systems and contributors" (NXLX.Systems is the whole practice; NXLX is the VJ name). Rules out: relicensing legacy files.

## D4. Security hotfix first, before any new feature
Why: the legacy panel was exposed to command injection and similar. Phase 1 closed the worst holes before the rewrite. Cost: delayed features.

## D5. One long-lived mpv, controlled over JSON IPC, supervised by systemd
Why: gapless switching by `loadfile`, recovery by systemd, one process to reason about. Cost: no second player, so **no true crossfade**; only "Dip to black" and "Cut". Revisit if a crossfade is wanted (needs a second player).

## D6. The web panel is unprivileged; root work goes through a small helper
Why: a bug in the panel must not become root. The network helper (`pvj-netd`) listens on a socket in `/run/pvj` (group `pvj`), checks the caller, re-validates every request and runs only fixed commands. Cost: an extra service. Consequence: the panel cannot restart a wedged mpv.

## D7. Auth model: PIN pairing, per-device tokens, roles view / live / full
Why: simple at a gig, no passwords to type on a phone. Tokens are stored hashed; POST only with a custom header and Origin check (CSRF); strict CSP. Guest links are view-only by default.

## D8. Signed, rollback-able updates
Why: a bad update at a venue must be recoverable. Ed25519 signatures via `ssh-keygen -Y`, releases in versioned folders with a `current` link, health check and automatic rollback. Not built: fetching from the network or channels, and an update button in the panel.

## D9. NDI, SRT/RTSP/RTMP, AES67/Dante and ST 2110 are separate optional modules
Why: each has its own licence, dependencies and risk. Modules are declared by manifests in `pvj/modules.d`; unbuilt ones are listed as "Not built yet". Beta modules default to off.

## D10. Network changes are never final until confirmed
Why: changing the network of the box you control over that network can lock you out. Apply builds a separate candidate profile; Confirm swaps it in; timeout, failure, helper restart or reboot reverts. Undo retries until it works. Wired only for now (no Wi-Fi, hotspot or VLAN). **Never run against a real NetworkManager**, so it is beta and off by default.

## D11. Target platform: Raspberry Pi OS Trixie Lite, 64-bit, for the image
Why: current, supported, small. The installer also targets Bookworm and Debian/Ubuntu. The image has never been built.

## D12. Project name: nxlx.mastercontrol
Why: the old name came from the last upstream version. The `pvj` package, `pvj-*` services and commands and install paths keep their names (renaming them touches every import and existing install for no gain). The GitHub repository was renamed from `PocketVJ-CP-v3`.

## D13. Device tests run through a manual-only self-hosted runner, or by hand
Why: a cloud session cannot reach devices on a LAN. The workflow starts only by manual dispatch by the repository owner, never for pushes, pull requests or forks, so nobody else's code reaches the board. Network tests are excluded because they could cut the runner off. See `tools/DEVICE-TESTING.md`.

## D14. Working process: one PR per finished branch, short comment, merge when green
Why: keeps history readable and CI as the gate. No history rewriting. Risky features (root, network, uploads, auth) get an independent read-only review, with every finding fixed by a test that reproduces it.

## D15. The scheduler never replays missed events, and can only do four harmless things
Why: a Pi has no battery clock, so its time jumps when the network sets it; replaying "missed" entries after a jump could start clips at the wrong moment on a live stage. The scheduler fires only for minutes it watched (short stalls of up to two minutes are caught up) and can only play, stop, blackout or show. Cost: a box that was off at 18:00 does not start the 18:00 entry when it boots at 18:05; use an autostart preset for that. Beta and off by default, like the network module.

## D16. Streams accept only srt, rtsp, rtsps, rtmp and rtmps addresses, and play by saved id
Why: mpv opens many URL kinds (`file://`, `edl://`, `lavf://`, `ytdl://`), some of which read local files or run helpers, so an open URL box would be a way around the media folder rules. Logins in addresses are hidden everywhere and never round-tripped to the panel. Cost: no http/HLS/UDP streams for now, and no free-form "play this address" over OSC or the API.

## D17. DMX and MIDI input are stdlib-only, fixed maps, baseline first, and split into two modules
Why: D1 says the standard library only, so Art-Net and sACN are parsed by hand from UDP and MIDI is read from the raw ALSA device files (`/dev/snd/midiC*D*`), with no libraries to install or patch. Maps are fixed and documented (`pvj/DMX.md`, `pvj/MIDI.md`) so a show file never depends on hidden state. The first DMX frame only sets a baseline, so a console at zero cannot black out the screen; the box holds its last state when the signal stops. The device path is checked against a strict pattern, never an arbitrary file. The umbrella `control` manifest is replaced by `control-dmx` and `control-midi` (OSC has its own switch in settings). Cost: no MIDI learn or custom DMX layouts, no Art-Net discovery replies, no sACN priority merging, no MIDI output or feedback to controller lights.

## D18. Autostart runs at first sight of a player and after a player restart, never because playback stopped
Why: the goal is a box that recovers by itself after a power cut or an mpv crash, without fighting the operator. Watching the player's process id distinguishes "the player was restarted" (start again) from "someone pressed Stop" (leave it). It plays through the same API calls as the panel and OSC, so validation and the mix apply. Off by default. Cost: a box whose clip simply ends will not restart it (use loop, or the schedule); autostart from a USB drive appearing later is not covered.

## D19. The screen viewer is a shared screenshot, not a video stream
Why: the box draws with mpv straight to the display (DRM/KMS) with no desktop, so there is nothing to mirror. mpv can save its own output on request (`screenshot-to-file ... window`), which includes brightness, size and blackout. Measured on a Pi 4 at 2560x1440: about 0.7 s and 190 KB a frame. So the panel asks for one frame at a time (at most every 0.7 s, shared by every viewer) and only while someone has "Show screen" on. Any paired device may look, including view-only guests. Cost: about 1 frame a second, not live video; real-time video needs an HDMI capture device or a second encoder, which is a different design.

## D20. The screen viewer is a snapshot on request, not a live view (supersedes D19)
Why: D19 built a viewer that refreshed about once a second. Measured on a real Pi 4 playing 1080p H.264 to a 2560x1440 monitor: playback dropped 0.0 frames a second with no viewer, 4.7 a second with the continuous viewer, and still 1.3 a second with one snapshot every 5 seconds. The owner saw it as choppy playback and said to drop live view if it cannot run without impact. Each snapshot is a GPU readback of the whole window (about 0.7 s of work, about a quarter second of visible stall). So there is one snapshot per tap, nothing repeats by itself, the server allows at most one every 3 seconds, and a live picture is left to an HDMI capture device. Cost: no live preview on the panel.

