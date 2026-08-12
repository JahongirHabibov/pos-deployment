# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Resolving the UID behind a loopback connection.

The behaviour that matters most is the negative one: when ownership cannot be
proven, the answer is None and the caller refuses. An access check that guesses
is not an access check.
"""

import io

from kassio_diagnostics import peercred

HEADER = ("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
          "retrnsmt   uid  timeout inode\n")

# 127.0.0.1 is stored as 0100007F: each 32-bit word in host byte order.
LOCALHOST = "0100007F"


def row(local_port, remote_port, uid, local=LOCALHOST, remote=LOCALHOST):
    return (f"   0: {local}:{local_port:04X} {remote}:{remote_port:04X} 01 "
            f"00000000:00000000 00:00000000  00000000  {uid}        0 12345\n")


def opener_for(tcp_content, tcp6_content=HEADER):
    def opener(path):
        if path.endswith("tcp6"):
            return io.StringIO(tcp6_content)
        return io.StringIO(tcp_content)
    return opener


def test_uid_is_found_for_a_matching_connection():
    content = HEADER + row(0x8B34, 0x23A0, 1000)
    uid = peercred.peer_uid("127.0.0.1", 0x8B34, "127.0.0.1", 0x23A0,
                            opener=opener_for(content))
    assert uid == 1000


def test_a_different_port_does_not_match():
    content = HEADER + row(0x8B34, 0x23A0, 1000)
    assert peercred.peer_uid("127.0.0.1", 0x9999, "127.0.0.1", 0x23A0,
                             opener=opener_for(content)) is None


def test_a_different_address_does_not_match():
    content = HEADER + row(0x8B34, 0x23A0, 1000, local="0200007F")
    assert peercred.peer_uid("127.0.0.1", 0x8B34, "127.0.0.1", 0x23A0,
                             opener=opener_for(content)) is None


def test_missing_proc_files_deny_rather_than_allow():
    def opener(path):
        raise OSError("no /proc here")
    assert peercred.peer_uid("127.0.0.1", 1, "127.0.0.1", 2, opener=opener) is None


def test_a_truncated_line_is_skipped_without_raising():
    content = HEADER + "   0: garbage\n" + row(0x8B34, 0x23A0, 1000)
    assert peercred.peer_uid("127.0.0.1", 0x8B34, "127.0.0.1", 0x23A0,
                             opener=opener_for(content)) == 1000


def test_a_non_numeric_uid_yields_none():
    content = HEADER + (
        "   0: 0100007F:8B34 0100007F:23A0 01 00000000:00000000 00:00000000 "
        "00000000  root        0 12345\n")
    assert peercred.peer_uid("127.0.0.1", 0x8B34, "127.0.0.1", 0x23A0,
                             opener=opener_for(content)) is None


def test_invalid_host_strings_yield_none():
    content = HEADER + row(0x8B34, 0x23A0, 1000)
    assert peercred.peer_uid("not-an-ip", 1, "127.0.0.1", 2,
                             opener=opener_for(content)) is None


def test_ipv4_mapped_ipv6_peers_are_matched():
    # The socket may report ::ffff:127.0.0.1 while /proc lists the IPv4 form.
    content = HEADER + row(0x8B34, 0x23A0, 1000)
    uid = peercred.peer_uid("::ffff:127.0.0.1", 0x8B34, "::ffff:127.0.0.1", 0x23A0,
                            opener=opener_for(content))
    assert uid == 1000


def test_ipv6_loopback_rows_are_decoded():
    ipv6_localhost = "00000000000000000000000001000000"
    content = HEADER
    tcp6 = HEADER + (f"   0: {ipv6_localhost}:8B34 {ipv6_localhost}:23A0 01 "
                     f"00000000:00000000 00:00000000  00000000  1000        0 1\n")
    uid = peercred.peer_uid("::1", 0x8B34, "::1", 0x23A0,
                            opener=opener_for(content, tcp6))
    assert uid == 1000
