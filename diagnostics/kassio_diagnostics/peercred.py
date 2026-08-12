# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Peer identification for loopback TCP connections.

Binding to 127.0.0.1 keeps the network out, but it does NOT keep other local
accounts out: a loopback socket is reachable by every user on the machine.
Without this module a secondary account or a compromised local service could
read container logs and the full support report through the endpoints that
carry no login.

There is no SO_PEERCRED for TCP, so the owning UID is resolved through
/proc/net/tcp and /proc/net/tcp6 by matching the connection's four-tuple.

The rule is deny-by-default: if the UID cannot be determined — the connection
was already closed, /proc is unreadable — access is refused.
"""

from __future__ import annotations

import ipaddress

PROC_TCP_FILES = ("/proc/net/tcp", "/proc/net/tcp6")


def _decode_address(field: str):
    """Turn a /proc/net/tcp address field into (ip, port), or None."""
    address, _, port = field.partition(":")
    try:
        port_number = int(port, 16)
    except ValueError:
        return None
    try:
        raw = bytes.fromhex(address)
    except ValueError:
        return None
    # Each 32-bit word is stored in host byte order, which is little-endian on
    # every platform this runs on.
    if len(raw) == 4:
        packed = raw[::-1]
    elif len(raw) == 16:
        packed = b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))
    else:
        return None
    try:
        return ipaddress.ip_address(packed), port_number
    except ValueError:
        return None


def _normalise(host: str):
    """Normalise a socket address, collapsing IPv4-mapped IPv6 to IPv4."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _iter_rows(path: str, opener):
    try:
        with opener(path) as handle:
            handle.readline()  # header
            for line in handle:
                yield line.split()
    except OSError:
        return


def peer_uid(client_host: str, client_port: int,
             server_host: str, server_port: int, opener=open):
    """Return the UID owning the peer socket, or None if it cannot be proven."""
    client = _normalise(client_host)
    server = _normalise(server_host)
    if client is None or server is None:
        return None

    for path in PROC_TCP_FILES:
        for fields in _iter_rows(path, opener):
            if len(fields) < 8:
                continue
            local = _decode_address(fields[1])
            remote = _decode_address(fields[2])
            if local is None or remote is None:
                continue
            # From the client process's point of view the connection is
            # local=client, remote=server.
            if local[1] != client_port or remote[1] != server_port:
                continue
            if _normalise(str(local[0])) != client or _normalise(str(remote[0])) != server:
                continue
            try:
                return int(fields[7])
            except ValueError:
                return None
    return None
