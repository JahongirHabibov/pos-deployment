#!/usr/bin/env bash
# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.
#
# Installs the KASSIO Power Agent on a kiosk terminal.
# Run as root on the terminal (or from the image build script):
#
#   sudo ./install.sh
#   sudo ./install.sh --origins http://192.168.1.50,http://localhost
#
set -euo pipefail

INSTALL_DIR=/opt/kassio-power-agent
UNIT_NAME=kassio-power-agent.service
UNIT_PATH=/etc/systemd/system/${UNIT_NAME}
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGINS=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --origins)
      ORIGINS="${2:-}"
      shift 2
      ;;
    -h|--help)
      tail -n +2 "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but not installed." >&2
  exit 1
fi

echo "Installing agent to ${INSTALL_DIR}"
install -d -m 0755 "${INSTALL_DIR}"
install -m 0755 "${SRC_DIR}/kassio_power_agent.py" "${INSTALL_DIR}/kassio_power_agent.py"
install -m 0644 "${SRC_DIR}/README.md" "${INSTALL_DIR}/README.md"

echo "Installing ${UNIT_PATH}"
install -m 0644 "${SRC_DIR}/${UNIT_NAME}" "${UNIT_PATH}"

if [[ -n "${ORIGINS}" ]]; then
  echo "Restricting allowed origins to: ${ORIGINS}"
  install -d -m 0755 "/etc/systemd/system/${UNIT_NAME}.d"
  cat >"/etc/systemd/system/${UNIT_NAME}.d/origins.conf" <<EOF
[Service]
Environment=KASSIO_POWER_ALLOWED_ORIGINS=${ORIGINS}
EOF
fi

systemctl daemon-reload
systemctl enable "${UNIT_NAME}"
systemctl restart "${UNIT_NAME}"

# Health check via python3 rather than curl: a minimal kiosk image may not ship
# curl, but python3 is already a hard requirement for the agent itself.
health_check() {
  python3 - <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:9110/health", timeout=3) as response:
        payload = json.load(response)
except Exception as exc:
    print(f"health check failed: {exc}", file=sys.stderr)
    sys.exit(1)
if not payload.get("ok"):
    print(f"unexpected health payload: {payload}", file=sys.stderr)
    sys.exit(1)
if not payload.get("poweroff_available"):
    print("agent is up but found no poweroff command on this system", file=sys.stderr)
    sys.exit(2)
PY
}

for _ in 1 2 3 4 5; do
  sleep 1
  status=0
  health_check || status=$?
  if [[ ${status} -eq 0 ]]; then
    echo "OK — agent is healthy on http://127.0.0.1:9110"
    exit 0
  fi
  # 2 = agent answered but the system has no poweroff command; retrying is pointless.
  if [[ ${status} -eq 2 ]]; then
    exit 2
  fi
done

echo "Agent did not answer on /health. Check: journalctl -u ${UNIT_NAME} -n 50" >&2
exit 1
