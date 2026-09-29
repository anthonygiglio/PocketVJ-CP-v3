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
