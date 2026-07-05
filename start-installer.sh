#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# start-installer.sh — POS System Setup & Installation Wizard Launcher
#
# Usage:
#   ./start-installer.sh              # full setup + 3-step GUI wizard
#   ./start-installer.sh --skip-setup # skip to deployment (provisioning done)
#   ./start-installer.sh --no-update  # skip the git freshness / self-update check
#
#   Remote access (WireGuard/VNC) is a SEPARATE, isolated step and is NOT run
#   by this installer. Set it up on its own: sudo ./setup-remote-access.sh
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/installer.py"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Output helpers ────────────────────────────────────────────────────────────
ok()      { echo -e "  ${GREEN}✓${NC}  $*"; }
fail()    { echo -e "  ${RED}✗${NC}  $*"; }
warn()    { echo -e "  ${YELLOW}!${NC}  $*"; }
info()    { echo -e "  ${CYAN}→${NC}  $*"; }
error()   { echo -e "\n${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

divider() {
    echo -e "${DIM}────────────────────────────────────────────────────────────────${NC}"
}

section() {
    echo ""
    echo -e "${BOLD}${BLUE}┌──────────────────────────────────────────────────────────────┐${NC}"
    printf "${BOLD}${BLUE}│${NC}  %-62s${BOLD}${BLUE}│${NC}\n" "$*"
    echo -e "${BOLD}${BLUE}└──────────────────────────────────────────────────────────────┘${NC}"
}

status_row() {
    local label="$1"
    local value="$2"
    local color="${3:-$NC}"
    printf "  %-22s ${color}%s${NC}\n" "$label" "$value"
}

# ── Argument parsing ──────────────────────────────────────────────────────────
SKIP_SETUP=false
NO_UPDATE=false
INSTALLER_ARGS=()
# Preserve the original arguments so the self-update guard can re-exec verbatim.
ORIG_ARGS=("$@")

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-setup) SKIP_SETUP=true; INSTALLER_ARGS+=("--skip-setup"); shift ;;
        # Deprecated no-op: remote access is a separate step (setup-remote-access.sh)
        # and is never run by this installer. Accepted silently for compatibility.
        --no-remote)  shift ;;
        --no-update)  NO_UPDATE=true;  shift ;;
        --help|-h)
            sed -n '3,11p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) INSTALLER_ARGS+=("$1"); shift ;;
    esac
done

# ── Self-update guard ─────────────────────────────────────────────────────────
# Ensure the admin never deploys stale code after forgetting `git pull`.
# Fetches origin/main, fast-forwards if behind, then re-execs so the freshly
# pulled version of THIS script runs. Offline → warn and continue.
UPDATE_BRANCH="main"

run_update_guard() {
    # Already re-executed once this run — don't loop.
    [[ -n "${_POS_INSTALLER_REEXEC:-}" ]] && return 0
    # Explicit opt-outs.
    [[ "$NO_UPDATE" == true ]] && { warn "Self-update check skipped (--no-update)"; return 0; }
    [[ "${SKIP_UPDATE_CHECK:-}" == "1" ]] && { warn "Self-update check skipped (SKIP_UPDATE_CHECK=1)"; return 0; }

    command -v git &>/dev/null || { warn "git not found — skipping self-update check"; return 0; }
    if ! git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree &>/dev/null; then
        warn "Not a git checkout — skipping self-update check"
        return 0
    fi

    info "Checking for a newer deployment version (branch ${UPDATE_BRANCH})..."
    # Hard timeout so an offline device can't hang the installer on fetch.
    if ! timeout 20 git -C "$SCRIPT_DIR" fetch --quiet origin "$UPDATE_BRANCH" 2>/dev/null; then
        warn "Could not reach origin (offline?) — continuing with the local version"
        return 0
    fi

    local local_rev remote_rev
    local_rev=$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || echo "")
    remote_rev=$(git -C "$SCRIPT_DIR" rev-parse "origin/${UPDATE_BRANCH}" 2>/dev/null || echo "")
    if [[ -z "$remote_rev" ]]; then
        warn "origin/${UPDATE_BRANCH} not found — skipping self-update check"
        return 0
    fi
    if [[ "$local_rev" == "$remote_rev" ]]; then
        ok "Deployment is up to date"
        return 0
    fi

    warn "Newer version available (local ${local_rev:0:7} → origin ${remote_rev:0:7})"

    # Never auto-pull over local uncommitted changes — would conflict/clobber.
    if ! git -C "$SCRIPT_DIR" diff --quiet || ! git -C "$SCRIPT_DIR" diff --cached --quiet; then
        fail "Local uncommitted changes present — automatic update stopped"
        error "Resolve manually: git -C \"$SCRIPT_DIR\" status  (commit/stash/discard), then re-run"
        die "Aborting to avoid overwriting local changes"
    fi

    # Only fast-forward when actually on the tracked branch.
    local cur_branch
    cur_branch=$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    if [[ "$cur_branch" != "$UPDATE_BRANCH" ]]; then
        warn "On branch '${cur_branch}', expected '${UPDATE_BRANCH}' — automatic update stopped"
        error "Switch first: git -C \"$SCRIPT_DIR\" checkout ${UPDATE_BRANCH} && git pull --ff-only"
        die "Aborting"
    fi

    info "Updating to the latest version (git pull --ff-only)..."
    if ! git -C "$SCRIPT_DIR" pull --ff-only --quiet origin "$UPDATE_BRANCH"; then
        fail "git pull --ff-only failed (diverged history?)"
        die "Resolve manually: git -C \"$SCRIPT_DIR\" pull"
    fi
    ok "Updated to $(git -C "$SCRIPT_DIR" rev-parse --short HEAD) — restarting installer"

    # Re-exec: the current process still holds the OLD script in memory.
    export _POS_INSTALLER_REEXEC=1
    exec "$0" "${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}"
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   POS System — Setup & Installation          ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# Make sure we are running the latest committed code before doing any work.
run_update_guard

