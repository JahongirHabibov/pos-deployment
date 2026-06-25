#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# setup-remote-access.sh — Remote-Zugriff Vorbereitung für POS-Gerät
#
# Installiert und konfiguriert:
#   1. WireGuard VPN Client  (wg0 → verbindet zu Verwaltungs-VPS)
#   2. TigerVNC Server       (Display :1, Port 5901 — für Apache Guacamole)
#   3. OpenSSH Server        (Port 22, nur an wg0-IP gebunden — für Guacamole SSH)
#
# Voraussetzungen:
#   - Ubuntu 22.04 / 24.04
#   - Sudo-Berechtigung
#   - Internetzugang (apt-get)
#
# Usage:
#   sudo ./setup-remote-access.sh                         # interaktiv
#   sudo ./setup-remote-access.sh --config ./custom.conf  # mit Config-Datei
#   sudo ./setup-remote-access.sh --reconfigure-wg        # WireGuard neu konfigurieren
#   sudo ./setup-remote-access.sh --reconfigure-vnc       # VNC neu konfigurieren
#   sudo ./setup-remote-access.sh --status                # Status anzeigen
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Konstanten ─────────────────────────────────────────────────────────────────
WG_INTERFACE="wg0"
WG_CONF="/etc/wireguard/${WG_INTERFACE}.conf"
VNC_DISPLAY=":1"
VNC_PORT="5901"
VNC_GEOMETRY="1920x1080"
VNC_DEPTH="24"
VNC_SERVICE="pos-vnc"

# ── Farben ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[FEHLER]${NC} $*" >&2; }
step()    { echo -e "\n${BOLD}${BLUE}══════════════════════════════════════════════${NC}"; \
            echo -e "${BOLD}${BLUE}  $*${NC}"; \
            echo -e "${BOLD}${BLUE}══════════════════════════════════════════════${NC}"; }
die()     { error "$*"; exit 1; }

# ── Argument-Parsing ───────────────────────────────────────────────────────────
CONFIG_FILE="${SCRIPT_DIR}/remote-access.conf"
RECONFIGURE_WG=false
RECONFIGURE_VNC=false
SHOW_STATUS=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            CONFIG_FILE="$2"; shift 2 ;;
        --reconfigure-wg)
            RECONFIGURE_WG=true; shift ;;
        --reconfigure-vnc)
            RECONFIGURE_VNC=true; shift ;;
        --status)
            SHOW_STATUS=true; shift ;;
        --help|-h)
            sed -n '3,13p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *)
            die "Unbekannte Option: $1 (--help für Hilfe)" ;;
    esac
done

# ── Banner ─────────────────────────────────────────────────────────────────────
echo -e "${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   POS System — Remote-Zugriff Setup          ║"
echo "  ║   WireGuard VPN + TigerVNC                   ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Root-Check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    die "Dieses Script muss mit sudo ausgeführt werden:\n       sudo $0 $*"
fi

# ── Echter Benutzer ermitteln (nicht root) ─────────────────────────────────────
if [[ -n "${SUDO_USER:-}" ]]; then
    REAL_USER="$SUDO_USER"
    REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
else
    die "SUDO_USER nicht gesetzt. Bitte mit 'sudo ./setup-remote-access.sh' ausführen."
fi

info "Konfiguriere für Benutzer: ${BOLD}${REAL_USER}${NC}"
info "Home-Verzeichnis:          ${REAL_HOME}"

# ── Config-Datei laden (falls vorhanden) ───────────────────────────────────────
# Variablen, die aus der Config-Datei befüllt werden können:
#   WG_SERVER_ENDPOINT   — VPS-Adresse:Port  (z.B. 1.2.3.4:51820)
#   WG_SERVER_PUBKEY     — Öffentlicher WG-Key des VPS
#   WG_CLIENT_IP         — Zugewiesene IP für dieses Gerät (z.B. 10.8.0.2/32)
#   WG_ALLOWED_IPS       — Routen über WG (Standard: 10.8.0.0/24)
#   VNC_PASSWORD         — VNC-Passwort (optional, sonst interaktiv)
#   VNC_GEOMETRY         — Auflösung (Standard: 1920x1080)

