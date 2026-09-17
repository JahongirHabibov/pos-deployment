"""Unit tests for the PostgreSQL hardware profile.

A wrong guess is silent: an SSD profile on a spinning disk makes the planner
pick random reads the disk cannot deliver, and queries crawl without any error.
Run: python3 -m pytest test_host_profile.py
"""

import re
import subprocess
from pathlib import Path

import host_profile
from host_profile import build_profile, detect_memory_bytes, detect_rotational

GIB = 1024**3
REPO_DIR = Path(__file__).parent


def test_hdd_small_machine():
    name, values = build_profile(True, 4 * GIB)
    assert name == "hdd-4g"
    assert values["PG_RANDOM_PAGE_COST"] == "4"
    assert values["PG_EFFECTIVE_IO_CONCURRENCY"] == "2"
    assert values["PG_SHARED_BUFFERS"] == "256MB"


def test_ssd_large_machine():
    name, values = build_profile(False, 8 * GIB)
    assert name == "ssd-8g"
    assert values["PG_RANDOM_PAGE_COST"] == "1.1"
    assert values["PG_SHARED_BUFFERS"] == "512MB"


def test_nominal_8g_machine_counts_as_8g():
    # The kernel reserves memory; an 8 GB machine reports roughly 7.6 GiB.
    assert build_profile(False, int(7.6 * GIB))[0] == "ssd-8g"


def test_unknown_hardware_falls_back_to_weakest_profile():
    assert build_profile(None, None)[0] == "hdd-4g"


def test_every_profile_sets_the_same_keys():
    keys = {frozenset(build_profile(r, m)[1]) for r in (True, False) for m in (GIB, 16 * GIB)}
    assert len(keys) == 1


def test_compose_defaults_equal_the_hdd_4g_profile():
    # A machine the installer never profiled must get the HDD values.
    compose = (REPO_DIR / "docker-compose.prod.yml").read_text(encoding="utf-8")
    defaults = dict(re.findall(r"\$\{(PG_[A-Z_]+):-([^}]+)\}", compose))
    assert defaults == build_profile(True, 4 * GIB)[1]


def test_meminfo_parsing(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        3906040 kB\nMemFree: 1 kB\n", encoding="utf-8")
    assert detect_memory_bytes(str(meminfo)) == 3906040 * 1024
    assert detect_memory_bytes(str(tmp_path / "missing")) is None


def _fake_run(outputs):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=outputs[cmd[0]], stderr="")
    return run


def test_rotational_disk_below_lvm_counts(monkeypatch):
    monkeypatch.setattr(host_profile.subprocess, "run",
                        _fake_run({"findmnt": "/dev/mapper/vg-root\n", "lsblk": "0\n1\n"}))
    assert detect_rotational("/") is True


def test_ssd_with_btrfs_subvolume(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        out = "/dev/nvme0n1p2[/@]\n" if cmd[0] == "findmnt" else "0\n0\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(host_profile.subprocess, "run", run)
    assert detect_rotational("/") is False
    assert calls[1][-1] == "/dev/nvme0n1p2"


def test_non_device_source_is_unknown(monkeypatch):
    monkeypatch.setattr(host_profile.subprocess, "run",
                        _fake_run({"findmnt": "overlay\n", "lsblk": ""}))
    assert detect_rotational("/") is None


def test_missing_tools_are_unknown(monkeypatch):
    def run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(host_profile.subprocess, "run", run)
    assert detect_rotational("/") is None


def test_installer_keeps_manual_profile(monkeypatch, tmp_path):
    installer = __import__("installer")
    env = tmp_path / ".env"
    env.write_text("POS_HOST_PROFILE=manual\nPG_SHARED_BUFFERS=1GB\n", encoding="utf-8")
    monkeypatch.setattr(installer, "ENV_FILE", env)
    assert installer._apply_host_profile(lambda: ("ssd-8g", {"PG_SHARED_BUFFERS": "512MB"})) is None
    assert "PG_SHARED_BUFFERS=1GB" in env.read_text(encoding="utf-8")


def test_installer_writes_detected_profile(monkeypatch, tmp_path):
    installer = __import__("installer")
    env = tmp_path / ".env"
    env.write_text("POS_HOST_PROFILE=ssd-8g\nPG_SHARED_BUFFERS=512MB\n", encoding="utf-8")
    monkeypatch.setattr(installer, "ENV_FILE", env)
    name = installer._apply_host_profile(lambda: build_profile(True, 4 * GIB))
    content = env.read_text(encoding="utf-8")
    assert name == "hdd-4g"
    assert "POS_HOST_PROFILE=hdd-4g" in content
    assert "PG_SHARED_BUFFERS=256MB" in content
    assert "PG_RANDOM_PAGE_COST=4" in content
    assert content.count("PG_SHARED_BUFFERS=") == 1


def test_installer_generates_updater_token_once(monkeypatch, tmp_path):
    installer = __import__("installer")
    env = tmp_path / ".env"
    env.write_text("UPDATER_API_TOKEN=\nOTHER=1\n", encoding="utf-8")
    monkeypatch.setattr(installer, "ENV_FILE", env)
    assert installer._ensure_updater_token() is True
    first = installer._read_env_keys(["UPDATER_API_TOKEN"])["UPDATER_API_TOKEN"]
    assert re.fullmatch(r"[0-9a-f]{64}", first)
    assert installer._ensure_updater_token() is False
    assert installer._read_env_keys(["UPDATER_API_TOKEN"])["UPDATER_API_TOKEN"] == first
    assert env.read_text(encoding="utf-8").count("UPDATER_API_TOKEN=") == 1
    assert env.stat().st_mode & 0o777 == 0o600


def test_compose_passes_updater_token_to_the_updater():
    compose = (REPO_DIR / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "UPDATER_API_TOKEN=${UPDATER_API_TOKEN:-}" in compose
    # Enforcement stays opt-in until every backend in the field sends the token.
    assert "UPDATER_REQUIRE_TOKEN=${UPDATER_REQUIRE_TOKEN:-false}" in compose
