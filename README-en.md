# POS System — Production Deployment Guide

This deployment is designed to be fully automated through the GUI installer.
Manual setup is only a fallback and is documented at the end.

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
- Verifies Python 3.10+ is available.
- Verifies `tkinter` is installed.
- Verifies `installer.py` exists in the same directory.
- Launches the wizard with forwarded CLI args (including `--skip-setup`).

---

## Prerequisites

- Linux server with Docker and Docker Compose.
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

This step also shows a read-only summary (API URL, GHCR user, app/port/db/image values) and live deployment logs.

---

## What the Installer Automates

- Calls `provision.py` and generates/updates `.env`.
- Patches deployment keys in `.env` (`IMAGE_*`, `DEPLOYMENT_REPO`, `HOST_COMPOSE_PROJECT_DIR`).
- Performs GHCR login and stores credential bridge file for updater.
- Network `pos-network` is created automatically by Docker Compose from `docker-compose.prod.yml` — no separate creation step is needed.
- Runs `docker compose pull` with a progress spinner (output is buffered internally, not streamed line-by-line) and `docker compose up -d` with live streaming logs.
- Stores deployment logs under `logs/deploy-<timestamp>.log`.

---

## Kiosk Power Agent

Kiosk terminals boot into a locked-down browser: no desktop, no window
controls, no power menu. Staff cannot switch such a device off. Browser
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
