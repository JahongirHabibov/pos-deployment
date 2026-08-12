# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
The degradation matrix from the design, one test per row.

The point of each of these is the same: when one part of the system is dead, the
diagnostics tool must still answer for every other part. A tool that goes blank
because Docker is down is useless precisely when it is needed.
"""

import pytest

from kassio_diagnostics import checks, netscan, runner
from kassio_diagnostics.checks import network as network_checks
from kassio_diagnostics.privileged import Outcome

checks.load_all()

SYSTEM_DATA = {
    "os": {"name": "Ubuntu 24.04", "id": "ubuntu", "version_id": "24.04"},
    "kernel": "6.8.0", "hostname": "pos-1", "architecture": "x86_64",
    "uptime_seconds": 90000.0, "loadavg": [0.2, 0.3, 0.4], "cpu_count": 4,
    "memory": {"total": 8 << 30, "available": 6 << 30, "used": 2 << 30, "percent": 25.0,
               "swap_total": 0, "swap_free": 0},
    "disks": [{"device": "/dev/sda1", "mountpoint": "/", "fstype": "ext4",
               "total": 100 << 30, "used": 40 << 30, "free": 60 << 30, "percent": 40.0}],
    "temperatures": [{"zone": "x86_pkg_temp", "celsius": 45.0}],
    "boot": {"mode": "uefi", "secure_boot": False},
    "machine_id": {"present": True, "hash": "sha256:abc"},
}

NETWORK_DATA = {
    "addresses": [{"ifname": "enp3s0", "operstate": "UP",
                   "addr_info": [{"family": "inet", "local": "192.168.1.10",
                                  "prefixlen": 24, "dynamic": False}]}],
    "routes": [{"dst": "default", "gateway": "192.168.1.1"}],
    "neighbours": [{"dst": "192.168.1.87", "lladdr": "00:26:ab:12:34:56",
                    "state": ["REACHABLE"]}],
    "nameservers": ["192.168.1.1"], "tool_available": True,
}

CONTAINERS_DATA = {"available": True, "containers": [
    {"name": "pos-backend", "image": "img", "state": "running", "status": "Up",
     "created": "", "ports": ""}]}

CONFIG = {
    "schema_version": 1,
    "site": {"name": "Test", "technician": "t", "configured_at": "", "language": "de"},
    "network": {"interface": "enp3s0", "subnet": "192.168.1.0/24",
                "gateway": "192.168.1.1", "addressing": "static"},
    "identity": {"machine_id_hash": "sha256:abc"},
    "devices": [{"id": "receipt-1", "name": "Bondrucker", "role": "receipt_printer",
                 "ip": "192.168.1.50", "mac": "00:26:ab:12:34:56", "port": 9100}],
    "containers": ["pos-backend"],
}


class FakePrivileged:
    """Answers helper reads from a table; anything absent counts as broken."""

    def __init__(self, table=None, broken=()):
        self.table = dict(table or {})
        self.broken = set(broken)

    def read(self, verb, *args, timeout=40):
        if verb in self.broken:
            return Outcome(False, None, "error.command_failed", "simulated failure")
        if verb not in self.table:
            return Outcome(False, None, "error.unavailable", "not simulated")
        value = self.table[verb]
        return Outcome(True, value(*args) if callable(value) else value)

    def act(self, verb, *args, password=b"", stdin_data=b"", timeout=150):
        return Outcome(True, {"verb": verb})

    def verify_password(self, password):
        return Outcome(True, {"verified": True})


@pytest.fixture
def offline(monkeypatch):
    """No test may touch the network."""
    monkeypatch.setattr(netscan, "tcp_probe", lambda host, port, timeout=0.3: False)
    monkeypatch.setattr(netscan, "ping", lambda host, timeout=1: False)
    from kassio_diagnostics.checks import pos as pos_checks
    monkeypatch.setattr(pos_checks, "_probe", lambda url: (0, "offline"))
    monkeypatch.setattr(network_checks, "resolve_name", lambda name: False)


def build_context(privileged, config=CONFIG):
    return runner.Context(privileged, config, [], "", {"POS_PUBLIC_PORT": "80"})


def groups_present(results):
    return {result.group for result in results}


def statuses(results, prefix):
    return [r.status for r in results if r.id.startswith(prefix)]


def test_everything_available_produces_all_groups(offline):
    privileged = FakePrivileged({
        "system": SYSTEM_DATA, "network": NETWORK_DATA, "containers": CONTAINERS_DATA,
        "timesync": {"values": {"NTP": "yes", "NTPSynchronized": "yes",
                                "Timezone": "Europe/Berlin"}},
        "services": {"units": {"docker.service": {"active": "active",
                                                  "enabled": "enabled"}}},
        "usb": {"devices": [], "printer_nodes": []},
        "boots": {"available": True, "boots": [], "persistent_journal": True},
        "container-inspect": {"name": "pos-backend", "available": True,
                              "state": "running", "health": None, "restart_count": 0},
    })
    results = runner.run(build_context(privileged))
    assert groups_present(results) >= {"system", "network", "devices", "docker",
                                       "services", "pos"}
    assert all(result.status != runner.UNKNOWN or result.message_key != "check.crashed"
               for result in results)


def test_docker_dead_leaves_every_other_group_working(offline):
    privileged = FakePrivileged({
        "system": SYSTEM_DATA, "network": NETWORK_DATA,
        "timesync": {"values": {"NTPSynchronized": "yes", "Timezone": "UTC"}},
        "services": {"units": {}}, "usb": {"devices": [], "printer_nodes": []},
        "boots": {"available": False, "boots": []},
    }, broken=["containers", "container-inspect"])
    results = runner.run(build_context(privileged))
    assert groups_present(results) >= {"system", "network", "devices", "services"}
    assert any(r.group == "system" and r.status == runner.OK for r in results)
    assert any(r.group == "docker" and r.status in (runner.FAIL, runner.UNAVAILABLE)
               for r in results)


def test_network_dead_leaves_system_and_docker_working(offline):
    privileged = FakePrivileged({
        "system": SYSTEM_DATA, "containers": CONTAINERS_DATA,
        "timesync": {"values": {"NTPSynchronized": "yes", "Timezone": "UTC"}},
        "services": {"units": {}}, "usb": {"devices": [], "printer_nodes": []},
        "boots": {"available": False, "boots": []},
        "container-inspect": {"name": "pos-backend", "available": True,
                              "state": "running", "health": None, "restart_count": 0},
    }, broken=["network"])
    results = runner.run(build_context(privileged))
    assert any(r.group == "system" and r.status == runner.OK for r in results)
    assert any(r.id.startswith("docker.container:") and r.status == runner.OK
               for r in results)


def test_missing_config_still_runs_every_config_free_check(offline):
    privileged = FakePrivileged({
        "system": SYSTEM_DATA, "network": NETWORK_DATA, "containers": CONTAINERS_DATA,
        "timesync": {"values": {"NTPSynchronized": "yes", "Timezone": "UTC"}},
        "services": {"units": {}}, "usb": {"devices": [], "printer_nodes": []},
        "boots": {"available": False, "boots": []},
        "container-inspect": {"name": "pos-backend", "available": True,
                              "state": "running", "health": None, "restart_count": 0},
    })
    results = runner.run(build_context(privileged, config=None))
    assert any(r.group == "system" and r.status == runner.OK for r in results)
    # The device group says "nothing recorded" rather than "all fine".
    configured = [r for r in results if r.id == "devices.configured"]
    assert configured and configured[0].status == runner.WARN
    assert configured[0].actions == ["setup.open_wizard"]


def test_missing_optional_tool_degrades_only_that_check(offline):
    privileged = FakePrivileged({
        "system": SYSTEM_DATA, "network": NETWORK_DATA, "containers": CONTAINERS_DATA,
        "timesync": {"values": {"NTPSynchronized": "yes", "Timezone": "UTC"}},
        "services": {"units": {}}, "usb": {"devices": [], "printer_nodes": []},
        "boots": {"available": False, "boots": []},
        "container-inspect": {"name": "pos-backend", "available": True,
                              "state": "running", "health": None, "restart_count": 0},
    }, broken=["smart"])
    results = runner.run(build_context(privileged))
    smart = [r for r in results if r.id.startswith("system.smart")]
    assert smart and all(r.status == runner.UNAVAILABLE for r in smart)
    assert any(r.id == "system.disk:/" and r.status == runner.OK for r in results)


def test_pos_backend_dead_does_not_break_device_checks(offline):
    privileged = FakePrivileged({
        "system": SYSTEM_DATA, "network": NETWORK_DATA, "containers": CONTAINERS_DATA,
        "timesync": {"values": {"NTPSynchronized": "yes", "Timezone": "UTC"}},
        "services": {"units": {}}, "usb": {"devices": [], "printer_nodes": []},
        "boots": {"available": False, "boots": []},
        "container-inspect": {"name": "pos-backend", "available": True,
                              "state": "running", "health": None, "restart_count": 0},
    })
    results = runner.run(build_context(privileged))
    assert any(r.group == "pos" and r.status == runner.FAIL for r in results)
    assert statuses(results, "devices.device:")


def test_printer_moved_is_diagnosed_with_the_new_address(offline, monkeypatch):
    # Answers at .87 but not at the recorded .50 — the real field failure.
    monkeypatch.setattr(netscan, "probe_device", lambda host, port, timeout=1.0: {
        "host": host, "port": port, "tcp": host == "192.168.1.87",
        "icmp": False, "reachable": host == "192.168.1.87"})
    privileged = FakePrivileged({"network": NETWORK_DATA})
    results = runner.run(build_context(privileged), ["devices"])
    device = [r for r in results if r.id == "devices.device:receipt-1"][0]
    assert device.status == runner.FAIL
    assert device.actual == "192.168.1.87"
    assert device.expected == "192.168.1.50"
    assert device.message_key == "check.devices.moved_dhcp"
    assert "printer.adopt_found_ip" in device.actions
    assert device.data["web_ui"] == "http://192.168.1.87/"


def test_printer_present_at_the_recorded_address_is_ok(offline, monkeypatch):
    monkeypatch.setattr(netscan, "probe_device", lambda host, port, timeout=1.0: {
        "host": host, "port": port, "tcp": True, "icmp": False, "reachable": True})
    neighbours = dict(NETWORK_DATA)
    neighbours["neighbours"] = [{"dst": "192.168.1.50",
                                 "lladdr": "00:26:ab:12:34:56", "state": ["REACHABLE"]}]
    privileged = FakePrivileged({"network": neighbours})
    results = runner.run(build_context(privileged), ["devices"])
    device = [r for r in results if r.id == "devices.device:receipt-1"][0]
    assert device.status == runner.OK


def test_foreign_device_at_the_recorded_address_is_flagged(offline, monkeypatch):
    monkeypatch.setattr(netscan, "probe_device", lambda host, port, timeout=1.0: {
        "host": host, "port": port, "tcp": True, "icmp": False, "reachable": True})
    data = dict(NETWORK_DATA)
    data["neighbours"] = [{"dst": "192.168.1.50", "lladdr": "aa:bb:cc:dd:ee:ff",
                           "state": ["REACHABLE"]}]
    privileged = FakePrivileged({"network": data})
    results = runner.run(build_context(privileged), ["devices"])
    device = [r for r in results if r.id == "devices.device:receipt-1"][0]
    assert device.status == runner.WARN
    assert device.message_key == "check.devices.foreign_device"
