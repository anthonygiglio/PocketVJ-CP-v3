# Installer

```
sudo install/install.sh            # install or update (safe to run again)
install/install.sh --dry-run       # show what it would do
sudo install/install.sh --uninstall [--purge]
```

What it does, on Debian, Ubuntu and Raspberry Pi OS (Bookworm, Trixie) for Pi 3, 4, 5 and x86:

1. Installs `mpv` and `python3` with apt (skipped with `--offline`; it then fails if something is missing).
2. Creates group `pvj` and the player account (default: the user who ran `sudo`, else a system user `pvj-player`), adds it to `video`, `render`, `audio`, `input`, and adds the web user to `pvj`.
3. Copies the program to `/opt/pvj/releases/<version>` and switches `/opt/pvj/current` to it atomically. The previous release stays in place and its path is written to `/opt/pvj/previous`, which is the basis for rollback.
4. Writes `/etc/pvj/pvj.env` (media and USB folders) only if it does not exist, so your edits survive updates, and `/etc/pvj/install.json`.
5. Installs, enables and starts `pvj-player.service`, and links `pvj-player` and `pvj-selftest` into `/usr/local/bin`.

Program files are owned by root and not writable by the web user. `--uninstall` keeps `/etc/pvj`, media, users and the group unless you add `--purge` (which removes only `/etc/pvj`).

`--stage DIR` installs into a folder without touching users, apt or systemd; the tests and the future image build use it.

Verified: `tests/test_install.py` (stage mode) and one real-mode run in a throwaway container (user and group creation, links, uninstall). Not verified: apt installation on a real Pi, `systemctl` enable and start, and boot-time behaviour.

Not included yet: the web panel, OSC and the read-only root option; updates from a signed USB stick or the network; automated rollback command.
