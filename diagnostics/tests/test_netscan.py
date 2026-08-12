# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""Probing helpers and the scan rate limit. Nothing here opens a socket."""

from kassio_diagnostics import netscan


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_the_first_scan_is_allowed():
    limiter = netscan.ScanLimiter(clock=Clock())
    allowed, wait = limiter.acquire()
    assert allowed and wait == 0


def test_a_second_concurrent_scan_is_refused():
    limiter = netscan.ScanLimiter(clock=Clock())
    limiter.acquire()
    allowed, wait = limiter.acquire()
    assert not allowed and wait > 0


def test_a_scan_right_after_the_previous_one_is_refused():
    clock = Clock()
    limiter = netscan.ScanLimiter(clock=clock)
    limiter.acquire()
    limiter.release()
    clock.advance(5)
    allowed, wait = limiter.acquire()
    assert not allowed and wait > 0


def test_a_scan_is_allowed_again_after_the_interval():
    clock = Clock()
    limiter = netscan.ScanLimiter(clock=clock)
    limiter.acquire()
    limiter.release()
    clock.advance(netscan.SCAN_MIN_INTERVAL_SECONDS + 1)
    allowed, _ = limiter.acquire()
    assert allowed


def test_hosts_of_expands_a_small_subnet():
    hosts = netscan.hosts_of("192.168.1.0/24")
    assert hosts[0] == "192.168.1.1"
    assert len(hosts) == 254


def test_hosts_of_refuses_an_oversized_subnet():
    assert netscan.hosts_of("10.0.0.0/8") == []


def test_hosts_of_tolerates_nonsense():
    assert netscan.hosts_of("not a subnet") == []
    assert netscan.hosts_of("") == []


def test_hosts_of_honours_the_limit():
    assert len(netscan.hosts_of("192.168.1.0/24", limit=10)) == 10


def test_probe_device_prefers_tcp_and_only_then_asks_icmp(monkeypatch):
    calls = []
    monkeypatch.setattr(netscan, "tcp_probe",
                        lambda host, port, timeout=0.3: calls.append("tcp") or True)
    monkeypatch.setattr(netscan, "ping",
                        lambda host, timeout=1: calls.append("icmp") or True)
    result = netscan.probe_device("192.168.1.50", 9100)
    assert result["reachable"] is True
    # ICMP is not even attempted when the print port answers.
    assert calls == ["tcp"]


def test_a_printer_that_ignores_ping_is_still_reachable(monkeypatch):
    monkeypatch.setattr(netscan, "tcp_probe", lambda host, port, timeout=0.3: True)
    monkeypatch.setattr(netscan, "ping", lambda host, timeout=1: False)
    assert netscan.probe_device("192.168.1.50", 9100)["reachable"] is True


def test_ping_only_reachability_is_reported(monkeypatch):
    monkeypatch.setattr(netscan, "tcp_probe", lambda host, port, timeout=0.3: False)
    monkeypatch.setattr(netscan, "ping", lambda host, timeout=1: True)
    result = netscan.probe_device("192.168.1.50", 9100)
    assert result["reachable"] is True and result["tcp"] is False


def test_an_oversized_subnet_is_recognised():
    # A /19 is 8192 addresses — sweeping it is a load test, not a diagnosis.
    assert netscan.too_large_to_scan("10.64.0.0/19") is True
    assert netscan.too_large_to_scan("192.168.1.0/24") is False
    assert netscan.too_large_to_scan("10.0.0.0/22") is False


def test_nonsense_is_not_reported_as_oversized():
    # Invalid input is a different fault and must not be masked as "too large".
    assert netscan.too_large_to_scan("not a subnet") is False
    assert netscan.too_large_to_scan("") is False


def test_the_scan_action_refuses_an_oversized_subnet_with_a_reason():
    from kassio_diagnostics.actions import ActionContext
    from kassio_diagnostics.actions.printer import scan_network

    class NoPrivileged:
        def read(self, verb, *args, timeout=40):
            raise AssertionError("an oversized subnet must be refused before probing")

    context = ActionContext(privileged=NoPrivileged(), scan_limiter=None,
                            config={"network": {"subnet": "10.64.0.0/19"}})
    result = scan_network(context, {})
    assert result.ok is False
    assert result.message_key == "action.devices.scan.subnet_too_large"
    assert result.params["subnet"] == "10.64.0.0/19"
