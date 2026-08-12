# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Check modules.

Importing a module is itself a failure path: a syntax error or a missing symbol
in one file must not stop the service from starting. Each module is therefore
imported defensively, and a module that cannot be loaded is recorded so the
interface can say which area is unavailable and why — instead of silently
showing one group fewer.
"""

from __future__ import annotations

import importlib
import logging

LOG = logging.getLogger("kassio.checks")

MODULES = ("system", "network", "docker", "devices", "services", "pos")

FAILED_IMPORTS: dict = {}


def load_all() -> dict:
    """Import every check module. Returns the modules that failed, if any."""
    for name in MODULES:
        try:
            importlib.import_module(f"{__name__}.{name}")
        except Exception as exc:  # noqa: BLE001 - see module docstring
            LOG.exception("check module %s could not be imported", name)
            FAILED_IMPORTS[name] = repr(exc)
    return dict(FAILED_IMPORTS)
