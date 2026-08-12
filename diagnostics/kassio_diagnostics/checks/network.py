# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Network checks.

These run before the device checks for a reason: when the link is down or the
gateway is gone, every printer on the site looks broken. Naming the network as
the cause keeps the customer from power-cycling a printer that was never at
fault.
"""

from __future__ import annotations

import ipaddress

from .. import netscan, runner
from ..runner import CheckResult, check

INTERNET_PROBES = (("1.1.1.1", 443), ("8.8.8.8", 53))
DNS_PROBE_NAME = "ghcr.io"


def _unavailable(check_id: str, title_key: str, outcome) -> CheckResult:
    return CheckResult(id=check_id, group="network", status=runner.UNAVAILABLE,
                       title_key=title_key,
                       message_key=outcome.error_key or "error.unavailable",
                       params=outcome.params, details=outcome.detail)


def primary_interface(data: dict, configured: str = "") -> dict:
    """The interface carrying a routable address, preferring the configured one."""
    addresses = data.get("addresses") or []
    if not isinstance(addresses, list):
        return {}
    candidates = []
    for entry in addresses:
        if not isinstance(entry, dict):
            continue
        name = entry.get("ifname", "")
        if name == "lo" or not name:
            continue
        for info in entry.get("addr_info") or []:
            if not isinstance(info, dict) or info.get("family") != "inet":
                continue
            candidates.append({
                "ifname": name,
                "operstate": entry.get("operstate", ""),
                "address": info.get("local", ""),
                "prefixlen": info.get("prefixlen"),
                "dynamic": bool(info.get("dynamic")),
            })
    if configured:
        for candidate in candidates:
            if candidate["ifname"] == configured:
                return candidate
    for candidate in candidates:
        if candidate["operstate"].upper() == "UP":
            return candidate
    return candidates[0] if candidates else {}


def default_gateway(data: dict) -> str:
    for route in data.get("routes") or []:
        if isinstance(route, dict) and route.get("dst") == "default":
            gateway = route.get("gateway", "")
            if gateway:
                return gateway
    return ""


def neighbour_table(data: dict) -> list:
    entries = []
    for entry in data.get("neighbours") or []:
        if not isinstance(entry, dict):
            continue
        address = entry.get("dst", "")
        mac = (entry.get("lladdr") or "").lower()
        if address and mac:
            entries.append({"ip": address, "mac": mac,
                            "state": " ".join(entry.get("state") or [])})
    return entries


def _configured_interface(context) -> str:
    if isinstance(context.config, dict):
        return str((context.config.get("network") or {}).get("interface", ""))
    return ""


@check("network", "network.interface", "check.network.interface.title")
def network_interface(context) -> CheckResult:
    outcome = context.read("network")
    if not outcome.ok:
        return _unavailable("network.interface", "check.network.interface.title", outcome)
    data = outcome.data or {}
    if not data.get("tool_available"):
        return CheckResult(id="network.interface", group="network",
                           status=runner.UNAVAILABLE,
                           title_key="check.network.interface.title",
                           message_key="error.tool_missing", params={"tool": "ip"})
    configured = _configured_interface(context)
    interface = primary_interface(data, configured)
    if not interface:
        return CheckResult(
            id="network.interface", group="network", status=runner.FAIL,
            title_key="check.network.interface.title",
            message_key="check.network.interface.none",
            actions=["network.restart_network"])
    up = interface["operstate"].upper() in ("UP", "UNKNOWN")
    status = runner.OK if up else runner.FAIL
    return CheckResult(
        id="network.interface", group="network", status=status,
        title_key="check.network.interface.title",
        message_key="check.network.interface.up" if up
        else "check.network.interface.down",
        params={"interface": interface["ifname"], "state": interface["operstate"]},
        actual=interface["ifname"],
        expected=configured,
        actions=[] if up else ["network.restart_network"],
        data=interface)


@check("network", "network.address", "check.network.address.title")
def network_address(context) -> CheckResult:
    outcome = context.read("network")
    if not outcome.ok:
        return _unavailable("network.address", "check.network.address.title", outcome)
    data = outcome.data or {}
    configured = _configured_interface(context)
    interface = primary_interface(data, configured)
    if not interface or not interface.get("address"):
        return CheckResult(
            id="network.address", group="network", status=runner.FAIL,
            title_key="check.network.address.title",
            message_key="check.network.address.none",
            actions=["network.renew_dhcp"])

    address = interface["address"]
    expected_subnet = ""
    if isinstance(context.config, dict):
        expected_subnet = str((context.config.get("network") or {}).get("subnet", ""))

    inside = True
    if expected_subnet:
        try:
            inside = ipaddress.ip_address(address) in ipaddress.ip_network(
                expected_subnet, strict=False)
        except ValueError:
            inside = True

    if not inside:
        return CheckResult(
            id="network.address", group="network", status=runner.FAIL,
            title_key="check.network.address.title",
            message_key="check.network.address.wrong_subnet",
            params={"address": address, "subnet": expected_subnet},
            actual=address, expected=expected_subnet,
            actions=["network.renew_dhcp"])

    dynamic = interface.get("dynamic")
    expected_addressing = "static"
    if isinstance(context.config, dict):
        expected_addressing = str((context.config.get("network") or {}).get(
            "addressing", "static"))
    if dynamic and expected_addressing == "static":
        return CheckResult(
            id="network.address", group="network", status=runner.WARN,
            title_key="check.network.address.title",
            message_key="check.network.address.dhcp_but_static_expected",
            params={"address": address}, actual=address, expected=expected_addressing)
    return CheckResult(
        id="network.address", group="network", status=runner.OK,
        title_key="check.network.address.title",
        message_key="check.network.address.message",
        params={"address": address, "prefix": interface.get("prefixlen", "")},
        actual=address, expected=expected_subnet)


@check("network", "network.gateway", "check.network.gateway.title")
def network_gateway(context) -> CheckResult:
    outcome = context.read("network")
    if not outcome.ok:
        return _unavailable("network.gateway", "check.network.gateway.title", outcome)
    data = outcome.data or {}
    gateway = default_gateway(data)
    expected = ""
    if isinstance(context.config, dict):
        expected = str((context.config.get("network") or {}).get("gateway", ""))
    if not gateway:
        return CheckResult(
            id="network.gateway", group="network", status=runner.FAIL,
            title_key="check.network.gateway.title",
            message_key="check.network.gateway.none", expected=expected,
            actions=["network.restart_network"])
    reachable = netscan.ping(gateway) or netscan.tcp_probe(gateway, 80, timeout=0.5)
    if expected and gateway != expected:
        return CheckResult(
            id="network.gateway", group="network", status=runner.WARN,
            title_key="check.network.gateway.title",
            message_key="check.network.gateway.unexpected",
            params={"gateway": gateway, "expected": expected},
            actual=gateway, expected=expected)
    if not reachable:
        return CheckResult(
            id="network.gateway", group="network", status=runner.FAIL,
            title_key="check.network.gateway.title",
            message_key="check.network.gateway.unreachable",
            params={"gateway": gateway}, actual=gateway, expected=expected,
            actions=["network.restart_network"])
    return CheckResult(
        id="network.gateway", group="network", status=runner.OK,
        title_key="check.network.gateway.title",
        message_key="check.network.gateway.message",
        params={"gateway": gateway}, actual=gateway, expected=expected)


def resolve_name(name: str) -> bool:
    """Own function rather than an inline call so it can be stubbed in tests
    without patching socket globally, which would also break the caller."""
    import socket
    try:
        socket.setdefaulttimeout(4)
        socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(None)


@check("network", "network.dns", "check.network.dns.title")
def network_dns(context) -> CheckResult:
    outcome = context.read("network")
    nameservers = (outcome.data or {}).get("nameservers") if outcome.ok else []
    resolved = resolve_name(DNS_PROBE_NAME)
    if resolved:
        return CheckResult(
            id="network.dns", group="network", status=runner.OK,
            title_key="check.network.dns.title", message_key="check.network.dns.message",
            params={"name": DNS_PROBE_NAME},
            data={"nameservers": nameservers})
    # Not fatal: the POS itself runs entirely locally. It only blocks updates.
    return CheckResult(
        id="network.dns", group="network", status=runner.WARN,
        title_key="check.network.dns.title", message_key="check.network.dns.failed",
        params={"name": DNS_PROBE_NAME}, actions=["network.flush_dns"],
        data={"nameservers": nameservers})


@check("network", "network.internet", "check.network.internet.title")
def network_internet(context) -> CheckResult:
    reachable = any(netscan.tcp_probe(host, port, timeout=1.5)
                    for host, port in INTERNET_PROBES)
    if reachable:
        return CheckResult(
            id="network.internet", group="network", status=runner.OK,
            title_key="check.network.internet.title",
            message_key="check.network.internet.message")
    return CheckResult(
        id="network.internet", group="network", status=runner.WARN,
        title_key="check.network.internet.title",
        message_key="check.network.internet.unreachable")


@check("network", "network.neighbours", "check.network.neighbours.title")
def network_neighbours(context) -> CheckResult:
    outcome = context.read("network")
    if not outcome.ok:
        return _unavailable("network.neighbours", "check.network.neighbours.title", outcome)
    entries = neighbour_table(outcome.data or {})
    return CheckResult(
        id="network.neighbours", group="network", status=runner.OK,
        title_key="check.network.neighbours.title",
        message_key="check.network.neighbours.message",
        params={"count": len(entries)}, data={"entries": entries[:100]})
