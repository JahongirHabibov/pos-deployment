# KASSIO Diagnostics

Local diagnostics and repair interface for POS terminals. Runs as a systemd
service on the terminal itself and is reached at <http://127.0.0.1:9120/>.

It is deliberately **not** a Docker service: diagnosing a broken Docker
installation is one of its jobs.

## Install

```bash
sudo ./install.sh
```

The installer refuses rather than displaces: it stops if port 9120 is taken, if
the sudoers rule fails `visudo -c`, or if `python3` or systemd are missing.
Every step is idempotent, so a re-run is safe.

## Remove

```bash
sudo ./uninstall.sh
```

Asks separately whether `/etc/kassio-diagnostics` should be kept.

## What it touches

Writes only to its own paths plus four new files it owns:
`/opt/kassio-diagnostics`, `/etc/kassio-diagnostics`,
`/etc/systemd/system/kassio-diagnostics.service`,
`/etc/sudoers.d/kassio-diagnostics`,
`/usr/share/applications/kassio-diagnostics.desktop` and one managed-bookmark
file per Chromium-family browser.

Reads, and never writes: `.env` (four allowlisted keys only),
`docker-compose.prod.yml`, `manifest.json`, `updater-state/`, `backups/`.

## Security model

* Bound to `127.0.0.1`; `SocketBindDeny=any` stops any further listening socket.
* Every request is checked against the UID owning the peer socket — a loopback
  port is reachable by every local account.
* One root-owned helper with a fixed verb table is the only path to root. Read
  verbs are reachable through a narrow `NOPASSWD` sudoers rule; everything that
  changes the system needs the password.
* The sudo password is held in memory for at most five minutes, with a two
  minute idle timeout and a visible lock button. `sudo -k` keeps sudo's own
  timestamp cache empty, so the session never widens sudo for other processes.
* The session token travels in a header, never a cookie.

## Logs

```bash
journalctl -u kassio-diagnostics -n 100
```

Every privileged action is logged there with its verb, arguments and result.
