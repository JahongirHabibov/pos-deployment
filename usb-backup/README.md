# KASSIO USB Backup — Automount

Makes a plugged-in USB stick usable as an additional backup target for the POS.

## Why a host component is needed

The backup service runs in a container, and a container cannot mount a block
device — that is a kernel operation on the host. Docker can only make an
*existing* host mount visible, which the compose file does:

```yaml
      - /mnt/kassio-usb:/external/managed:rslave
      - /media:/external/media:rslave
      - /run/media:/external/run-media:rslave
```

`rslave` is mandatory. Without mount propagation the container keeps seeing the
state from container start, so a stick plugged in later stays invisible — and
backups would report success while landing on the container's own disk.

On a machine with a desktop session, udisks already mounts sticks under
`/media/<user>/<LABEL>` and nothing here is required. A kiosk terminal boots
straight into a browser with no desktop and no file manager, so nothing mounts
anything. This rule closes that gap and, as a side effect, gives every customer
machine the same deterministic behaviour instead of one that depends on whether
someone happens to be logged in.

## Install

```bash
sudo ./install.sh                 # rule + mount root
sudo ./install.sh --install-deps  # also apt-get exfatprogs / ntfs-3g
docker compose up -d backup       # apply the bind mounts
```

Then: **POS ▸ Settings ▸ Backup ▸ External targets ▸ Add target**.

The GUI installer offers this as a checkbox in step 3 (`POS_USB_BACKUP` in
`.env`). Unticking it later stops future installs; it does not remove a rule
already on the machine — use `uninstall.sh` for that.

## What it does

`99-kassio-usb-backup.rules` mounts every USB *storage partition* to

```
/mnt/kassio-usb/by-uuid/<FS-UUID>
```

with `nosuid,nodev,noexec`, and unmounts it on removal. `systemd-mount
--no-block` hands the work to systemd because udev kills long-running commands
in `RUN+=`.

## Security

Auto-mounting removable media means the kernel parses a filesystem handed to the
machine by whoever walked in with a stick. Three things bound that:

- `nosuid,nodev,noexec` — nothing on the medium can gain privileges or execute.
- Opt-in — without the checkbox (or this script) no rule exists at all.
- Only `ID_BUS=="usb"` partitions with a real filesystem are touched.

Physical access to a POS terminal already implies a lot; this does not widen it
meaningfully, but it is a deliberate trade rather than an accident.

## Which machine gets the stick

The **Docker host** — the machine running the POS backend. Not a thin-client
terminal. In split deployments those are different machines
(see `../README-en.md`).

## Filesystem choice

FAT32 or exFAT are the safest choices: no ownership semantics, so files stay
readable on any computer. On ext4 the backups end up owned by root, which is
fine for the POS but awkward when the customer plugs the stick into a laptop.

## Troubleshooting

Nothing appears in the "Add target" dialog:

```bash
lsblk -o NAME,FSTYPE,LABEL,UUID,MOUNTPOINT   # is it mounted at all?
findmnt --submounts /mnt/kassio-usb          # did the rule fire?
journalctl -u 'mnt-kassio\\x2dusb-*' -n 50   # why did the mount fail?
udevadm test /sys/class/block/sdb1 2>&1 | tail -30
```

An exFAT or NTFS stick that never mounts usually means the userspace helper is
missing — re-run `install.sh --install-deps`.

After changing the compose file, the sidecar needs recreating, not restarting:

```bash
docker compose up -d backup
```
