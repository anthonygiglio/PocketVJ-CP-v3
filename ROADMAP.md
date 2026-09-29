## nxlx.mastercontrol roadmap

Legacy v3 items are kept below the line for reference.

| Phase | Scope |
| --- | --- |
| 0 | Fork housekeeping: README, legacy tag, Pages fix, CI |
| 1 | Security hotfix on the old line (3.9.x): input casts and quoting, narrow sudoers, auth, POST plus tokens, pairing PIN |
| 2 | Platform layer: `pvj-player` on mpv, hardware detection, display layer, Python 3, gpiozero, no hardcoded `/home/pi` |
| 3 | Installer, systemd services, watchdog, USB automount, images built in CI, binaries out of git |
| 4 | New core in Python 3: module manifests, versioned settings and migrations, themes, update and rollback |
| 5 | Feature parity with the 12 legacy tabs |
| 6 | Mapper spike and network inputs (see below) |
| 7 | Themes, manual, migration tool, hardware test matrix, 4.0 |

### Network input modules (all optional, each updatable on its own)

| Module | Notes |
| --- | --- |
| NDI | First-class. Free proprietary runtime, fetched and kept current by the module updater. Main route for Resolume and MadMapper output. |
| SRT, RTSP, RTMP | Fully open. Via ffmpeg or mpv. |
| AES67 / Dante audio | Dante devices interoperate through AES67 mode. Native Dante on Linux needs the community Inferno project or a Dante hardware card; to be evaluated. |
| SMPTE ST 2110 | Needs PTP time sync and a capable NIC, so x86 only. Candidates: Intel Media Transport Library, GStreamer. Not for Pi. |

### Legacy v3 list (upstream, 2022)

 <br />

- add rescue script similar to exhibition <br />
- fix the overlay edge case, needed? <br />
- play all video files random, integrated in custom01  <br />
- mapping without mouse, adopt it from exhibition so it will work too <br />
- remove the underline from play video 01_* so its more userfriendly  <br />
<br />
- bring the audio quality fixes from exhibition to the rtc as well!<br />
- change build pipeline to build docs into cp and to website <br />
- feedback in cp when moving speed or opacity slider <br />
- rewrite update script to force remove content in ofxPiMapper/example/bin/data/sources/videos, recheck to mapper update process! <br />
- port the new pjlink commands from Exhibition <br />
- moving big steps for mapper witout mouse or create faders <br />
- prevent bootloops when pusing reboot and browser reconnects, solve with closing the browser tab <br />
- add omxplayer error output when setting to alsa/usb audio but no soundcard is recognized <br />
- Update the osc_control.js (startlesser scripts) <br />
- playing videos once does not give feedback in CP, add feedbacks inbackend or in scripts <br />

### Tutorials: <br />

- Show how to use mapper without mouse  <br />
- All autostart functions  <br />



=======================<br />

Opensource rocks! <br />
©2022 marc-andré gasser

