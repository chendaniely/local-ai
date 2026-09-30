"""Free memory as GB10 sees it: MemAvailable from /proc/meminfo (never nvidia-smi)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

KIB_PER_GIB = 1024 * 1024


@dataclass(frozen=True)
class MemInfo:
    total_gib: float
    available_gib: float


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
    return MemInfo(values["MemTotal"] / KIB_PER_GIB, values["MemAvailable"] / KIB_PER_GIB)


def read_meminfo(path: Path = Path("/proc/meminfo")) -> MemInfo:
    return parse_meminfo(Path(path).read_text())
