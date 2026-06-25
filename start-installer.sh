#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# start-installer.sh — POS System Setup & Installation Wizard Launcher
#
# Usage:
#   ./start-installer.sh              # full setup + 3-step GUI wizard
#   ./start-installer.sh --skip-setup # skip to deployment (provisioning done)
#   ./start-installer.sh --no-remote  # skip remote access setup
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/installer.py"
REMOTE_SETUP="$SCRIPT_DIR/setup-remote-access.sh"

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
NO_REMOTE=false
INSTALLER_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-setup) SKIP_SETUP=true; INSTALLER_ARGS+=("--skip-setup"); shift ;;
        --no-remote)  NO_REMOTE=true;  shift ;;
        --help|-h)
            sed -n '3,7p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *) INSTALLER_ARGS+=("$1"); shift ;;
    esac
done

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   POS System — Setup & Installation          ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── State tracking ────────────────────────────────────────────────────────────
PYTHON=""
PYTHON_VER=""
TKINTER_OK=false
WG_OK=false
VNC_OK=false
WG_LABEL="not configured"
VNC_LABEL="not configured"

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
section "STEP 1  Prerequisites"
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

# ══════════════════════════════════════════════════════════════════════════════
section "STEP 2  Remote Access (WireGuard + TigerVNC)"
# ══════════════════════════════════════════════════════════════════════════════

if [[ "$NO_REMOTE" == true ]]; then
    warn "Remote access setup skipped (--no-remote)"
else
    # ── Check current WireGuard status ────────────────────────────────────────
    if systemctl is-active --quiet "wg-quick@wg0" 2>/dev/null; then
        WG_IP=$(ip addr show wg0 2>/dev/null | grep 'inet ' | awk '{print $2}' | head -1 || echo "")
        WG_LABEL="active${WG_IP:+ · ${WG_IP}}"
        WG_OK=true
        ok "WireGuard: ${WG_LABEL}"
    elif [[ -f "/etc/wireguard/wg0.conf" ]]; then
        WG_LABEL="configured — not running"
        warn "WireGuard: ${WG_LABEL}"
    else
        fail "WireGuard: not configured"
    fi

    # ── Check current VNC status ──────────────────────────────────────────────
    if systemctl is-active --quiet "pos-vnc" 2>/dev/null; then
        VNC_LABEL="active · port 5901"
        VNC_OK=true
        ok "VNC Server:  ${VNC_LABEL}"
    elif systemctl is-enabled --quiet "pos-vnc" 2>/dev/null; then
        VNC_LABEL="configured — not running"
        warn "VNC Server:  ${VNC_LABEL}"
    else
        fail "VNC Server:  not configured"
    fi

    # ── Run setup if anything is missing ─────────────────────────────────────
    if [[ "$WG_OK" == false ]] || [[ "$VNC_OK" == false ]]; then
        echo ""
        if [[ ! -f "$REMOTE_SETUP" ]]; then
            warn "setup-remote-access.sh not found in ${SCRIPT_DIR} — skipping"
        else
            info "Starting remote access setup..."
            echo ""
            divider
            if [[ $EUID -eq 0 ]]; then
                bash "$REMOTE_SETUP"
            else
                sudo bash "$REMOTE_SETUP"
            fi
            divider
            echo ""

            # ── Re-check after setup ──────────────────────────────────────────
            if systemctl is-active --quiet "wg-quick@wg0" 2>/dev/null; then
                WG_IP=$(ip addr show wg0 2>/dev/null | grep 'inet ' | awk '{print $2}' | head -1 || echo "")
                WG_LABEL="active${WG_IP:+ · ${WG_IP}}"
                WG_OK=true
            fi
            if systemctl is-active --quiet "pos-vnc" 2>/dev/null; then
                VNC_LABEL="active · port 5901"
                VNC_OK=true
            fi
        fi
    fi
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

if [[ "$NO_REMOTE" == false ]]; then
    # WireGuard
    if [[ "$WG_OK" == true ]]; then
        status_row "WireGuard:" "$WG_LABEL" "$GREEN"
    elif [[ -f "/etc/wireguard/wg0.conf" ]]; then
        status_row "WireGuard:" "$WG_LABEL" "$YELLOW"
    else
        status_row "WireGuard:" "$WG_LABEL" "$RED"
    fi

    # VNC
    if [[ "$VNC_OK" == true ]]; then
        status_row "VNC Server:" "$VNC_LABEL" "$GREEN"
    elif systemctl is-enabled --quiet "pos-vnc" 2>/dev/null; then
        status_row "VNC Server:" "$VNC_LABEL" "$YELLOW"
    else
        status_row "VNC Server:" "$VNC_LABEL" "$RED"
    fi

    # WG public key (useful for admin reference)
    if [[ -f "/etc/wireguard/pos-public.key" ]]; then
        echo ""
        WG_PUBKEY=$(cat /etc/wireguard/pos-public.key)
        status_row "WG Public Key:" "${WG_PUBKEY}" "$CYAN"
    fi
fi

echo ""
divider

# ── Launch GUI installer ───────────────────────────────────────────────────────
echo ""
info "Launching POS Installation Wizard..."
echo ""

cd "$SCRIPT_DIR"
exec "$PYTHON" "$INSTALLER" "${INSTALLER_ARGS[@]+"${INSTALLER_ARGS[@]}"}"
