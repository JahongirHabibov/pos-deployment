# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Printer and peripheral checks — the heart of the tool.

The failure being diagnosed is specific: after a power cut the receipt printer
comes back on a different address, the POS keeps sending to the old one, and
nothing prints. Reporting "printer unreachable" would be true and useless. What
the customer needs is the sentence "it is over here now, and here is why it
moved".

The MAC address is what makes that possible: it is the only identifier that
survives an address change. That is also why a missing MAC in the configuration
is flagged during setup rather than at the moment it is needed.

Finding the device does not trigger a scan on its own. The neighbour table
already knows every device that spoke recently, and it costs nothing. Only when
that fails does the result offer the scan as an explicit action, which keeps the
promise that scans happen on request and never in the background.
"""

from __future__ import annotations

import ipaddress

from .. import netscan, runner, vendors
from ..runner import CheckResult, check
from . import network as network_checks

PROBE_TIMEOUT_SECONDS = 1.0


def _configured_devices(context) -> list:
    document = context.config
    if not isinstance(document, dict):
        return []
    devices = document.get("devices")
    return [d for d in devices if isinstance(d, dict)] if isinstance(devices, list) else []


def _neighbours(context) -> list:
    outcome = context.read("network")
    if not outcome.ok:
        return []
    return network_checks.neighbour_table(outcome.data or {})


def _find_by_mac(neighbours, mac: str) -> str:
    wanted = vendors.normalise_mac(mac)
    if not wanted:
        return ""
    for entry in neighbours:
        if vendors.normalise_mac(entry.get("mac", "")) == wanted:
            return entry.get("ip", "")
    return ""


def _mac_at(neighbours, ip: str) -> str:
    for entry in neighbours:
        if entry.get("ip") == ip:
            return vendors.normalise_mac(entry.get("mac", ""))
    return ""


def _same_subnet(left: str, right: str) -> bool:
    try:
        return (ipaddress.ip_address(left).version == ipaddress.ip_address(right).version
                and ipaddress.ip_address(left).packed[:3]
                == ipaddress.ip_address(right).packed[:3])
    except ValueError:
        return False


@check("devices", "devices.configured", "check.devices.configured.title")
def devices_configured(context) -> list:
    devices = _configured_devices(context)
    if not devices:
        # Without a recorded expected state there is nothing to compare against.
        # Say so plainly and point at the wizard instead of reporting "all fine".
        return [CheckResult(
            id="devices.configured", group="devices", status=runner.WARN,
            title_key="check.devices.configured.title",
            message_key="check.devices.none_configured",
            actions=["setup.open_wizard"])]

    neighbours = _neighbours(context)
    results = []
    for device in devices:
        results.append(_check_device(device, neighbours))
    return results


def _check_device(device: dict, neighbours: list) -> CheckResult:
    identifier = str(device.get("id", "device"))
    name = str(device.get("name", "")) or identifier
    expected_ip = str(device.get("ip", ""))
    expected_mac = vendors.normalise_mac(device.get("mac", ""))
    port = device.get("port", 9100)
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        port = 9100
    check_id = f"devices.device:{identifier}"
    title_key = "check.devices.device.title"

    if not expected_ip:
        return CheckResult(
            id=check_id, group="devices", status=runner.UNKNOWN, title_key=title_key,
            message_key="check.devices.no_address", params={"device": name},
            actions=["setup.open_wizard"], data={"device": device})

    probe = netscan.probe_device(expected_ip, port, timeout=PROBE_TIMEOUT_SECONDS)
    observed_mac = _mac_at(neighbours, expected_ip)
    vendor = vendors.resolve(device, observed_mac)
    base_data = {
        "device": device, "probe": probe, "vendor": vendor["id"],
        "vendor_label": vendor["label"],
        "instructions_key": vendor["instructions_key"],
        "web_ui": vendors.web_ui_url(vendor, expected_ip),
    }

    if probe["reachable"]:
        # Reachable, but is it the right box? A different MAC at the expected
        # address means some other device took it over — the print job would go
        # somewhere unexpected rather than nowhere, which is worse.
        if expected_mac and observed_mac and observed_mac != expected_mac:
            found_ip = _find_by_mac(neighbours, expected_mac)
            data = dict(base_data, found_ip=found_ip, observed_mac=observed_mac)
            data["web_ui"] = vendors.web_ui_url(vendor, found_ip or expected_ip)
            return CheckResult(
                id=check_id, group="devices", status=runner.WARN, title_key=title_key,
                message_key="check.devices.foreign_device",
                params={"device": name, "ip": expected_ip,
                        "expected_mac": expected_mac, "found_mac": observed_mac},
                actual=f"{expected_ip} ({observed_mac})",
                expected=f"{expected_ip} ({expected_mac})",
                actions=["printer.open_web_ui", "printer.test_print"], data=data)
        return CheckResult(
            id=check_id, group="devices", status=runner.OK, title_key=title_key,
            message_key="check.devices.reachable",
            params={"device": name, "ip": expected_ip, "port": port},
            actual=expected_ip, expected=expected_ip,
            actions=["printer.test_print", "printer.open_web_ui"], data=base_data)

    # Not at the recorded address. Try to prove where it went.
    found_ip = _find_by_mac(neighbours, expected_mac) if expected_mac else ""
    if found_ip and found_ip != expected_ip:
        found_probe = netscan.probe_device(found_ip, port, timeout=PROBE_TIMEOUT_SECONDS)
        likely_dhcp = _same_subnet(found_ip, expected_ip)
        data = dict(base_data, found_ip=found_ip, found_probe=found_probe,
                    likely_dhcp=likely_dhcp)
        data["web_ui"] = vendors.web_ui_url(vendor, found_ip)
        return CheckResult(
            id=check_id, group="devices", status=runner.FAIL, title_key=title_key,
            message_key="check.devices.moved_dhcp" if likely_dhcp
            else "check.devices.moved",
            params={"device": name, "expected_ip": expected_ip, "found_ip": found_ip},
            actual=found_ip, expected=expected_ip,
            actions=["printer.adopt_found_ip", "printer.open_web_ui",
                     "printer.show_instructions"],
            data=data)

    return CheckResult(
        id=check_id, group="devices", status=runner.FAIL, title_key=title_key,
        message_key="check.devices.unreachable",
        params={"device": name, "ip": expected_ip, "port": port},
        actual="", expected=expected_ip,
        actions=["devices.scan", "printer.show_instructions"], data=base_data)


@check("devices", "devices.usb", "check.devices.usb.title")
def devices_usb(context) -> CheckResult:
    outcome = context.read("usb")
    if not outcome.ok:
        return CheckResult(
            id="devices.usb", group="devices", status=runner.UNAVAILABLE,
            title_key="check.devices.usb.title",
            message_key=outcome.error_key or "error.unavailable",
            params=outcome.params, details=outcome.detail)
    data = outcome.data or {}
    devices = data.get("devices") or []
    nodes = data.get("printer_nodes") or []
    return CheckResult(
        id="devices.usb", group="devices", status=runner.OK,
        title_key="check.devices.usb.title", message_key="check.devices.usb.message",
        params={"count": len(devices), "printers": len(nodes)},
        data={"devices": devices[:60], "printer_nodes": nodes})
