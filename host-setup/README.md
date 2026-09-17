# Host Setup — Debian 13 Kiosk Terminal

Prepares a fresh Debian 13 (trixie) machine as a POS terminal. Run it
**before** `start-installer.sh`: the host setup prepares the machine, and the
installer then sets up the POS itself.

```bash
sudo ./host-setup/setup.sh --dry-run    # show what would change
sudo ./host-setup/setup.sh              # apply
sudo reboot
```

Every step checks the current state first and only changes what is missing, so
a re-run is safe. After a change of `POS_PUBLIC_PORT`, run it again with
`--only kiosk`.

## Result

```
Debian 13
├── LightDM ── autologin "pos" ──► Openbox ──► Chromium --kiosk http://localhost[:PORT]
│          └─ login screen ──────► LXQt (administrator, emergencies only)
├── Docker CE + Compose plugin (from download.docker.com, apt-mark hold)
│   └── POS stack (docker-compose.prod.yml, restart: unless-stopped)
├── pos-container-watchdog.timer   restarts unhealthy containers
├── systemd-timesyncd, zram swap, OpenSSH, NetworkManager
└── power-cut hardening (GRUB fallback path, fsck.repair, no suspend)
```

## Options

| Option | Default | Purpose |
|---|---|---|
| `--admin-user` | the user who ran `sudo` | Administrator account (LXQt, SSH). Must exist; never `root` or the kiosk user. |
| `--kiosk-user` | `pos` | Account that is logged in automatically. Created if missing. |
| `--deployment-dir` | this repository | Path used by the watchdog to see a running update. |
| `--port` | `POS_PUBLIC_PORT` from `.env`, else `80` | Port of the kiosk URL. |
| `--only`, `--skip` | all steps | Comma-separated step names (see below). |
| `--dry-run` | off | Print commands and files, change nothing. |
| `--force` | off | Run on a system other than Debian 13. |

## Steps

| Step | What it does |
|---|---|
| `packages` | Comments out `deb cdrom:` sources (backup: `sources.list.pos-backup`) and stops with a clear message if no Debian mirror is reachable. Installs LightDM, Openbox, Chromium, Xorg with libinput, `unclutter-xfixes`, `zram-tools`, OpenSSH, NetworkManager, `curl`, `python3-tk`, `git`, `sudo`, `systemd-timesyncd` (skipped if chrony/ntpsec is present), and `lxqt-core` if no LXQt is installed. Presets LightDM as the display manager. |
| `docker` | Adds the Docker apt repository (signing key fingerprint verified), installs Docker CE and the Compose plugin, holds the packages. If Debian's `docker.io` is installed, it is left alone with a warning. |
| `users` | Creates the kiosk user with a locked password and adds it to `nopasswdlogin`. Removes it from `sudo`, `docker` and `adm`. Adds the administrator to `sudo` (needed by `installer.py`). |
| `kiosk` | Kiosk session, Chromium policy, LightDM autologin, disables SDDM (details below). |
| `powerloss` | GRUB: `fsck.repair=yes`, timeout 3 s; on UEFI also installs `EFI/BOOT/BOOTX64.EFI`. journald persistent (max. 200 MB), power button = clean shutdown, suspend/hibernate masked. |
| `memory` | zram swap: zstd, 50 % of RAM, priority 100 (used before any disk swap). |
| `time` | NTP on, RTC in UTC. Warns if the system clock or the RTC is obviously wrong. |
| `ssh` | Key login only: password and keyboard-interactive login off, `PermitRootLogin no`, kiosk user denied. Safeguards: refuses to start when run over SSH without a key in `~/.ssh/authorized_keys` (you would be locked out); `sshd -t` before reload, previous file restored if it fails; `sshd -T` must report the values (another drop-in may win); `reload` keeps open connections. Without a key only local login works until one is added. |
| `watchdog` | Installs and starts `pos-container-watchdog.timer`. |

## Kiosk session

- **Autostart:** LightDM logs in the kiosk user and starts the `pos-kiosk`
  session. A session wrapper enforces it: the kiosk user gets the kiosk
  session even if LXQt is picked on the login screen.
- **Browser:** `pos-kiosk-browser.service` (systemd user unit,
  `Restart=always`) runs `/usr/local/lib/pos-kiosk/kiosk-browser`, which:
  1. removes stale Chromium lock files and the "crashed" flag after a power cut,
  2. waits until the frontend answers on the POS URL (the POS then shows its
     own loading screen until the backend is ready),
  3. starts Chromium with `--kiosk`, pinch zoom and swipe navigation off, and
     autoplay allowed for POS sounds.
- **Screen:** blanking and DPMS are off; the mouse pointer is hidden until it
  moves.
