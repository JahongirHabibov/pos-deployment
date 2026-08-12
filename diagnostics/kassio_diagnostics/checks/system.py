# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Device and operating system checks.

Two of these exist because of a specific field incident: a power cut reset BIOS
parameters, the machine stopped booting, and the clock jumped. The clock is
treated as a first-class fault rather than a detail because a wrong system time
produces three unrelated-looking symptoms at once — TLS errors, failing image
pulls and a failing licence check — and the customer otherwise chases three
problems that have one cause.
"""

from __future__ import annotations

import re

from .. import runner
from ..runner import CheckResult, check, facts_of, yes_no

MEMORY_WARN_PERCENT = 90
DISK_WARN_PERCENT = 85
DISK_FAIL_PERCENT = 95
TEMPERATURE_WARN_CELSIUS = 80
TEMPERATURE_FAIL_CELSIUS = 90
LOAD_WARN_PER_CPU = 2.0
TIME_OFFSET_WARN_SECONDS = 60


def _unavailable(check_id: str, title_key: str, outcome) -> CheckResult:
    return CheckResult(id=check_id, group="system", status=runner.UNAVAILABLE,
                       title_key=title_key,
                       message_key=outcome.error_key or "error.unavailable",
                       params=outcome.params, details=outcome.detail)


def _human_bytes(value) -> str:
    if not isinstance(value, (int, float)) or value < 0:
        return "?"
    units = ("B", "KB", "MB", "GB", "TB")
    size, index = float(value), 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    return f"{size:.1f} {units[index]}"


@check("system", "system.os", "check.system.os.title")
def system_os(context) -> CheckResult:
    outcome = context.read("system")
    if not outcome.ok:
        return _unavailable("system.os", "check.system.os.title", outcome)
    data = outcome.data or {}
    os_info = data.get("os", {})
    uptime = data.get("uptime_seconds") or 0
    days, remainder = divmod(int(uptime), 86400)
    hours = remainder // 3600
    return CheckResult(
        id="system.os", group="system", status=runner.OK,
        title_key="check.system.os.title", message_key="check.system.os.message",
        params={"os": os_info.get("name", "?"), "kernel": data.get("kernel", "?"),
                "days": days, "hours": hours},
        actual=str(os_info.get("name", "")),
        facts=facts_of(
            ("fact.hostname", data.get("hostname")),
            ("fact.os", os_info.get("name")),
            ("fact.kernel", data.get("kernel")),
            ("fact.architecture", data.get("architecture")),
            ("fact.cpu_cores", data.get("cpu_count")),
            ("fact.uptime", f"{days} d {hours} h"),
        ),
        data={"hostname": data.get("hostname", ""),
              "architecture": data.get("architecture", ""),
              "cpu_count": data.get("cpu_count")},
    )


@check("system", "system.memory", "check.system.memory.title")
def system_memory(context) -> CheckResult:
    outcome = context.read("system")
    if not outcome.ok:
        return _unavailable("system.memory", "check.system.memory.title", outcome)
    memory = (outcome.data or {}).get("memory", {})
    percent = memory.get("percent")
    if percent is None:
        return CheckResult(id="system.memory", group="system", status=runner.UNKNOWN,
                           title_key="check.system.memory.title",
                           message_key="check.system.memory.unreadable")
    status = runner.WARN if percent >= MEMORY_WARN_PERCENT else runner.OK
    return CheckResult(
        id="system.memory", group="system", status=status,
        title_key="check.system.memory.title",
        message_key="check.system.memory.high" if status == runner.WARN
        else "check.system.memory.message",
        params={"percent": percent, "used": _human_bytes(memory.get("used")),
                "total": _human_bytes(memory.get("total"))},
        actual=f"{percent} %",
        facts=facts_of(
            ("fact.memory_total", _human_bytes(memory.get("total"))),
            ("fact.memory_used", _human_bytes(memory.get("used"))),
            ("fact.memory_free", _human_bytes(memory.get("available"))),
            ("fact.swap_total", _human_bytes(memory.get("swap_total"))),
        ),
    )


@check("system", "system.load", "check.system.load.title")
def system_load(context) -> CheckResult:
    outcome = context.read("system")
    if not outcome.ok:
        return _unavailable("system.load", "check.system.load.title", outcome)
    data = outcome.data or {}
    loads = data.get("loadavg") or []
    cpus = data.get("cpu_count") or 1
    if not loads:
        return CheckResult(id="system.load", group="system", status=runner.UNKNOWN,
                           title_key="check.system.load.title",
                           message_key="check.system.load.unreadable")
    per_cpu = loads[0] / max(1, cpus)
    status = runner.WARN if per_cpu >= LOAD_WARN_PER_CPU else runner.OK
    return CheckResult(
        id="system.load", group="system", status=status,
        title_key="check.system.load.title",
        message_key="check.system.load.high" if status == runner.WARN
        else "check.system.load.message",
        params={"load": f"{loads[0]:.2f}", "cpus": cpus},
        actual=f"{loads[0]:.2f}",
    )


@check("system", "system.temperature", "check.system.temperature.title")
def system_temperature(context) -> CheckResult:
    outcome = context.read("system")
    if not outcome.ok:
        return _unavailable("system.temperature", "check.system.temperature.title", outcome)
    readings = (outcome.data or {}).get("temperatures") or []
    if not readings:
        return CheckResult(id="system.temperature", group="system",
                           status=runner.UNAVAILABLE,
                           title_key="check.system.temperature.title",
                           message_key="check.system.temperature.no_sensor")
    hottest = max(readings, key=lambda entry: entry.get("celsius", 0))
    celsius = hottest.get("celsius", 0)
    if celsius >= TEMPERATURE_FAIL_CELSIUS:
        status, message = runner.FAIL, "check.system.temperature.critical"
    elif celsius >= TEMPERATURE_WARN_CELSIUS:
        status, message = runner.WARN, "check.system.temperature.high"
    else:
        status, message = runner.OK, "check.system.temperature.message"
    return CheckResult(
        id="system.temperature", group="system", status=status,
        title_key="check.system.temperature.title", message_key=message,
        params={"celsius": celsius, "zone": hottest.get("zone", "")},
        actual=f"{celsius} °C", data={"readings": readings},
    )


@check("system", "system.disk", "check.system.disk.title")
def system_disk(context) -> list:
    outcome = context.read("system")
    if not outcome.ok:
        return [_unavailable("system.disk", "check.system.disk.title", outcome)]
    disks = (outcome.data or {}).get("disks") or []
    if not disks:
        return [CheckResult(id="system.disk", group="system", status=runner.UNKNOWN,
                            title_key="check.system.disk.title",
                            message_key="check.system.disk.unreadable")]
    results = []
    for disk in disks:
        percent = disk.get("percent")
        if percent is None:
            continue
        if percent >= DISK_FAIL_PERCENT:
            status, message = runner.FAIL, "check.system.disk.full"
        elif percent >= DISK_WARN_PERCENT:
            status, message = runner.WARN, "check.system.disk.filling"
        else:
            status, message = runner.OK, "check.system.disk.message"
        actions = ["system.prune_dangling_images"] if status != runner.OK else []
        results.append(CheckResult(
            id=f"system.disk:{disk.get('mountpoint', '?')}", group="system",
            status=status, title_key="check.system.disk.title", message_key=message,
            params={"mountpoint": disk.get("mountpoint", "?"), "percent": percent,
                    "free": _human_bytes(disk.get("free")),
                    "total": _human_bytes(disk.get("total"))},
            actual=f"{percent} %", actions=actions,
            facts=facts_of(
                ("fact.mountpoint", disk.get("mountpoint")),
                ("fact.device", disk.get("device")),
                ("fact.filesystem", disk.get("fstype")),
                ("fact.disk_total", _human_bytes(disk.get("total"))),
                ("fact.disk_used", _human_bytes(disk.get("used"))),
                ("fact.disk_free", _human_bytes(disk.get("free"))),
            ),
        ))
    return results or [CheckResult(id="system.disk", group="system",
                                   status=runner.UNKNOWN,
                                   title_key="check.system.disk.title",
                                   message_key="check.system.disk.unreadable")]


# SMART is a property of a physical disk. LVM and device-mapper paths are not
# one, so they are skipped outright rather than reported as "not available",
# which would be noise the customer cannot act on.
PHYSICAL_DISK = re.compile(r"\A/dev/(sd[a-z]{1,2}|nvme\d{1,2}n\d{1,2}|mmcblk\d{1,2})\Z")


@check("system", "system.smart", "check.system.smart.title")
def system_smart(context) -> list:
    outcome = context.read("system")
    if not outcome.ok:
        return [_unavailable("system.smart", "check.system.smart.title", outcome)]
    devices = []
    for disk in (outcome.data or {}).get("disks") or []:
        device = disk.get("device", "")
        # Strip a partition suffix: SMART is a property of the whole disk.
        for suffix_length in (2, 1):
            if len(device) > suffix_length and device[-suffix_length:].isdigit():
                candidate = device[:-suffix_length]
                if candidate.endswith("p") and "nvme" in candidate:
                    candidate = candidate[:-1]
                device = candidate
                break
        if device and PHYSICAL_DISK.fullmatch(device) and device not in devices:
            devices.append(device)
    if not devices:
        return [CheckResult(id="system.smart", group="system", status=runner.UNAVAILABLE,
                            title_key="check.system.smart.title",
                            message_key="check.system.smart.no_device")]
    results = []
    for device in devices[:4]:
        smart = context.read("smart", device)
        if not smart.ok:
            results.append(CheckResult(
                id=f"system.smart:{device}", group="system", status=runner.UNAVAILABLE,
                title_key="check.system.smart.title",
                message_key="check.system.smart.unavailable",
                params={"device": device}, details=smart.detail))
            continue
        payload = (smart.data or {}).get("smart") or {}
        status_block = payload.get("smart_status") or {}
        passed = status_block.get("passed")
        if passed is True:
            status, message = runner.OK, "check.system.smart.passed"
        elif passed is False:
            status, message = runner.FAIL, "check.system.smart.failed"
        else:
            status, message = runner.UNKNOWN, "check.system.smart.unknown"
        results.append(CheckResult(
            id=f"system.smart:{device}", group="system", status=status,
            title_key="check.system.smart.title", message_key=message,
            params={"device": device}, actual=str(passed)))
    return results


@check("system", "system.time", "check.system.time.title")
def system_time(context) -> CheckResult:
    outcome = context.read("timesync")
    if not outcome.ok:
        return _unavailable("system.time", "check.system.time.title", outcome)
    values = (outcome.data or {}).get("values") or {}
    ntp_enabled = values.get("NTP", "").lower() == "yes"
    synchronised = values.get("NTPSynchronized", "").lower() == "yes"
    timezone = values.get("Timezone", "")

    if synchronised:
        return CheckResult(
            id="system.time", group="system", status=runner.OK,
            title_key="check.system.time.title", message_key="check.system.time.synced",
            params={"timezone": timezone}, actual=timezone,
            facts=facts_of(
                ("fact.timezone", timezone),
                ("fact.local_time", values.get("TimeUSec", "")),
                ("fact.ntp_enabled", "", yes_no(ntp_enabled)),
                ("fact.ntp_server", values.get("ServerName") or values.get("ServerAddress")),
            ),
            data={"ntp": ntp_enabled})
    message = "check.system.time.not_synced" if ntp_enabled else "check.system.time.ntp_off"
    return CheckResult(
        id="system.time", group="system", status=runner.FAIL,
        title_key="check.system.time.title", message_key=message,
        params={"timezone": timezone}, actual=timezone,
        actions=["system.sync_time"],
        facts=facts_of(
            ("fact.timezone", timezone),
            ("fact.local_time", values.get("TimeUSec", "")),
            ("fact.ntp_enabled", "", yes_no(ntp_enabled)),
            ("fact.ntp_synchronised", "", yes_no(synchronised)),
        ),
        data={"ntp": ntp_enabled})


@check("system", "system.boot_mode", "check.system.boot_mode.title")
def system_boot_mode(context) -> CheckResult:
    outcome = context.read("system")
    if not outcome.ok:
        return _unavailable("system.boot_mode", "check.system.boot_mode.title", outcome)
    boot = (outcome.data or {}).get("boot") or {}
    mode = boot.get("mode", "unknown")
    secure_boot = boot.get("secure_boot")
    # Informational on purpose: this can only be changed in the BIOS. The value
    # is that a change after an incident is visible immediately.
    return CheckResult(
        id="system.boot_mode", group="system", status=runner.OK,
        title_key="check.system.boot_mode.title",
        message_key="check.system.boot_mode.uefi" if mode == "uefi"
        else "check.system.boot_mode.legacy",
        params={"secure_boot": "on" if secure_boot else "off"},
        actual=mode,
        facts=facts_of(
            ("fact.boot_mode", mode.upper()),
            ("fact.secure_boot", "", yes_no(secure_boot)),
        ),
        data={"secure_boot": secure_boot})


@check("system", "system.machine_id", "check.system.machine_id.title")
def system_machine_id(context) -> CheckResult:
    outcome = context.read("system")
    if not outcome.ok:
        return _unavailable("system.machine_id", "check.system.machine_id.title", outcome)
    identity = (outcome.data or {}).get("machine_id") or {}
    current = identity.get("hash") or ""
    if not identity.get("present"):
        return CheckResult(
            id="system.machine_id", group="system", status=runner.FAIL,
            title_key="check.system.machine_id.title",
            message_key="check.system.machine_id.missing")
    expected = ""
    if isinstance(context.config, dict):
        expected = str((context.config.get("identity") or {}).get("machine_id_hash", ""))
    if not expected:
        return CheckResult(
            id="system.machine_id", group="system", status=runner.OK,
            title_key="check.system.machine_id.title",
            message_key="check.system.machine_id.not_recorded", actual=current[:23],
            facts=facts_of(("fact.machine_id", current)))
    if expected == current:
        return CheckResult(
            id="system.machine_id", group="system", status=runner.OK,
            title_key="check.system.machine_id.title",
            message_key="check.system.machine_id.stable", actual=current[:23],
            expected=expected[:23],
            facts=facts_of(("fact.machine_id", current)))
    # The licence is bound to this identifier, so a change breaks the POS in a
    # way whose cause is otherwise very hard to see.
    return CheckResult(
        id="system.machine_id", group="system", status=runner.FAIL,
        title_key="check.system.machine_id.title",
        message_key="check.system.machine_id.changed",
        actual=current[:23], expected=expected[:23],
        facts=facts_of(("fact.machine_id", current),
                       ("fact.machine_id_expected", expected)))


@check("system", "system.boots", "check.system.boots.title")
def system_boots(context) -> CheckResult:
    outcome = context.read("boots")
    if not outcome.ok:
        return _unavailable("system.boots", "check.system.boots.title", outcome)
    data = outcome.data or {}
    boots = data.get("boots") or []
    if not data.get("available") or not boots:
        return CheckResult(id="system.boots", group="system", status=runner.UNAVAILABLE,
                           title_key="check.system.boots.title",
                           message_key="check.system.boots.unavailable")
    return CheckResult(
        id="system.boots", group="system", status=runner.OK,
        title_key="check.system.boots.title", message_key="check.system.boots.message",
        params={"count": len(boots)},
        facts=facts_of(
            ("fact.boot_count", len(boots)),
            ("fact.last_boot", boots[-1].get("raw", "") if boots else ""),
        ),
        data={"boots": [entry.get("raw", "") for entry in boots[-10:]],
              "persistent_journal": data.get("persistent_journal")})
