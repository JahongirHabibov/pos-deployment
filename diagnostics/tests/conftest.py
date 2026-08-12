# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""Test fixtures. Nothing here touches the network or sudo."""

import importlib.util
import os
import sys

import pytest

DIAGNOSTICS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HELPER_PATH = os.path.join(DIAGNOSTICS_DIR, "bin", "diag-helper")

if DIAGNOSTICS_DIR not in sys.path:
    sys.path.insert(0, DIAGNOSTICS_DIR)


@pytest.fixture(scope="session")
def helper_module():
    """Import diag-helper, which has no .py suffix, as a module."""
    spec = importlib.util.spec_from_loader(
        "diag_helper",
        importlib.machinery.SourceFileLoader("diag_helper", HELPER_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def helper_path():
    return HELPER_PATH


@pytest.fixture
def recorded_commands(monkeypatch, helper_module):
    """Capture every argument list the helper would execute."""
    recorded = []

    def fake_run(argv, timeout=15, check=False):
        recorded.append(list(argv))
        return {"ok": True, "rc": 0, "out": "", "err": ""}

    monkeypatch.setattr(helper_module, "run", fake_run)
    return recorded
