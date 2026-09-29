# pvj: platform layer (Phase 2, work in progress)

Python 3, standard library only. Replaces omxplayer, D-Bus, `tvservice` and `vcgencmd` with mpv and `/sys`.

| Piece | What it does |
| --- | --- |
| `bin/pvj-player` | One command for playback: `play`, `stop`, `pause`, `seek`, `speed`, `volume`, `opacity`, `size`, `position`, `status`, `info` |
| `pvj/hardware.py` | Board, OS, temperatures and display modes from `/proc` and `/sys` (Pi 3, 4, 5, x86, other ARM) |
| `pvj/player.py` | One long-lived mpv controlled over its JSON IPC socket. Changing clips is `loadfile`, so no black gap |
| `bin/pvj-selftest` | Run on each device; prints a JSON report to send back. `--play` also plays a test pattern on the real screen |

## Old command to new command

| Legacy (`dbuscontrol.sh`, `omxplayer`) | New |
| --- | --- |
| `omxplayer --loop file` | `pvj-player play file` |
| `omxplayer file` (once) | `pvj-player play --once file` |
| `dbuscontrol.sh pause` | `pvj-player pause` |
| `dbuscontrol.sh seek <us>` | `pvj-player seek <seconds>` |
| `dbuscontrol.sh rate <x>` | `pvj-player speed <x>` |
| `dbuscontrol.sh setalpha <0-255>` | `pvj-player opacity <0-255>` |
| `omxsizetocenter <percent>` | `pvj-player size <percent>` |
| `omxXposition <x>` | `pvj-player position <x>` |
| `stopall` (player part) | `pvj-player stop` |

## Behaviour differences to know about

- **Opacity** fades to black. mpv cannot blend against other layers, which is right on a black stage background.
- **Position** is in thousandths of the picture width, not omxplayer pixels.
- **Display output**: on a desktop session mpv uses the default output; on a bare console it uses DRM/KMS directly.
- **Pi 5** has no hardware H.264 decode; use HEVC files or expect software decoding.

## Testing

```
python3 -m unittest discover -s tests -t .      # needs mpv; the tests run it headless
bin/pvj-selftest --play --json report.json       # on a real device
```

Not yet done: the PHP panel and OSC do not call `pvj-player` yet, and the audio, overlay and mapper paths are still legacy.

## Legacy start scripts

`pvj-player start startlessonce05` runs a legacy preset by its old script name. One table in `pvj/presets.py` replaces ~290 scripts (`startless`, `startseamless`, `startlessonce`, `startlesseronce`, `startmaster`, `startmasterone`, `startmasterusb`, each with an optional two-digit index). `tests/test_presets.py` checks that table against every real script in `sync/`; it matches all but two upstream defects:

- `startless` (no number) is a wrapper that loops every file; the preset does the same.
- `startmaster95` plays `65*` upstream (copy-paste error); the preset plays `95*`.

Not ported yet: slave, stream and wifi presets, network sync between boxes (master presets play locally and print a warning), the "play a slideshow after the video" option, and the audio output flag (`local`, `both`); Pi 5 has no headphone jack, so audio selection needs its own module.

## Running as a service

`install/pvj-player.service` runs `pvj-player serve` under systemd as the account that owns the screen and sound card (`@PVJ_USER@`, `@PVJ_DIR@` are filled in by the installer, which is not written yet). It restarts the player if it dies, and puts the control socket in `/run/pvj`, group-writable for the `pvj` group. The web panel and OSC then run as their own users, join that group, and use `pvj-player play --no-spawn ...` (or the socket directly). A world-accessible runtime directory is refused.

Not verified on a real device: the unit passes `systemd-analyze verify` here, but restart-on-crash, the group permissions and DRM access need a test on a Pi.

## USB drives

`pvj-usb` (run by `pvj-usb@<partition>.service`, started from `install/99-pvj-usb.rules`) mounts each USB partition under `/media/pvj/<label>` and points `/media/usb` at the most recent one, which is what the old `startmasterusb` presets expect. Pulling the drive stops the unit, which unmounts it.

- Read-only by default, with `nosuid,nodev,noexec`; set `PVJ_USB_RW=1` in `/etc/pvj/pvj.env` to allow writing. A drive pulled mid-write is the usual way to corrupt one at a gig.
- Filesystems: vfat, exfat, ext2/3/4, ntfs (kernel `ntfs3` driver). Anything else is refused.
- Labels are cleaned to `a-z 0-9 . _ -`; two drives with the same label get `-2`, `-3`.
- It never touches a disk that also holds the running system, so a mini PC booted from a USB SSD keeps its own disk safe. The old backend mounted `/dev/sda` blindly.
- `/media/usb` is only replaced if it is already a link; a real folder there (legacy image) is left alone.

Tested with a fake `blkid`/`mount` (label cleaning, options, collisions, refusal cases, unmount). Not tested: a real udev event, real filesystems, or the udev rule itself (`udevadm` was not available to verify its syntax).
