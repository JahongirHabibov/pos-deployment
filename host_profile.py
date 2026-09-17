"""Hardware profile for the PostgreSQL container.

Terminals in the field range from 4 GB machines with a spinning disk to 8 GB
machines with an SSD. PostgreSQL has no idea which one it runs on, and the two
settings that matter most point in opposite directions:

  random_page_cost          4 on a spinning disk (seeks are expensive),
                            1.1 on an SSD (random reads cost about the same as
                            sequential ones).
  effective_io_concurrency  2 on a spinning disk (one head), 200 on an SSD.

Memory settings follow the installed RAM. The browser, the containers and the
OS have to fit next to the database, so a 4 GB machine gets half of what an
8 GB machine gets.

The installer writes the result as PG_* keys into .env; docker-compose.prod.yml
reads them with HDD defaults, so a machine that was never profiled still gets
safe values. Set POS_HOST_PROFILE=manual in .env to keep hand-tuned values.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROFILE_ENV_KEY = "POS_HOST_PROFILE"
MANUAL_PROFILE = "manual"

# Below this much RAM the machine gets the small memory profile. 6 GiB sits
# between the 4 GB and 8 GB terminals; the kernel reports a little less than
# the nominal size, so a nominal 8 GB machine still lands above it.
SMALL_MEMORY_LIMIT = 6 * 1024**3

DISK_SETTINGS = {
    "hdd": {"PG_RANDOM_PAGE_COST": "4", "PG_EFFECTIVE_IO_CONCURRENCY": "2"},
    "ssd": {"PG_RANDOM_PAGE_COST": "1.1", "PG_EFFECTIVE_IO_CONCURRENCY": "200"},
}

MEMORY_SETTINGS = {
    "4g": {
        "PG_SHARED_BUFFERS": "256MB",
        "PG_EFFECTIVE_CACHE_SIZE": "1GB",
        "PG_WORK_MEM": "4MB",
        "PG_MAINTENANCE_WORK_MEM": "64MB",
    },
    "8g": {
        "PG_SHARED_BUFFERS": "512MB",
        "PG_EFFECTIVE_CACHE_SIZE": "2GB",
        "PG_WORK_MEM": "8MB",
        "PG_MAINTENANCE_WORK_MEM": "128MB",
    },
}


def _existing_ancestor(path: Path) -> Path:
    # /var/lib/docker does not exist before Docker is installed; the disk that
    # will hold it is the disk of its nearest existing parent.
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def detect_rotational(path: str = "/var/lib/docker") -> bool | None:
    """True for a spinning disk, False for an SSD, None if unknown.

    Follows the device stack (partition, LVM, dm-crypt) down to the physical
    disks: one rotational disk anywhere below makes the whole stack slow.
    """
    target = _existing_ancestor(Path(path))
    try:
        source = subprocess.run(
            ["findmnt", "-n", "-o", "SOURCE", "--target", str(target)],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        # btrfs reports "/dev/sda2[/@rootfs]"
        source = source.split("[", 1)[0]
        if not source.startswith("/dev/"):
            return None
        rota = subprocess.run(
            ["lsblk", "-n", "-s", "-o", "ROTA", source],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    if not rota:
        return None
    return any(value == "1" for value in rota)


def detect_memory_bytes(meminfo: str = "/proc/meminfo") -> int | None:
    try:
        for line in Path(meminfo).read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def build_profile(rotational: bool | None, memory_bytes: int | None) -> tuple[str, dict[str, str]]:
    """Return (profile name, PG_* settings). Unknown values fall back to the
    weakest hardware: a wrong HDD guess on an SSD costs a little speed, a wrong
    SSD guess on an HDD makes queries crawl."""
    disk = "ssd" if rotational is False else "hdd"
    memory = "8g" if memory_bytes is not None and memory_bytes >= SMALL_MEMORY_LIMIT else "4g"
    settings = {**DISK_SETTINGS[disk], **MEMORY_SETTINGS[memory]}
    return f"{disk}-{memory}", settings


def detect_profile(path: str = "/var/lib/docker") -> tuple[str, dict[str, str]]:
    return build_profile(detect_rotational(path), detect_memory_bytes())


if __name__ == "__main__":
    name, values = detect_profile()
    print(f"{PROFILE_ENV_KEY}={name}")
    for key, value in values.items():
        print(f"{key}={value}")
