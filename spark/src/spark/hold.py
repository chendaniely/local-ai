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


def _parse(text: str) -> Hold:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a hold: expected a JSON object")
    since, reason, unloaded = data.get("since"), data.get("reason"), data.get("unloaded", [])
    if not (isinstance(since, str) and isinstance(reason, str) and isinstance(unloaded, list)
            and all(isinstance(name, str) for name in unloaded)):
        raise ValueError("not a hold: since and reason must be strings, unloaded a list of model names")
    return Hold(since, reason, tuple(unloaded))


def read_hold(state_dir: Path) -> Hold | None:
    """None only when there is no hold file. One that can't be read or parsed still holds (fail closed),
    with a reason that names the file."""
    path = Path(state_dir) / FILE
    try:
        return _parse(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as err:
        return Hold("an unknown time", f"hold file {path} can't be read: {err}", ())


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
    try:
        (Path(state_dir) / FILE).unlink()
    except FileNotFoundError:
        return False
    return True
