"""Unit tests for host-setup/ (kiosk terminal bootstrap) and its watchdog.

Only the pure parts are tested here; the steps themselves change a live system
and are checked with `sudo ./host-setup/setup.sh --dry-run` on the terminal.
Run: python3 -m pytest test_host_setup.py
"""

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).parent
HOST_SETUP = REPO_DIR / "host-setup"
sys.path.insert(0, str(HOST_SETUP))

import pos_host_setup as hs  # noqa: E402
from installer import _kiosk_agent_origin  # noqa: E402


def _load_watchdog():
    path = HOST_SETUP / "files" / "container-watchdog"
    loader = importlib.machinery.SourceFileLoader("container_watchdog", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


wd = _load_watchdog()


# ── kiosk URL ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("port", ["80", "", None, "8080", " 8080 "])
def test_kiosk_url_matches_power_agent_origin(port):
    # The power agent only answers the origin the installer pins; the kiosk
    # must open exactly that URL or the power button disappears.
    assert hs.pos_url(port) == _kiosk_agent_origin(port)


def test_port_is_read_from_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# POS_PUBLIC_PORT=1\nPOS_PUBLIC_PORT=8080\n", encoding="utf-8")
    assert hs.read_env_port(env) == "8080"
    assert hs.read_env_port(tmp_path / "missing") is None


# ── templates ────────────────────────────────────────────────────────────────

def test_all_templates_render_without_leftovers():
    hs.render("kiosk-browser")
    hs.render("kiosk-session")
    hs.render("session-wrapper", KIOSK_USER="pos")
    hs.render("pos-container-watchdog.service", DEPLOYMENT_DIR="/opt/pos-deployment")
    with pytest.raises(ValueError):
        hs.render("session-wrapper")


def test_chromium_policy_allows_only_the_pos():
    policy = json.loads(hs.render("chromium-policy.json", POS_URL="http://localhost:8080"))
    assert policy["URLBlocklist"] == ["*"]
    assert "http://localhost:8080" in policy["URLAllowlist"]
    assert "http://127.0.0.1:9110" in policy["URLAllowlist"]  # power agent
    assert policy["DeveloperToolsAvailability"] == 2


@pytest.mark.parametrize("name", ["kiosk-browser", "kiosk-session", "session-wrapper"])
def test_shell_scripts_parse(name):
    subprocess.run(["sh", "-n", str(HOST_SETUP / "files" / name)], check=True)


def test_scripts_are_executable():
    for name in ("setup.sh", "files/kiosk-browser", "files/kiosk-session",
                 "files/session-wrapper", "files/container-watchdog"):
        assert (HOST_SETUP / name).stat().st_mode & 0o111, name


def test_lightdm_autologins_the_kiosk_session():
    conf = hs.lightdm_conf("pos", "lxqt")
    assert "autologin-user=pos" in conf
    assert "autologin-session=pos-kiosk" in conf
    assert "session-wrapper=/usr/local/lib/pos-kiosk/session-wrapper" in conf
    assert "user-session=lxqt" in conf
    assert "user-session" not in hs.lightdm_conf("pos", None)


def test_ssh_is_key_only():
    conf = hs.sshd_conf("pos")
    assert "PasswordAuthentication no" in conf
    assert "KbdInteractiveAuthentication no" in conf
    assert "PermitRootLogin no" in conf
    assert "DenyUsers pos" in conf
    # Must sort before the usual 50-*.conf drop-ins: sshd keeps the first value.
    assert hs.SSHD_DROPIN.name < "50-cloud-init.conf"


def test_sshd_effective_settings_are_verified():
    good = ("port 22\npasswordauthentication no\nkbdinteractiveauthentication no\n"
            "permitrootlogin no\npubkeyauthentication yes\n")
    assert hs.sshd_mismatches(good) == []
    bad = good.replace("passwordauthentication no", "passwordauthentication yes")
    assert hs.sshd_mismatches(bad) == ["passwordauthentication is yes"]
    assert "permitrootlogin is unset" in hs.sshd_mismatches("")


def _fake_proc(tmp_path, tree):
    """tree: pid -> (comm, ppid)"""
    for pid, (comm, ppid) in tree.items():
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 0 0\n")


def test_running_over_ssh_follows_the_parent_chain(tmp_path, monkeypatch):
    _fake_proc(tmp_path, {
        900: ("sshd-session", 1), 950: ("bash", 900), 990: ("sudo", 950),
        995: ("python3", 990), 500: ("lightdm", 1), 510: ("bash", 500), 520: ("python3", 510),
    })
    real_path = hs.Path
    monkeypatch.setattr(hs, "Path", lambda p: real_path(str(p).replace("/proc", str(tmp_path))))
    assert hs.running_over_ssh(995) is True
    assert hs.running_over_ssh(520) is False
    assert hs.running_over_ssh(12345) is False


def test_lockout_guard(monkeypatch):
    ctx = hs.Context("pos", "admin", Path("/"), "80")
    monkeypatch.setattr(hs, "running_over_ssh", lambda: True)
    monkeypatch.setattr(hs, "has_authorized_keys", lambda user: False)
    with pytest.raises(SystemExit):
        hs.check_ssh_lockout(ctx)
    monkeypatch.setattr(hs, "has_authorized_keys", lambda user: True)
    hs.check_ssh_lockout(ctx)
    monkeypatch.setattr(hs, "running_over_ssh", lambda: False)
    monkeypatch.setattr(hs, "has_authorized_keys", lambda user: False)
    hs.check_ssh_lockout(ctx)  # at the terminal itself: allowed


def test_grub_conf_extends_instead_of_replacing_cmdline():
    assert '"$GRUB_CMDLINE_LINUX_DEFAULT fsck.repair=yes"' in hs.grub_conf()


def test_write_file_is_idempotent(tmp_path):
    ctx = hs.Context("pos", "admin", tmp_path, "80")
    target = tmp_path / "a" / "b.conf"
    assert hs.write_file(ctx, target, "x\n", 0o755) is True
    assert target.stat().st_mode & 0o777 == 0o755
    assert hs.write_file(ctx, target, "x\n", 0o755) is False
    assert hs.write_file(ctx, target, "y\n", 0o755) is True


def test_dry_run_writes_nothing(tmp_path):
    ctx = hs.Context("pos", "admin", tmp_path, "80", dry_run=True)
    assert hs.write_file(ctx, tmp_path / "f", "x") is True
    assert not (tmp_path / "f").exists()


# ── misc ─────────────────────────────────────────────────────────────────────

def test_clock_problem():
    now = 1_790_000_000  # 2026-09
    assert hs.clock_problem(now, now + 5) is None
    assert hs.clock_problem(now, None) is None
    assert "CMOS" in hs.clock_problem(now, 946_684_800)
    assert "before 2026" in hs.clock_problem(946_684_800, None)


def test_select_steps():
    assert hs.select_steps(None, None) == hs.STEPS
    assert hs.select_steps("ssh,kiosk", None) == ["kiosk", "ssh"]
    assert "docker" not in hs.select_steps(None, "docker")
    with pytest.raises(SystemExit):
        hs.select_steps("nope", None)


def test_os_release_parsing():
    info = hs.parse_os_release('ID=debian\nVERSION_ID="13"\nVERSION_CODENAME=trixie\n')
    assert info["VERSION_ID"] == "13"
    assert info["VERSION_CODENAME"] == "trixie"


# ── watchdog ─────────────────────────────────────────────────────────────────

def test_watchdog_never_touches_database_or_updater():
    assert "pos-database" not in wd.WATCHED
    assert "pos-updater" not in wd.WATCHED


def test_watchdog_waits_for_consecutive_unhealthy_runs():
    entry, action = wd.decide({}, "unhealthy", 1000)
    assert action == "none"
    entry, action = wd.decide(entry, "unhealthy", 1060)
    assert action == "restart"
    assert entry["unhealthy_runs"] == 0


def test_watchdog_healthy_resets_the_count():
    entry, action = wd.decide({"unhealthy_runs": 1}, "healthy", 1000)
    assert (entry["unhealthy_runs"], action) == (0, "none")
    # "starting" or a missing container is not a reason to restart
    assert wd.decide({"unhealthy_runs": 5}, "starting", 1000)[1] == "none"
    assert wd.decide({"unhealthy_runs": 5}, None, 1000)[1] == "none"


def test_watchdog_restart_limit_per_hour():
    now = 10_000
    full = {"unhealthy_runs": 1, "restarts": [now - 100, now - 200, now - 300]}
    entry, action = wd.decide(full, "unhealthy", now)
    assert action == "limit"
    assert wd.decide(entry, "unhealthy", now + 60)[1] == "none"  # logged once
    old = {"unhealthy_runs": 1, "restarts": [now - 4000] * 3}
    entry, action = wd.decide(old, "unhealthy", now)
    assert action == "restart"
    assert entry["restarts"] == [now]


def test_watchdog_pauses_during_update(tmp_path):
    state = tmp_path / "updater-state" / "state.json"
    state.parent.mkdir()
    assert wd.update_running(tmp_path) is False
    state.write_text(json.dumps({"last_upgrade": {"status": "running"}}), encoding="utf-8")
    assert wd.update_running(tmp_path) is True
    state.write_text(json.dumps({"last_upgrade": {"status": "success"}}), encoding="utf-8")
    assert wd.update_running(tmp_path) is False
    state.write_text("{broken", encoding="utf-8")
    assert wd.update_running(tmp_path) is False


def test_cdrom_sources_are_commented_out():
    text = ("deb cdrom:[Debian 13]/ trixie main\n"
            "deb [arch=amd64] cdrom:[x]/ trixie main\n"
            "deb http://deb.debian.org/debian trixie main\n")
    out = hs.disable_cdrom_sources(text)
    assert out.splitlines()[0].startswith("# deb cdrom:")
    assert out.splitlines()[1].startswith("# deb [arch=amd64] cdrom:")
    assert out.splitlines()[2] == "deb http://deb.debian.org/debian trixie main"
    assert hs.disable_cdrom_sources(out) == out


def test_policy_allows_downloads_with_save_dialog():
    # CSV/XLSX exports and backup downloads must keep working in the kiosk.
    policy = json.loads(hs.render("chromium-policy.json", POS_URL="http://localhost"))
    assert "DownloadRestrictions" not in policy
    assert policy["PromptForDownloadLocation"] is True


def test_watchdog_never_restarts_backup():
    assert "pos-backup" not in wd.WATCHED
