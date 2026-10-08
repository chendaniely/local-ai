"""Free memory as GB10 sees it: MemAvailable from /proc/meminfo (never nvidia-smi), with MemFree and Cached beside it,
and the boot id."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

KIB_PER_GIB = 1024 * 1024


@dataclass(frozen=True)
class MemInfo:
    """/proc/meminfo's figures, in GiB. `memfree_gib` is MemFree, never *free for a load*, which is rule 9's admission
    figure and is called `free_gib` everywhere else."""

    total_gib: float
    available_gib: float
    memfree_gib: float | None = None  # MemFree, when /proc/meminfo has it
    cached_gib: float | None = None  # Cached, the page cache, when /proc/meminfo has it


def parse_meminfo(text: str) -> MemInfo:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key.strip()] = int(parts[0])
    for needed in ("MemTotal", "MemAvailable"):
        if needed not in values:
            raise ValueError(f"/proc/meminfo has no {needed}")
    free, cached = values.get("MemFree"), values.get("Cached")
    return MemInfo(values["MemTotal"] / KIB_PER_GIB, values["MemAvailable"] / KIB_PER_GIB,
                   None if free is None else free / KIB_PER_GIB, None if cached is None else cached / KIB_PER_GIB)


def read_meminfo(path: Path = Path("/proc/meminfo")) -> MemInfo:
    return parse_meminfo(Path(path).read_text())


def boot_id(path: Path = Path("/proc/sys/kernel/random/boot_id")) -> str:
    """This boot's id, which the kernel makes new at every boot: its text, stripped."""
    return Path(path).read_text().strip()
