# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
The expected state of one customer site.

This file is what turns "the printer does not answer" into "the printer is not
where the technician put it". It is written by the technician through the setup
wizard, never guessed, and it deliberately holds no credentials of any kind.

Validation returns a list of findings rather than raising, because the wizard
has to show every problem at once instead of one per attempt.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re

CONFIG_DIR = "/etc/kassio-diagnostics"
CONFIG_PATH = os.path.join(CONFIG_DIR, "expected-config.json")
SCHEMA_VERSION = 1

RE_ID = re.compile(r"\A[a-z0-9][a-z0-9-]{0,31}\Z")
RE_MAC = re.compile(r"\A([0-9a-f]{2}:){5}[0-9a-f]{2}\Z", re.IGNORECASE)
RE_CONTAINER = re.compile(r"\Apos-[a-z0-9][a-z0-9-]{0,31}\Z")
RE_IFACE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,14}\Z")

ROLES = ("receipt_printer", "kitchen_printer", "label_printer",
         "payment_terminal", "scale", "other")

DEFAULT_CONTAINERS = ["pos-database", "pos-redis", "pos-backend", "pos-frontend",
                      "pos-image-service", "pos-updater", "pos-backup"]

ERROR, WARNING = "error", "warning"


class Finding:
    __slots__ = ("severity", "key", "params", "field")

    def __init__(self, severity: str, key: str, field: str = "", **params):
        self.severity = severity
        self.key = key
        self.field = field
        self.params = params

    def as_dict(self) -> dict:
        return {"severity": self.severity, "key": self.key,
                "field": self.field, "params": self.params}


def empty_config() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "site": {"name": "", "technician": "", "configured_at": "", "language": "de"},
        "network": {"interface": "", "subnet": "", "gateway": "", "addressing": "static"},
        "identity": {"machine_id_hash": ""},
        "devices": [],
        "containers": list(DEFAULT_CONTAINERS),
    }


def load(path: str = CONFIG_PATH):
    """Return (config, findings). A missing or broken file is not an exception."""
    if not os.path.exists(path):
        return None, [Finding(WARNING, "config.missing")]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, [Finding(ERROR, "config.unreadable", detail=str(exc)[:200])]
    if not isinstance(document, dict):
        return None, [Finding(ERROR, "config.not_an_object")]
    return document, validate(document)


