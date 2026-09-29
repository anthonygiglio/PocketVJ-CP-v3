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
