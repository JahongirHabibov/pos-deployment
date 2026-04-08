#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# start-installer.sh — POS System Installation Wizard Launcher
#
# Usage:
#   chmod +x start-installer.sh
#   ./start-installer.sh
#
# What this script does:
#   1. Verifies that Python 3.10+ is available
#   2. Verifies that the Tkinter GUI library is available
#   3. Ensures a graphical display is available (local, SSH -X, or Xvfb)
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

# ── 2. Check Tkinter ──────────────────────────────────────────────────────────
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    error "Das Python-Modul 'tkinter' ist nicht installiert."
    echo ""
    echo "  Bitte installieren Sie es mit einem der folgenden Befehle:"
    echo ""
    if command -v apt-get &>/dev/null; then
        echo "    sudo apt-get install python3-tk"
    elif command -v dnf &>/dev/null; then
        echo "    sudo dnf install python3-tkinter"
    elif command -v pacman &>/dev/null; then
        echo "    sudo pacman -S tk"
    else
        echo "    (Paketmanager unbekannt — bitte tkinter manuell installieren)"
    fi
    echo ""
    exit 1
fi
success "Tkinter verfügbar"

# ── 3. Ensure a graphical display is available ────────────────────────────────
#
# Three cases:
#   A) $DISPLAY is already set  →  local desktop or SSH -X/forwarding  →  OK
#   B) $DISPLAY not set, Xvfb available  →  start virtual framebuffer  →  OK
#   C) $DISPLAY not set, no Xvfb  →  print instructions and exit
#
_XVFB_PID=""

_cleanup_xvfb() {
    if [[ -n "$_XVFB_PID" ]] && kill -0 "$_XVFB_PID" 2>/dev/null; then
        kill "$_XVFB_PID" 2>/dev/null || true
    fi
}
trap _cleanup_xvfb EXIT

if [[ -z "${DISPLAY:-}" ]]; then
    warn "Keine grafische Umgebung gefunden (\$DISPLAY nicht gesetzt)."
    echo ""

    if command -v Xvfb &>/dev/null; then
        info "Xvfb gefunden — starte virtuellen Framebuffer auf :99 …"
        Xvfb :99 -screen 0 1280x800x24 &>/dev/null &
        _XVFB_PID=$!
        sleep 1  # give Xvfb time to initialise
        if kill -0 "$_XVFB_PID" 2>/dev/null; then
            export DISPLAY=:99
            success "Virtueller Display :99 gestartet (Xvfb PID $_XVFB_PID)"
        else
            _XVFB_PID=""
            error "Xvfb konnte nicht gestartet werden."
        fi
    fi

    # Still no display after Xvfb attempt → print help and exit
    if [[ -z "${DISPLAY:-}" ]]; then
        echo ""
        error "Kein grafischer Display verfügbar. Bitte eine der folgenden Optionen wählen:"
        echo ""
        echo "  Option 1 — SSH mit X11-Weiterleitung (empfohlen für Remote-Zugriff):"
        echo "    Verbinden Sie sich mit:  ssh -X benutzer@server"
        echo "    Dann starten Sie:        ./start-installer.sh"
        echo ""
        echo "  Option 2 — Xvfb installieren (virtueller Display):"
        if command -v apt-get &>/dev/null; then
            echo "    sudo apt-get install xvfb"
        elif command -v dnf &>/dev/null; then
            echo "    sudo dnf install xorg-x11-server-Xvfb"
        elif command -v pacman &>/dev/null; then
            echo "    sudo pacman -S xorg-server-xvfb"
        else
            echo "    (Paketmanager unbekannt — Xvfb manuell installieren)"
        fi
        echo "    Danach erneut ausführen: ./start-installer.sh"
        echo ""
        exit 1
    fi
else
    success "Grafischer Display verfügbar (\$DISPLAY=$DISPLAY)"
fi

# ── 4. Check installer.py exists ─────────────────────────────────────────────
if [[ ! -f "$INSTALLER" ]]; then
    die "installer.py nicht gefunden in $SCRIPT_DIR"
fi

# ── 5. Launch ─────────────────────────────────────────────────────────────────
echo ""
info "Starte Installations-Assistent …"
echo ""
cd "$SCRIPT_DIR"
exec "$PYTHON" "$INSTALLER"
