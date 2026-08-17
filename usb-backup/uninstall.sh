#!/usr/bin/env bash
# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.
#
# Removes the KASSIO USB backup automount.
#
#   sudo ./uninstall.sh
#
# Backup files already written to a stick are left untouched — only the
# automount rule goes away. Targets registered in the POS keep their entry and
# will simply report "not connected".
set -euo pipefail

RULE_DEST=/etc/udev/rules.d/99-kassio-usb-backup.rules
MOUNT_ROOT=/mnt/kassio-usb

if [[ $EUID -ne 0 ]]; then
  echo "This uninstaller must run as root." >&2
  exit 1
fi

if [[ -f "${RULE_DEST}" ]]; then
  echo "Removing ${RULE_DEST}"
  rm -f "${RULE_DEST}"
  udevadm control --reload-rules
else
  echo "No rule at ${RULE_DEST} — nothing to remove."
fi

# Unmount deepest paths first so nested mounts do not block their parent.
mapfile -t mounted < <(findmnt -rno TARGET --submounts "${MOUNT_ROOT}" 2>/dev/null \
  | grep -v "^${MOUNT_ROOT}$" | sort -r || true)
for m in "${mounted[@]}"; do
  echo "Unmounting ${m}"
  systemd-umount "${m}" || umount "${m}" || echo "  (still busy — unmount by hand)"
done

echo "Done. ${MOUNT_ROOT} is left in place; remove it by hand if you want it gone."
