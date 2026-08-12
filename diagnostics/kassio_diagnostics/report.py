# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
The support report.

A single .txt file, because the customer has to be able to paste it into a chat
or attach it to a mail without first learning what an archive is.

Redaction works by whitelist, not by detection: the report is assembled from
material that is known to be safe, and .env is never embedded — only the names
of the keys it defines, with "set" or "not set". The regular expression pass
that follows is a second line of defence for material we do not control, such as
container logs, and never the first.
"""

from __future__ import annotations

import datetime
import re

REDACTED = "[redacted]"

# Second line of defence over text we do not control (logs, command output).
SECRET_PATTERNS = (
    # The value alternative accepts a two-token scheme form ("Basic xyz"), or a
    # quoted string, before falling back to a single token. Without the scheme
    # branch only the word "Basic" would be removed and the credential would
    # survive.
    re.compile(r"(?i)\b([A-Z0-9_]*(?:password|passwd|secret|token|otpk|api[_-]?key)"
               r"[A-Z0-9_]*)\s*[:=]\s*(\"[^\"]*\"|'[^']*'"
               r"|(?:basic|bearer|token|digest)\s+\S+|\S+)"),
    # Authorization headers carry exactly one value per line, so consuming to
    # the end of the line is both safe and the only way to catch every scheme.
    re.compile(r"(?i)\b((?:proxy-)?authorization)\s*[:=][^\n]*"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]+):([^\s@]+)@"),
)


def scrub(text) -> str:
    """Remove anything that looks like a credential from free-form text."""
    if not isinstance(text, str):
        text = str(text)
    result = text
    result = SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}={REDACTED}", result)
    result = SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}: {REDACTED}", result)
    result = SECRET_PATTERNS[2].sub(f"Bearer {REDACTED}", result)
    result = SECRET_PATTERNS[3].sub(REDACTED, result)
    result = SECRET_PATTERNS[4].sub(REDACTED, result)
    result = SECRET_PATTERNS[5].sub(REDACTED, result)
    result = SECRET_PATTERNS[6].sub(lambda m: f"{m.group(1)}:{REDACTED}@", result)
    return result


def _section(title: str) -> str:
    return f"\n{'=' * 78}\n{title}\n{'=' * 78}\n"


def _format_result(result: dict, translate) -> str:
    status = str(result.get("status", "?")).upper()
    title = translate(result.get("title_key", ""))
    message = translate(result.get("message_key", ""), **(result.get("params") or {}))
    line = f"[{status:<11}] {result.get('id', '')}  {title}\n              {message}"
    actual, expected = result.get("actual", ""), result.get("expected", "")
    if actual or expected:
        line += f"\n              actual={actual or '-'}  expected={expected or '-'}"
    details = result.get("details", "")
    if details:
        line += "\n              details: " + scrub(details).replace("\n", "\n              ")
    return line


def build(*, translate, results, config_document, config_findings, system_info,
          containers, container_logs, updater_state, upgrade_events, backup,
          env_keys, deployment_dir, tool_version, language, now=None) -> str:
    """Assemble the report. Everything included here is chosen explicitly."""
    stamp = (now or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        "KASSIO POS — Support-Report / support report",
        f"created:        {stamp}",
        f"tool version:   {tool_version}",
        f"language:       {language}",
        f"deployment dir: {deployment_dir or '-'}",
    ]

    parts.append(_section("1. System"))
    if system_info:
        os_info = system_info.get("os") or {}
        boot = system_info.get("boot") or {}
        parts.append(f"os:           {os_info.get('name', '?')}")
        parts.append(f"kernel:       {system_info.get('kernel', '?')}")
        parts.append(f"hostname:     {system_info.get('hostname', '?')}")
        parts.append(f"architecture: {system_info.get('architecture', '?')}")
        parts.append(f"boot mode:    {boot.get('mode', '?')} "
                     f"(secure boot: {boot.get('secure_boot')})")
        parts.append(f"machine-id:   {(system_info.get('machine_id') or {}).get('hash', '-')}")
        for disk in system_info.get("disks") or []:
            parts.append(f"disk:         {disk.get('mountpoint')} "
                         f"{disk.get('percent')}% used")
    else:
        parts.append("system information could not be read")

    parts.append(_section("2. Checks"))
    for result in results or []:
        parts.append(_format_result(result, translate))

    parts.append(_section("3. Expected configuration"))
    if config_document:
        import json
        # Contains no credentials by design — see config.py.
        parts.append(scrub(json.dumps(config_document, indent=2, ensure_ascii=False)))
    else:
        parts.append("no expected-config.json present")
    for finding in config_findings or []:
        parts.append(f"  [{finding.get('severity', '?')}] {finding.get('key', '')} "
                     f"{finding.get('field', '')} {finding.get('params', {})}")

    parts.append(_section("4. Containers"))
    for container in containers or []:
        parts.append(f"{container.get('name', '?'):<22} "
                     f"{container.get('state', '?'):<10} "
                     f"{container.get('status', '')}  {container.get('image', '')}")

    parts.append(_section("5. Updater and backup"))
    if updater_state:
        parts.append(f"current version:            {updater_state.get('current_version')}")
        parts.append(f"upgrade_recovery_required:  "
                     f"{updater_state.get('upgrade_recovery_required')}")
        parts.append(f"upgrade_recovery_reason:    "
                     f"{updater_state.get('upgrade_recovery_reason')}")
        parts.append(f"download:                   {updater_state.get('download')}")
        for service, info in (updater_state.get("services") or {}).items():
            parts.append(f"  service {service:<16} {info}")
    else:
        parts.append("no updater state found")
    for event in upgrade_events or []:
        parts.append(f"  event {event.get('timestamp', '')} {event.get('event', '')} "
                     f"{event.get('services', '')}")
    parts.append(f"newest backup: {backup or 'none'}")

    parts.append(_section("6. .env keys (names only, never values)"))
    for entry in env_keys or []:
        parts.append(f"{entry.get('key', ''):<34} "
                     f"{'set' if entry.get('set') else 'not set'}")

    parts.append(_section("7. Container logs"))
    for name, lines in (container_logs or {}).items():
        parts.append(f"\n--- {name} ---")
        for line in lines or []:
            parts.append(scrub(line))

    return "\n".join(str(part) for part in parts) + "\n"
