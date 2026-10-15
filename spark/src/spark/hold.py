"""The brake's hold: while it exists, `spark launch` refuses every load. Only Dan releases it, until Phase 2a's gate
lifts it within rule 5's bounds (Task 17).

From Phase 2a the hold also carries the boot it was written on, its brake episode, the model that was starting when the
brake fired, and what was available then (Task 13), which the brake writes (Task 23) and the gate reads (Task 17). A
hold written by Phase 1's brake has none of the four, and reads with each None."""

from __future__ import annotations

import json
import logging
import math
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from spark.gateproto import AUTO_RELEASE_EVERY_S

FILE = "hold.json"
UNREADABLE_SINCE = "an unknown time"  # the `since` of read_hold's stand-in for a hold file it can't read
_PHASE_1 = ("since", "reason", "unloaded")  # the fields Phase 1's brake writes and reads
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hold:
    # Local time, to the second: Phase 1's brake wrote it with no zone (2026-10-08T03:12:00); 2a's writes its UTC offset
    # too (2026-10-08T03:12:00-07:00), so it reads unambiguously across a clock going back (Task 23). Phase 1's reader
    # takes either as text.
    since: str
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


def release_hold(state_dir: Path, expected: Hold | None = None) -> bool:
    """Remove the brake's hold: True when one was removed. Dan's release (`expected` None) lifts whatever stands.

    The gate's automatic release passes the hold it judged, and only that one goes (Task 17; the controller's ruling
    there): the file is first moved aside under a name of this call's own, then read. One that isn't `expected` (the
    brake fired again between the gate's reading and now, or it can't be read) is put back, but never over a newer hold
    the brake wrote meanwhile, which then stands. The folder is fsynced, so what was done survives a power cycle. Should
    neither the link nor a rename put it back (a filesystem gone read-only since the move aside), it says so in the
    journal and raises: no hold stands until the gate's next start puts it back (recover_release)."""
    path = Path(state_dir) / FILE
    if expected is None:  # Dan's: whatever stands, and any hold left aside, so the gate's start never puts it back
        lifted = False
        for each in (path, *Path(state_dir).glob(f"{FILE}.releasing-*")):
            try:
                each.unlink()
            except FileNotFoundError:
                continue
            lifted = True
        return lifted
    aside = path.with_name(f"{FILE}.releasing-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        os.rename(path, aside)
    except FileNotFoundError:
        return False
    try:
        found = _parse(aside.read_text())
    except (OSError, ValueError, RecursionError):
        found = None
    released = found is not None and found == expected
    if released:
        aside.unlink(missing_ok=True)
    else:
        try:
            os.link(aside, path)  # back where it was, unless the brake has written a newer hold there meanwhile
        except FileExistsError:
            pass
        except OSError:
            try:
                os.rename(aside, path)  # no link here: renamed back, so a hold always stands
            except OSError as err:  # a filesystem gone read-only since the move aside: no hold stands, and it says so
                _log.error("the brake's hold, moved aside as %s, couldn't be put back (%s): no hold stands until the "
                           "gate's next start puts it back", aside.name, err)
                raise
        aside.unlink(missing_ok=True)
    try:
        _fsync_folder(state_dir)
    except OSError:
        pass  # what was done is done; only its surviving a power cycle is less sure
    return released


def recover_release(state_dir: Path) -> Hold | None:
    """At the gate's start, before anything reads the hold (Task 18; the controller's rulings at Task 17's review and
    re-review): a hold a crash left aside in release_hold's window, between its rename and its link back or its
    removal, is put back when no hold file stands, which fails safe: loads stay paused. Rule 5 judges it again, and one
    whose release was recorded waits for Dan, which the gate then says (Room.at_start's `recovered`). With a hold file
    standing, the brake's newer one, that one stays, and what is aside goes. Returns the hold put back, None when none
    was (nothing aside, or a hold standing)."""
    folder = Path(state_dir)
    path = folder / FILE

    def written(aside: Path) -> float:
        try:
            return aside.lstat().st_mtime
        except OSError:
            return float("-inf")

    asides = sorted(folder.glob(f"{FILE}.releasing-*"), key=written)  # the newest last
    if not asides:
        return None
    restored: Hold | None = None
    if not os.path.lexists(path):
        newest = asides[-1]
        try:
            os.link(newest, path)
        except FileExistsError:  # the brake wrote one meanwhile: it stands, and nothing was put back
            pass
        except OSError:
            os.rename(newest, path)
            asides.pop()
            restored = read_hold(Path(state_dir))
        else:
            restored = read_hold(Path(state_dir))
    for aside in asides:
        aside.unlink(missing_ok=True)
    try:
        _fsync_folder(state_dir)
    except OSError:
        pass
    return restored


def _fsync_folder(state_dir: Path) -> None:
    folder = os.open(state_dir, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


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
        fired = datetime.fromisoformat(hold.since).timestamp()  # by its UTC offset; with none (Phase 1's), local time
    except (ValueError, OverflowError, OSError):
        return True
    return min(fired, now) - last_auto_release_at < AUTO_RELEASE_EVERY_S
