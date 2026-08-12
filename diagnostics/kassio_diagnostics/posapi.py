# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Talking to the POS backend.

Reading and writing the printer setting goes through the official API, never
around it into the database: the backend owns validation and cache invalidation,
and a diagnostics tool that writes rows behind its back is a diagnostics tool
that creates the next incident.

No credentials are stored. The operator signs in when a POS value is actually
needed; the token lives in memory, is never written to disk, never logged and
never included in the support report.

Setting keys are matched by pattern rather than hardcoded, because the settings
schema belongs to a separately versioned backend. A renamed key then shows up as
"no printer setting found" instead of silently writing to the wrong place.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

LOG = logging.getLogger("kassio.posapi")

DEFAULT_TIMEOUT = 8
TOKEN_LIFETIME_SECONDS = 1800

PRINTER_KEY_PATTERN = re.compile(r"print", re.IGNORECASE)
# Anchored to segment boundaries rather than a plain substring: "receipt"
# contains "ip", so a loose search would classify printer.receipt.width as an
# address field — and adopting a found IP into a width setting would be a real
# misconfiguration that looks like a success.
ADDRESS_KEY_PATTERN = re.compile(
    r"(?:\A|[._\-])(ip|ip_address|host|hostname|address|addr)(?:\Z|[._\-])",
    re.IGNORECASE)


class PosError(Exception):
    def __init__(self, error_key: str, detail: str = "", status: int = 0):
        super().__init__(error_key)
        self.error_key = error_key
        self.detail = detail
        self.status = status


class PosApi:
    def __init__(self, base_url: str, timeout: int = DEFAULT_TIMEOUT):
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------
    def _request(self, method: str, path: str, token: str = "", payload=None):
        if not self.base_url:
            raise PosError("pos.no_base_url")
        url = f"{self.base_url}{path}"
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(4 * 1024 * 1024)
                if not raw:
                    return {}
                try:
                    return json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    raise PosError("pos.unexpected_response", "response was not JSON")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(4096).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - detail is best effort only
                detail = ""
            if exc.code in (401, 403):
                raise PosError("pos.unauthorised", detail[:400], exc.code)
            if exc.code == 404:
                raise PosError("pos.endpoint_missing", f"{method} {path}", exc.code)
            raise PosError("pos.http_error", f"{exc.code}: {detail[:400]}", exc.code)
        except urllib.error.URLError as exc:
            raise PosError("pos.unreachable", str(exc.reason)[:300])
        except (TimeoutError, OSError) as exc:
            raise PosError("pos.unreachable", str(exc)[:300])

    # -- endpoints --------------------------------------------------------
    def login(self, username: str, password: str) -> str:
        payload = self._request("POST", "/api/v1/auth/login",
                                payload={"username": username, "password": password})
        for key in ("access_token", "token", "accessToken"):
            value = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(value, str) and value:
                return value
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            for key in ("access_token", "token", "accessToken"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
        raise PosError("pos.no_token_in_response")

    def system_settings(self, token: str) -> dict:
        payload = self._request("GET", "/api/v1/settings/system", token=token)
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                return data
            return payload
        return {}

    def update_setting(self, token: str, key: str, value) -> dict:
        quoted = urllib.parse.quote(key, safe="")
        return self._request("PUT", f"/api/v1/settings/system/{quoted}",
                             token=token, payload={"value": value})

    def print_test(self, token: str) -> dict:
        return self._request("POST", "/api/v1/print/test", token=token)

    def printer_status(self, token: str) -> dict:
        return self._request("GET", "/api/v1/print/status", token=token)


def flatten_settings(settings) -> dict:
    """Flatten a possibly nested settings document into dotted keys."""
    flat = {}

    def walk(prefix: str, node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(f"{prefix}.{key}" if prefix else str(key), value)
        elif isinstance(node, list):
            if all(not isinstance(item, (dict, list)) for item in node):
                flat[prefix] = node
        else:
            flat[prefix] = node

    walk("", settings)
    return flat


def printer_address_settings(settings) -> dict:
    """Keys that look like a printer address, with their current values."""
    flat = flatten_settings(settings)
    return {
        key: value for key, value in flat.items()
        if PRINTER_KEY_PATTERN.search(key) and ADDRESS_KEY_PATTERN.search(key)
    }


class PosSessionStore:
    """In-memory POS tokens. Nothing here survives a restart, by design."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens: dict = {}

    def store(self, session_token: str, pos_token: str, username: str) -> None:
        with self._lock:
            self._tokens[session_token] = {
                "token": pos_token,
                "username": username,
                "expires_at": self._clock() + TOKEN_LIFETIME_SECONDS,
            }

    def get(self, session_token: str) -> str:
        with self._lock:
            entry = self._tokens.get(session_token)
            if entry is None:
                return ""
            if self._clock() > entry["expires_at"]:
                self._tokens.pop(session_token, None)
                return ""
            return entry["token"]

    def username(self, session_token: str) -> str:
        with self._lock:
            entry = self._tokens.get(session_token)
            return entry["username"] if entry else ""

    def revoke(self, session_token: str) -> None:
        with self._lock:
            self._tokens.pop(session_token, None)
