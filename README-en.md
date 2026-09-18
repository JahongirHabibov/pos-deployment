# POS System — Production Deployment Guide

This deployment is designed to be fully automated through the GUI installer.
Manual setup is only a fallback and is documented at the end.

---

## POS Terminal: Installation Order

A POS terminal is a Debian 13 machine that boots straight into the POS:

```
Debian 13 (trixie)
├── LightDM ── autologin "pos" ──► Openbox ──► Chromium --kiosk http://localhost[:PORT]
│          └─ login screen ──────► LXQt (administrator, emergencies only)
├── Docker CE + Compose ──► frontend (nginx) · backend (FastAPI) · PostgreSQL · Redis
│                           image-service · updater · backup   (restart: unless-stopped)
└── systemd: container watchdog · time sync · kiosk power agent · diagnostics
```

1. Install Debian 13 with LXQt and an administrator account (with sudo),
   clone this repository. Install with network access (package mirror and
   `trixie-security`, Chromium's updates come only from there) and pick the
   restaurant's timezone: receipts print in the host timezone.
2. Prepare the machine (system update, kiosk, Docker, power-cut hardening):
   ```bash
   sudo ./host-setup/setup.sh
   sudo reboot
   ```
   If a root password was set during the Debian install, `sudo` is missing:
   run `su -`, then `<repo>/host-setup/setup.sh --admin-user <name>`. The
   script installs `sudo` and adds the administrator to it. Without network
   access or a Debian mirror it stops with a message saying what to fix; a
   re-run continues where it stopped.
3. On the kiosk screen press `Ctrl+Alt+Shift+A`, log in as the administrator
   (LXQt) and run `./start-installer.sh` (see below). In step 3, tick the
   kiosk power agent, diagnostics and USB backup.
4. Log out and choose the kiosk user: the POS opens in full screen.

- **URL:** the kiosk always opens `http://localhost[:POS_PUBLIC_PORT]`, never
  `127.0.0.1`. The power agent only accepts this origin, and browser storage
  (device identity) is kept per origin.
- **Network:** `POS_PUBLIC_PORT` is published on all interfaces on purpose, so
  waiter devices reach the POS at `http://<terminal-ip>[:PORT]`.
- **Recovery:** Chromium is restarted by systemd, containers by Docker
  (`unless-stopped`) and, when unhealthy, by the watchdog.
- **SSH:** key login only. Add your key before running the host setup over
  SSH (`ssh-copy-id`); the script refuses otherwise.

Details, keyboard shortcuts and the manual firmware checklist:
[`host-setup/README.md`](host-setup/README.md).

---

## Recommended: Fully Automated Setup (`installer.py`)

Use the launcher script:

```bash
chmod +x start-installer.sh
./start-installer.sh
```

Optional fast re-deploy mode:

```bash
./start-installer.sh --skip-setup
```

What `start-installer.sh` does before opening the GUI:
- Pulls the latest version of this repository (`git pull --ff-only`) and
  restarts itself; offline it continues with the local version.
- Verifies Python 3.10+ is available.
- Verifies `tkinter` is installed.
- Verifies `installer.py` exists in the same directory.
- Stops if Docker or the Compose plugin is missing.
- Launches the wizard with forwarded CLI args (including `--skip-setup`).

---

## Prerequisites

- Linux machine with Docker and Docker Compose. For a POS terminal:
  Debian 13 prepared with `host-setup/` (see above). Minimum hardware: 4 GB
  RAM, 64 GB disk, Intel Core i3 (4th gen).
Required input data from the developer/distributor or Legisell admin:
- One-time provisioning token (OTPK).
- Legisell backend URL.
- GHCR username and token (read:packages).
- Docker image tags (`IMAGE_*`).

---

## GUI Field Reference

### Step 1 — License Data & Image Tags

| Field | Purpose |
|---|---|
| Credentials were already fetched from Legisell (checkbox) | Skips provisioning API call. OTPK and URL fields are disabled. Requires an existing `.env`; only changed tag/repo/path values are patched. |
| Provisioning Token (OTPK) | One-time token used by `provision.py` to fetch tenant secrets from Legisell. |
| Legisell Backend URL | Target API base URL for provisioning request. |
| IMAGE_BACKEND | Backend image tag written to `.env`. |
| IMAGE_FRONTEND | Frontend image tag written to `.env`. |
| IMAGE_IMAGE_SERVICE | Image service tag written to `.env`. |
| IMAGE_UPDATER | Updater sidecar tag written to `.env`. |
| IMAGE_BACKUP | Backup sidecar tag written to `.env`. |
| DEPLOYMENT_REPO | Repo in `org/pos-deployment` format; source of `manifest.json` and stored in `.env`. Pre-filled with the official repo, so a first install (no `.env` yet) still gets the latest tags. |
| Path to pos-deployment (`HOST_COMPOSE_PROJECT_DIR`) | Absolute host path to this deployment directory; required by updater self-update and bind-mount path resolution. |

> Timezone, administrator login (ID `0001`, 6-digit PIN, optional e-mail) are configured in-app during the first-run Setup wizard and stored in the database — not in `.env`.

Notes:
- If `.env` already exists, relevant fields are pre-filled automatically.
- The latest release tags are read from `manifest.json` on `DEPLOYMENT_REPO` and
  written into the `IMAGE_*` fields; changed rows are highlighted. If the fetch
  fails, the box states the reason (wrong repo, HTTP status, offline).

### Step 2 — Docker Login

| Field | Purpose |
|---|---|
| GHCR login already present (checkbox) | Skips `docker login` if GHCR credentials already exist in `~/.docker`. |
| GHCR Username | Used for `docker login ghcr.io`. |
| GHCR Token / PAT | Used as registry password input (`read:packages`). |
| Sudo Password | Required to execute Docker commands via `sudo`. |
| Show token / Show password checkboxes | Visibility toggles only; do not change stored values. |

Notes:
- On successful login, the installer writes `~/.docker/pos-auth.json` for updater-side GHCR pulls.
- The installer writes `POS_DOCKER_AUTH_FILE` to `.env` with the absolute Linux path to that file.
- The GUI does not show this as a user-editable field; it is a technical value managed by the installer.
- The compose file refuses to auto-create this path; if it is missing or a directory, rerun Docker Login in the installer.
- The backup service has no separate login or published port — it is managed from the POS admin UI (Settings ▸ Backup), gated by the `system.backup` permission.

### Step 3 — Deployment

| Field | Purpose |
|---|---|
| Sudo Password (conditional) | Only shown if no sudo password is already available from Step 2 / state. Required to run final Docker operations. |
| Show password (checkbox) | Visibility toggle only. |
| Install kiosk power agent (checkbox) | Installs `kiosk-agent/` as a systemd service on this machine so the POS login screen can shut it down. Off by default; the choice is stored as `POS_KIOSK_AGENT` in `.env` and pre-filled on the next run. Hidden in WSL 2 mode. See "Kiosk Power Agent" below. |
| Set up USB backup automount (checkbox) | Installs `usb-backup/` (a udev rule) so a plugged-in USB stick can serve as an extra backup target. Off by default; stored as `POS_USB_BACKUP` in `.env`. Hidden in WSL 2 mode. See "USB Backup Target" below. |

This step also shows a read-only summary (API URL, GHCR user, app/port/db/image values) and live deployment logs.

---

## What the Installer Automates

- Calls `provision.py` and generates/updates `.env`.
- Patches deployment keys in `.env` (`IMAGE_*`, `DEPLOYMENT_REPO`, `HOST_COMPOSE_PROJECT_DIR`).
- Performs GHCR login and stores credential bridge file for updater.
- Network `pos-network` is created automatically by Docker Compose from `docker-compose.prod.yml` — no separate creation step is needed.
- Runs `docker compose pull` with a progress spinner (output is buffered internally, not streamed line-by-line) and `docker compose up -d` with live streaming logs.
- Enables time synchronisation and writes the host timezone (`TZ`) to `.env`.
- Detects disk type and RAM and writes the database profile (`PG_*`) to `.env`
  (see below).
- Generates the updater service token (`UPDATER_API_TOKEN`) once (see
  "Operation & Maintenance").
- Stores deployment logs under `logs/deploy-<timestamp>.log`.

## Database Hardware Profile

PostgreSQL needs different settings on a hard disk than on an SSD, and on 4 GB
than on 8 GB RAM. On every deployment the installer runs `host_profile.py`: it
follows the device of `/var/lib/docker` down to the physical disk (partition,
LVM, dm-crypt; one rotational disk makes the whole stack count as HDD) and
reads `MemTotal`. The result is written to `.env`:

| Key | HDD | SSD |
|---|---|---|
| `PG_RANDOM_PAGE_COST` | 4 | 1.1 |
| `PG_EFFECTIVE_IO_CONCURRENCY` | 2 | 200 |

| Key | below 6 GiB RAM (`4g`) | 6 GiB or more (`8g`) |
|---|---|---|
| `PG_SHARED_BUFFERS` | 256MB | 512MB |
| `PG_EFFECTIVE_CACHE_SIZE` | 1GB | 2GB |
| `PG_WORK_MEM` | 4MB | 8MB |
| `PG_MAINTENANCE_WORK_MEM` | 64MB | 128MB |

- `POS_HOST_PROFILE` names the result: `hdd-4g`, `hdd-8g`, `ssd-4g` or
  `ssd-8g`.
- If detection fails, the HDD/4 GB values are used. The compose file has the
  same values as defaults, so a machine the installer never profiled is safe
  too.
- Set `POS_HOST_PROFILE=manual` to keep hand-edited `PG_*` values.
- Check the detection: `python3 host_profile.py`.
- A changed value recreates the database container once on the next
  `docker compose up -d` (a few seconds of downtime).
- `wal_compression` is always on. `fsync`, `full_page_writes` and
  `synchronous_commit` stay at their safe defaults, so committed data survives
  a power cut.
- Existing installations: `git pull`, then run the installer again
  (`./start-installer.sh --skip-setup`). Updates through the admin UI only
  deliver images, not the compose file.

---

## USB Backup Target

Backups normally live in `backups/` on this machine only. A USB stick can be
registered as an additional target, so every database and image backup is
written to both places.

Why a host component is involved: the backup service is a container, and a
container cannot mount a block device. The host mounts the stick; compose only
makes that mount visible (`/external/*`, with `rslave` propagation — without it
a stick plugged in after container start stays invisible). On a machine with a
desktop session udisks already mounts sticks under `/media/<user>/<LABEL>` and
nothing extra is needed. The kiosk session has no file manager that would do
this, so the `usb-backup/` udev rule mounts them to
`/mnt/kassio-usb/by-uuid/<UUID>` instead.

Setup:

1. Tick *Set up USB backup automount* in step 3 (or run
   `sudo usb-backup/install.sh` by hand).
2. `docker compose up -d backup` — the `/external` bind mounts come from the
   compose file, so an existing installation needs `git pull` here first.
3. POS ▸ Settings ▸ Backup ▸ External targets ▸ Add target.

The stick belongs in the **Docker host**, not in a thin-client terminal. If a
target is not connected when a backup runs, the backup still succeeds and the
target is flagged as failed — a pulled-out stick never fails the nightly
backup. Details and troubleshooting: `usb-backup/README.md`.

## Kiosk Power Agent

Kiosk terminals boot into a locked-down browser (`host-setup/`): no desktop,
no window controls, no power menu. Staff cannot switch such a device off. Browser
JavaScript cannot power off a machine either, and the backend must not do it —
in mixed deployments it runs on a server while the user stands in front of a
different terminal, so a backend-side shutdown would hit the wrong machine.

`kiosk-agent/` closes that gap: a loopback-only HTTP service on
`127.0.0.1:9110`, installed per machine. The POS login screen probes it and
shows a power-off button only where it answers. It runs as root via systemd,
which is what makes the shutdown work without a sudo password or a polkit
prompt.

- **On this Docker host:** tick *Install kiosk power agent* in step 3. The
  installer runs `kiosk-agent/install.sh` with the sudo password it already
  has and pins the accepted origin to the local POS URL.
- **On terminals without this repo** (browser-only thin clients): run
  `sudo kiosk-agent/install.sh --origins http://<pos-url>` there by hand, or
  bake it into the kiosk image.
- **On a server in a rack:** leave it unchecked. Nothing else changes.

A failed agent install never fails the deployment — the POS simply hides its
power button. Details, API and security model: `kiosk-agent/README.md`.

## Operation & Maintenance

### Requirements and accepted risks

- **Network:** the POS runs plain HTTP. Terminals and waiter devices belong in
  a separate POS Wi-Fi/LAN (WPA2/WPA3, strong key, no guest devices). The
  terminals sit behind NAT and are not reachable from the internet.
- **Firewall:** not set up by any script; configure it by hand if needed.
  Docker publishes `POS_PUBLIC_PORT` past ufw/nftables input rules, so a host
  firewall does not close that port (it is meant to be open).
- **Accepted for now:** no disk or backup encryption, no GRUB password. A
  stolen device exposes its data and `.env`.

### Updates

- **POS:** from the admin UI (Settings ▸ System update). Images only; changes
  to this repository need `git pull` and `./start-installer.sh --skip-setup`.
- **Operating system:** by hand, when an administrator is on site:
  ```bash
  sudo apt update && sudo apt upgrade        # Docker stays held
  sudo apt-mark unhold docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo apt install --only-upgrade docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo apt-mark hold docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  ```
  Update Docker only outside opening hours: the containers restart.
- **After a Chromium update:** press `Ctrl+Alt+Shift+R` (or reboot). A running
  Chromium whose files were replaced crashes its pages.

### Updater service token

The updater controls Docker, so it only accepts requests from the backend.
The installer generates `UPDATER_API_TOKEN` into `.env` on the first
deployment; compose passes it to the backend (which sends it) and to the
updater (which checks it). Admins keep using the admin UI as before.

- `UPDATER_REQUIRE_TOKEN=false` (default): a wrong token is refused, a missing
  one is still accepted, so a terminal with an older backend image keeps
  working.
- `UPDATER_REQUIRE_TOKEN=true`: requests without the token are refused too.
  Set it only once the terminal runs a backend image that sends the token.
- Never share or print the value; to replace it, delete the line and run the
  installer again.

### Staff quick guide

| Situation | What to do |
|---|---|
| Start takes up to 2 minutes (black screen) | Wait; do not switch off. |
| Page shows "Aw, Snap!" | Tap **Reload**, or `Ctrl+Alt+Shift+R`. |
| Login screen instead of the POS | Tap the kiosk user (no password). |
| Device frozen | Hold the power button for 5 s, then switch on again. |
| Switch off at closing time | Power button on the POS login screen, or press the device's power button briefly. |

Administrators: log out of the LXQt session when done; it stays open on the
screen otherwise.

---

## Manual Setup (Short Fallback)

Use this only when the GUI cannot be used.

1. Log in to GHCR:

```bash
export GHCR_USER="<your-ghcr-username>"
export GHCR_TOKEN="<your-ghcr-readonly-token>"
echo "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin
python3 -c 'import base64,json,os,pathlib; p=pathlib.Path.home()/".docker"/"pos-auth.json"; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps({"auths":{"ghcr.io":{"auth":base64.b64encode((os.environ["GHCR_USER"]+":"+os.environ["GHCR_TOKEN"]).encode()).decode()}}}, indent=2)+"\n"); p.chmod(0o600)'
```

The `pos-auth.json` file is the updater bridge for WSL/Docker Desktop compatibility. It must be a regular file, never a directory.

2. Provision `.env`:

```bash
python3 provision.py --token <ONE_TIME_PROVISIONING_TOKEN> --api-url <LEGISELL_BACKEND_URL>
```

3. Ensure at least these values are correct in `.env`:

```dotenv
IMAGE_BACKEND=ghcr.io/<org>/pos-backend:<tag>
IMAGE_FRONTEND=ghcr.io/<org>/pos-frontend:<tag>
IMAGE_IMAGE_SERVICE=ghcr.io/<org>/pos-image-service:<tag>
IMAGE_UPDATER=ghcr.io/<org>/pos-updater:<tag>
IMAGE_BACKUP=ghcr.io/<org>/pos-backup:<tag>
DEPLOYMENT_REPO=<org>/pos-deployment
HOST_COMPOSE_PROJECT_DIR=/absolute/path/to/pos-deployment
POS_DOCKER_AUTH_FILE=/home/<user>/.docker/pos-auth.json
```

Timezone and the administrator account are set later in the browser via the first-run Setup wizard.

Optional: add the database profile printed by `python3 host_profile.py`
(without it, the HDD/4 GB defaults apply).

Use an absolute Linux path for `POS_DOCKER_AUTH_FILE`; do not use `~` in `.env`. In the GUI flow, the installer writes this value automatically.

4. Start services:

```bash
sudo docker network create --driver bridge pos-network || true
sudo docker compose -f docker-compose.prod.yml pull
sudo docker compose -f docker-compose.prod.yml up -d
```

5. Verify:

```bash
sudo docker compose -f docker-compose.prod.yml ps
sudo docker compose -f docker-compose.prod.yml logs -f
```
