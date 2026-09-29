<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Testing on a real board

Everything so far was tested in a container. This lets a real Raspberry Pi (or mini PC) run the tests and send the results back, so a claude session or you can read them.

## What runs

`tools/device-test.sh` collects board details (model, OS, mpv version, temperature, throttling), runs `pvj-selftest` (with a test pattern on the display if you ask), and runs the unit tests with the real mpv. It never changes the network, never installs anything and needs no root. Output goes to a `device-report` folder.

## Option A: run it by hand (no GitHub setup)

On the Pi, in a clone of the repo:

```
tools/device-test.sh            # add PVJ_DEVICE_PLAY=1 in front to also play a test pattern
```

Send me `device-report/system.txt`, `selftest.txt` and the last lines of `unittest.txt`.

## Option B: the Pi as a GitHub Actions runner (I can then start tests myself)

The Pi connects out to GitHub over HTTPS; nothing is opened on your network.

1. Use a **dedicated Pi** with Raspberry Pi OS Lite (64-bit), `git`, `python3` and `mpv` installed. Do not use the Pi that runs your shows.
2. Make an unprivileged user for it, for example `sudo adduser --disabled-password runner`.
3. On GitHub: **Settings, Actions, Runners, New self-hosted runner**, pick Linux and ARM64 (or x64), and follow the commands it shows as the `runner` user. When it asks for labels, add `pvj-device`.
4. Install it as a service: `sudo ./svc.sh install runner` then `sudo ./svc.sh start` (run in the runner folder).
5. On GitHub: **Actions, Device test, Run workflow**. Tick "play" if a screen is attached. The report appears as the `device-report` artifact.

## Safety

- The workflow starts by **manual dispatch only**, and only for the repository owner. It never runs for pushes, pull requests or forks, so nobody else's code reaches the Pi. Keep it that way.
- Under **Settings, Actions, General**, set "Fork pull request workflows" to require approval, and keep the runner group limited to this repository.
- The runner user has no sudo. Anything that needs root (the network helper, USB mounting) is not exercised by this workflow.
- Network tests are deliberately not included: a bad change could cut the runner off. Test the Network module by hand, with a monitor and keyboard on the Pi.
- If the Pi is a shared or public-facing machine, do not register it.
