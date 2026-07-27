#!/usr/bin/env python3
# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
KASSIO Power Agent — local shutdown endpoint for kiosk terminals.

The POS frontend runs inside a locked-down kiosk browser, so there is no way
for a user to reach a desktop, a terminal or a power menu. Browser JavaScript
cannot power off a machine either. This agent closes that gap: a tiny HTTP
service on the loopback interface that the login screen can call.

It deliberately does NOT live in the Docker stack: the backend may run on a
different machine than the terminal in front of the user (mixed all-in-one /
thin-client deployments), and powering off the server instead of the terminal
would be exactly wrong. One agent per terminal, always the local one.

Endpoints (bound to 127.0.0.1 only):
    GET  /health    -> {"ok": true, ...}   used by the frontend to decide
                                           whether to show the power button
    POST /poweroff  -> 202, then powers the machine off after a short delay

Security model:
  * Loopback bind — unreachable from the network.
  * Runs as root via systemd, so no sudo and no password prompt is involved.
    The unit file is what restricts it, not a sudoers entry.
  * POST requires the ``X-Kassio-Power: 1`` header. That header is not
    CORS-safelisted, so the browser must send a preflight first, which in turn
    means a random web page opened in the same browser cannot silently POST
    here — it only gets through if we answer its preflight.
  * Optional origin allowlist (KASSIO_POWER_ALLOWED_ORIGINS) narrows that
    further to the POS URL. See README.

Environment:
    KASSIO_POWER_HOST              default 127.0.0.1
    KASSIO_POWER_PORT              default 9110
    KASSIO_POWER_ALLOWED_ORIGINS   comma-separated list; unset = any origin
    KASSIO_POWER_DELAY             seconds before the actual poweroff (0.5)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AGENT_VERSION = "1.0.0"

HOST = os.environ.get("KASSIO_POWER_HOST", "127.0.0.1")
PORT = int(os.environ.get("KASSIO_POWER_PORT", "9110"))
DELAY_SECONDS = float(os.environ.get("KASSIO_POWER_DELAY", "0.5"))
_RAW_ORIGINS = os.environ.get("KASSIO_POWER_ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS = [o.strip() for o in _RAW_ORIGINS.split(",") if o.strip()]

REQUIRED_HEADER = "X-Kassio-Power"

# Tried in order; the first command that exists on the system is used.
POWEROFF_COMMANDS = [
    ["systemctl", "poweroff"],
    ["poweroff"],
    ["shutdown", "-h", "now"],
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("kassio-power-agent")


def _resolve_poweroff_command() -> list[str] | None:
    """Return the first available poweroff command, or None if the box has none."""
    for command in POWEROFF_COMMANDS:
        if shutil.which(command[0]):
            return command
    return None


def _do_poweroff() -> None:
    command = _resolve_poweroff_command()
    if not command:
        logger.error("No poweroff command found (tried: systemctl, poweroff, shutdown)")
        return
    logger.warning("Powering off via %s", " ".join(command))
    try:
        subprocess.run(command, check=True, timeout=30)
    except Exception as exc:  # noqa: BLE001 — last line before the lights go out
        logger.error("Poweroff command failed: %s", exc)


class PowerHandler(BaseHTTPRequestHandler):
    server_version = f"KassioPowerAgent/{AGENT_VERSION}"
    protocol_version = "HTTP/1.1"
    # Keep-alive connections must not pin a worker thread forever.
    timeout = 10

    # ── helpers ──────────────────────────────────────────────────────

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not ALLOWED_ORIGINS:
            return True
        if not origin:
            # Same-origin / non-browser callers send no Origin header. With an
            # explicit allowlist configured, only listed browser origins pass.
            return False
        return origin in ALLOWED_ORIGINS

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and self._origin_allowed():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", f"Content-Type, {REQUIRED_HEADER}")
        self.send_header("Access-Control-Max-Age", "600")
        # Chrome's Private Network Access: a page served from the LAN reaching
        # 127.0.0.1 counts as a private->local request and is blocked without this.
        if self.headers.get("Access-Control-Request-Private-Network") == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:  # journald gets our logger instead
        logger.info("%s - %s", self.address_string(), fmt % args)

    # ── routes ───────────────────────────────────────────────────────

    def do_OPTIONS(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler naming
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/health":
            self._send_json(404, {"error": "not_found"})
            return
        self._send_json(
            200,
            {
                "ok": True,
                "service": "kassio-power-agent",
                "version": AGENT_VERSION,
                "actions": ["poweroff"],
                "poweroff_available": _resolve_poweroff_command() is not None,
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/poweroff":
            self._send_json(404, {"error": "not_found"})
            return
        if self.headers.get(REQUIRED_HEADER) != "1":
            logger.warning("Rejected poweroff without %s header", REQUIRED_HEADER)
            self._send_json(403, {"error": "missing_header"})
            return
        if not self._origin_allowed():
            logger.warning("Rejected poweroff from origin %s", self.headers.get("Origin"))
            self._send_json(403, {"error": "origin_not_allowed"})
            return
        if _resolve_poweroff_command() is None:
            self._send_json(500, {"error": "no_poweroff_command"})
            return

        logger.warning("Poweroff requested by %s", self.headers.get("Origin") or "local client")
        # Answer first, power off after — otherwise the browser sees a dead
        # socket and reports a failure for a shutdown that actually worked.
        self._send_json(202, {"status": "powering_off", "delay_seconds": DELAY_SECONDS})
        threading.Timer(DELAY_SECONDS, _do_poweroff).start()


def main() -> int:
    if _resolve_poweroff_command() is None:
        logger.warning("No poweroff command available — /poweroff will fail on this system")
    server = ThreadingHTTPServer((HOST, PORT), PowerHandler)
    server.daemon_threads = True
    logger.info(
        "kassio-power-agent %s listening on http://%s:%d (origins: %s)",
        AGENT_VERSION,
        HOST,
        PORT,
        ", ".join(ALLOWED_ORIGINS) if ALLOWED_ORIGINS else "any",
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down agent")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
