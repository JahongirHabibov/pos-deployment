#!/usr/bin/env python3
"""Prepares a Debian 13 machine as a POS kiosk terminal.

Runs before installer.py. Every step checks the current state first and only
changes what is missing, so re-running is safe. Start it via setup.sh.
"""

from __future__ import annotations

import argparse
import os
import pwd
import grp
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = HERE / "files"
REPO_DIR = HERE.parent

KIOSK_LIB = Path("/usr/local/lib/pos-kiosk")
KIOSK_ETC = Path("/etc/pos-kiosk")

BASE_PACKAGES = [
    "lightdm", "lightdm-gtk-greeter", "openbox", "chromium",
    "xserver-xorg", "xserver-xorg-input-libinput", "x11-xserver-utils",
    "unclutter-xfixes", "zram-tools", "openssh-server", "network-manager",
    "curl", "ca-certificates", "gnupg", "python3", "python3-tk", "git",
    # Missing when a root password was set during the Debian install;
    # installer.py needs it.
    "sudo",
    # USB backup sticks: exFAT tools and NTFS read-write (the kernel driver
    # mounts NTFS read-only). usb-backup/install.sh only reports them missing.
    "exfatprogs", "ntfs-3g",
]
# Only when no LXQt is present: the administrator needs a desktop for installer.py.
ADMIN_DESKTOP_PACKAGES = ["lxqt-core", "lxqt-config"]
LXQT_SESSION = Path("/usr/share/xsessions/lxqt.desktop")
DOCKER_PACKAGES = [
    "docker-ce", "docker-ce-cli", "containerd.io",
    "docker-buildx-plugin", "docker-compose-plugin",
]
# Unofficial packages that conflict with Docker CE (docs.docker.com/engine/install/debian).
DOCKER_CONFLICTS = ["docker.io", "docker-compose", "docker-doc", "docker-buildx",
                    "podman-docker", "containerd", "runc"]
# Keep changed configuration files instead of stopping at a dpkg prompt.
APT_KEEP_CONFIG = ["-o", "Dpkg::Options::=--force-confdef",
                   "-o", "Dpkg::Options::=--force-confold"]
DOCKER_GPG_URL = "https://download.docker.com/linux/debian/gpg"
DOCKER_GPG_FINGERPRINT = "9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
SLEEP_TARGETS = ["sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target"]
STEPS = ["packages", "docker", "users", "kiosk", "powerloss", "memory", "time", "ssh", "watchdog"]

# Before this date the machine clock is certainly wrong.
CLOCK_FLOOR = 1767225600  # 2026-01-01T00:00:00Z


class SetupError(Exception):
    """A step failed; the message tells the administrator what to do."""


@dataclass
class Context:
    kiosk_user: str
    admin_user: str
    deployment_dir: Path
    port: str
    dry_run: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def pos_url(self) -> str:
        return pos_url(self.port)


# ───────────────────────────────────────────────────────────── helpers

def say(message: str) -> None:
    print(message, flush=True)


def warn(ctx: Context, message: str) -> None:
    ctx.warnings.append(message)
    say(f"  ! {message}")


def run(ctx: Context, cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    say("  $ " + " ".join(cmd))
    if ctx.dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    return subprocess.run(cmd, check=check, env=env, text=True, **kwargs)


def query(cmd: list[str]) -> subprocess.CompletedProcess:
    """Read-only command; also runs in dry-run mode. Output is parsed, so it
    must not be translated (German apt says "Installationskandidat:")."""
    env = {k: v for k, v in os.environ.items() if k != "LANGUAGE"}
    env["LC_ALL"] = "C.UTF-8"
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, stdout="", stderr=str(exc))


def write_file(ctx: Context, path: Path, content: str, mode: int = 0o644) -> bool:
    """Write only if the content differs. Returns True if something changed."""
    try:
        if path.read_text(encoding="utf-8") == content and (path.stat().st_mode & 0o777) == mode:
            return False
    except OSError:
        pass
    say(f"  > {path}")
    if ctx.dry_run:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(content)
    os.chmod(tmp.name, mode)
    os.replace(tmp.name, path)
    return True


def render(name: str, **values: str) -> str:
    text = (FILES / name).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace(f"@{key}@", value)
    leftover = re.findall(r"@[A-Z_]+@", text)
    if leftover:
        raise ValueError(f"{name}: unresolved placeholders {leftover}")
    return text


