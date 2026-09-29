<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Notes for claude sessions

Read first: [HANDOFF.md](HANDOFF.md), then [project-log/](project-log/README.md). The decisions, lessons and open items are there.

- Log as you go: a lasting decision goes in `project-log/DECISIONS.md`, a surprise in `LESSONS.md`, and add a dated entry to `JOURNAL.md` at the end of a session. Do it in the same pull request as the change.
- Write plain text with no em dashes (use commas, semicolons or new sentences).
- New code is Apache-2.0 with an SPDX header ("NXLX.Systems and contributors"). Never edit `LICENSE.md` or `AUTHORS.md`.
- One pull request per finished branch; short comment, merge when checks are green. Do not rewrite history.
- Ask before destructive or outward actions. Report outcomes faithfully, and never claim hardware behaviour that was not tested on hardware.
- Tests: `python3 -m unittest discover -s tests`; browser test `node tests/ui/panel.test.js`.
