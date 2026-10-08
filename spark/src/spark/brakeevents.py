"""The brake's steps, recorded for the gate (Phase 2a; plan.md, rule 5): one JSON line per step in the brake's folder,
`brake/events.jsonl` (paths.STATE / EVENTS_FILE), which the brake appends (Task 23) and the gate reads every second
(Tasks 14 and 18), turning each into its notification and its marks. The brake never waits on the gate: it writes the
file and goes on.

Each event is keyed by the boot it was written on and its sequence number, `seq`, which comes from the file itself,
read under an exclusive `fcntl.flock`, so two writers (the brake and the S05 drill's `--once`, Task 25) never share
one, and `seq` goes on across the brake's restarts and across boots. The gate keeps the key of the last event it read
(GateState.brake_events_after) and reads the events after it, by their place in the file: a file removed, trimmed or
started again no longer holds that event, and then every event it holds is new to the gate, so none is hidden. The one
exception: a file removed within a boot starts its seq again from 1, and if it reaches the gate's key before the gate
reads it, the events up to that key are taken as read.

A line is written whole, with its newline, and fsynced before append_event returns, as the hold is: the brake writes
as memory runs out, when a GB10 can hard-freeze. The gate reads without the lock, so a last line with no newline is a
write in progress: it is never returned, and never read past. A line a crash cut short is ended by the next append,
so the next event stands on a line of its own; a line that isn't a whole event is passed over. The file is opened
without following a link or waiting on a FIFO, and root never writes it: the brake's folder is spark's.

The standard library only: the gate and the brake import it."""

from __future__ import annotations

import fcntl
import json
import math
import os
import stat
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal, get_args

from spark.hold import Hold

EVENTS_FILE = "events.jsonl"  # in the brake's folder, beside hold.FILE
Kind = Literal["fired", "unload", "warn"]
KINDS: tuple[str, ...] = get_args(Kind)
# An unload's model was starting, idle or answering, by the gate's activity record; None when that was missing or
# stale (the controller's ruling at Task 7). messages words each.
UNLOAD_STATES = ("starting", "idle", "answering")
_OPEN_FLAGS = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


@dataclass(frozen=True)
class BrakeEvent:
    seq: int  # from the file, under its lock: append_event sets it, whatever the event it is given holds
    at: float  # Unix seconds
    boot_id: str
    episode: int  # episode_for's
    kind: Kind  # the step: fired (the brake fired), unload (it unloaded `model`), or warn (Task 23 says when)
    model: str | None  # an unload's model; None for the rest
    state: str | None  # an unload's model's state, one of UNLOAD_STATES, or None
    available_gib: float  # MemAvailable when the brake acted
    line_gib: float  # the line the writer acted on, its own registry's, so a drill's raised line is worded as itself
    sent_by_brake: bool  # the brake sent its own alert, the gate being down: the gate doesn't send it again


