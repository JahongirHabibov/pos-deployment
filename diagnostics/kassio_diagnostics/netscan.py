# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Reachability probing and the on-demand subnet scan.

Reachability is decided by a TCP connect, not by ICMP: a printer that answers on
port 9100 is printing, and several models suppress ICMP entirely. Ping is only
consulted as a secondary signal, so a silent-to-ping printer is never reported
as dead while its print port is open.

No payload is ever sent to a device. The probe opens a connection and closes it,
which is the least invasive question that still has a meaningful answer.

The scan is rate limited because its endpoint carries no login: without a brake
it would be a convenient way to hammer the LAN and every printer on it.
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_PORTS = (9100, 631, 80)
MAX_HOSTS = 256
MAX_WORKERS = 64
PROBE_TIMEOUT = 0.3

SCAN_MIN_INTERVAL_SECONDS = 30


def tcp_probe(host: str, port: int, timeout: float = PROBE_TIMEOUT) -> bool:
    """True when a TCP connection can be established. Sends no data."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError, OverflowError):
        return False


def ping(host: str, timeout: int = 1) -> bool:
    """Secondary signal only. Absence of ping is never treated as failure."""
    if shutil.which("ping") is None:
        return False
    try:
        proc = subprocess.run(
            ["ping", "-n", "-c", "1", "-W", str(max(1, int(timeout))), "--", host],
            capture_output=True, timeout=max(2, int(timeout) + 2), check=False,
        )
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return False


def probe_device(host: str, port: int, timeout: float = 1.0) -> dict:
    reachable_tcp = tcp_probe(host, port, timeout=timeout)
    # Only pay for the ping when TCP already said no; that is the only case
    # where the extra signal changes the wording shown to the customer.
    reachable_icmp = False if reachable_tcp else ping(host)
    return {
        "host": host,
        "port": port,
        "tcp": reachable_tcp,
        "icmp": reachable_icmp,
        "reachable": reachable_tcp or reachable_icmp,
    }


def hosts_of(subnet: str, limit: int = MAX_HOSTS) -> list:
    try:
        network = ipaddress.ip_network(subnet, strict=False)
    except (ValueError, TypeError):
        return []
    if network.num_addresses > 4096:
        return []
    return [str(host) for host in list(network.hosts())[:limit]]


def scan(subnet: str, ports=DEFAULT_PORTS, limit: int = MAX_HOSTS,
         timeout: float = PROBE_TIMEOUT) -> list:
    """Probe a subnet for open printer-ish ports. Returns only responders."""
    targets = hosts_of(subnet, limit)
    if not targets:
        return []
    found = []
    lock = threading.Lock()

    def probe(host: str) -> None:
        open_ports = [port for port in ports if tcp_probe(host, port, timeout)]
        if open_ports:
            with lock:
                found.append({"ip": host, "open_ports": open_ports})

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(targets))) as pool:
        list(pool.map(probe, targets))
    found.sort(key=lambda entry: ipaddress.ip_address(entry["ip"]))
    return found


class ScanLimiter:
    """One scan at a time, and not more often than once every 30 seconds."""

    def __init__(self, min_interval: float = SCAN_MIN_INTERVAL_SECONDS,
                 clock=time.monotonic):
        self._min_interval = min_interval
        self._clock = clock
        self._lock = threading.Lock()
        self._running = False
        self._last_finished = -float("inf")

    def acquire(self):
        """Return (True, 0) when a scan may start, else (False, seconds_to_wait)."""
        with self._lock:
            if self._running:
                return False, int(self._min_interval)
            waited = self._clock() - self._last_finished
            if waited < self._min_interval:
                return False, int(self._min_interval - waited) + 1
            self._running = True
            return True, 0

    def release(self) -> None:
        with self._lock:
            self._running = False
            self._last_finished = self._clock()
