# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
The systemd unit's writable paths, and the values shown on the cards.

The unit test exists because of a defect that no amount of Python review would
have found: ProtectSystem=full mounts /etc read-only for the whole unit — the
manual says so plainly — including the root child started through sudo. Every
attempt to save the site configuration therefore failed with "read-only file
system", and the interface reported a command that simply refused to work.

The fact tests exist because a status word on its own is not something a
customer can read out to support. If a check shows a value, that value has to be
translated in all three languages, or a Russian screen ends up half German.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from kassio_diagnostics import checks, config as config_module, runner
from kassio_diagnostics.privileged import Outcome

DIAGNOSTICS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIT_PATH = os.path.join(DIAGNOSTICS_DIR, "kassio-diagnostics.service")
LOCALE_DIR = os.path.join(DIAGNOSTICS_DIR, "locales")

checks.load_all()


def unit_text() -> str:
    with open(UNIT_PATH, encoding="utf-8") as handle:
        return handle.read()


def locale(language: str = "de") -> dict:
    with open(os.path.join(LOCALE_DIR, f"{language}.json"), encoding="utf-8") as handle:
        return json.load(handle)


# ------------------------------------------------------------------- unit


def test_the_configuration_directory_is_writable_for_the_service():
    """Without this the site configuration can never be saved."""
    text = unit_text()
    protect = re.search(r"^ProtectSystem=(\w+)", text, re.M)
    assert protect, "ProtectSystem is no longer set"
    if protect.group(1) in ("full", "strict"):
        assert re.search(r"^ReadWritePaths=-?/etc/kassio-diagnostics", text, re.M), (
            f"ProtectSystem={protect.group(1)} mounts /etc read-only for the whole "
            "unit, so the configuration directory needs an explicit exception")


def test_the_writable_exception_is_limited_to_this_tool():
    for match in re.findall(r"^ReadWritePaths=(.+)$", unit_text(), re.M):
        for path in match.split():
            assert path.lstrip("-").startswith("/etc/kassio-diagnostics"), path


def test_no_new_privileges_stays_off_so_sudo_keeps_working():
    text = unit_text()
    assert not re.search(r"^NoNewPrivileges=yes", text, re.M), (
        "NoNewPrivileges blocks setuid binaries and therefore sudo, on which the "
        "whole privilege model rests")


def test_netlink_is_permitted_for_the_ip_command():
    assert re.search(r"^RestrictAddressFamilies=.*AF_NETLINK", unit_text(), re.M)


def test_listening_is_restricted_without_blocking_outbound_traffic():
    text = unit_text()
    assert re.search(r"^SocketBindDeny=any", text, re.M)
    assert not re.search(r"^IPAddressDeny=", text, re.M), (
        "IPAddress filters apply to every socket and would block the subnet "
        "scan, the printer probes and the POS API")


def test_core_dumps_are_disabled_so_the_password_cannot_leak():
    assert re.search(r"^LimitCORE=0", unit_text(), re.M)


# ------------------------------------------------------------------ facts


def test_facts_of_drops_entries_without_a_value():
    built = runner.facts_of(("a", "x"), ("b", ""), ("c", None), ("d", []), ("e", 0))
    labels = [entry["label_key"] for entry in built]
    assert labels == ["a", "e"], "an empty row reads as missing data"


def test_a_fact_may_carry_a_translatable_value():
    built = runner.facts_of(("a", "", "fact.value.yes"))
    assert built == [{"label_key": "a", "value_key": "fact.value.yes", "value": ""}]


@pytest.mark.parametrize("flag,expected", [
    (True, "fact.value.yes"), (False, "fact.value.no"), (None, "fact.value.unknown"),
])
def test_boolean_facts_use_locale_keys(flag, expected):
    assert runner.yes_no(flag) == expected


SYSTEM_DATA = {
    "os": {"name": "Ubuntu 24.04", "id": "ubuntu", "version_id": "24.04"},
    "kernel": "6.8.0", "hostname": "pos-1", "architecture": "x86_64",
    "uptime_seconds": 90000.0, "loadavg": [0.2, 0.3, 0.4], "cpu_count": 4,
    "memory": {"total": 8 << 30, "available": 6 << 30, "used": 2 << 30,
               "percent": 25.0, "swap_total": 0, "swap_free": 0},
    "disks": [{"device": "/dev/sda1", "mountpoint": "/", "fstype": "ext4",
               "total": 100 << 30, "used": 40 << 30, "free": 60 << 30,
               "percent": 40.0}],
    "temperatures": [{"zone": "x86_pkg_temp", "celsius": 45.0}],
    "boot": {"mode": "uefi", "secure_boot": False},
    "machine_id": {"present": True, "hash": "sha256:abc"},
}
NETWORK_DATA = {
    "addresses": [{"ifname": "enp3s0", "operstate": "UP",
                   "addr_info": [{"family": "inet", "local": "192.168.1.10",
                                  "prefixlen": 24, "dynamic": False}]}],
    "routes": [{"dst": "default", "gateway": "192.168.1.1"}],
    "neighbours": [{"dst": "192.168.1.50", "lladdr": "00:26:ab:12:34:56",
                    "state": ["REACHABLE"]}],
    "nameservers": ["192.168.1.1"], "tool_available": True,
}
CONFIG = {
    "schema_version": 1,
    "site": {"name": "Test", "technician": "t", "configured_at": "", "language": "de"},
    "network": {"interface": "enp3s0", "subnet": "192.168.1.0/24",
                "gateway": "192.168.1.1", "addressing": "static"},
    "identity": {"machine_id_hash": "sha256:abc"},
    "devices": [{"id": "receipt-1", "name": "Bondrucker", "role": "receipt_printer",
                 "ip": "192.168.1.50", "mac": "00:26:ab:12:34:56", "port": 9100,
                 "model": "TM-m30III"}],
    "containers": ["pos-backend"],
}