def validate(document: dict) -> list:
    findings = []
    if not isinstance(document, dict):
        return [Finding(ERROR, "config.not_an_object")]

    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        findings.append(Finding(ERROR, "config.schema_version",
                                field="schema_version",
                                found=str(version), expected=str(SCHEMA_VERSION)))

    site = document.get("site")
    if not isinstance(site, dict) or not str(site.get("name", "")).strip():
        findings.append(Finding(WARNING, "config.site_name_missing", field="site.name"))

    network = document.get("network") if isinstance(document.get("network"), dict) else {}
    subnet = None
    subnet_raw = str(network.get("subnet", "")).strip()
    if subnet_raw:
        try:
            subnet = ipaddress.ip_network(subnet_raw, strict=False)
        except ValueError:
            findings.append(Finding(ERROR, "config.subnet_invalid",
                                    field="network.subnet", value=subnet_raw))
    else:
        findings.append(Finding(WARNING, "config.subnet_missing", field="network.subnet"))

    interface = str(network.get("interface", "")).strip()
    if interface and not RE_IFACE.fullmatch(interface):
        findings.append(Finding(ERROR, "config.interface_invalid",
                                field="network.interface", value=interface))

    gateway_raw = str(network.get("gateway", "")).strip()
    if gateway_raw:
        try:
            gateway = ipaddress.ip_address(gateway_raw)
            if subnet is not None and gateway not in subnet:
                findings.append(Finding(WARNING, "config.gateway_outside_subnet",
                                        field="network.gateway",
                                        value=gateway_raw, subnet=str(subnet)))
        except ValueError:
            findings.append(Finding(ERROR, "config.gateway_invalid",
                                    field="network.gateway", value=gateway_raw))

    devices = document.get("devices")
    if not isinstance(devices, list):
        findings.append(Finding(ERROR, "config.devices_not_a_list", field="devices"))
        devices = []
    elif not devices:
        findings.append(Finding(WARNING, "config.no_devices", field="devices"))

    seen_ids, seen_ips, seen_macs = set(), set(), set()
    for index, device in enumerate(devices):
        field = f"devices[{index}]"
        if not isinstance(device, dict):
            findings.append(Finding(ERROR, "config.device_not_an_object", field=field))
            continue
        name = str(device.get("name", "")).strip() or f"#{index + 1}"

        identifier = str(device.get("id", "")).strip()
        if not RE_ID.fullmatch(identifier):
            findings.append(Finding(ERROR, "config.device_id_invalid",
                                    field=f"{field}.id", device=name, value=identifier))
        elif identifier in seen_ids:
            findings.append(Finding(ERROR, "config.device_id_duplicate",
                                    field=f"{field}.id", device=name, value=identifier))
        else:
            seen_ids.add(identifier)

        if str(device.get("role", "")) not in ROLES:
            findings.append(Finding(WARNING, "config.device_role_unknown",
                                    field=f"{field}.role", device=name,
                                    value=str(device.get("role", ""))))

        address_raw = str(device.get("ip", "")).strip()
        try:
            address = ipaddress.ip_address(address_raw)
            if address_raw in seen_ips:
                findings.append(Finding(ERROR, "config.device_ip_duplicate",
                                        field=f"{field}.ip", device=name, value=address_raw))
            seen_ips.add(address_raw)
            if subnet is not None and address not in subnet:
                findings.append(Finding(ERROR, "config.device_ip_outside_subnet",
                                        field=f"{field}.ip", device=name,
                                        value=address_raw, subnet=str(subnet)))
        except ValueError:
            findings.append(Finding(ERROR, "config.device_ip_invalid",
                                    field=f"{field}.ip", device=name, value=address_raw))

        mac_raw = str(device.get("mac", "")).strip()
        if mac_raw:
            if not RE_MAC.fullmatch(mac_raw):
                findings.append(Finding(ERROR, "config.device_mac_invalid",
                                        field=f"{field}.mac", device=name, value=mac_raw))
            else:
                normalised = mac_raw.lower()
                if normalised in seen_macs:
                    findings.append(Finding(ERROR, "config.device_mac_duplicate",
                                            field=f"{field}.mac", device=name,
                                            value=mac_raw))
                seen_macs.add(normalised)
        else:
            # Not fatal, but the MAC is the only identifier that survives an
            # address change — which is exactly the failure being diagnosed.
            findings.append(Finding(WARNING, "config.device_mac_missing",
                                    field=f"{field}.mac", device=name))

        port = device.get("port", 9100)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            findings.append(Finding(ERROR, "config.device_port_invalid",
                                    field=f"{field}.port", device=name, value=str(port)))

    containers = document.get("containers")
    if not isinstance(containers, list) or not containers:
        findings.append(Finding(WARNING, "config.containers_missing", field="containers"))
    else:
        for name in containers:
            if not isinstance(name, str) or not RE_CONTAINER.fullmatch(name):
                findings.append(Finding(ERROR, "config.container_name_invalid",
                                        field="containers", value=str(name)[:64]))
    return findings


def has_errors(findings) -> bool:
    return any(finding.severity == ERROR for finding in findings)


def devices_of(document) -> list:
    if not isinstance(document, dict):
        return []
    devices = document.get("devices")
    return [d for d in devices if isinstance(d, dict)] if isinstance(devices, list) else []


def expected_containers(document) -> list:
    if isinstance(document, dict):
        containers = document.get("containers")
        if isinstance(containers, list):
            valid = [c for c in containers
                     if isinstance(c, str) and RE_CONTAINER.fullmatch(c)]
            if valid:
                return valid
    return list(DEFAULT_CONTAINERS)


def list_backups(directory: str = CONFIG_DIR) -> list:
    prefix = os.path.basename(CONFIG_PATH) + ".bak-"
    try:
        names = [n for n in os.listdir(directory) if n.startswith(prefix)]
    except OSError:
        return []
    entries = []
    for name in sorted(names, reverse=True):
        path = os.path.join(directory, name)
        try:
            entries.append({"name": name, "size": os.path.getsize(path),
                            "modified": os.path.getmtime(path)})
        except OSError:
            continue
    return entries