def package_installed(name: str) -> bool:
    result = query(["dpkg-query", "-W", "--showformat=${db:Status-Status}", name])
    return result.returncode == 0 and result.stdout.strip() == "installed"


def unit_exists(name: str) -> bool:
    result = query(["systemctl", "list-unit-files", "--no-legend", name])
    return bool(result.stdout.strip())


def user_exists(name: str) -> bool:
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def group_members(group: str) -> list[str]:
    try:
        return list(grp.getgrnam(group).gr_mem)
    except KeyError:
        return []


def pos_url(port: str | None) -> str:
    """The kiosk always opens localhost; it is also the only origin the power
    agent accepts (see installer._kiosk_agent_origin)."""
    port = (port or "80").strip()
    return "http://localhost" if port in ("", "80") else f"http://localhost:{port}"


def read_env_port(env_file: Path) -> str | None:
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*POS_PUBLIC_PORT\s*=\s*([0-9]+)", line)
            if match:
                return match.group(1)
    except OSError:
        return None
    return None


def parse_os_release(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    return values


# ─────────────────────────────────────────────────────── file contents

NOPASSWD_RULE = "auth sufficient pam_succeed_if.so user ingroup nopasswdlogin"


def pam_with_nopasswdlogin(pam: str) -> str | None:
    """Add the nopasswdlogin rule before common-auth, as Ubuntu ships it.
    Debian's /etc/pam.d/lightdm lacks it, so switching back from the login
    screen to the running kiosk session asks for the locked password.
    Returns None if the rule is present or there is no place for it."""
    if "nopasswdlogin" in pam:
        return None
    new, count = re.subn(r"^@include common-auth$", f"{NOPASSWD_RULE}\n@include common-auth",
                         pam, count=1, flags=re.MULTILINE)
    return new if count else None


def lightdm_conf(kiosk_user: str, admin_session: str | None) -> str:
    lines = [
        "# Written by pos-deployment/host-setup. The kiosk user is logged in",
        "# automatically; the administrator uses the login screen.",
        "[Seat:*]",
        f"autologin-user={kiosk_user}",
        "autologin-user-timeout=0",
        "autologin-session=pos-kiosk",
        "session-wrapper=/usr/local/lib/pos-kiosk/session-wrapper",
        "greeter-hide-users=false",
        "xserver-command=X -s 0 -dpms",
    ]
    if admin_session:
        lines.append(f"user-session={admin_session}")
    return "\n".join(lines) + "\n"


def grub_conf() -> str:
    return (
        "# Written by pos-deployment/host-setup.\n"
        "# fsck.repair=yes: after a power cut, repair the file system at boot\n"
        "# instead of stopping at an emergency shell.\n"
        'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT fsck.repair=yes"\n'
        "GRUB_TIMEOUT=3\n"
    )


JOURNALD_CONF = (
    "# Written by pos-deployment/host-setup.\n"
    "[Journal]\nStorage=persistent\nSystemMaxUse=200M\n"
)

LOGIND_CONF = (
    "# Written by pos-deployment/host-setup. The power button shuts down\n"
    "# cleanly; nothing suspends the POS.\n"
    "[Login]\nHandlePowerKey=poweroff\nHandleSuspendKey=ignore\n"
    "HandleHibernateKey=ignore\nHandleLidSwitch=ignore\n"
    "HandleLidSwitchExternalPower=ignore\nIdleAction=ignore\n"
)

ZRAM_CONF = (
    "# Written by pos-deployment/host-setup. Compressed swap in RAM: keeps a\n"
    "# 4 GB machine responsive without swapping to a slow disk.\n"
    "ALGO=zstd\nPERCENT=50\nPRIORITY=100\n"
)


SSHD_DROPIN = Path("/etc/ssh/sshd_config.d/10-pos-kiosk.conf")

# Values `sshd -T` must report once the drop-in is active.
SSHD_EXPECTED = {
    "passwordauthentication": "no",
    "kbdinteractiveauthentication": "no",
    "permitrootlogin": "no",
    "pubkeyauthentication": "yes",
}


def sshd_conf(kiosk_user: str) -> str:
    # sshd uses the first value it reads; Debian includes sshd_config.d/*.conf
    # at the top of sshd_config, and "10-" sorts before the usual "50-" drop-ins.
    return "\n".join([
        "# Written by pos-deployment/host-setup. Key login only.",
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "PubkeyAuthentication yes",
        "PermitRootLogin no",
        f"DenyUsers {kiosk_user}",
    ]) + "\n"


def sshd_mismatches(effective: str) -> list[str]:
    """Settings from `sshd -T` output that differ from SSHD_EXPECTED."""
    values = {}
    for line in effective.splitlines():
        key, _, value = line.strip().partition(" ")
        values[key.lower()] = value.strip()
    return [f"{key} is {values.get(key, 'unset')}" for key, want in SSHD_EXPECTED.items()
            if values.get(key) != want]


def running_over_ssh(pid: int | None = None) -> bool:
    """True if this process descends from sshd (sudo drops SSH_CONNECTION)."""
    pid = pid or os.getpid()
    for _ in range(64):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return False
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        if comm.startswith("sshd"):
            return True
        pid = int(stat[stat.rindex(")") + 2:].split()[1])
        if pid <= 1:
            return False
    return False


def has_authorized_keys(user: str) -> bool:
    try:
        home = Path(pwd.getpwnam(user).pw_dir)
    except KeyError:
        return False
    path = home / ".ssh" / "authorized_keys"
    try:
        return any(l.strip() and not l.startswith("#") for l in path.read_text().splitlines())
    except OSError:
        return False


def has_candidate(policy: str) -> bool:
    """True if `apt-cache policy <pkg>` (untranslated) offers a version."""
    match = re.search(r"^\s*Candidate:\s*(\S+)", policy, flags=re.MULTILINE)
    return bool(match) and match.group(1) != "(none)"


def has_security_source(policy: str, codename: str) -> bool:
    """True if `apt-cache policy` lists the Debian security archive."""
    return f"{codename}-security" in policy


def timezone_problem(zone: str) -> str | None:
    if zone in ("", "UTC", "Etc/UTC"):
        return (f"timezone is {zone or 'unset'}: receipts print UTC. Set it, e.g. "
                "sudo timedatectl set-timezone Europe/Berlin, then run start-installer.sh")
    return None


def admin_user_problem(admin: str, kiosk: str, exists: bool) -> str | None:
    if not admin:
        return ("no administrator user: run with sudo from the administrator's "
                "account, or add --admin-user <name>")
    if admin == "root":
        return "the administrator must not be root: add --admin-user <name>"
    if admin == kiosk:
        # The users step locks the kiosk user's password and removes it from sudo.
        return (f"'{admin}' is both administrator and kiosk user; the kiosk user "
                "loses its password and sudo. Keep this account as administrator "
                "and name another kiosk user, e.g. --kiosk-user kiosk")
    if not exists:
        return f"administrator user '{admin}' does not exist (use --admin-user)"
    return None


def clock_problem(system_now: float, rtc_epoch: float | None) -> str | None:
    if system_now < CLOCK_FLOOR:
        return "system clock is before 2026 - set the time and check the CMOS battery"
    if rtc_epoch is not None and abs(system_now - rtc_epoch) > 86400:
        return ("hardware clock (RTC) is off by more than a day - "
                "the CMOS battery is probably empty")
    return None


# ─────────────────────────────────────────────────────────────── steps

def step_preflight(ctx: Context, force: bool) -> None:
    info = parse_os_release(Path("/etc/os-release").read_text(encoding="utf-8"))
    if info.get("ID") != "debian" or info.get("VERSION_ID") != "13":
        message = f"expected Debian 13, found {info.get('PRETTY_NAME', 'unknown')}"
        if not force:
            raise SystemExit(f"ERROR: {message} (use --force to continue anyway)")
        warn(ctx, message)
    problem = admin_user_problem(ctx.admin_user, ctx.kiosk_user, user_exists(ctx.admin_user))
    if problem:
        raise SystemExit(f"ERROR: {problem}")


def check_ssh_lockout(ctx: Context) -> None:
    """Refuse to switch off password login from an SSH session that would not
    get back in: without a key, the next connection would be refused."""
    if running_over_ssh() and not has_authorized_keys(ctx.admin_user):
        raise SystemExit(
            f"ERROR: running over SSH, but {ctx.admin_user} has no key in "
            "~/.ssh/authorized_keys. Password login is switched off by this "
            "script, so you would be locked out. Add a key first "
            "(ssh-copy-id), or run the script at the terminal itself.")


def disable_cdrom_sources(text: str) -> str:
    """Comment out `deb cdrom:` lines. An install from a USB stick or DVD leaves
    them active, and `apt-get update` then fails once the medium is gone."""
    return re.sub(r"^(deb(?:-src)?\s+(?:\[[^\]]*\]\s+)?cdrom:)", r"# \1", text, flags=re.MULTILINE)


def step_packages(ctx: Context) -> None:
    sources = Path("/etc/apt/sources.list")
    try:
        original = sources.read_text(encoding="utf-8")
    except OSError:
        original = None
    if original is not None and disable_cdrom_sources(original) != original:
        write_file(ctx, sources.with_name("sources.list.pos-backup"), original)
        write_file(ctx, sources, disable_cdrom_sources(original))

    packages = list(BASE_PACKAGES)
    if not any(package_installed(p) for p in ("chrony", "ntpsec", "ntp")):
        packages.append("systemd-timesyncd")
    if not LXQT_SESSION.exists():
        packages += ADMIN_DESKTOP_PACKAGES
    # LXQt ships SDDM; LightDM becomes the display manager without a prompt.
    run(ctx, ["debconf-set-selections"],
        input="lightdm shared/default-x-display-manager select lightdm\n")
    run(ctx, ["apt-get", "update"])
    if not ctx.dry_run:
        if not has_candidate(query(["apt-cache", "policy", "chromium"]).stdout):
            raise SetupError(
                "no Debian package mirror is configured (chromium not found). "
                "Add one, e.g. /etc/apt/sources.list.d/debian.sources with "
                "URIs: http://deb.debian.org/debian, and check the network.")
        codename = parse_os_release(Path("/etc/os-release").read_text()).get("VERSION_CODENAME", "trixie")
        if not has_security_source(query(["apt-cache", "policy"]).stdout, codename):
            warn(ctx, f"no {codename}-security source: Chromium and the kernel get no "
                      "security fixes. Add URIs: http://security.debian.org/debian-security "
                      f"Suites: {codename}-security to /etc/apt/sources.list.d/debian.sources")
    # Bring an install from an older image up to the current point release.
    run(ctx, ["apt-get", "full-upgrade", "-y", *APT_KEEP_CONFIG])
    run(ctx, ["apt-get", "install", "-y", *APT_KEEP_CONFIG, *packages])


def step_docker(ctx: Context) -> None:
    conflicts = [p for p in DOCKER_CONFLICTS if package_installed(p)]
    if conflicts and not package_installed("docker-ce"):
        warn(ctx, f"{', '.join(conflicts)} installed; Docker CE not set up. Remove "
                  f"them first (sudo apt-get remove {' '.join(conflicts)}), then run "
                  "again with --only docker")
        return
    if not all(package_installed(p) for p in DOCKER_PACKAGES):
        codename = parse_os_release(Path("/etc/os-release").read_text()).get("VERSION_CODENAME", "trixie")
        arch = query(["dpkg", "--print-architecture"]).stdout.strip() or "amd64"
        keyring = Path("/etc/apt/keyrings/docker.asc")
        if not package_installed("gnupg"):  # --only docker before packages
            run(ctx, ["apt-get", "install", "-y", "gnupg", "ca-certificates"])
        if not ctx.dry_run:
            try:
                with urllib.request.urlopen(DOCKER_GPG_URL, timeout=30) as response:
                    key = response.read().decode("ascii")
            except OSError as exc:
                raise SetupError(f"cannot reach download.docker.com ({exc}); check the network")
            with tempfile.NamedTemporaryFile("w", suffix=".asc", delete=False) as tmp:
                tmp.write(key)
            shown = query(["gpg", "--show-keys", "--with-colons", tmp.name]).stdout
            os.unlink(tmp.name)
            if f"fpr:::::::::{DOCKER_GPG_FINGERPRINT}:" not in shown:
                raise SetupError("the Docker signing key has an unexpected fingerprint; nothing installed")
            write_file(ctx, keyring, key)
        write_file(ctx, Path("/etc/apt/sources.list.d/docker.sources"), (
            "Types: deb\n"
            "URIs: https://download.docker.com/linux/debian\n"
            f"Suites: {codename}\n"
            "Components: stable\n"
            f"Architectures: {arch}\n"
            f"Signed-By: {keyring}\n"
        ))
        run(ctx, ["apt-get", "update"])
        run(ctx, ["apt-get", "install", "-y", *DOCKER_PACKAGES])
    # A new engine may drop API versions the updater relies on; upgrades are a
    # deliberate step (apt-mark unhold ...), never a side effect of apt upgrade.
    run(ctx, ["apt-mark", "hold", *DOCKER_PACKAGES])
    run(ctx, ["systemctl", "enable", "--now", "docker.service"])


def step_users(ctx: Context) -> None:
    if query(["getent", "group", "nopasswdlogin"]).returncode != 0:
        run(ctx, ["groupadd", "--system", "nopasswdlogin"])
    if not user_exists(ctx.kiosk_user):
        run(ctx, ["useradd", "--create-home", "--shell", "/bin/bash",
                  "--comment", "POS Kiosk", ctx.kiosk_user])
        run(ctx, ["passwd", "--lock", ctx.kiosk_user])
    if ctx.kiosk_user not in group_members("nopasswdlogin"):
        run(ctx, ["usermod", "--append", "--groups", "nopasswdlogin", ctx.kiosk_user])
    # docker = root on this machine; the kiosk user never gets it.
    for group in ("sudo", "docker", "adm"):
        if ctx.kiosk_user in group_members(group):
            run(ctx, ["gpasswd", "--delete", ctx.kiosk_user, group])
    # installer.py runs Docker through sudo.
    if ctx.admin_user not in group_members("sudo"):
        run(ctx, ["usermod", "--append", "--groups", "sudo", ctx.admin_user])
        warn(ctx, f"{ctx.admin_user} was added to the sudo group; it takes effect at the next login")


def step_kiosk(ctx: Context) -> None:
    write_file(ctx, KIOSK_LIB / "kiosk-browser", render("kiosk-browser"), 0o755)
    write_file(ctx, KIOSK_LIB / "kiosk-session", render("kiosk-session"), 0o755)
    write_file(ctx, KIOSK_LIB / "session-wrapper",
               render("session-wrapper", KIOSK_USER=ctx.kiosk_user), 0o755)
    write_file(ctx, KIOSK_ETC / "kiosk.conf",
               f"# Written by pos-deployment/host-setup.\nPOS_URL={ctx.pos_url}\n")
    write_file(ctx, KIOSK_ETC / "openbox-rc.xml", render("openbox-rc.xml"))
    write_file(ctx, Path("/usr/share/xsessions/pos-kiosk.desktop"), render("pos-kiosk.desktop"))
    write_file(ctx, Path("/etc/systemd/user/pos-kiosk-browser.service"),
               render("pos-kiosk-browser.service"))
    # Machine-wide: applies to every Chromium user on this terminal.
    write_file(ctx, Path("/etc/chromium/policies/managed/pos-kiosk.json"),
               render("chromium-policy.json", POS_URL=ctx.pos_url))

    admin_session = "lxqt" if LXQT_SESSION.exists() else None
    if admin_session is None:
        warn(ctx, "no LXQt session found: the administrator has no desktop for installer.py")
    write_file(ctx, Path("/etc/lightdm/lightdm.conf.d/50-pos-kiosk.conf"),
               lightdm_conf(ctx.kiosk_user, admin_session))
    write_file(ctx, Path("/etc/X11/default-display-manager"), "/usr/sbin/lightdm\n")
    if unit_exists("sddm.service"):
        run(ctx, ["systemctl", "disable", "sddm.service"], check=False)
    run(ctx, ["systemctl", "enable", "--force", "lightdm.service"])
    run(ctx, ["systemctl", "set-default", "graphical.target"])

    pam_file = Path("/etc/pam.d/lightdm")
    try:
        pam = pam_file.read_text(encoding="utf-8")
    except OSError:
        pam = ""
    patched = pam_with_nopasswdlogin(pam)
    if patched is not None:
        write_file(ctx, pam_file, patched)
    elif "nopasswdlogin" not in pam:
        warn(ctx, "/etc/pam.d/lightdm has no nopasswdlogin rule: returning to the "
                  "kiosk from the login screen will ask for a password")


def step_powerloss(ctx: Context) -> None:
    changed = write_file(ctx, Path("/etc/default/grub.d/90-pos-kiosk.cfg"), grub_conf())
    if Path("/sys/firmware/efi").is_dir():
        # Also install GRUB to EFI/BOOT/BOOTX64.EFI: a firmware that lost its
        # boot entries (empty CMOS battery, BIOS reset) still finds it there.
        if package_installed("grub-efi-amd64"):
            run(ctx, ["debconf-set-selections"],
                input="grub-efi-amd64 grub2/force_efi_extra_removable boolean true\n")
            # Re-runs grub-install (now with the fallback copy) and update-grub.
            run(ctx, ["dpkg-reconfigure", "--frontend", "noninteractive", "grub-efi-amd64"])
            changed = False
            # vfat is case-insensitive, so one spelling covers all.
            fallback = Path("/boot/efi/EFI/BOOT/BOOTX64.EFI")
            if not ctx.dry_run and not fallback.exists():
                run(ctx, ["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
                          "--force-extra-removable"], check=False)
                changed = True
                if not fallback.exists():
                    warn(ctx, "EFI fallback loader EFI/BOOT/BOOTX64.EFI could not be installed")
        else:
            warn(ctx, "UEFI system without grub-efi-amd64: fallback boot path not set up")
    if changed:
        run(ctx, ["update-grub"])

    write_file(ctx, Path("/etc/systemd/journald.conf.d/50-pos-kiosk.conf"), JOURNALD_CONF)
    write_file(ctx, Path("/etc/systemd/logind.conf.d/50-pos-kiosk.conf"), LOGIND_CONF)
    run(ctx, ["systemctl", "mask", *SLEEP_TARGETS])


def step_memory(ctx: Context) -> None:
    write_file(ctx, Path("/etc/default/zramswap"), ZRAM_CONF)
    run(ctx, ["systemctl", "enable", "zramswap.service"])
    run(ctx, ["systemctl", "restart", "zramswap.service"], check=False)


def step_time(ctx: Context) -> None:
    run(ctx, ["timedatectl", "set-ntp", "true"], check=False)
    run(ctx, ["timedatectl", "set-local-rtc", "0"], check=False)
    try:
        rtc = float(Path("/sys/class/rtc/rtc0/since_epoch").read_text().strip())
    except (OSError, ValueError):
        rtc = None
    problem = clock_problem(time.time(), rtc)
    if problem:
        warn(ctx, problem)
    zone = query(["timedatectl", "show", "-p", "Timezone", "--value"]).stdout.strip()
    problem = timezone_problem(zone)
    if problem:
        warn(ctx, problem)


def step_ssh(ctx: Context) -> None:
    if not has_authorized_keys(ctx.admin_user):
        warn(ctx, f"no SSH key for {ctx.admin_user}: remote login is impossible until "
                  "one is added to ~/.ssh/authorized_keys (password login is off)")
    try:
        previous = SSHD_DROPIN.read_text(encoding="utf-8")
    except OSError:
        previous = None
    write_file(ctx, SSHD_DROPIN, sshd_conf(ctx.kiosk_user))
    if not ctx.dry_run:
        # sshd -t refuses to run without its privilege separation directory,
        # which only exists while the service runs.
        Path("/run/sshd").mkdir(mode=0o755, exist_ok=True)
        check = query(["sshd", "-t"])
        if check.returncode != 0:
            # Never leave a config behind that stops sshd at the next boot.
            if previous is None:
                SSHD_DROPIN.unlink(missing_ok=True)
            else:
                write_file(ctx, SSHD_DROPIN, previous)
            raise SetupError(f"sshd rejected the configuration, previous state restored: "
                             f"{check.stderr.strip()}")
        effective = query(["sshd", "-T"])
        if effective.returncode != 0:
            raise SetupError(f"could not read the effective sshd settings: {effective.stderr.strip()}")
        mismatches = sshd_mismatches(effective.stdout)
        if mismatches:
            raise SetupError(
                "another sshd setting takes precedence (" + ", ".join(mismatches) + "). "
                "Find it with: grep -ri -e passwordauthentication -e permitrootlogin /etc/ssh")
    run(ctx, ["systemctl", "enable", "ssh.service"])
    # Reload keeps open connections; a stopped service is started.
    run(ctx, ["systemctl", "reload-or-restart", "ssh.service"])


def step_watchdog(ctx: Context) -> None:
    write_file(ctx, KIOSK_LIB / "container-watchdog", render("container-watchdog"), 0o755)
    write_file(ctx, Path("/etc/systemd/system/pos-container-watchdog.service"),
               render("pos-container-watchdog.service", DEPLOYMENT_DIR=str(ctx.deployment_dir)))
    write_file(ctx, Path("/etc/systemd/system/pos-container-watchdog.timer"),
               render("pos-container-watchdog.timer"))
    run(ctx, ["systemctl", "daemon-reload"])
    run(ctx, ["systemctl", "enable", "--now", "pos-container-watchdog.timer"])


STEP_FUNCS = {
    "packages": step_packages, "docker": step_docker, "users": step_users,
    "kiosk": step_kiosk, "powerloss": step_powerloss, "memory": step_memory,
    "time": step_time, "ssh": step_ssh, "watchdog": step_watchdog,
}

CHECKLIST = """\
Manual checks (firmware, cannot be done from the OS):
  [ ] CMOS battery (CR2032) replaced on older machines
  [ ] BIOS/UEFI: "Restore on AC power loss" = Power On
  [ ] BIOS/UEFI: boot only from the internal disk, supervisor password set
  [ ] Touchscreen: taps land where the finger is (calibrate if not)"""


def select_steps(only: str | None, skip: str | None) -> list[str]:
    chosen = [s.strip() for s in only.split(",")] if only else list(STEPS)
    skipped = {s.strip() for s in skip.split(",")} if skip else set()
    unknown = (set(chosen) | skipped) - set(STEPS)
    if unknown:
        raise SystemExit(f"ERROR: unknown step(s): {', '.join(sorted(unknown))}")
    return [s for s in STEPS if s in chosen and s not in skipped]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kiosk-user", default="pos")
    parser.add_argument("--admin-user", default=os.environ.get("SUDO_USER", ""))
    parser.add_argument("--deployment-dir", default=str(REPO_DIR))
    parser.add_argument("--port", help="POS_PUBLIC_PORT (default: from .env, else 80)")
    parser.add_argument("--only", help=f"comma-separated steps: {','.join(STEPS)}")
    parser.add_argument("--skip", help="comma-separated steps to leave out")
    parser.add_argument("--dry-run", action="store_true", help="show what would change")
    parser.add_argument("--force", action="store_true", help="run on a system other than Debian 13")
    args = parser.parse_args(argv)

    if os.geteuid() != 0 and not args.dry_run:
        raise SystemExit("ERROR: run as root: sudo ./host-setup/setup.sh "
                         "(without sudo: su -, then add --admin-user <name>)")

    deployment_dir = Path(args.deployment_dir).resolve()
    ctx = Context(
        kiosk_user=args.kiosk_user,
        admin_user=args.admin_user,
        deployment_dir=deployment_dir,
        port=args.port or read_env_port(deployment_dir / ".env") or "80",
        dry_run=args.dry_run,
    )
    steps = select_steps(args.only, args.skip)
    step_preflight(ctx, args.force)
    if "ssh" in steps:
        check_ssh_lockout(ctx)
    say(f"POS URL: {ctx.pos_url}   kiosk user: {ctx.kiosk_user}   admin: {ctx.admin_user}")

    for name in steps:
        say(f"\n== {name}")
        try:
            STEP_FUNCS[name](ctx)
        except SetupError as exc:
            say(f"\nERROR in step '{name}': {exc}")
            return 1
        except subprocess.CalledProcessError as exc:
            say(f"\nERROR in step '{name}': command failed (exit {exc.returncode}): "
                + " ".join(exc.cmd))
            say("Fix the cause shown above and run the script again; "
                f"finished steps are skipped quickly (--only {name} continues here).")
            return 1

    say("\n" + CHECKLIST)
    if ctx.warnings:
        say("\nWarnings:")
        for message in ctx.warnings:
            say(f"  - {message}")
    say("\nDone. Next: reboot, log in as the administrator "
        "(Ctrl+Alt+Shift+A on the kiosk screen) and run ./start-installer.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