WG_SERVER_ENDPOINT=""
WG_SERVER_PUBKEY=""
WG_CLIENT_IP=""
WG_ALLOWED_IPS=""
VNC_PASSWORD=""

if [[ -f "$CONFIG_FILE" ]]; then
    info "Lade Config: ${CONFIG_FILE}"
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
    success "Config geladen"
fi

# ── Status-Anzeige ─────────────────────────────────────────────────────────────
show_status() {
    step "Status"

    echo ""
    echo -e "  ${BOLD}WireGuard (${WG_INTERFACE}):${NC}"
    if systemctl is-active --quiet "wg-quick@${WG_INTERFACE}" 2>/dev/null; then
        echo -e "    Status:  ${GREEN}aktiv${NC}"
        wg show "$WG_INTERFACE" 2>/dev/null | sed 's/^/    /' || true
    elif [[ -f "$WG_CONF" ]]; then
        echo -e "    Status:  ${YELLOW}konfiguriert, aber nicht aktiv${NC}"
    else
        echo -e "    Status:  ${RED}nicht konfiguriert${NC}"
    fi

    echo ""
    echo -e "  ${BOLD}TigerVNC (${VNC_SERVICE}):${NC}"
    if systemctl is-active --quiet "${VNC_SERVICE}" 2>/dev/null; then
        echo -e "    Status:  ${GREEN}aktiv${NC} (Port ${VNC_PORT})"
    elif systemctl is-enabled --quiet "${VNC_SERVICE}" 2>/dev/null; then
        echo -e "    Status:  ${YELLOW}konfiguriert, aber nicht aktiv${NC}"
    else
        echo -e "    Status:  ${RED}nicht konfiguriert${NC}"
    fi
    echo ""
}

if [[ "$SHOW_STATUS" == true ]]; then
    show_status
    exit 0
fi

# ── 1. Abhängigkeiten installieren ─────────────────────────────────────────────
step "Schritt 1: Abhängigkeiten installieren"

apt-get update -qq

PKGS_TO_INSTALL=()

# WireGuard
if ! command -v wg &>/dev/null; then
    PKGS_TO_INSTALL+=(wireguard)
else
    success "WireGuard bereits installiert"
fi

# TigerVNC
if ! command -v vncserver &>/dev/null; then
    PKGS_TO_INSTALL+=(tigervnc-standalone-server)
else
    success "TigerVNC bereits installiert"
fi

# OpenSSH-Server (für Guacamole SSH-Verbindung)
if ! dpkg -l openssh-server &>/dev/null; then
    PKGS_TO_INSTALL+=(openssh-server)
else
    success "OpenSSH-Server bereits installiert"
fi

# Desktop-Umgebung prüfen und ggf. XFCE installieren
HAS_DESKTOP=false
for de_cmd in gnome-session startxfce4 startkde startlxde; do
    if command -v "$de_cmd" &>/dev/null; then
        HAS_DESKTOP=true
        break
    fi
done

if [[ "$HAS_DESKTOP" == false ]]; then
    warn "Keine Desktop-Umgebung gefunden — installiere XFCE4 (zuverlässigste VNC-Option)"
    PKGS_TO_INSTALL+=(xfce4 xfce4-goodies dbus-x11)
fi

