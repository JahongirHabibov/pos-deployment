#!/usr/bin/env bash
# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.
#
# Removes the KASSIO diagnostics service.
#
#   sudo ./uninstall.sh              asks whether to keep the configuration
#   sudo ./uninstall.sh --purge      removes the configuration as well
#   sudo ./uninstall.sh --keep-config

set -euo pipefail

INSTALL_DIR=/opt/kassio-diagnostics
CONFIG_DIR=/etc/kassio-diagnostics
UNIT_NAME=kassio-diagnostics.service
MODE=ask

while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) MODE=purge; shift ;;
    --keep-config) MODE=keep; shift ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "must run as root (use sudo)." >&2; exit 1; }

systemctl disable --now "${UNIT_NAME}" >/dev/null 2>&1 || true
rm -f "/etc/systemd/system/${UNIT_NAME}"
rm -rf "/etc/systemd/system/${UNIT_NAME}.d"
systemctl daemon-reload

rm -f /etc/sudoers.d/kassio-diagnostics
rm -f /usr/share/applications/kassio-diagnostics.desktop
rm -f /etc/chromium/policies/managed/kassio-diagnostics.json \
      /etc/opt/chrome/policies/managed/kassio-diagnostics.json \
      /etc/chromium-browser/policies/managed/kassio-diagnostics.json
rm -rf "${INSTALL_DIR}"

# The Firefox policy file is shared, so it is only removed when this tool is
# demonstrably its only content.
FIREFOX_POLICY=/etc/firefox/policies/policies.json
if [[ -f "${FIREFOX_POLICY}" ]] && grep -q "127.0.0.1:9120" "${FIREFOX_POLICY}" \
   && [[ "$(wc -l <"${FIREFOX_POLICY}")" -le 3 ]]; then
  rm -f "${FIREFOX_POLICY}"
fi

if [[ "${MODE}" == "ask" ]]; then
  read -r -p "Remove the site configuration in ${CONFIG_DIR} as well? [y/N] " answer
  [[ "${answer}" =~ ^[Yy]$ ]] && MODE=purge || MODE=keep
fi
if [[ "${MODE}" == "purge" ]]; then
  rm -rf "${CONFIG_DIR}"
  echo "Configuration removed."
else
  echo "Configuration kept in ${CONFIG_DIR}."
fi

command -v update-desktop-database >/dev/null 2>&1 \
  && update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
echo "KASSIO diagnostics removed."
