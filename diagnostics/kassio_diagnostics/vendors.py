# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Printer vendor recognition.

Deliberately a plain data table rather than a plugin architecture: in phase one
nothing is executed per vendor, only a different instruction text and a link to
the printer's own web interface are shown. Building an abstract interface for
zero implementations would be architecture on speculation.

Recognition never changes what the tool is allowed to do — it only selects which
instructions the customer reads. A wrong or missing match therefore degrades to
the generic instructions rather than to a wrong action.

OUI prefixes are added only after verification against the IEEE registry; an
unlisted prefix is a missing convenience, not a defect.
"""

from __future__ import annotations

VENDOR_GENERIC = "generic"

# prefix (lowercase, colon separated) -> vendor id
OUI_TABLE = {
    "00:00:48": "epson",
    "00:26:ab": "epson",
    "64:eb:8c": "epson",
    "00:11:62": "star",
}

VENDORS = {
    "epson": {
        "id": "epson",
        "label": "Epson",
        "web_ui_scheme": "http",
        "web_ui_path": "/",
        "instructions_key": "printer.instructions.epson",
        "supports_auto_ip": False,   # phase two, once a test device is available
    },
    "star": {
        "id": "star",
        "label": "Star Micronics",
        "web_ui_scheme": "http",
        "web_ui_path": "/",
        "instructions_key": "printer.instructions.star",
        "supports_auto_ip": False,
    },
    VENDOR_GENERIC: {
        "id": VENDOR_GENERIC,
        "label": "",
        "web_ui_scheme": "http",
        "web_ui_path": "/",
        "instructions_key": "printer.instructions.generic",
        "supports_auto_ip": False,
    },
}


def normalise_mac(mac) -> str:
    if not isinstance(mac, str):
        return ""
    cleaned = mac.strip().lower().replace("-", ":")
    parts = cleaned.split(":")
    if len(parts) != 6:
        return ""
    if not all(len(part) == 2 and all(c in "0123456789abcdef" for c in part)
               for part in parts):
        return ""
    return ":".join(parts)


def vendor_for_mac(mac) -> dict:
    normalised = normalise_mac(mac)
    if normalised:
        vendor_id = OUI_TABLE.get(normalised[:8])
        if vendor_id:
            return VENDORS[vendor_id]
    return VENDORS[VENDOR_GENERIC]


def vendor_by_id(vendor_id) -> dict:
    if isinstance(vendor_id, str) and vendor_id in VENDORS:
        return VENDORS[vendor_id]
    return VENDORS[VENDOR_GENERIC]


def resolve(device: dict, observed_mac: str = "") -> dict:
    """Vendor for a configured device: an explicit setting wins over the OUI."""
    configured = device.get("vendor") if isinstance(device, dict) else None
    if isinstance(configured, str) and configured in VENDORS and configured != VENDOR_GENERIC:
        return VENDORS[configured]
    mac = observed_mac or (device.get("mac") if isinstance(device, dict) else "")
    return vendor_for_mac(mac)


def web_ui_url(vendor: dict, ip: str) -> str:
    if not ip:
        return ""
    scheme = vendor.get("web_ui_scheme", "http")
    path = vendor.get("web_ui_path", "/")
    return f"{scheme}://{ip}{path}"
