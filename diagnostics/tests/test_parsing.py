# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""Parsing of MAC addresses, vendors, interfaces and POS settings."""

import pytest

from kassio_diagnostics import posapi, vendors
from kassio_diagnostics.checks import network as network_checks


@pytest.mark.parametrize("value,expected", [
    ("00:26:AB:12:34:56", "00:26:ab:12:34:56"),
    ("00-26-ab-12-34-56", "00:26:ab:12:34:56"),
    ("  00:26:ab:12:34:56  ", "00:26:ab:12:34:56"),
    ("00:26:ab:12:34", ""),
    ("00:26:ab:12:34:5g", ""),
    ("", ""),
    (None, ""),
    (12345, ""),
])
def test_mac_normalisation(value, expected):
    assert vendors.normalise_mac(value) == expected


def test_a_known_prefix_identifies_the_vendor():
    assert vendors.vendor_for_mac("00:26:ab:12:34:56")["id"] == "epson"
    assert vendors.vendor_for_mac("00:11:62:aa:bb:cc")["id"] == "star"


def test_an_unknown_prefix_falls_back_to_generic():
    assert vendors.vendor_for_mac("de:ad:be:ef:00:01")["id"] == "generic"
    assert vendors.vendor_for_mac("")["id"] == "generic"


def test_an_explicit_vendor_wins_over_the_prefix():
    device = {"vendor": "star", "mac": "00:26:ab:12:34:56"}
    assert vendors.resolve(device)["id"] == "star"


def test_an_observed_mac_is_used_when_the_device_has_none():
    assert vendors.resolve({}, "00:11:62:aa:bb:cc")["id"] == "star"


def test_the_web_ui_url_points_at_the_device():
    vendor = vendors.vendor_by_id("epson")
    assert vendors.web_ui_url(vendor, "192.168.1.87") == "http://192.168.1.87/"
    assert vendors.web_ui_url(vendor, "") == ""


def test_every_vendor_has_an_instruction_text():
    from kassio_diagnostics import i18n
    for vendor in vendors.VENDORS.values():
        assert i18n.translate("de", vendor["instructions_key"]) != \
               vendor["instructions_key"]


# ------------------------------------------------------------ interfaces

ADDRESSES = {
    "addresses": [
        {"ifname": "lo", "operstate": "UNKNOWN",
         "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
        {"ifname": "enp3s0", "operstate": "UP",
         "addr_info": [{"family": "inet", "local": "192.168.1.10", "prefixlen": 24}]},
        {"ifname": "wlan0", "operstate": "DOWN",
         "addr_info": [{"family": "inet", "local": "192.168.5.5", "prefixlen": 24,
                        "dynamic": True}]},
    ],
    "routes": [{"dst": "192.168.1.0/24"}, {"dst": "default", "gateway": "192.168.1.1"}],
    "neighbours": [{"dst": "192.168.1.50", "lladdr": "00:26:AB:12:34:56",
                    "state": ["REACHABLE"]},
                   {"dst": "192.168.1.99", "state": ["FAILED"]}],
}


def test_loopback_is_never_the_primary_interface():
    assert network_checks.primary_interface(ADDRESSES)["ifname"] == "enp3s0"


def test_the_configured_interface_wins():
    assert network_checks.primary_interface(ADDRESSES, "wlan0")["ifname"] == "wlan0"


def test_a_missing_configured_interface_falls_back_to_the_live_one():
    assert network_checks.primary_interface(ADDRESSES, "eth9")["ifname"] == "enp3s0"


def test_no_addresses_yields_an_empty_result():
    assert network_checks.primary_interface({}) == {}
    assert network_checks.primary_interface({"addresses": "nonsense"}) == {}


def test_the_default_gateway_is_extracted():
    assert network_checks.default_gateway(ADDRESSES) == "192.168.1.1"
    assert network_checks.default_gateway({"routes": []}) == ""


def test_neighbours_without_a_hardware_address_are_dropped():
    entries = network_checks.neighbour_table(ADDRESSES)
    assert entries == [{"ip": "192.168.1.50", "mac": "00:26:ab:12:34:56",
                        "state": "REACHABLE"}]


# --------------------------------------------------------- POS settings


def test_printer_address_settings_are_found_in_a_nested_document():
    settings = {
        "printer": {"receipt": {"ip": "192.168.1.50", "width": 48}},
        "kitchen_printer_host": "192.168.1.60",
        "shop": {"name": "Test", "ip": "irrelevant"},
    }
    found = posapi.printer_address_settings(settings)
    assert found == {"printer.receipt.ip": "192.168.1.50",
                     "kitchen_printer_host": "192.168.1.60"}


def test_flatten_keeps_scalar_lists_whole():
    flat = posapi.flatten_settings({"a": {"b": [1, 2, 3]}, "c": "x"})
    assert flat == {"a.b": [1, 2, 3], "c": "x"}


def test_no_printer_settings_yields_an_empty_mapping():
    assert posapi.printer_address_settings({"shop": {"name": "Test"}}) == {}
