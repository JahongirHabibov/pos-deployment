# POS System — Production Deployment Guide

This guide walks through deploying the Point of Sale system on a customer server using Docker Compose.

---

## Prerequisites

- Linux server with **Docker** and **Docker Compose** installed
- Access credentials provided by the developer/distributor or Legisell Admin:
  - GHCR username and read-only token
  - Legisell backend URL and a one-time provisioning token

---

## Step 1 — Authenticate with the Private Container Registry

The Docker images are hosted on GitHub Container Registry (GHCR). Log in before pulling them:

```bash
export GHCR_USER="<your-ghcr-username>" && \
export GHCR_TOKEN="<your-ghcr-readonly-token>" && \
echo "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin
```

> All credentials are provided by the developer/distributor or the Legisell Admin.

---

## Step 2 — Provision the `.env` File

Secrets (database passwords, API keys, etc.) are managed centrally in the **Legisell License Manager**. A one-time provisioning token is generated there for each tenant and consumed by `provision.py` to populate the `.env` file automatically.

### 2.1 — Run the provisioning script

```bash
python3 provision.py \
  --token <ONE_TIME_PROVISIONING_TOKEN> \
  --api-url <LEGISELL_BACKEND_URL>
```

**Optional arguments:**

| Argument | Default | Description |
|---|---|---|
| `--env-example` | `.env.example` | Path to the `.env` template file |
| `--env-output` | `.env` | Path for the generated `.env` file |

The script will:
1. Call the Legisell API to consume the token and retrieve the secrets.
2. Copy `.env.example` → `.env` (backing up any existing `.env`).
3. Replace matching keys in `.env` with the provisioned values and append any additional secrets.

### 2.2 — Set the Docker image tags

After the `.env` file has been generated, set the correct image tags for the `IMAGE_*` variables. These values are provided by the developer/distributor:

```dotenv
IMAGE_BACKEND=ghcr.io/<org>/pos-backend:<tag>
IMAGE_FRONTEND=ghcr.io/<org>/pos-frontend:<tag>
IMAGE_IMAGE_SERVICE=ghcr.io/<org>/pos-image-service:<tag>
```

Open `.env` with any text editor and update these values accordingly.

---

## Step 3 — Start the Stack

Once the `.env` file is complete, bring up all services:

```bash
sudo docker compose -f docker-compose.prod.yml up -d
```

Docker Compose will start the following services:

| Service | Description |
|---|---|
| `pos-database` | PostgreSQL 17 — persistent data storage |
| `pos-redis` | Redis 8 — caching layer |
| `pos-backend` | FastAPI application server |
| `pos-frontend` | React PWA served via Nginx (public entry point) |
| `pos-image-service` | Image upload & thumbnail service |

The frontend is accessible on the port defined by `POS_PUBLIC_PORT` in `.env`.

---

## Useful Commands

```bash
# View running containers and their status
sudo docker compose -f docker-compose.prod.yml ps

# Follow logs for all services
sudo docker compose -f docker-compose.prod.yml logs -f

# Follow logs for a specific service (e.g. backend)
sudo docker compose -f docker-compose.prod.yml logs -f backend

# Stop the stack (data is preserved in Docker volumes)
sudo docker compose -f docker-compose.prod.yml down

# Stop and remove all data volumes (destructive — use with caution)
sudo docker compose -f docker-compose.prod.yml down -v
```

---

## Troubleshooting

| Symptom | Likely cause | Resolution |
|---|---|---|
| `pull access denied` on docker compose up | Not logged in to GHCR or token expired | Repeat Step 1 |
| `API request failed (HTTP 401/403)` from provision.py | Invalid or already-consumed provisioning token | Request a new token from the Legisell Admin |
| Container exits immediately | Missing or incorrect values in `.env` | Check `docker compose logs <service>` and verify `.env` |
| Frontend unreachable | `POS_PUBLIC_PORT` blocked by firewall | Open the configured port in the server firewall/security group |