if [[ ${#PKGS_TO_INSTALL[@]} -gt 0 ]]; then
    info "Installiere: ${PKGS_TO_INSTALL[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${PKGS_TO_INSTALL[@]}"
    success "Pakete installiert"
fi

# ── 2. WireGuard einrichten ────────────────────────────────────────────────────
step "Schritt 2: WireGuard VPN einrichten"

WG_PRIVKEY_FILE="/etc/wireguard/pos-private.key"
WG_PUBKEY_FILE="/etc/wireguard/pos-public.key"

# Schlüssel generieren (falls noch nicht vorhanden oder Neukonfiguration)
if [[ ! -f "$WG_PRIVKEY_FILE" ]] || [[ "$RECONFIGURE_WG" == true ]]; then
    info "Generiere WireGuard-Schlüsselpaar..."
    wg genkey | tee "$WG_PRIVKEY_FILE" | wg pubkey > "$WG_PUBKEY_FILE"
    chmod 600 "$WG_PRIVKEY_FILE"
    success "Neues WireGuard-Schlüsselpaar generiert"
else
    success "WireGuard-Schlüssel bereits vorhanden"
fi

WG_PRIVKEY=$(cat "$WG_PRIVKEY_FILE")
WG_PUBKEY=$(cat "$WG_PUBKEY_FILE")

# Öffentlichen Key anzeigen — Admin muss diesen auf dem VPS eintragen
echo ""
echo -e "  ${BOLD}${YELLOW}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "  ${BOLD}${YELLOW}║  ÖFFENTLICHER KEY DIESES POS-GERÄTS (für VPS-Admin)          ║${NC}"
echo -e "  ${BOLD}${YELLOW}╠══════════════════════════════════════════════════════════════╣${NC}"
echo -e "  ${BOLD}${YELLOW}║${NC}  ${BOLD}${WG_PUBKEY}${NC}"
echo -e "  ${BOLD}${YELLOW}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}→ Diesen Key auf dem VPS in /etc/wireguard/wg0.conf eintragen:${NC}"
echo ""
echo "    [Peer]"
echo "    PublicKey = ${WG_PUBKEY}"
echo "    AllowedIPs = <ZUGEWIESENE-IP-FÜR-DIESES-GERÄT>/32"
echo ""

# WireGuard-Konfiguration abfragen (falls nicht aus Config-Datei)
WG_NEEDS_CONFIG=false
if [[ -z "$WG_SERVER_ENDPOINT" ]] || [[ -z "$WG_SERVER_PUBKEY" ]] || [[ -z "$WG_CLIENT_IP" ]]; then
    WG_NEEDS_CONFIG=true
fi
if [[ "$RECONFIGURE_WG" == true ]]; then
    WG_NEEDS_CONFIG=true
fi

if [[ "$WG_NEEDS_CONFIG" == true ]]; then
    echo -e "  ${BOLD}VPS-Informationen eingeben:${NC}"
    echo "  (Diese Daten erhalten Sie vom VPS-Administrator)"
    echo ""

    if [[ -z "$WG_SERVER_ENDPOINT" ]]; then
        read -rp "  VPS-Adresse:Port (z.B. 1.2.3.4:51820): " WG_SERVER_ENDPOINT
    fi
    if [[ -z "$WG_SERVER_PUBKEY" ]]; then
        read -rp "  VPS öffentlicher WG-Key:                 " WG_SERVER_PUBKEY
    fi
    if [[ -z "$WG_CLIENT_IP" ]]; then
        read -rp "  Zugewiesene IP für dieses Gerät (CIDR):  " WG_CLIENT_IP
    fi
    if [[ -z "$WG_ALLOWED_IPS" ]]; then
        # Subnetz aus der Client-IP ableiten (z.B. 10.8.0.2/32 → 10.8.0.0/24)
        WG_SUBNET=$(echo "$WG_CLIENT_IP" | sed 's|\([0-9]*\.[0-9]*\.[0-9]*\)\.[0-9]*/.*|\1.0/24|')
        read -rp "  WG-Subnetz für Routing [${WG_SUBNET}]:    " WG_ALLOWED_IPS_INPUT
        WG_ALLOWED_IPS="${WG_ALLOWED_IPS_INPUT:-$WG_SUBNET}"
    fi
fi

# Validierung
[[ -z "$WG_SERVER_ENDPOINT" ]] && die "WG_SERVER_ENDPOINT darf nicht leer sein"
[[ -z "$WG_SERVER_PUBKEY" ]]   && die "WG_SERVER_PUBKEY darf nicht leer sein"
[[ -z "$WG_CLIENT_IP" ]]       && die "WG_CLIENT_IP darf nicht leer sein"
[[ -z "$WG_ALLOWED_IPS" ]]     && WG_ALLOWED_IPS="10.8.0.0/24"

# WG-IP ohne Maske (für VNC-Bind-Adresse)
WG_CLIENT_IP_ADDR="${WG_CLIENT_IP%%/*}"

# /etc/wireguard/wg0.conf schreiben
if [[ -f "$WG_CONF" ]]; then
    cp "$WG_CONF" "${WG_CONF}.backup.$(date +%Y%m%d-%H%M%S)"
    info "Bestehende WG-Config gesichert"
fi

cat > "$WG_CONF" << EOF
# WireGuard Client — POS-Gerät
# Generiert von setup-remote-access.sh am $(date)

[Interface]
PrivateKey = ${WG_PRIVKEY}
Address    = ${WG_CLIENT_IP}
# Konservative MTU — verhindert Fragmentierung/Stalls hinter PPPoE/DSL (VNC)
MTU        = 1380

[Peer]
PublicKey            = ${WG_SERVER_PUBKEY}
Endpoint             = ${WG_SERVER_ENDPOINT}
AllowedIPs           = ${WG_ALLOWED_IPS}
PersistentKeepalive  = 25
EOF

chmod 600 "$WG_CONF"
success "WireGuard-Config geschrieben: ${WG_CONF}"

# Dienst aktivieren und starten
systemctl enable "wg-quick@${WG_INTERFACE}"
if systemctl is-active --quiet "wg-quick@${WG_INTERFACE}"; then
    systemctl restart "wg-quick@${WG_INTERFACE}"
else
    systemctl start "wg-quick@${WG_INTERFACE}"
fi
success "WireGuard-Dienst aktiv (wg-quick@${WG_INTERFACE})"

# Verbindung prüfen
sleep 2
if ip addr show "$WG_INTERFACE" &>/dev/null; then
    WG_IP_ACTIVE=$(ip addr show "$WG_INTERFACE" | grep 'inet ' | awk '{print $2}' || echo "unbekannt")
    success "WireGuard-Interface aktiv: ${WG_IP_ACTIVE}"
else
    warn "WireGuard-Interface ${WG_INTERFACE} noch nicht sichtbar — ggf. VPS-Config prüfen"
fi

# ── 3. TigerVNC einrichten ─────────────────────────────────────────────────────
step "Schritt 3: TigerVNC Server einrichten"

VNC_DIR="${REAL_HOME}/.vnc"
mkdir -p "$VNC_DIR"
chown "${REAL_USER}:${REAL_USER}" "$VNC_DIR"

# VNC-Passwort setzen
VNC_NEEDS_PASSWORD=false
if [[ ! -f "${VNC_DIR}/passwd" ]]; then
    VNC_NEEDS_PASSWORD=true
fi
if [[ "$RECONFIGURE_VNC" == true ]]; then
    VNC_NEEDS_PASSWORD=true
fi

if [[ "$VNC_NEEDS_PASSWORD" == true ]]; then
    if [[ -n "$VNC_PASSWORD" ]]; then
        # Passwort aus Config-Datei (non-interaktiv)
        echo "$VNC_PASSWORD" | sudo -u "$REAL_USER" vncpasswd -f > "${VNC_DIR}/passwd"
    else
        info "VNC-Passwort setzen (min. 6 Zeichen, max. 8 werden verwendet):"
        sudo -u "$REAL_USER" vncpasswd "${VNC_DIR}/passwd"
    fi
    # passwd-Datei muss REAL_USER gehören — sonst kann vncserver sie nicht lesen
    # (bei --config schreibt der root-Redirect die Datei als root)
    chown "${REAL_USER}:${REAL_USER}" "${VNC_DIR}/passwd"
    chmod 600 "${VNC_DIR}/passwd"
    success "VNC-Passwort gesetzt"
else
    success "VNC-Passwort bereits vorhanden"
fi

# xstartup schreiben (Desktop-Session für VNC)
XSTARTUP="${VNC_DIR}/xstartup"
if [[ ! -f "$XSTARTUP" ]] || [[ "$RECONFIGURE_VNC" == true ]]; then

    # Desktop-Umgebung erkennen
    VNC_SESSION_CMD=""
    if command -v startxfce4 &>/dev/null; then
        VNC_SESSION_CMD="exec startxfce4"
    elif command -v gnome-session &>/dev/null; then
        VNC_SESSION_CMD="export XDG_SESSION_TYPE=x11\nexec gnome-session"
    elif command -v startlxde &>/dev/null; then
        VNC_SESSION_CMD="exec startlxde"
    elif command -v startkde &>/dev/null; then
        VNC_SESSION_CMD="exec startkde"
    else
        VNC_SESSION_CMD="exec startxfce4"
    fi

    cat > "$XSTARTUP" << EOF
#!/bin/sh
# VNC xstartup — automatisch generiert von setup-remote-access.sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
[ -r /etc/X11/Xresources ] && xrdb /etc/X11/Xresources
[ -r "\${HOME}/.Xresources" ] && xrdb -merge "\${HOME}/.Xresources"
$(echo -e "$VNC_SESSION_CMD")
EOF
    chmod +x "$XSTARTUP"
    chown "${REAL_USER}:${REAL_USER}" "$XSTARTUP"
    success "xstartup geschrieben (${VNC_SESSION_CMD})"
else
    success "xstartup bereits vorhanden"
fi

# Systemd-Service für VNC erstellen
# Bindet an WireGuard-IP (nur über VPN erreichbar)
VNC_SERVICE_FILE="/etc/systemd/system/${VNC_SERVICE}.service"

cat > "$VNC_SERVICE_FILE" << EOF
[Unit]
Description=POS TigerVNC Server (Display ${VNC_DISPLAY})
After=network.target wg-quick@${WG_INTERFACE}.service
Wants=wg-quick@${WG_INTERFACE}.service

[Service]
Type=forking
User=${REAL_USER}
WorkingDirectory=${REAL_HOME}
PIDFile=${REAL_HOME}/.vnc/%H${VNC_DISPLAY}.pid

ExecStartPre=-/usr/bin/vncserver -kill ${VNC_DISPLAY} > /dev/null 2>&1
ExecStart=/usr/bin/vncserver ${VNC_DISPLAY} \
    -geometry ${VNC_GEOMETRY} \
    -depth ${VNC_DEPTH} \
    -interface ${WG_CLIENT_IP_ADDR} \
    -SecurityTypes VncAuth \
    -rfbauth ${REAL_HOME}/.vnc/passwd
ExecStop=/usr/bin/vncserver -kill ${VNC_DISPLAY}

Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${VNC_SERVICE}"

# Dienst starten (nur wenn WireGuard-Interface vorhanden)
if ip addr show "$WG_INTERFACE" &>/dev/null; then
    if systemctl is-active --quiet "${VNC_SERVICE}"; then
        systemctl restart "${VNC_SERVICE}"
    else
        systemctl start "${VNC_SERVICE}"
    fi
    success "VNC-Dienst aktiv (${VNC_SERVICE})"
else
    warn "WireGuard-Interface nicht verfügbar — VNC startet automatisch nach WG-Verbindung"
    info "Manuell starten: sudo systemctl start ${VNC_SERVICE}"
fi

# ── 4. SSH-Server einrichten (nur über WireGuard erreichbar) ───────────────────
step "Schritt 4: SSH-Server einrichten"

# sshd nur an WireGuard-IP binden → kein Zugriff aus LAN/Internet, nur Tunnel
SSHD_DROPIN_DIR="/etc/ssh/sshd_config.d"
mkdir -p "$SSHD_DROPIN_DIR"
cat > "${SSHD_DROPIN_DIR}/10-pos-remote.conf" << EOF
# Generiert von setup-remote-access.sh — SSH nur über WireGuard-Tunnel
ListenAddress ${WG_CLIENT_IP_ADDR}
EOF
success "sshd an ${WG_CLIENT_IP_ADDR} gebunden (nur Tunnel)"

# Socket-Activation deaktivieren — überschreibt sonst ListenAddress (Ubuntu 24.04)
if systemctl list-unit-files ssh.socket &>/dev/null; then
    systemctl disable --now ssh.socket 2>/dev/null || true
fi

# ssh.service erst nach WireGuard starten — ListenAddress braucht die wg0-IP
SSH_DROPIN_DIR="/etc/systemd/system/ssh.service.d"
mkdir -p "$SSH_DROPIN_DIR"
cat > "${SSH_DROPIN_DIR}/pos-wg.conf" << EOF
[Unit]
After=wg-quick@${WG_INTERFACE}.service
Wants=wg-quick@${WG_INTERFACE}.service

[Service]
Restart=on-failure
RestartSec=5
EOF

systemctl daemon-reload
systemctl enable ssh 2>/dev/null || systemctl enable sshd 2>/dev/null || true

if ip addr show "$WG_INTERFACE" &>/dev/null; then
    systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null || \
        warn "SSH-Dienst konnte nicht gestartet werden — manuell prüfen: systemctl status ssh"
    success "SSH-Dienst aktiv (${WG_CLIENT_IP_ADDR}:22)"
else
    warn "WireGuard-Interface nicht verfügbar — SSH startet automatisch nach WG-Verbindung"
fi

# ── Zusammenfassung ────────────────────────────────────────────────────────────
step "Setup abgeschlossen"

echo ""
echo -e "  ${BOLD}WireGuard:${NC}"
echo -e "    Öffentlicher Key:  ${BOLD}${WG_PUBKEY}${NC}"
echo -e "    Zugewiesene IP:    ${BOLD}${WG_CLIENT_IP}${NC}"
echo -e "    Server-Endpoint:   ${WG_SERVER_ENDPOINT}"
echo -e "    Dienst:            wg-quick@${WG_INTERFACE}"
echo ""
echo -e "  ${BOLD}TigerVNC:${NC}"
echo -e "    Bind-Adresse:      ${WG_CLIENT_IP_ADDR}:${VNC_PORT}"
echo -e "    Display:           ${VNC_DISPLAY}"
echo -e "    Auflösung:         ${VNC_GEOMETRY}"
echo -e "    Dienst:            ${VNC_SERVICE}"
echo ""
echo -e "  ${BOLD}SSH:${NC}"
echo -e "    Bind-Adresse:      ${WG_CLIENT_IP_ADDR}:22 (nur über Tunnel)"
echo -e "    Dienst:            ssh"
echo ""
echo -e "  ${BOLD}${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "  ${BOLD}${GREEN}║  Apache Guacamole Verbindungen (VNC + SSH):                  ║${NC}"
echo -e "  ${BOLD}${GREEN}╠══════════════════════════════════════════════════════════════╣${NC}"
echo -e "  ${BOLD}${GREEN}║${NC}  VNC →  Hostname: ${BOLD}${WG_CLIENT_IP_ADDR}${NC}  Port: ${BOLD}${VNC_PORT}${NC}  (Passwort: VNC-Passwort)"
echo -e "  ${BOLD}${GREEN}║${NC}  SSH →  Hostname: ${BOLD}${WG_CLIENT_IP_ADDR}${NC}  Port: ${BOLD}22${NC}    (User/Pass: ${REAL_USER} + Linux-Passwort)"
echo -e "  ${BOLD}${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Status prüfen:${NC}  sudo $0 --status"
echo -e "  ${CYAN}WG neu:${NC}         sudo $0 --reconfigure-wg"
echo -e "  ${CYAN}VNC neu:${NC}        sudo $0 --reconfigure-vnc"
echo ""
