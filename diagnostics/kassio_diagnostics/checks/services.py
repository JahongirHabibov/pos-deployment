# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
System service checks, including the diagnostics tool's own health.

The self-check exists because a diagnostic tool that is quietly half-installed
is worse than none: it shows green cards while the privileged half is missing.
It therefore verifies the two things installation can get wrong — helper
ownership and the sudoers drop-in — and says so in plain language.
"""

from __future__ import annotations

import os
import stat

from .. import runner
from ..privileged import default_helper_path
from ..runner import CheckResult, check

SUDOERS_PATH = "/etc/sudoers.d/kassio-diagnostics"

# Units that matter, and whether their absence is a fault or simply a fact
# about how this machine is set up.
REQUIRED_UNITS = ("docker.service",)
OPTIONAL_UNITS = ("kassio-power-agent.service", "cups.service",
                  "systemd-timesyncd.service", "chronyd.service", "chrony.service",
                  "NetworkManager.service", "systemd-networkd.service",
                  "systemd-resolved.service", "containerd.service")


@check("services", "services.units", "check.services.units.title")
def services_units(context) -> list:
    outcome = context.read("services")
    if not outcome.ok:
        return [CheckResult(
            id="services.units", group="services", status=runner.UNAVAILABLE,
            title_key="check.services.units.title",
            message_key=outcome.error_key or "error.unavailable",
            params=outcome.params, details=outcome.detail)]

    units = (outcome.data or {}).get("units") or {}
    results = []
    for unit in REQUIRED_UNITS:
        info = units.get(unit) or {}
        active = info.get("active", "")
        status = runner.OK if active == "active" else runner.FAIL
        results.append(CheckResult(
            id=f"services.unit:{unit}", group="services", status=status,
            title_key="check.services.units.title",
            message_key="check.services.unit.active" if status == runner.OK
            else "check.services.unit.inactive",
            params={"unit": unit, "state": active or "?"},
            actual=active, expected="active", data=info))

    for unit in OPTIONAL_UNITS:
        info = units.get(unit) or {}
        active = info.get("active", "")
        if active in ("", "inactive", "unknown"):
            # Not installed or deliberately off — a POS terminal does not need
            # every one of these, so this is information, not a fault.
            continue
        status = runner.OK if active == "active" else runner.WARN
        actions = ["system.restart_power_agent"] if (
            unit == "kassio-power-agent.service" and status != runner.OK) else []
        results.append(CheckResult(
            id=f"services.unit:{unit}", group="services", status=status,
            title_key="check.services.units.title",
            message_key="check.services.unit.active" if status == runner.OK
            else "check.services.unit.degraded",
            params={"unit": unit, "state": active},
            actual=active, expected="active", actions=actions, data=info))
    return results


@check("services", "services.self", "check.services.self.title")
def services_self(context) -> list:
    results = []
    helper = default_helper_path()
    problems = []
    try:
        info = os.stat(helper)
        if info.st_uid != 0:
            problems.append("helper is not owned by root")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            problems.append("helper is writable by non-root")
        installed = True
    except OSError:
        installed = False

    if not installed:
        results.append(CheckResult(
            id="services.self.helper", group="services", status=runner.FAIL,
            title_key="check.services.self.title",
            message_key="check.services.self.helper_missing",
            params={"path": helper}))
    elif problems:
        results.append(CheckResult(
            id="services.self.helper", group="services", status=runner.FAIL,
            title_key="check.services.self.title",
            message_key="check.services.self.helper_permissions",
            params={"path": helper}, details="; ".join(problems)))
    else:
        results.append(CheckResult(
            id="services.self.helper", group="services", status=runner.OK,
            title_key="check.services.self.title",
            message_key="check.services.self.helper_ok", params={"path": helper}))

    # A missing sudoers rule is not fatal — reads fall back to unprivileged
    # execution — but it silently costs the container view, so name it.
    if os.path.exists(SUDOERS_PATH):
        results.append(CheckResult(
            id="services.self.sudoers", group="services", status=runner.OK,
            title_key="check.services.self.title",
            message_key="check.services.self.sudoers_ok"))
    else:
        results.append(CheckResult(
            id="services.self.sudoers", group="services", status=runner.WARN,
            title_key="check.services.self.title",
            message_key="check.services.self.sudoers_missing",
            params={"path": SUDOERS_PATH}))

    from ..checks import FAILED_IMPORTS
    if FAILED_IMPORTS:
        results.append(CheckResult(
            id="services.self.modules", group="services", status=runner.WARN,
            title_key="check.services.self.title",
            message_key="check.services.self.modules_failed",
            params={"modules": ", ".join(sorted(FAILED_IMPORTS))},
            details="\n".join(f"{k}: {v}" for k, v in FAILED_IMPORTS.items())))
    return results
