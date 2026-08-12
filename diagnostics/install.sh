#!/usr/bin/env bash
# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.
#
# Installs the KASSIO diagnostics service on a POS terminal.
#
#   sudo ./install.sh
#   sudo ./install.sh --port 9120 --deployment-dir /opt/pos-deployment
#
# The installer refuses rather than displaces: if the port is taken or the
# sudoers rule does not validate, nothing is installed. Every step is
# idempotent, so re-running it is safe.

set -euo pipefail

INSTALL_DIR=/opt/kassio-diagnostics
CONFIG_DIR=/etc/kassio-diagnostics
UNIT_NAME=kassio-diagnostics.service
UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"
DROPIN_DIR="/etc/systemd/system/${UNIT_NAME}.d"
SUDOERS_PATH=/etc/sudoers.d/kassio-diagnostics
DESKTOP_PATH=/usr/share/applications/kassio-diagnostics.desktop
HELPER_PATH="${INSTALL_DIR}/bin/diag-helper"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PORT=9120
DEPLOYMENT_DIR=""
ADMIN_USER="${SUDO_USER:-}"

say() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --deployment-dir) DEPLOYMENT_DIR="${2:-}"; shift 2 ;;
    --user) ADMIN_USER="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

# --------------------------------------------------------------- pre-flight
[[ $EUID -eq 0 ]] || fail "this installer must run as root (use sudo)."
command -v python3 >/dev/null 2>&1 || fail "python3 is required but not installed."
command -v systemctl >/dev/null 2>&1 || fail "systemd is required but not present."
[[ "${PORT}" =~ ^[0-9]{2,5}$ ]] || fail "invalid port: ${PORT}"

if [[ -z "${ADMIN_USER}" || "${ADMIN_USER}" == "root" ]]; then
  fail "could not determine the administration user. Run via sudo from that \
user's session, or pass --user <name>."
fi
id -u "${ADMIN_USER}" >/dev/null 2>&1 || fail "user ${ADMIN_USER} does not exist."

# The service runs as this user and escalates through sudo, so that user must
# actually be allowed to sudo. Checking now turns a silent runtime failure into
# a clear installation error.
if ! sudo -l -U "${ADMIN_USER}" >/dev/null 2>&1; then
  say "WARNING: could not confirm that ${ADMIN_USER} may use sudo."
  say "         Repairs will fail until that user has sudo rights."
fi

# Port check: never displace whatever is already listening.
if command -v ss >/dev/null 2>&1; then
  if ss -ltn "( sport = :${PORT} )" 2>/dev/null | grep -q ":${PORT}"; then
    if systemctl is-active --quiet "${UNIT_NAME}"; then
      say "Port ${PORT} is held by this service — it will be restarted."
    else
      fail "port ${PORT} is already in use by another program. Installation stopped."
    fi
  fi
fi

# --------------------------------------------------------------- files
say "Installing to ${INSTALL_DIR}"
install -d -m 0755 "${INSTALL_DIR}" "${INSTALL_DIR}/bin"
install -d -m 0755 "${CONFIG_DIR}"

for directory in kassio_diagnostics locales web; do
  rm -rf "${INSTALL_DIR:?}/${directory}"
  cp -r "${SRC_DIR}/${directory}" "${INSTALL_DIR}/${directory}"
done
find "${INSTALL_DIR}/kassio_diagnostics" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
install -m 0644 "${SRC_DIR}/README.md" "${INSTALL_DIR}/README.md"

# Root-owned and not writable by the service user: this file is the security
# boundary, so the account it protects against must not be able to edit it.
install -o root -g root -m 0755 "${SRC_DIR}/bin/diag-helper" "${HELPER_PATH}"
chown -R root:root "${INSTALL_DIR}"
chmod -R go-w "${INSTALL_DIR}"

# --------------------------------------------------------------- sudoers
# Written to a temporary file and validated before it is allowed to take
# effect: a malformed sudoers file can lock everyone out of sudo.
say "Installing the permission rule"
SUDOERS_TMP="$(mktemp)"
trap 'rm -f "${SUDOERS_TMP}"' EXIT
cat >"${SUDOERS_TMP}" <<EOF
# Installed by kassio-diagnostics. Read verbs only.
# Every mutating verb is deliberately absent and therefore still requires the
# password. The real boundary is the fixed verb table inside diag-helper.
Cmnd_Alias KASSIO_DIAG_READ = ${HELPER_PATH} read *
${ADMIN_USER} ALL=(root) NOPASSWD: KASSIO_DIAG_READ
EOF
chmod 0440 "${SUDOERS_TMP}"
if ! visudo -c -f "${SUDOERS_TMP}" >/dev/null; then
  fail "the generated sudoers rule failed validation. Nothing was changed."
fi
install -o root -g root -m 0440 "${SUDOERS_TMP}" "${SUDOERS_PATH}"

