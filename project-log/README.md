<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Project log

Where the project remembers itself, so a new person or a new claude session can pick up cold, and so you can look back and see why something is the way it is.

| File | What goes in it | How |
| --- | --- | --- |
| [DECISIONS.md](DECISIONS.md) | A choice with lasting effect: what, why, what it costs, what it rules out | Add a numbered entry; never edit an old one, add a new entry that supersedes it |
| [LESSONS.md](LESSONS.md) | Something that went wrong or surprised us, and what to do next time | Append |
| [JOURNAL.md](JOURNAL.md) | Dated record of what was done, what merged, what is open | Append one entry per working session, newest at the top |
| [../HANDOFF.md](../HANDOFF.md) | Current state in one page | Keep it true; rewrite it when things change |

Rules of thumb:

- Log the reason, not just the fact. "Chose X" is useless in six months; "Chose X because Y, at the cost of Z" is not.
- Record what was **not** verified as loudly as what was. Say "not run on a real board" when that is the case.
- Write it in the same pull request as the change when you can. A decision made in chat and not logged is lost when the session ends.
- Plain language, no em dashes.