class FakePrivileged:
    TABLE = {
        "system": SYSTEM_DATA, "network": NETWORK_DATA,
        "timesync": {"values": {"NTP": "yes", "NTPSynchronized": "yes",
                                "Timezone": "Europe/Berlin"}},
        "services": {"units": {"docker.service": {"active": "active",
                                                  "enabled": "enabled"}}},
        "usb": {"devices": [{"description": "ID 04b8:0202 Epson"}],
                "printer_nodes": ["/dev/usb/lp0"]},
        "boots": {"available": True, "boots": [{"raw": "-1 abc"}],
                  "persistent_journal": True},
        "containers": {"available": True, "containers": [
            {"name": "pos-backend", "image": "img:1", "state": "running",
             "status": "Up 2 hours", "created": "", "ports": ""}]},
        "container-inspect": {"name": "pos-backend", "available": True,
                              "state": "running", "health": "healthy",
                              "restart_count": 0},
    }

    def read(self, verb, *args, timeout=40):
        if verb not in self.TABLE:
            return Outcome(False, None, "error.unavailable", "")
        return Outcome(True, self.TABLE[verb])


@pytest.fixture
def all_results(monkeypatch):
    from kassio_diagnostics import netscan
    from kassio_diagnostics.checks import network as network_checks
    from kassio_diagnostics.checks import pos as pos_checks
    monkeypatch.setattr(netscan, "tcp_probe", lambda host, port, timeout=0.3: True)
    monkeypatch.setattr(netscan, "ping", lambda host, timeout=1: True)
    monkeypatch.setattr(netscan, "probe_device", lambda host, port, timeout=1.0: {
        "host": host, "port": port, "tcp": True, "icmp": False, "reachable": True})
    monkeypatch.setattr(network_checks, "resolve_name", lambda name: True)
    monkeypatch.setattr(pos_checks, "_probe", lambda url: (200, ""))
    context = runner.Context(FakePrivileged(), CONFIG, [], "",
                             {"POS_PUBLIC_PORT": "80"})
    return runner.run(context)


def test_the_important_checks_show_concrete_values(all_results):
    with_facts = {result.id for result in all_results if result.facts}
    for expected in ("system.os", "system.time", "system.boot_mode",
                     "network.address", "network.gateway",
                     "devices.device:receipt-1", "docker.container:pos-backend"):
        assert expected in with_facts, f"{expected} shows no concrete values"


def test_the_printer_card_names_expected_and_actual_addresses(all_results):
    device = [r for r in all_results if r.id == "devices.device:receipt-1"][0]
    labels = {entry["label_key"] for entry in device.facts}
    assert {"fact.ip_expected", "fact.mac_expected", "fact.port",
            "fact.device_name"} <= labels


def test_every_label_and_value_key_shown_is_translated(all_results):
    strings = locale("de")
    missing = set()
    for result in all_results:
        for key in (result.title_key, result.message_key):
            if key and key not in strings:
                missing.add(key)
        for entry in result.facts:
            if entry["label_key"] not in strings:
                missing.add(entry["label_key"])
            if entry["value_key"] and entry["value_key"] not in strings:
                missing.add(entry["value_key"])
    assert not missing, f"untranslated keys reach the screen: {sorted(missing)}"


def test_facts_survive_the_json_round_trip(all_results):
    payload = json.loads(json.dumps([result.as_dict() for result in all_results]))
    device = [entry for entry in payload
              if entry["id"] == "devices.device:receipt-1"][0]
    assert device["facts"]
    assert all({"label_key", "value", "value_key"} <= set(entry)
               for entry in device["facts"])


def test_saving_records_when_it_happened_and_keeps_the_rest():
    document = config_module.stamp(dict(CONFIG))
    assert document["site"]["configured_at"]
    assert config_module.validate(document) == []
