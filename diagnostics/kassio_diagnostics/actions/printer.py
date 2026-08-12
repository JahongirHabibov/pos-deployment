# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Printer repairs and the on-demand network scan.

Adopting a found address is the "print again now" path: it points the POS at
where the printer actually is. It is explicitly not the cure — the printer will
lose the address again at the next power cut — so the interface offers it next
to the instructions for setting a fixed address in the printer itself.

The setting key is discovered rather than hardcoded, because the settings schema
belongs to a separately versioned backend. When the discovery is ambiguous the
action refuses and asks, instead of writing to a key that merely looked right.
"""

from __future__ import annotations

import ipaddress

from .. import netscan, vendors
from ..checks import network as network_checks
from ..posapi import PosError, printer_address_settings
from . import RISK_LOW, RISK_MEDIUM, ActionResult, action


def _valid_ip(value) -> str:
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        return ""


@action("printer.test_print", needs_pos_login=True, risk=RISK_LOW)
def test_print(context, params: dict) -> ActionResult:
    if context.pos_api is None or not context.pos_token:
        return ActionResult(False, "pos.login_required")
    try:
        response = context.pos_api.print_test(context.pos_token)
    except PosError as exc:
        return ActionResult(False, exc.error_key, {}, details=exc.detail)
    return ActionResult(True, "action.printer.test_print.done", {},
                        data=response if isinstance(response, dict) else {},
                        recheck_groups=["devices"])


@action("printer.adopt_found_ip", needs_pos_login=True, risk=RISK_MEDIUM)
def adopt_found_ip(context, params: dict) -> ActionResult:
    if context.pos_api is None or not context.pos_token:
        return ActionResult(False, "pos.login_required")
    address = _valid_ip((params or {}).get("ip"))
    if not address:
        return ActionResult(False, "error.rejected_argument",
                            {"value": str((params or {}).get("ip", ""))[:40]})

    try:
        settings = context.pos_api.system_settings(context.pos_token)
    except PosError as exc:
        return ActionResult(False, exc.error_key, {}, details=exc.detail)

    candidates = printer_address_settings(settings)
    requested_key = str((params or {}).get("setting_key", "")).strip()
    if requested_key:
        if requested_key not in candidates:
            return ActionResult(False, "pos.setting_not_found",
                                {"key": requested_key[:80]},
                                data={"candidates": sorted(candidates)})
        key = requested_key
    elif len(candidates) == 1:
        key = next(iter(candidates))
    elif not candidates:
        return ActionResult(False, "pos.no_printer_setting", {},
                            data={"settings_seen": len(settings) if settings else 0})
    else:
        # Ambiguous on purpose: writing to the wrong setting would look like a
        # success and break printing in a way nobody would connect to this tool.
        return ActionResult(False, "pos.multiple_printer_settings",
                            {"count": len(candidates)},
                            data={"candidates": sorted(candidates)})

    previous = candidates.get(key)
    try:
        context.pos_api.update_setting(context.pos_token, key, address)
    except PosError as exc:
        return ActionResult(False, exc.error_key, {"key": key[:80]}, details=exc.detail)
    return ActionResult(True, "action.printer.adopt_found_ip.done",
                        {"key": key, "ip": address, "previous": str(previous)},
                        data={"key": key, "ip": address, "previous": previous},
                        recheck_groups=["devices"])


def _refuse_oversized(subnet: str) -> ActionResult:
    """Say the network was never searched, rather than returning an empty
    result that reads as "there are no printers here"."""
    return ActionResult(False, "action.devices.scan.subnet_too_large",
                        {"subnet": subnet, "limit": netscan.MAX_SCANNABLE_ADDRESSES})


@action("devices.scan", risk=RISK_LOW)
def scan_network(context, params: dict) -> ActionResult:
    """Sweep the local subnet for printer-ish ports and match against the config."""
    limiter = context.scan_limiter
    acquired = False
    if limiter is not None:
        allowed, wait = limiter.acquire()
        if not allowed:
            # Return before the try block: releasing a slot that was never
            # acquired would reset the interval and defeat the rate limit.
            return ActionResult(False, "action.devices.scan.rate_limited",
                                {"seconds": wait})
        acquired = True
    try:
        subnet = str((params or {}).get("subnet", "")).strip()
        if not subnet and isinstance(context.config, dict):
            subnet = str((context.config.get("network") or {}).get("subnet", "")).strip()

        # Checked before anything else is read: when the answer is already
        # known there is no reason to touch the system at all.
        if subnet and netscan.too_large_to_scan(subnet):
            return _refuse_oversized(subnet)

        network_outcome = context.privileged.read("network")
        network_data = network_outcome.data if network_outcome.ok else {}
        neighbours = network_checks.neighbour_table(network_data or {})

        if not subnet:
            configured_interface = ""
            if isinstance(context.config, dict):
                configured_interface = str(
                    (context.config.get("network") or {}).get("interface", ""))
            interface = network_checks.primary_interface(network_data or {},
                                                         configured_interface)
            address, prefix = interface.get("address", ""), interface.get("prefixlen")
            if address and prefix:
                try:
                    subnet = str(ipaddress.ip_network(f"{address}/{prefix}",
                                                      strict=False))
                except ValueError:
                    subnet = ""
        if not subnet:
            return ActionResult(False, "action.devices.scan.no_subnet")
        # Repeated for the subnet just derived from the interface, which has
        # not been through the check above.
        if netscan.too_large_to_scan(subnet):
            return _refuse_oversized(subnet)

        found = netscan.scan(subnet)
        by_ip = {entry["ip"]: entry for entry in found}
        for entry in found:
            entry["mac"] = ""
            entry["vendor"] = ""
        for neighbour in neighbours:
            entry = by_ip.get(neighbour.get("ip"))
            if entry is not None:
                mac = vendors.normalise_mac(neighbour.get("mac", ""))
                entry["mac"] = mac
                entry["vendor"] = vendors.vendor_for_mac(mac)["label"]

        matches = []
        devices = (context.config or {}).get("devices") if isinstance(
            context.config, dict) else []
        for device in devices or []:
            if not isinstance(device, dict):
                continue
            wanted = vendors.normalise_mac(device.get("mac", ""))
            if not wanted:
                continue
            for entry in found:
                if entry.get("mac") and entry["mac"] == wanted:
                    matches.append({
                        "device_id": device.get("id", ""),
                        "device_name": device.get("name", ""),
                        "expected_ip": device.get("ip", ""),
                        "found_ip": entry["ip"],
                        "moved": entry["ip"] != str(device.get("ip", "")),
                    })
                    break
        return ActionResult(True, "action.devices.scan.done",
                            {"count": len(found), "subnet": subnet},
                            data={"subnet": subnet, "found": found, "matches": matches},
                            recheck_groups=["devices"])
    finally:
        if acquired:
            limiter.release()
