#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# start-installer.sh — POS System Installation Wizard Launcher
#
# Usage:
#   chmod +x start-installer.sh
#   ./start-installer.sh              # full 3-step wizard
#   ./start-installer.sh --skip-setup # skip to deployment (steps 1 & 2 already done)
#
# What this script does:
#   1. Verifies that Python 3.10+ is available
#   2. Verifies that the Tkinter GUI library is available
#   3. Verifies that installer.py exists in the same directory
#   4. Launches installer.py
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/installer.py"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[FEHLER]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   POS System — Installations-Assistent       ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── WSL 2 / DISPLAY Vorbereitung ──────────────────────────────────────────────
IS_WSL=false
if grep -qi microsoft /proc/version 2>/dev/null; then
    IS_WSL=true
fi

if [[ "$IS_WSL" == true ]]; then
    if [[ -z "${DISPLAY:-}" ]]; then
        HOST_IP=$(grep nameserver /etc/resolv.conf | awk '{print $2}' | head -n1 || echo "")
        if [[ -n "$HOST_IP" ]]; then
            export DISPLAY="$HOST_IP:0.0"
            info "WSL 2 erkannt. DISPLAY-Variable wurde auf '$DISPLAY' gesetzt."
        fi
    fi
fi

# ── 1. Check Python 3.10+ ─────────────────────────────────────────────────────
PYTHON=""
for candidate in python3 python3.12 python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c \
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major="${ver%%.*}"
        minor="${ver##*.}"
        if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    die "Python 3.10 oder neuer wurde nicht gefunden.\n" \
        "       Bitte installieren Sie Python: https://www.python.org/downloads/"
fi
success "Python gefunden: $PYTHON ($ver)"

# ── 2. Check & Install Tkinter ────────────────────────────────────────────────
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    warn "Das Python-Modul 'tkinter' ist nicht installiert."
    info "Versuche, 'tkinter' automatisch zu installieren (erfordert ggf. Sudo-Berechtigungen) ..."
    if command -v apt-get &>/dev/null; then
        if sudo apt-get update && sudo apt-get install -y python3-tk; then
            success "Tkinter erfolgreich über apt-get installiert."
        else
            die "Automatische Installation von python3-tk fehlgeschlagen."
        fi
    elif command -v dnf &>/dev/null; then
        if sudo dnf install -y python3-tkinter; then
            success "Tkinter erfolgreich über dnf installiert."
        else
            die "Automatische Installation von python3-tkinter fehlgeschlagen."
        fi
    elif command -v pacman &>/dev/null; then
        if sudo pacman -S --noconfirm tk; then
            success "Tkinter erfolgreich über pacman installiert."
        else
            die "Automatische Installation von tk (pacman) fehlgeschlagen."
        fi
    else
        die "Paketmanager unbekannt — bitte installieren Sie 'tkinter' manuell."
    fi
fi
success "Tkinter verfügbar"

# ── 3. Check installer.py exists ─────────────────────────────────────────────
if [[ ! -f "$INSTALLER" ]]; then
    die "installer.py nicht gefunden in $SCRIPT_DIR"
fi

# ── 4. Launch ─────────────────────────────────────────────────────────────────
echo ""
info "Starte Installations-Assistent …"
echo ""
cd "$SCRIPT_DIR"
exec "$PYTHON" "$INSTALLER" "$@"
