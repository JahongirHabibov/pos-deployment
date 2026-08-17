#!/usr/bin/env bash
# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.
#
# Installs the KASSIO USB backup automount on the Docker host.
#
#   sudo ./install.sh
#   sudo ./install.sh --install-deps     # also apt-get exfatprogs / ntfs-3g
#
# Why this exists: the backup sidecar is a container and cannot mount a block
# device. On a kiosk terminal nothing else does it either — there is no desktop
# session, so no udisks automount. This rule makes a plugged-in stick appear at
# a deterministic path that the compose bind mounts expose to the sidecar.
#
# Removing it again: ./uninstall.sh
set -euo pipefail

RULE_NAME=99-kassio-usb-backup.rules
RULE_DEST=/etc/udev/rules.d/${RULE_NAME}
MOUNT_ROOT=/mnt/kassio-usb
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DEPS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-deps) INSTALL_DEPS=1; shift ;;
    -h|--help)
      tail -n +2 "${BASH_SOURCE[0]}" | grep '^#' | sed 's/^# \?//'
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "This installer must run as root." >&2
  exit 1
fi

if ! command -v systemd-mount >/dev/null 2>&1; then
  echo "systemd-mount not found — this host does not run systemd." >&2
  echo "Mount the medium yourself (fstab) under ${MOUNT_ROOT} instead." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Filesystem helpers for the formats USB sticks actually ship with
# ---------------------------------------------------------------------------
# vfat is in the kernel; exFAT and NTFS need userspace helpers. Without them the
# mount fails at 02:00 during a scheduled run instead of here, so check now.
missing=()
command -v mount.exfat      >/dev/null 2>&1 || \
  command -v mount.exfat-fuse >/dev/null 2>&1 || missing+=("exfatprogs")
command -v mount.ntfs       >/dev/null 2>&1 || \
  command -v ntfs-3g          >/dev/null 2>&1 || missing+=("ntfs-3g")

if [[ ${#missing[@]} -gt 0 ]]; then
  if [[ ${INSTALL_DEPS} -eq 1 ]]; then
    echo "Installing: ${missing[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
  else
    echo "NOTE: missing filesystem helpers: ${missing[*]}"
    echo "  Sticks formatted as exFAT or NTFS will not mount without them."
    echo "  Install with: sudo apt-get install ${missing[*]}"
    echo "  (or re-run this script with --install-deps)"
  fi
fi

# ---------------------------------------------------------------------------
# Rule + mount root
# ---------------------------------------------------------------------------
echo "Creating ${MOUNT_ROOT}"
install -d -m 0755 "${MOUNT_ROOT}" "${MOUNT_ROOT}/by-uuid"

echo "Installing ${RULE_DEST}"
install -m 0644 "${SRC_DIR}/${RULE_NAME}" "${RULE_DEST}"

udevadm control --reload-rules

# Pick up media that were already plugged in before the rule existed, so the
# admin does not have to re-plug the stick to finish setup.
echo "Triggering already-connected devices"
udevadm trigger --subsystem-match=block --action=add
udevadm settle --timeout=15 || true

# ---------------------------------------------------------------------------
# Report what is now visible
# ---------------------------------------------------------------------------
mapfile -t mounted < <(findmnt -rno TARGET --submounts "${MOUNT_ROOT}" 2>/dev/null | grep -v "^${MOUNT_ROOT}$" || true)
if [[ ${#mounted[@]} -gt 0 ]]; then
  echo "OK — mounted media:"
  for m in "${mounted[@]}"; do
    echo "  ${m}"
  done
else
  echo "OK — rule installed. No USB storage is plugged in right now."
fi

cat <<'EOF'

Next steps:
  1. docker compose up -d backup      (applies the /external bind mounts)
  2. POS ▸ Settings ▸ Backup ▸ External targets ▸ Add target

The stick belongs in THIS machine (the Docker host), not in a thin-client
terminal — the backup service runs here.
EOF