- **Chromium policy** `/etc/chromium/policies/managed/pos-kiosk.json`: only the
  POS URL, the power agent (`127.0.0.1:9110`), the diagnostics
  (`127.0.0.1:9120`) and print preview are allowed. Developer tools,
  extensions, sign-in and the password manager are off. Downloads (CSV/XLSX
  exports, backup files) always open a save dialog, so they can go straight
  to a USB stick. The policy
  applies **machine-wide**, so it also applies to Chromium in the administrator
  session. For other websites there, use another browser (e.g. Firefox ESR).
- **Configuration:** `/etc/pos-kiosk/kiosk.conf` (`POS_URL`),
  `/etc/pos-kiosk/openbox-rc.xml`,
  `/etc/lightdm/lightdm.conf.d/50-pos-kiosk.conf`.

### Keyboard shortcuts

| Keys | Action |
|---|---|
| `Ctrl+Alt+Shift+A` | Login screen for the administrator; the kiosk keeps running. After logging out, choose the kiosk user to return (no password). |
| `Ctrl+Alt+Shift+R` | Restart the kiosk browser. |
| `Ctrl+Alt+F2` … `F6` | Text console (administrator password required). `Ctrl+Alt+F7` goes back. |

Openbox defines no other shortcuts (no Alt+Tab, no menus). If Chromium is
closed (`Alt+F4`, `Ctrl+Shift+Q`), systemd starts it again.

## Container watchdog

`restart: unless-stopped` only acts when a container **exits**. A hanging
process keeps failing its healthcheck and stays that way. Once a minute,
`pos-container-watchdog.timer` checks the health of `pos-backend` and
`pos-image-service` and restarts a container that is:

- `unhealthy` in two consecutive runs (on top of Docker's own retries),
- restarted fewer than 3 times in the last hour (after that it only logs),
- not in the middle of an update (`updater-state/state.json`).

The database, the backup sidecar (it may be restoring) and the updater are
never restarted. Logs:
`journalctl -u pos-container-watchdog`.

## Power cuts and clock

What the OS handles:

- the file system is repaired at boot (`fsck.repair=yes`) instead of stopping
  at an emergency shell,
- the machine still boots when the firmware has lost its boot entries (UEFI
  fallback path),
- Chromium starts cleanly (lock files, crash flag),
- PostgreSQL keeps `fsync`, `full_page_writes` and `synchronous_commit` on:
  committed payments survive,
- the clock never goes back behind the last synchronised time
  (systemd-timesyncd).

What the OS **cannot** handle, so it has to be checked by hand (the script
prints this list at the end):

- [ ] CMOS battery (CR2032) replaced on older machines. An empty battery resets
      the clock and the BIOS settings on every power cut.
- [ ] BIOS/UEFI: "Restore on AC power loss" = Power On.
- [ ] BIOS/UEFI: boot only from the internal disk, supervisor password set.
- [ ] Touchscreen: taps land where the finger is (calibrate if not).

A UPS is still recommended: no software protects a disk that loses power
while writing.

## Hardware

Minimum: 4 GB RAM, 64 GB disk, Intel Core i3 (4th gen) or comparable. The
installer detects disk type and RAM and sizes PostgreSQL accordingly (see
"Database Hardware Profile" in the main README). An SSD instead of a hard disk
is the single most effective upgrade.

## Files installed

| Path | Purpose |
|---|---|
| `/usr/local/lib/pos-kiosk/` | `kiosk-session`, `kiosk-browser`, `session-wrapper`, `container-watchdog` |
| `/etc/pos-kiosk/` | `kiosk.conf`, `openbox-rc.xml` |
| `/usr/share/xsessions/pos-kiosk.desktop` | Kiosk session entry |
| `/etc/systemd/user/pos-kiosk-browser.service` | Browser unit |
| `/etc/systemd/system/pos-container-watchdog.{service,timer}` | Watchdog |
| `/etc/chromium/policies/managed/pos-kiosk.json` | Chromium policy |
| `/etc/lightdm/lightdm.conf.d/50-pos-kiosk.conf` | Autologin |
| `/etc/default/grub.d/90-pos-kiosk.cfg` | Kernel command line, GRUB timeout |
| `/etc/systemd/{journald,logind}.conf.d/50-pos-kiosk.conf` | Logs, power button |
| `/etc/ssh/sshd_config.d/10-pos-kiosk.conf` | SSH hardening |
| `/etc/default/zramswap`, `/etc/apt/sources.list.d/docker.sources` | zram, Docker repository |

## Tests

```bash
python3 -m pytest test_host_setup.py test_host_profile.py
```
