# KASSIO Power Agent

Lets the POS login screen power off the terminal it is running on.

## Why this exists

The kiosk distribution boots straight into a browser showing the POS. There is
no desktop, no task bar and no power menu, so staff had no way to switch the
device off at closing time. Browser JavaScript cannot power off a machine, and
the backend cannot do it either: in mixed deployments the backend runs on a
server while the user stands in front of a thin client, so a backend-side
shutdown would take down the wrong machine.

This agent runs **on every terminal** and exposes a loopback-only HTTP endpoint
that the login screen calls. One terminal, one agent, always the local machine.

## Install

**On the Docker host**, the GUI installer does it: tick *Install kiosk power
agent* in step 3 of `installer.py`. It runs this script with the sudo password
it already has and pins the allowed origin to the local POS URL.

**On any other terminal** (thin clients, which never get this repo) run it by
hand or bake it into the kiosk image:

```bash
sudo ./install.sh
# pin the origin to the POS URL that terminal actually opens:
sudo ./install.sh --origins http://192.168.1.50
```

Either way the script copies the agent to `/opt/kassio-power-agent`, installs
the systemd unit, enables it and verifies `/health`.

## Uninstall

Unticking the installer checkbox only stops future installs — an agent already
on the machine keeps running. Remove it explicitly:

```bash
sudo systemctl disable --now kassio-power-agent
sudo rm -rf /etc/systemd/system/kassio-power-agent.service \
            /etc/systemd/system/kassio-power-agent.service.d \
            /opt/kassio-power-agent
sudo systemctl daemon-reload
```

The POS hides its power button again as soon as `/health` stops answering.

## API

Bound to `127.0.0.1:9110`.

| Method | Path        | Purpose                                                    |
| ------ | ----------- | ---------------------------------------------------------- |
| `GET`  | `/health`   | Frontend probe — the power button is hidden if this fails. |
| `POST` | `/poweroff` | Answers `202`, then powers the machine off ~0.5 s later.   |

`POST /poweroff` requires the header `X-Kassio-Power: 1`.

```bash
curl -i -X POST -H 'X-Kassio-Power: 1' http://127.0.0.1:9110/poweroff
```

## Security

* **Loopback bind** plus `IPAddressAllow=localhost` in the unit — not reachable
  over the network.
* **Runs as root** via systemd. That is what makes the poweroff work with no
  sudo password and no polkit prompt; the unit's hardening (`NoNewPrivileges`,
  `ProtectSystem=full`, `ProtectHome`, `RestrictAddressFamilies`, …) is what
  keeps the privilege contained.
* **`X-Kassio-Power` header** is not CORS-safelisted, so a browser must send a
  preflight before any POST. A random page opened in the same browser cannot
  silently trigger a shutdown.
* **Origin allowlist** (`KASSIO_POWER_ALLOWED_ORIGINS`, comma-separated) narrows
  it further to the POS URL. Unset means any origin is accepted — fine for a
  locked-down kiosk browser, worth setting on anything less locked down.

Physical access to the terminal already implies the ability to pull the plug, so
the endpoint is intentionally not authenticated: the frontend gates it with a
confirmation dialog, nothing more.

## Configuration

| Variable                       | Default     | Meaning                              |
| ------------------------------ | ----------- | ------------------------------------ |
| `KASSIO_POWER_HOST`            | `127.0.0.1` | Bind address                         |
| `KASSIO_POWER_PORT`            | `9110`      | Bind port                            |
| `KASSIO_POWER_ALLOWED_ORIGINS` | *(unset)*   | Comma-separated browser origins      |
| `KASSIO_POWER_DELAY`           | `0.5`       | Seconds between response and poweroff |

Override via a drop-in:

```bash
sudo systemctl edit kassio-power-agent.service
```

If you change the port, change `POWER_AGENT_URL` in the POS frontend
(`docker/frontend/app/src/hooks/usePowerAgent.js` in the point-of-sale repo) to
match — the port is the contract between the two.

## Troubleshooting

```bash
systemctl status kassio-power-agent
journalctl -u kassio-power-agent -n 50
curl -fsS http://127.0.0.1:9110/health
```

* **Power button not visible in the POS** — `/health` is not answering, or the
  browser blocked the request. Check the browser console for a Private Network
  Access error; the agent answers `Access-Control-Allow-Private-Network: true`,
  which needs Chrome/Chromium 104+.
* **`no_poweroff_command`** — neither `systemctl`, `poweroff` nor `shutdown` is
  on the agent's `PATH`.
