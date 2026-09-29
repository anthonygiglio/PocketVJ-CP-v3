# NXLX PocketVJ

A modernization of PocketVJ CP v3, a control panel for playing, mixing and mapping video on small computers. It is built for artists who run visuals at gigs: raves, concerts and installations.

> **Status: work in progress.** The `master` branch still holds the legacy v3 code, which targets Raspberry Pi 3B+ on Raspbian Jessie. The state before the fork work began is tagged `legacy-v3`. Do not expose the legacy code to an untrusted network; see [SECURITY.md](SECURITY.md).

## Goals

- Run on Raspberry Pi 3B, 4 and 5, small x86 mini PCs, and old recycled computers.
- Run on current systems: Raspberry Pi OS Bookworm and Trixie, Debian and Ubuntu on x86.
- Be rugged at gigs. The preferred setup is a direct Ethernet link on a private network; Wi-Fi is optional, for the phone only.
- Take video from the network: NDI, SRT, RTSP and RTMP, plus AES67/Dante audio and SMPTE ST 2110 on capable hardware. Each is an optional, updatable module. See [ROADMAP.md](ROADMAP.md).

## Layout

| Path | What it is |
| --- | --- |
| `backend.php`, `index.html`, `sync/`, ... | Legacy v3 code, kept until the new core replaces it |
| `docs/` | Legacy manual and notes |
| `ROADMAP.md` | Phased plan for the modernization |
| `NOTICE.md`, `THIRD_PARTY_LICENSES.md` | Credits and licensing of bundled parts |

## Licence and credits

New code in this fork is licensed under the Apache License 2.0; the legacy code keeps its original licence. See [NOTICE.md](NOTICE.md) for exactly which is which.

This is a fork of [PocketVJ CP v3](https://github.com/magdesign/PocketVJ-CP-v3) by Marc-André Gasser (magdesign) and contributors. The original [LICENSE.md](LICENSE.md) and [AUTHORS.md](AUTHORS.md) are kept unchanged. See [NOTICE.md](NOTICE.md).
