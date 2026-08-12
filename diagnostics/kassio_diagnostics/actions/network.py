# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""Network and clock repairs."""

from __future__ import annotations

import re

from . import RISK_LOW, RISK_MEDIUM, ActionResult, action, outcome_to_result

RE_IFACE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,14}\Z")


def _interface_for(context, params: dict) -> str:
    candidate = str((params or {}).get("interface", "")).strip()
    if candidate:
        return candidate
    if isinstance(context.config, dict):
        return str((context.config.get("network") or {}).get("interface", "")).strip()
    return ""


@action("network.restart_network", needs_sudo=True, risk=RISK_MEDIUM)
def restart_network(context, params: dict) -> ActionResult:
    outcome = context.privileged.act("restart-network", password=context.password)
    return outcome_to_result(outcome, "action.network.restart_network.done", {},
                             ["network", "devices", "pos"])


@action("network.renew_dhcp", needs_sudo=True, risk=RISK_LOW)
def renew_dhcp(context, params: dict) -> ActionResult:
    interface = _interface_for(context, params)
    if not RE_IFACE.fullmatch(interface or ""):
        return ActionResult(False, "action.network.renew_dhcp.no_interface",
                            {"value": interface[:32]})
    outcome = context.privileged.act("renew-dhcp", interface,
                                     password=context.password)
    return outcome_to_result(outcome, "action.network.renew_dhcp.done",
                             {"interface": interface}, ["network", "devices"])


@action("network.flush_dns", needs_sudo=True, risk=RISK_LOW)
def flush_dns(context, params: dict) -> ActionResult:
    outcome = context.privileged.act("flush-dns", password=context.password)
    return outcome_to_result(outcome, "action.network.flush_dns.done", {}, ["network"])


@action("system.sync_time", needs_sudo=True, risk=RISK_LOW)
def sync_time(context, params: dict) -> ActionResult:
    outcome = context.privileged.act("sync-time", password=context.password)
    return outcome_to_result(outcome, "action.system.sync_time.done", {},
                             ["system", "docker", "pos"])
