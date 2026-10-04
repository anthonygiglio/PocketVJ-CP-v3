<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Panel UX proposal (a click-through prototype)

A proposal for the panel's navigation and its everyday screens, built as one page you can use like the real panel on a phone. It is a dev tool: nothing under `pvj/`, `bin/` or `install/` uses it, and it is never installed on a box (the same rule as the design playground, D34). **Nothing in it is decided.** It exists so the owner can try the ideas before any of them touch `pvj/web/app.js`.

Published copy: https://claude.ai/artifact/NwPErwtYzJb6WTaAj9UqKj (private to the owner until shared).

## What is in it

- **Current / Proposed** switch at the top. "Current" is today's panel rebuilt from `pvj/web/app.js` (same order, same words; the contents of the System cards are shortened). "Proposed" is the new design. Both run on the same pretend box, so a module switched on in one is on in the other.
- **Who is holding the phone**: owner, staff (presenter), guest, or a first visit (the pairing screen). In the proposal each role gets its own set of tabs.
- **Screens**: pairing, Room (a placeholder that only reserves the first tab; the real one is built on the branch `room-scenes`), Live, Mix, Media, and System with one page per module.
- **Things that work**: tabs, playing a pad or a clip, the clock counting down, Blackout and Fade with their way back, ambience (Vibes) on and off, editing a pad, the upload flow (progress, done, failed, try again), copy from USB, putting a clip on a pad from Media, rename and delete, module switches with a first-run step, the find box on System, Close the room, and the four real themes.
- **What changed and why**, beside the phone (below it on a phone), System first.
- **Notes per screen**. On the published page they are saved with the page (its `notes` collection, one document per screen), where a later session can read them. Opened from disk the notes box is shown but cannot save.

All names, sizes, addresses and codes are examples. Codes for the pairing screen: `123456` joins as a presenter, `654321` as a guest, any four digits pair as the owner.

## How it is made

One hand-written file, `index.html`: plain JavaScript, no build step, `textContent` only, with the panel's own `h()` helper, its seven theme tokens (`pvj/themes.d`) and its class names (`.card`, `.btn`, `.pad`, `.tabs`, `.list`, `.picker` and so on). The proposed styles are grouped under "proposed additions" in the style block, written so they could be appended to `app.css`. The pretend box is the object `B`; modules are the table `MODS`.

The file is in the shape the page publisher expects: it starts at `<title>`, with no doctype, `<html>`, `<head>` or `<body>`. To open it from disk, wrap it first:

```
( printf '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'; cat index.html ) > /tmp/ux-proposal.html
```

When `pvj/web/app.js` or `app.css` changes, the "Current" side drifts, like the playground does. It is a snapshot of 2026-10-03 (master at 0793d2a).
