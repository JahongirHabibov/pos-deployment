# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Read-only view of the POS deployment directory.

Everything here is strictly read-only. The diagnostics tool never writes to
.env, docker-compose.prod.yml, manifest.json, updater-state/ or backups/ — the
POS stack owns those files and a diagnostic tool that edits them is a diagnostic
tool that can break the thing it is meant to inspect.

Only four .env keys are ever read, as a fixed allowlist. The file also holds
database passwords and registry tokens; the safest way not to leak them is
never to hold them in the first place.
"""

from __future__ import annotations

import json
import os

ENV_ALLOWLIST = ("POS_PUBLIC_PORT", "TZ", "COMPOSE_PROJECT_NAME",
                 "HOST_COMPOSE_PROJECT_DIR")

DEFAULT_SEARCH_PATHS = (
    "/opt/pos-deployment",
    "/srv/pos-deployment",
    os.path.expanduser("~/pos-deployment"),
)


def find_deployment_dir(explicit: str = "") -> str:
    """Locate the deployment directory, preferring an explicit setting."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_dir = os.environ.get("KASSIO_DIAG_DEPLOYMENT_DIR", "")
    if env_dir:
        candidates.append(env_dir)
    candidates.extend(DEFAULT_SEARCH_PATHS)
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(candidate, "docker-compose.prod.yml")):
            return candidate
    return ""


def read_env(deployment_dir: str) -> dict:
    """Read the four allowlisted keys from .env. Never returns anything else."""
    values = {}
    if not deployment_dir:
        return values
    path = os.path.join(deployment_dir, ".env")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key in ENV_ALLOWLIST:
                    values[key] = value.strip().strip('"').strip("'")
    except OSError:
        return values
    return values


def env_key_presence(deployment_dir: str) -> list:
    """Names of the keys defined in .env, with no values. For the report."""
    names = []
    if not deployment_dir:
        return names
    path = os.path.join(deployment_dir, ".env")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key:
                    names.append({"key": key, "set": bool(value.strip())})
    except OSError:
        return names
    return names


def read_manifest(deployment_dir: str) -> dict:
    if not deployment_dir:
        return {}
    try:
        with open(os.path.join(deployment_dir, "manifest.json"), "r",
                  encoding="utf-8") as handle:
            document = json.load(handle)
        return document if isinstance(document, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def read_updater_state(deployment_dir: str) -> dict:
    if not deployment_dir:
        return {}
    path = os.path.join(deployment_dir, "updater-state", "state.json")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        return document if isinstance(document, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def read_upgrade_events(deployment_dir: str, limit: int = 20) -> list:
    if not deployment_dir:
        return []
    path = os.path.join(deployment_dir, "updater-state", "upgrades.jsonl")
    events = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    events.append(entry)
    except OSError:
        return []
    return events[-limit:]


def newest_backup(deployment_dir: str) -> dict:
    """Age and size of the most recent backup artefact, or an empty dict."""
    if not deployment_dir:
        return {}
    directory = os.path.join(deployment_dir, "backups")
    newest = {}
    try:
        entries = os.listdir(directory)
    except OSError:
        return {}
    for name in entries:
        path = os.path.join(directory, name)
        try:
            if not os.path.isfile(path):
                continue
            modified = os.path.getmtime(path)
            size = os.path.getsize(path)
        except OSError:
            continue
        if not newest or modified > newest["modified"]:
            newest = {"name": name, "modified": modified, "size": size}
    return newest
