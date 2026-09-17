#!/usr/bin/env bash
# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.
#
# Prepares a Debian 13 machine as a POS kiosk terminal. Run once before
# ./start-installer.sh, as the administrator user:
#
#   sudo ./host-setup/setup.sh
#   sudo ./host-setup/setup.sh --dry-run          show what would change
#   sudo ./host-setup/setup.sh --only kiosk,ssh   run selected steps
#
# All options: sudo ./host-setup/setup.sh --help
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "This script must run as root: sudo $0 $*  (without sudo: su -, then add --admin-user <name>)" >&2
  exit 1
fi

# Debian always ships python3; a stripped install may not.
if ! command -v python3 >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3
fi

exec python3 "${SRC_DIR}/pos_host_setup.py" "$@"
