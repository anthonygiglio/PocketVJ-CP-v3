<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Lessons learned

Append new lessons at the bottom. Format: what happened, what we do now.

- **An archive extractor stripped `../evil` as if it were a top folder.** Found by a test. Validate the raw member name before stripping anything.
- **A rotation of 45 degrees was accepted, then the panel reported "player down".** Validate values at the API (only 0, 90, 180, 270), not only in the player.
- **The server dropped connections on unexpected exceptions.** Added a catch-all 500, and made responses tolerate a client that vanished.
- **Compiled `.pyc` files got committed.** Untracked, and a test now guards against it.
- **The installer's uninstall crashed with an empty root variable.** Found only by running the installer for real in a container. Prefix safety guards added. Run installers for real, not only through ShellCheck.
- **Independent reviews found real defects in every risky feature** (the update path, uploads, the network helper): overwrite races, temp-file leaks, state kept in a directory other users could write, an undo that could fail silently. Keep doing a separate, read-only review for anything touching root, the network, uploads or auth, and turn each finding into a test.
- **The browser test caught real UI bugs**: a helper that did not flatten arrays, a long preview widening the page on phones, a sticky tab bar covering controls, a card calling the API while its module was off.
- **A redraw of the System screen wiped the Network form.** It also made the browser test flaky on slower CI runners. The card now keeps its state. When a UI test fails only in CI, look for a redraw or load race before touching assertions.
- **Link checking failed CI on dead legacy hotlinks.** The link check is advisory and excludes known-dead hosts.
- **A cloud session cannot push tags or delete branches** (HTTP 403 from the proxy). Do those from a normal clone or on github.com.
- **A session's GitHub access is fixed to the repo name it started with.** After the rename, start new sessions on the new name; an old session still reaches it through GitHub's redirect.
- **Two legacy images differed only by letter case**, which broke clones on macOS and Windows. Removed the unused one.
- **When the shell tool went down mid-work**, files were backed up to a throwaway branch through the GitHub API before resuming. Commit and push early.
- **Claims must match what was run.** Everything so far ran in a container with a real headless mpv and fakes. Nothing has run on a real Pi, display, USB stick, TouchOSC or NetworkManager. Say so in every summary until that changes.
- **On macOS, pulling the case-collision fix left the surviving image showing as deleted.** #9 removed `01_hdmi_connect.jpg`; on a case-insensitive disk that also removed the file on disk that `01_Hdmi_connect.jpg` maps to. The tracked file was fine; `git checkout -- <path>` restored it. If `git status` shows a lone deletion after such a pull, check for a case-variant removal before assuming damage.
- **`re.match` with a `$` anchor accepts a trailing newline.** A MIDI device path and a schedule time both passed with `\n` on the end. Use `re.fullmatch` for every validation of untrusted text; a test that appends `\n` to a good value catches it.
- **A per-source rate limit means nothing for UDP from private addresses, because the source can be forged and the limiter clears its table when full.** Put a global packet cap and a cap on calls into the player next to it. Also: `apply()` methods that start threads or sockets need one lock, and turning a module off must stop what it started.
- **Read the check list before merging, not only the merge command's exit.** A `while ... pending` wait loop ends on any result, including a failure. Count failures explicitly and stop if there is one.
- **A sandboxed service can silently lose a device the code needs.** MIDI worked in tests and would have failed on the image because the unit hid `/dev`. When a module needs a device, network port or group, check the systemd unit, not only the Python.

