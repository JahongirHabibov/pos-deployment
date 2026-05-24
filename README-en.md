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
| DEPLOYMENT_REPO | Repo in `org/pos-deployment` format; used for release/tag hints and stored in `.env`. |
| Path to pos-deployment (`HOST_COMPOSE_PROJECT_DIR`) | Absolute host path to this deployment directory; required by updater self-update and bind-mount path resolution. |
| Timezone (`TZ`) | IANA timezone applied to containers (for logs, schedules, timestamps). |

Notes:
- If `.env` already exists, relevant fields are pre-filled automatically.
- Recent tags are fetched automatically for `DEPLOYMENT_REPO` (display hint).

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
- `BACKUP_UI_PASSWORD` is provided by Legisell provisioning (`provision.py`) and written to `.env` automatically.
- `BACKUP_UI_USER` is fixed to `admin`.

### Step 3 — Deployment

| Field | Purpose |
|---|---|
| Sudo Password (conditional) | Only shown if no sudo password is already available from Step 2 / state. Required to run final Docker operations. |
| Show password (checkbox) | Visibility toggle only. |

This step also shows a read-only summary (API URL, GHCR user, app/port/db/image values) and live deployment logs.

---

## What the Installer Automates

- Calls `provision.py` and generates/updates `.env`.
- Writes `BACKUP_UI_PASSWORD` from Legisell provisioning into `.env` and uses `BACKUP_UI_USER=admin`.
- Patches deployment keys in `.env` (`IMAGE_*`, `DEPLOYMENT_REPO`, `HOST_COMPOSE_PROJECT_DIR`, `TZ`).
- Performs GHCR login and stores credential bridge file for updater.
- Network `pos-network` is created automatically by Docker Compose from `docker-compose.prod.yml` — no separate creation step is needed.
- Runs `docker compose pull` with a progress spinner (output is buffered internally, not streamed line-by-line) and `docker compose up -d` with live streaming logs.
- Stores deployment logs under `logs/deploy-<timestamp>.log`.

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

3. Ensure at least these values are correct in `.env`.
`BACKUP_UI_PASSWORD` must come from the provisioning response, and `BACKUP_UI_USER` must be `admin`:

```dotenv
IMAGE_BACKEND=ghcr.io/<org>/pos-backend:<tag>
IMAGE_FRONTEND=ghcr.io/<org>/pos-frontend:<tag>
IMAGE_IMAGE_SERVICE=ghcr.io/<org>/pos-image-service:<tag>
IMAGE_UPDATER=ghcr.io/<org>/pos-updater:<tag>
IMAGE_BACKUP=ghcr.io/<org>/pos-backup:<tag>
DEPLOYMENT_REPO=<org>/pos-deployment
HOST_COMPOSE_PROJECT_DIR=/absolute/path/to/pos-deployment
POS_DOCKER_AUTH_FILE=/home/<user>/.docker/pos-auth.json
TZ=Europe/Berlin
BACKUP_UI_USER=admin
BACKUP_UI_PASSWORD=<from-provisioning-secret>
```

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