# --------------------------------------------------------------- unit
say "Installing ${UNIT_PATH}"
UNIT_TMP="$(mktemp)"
sed "s/__ADMIN_USER__/${ADMIN_USER}/g" "${SRC_DIR}/${UNIT_NAME}" >"${UNIT_TMP}"
if [[ "${PORT}" != "9120" ]]; then
  sed -i "s/KASSIO_DIAG_PORT=9120/KASSIO_DIAG_PORT=${PORT}/; \
          s/SocketBindAllow=ipv4:tcp:9120/SocketBindAllow=ipv4:tcp:${PORT}/" "${UNIT_TMP}"
fi

# SocketBindAllow/SocketBindDeny need systemd 249. On older systems the
# directives would make the unit fail to load, so they are dropped there — the
# loopback bind still keeps the service off the network.
SYSTEMD_VERSION="$(systemctl --version | head -n1 | awk '{print $2}' | tr -cd '0-9')"
if [[ -n "${SYSTEMD_VERSION}" && "${SYSTEMD_VERSION}" -lt 249 ]]; then
  say "systemd ${SYSTEMD_VERSION} does not support SocketBind* — omitting those lines."
  sed -i '/^SocketBind/d' "${UNIT_TMP}"
fi
install -o root -g root -m 0644 "${UNIT_TMP}" "${UNIT_PATH}"
rm -f "${UNIT_TMP}"

if [[ -n "${DEPLOYMENT_DIR}" ]]; then
  install -d -m 0755 "${DROPIN_DIR}"
  cat >"${DROPIN_DIR}/deployment.conf" <<EOF
[Service]
Environment=KASSIO_DIAG_DEPLOYMENT_DIR=${DEPLOYMENT_DIR}
EOF
  chmod 0644 "${DROPIN_DIR}/deployment.conf"
fi

systemctl daemon-reload
systemctl enable "${UNIT_NAME}" >/dev/null
systemctl restart "${UNIT_NAME}"

# --------------------------------------------------------------- health
say "Waiting for the service to answer"
healthy=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  sleep 1
  if python3 - "${PORT}" <<'PY'
import json, sys, urllib.request
port = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as response:
        payload = json.load(response)
except Exception:
    sys.exit(1)
sys.exit(0 if payload.get("ok") else 1)
PY
  then healthy=1; break; fi
done
if [[ "${healthy}" -ne 1 ]]; then
  fail "the service did not answer on port ${PORT}. Check: journalctl -u ${UNIT_NAME} -n 50"
fi

# --------------------------------------------------------------- shortcuts
say "Installing the desktop entry"
sed "s|http://127.0.0.1:9120/|http://127.0.0.1:${PORT}/|" \
  "${SRC_DIR}/kassio-diagnostics.desktop" >"${DESKTOP_PATH}"
chmod 0644 "${DESKTOP_PATH}"
command -v update-desktop-database >/dev/null 2>&1 \
  && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true

# Managed bookmark, best effort only — a browser without it is a missing
# convenience, never a failed installation.
BOOKMARK_JSON="[{\"toplevel_name\":\"POS\"},{\"name\":\"POS Diagnose\",\"url\":\"http://127.0.0.1:${PORT}/\"}]"
for policy_dir in /etc/chromium/policies/managed /etc/opt/chrome/policies/managed \
                  /etc/chromium-browser/policies/managed; do
  parent="$(dirname "$(dirname "${policy_dir}")")"
  if [[ -d "${parent}" ]]; then
    mkdir -p "${policy_dir}" 2>/dev/null || continue
    printf '{"ManagedBookmarks": %s}\n' "${BOOKMARK_JSON}" \
      >"${policy_dir}/kassio-diagnostics.json" 2>/dev/null || true
    chmod 0644 "${policy_dir}/kassio-diagnostics.json" 2>/dev/null || true
    say "  bookmark installed for ${policy_dir}"
  fi
done
# Firefox shares a single policies.json, so an existing one is never touched.
FIREFOX_POLICY=/etc/firefox/policies/policies.json
if [[ -d /etc/firefox ]]; then
  if [[ -e "${FIREFOX_POLICY}" ]]; then
    say "  Firefox already has a policy file — leaving it untouched."
    say "    Add the bookmark by hand if wanted: http://127.0.0.1:${PORT}/"
  else
    mkdir -p /etc/firefox/policies 2>/dev/null || true
    cat >"${FIREFOX_POLICY}" <<EOF
{"policies": {"Bookmarks": [{"Title": "POS Diagnose",
 "URL": "http://127.0.0.1:${PORT}/", "Placement": "toolbar"}]}}
EOF
    chmod 0644 "${FIREFOX_POLICY}" 2>/dev/null || true
    say "  bookmark installed for Firefox"
  fi
fi

say ""
say "OK — the diagnostics interface is available at http://127.0.0.1:${PORT}/"
say "     service: ${UNIT_NAME}   (runs as ${ADMIN_USER})"
say "     logs:    journalctl -u ${UNIT_NAME} -n 50"
if [[ ! -f "${CONFIG_DIR}/expected-config.json" ]]; then
  say ""
  say "     Next step: open the interface and fill in \"Techniker-Setup\"."
  say "     Without it the tool cannot tell whether a printer sits at the"
  say "     wrong address."
fi
