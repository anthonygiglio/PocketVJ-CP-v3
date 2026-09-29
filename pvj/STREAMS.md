<!-- SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
     SPDX-License-Identifier: Apache-2.0 -->
# Streams: SRT, RTSP, RTMP (beta)

Save network stream addresses and play them like clips. Switch on **Streams: SRT, RTSP, RTMP** under System > Modules, then use System > Streams. Full-access devices add and remove streams; live devices can play them; view devices see the list.

## Safety

- Only `srt://`, `rtsp://`, `rtsps://`, `rtmp://` and `rtmps://` addresses are accepted. mpv understands many more (`file://`, `edl://`, `lavf://`, `ytdl://`), some of which read local files or run helper programs, so nothing else is ever passed on.
- The play call takes a saved stream's id, never a free-form address.
- A login inside an address (`rtsp://user:pass@camera/live`) is stored in the settings file (mode 0600) and shown as `***` in the list, the player status and "Now playing". The panel can only add or remove a stream, so a hidden password is never sent back and forth.
- The box will connect to any host a full-access device saves. Keep the show network private.

## Not verified

- Tested with a fake player: address checks, hiding of logins, roles and the API. **Never played a real stream**, on any board. Whether a given mpv build has SRT and RTMP support (it needs ffmpeg with those protocols) is unchecked; Raspberry Pi OS builds should, but test it.
- Latency, reconnect after a dropped stream and behaviour when the source is down are not handled yet: a stream that cannot open leaves the player idle.
- Not built: NDI, AES67/Dante and ST 2110 (separate modules), streams as pads, and scheduling a stream.