def _whole(value: Any, least: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= least


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # a JSON int too large for a float
        return False


def _problem(data: dict[str, Any]) -> str | None:
    """What makes `data` not a whole event, or None: each field by name, never its value."""
    checks = {
        "seq": _whole(data.get("seq"), 1),
        "at": _finite(data.get("at")),
        "boot_id": isinstance(data.get("boot_id"), str) and bool(data.get("boot_id")),
        "episode": _whole(data.get("episode"), 1),
        "kind": data.get("kind") in KINDS,
        "model": data.get("model") is None or isinstance(data.get("model"), str),
        "state": data.get("state") is None or data.get("state") in UNLOAD_STATES,
        "available_gib": _finite(data.get("available_gib")),
        "line_gib": _finite(data.get("line_gib")),
        "sent_by_brake": isinstance(data.get("sent_by_brake"), bool),
    }
    wrong = [name for name, ok in checks.items() if not ok]
    return f"not a brake event: {', '.join(wrong)}" if wrong else None


def _parse(line: bytes) -> BrakeEvent | None:
    """One line as an event, or None for one that isn't a whole event."""
    try:
        data = json.loads(line)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        return None
    if not isinstance(data, dict) or _problem(data):
        return None
    return BrakeEvent(data["seq"], float(data["at"]), data["boot_id"], data["episode"], data["kind"], data["model"],
                      data["state"], float(data["available_gib"]), float(data["line_gib"]), data["sent_by_brake"])


def _events(data: bytes) -> list[BrakeEvent]:
    """The whole events in `data`, in the file's order: only lines that end in a newline, so a write in progress is
    left for next time."""
    lines = data.split(b"\n")[:-1]  # what follows the last newline isn't a whole line yet
    return [event for event in map(_parse, lines) if event is not None]


def _read_all(fd: int) -> bytes:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise ValueError("the brake's events file isn't a regular file")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(fd, 1 << 16):
        chunks.append(chunk)
    return b"".join(chunks)


def _last_seq(data: bytes) -> int:
    return max((event.seq for event in _events(data)), default=0)


def _read_locked(path: Path) -> bytes | None:
    """The file's bytes, read under a shared lock, so no append is part-way; None for a file that isn't there."""
    try:
        fd = os.open(path, os.O_RDONLY | _OPEN_FLAGS)
    except FileNotFoundError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        return _read_all(fd)
    finally:
        os.close(fd)  # and its lock with it


def append_event(path: Path, event: BrakeEvent) -> BrakeEvent:
    """Append `event` as one JSON line, its seq one past the file's last event, taken under an exclusive lock, so two
    writers never share one; fsynced before it returns. Returns the event as written. An event the reader would pass
    over is refused (ValueError) before anything is written, and so is root (PermissionError)."""
    if os.geteuid() == 0:
        raise PermissionError(f"root never writes in the brake's folder, so {path} isn't written")
    problem = _problem(asdict(replace(event, seq=1)))
    if problem:
        raise ValueError(problem)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | _OPEN_FLAGS, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = _read_all(fd)
        written = replace(event, seq=_last_seq(data) + 1)
        line = json.dumps(asdict(written), allow_nan=False).encode() + b"\n"
        if data and not data.endswith(b"\n"):
            line = b"\n" + line  # a line a crash cut short: end it, so this one stands on its own
        while line:
            line = line[os.write(fd, line):]
        os.fsync(fd)
    finally:
        os.close(fd)  # and its lock with it
    return written


def next_seq(path: Path) -> int:
    """One past the file's last event: 1 for a file that isn't there, or holds none."""
    data = _read_locked(path)
    return 1 if data is None else _last_seq(data) + 1


def episode_for(path: Path, hold: Hold | None, *, boot_id: str) -> int:
    """The brake episode a step belongs to: the standing hold's while a hold of this boot stands; otherwise the last
    episode in the file for this boot plus one (1 for none), so each drill run after a release is an episode of its
    own. The file is read under its lock."""
    if hold is not None and hold.boot_id == boot_id and hold.episode is not None:
        return hold.episode
    data = _read_locked(path)
    events = [] if data is None else _events(data)
    return max((event.episode for event in events if event.boot_id == boot_id), default=0) + 1


def read_events(path: Path, after: tuple[str, int] | None) -> list[BrakeEvent]:
    """The events after `after`, a (boot id, seq) key, in the file's order; every event for None. When no event in the
    file has that key (the file was removed, trimmed, or started again), every event in it is after it. Read without
    the lock: a last line with no newline is a write in progress, never returned, and never read past. A file that
    isn't there holds none; one that can't be read raises OSError, or ValueError when it isn't a regular file."""
    try:
        fd = os.open(path, os.O_RDONLY | _OPEN_FLAGS)
    except FileNotFoundError:
        return []
    try:
        events = _events(_read_all(fd))
    finally:
        os.close(fd)
    if after is None:
        return events
    for place in range(len(events) - 1, -1, -1):
        if (events[place].boot_id, events[place].seq) == tuple(after):
            return events[place + 1:]
    return events
