"""Unit tests for the kiosk power agent wiring in the installer.

The agent only accepts requests from the origin the POS is served on. That
origin is derived from POS_PUBLIC_PORT, and getting it wrong is silent: the
browser's preflight fails and the power-off button never appears, with nothing
in the deployment log to explain it. Run: python3 -m pytest test_kiosk_agent.py
(or python3 test_kiosk_agent.py for a plain assert run).
"""

from pathlib import Path

from installer import (
    KIOSK_AGENT_DIR,
    KIOSK_AGENT_ENV_KEY,
    KIOSK_AGENT_INSTALL,
    _kiosk_agent_origin,
    _step3_row_offsets,
)


def test_default_port_yields_bare_localhost():
    # Port 80 must not be spelled out — "http://localhost:80" is a different
    # origin string than "http://localhost" as far as CORS is concerned.
    assert _kiosk_agent_origin("80") == "http://localhost"


def test_missing_port_falls_back_to_default():
    assert _kiosk_agent_origin("") == "http://localhost"
    assert _kiosk_agent_origin("  ") == "http://localhost"
    assert _kiosk_agent_origin(None) == "http://localhost"


def test_custom_port_is_included():
    assert _kiosk_agent_origin("8080") == "http://localhost:8080"
    assert _kiosk_agent_origin(" 8080 ") == "http://localhost:8080"


def test_agent_ships_with_the_deployment_repo():
    # The installer offers the checkbox only when the script is present; if the
    # files ever get dropped from the repo the box silently disappears.
    assert KIOSK_AGENT_DIR.is_dir()
    assert KIOSK_AGENT_INSTALL.is_file()
    for name in ("kassio_power_agent.py", "kassio-power-agent.service", "README.md"):
        assert (KIOSK_AGENT_DIR / name).is_file(), f"missing kiosk-agent/{name}"


def test_agent_port_matches_the_frontend_contract():
    # 9110 is hard-coded in the POS frontend (usePowerAgent.js). Changing it on
    # one side only breaks the probe.
    agent_source = (KIOSK_AGENT_DIR / "kassio_power_agent.py").read_text(encoding="utf-8")
    unit_source = (KIOSK_AGENT_DIR / "kassio-power-agent.service").read_text(encoding="utf-8")
    assert 'KASSIO_POWER_PORT", "9110"' in agent_source
    assert "KASSIO_POWER_PORT=9110" in unit_source


def test_step3_rows_stack_without_overlapping():
    # Nothing optional shown: log sits where it always did.
    assert _step3_row_offsets(False, False) == (0, 0, 0)
    # Only the checkbox: it takes the two rows the sudo field would have used.
    assert _step3_row_offsets(False, True) == (0, 2, 2)
    # Only sudo: unchanged from before the checkbox existed.
    assert _step3_row_offsets(True, False) == (2, 0, 2)
    # Both: the checkbox starts below the sudo field, log below both.
    sudo, kiosk, total = _step3_row_offsets(True, True)
    assert (sudo, kiosk) == (2, 2)
    assert total == sudo + kiosk == 4


def test_env_key_is_documented():
    example = Path(__file__).parent / ".env.example"
    assert KIOSK_AGENT_ENV_KEY in example.read_text(encoding="utf-8")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all kiosk-agent tests passed")
