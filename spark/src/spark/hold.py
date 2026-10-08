"""The brake's hold: while it exists, `spark launch` refuses every load. Only Dan releases it, until Phase 2a's gate
lifts it within rule 5's bounds (Task 17).

From Phase 2a the hold also carries the boot it was written on, its brake episode, the model that was starting when the
brake fired, and what was available then (Task 13), which the brake writes (Task 23) and the gate reads (Task 17). A
hold written by Phase 1's brake has none of the four, and reads with each None."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from spark.gateproto import AUTO_RELEASE_EVERY_S

FILE = "hold.json"
UNREADABLE_SINCE = "an unknown time"  # the `since` of read_hold's stand-in for a hold file it can't read
_PHASE_1 = ("since", "reason", "unloaded")  # the fields Phase 1's brake writes and reads


@dataclass(frozen=True)
class Hold:
    since: str  # local time, to the second, with no zone, as Phase 1 wrote it: 2026-10-08T03:12:00
    reason: str
    unloaded: tuple[str, ...]
    boot_id: str | None = None  # the boot the brake wrote it on; None (Phase 1's) counts as another boot's
    episode: int | None = None  # its brake episode (brakeevents.episode_for)
    loading: str | None = None  # the model that was starting when the brake fired: it doesn't reload by itself
    available_gib: float | None = None  # MemAvailable when it fired, for held_by_brake's words


def _finite(value: Any) -> bool:
    """A finite int or float, never a bool; a JSON int too large for a float isn't one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _parse(text: str) -> Hold:
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a hold: expected a JSON object")
    since, reason, unloaded = data.get("since"), data.get("reason"), data.get("unloaded", [])
    if not (isinstance(since, str) and isinstance(reason, str) and isinstance(unloaded, list)
            and all(isinstance(name, str) for name in unloaded)):
        raise ValueError("not a hold: since and reason must be strings, unloaded a list of model names")
    boot_id, episode = data.get("boot_id"), data.get("episode")
    loading, available = data.get("loading"), data.get("available_gib")
    if not (boot_id is None or (isinstance(boot_id, str) and boot_id)):
        raise ValueError("not a hold: boot_id must be non-empty text, or null")
    if not (episode is None or (isinstance(episode, int) and not isinstance(episode, bool) and episode >= 1)):
        raise ValueError("not a hold: episode must be a whole number from 1, or null")
    if not (loading is None or isinstance(loading, str)):
        raise ValueError("not a hold: loading must be a model's name, or null")
    if not (available is None or _finite(available)):
        raise ValueError("not a hold: available_gib must be a finite number, or null")
    return Hold(since, reason, tuple(unloaded), boot_id, episode, loading,
                None if available is None else float(available))


def read_hold(state_dir: Path) -> Hold | None:
    """None only when there is no hold file. One that can't be read or parsed still holds (fail closed),
    with a reason that names the file."""
    path = Path(state_dir) / FILE
    try:
        return _parse(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError) as err:  # RecursionError: JSON nested too deep
        return Hold(UNREADABLE_SINCE, f"hold file {path} can't be read: {err}", ())


def write_hold(state_dir: Path, hold: Hold) -> None:
    """Atomic, and on disk before it returns: the brake writes the hold as memory runs out, when a GB10
    can hard-freeze, and the hold must still be there after the power cycle. Phase 2a's four fields are written only
    when set, so a hold without them is Phase 1's, byte for byte, and Phase 1's reader takes either."""
    path = Path(state_dir) / FILE
    tmp = path.with_suffix(".tmp")
    written = {name: value for name, value in asdict(hold).items() if value is not None or name in _PHASE_1}
    with open(tmp, "w") as f:
        f.write(json.dumps(written | {"unloaded": list(hold.unloaded)}))
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


def release_waits_for_dan(hold: Hold, *, boot_id: str, last_auto_release_at: float | None, now: float) -> bool:
    """Whether only Dan may lift `hold` (rule 5): one from another boot, Phase 1's and a damaged file's among them,
    since a freeze is the case the fsynced hold exists for; or one that fired within AUTO_RELEASE_EVERY_S of the last
    automatic release. The one rule Task 15's words and Task 17's release both use.

    The hold fired at its `since`, local time. One that can't be read, or that lies ahead of `now` (written before the
    clock was set back), errs towards Dan: an unreadable one waits for him, and one ahead counts as fired `now`."""
    if hold.boot_id is None or hold.boot_id != boot_id:
        return True
    if last_auto_release_at is None:
        return False
    try:
        fired = datetime.fromisoformat(hold.since).timestamp()  # no zone: local time
    except (ValueError, OverflowError, OSError):
        return True
    return min(fired, now) - last_auto_release_at < AUTO_RELEASE_EVERY_S