# ── State tracking ────────────────────────────────────────────────────────────
PYTHON=""
PYTHON_VER=""
TKINTER_OK=false

# ── WSL 2 / DISPLAY preparation ───────────────────────────────────────────────
IS_WSL=false
if grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
fi

if [[ "$IS_WSL" == true ]]; then
    if [[ -z "${DISPLAY:-}" ]]; then
        HOST_IP=$(grep nameserver /etc/resolv.conf | awk '{print $2}' | head -n1 || echo "")
        if [[ -n "$HOST_IP" ]]; then
            export DISPLAY="$HOST_IP:0.0"
            info "WSL 2 detected — DISPLAY set to '$DISPLAY'"
        fi
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
section "Prerequisites"
# ══════════════════════════════════════════════════════════════════════════════

# ── Python 3.10+ ──────────────────────────────────────────────────────────────
for candidate in python3 python3.12 python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c \
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "")
        major="${ver%%.*}"
        minor="${ver##*.}"
        if [[ -n "$ver" && "$major" -ge 3 && "$minor" -ge 10 ]]; then
            PYTHON="$candidate"
            PYTHON_VER="$ver"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    fail "Python 3.10+ not found"
    die "Install Python 3.10 or newer: https://www.python.org/downloads/"
fi
ok "Python ${PYTHON_VER}  (${PYTHON})"

# ── Tkinter ───────────────────────────────────────────────────────────────────
if "$PYTHON" -c "import tkinter" 2>/dev/null; then
    TKINTER_OK=true
    ok "Tkinter available"
else
    warn "Tkinter not installed — attempting automatic install..."
    if command -v apt-get &>/dev/null; then
        if sudo apt-get update -qq && sudo apt-get install -y python3-tk -qq; then
            TKINTER_OK=true
            ok "Tkinter installed via apt-get"
        else
            die "Failed to install python3-tk automatically. Run: sudo apt-get install python3-tk"
        fi
    elif command -v dnf &>/dev/null; then
        if sudo dnf install -y python3-tkinter -q; then
            TKINTER_OK=true
            ok "Tkinter installed via dnf"
        else
            die "Failed to install python3-tkinter. Run: sudo dnf install python3-tkinter"
        fi
    elif command -v pacman &>/dev/null; then
        if sudo pacman -S --noconfirm tk; then
            TKINTER_OK=true
            ok "Tkinter installed via pacman"
        else
            die "Failed to install tk. Run: sudo pacman -S tk"
        fi
    else
        die "Unknown package manager — install tkinter manually, then re-run."
    fi
fi

# ── installer.py ──────────────────────────────────────────────────────────────
if [[ ! -f "$INSTALLER" ]]; then
    die "installer.py not found in $SCRIPT_DIR"
fi
ok "installer.py found"

# ── Docker + Compose ──────────────────────────────────────────────────────────
# Fail early here instead of deep inside the GUI's deployment step.
if ! command -v docker &>/dev/null; then
    fail "Docker not found"
    die "Install Docker Engine + Compose plugin: https://docs.docker.com/engine/install/"
fi
ok "Docker found  ($(docker --version 2>/dev/null | awk '{print $3}' | tr -d ','))"

if docker compose version &>/dev/null; then
    ok "Docker Compose plugin available"
else
    fail "Docker Compose plugin not found"
    die "Install the Compose plugin: https://docs.docker.com/compose/install/"
fi

# Daemon reachability — try direct, then via sudo (installer runs docker as root).
if docker info &>/dev/null || sudo -n docker info &>/dev/null 2>&1; then
    ok "Docker daemon reachable"
else
    warn "Docker daemon not reachable yet — the installer will prompt for sudo during deployment"
fi

# ══════════════════════════════════════════════════════════════════════════════
section "STATUS SUMMARY"
# ══════════════════════════════════════════════════════════════════════════════

echo ""
# Python
status_row "Python:" "${PYTHON_VER}  (${PYTHON})" "$GREEN"

# Tkinter
if [[ "$TKINTER_OK" == true ]]; then
    status_row "Tkinter:" "available" "$GREEN"
else
    status_row "Tkinter:" "missing" "$RED"
fi

echo ""
divider

# ── Launch GUI installer ───────────────────────────────────────────────────────
echo ""
info "Launching POS Installation Wizard..."
echo ""

cd "$SCRIPT_DIR"
exec "$PYTHON" "$INSTALLER" "${INSTALLER_ARGS[@]+"${INSTALLER_ARGS[@]}"}"
