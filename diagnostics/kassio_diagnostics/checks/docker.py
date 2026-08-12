# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Docker and POS container checks.

Also covers the updater and backup state, because that is where the customer
looks when "the POS is behaving strangely" and it is the cheapest signal in the
whole system: the files are already on disk. In particular
``upgrade_recovery_required`` marks a half-finished upgrade, a condition that is
otherwise found only after a long search.

Nothing here restarts the stack. The only container repair is restarting one
named pos-* container, because a full stack restart costs minutes of downtime
and is a decision for a technician, not a side effect of a diagnosis.
"""

from __future__ import annotations

import time

from .. import deployment, runner
from ..config import expected_containers
from ..runner import CheckResult, check

BACKUP_WARN_AGE_HOURS = 48
RESTART_WARN_COUNT = 5


def _daemon_unavailable(outcome) -> CheckResult:
    return CheckResult(
        id="docker.daemon", group="docker", status=runner.FAIL,
        title_key="check.docker.daemon.title",
        message_key=outcome.error_key or "check.docker.daemon.unreachable",
        params=outcome.params, details=outcome.detail,
        actions=[])


@check("docker", "docker.daemon", "check.docker.daemon.title")
def docker_daemon(context) -> CheckResult:
    outcome = context.read("containers")
    if not outcome.ok:
        return _daemon_unavailable(outcome)
    data = outcome.data or {}
    if not data.get("available"):
        return CheckResult(
            id="docker.daemon", group="docker", status=runner.FAIL,
            title_key="check.docker.daemon.title",
            message_key="check.docker.daemon.unreachable",
            details=str(data.get("error", ""))[:500])
    containers = data.get("containers") or []
    return CheckResult(
        id="docker.daemon", group="docker", status=runner.OK,
        title_key="check.docker.daemon.title", message_key="check.docker.daemon.message",
        params={"count": len(containers)})


@check("docker", "docker.containers", "check.docker.container.title")
def docker_containers(context) -> list:
    outcome = context.read("containers")
    if not outcome.ok or not (outcome.data or {}).get("available"):
        return [CheckResult(
            id="docker.containers", group="docker", status=runner.UNAVAILABLE,
            title_key="check.docker.container.title",
            message_key="check.docker.containers.unavailable",
            details=outcome.detail if not outcome.ok else "")]

    present = {entry["name"]: entry for entry in (outcome.data or {}).get("containers") or []}
    expected = expected_containers(context.config)
    results = []
    for name in expected:
        entry = present.get(name)
        if entry is None:
            results.append(CheckResult(
                id=f"docker.container:{name}", group="docker", status=runner.FAIL,
                title_key="check.docker.container.title",
                message_key="check.docker.container.missing",
                params={"container": name}, expected=name,
                data={"container": name}))
            continue
        inspect = context.read("container-inspect", name)
        details = inspect.data if inspect.ok and isinstance(inspect.data, dict) else {}
        state = (details.get("state") or entry.get("state") or "").lower()
        health = details.get("health")
        restarts = details.get("restart_count")
        payload = {"container": name, "image": entry.get("image", ""),
                   "status": entry.get("status", ""), "state": state,
                   "health": health, "restart_count": restarts}

        if state != "running":
            results.append(CheckResult(
                id=f"docker.container:{name}", group="docker", status=runner.FAIL,
                title_key="check.docker.container.title",
                message_key="check.docker.container.not_running",
                params={"container": name, "state": state or "?"},
                actual=state, expected="running",
                actions=["container.restart"], data=payload))
            continue
        if health == "unhealthy":
            results.append(CheckResult(
                id=f"docker.container:{name}", group="docker", status=runner.FAIL,
                title_key="check.docker.container.title",
                message_key="check.docker.container.unhealthy",
                params={"container": name}, actual=str(health), expected="healthy",
                actions=["container.restart"], data=payload))
            continue
        if isinstance(restarts, int) and restarts >= RESTART_WARN_COUNT:
            results.append(CheckResult(
                id=f"docker.container:{name}", group="docker", status=runner.WARN,
                title_key="check.docker.container.title",
                message_key="check.docker.container.restarting",
                params={"container": name, "count": restarts},
                actual=str(restarts), actions=["container.restart"], data=payload))
            continue
        results.append(CheckResult(
            id=f"docker.container:{name}", group="docker", status=runner.OK,
            title_key="check.docker.container.title",
            message_key="check.docker.container.running",
            params={"container": name, "health": health or "-"},
            actual=state, expected="running",
            actions=["container.restart"], data=payload))

    unexpected = [name for name in present if name not in expected]
    if unexpected:
        results.append(CheckResult(
            id="docker.container:unexpected", group="docker", status=runner.WARN,
            title_key="check.docker.container.title",
            message_key="check.docker.container.unexpected",
            params={"containers": ", ".join(sorted(unexpected)[:8])},
            data={"containers": sorted(unexpected)}))
    return results


@check("docker", "docker.updater", "check.docker.updater.title")
def docker_updater(context) -> list:
    state = deployment.read_updater_state(context.deployment_dir)
    if not state:
        return [CheckResult(
            id="docker.updater", group="docker", status=runner.UNAVAILABLE,
            title_key="check.docker.updater.title",
            message_key="check.docker.updater.no_state")]

    results = []
    if state.get("upgrade_recovery_required"):
        # A half-applied upgrade. Explicitly not repaired here: recovery is the
        # updater's job and guessing at it could leave the stack worse off.
        results.append(CheckResult(
            id="docker.updater.recovery", group="docker", status=runner.FAIL,
            title_key="check.docker.updater.title",
            message_key="check.docker.updater.recovery_required",
            params={"reason": str(state.get("upgrade_recovery_reason") or "")[:200]},
            data={"state": state}))
    else:
        results.append(CheckResult(
            id="docker.updater.recovery", group="docker", status=runner.OK,
            title_key="check.docker.updater.title",
            message_key="check.docker.updater.message",
            params={"version": str(state.get("current_version") or "?")},
            actual=str(state.get("current_version") or ""),
            data={"services": state.get("services") or {},
                  "download": state.get("download") or {}}))

    download = state.get("download") or {}
    if str(download.get("status", "")).lower() in ("failed", "error"):
        results.append(CheckResult(
            id="docker.updater.download", group="docker", status=runner.WARN,
            title_key="check.docker.updater.title",
            message_key="check.docker.updater.download_failed",
            params={"status": str(download.get("status"))},
            data={"download": download}))
    return results


@check("docker", "docker.backup", "check.docker.backup.title")
def docker_backup(context) -> CheckResult:
    newest = deployment.newest_backup(context.deployment_dir)
    if not newest:
        return CheckResult(
            id="docker.backup", group="docker", status=runner.WARN,
            title_key="check.docker.backup.title",
            message_key="check.docker.backup.none")
    age_hours = max(0, (time.time() - newest["modified"]) / 3600.0)
    status = runner.WARN if age_hours > BACKUP_WARN_AGE_HOURS else runner.OK
    return CheckResult(
        id="docker.backup", group="docker", status=status,
        title_key="check.docker.backup.title",
        message_key="check.docker.backup.stale" if status == runner.WARN
        else "check.docker.backup.message",
        params={"hours": int(age_hours), "name": newest["name"]},
        actual=newest["name"], data=newest)
