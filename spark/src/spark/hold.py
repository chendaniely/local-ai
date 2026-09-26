"""The brake's hold: while it exists, `spark launch` refuses every load. Only Dan releases it."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

FILE = "hold.json"


@dataclass(frozen=True)
class Hold:
    since: str
    reason: str
    unloaded: tuple[str, ...]


def read_hold(state_dir: Path) -> Hold | None:
    path = Path(state_dir) / FILE
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return Hold(data["since"], data["reason"], tuple(data.get("unloaded", ())))


def write_hold(state_dir: Path, hold: Hold) -> None:
    """Atomic, and on disk before it returns: the brake writes the hold as memory runs out, when a GB10
    can hard-freeze, and the hold must still be there after the power cycle."""
    path = Path(state_dir) / FILE
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(asdict(hold) | {"unloaded": list(hold.unloaded)}))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    folder = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(folder)  # the rename itself
    finally:
        os.close(folder)


def release_hold(state_dir: Path) -> bool:
    path = Path(state_dir) / FILE
    if path.exists():
        path.unlink()
        return True
    return False
