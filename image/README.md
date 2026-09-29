# Image build (Raspberry Pi OS Lite, Trixie, 64-bit)

`.github/workflows/image.yml` builds `nxlx-pocketvj-*.img.xz` with [pi-gen](https://github.com/RPi-Distro/pi-gen) (`arm64` branch) and uploads it, with `SHA256SUMS`, as a workflow artifact. Run it from the Actions tab (it takes about an hour) or by pushing a `v*` tag.

The image works on Pi 3, 4 and 5 (all 64-bit capable). It contains Raspberry Pi OS Lite plus `mpv`, `python3` and this repository's `pvj/`, `bin/` and `install/`, installed with the offline installer, with `pvj-player.service` enabled. The web panel is **not** in the image yet.

- **No default password.** The first boot asks for a user name and password. SSH is off; enable it with `raspi-config` after setting a password.
- **Media** goes to `/var/lib/pvj/video`. Before turning on the read-only root (`sudo pvj-rootfs enable`), move media to a second disk or USB drive, because `/` is in RAM afterwards.
- **Build inputs** are `image/config` and `image/stage-pvj/`. The workflow copies them into a pi-gen checkout, so nothing here modifies pi-gen itself.

## Not verified

This has never been built. `tests/test_image.py` only checks the files. Expect to fix things on the first run; likely spots: the pi-gen branch or stage names changing (the workflow input `pi_gen_ref` lets you pin one), `qemu` setup on the runner, disk space, and the installer step inside the chroot. Pin `pi_gen_ref` to a commit once a build works. Boot the resulting image on each of Pi 3, 4 and 5 and run `pvj-selftest --play`.
