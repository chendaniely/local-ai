"""The gate's state, kept across its restarts (Phase 2a; plan.md, *The front and the gate*, *Admission and memory rules*
4, 5 and 9): each loaded model's footprint and what its load took, pins, sessions, make-room's hold, the brake's marks,
the last automatic release, apply's hold, the engines each ticket started and the tickets issued, what has been
notified, and the refusal history. A restart must never lose a hold, a ticket in flight, or a model's growth still owed,
so the state is saved whole on every change (Task 18), in `GATE_STATE/state.json`, the file and its folder fsynced
before save_state returns, as the brake's hold is: a reader finds the old file or the new one, never part of one.

At start the gate loads it (load_state, which never raises) and settles it against llama-swap's `/running` and this
boot (restore). A damaged file, or one from a newer version, gives a fresh state that errs safe, and a problem for
`spark status`: no automatic release of the brake's hold for an hour, the stop counted unclean, the front's drains
settled from `/running` at start (Tasks 18 and 22), and the file kept aside for diagnosis (the controller's rulings at
Task 13's review). A field this version doesn't know is kept, and written back as it was.

What belongs to a boot ends with it: make-room's hold, apply's hold, the sessions, the model records, the engines
ticketed, the tickets issued and the drains. Pins, the brake's marks and the refusal history stay. A drain the gate
saved is resumed under its id, or the front is owed an `undrain` under it, never left (the controller's rulings at
Task 13).

Nothing heavy at import (no uvicorn, Starlette or httpx), since the brake reads the last automatic release from it too
(Task 23)."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import secrets
import stat
import time
import types
from collections import deque
from dataclasses import MISSING, dataclass, field, fields
from functools import cache
from pathlib import Path
from typing import Any, Literal, Protocol, Union, get_args, get_origin, get_type_hints, runtime_checkable

from spark import procs
from spark.gateproto import NOTIFIED_KEEP_S, NTFY_LATE_KEEP_S, REFUSAL_HISTORY, DrainWhy
from spark.llamaswap import Running
from spark.registry import Registry

STATE_FILE = "state.json"
STATE_SCHEMA = 1  # raised when a field's meaning changes; a file with a higher one is a newer version's: damaged
DAMAGED_SUFFIX = ".damaged-"  # a damaged state file is kept as state.json.damaged-<UTC time>
DAMAGED_KEEP = 3  # the newest damaged files kept
STATE_MAX_BYTES = 16 << 20  # far more than 50 refusals and a day of notifications: a larger file isn't the gate's
# A loaded model's state in the gate's record. stopping: from the moment its unload call is sent until /running shows
# it gone, its memory not yet freed (gateproto.ModelState's, the controller's rulings at Task 12's re-reviews and at
# Task 13: never before the call is sent, so a model never sits stopping while it serves).
RecordState = Literal["starting", "ready", "draining", "stopping"]
RECORD_STATES: tuple[str, ...] = get_args(RecordState)
_log = logging.getLogger(__name__)
# Each record keeps the fields of its file this version doesn't know (a later version's, after a rollback), and writes
# them back as they were (the controller's ruling at Task 13's review).
def _extra() -> Any:
    # A field of its own for each class, since dataclass fills in its name. Compared, so a record that differs only
    # there isn't equal; not hashed, so a frozen record still hashes (the controller's ruling at Task 13's re-review).
    return field(default_factory=dict, repr=False, hash=False)


@dataclass
class ModelRecord:
    """A model llama-swap runs, as the gate counts it (rule 9)."""

    name: str
    footprint_gib: float  # the registry's when it loaded, never a newer one's
    loaded_at: float
    load_fall_gib: float | None  # MemAvailable's fall across its load, capped (Task 15); None when unknown
    fall_flagged: bool  # the fall was capped: other memory moved during the load
    rss_anon_at_load_gib: float | None  # its engine's RssAnon when it loaded; None: no growth is credited
    last_use: float
    state: RecordState
    # "unload requested": saved before the gate calls unload (the controller's ruling at Task 12's second re-review),
    # so its leaving /running is the gate's own unload, never a crash for the residents' rule. It goes with the record
    # once /running shows the model gone, and the drain clears it when it undrains (Task 16).
    unload_requested_at: float | None = None
    # The drain under way, saved before the drainer sends `drain` (Task 16), so a restarted gate resumes it under its id
    # or sends the front `undrain` under it (the controller's rulings at Task 13 and its review). Each origin saves its
    # own why. None, for an unload with no why of the gate's (restore found the model stopping with no record of it:
    # the brake's unload, or one after damage), reads as unknown.
    drain_id: str | None = None
    drain_why: DrainWhy | None = None
    extra: dict[str, Any] = _extra()


@dataclass(frozen=True)
class Pin:
    model: str
    until: float | None  # None: no end
    by_uid: int
    extra: dict[str, Any] = _extra()


@dataclass
class Session:
    """An agent's session, which keeps its model loaded while it is live (session_live)."""

    id: str
    uid: int
    pid: int
    model: str
    label: str
    started_at: float
    renewed_at: float
    boot_id: str  # the boot it was recorded on: a reboot ends it
    start_time: int | None  # its pid's procs.start_time when it was recorded; None: never live
    extra: dict[str, Any] = _extra()


@dataclass
class RoomHold:
    """make-room's hold (rule 4): ends at `until`, at `spark make-room --done`, when Dan's own loads use it up, or at
    the next boot."""

    size_gib: float
    until: float | None  # None: until --done or the next boot
    boot_id: str
    created_at: float
    unloaded: list[str]  # what make-room unloaded, for the reloads when it ends
    extra: dict[str, Any] = _extra()


@dataclass(frozen=True)
class BrakeMark:
    """The model that was starting when the brake fired (rule 5): it doesn't reload by itself."""

    model: str
    at: float
    # What it was seen using: its load's fall from where it started to the brake's reading as it unloaded it; None
    # without a reading of where it started, never a guess (the controller's ruling, at Task 14).
    seen_gib: float | None
    extra: dict[str, Any] = _extra()


@dataclass(frozen=True)
class RefusalRecord:
    at: float
    model: str | None  # None for a refusal of the front's that named no model, such as route_not_served
    key: str | None  # the asking key's name; None for one of Dan's commands
    uid: int | None
    code: str
    message: str
    extra: dict[str, Any] = _extra()


@dataclass(frozen=True)
class LateAlert:
    """A high alert ntfy didn't take, held to be sent late once it answers again (Task 14), kept with the state so a
    restart or a crash doesn't lose it (the controller's ruling at Task 14's re-review): its words as built, when it
    was accepted, when it first failed, and what is known of that send (messages.LATE_OUTCOMES), which its late words
    say."""

    type: str
    priority: str
    message: str
    at: float
    failed_at: float
    outcome: Literal["unreached", "refused", "unconfirmed"]
    extra: dict[str, Any] = _extra()


@dataclass
class ApplyHold:
    """Apply's hold, saved like the rest, so a gate restarted inside an apply still holds."""

    since: float
    by_uid: int
    renewed_at: float
    begun_at: float | None  # apply/begin's time; None before it
    restarting: list[str]  # the units apply/begin named
    ended: bool  # a second end does nothing
    extra: dict[str, Any] = _extra()


@dataclass(frozen=True)
class Ticketed:
    """An engine a ticket started this boot, for the bypass check and the holders (Task 18)."""

    model: str
    pid: int
    ticket_id: str
    at: float
    # procs.start_time's ticks, so a pid handed out again is never read as the engine (the controller's ruling at
    # Task 8's review); None when it couldn't be read, and then procs.engine_pid trusts the pid not at all.
    start_time: int | None
    extra: dict[str, Any] = _extra()


@dataclass
class GateState:
    """Everything the gate keeps across a restart. GateState() is a fresh one."""

    models: dict[str, ModelRecord] = field(default_factory=dict)
    pins: dict[str, Pin] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)  # by session id
    room_hold: RoomHold | None = None
    brake_marks: dict[str, BrakeMark] = field(default_factory=dict)
    last_auto_release_at: float | None = None
    # last_auto_release_at is assumed, not real: a damaged file's fresh state sets it to its start, so no automatic
    # release comes for an hour, and brake_fired's words never name it as a release (Task 13's re-review).
    release_assumed: bool = False
    brake_events_after: tuple[str, int, float] | None = None  # the (boot id, seq, at) of the last brake event read
    # When each notification was sent, keyed by its type and its event key together (notified_key): the controller's
    # ruling after Task 7's fix round, so two types never share a key. Pruned on every save.
    notified: dict[str, float] = field(default_factory=dict)
    notify_failing_since: float | None = None
    refusals: deque[RefusalRecord] = field(default_factory=deque)  # the newest REFUSAL_HISTORY, oldest first
    applying: ApplyHold | None = None
    ticketed: dict[str, Ticketed] = field(default_factory=dict)  # by model
    # Each model's ticket id for the load the gate issued it for this boot, saved before the load call and dropped
    # once its started/ record has moved into ticketed or the load has failed or gone (Task 15): Task 18's bypass
    # check counts a started/ record as ticketed only when its id is here (the controller's ruling at Task 11's review).
    issued: dict[str, str] = field(default_factory=dict)
    # Each drain the gate owes the front an `undrain` for, model to its saved drain id: restore puts here every saved
    # drain it doesn't resume, and the core sends each and drops it (Task 18), saved meanwhile, so a gate that stops
    # first still owes it.
    undrains: dict[str, str] = field(default_factory=dict)
    # The high alerts ntfy didn't take, oldest first, held to send late (Task 14's Notifier); pruned at
    # NTFY_LATE_KEEP_S on every save, and bounded by the Notifier.
    late_alerts: list[LateAlert] = field(default_factory=list)
    clean_shutdown: bool = True  # a fresh state had no run to end
    saved_at: float = 0.0
    boot_id: str = ""  # the boot it was last settled on (restore)
    # A damaged file's fresh state (load_state): the gate settles the front's drains from /running at start, then
    # clears it (Tasks 18 and 22).
    fresh_after_damage: bool = False
    damaged_at: float | None = None  # when the gate started fresh after damage, until the next boot: `spark status`
    damaged_kept_as: str | None = None  # where the damaged file was kept, for `spark status`
    extra: dict[str, Any] = _extra()  # the parts of the file this version doesn't know

    def __post_init__(self) -> None:
        self.refusals = deque(self.refusals, maxlen=REFUSAL_HISTORY)


@runtime_checkable
class Emit(Protocol):
    """What every gate module notifies through; Task 14's Notifier implements it. `type` is a notification type (the
    registry's), `event_key` the event's own identity (Task 14), and `fields` what its words take (messages)."""

    def emit(self, type: str, event_key: str, /, **fields: Any) -> None: ...


@dataclass(frozen=True)
class SavedDrain:
    """A drain a restarted gate resumes (resumable_drains): under its saved id, or, for an unload requested with no
    drain (Task 15's abort of a start past its deadline), under a new one."""

    model: str
    drain_id: str | None
    why: DrainWhy | None


def resumable_drains(state: GateState) -> list[SavedDrain]:
    """The drains the gate resumes at start, on the state restore returned: each model whose unload it requested. One
    left `draining` is drained again, so the requests in flight finish, then unloaded; one `stopping` waits for
    /running to show it gone (Task 16). Every other drain saved is in `state.undrains`. A model in a state the gate
    doesn't know waits until /running shows one it knows (Task 18). An unload with no why of the gate's (restore found
    the model stopping with no record of it) reads as `unknown`, worded neutrally (the controller's rulings at Task 13's
    review and re-review)."""
    return [SavedDrain(record.name, record.drain_id, record.drain_why or "unknown") for record in state.models.values()
            if record.unload_requested_at is not None and record.state in ("draining", "stopping")]


def session_live(session: Session, *, boot_id: str, proc: Path = procs.PROC) -> bool:
    """Whether `session` still keeps its model: recorded this boot, and its pid still the process that started at
    the time recorded with it (procs.still_running), so a pid the kernel handed out again keeps nothing loaded (the
    controller's ruling at Task 13). A /proc that won't let the gate read the process raises, as procs' readers do."""
    return (session.boot_id == boot_id and session.start_time is not None
            and procs.still_running(session.pid, session.start_time, proc=proc))


def notified_key(type: str, event_key: str) -> str:
    """GateState.notified's key: the type and the event key together, `<type> <event_key>`, so `loaded` is never taken
    for a repeat of `load_started`, which sends `load:<ticket id>` for the same load."""
    return f"{type} {event_key}"


# Reading and writing the file.

_TABLES: tuple[tuple[str, type, str], ...] = (  # each table of records, and the field its keys must match
    ("models", ModelRecord, "name"), ("pins", Pin, "model"), ("sessions", Session, "id"),
    ("brake_marks", BrakeMark, "model"), ("ticketed", Ticketed, "model"))
_POSITIVE = ("pid",)  # a pid of 0 or below names a process group, never a process
_SCALARS: dict[str, Any] = {"last_auto_release_at": float | None, "notify_failing_since": float | None,
                            "clean_shutdown": bool, "saved_at": float, "boot_id": str, "fresh_after_damage": bool,
                            "damaged_at": float | None, "damaged_kept_as": str | None, "release_assumed": bool}
# Every part of the file this version reads; any other is kept as it was (GateState.extra).
_KNOWN = {"schema", "room_hold", "applying", "brake_events_after", "notified", "issued", "undrains", "refusals",
          "late_alerts",
          *(section for section, _, _ in _TABLES), *_SCALARS}


def _finite(value: Any) -> bool:
    """A finite int or float, never a bool; a JSON int too large for a float isn't one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _value(hint: Any, raw: Any, where: str) -> Any:
    """`raw`, checked against the type `hint` (the records' few: text, whole and finite numbers, true or false, a
    Literal, a list, or one of them or None). Each ValueError names the field, never its value."""
    origin = get_origin(hint)
    if origin is Union or origin is types.UnionType:
        if raw is None and type(None) in get_args(hint):
            return None
        (inner,) = [arg for arg in get_args(hint) if arg is not type(None)]
        return _value(inner, raw, where)
    if origin is Literal:
        if isinstance(raw, str) and raw in get_args(hint):
            return raw
        raise ValueError(f"{where} must be one of {', '.join(get_args(hint))}")
    if origin is list:
        if not isinstance(raw, list):
            raise ValueError(f"{where} must be a list")
        return [_value(get_args(hint)[0], item, where) for item in raw]
    if hint is bool and isinstance(raw, bool):
        return raw
    if hint is int and isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if hint is float and _finite(raw):
        return float(raw)
    if hint is str and isinstance(raw, str):
        return raw
    if hint in (bool, int, float, str):
        words = {bool: "true or false", int: "a whole number", float: "a finite number", str: "text"}
        raise ValueError(f"{where} must be {words[hint]}")
    raise TypeError(f"no reader for {hint!r}")  # a record's field of a type this module doesn't read: a bug


@cache
def _hints(cls: type) -> dict[str, Any]:
    return get_type_hints(cls)


def _record(cls: type, raw: Any, section: str) -> Any:
    if not isinstance(raw, dict):
        raise ValueError(f"{section} holds something that isn't an object")
    known = [f for f in fields(cls) if f.name != "extra"]
    given: dict[str, Any] = {"extra": {key: value for key, value in raw.items() if key not in {f.name for f in known}}}
    for f in known:
        where = f"{section}.{f.name}"
        if f.name in raw:
            given[f.name] = _value(_hints(cls)[f.name], raw[f.name], where)
        elif f.default is MISSING and f.default_factory is MISSING:
            raise ValueError(f"{where} is missing")
        if f.name in _POSITIVE and f.name in given and given[f.name] < 1:
            raise ValueError(f"{where} must be 1 or more")
    return cls(**given)


def _table(raw: Any, cls: type, key: str, section: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{section} must be an object")
    table = {name: _record(cls, item, section) for name, item in raw.items()}
    if any(getattr(item, key) != name for name, item in table.items()):
        raise ValueError(f"{section} holds a record under a name that isn't its {key}")
    return table


def _decode(data: Any) -> GateState:
    """The state in `data`, as save_state wrote it. A part that is missing takes a fresh state's; one that is there
    but wrong is a ValueError naming it."""
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    schema = data.get("schema")
    if isinstance(schema, int) and not isinstance(schema, bool) and schema > STATE_SCHEMA:
        raise ValueError(f"written by a newer version of the gate (schema {schema}; this one reads {STATE_SCHEMA})")
    if type(schema) is not int or schema != STATE_SCHEMA:  # 1.0 and true aren't 1
        raise ValueError(f"its schema isn't {STATE_SCHEMA}")
    state = GateState(extra={key: value for key, value in data.items() if key not in _KNOWN})
    for section, cls, key in _TABLES:
        if section in data:
            setattr(state, section, _table(data[section], cls, key, section))
    for name, hint in _SCALARS.items():
        if name in data:
            setattr(state, name, _value(hint, data[name], name))
    for name, cls in (("room_hold", RoomHold), ("applying", ApplyHold)):
        if data.get(name) is not None:
            setattr(state, name, _record(cls, data[name], name))
    after = data.get("brake_events_after")
    if after is not None:
        if not (isinstance(after, list) and len(after) == 3 and isinstance(after[0], str) and after[0]
                and isinstance(after[1], int) and not isinstance(after[1], bool) and after[1] >= 1
                and _finite(after[2])):
            raise ValueError("brake_events_after must be a boot id, a seq and a time")
        state.brake_events_after = (after[0], after[1], float(after[2]))
    notified = data.get("notified", {})
    if not (isinstance(notified, dict) and all(_finite(sent) for sent in notified.values())):
        raise ValueError("notified must map each notification to a finite time")
    state.notified = {name: float(sent) for name, sent in notified.items()}
    for name in ("issued", "undrains"):
        ids = data.get(name, {})
        if not (isinstance(ids, dict) and all(isinstance(value, str) and value for value in ids.values())):
            raise ValueError(f"{name} must map each model to an id")
        setattr(state, name, dict(ids))
    refusals = data.get("refusals", [])
    if not isinstance(refusals, list):
        raise ValueError("refusals must be a list")
    state.refusals = deque((_record(RefusalRecord, item, "refusals") for item in refusals), maxlen=REFUSAL_HISTORY)
    late = data.get("late_alerts", [])
    if not isinstance(late, list):
        raise ValueError("late_alerts must be a list")
    state.late_alerts = [_record(LateAlert, item, "late_alerts") for item in late]
    return state


def _plain(item: Any) -> dict[str, Any]:
    """A record as its file holds it: its fields, and those of its file this version didn't know, as they were."""
    known = {f.name: copy.deepcopy(getattr(item, f.name)) for f in fields(item) if f.name != "extra"}
    return {**copy.deepcopy(item.extra), **known}


def _encode(state: GateState) -> dict[str, Any]:
    data: dict[str, Any] = {"schema": STATE_SCHEMA}
    data |= {section: {name: _plain(item) for name, item in getattr(state, section).items()}
             for section, _, _ in _TABLES}
    data |= {name: getattr(state, name) for name in _SCALARS}
    data |= {"room_hold": None if state.room_hold is None else _plain(state.room_hold),
             "applying": None if state.applying is None else _plain(state.applying),
             "brake_events_after": None if state.brake_events_after is None else list(state.brake_events_after),
             "notified": dict(state.notified), "issued": dict(state.issued), "undrains": dict(state.undrains),
             "refusals": [_plain(item) for item in state.refusals],
             "late_alerts": [_plain(item) for item in state.late_alerts]}
    return copy.deepcopy(state.extra) | data


def _not_a_number(name: str) -> None:
    raise ValueError(f"{name} isn't a number the gate writes")


def _read(path: Path) -> bytes:
    """The file's bytes, read without following a link or waiting on a FIFO, only from a regular file of at most
    STATE_MAX_BYTES."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("not a regular file")
        chunks, size = [], 0
        while chunk := os.read(fd, 1 << 20):
            size += len(chunk)
            if size > STATE_MAX_BYTES:
                raise ValueError(f"larger than {STATE_MAX_BYTES} bytes")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _why(err: BaseException) -> str:
    if isinstance(err, OSError) and err.strerror:
        return err.strerror
    if isinstance(err, RecursionError):
        return "JSON nested too deep"
    return " ".join(str(err).split()) or type(err).__name__


def load_state(folder: Path, *, now: float | None = None, set_aside: bool = False) -> tuple[GateState, str | None]:
    """The state saved in `folder`, and None; a fresh state and None when there is none. Never raises.

    For a file that can't be read, isn't the gate's whole state, or is a newer version's (its schema higher): a fresh
    state that errs safe, and a problem naming the file, for `spark status` (the controller's rulings at Task 13's
    review). Its `last_auto_release_at` is `now` (time.time() by default), assumed (`release_assumed`), so no
    automatic release of the brake's hold comes for an hour; its `clean_shutdown` is false; and `fresh_after_damage`
    asks the gate to settle the front's drains from `/running` at start (Tasks 18 and 22). With `set_aside`, which only
    the gate passes, the damaged file is first moved aside (set_aside_damaged), the state records where, and the state
    is saved at once, whole, so a gate that stops before anything else runs finds the damage again (the controller's
    ruling at Task 13's re-review)."""
    path = Path(folder) / STATE_FILE
    now = time.time() if now is None else now
    try:
        raw = _read(path)
    except FileNotFoundError:
        return GateState(), None
    except Exception as err:  # noqa: BLE001 (it never raises: a gate that can't start keeps nothing either)
        return _damaged(path, err, now=now, set_aside=set_aside)
    try:
        return _decode(json.loads(raw, parse_constant=_not_a_number)), None
    except Exception as err:  # noqa: BLE001
        return _damaged(path, err, now=now, set_aside=set_aside)


def _damaged(path: Path, err: BaseException, *, now: float, set_aside: bool) -> tuple[GateState, str]:
    state = GateState(last_auto_release_at=now, release_assumed=True, clean_shutdown=False, fresh_after_damage=True,
                      damaged_at=now)
    kept = ""
    if set_aside:
        try:
            moved, trouble = set_aside_damaged(path, now=now)
        except OSError as move:
            kept = f"; the damaged file couldn't be kept ({_why(move)}), so the next save replaces it"
        else:
            state.damaged_kept_as = str(moved)
            kept = f"; the damaged file is kept as {moved}" + (f", but {trouble}" if trouble else "")
            try:
                save_state(path.parent, state, now=now)
            except (OSError, ValueError) as save:
                kept += (f"; the fresh state couldn't be saved ({_why(save)}), so a restart before the next save "
                         "starts as a new box would")
    return state, (f"the gate's state {path} can't be read ({_why(err)}), so the gate starts from a fresh state: the "
                   f"pins, sessions, make-room hold and brake marks it kept are gone{kept}")


def set_aside_damaged(path: Path, *, now: float) -> tuple[Path, str | None]:
    """Move the damaged state file at `path` aside, as `<path>.damaged-<UTC time>` (with -2, -3 … should that name be
    taken), mode 0600 when it is a regular file, never changed through a link; then remove all but the newest
    DAMAGED_KEEP of them, by when each was last written, never the one just moved, so a clock set back can't remove it.
    Returns where it went, and what couldn't be done once it had moved (its mode), or None: the move is what counts
    (the controller's rulings at Task 13's re-review). Root is refused (PermissionError): the gate's folder is
    spark's."""
    if os.geteuid() == 0:
        raise PermissionError(f"root never writes in the gate's folder, so {path} isn't moved")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    kept, n = path.with_name(f"{path.name}{DAMAGED_SUFFIX}{stamp}"), 2
    while os.path.lexists(kept):
        kept, n = path.with_name(f"{path.name}{DAMAGED_SUFFIX}{stamp}-{n}"), n + 1
    os.rename(path, kept)
    trouble = None
    try:
        fd = os.open(kept, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        fd = None  # a link, or something it can't open: left as it is
    if fd is not None:
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode):
                os.fchmod(fd, 0o600)
        except OSError as err:
            trouble = f"its mode couldn't be set ({_why(err)})"
        finally:
            os.close(fd)
    for old in _older_damaged(path, kept)[DAMAGED_KEEP - 1:]:
        try:
            old.unlink()
        except OSError:
            try:
                old.rmdir()  # a folder someone left under that name: removed only when empty
            except OSError:
                pass
    return kept, trouble


def _older_damaged(path: Path, kept: Path) -> list[Path]:
    """The damaged files kept beside `path` but `kept`, the last written first; none when the folder can't be read."""
    def written(old: Path) -> float:
        try:
            return old.lstat().st_mtime
        except OSError:
            return float("-inf")  # gone meanwhile: nothing to keep

    try:
        names = os.listdir(path.parent)
    except OSError:
        return []
    older = [path.parent / name for name in names
             if name.startswith(f"{path.name}{DAMAGED_SUFFIX}") and name != kept.name]
    return sorted(older, key=written, reverse=True)


def save_state(folder: Path, state: GateState, *, now: float | None = None) -> None:
    """Write `state` whole as `folder`/STATE_FILE, mode 0600: to a new file of this write's own, fsynced, then swapped
    in, and the folder fsynced, so the file is on disk before it returns and a write that fails leaves the previous one
    whole. First it drops from `state.notified`, in place, what was sent more than NOTIFIED_KEEP_S before `now`, so
    the record never grows for the life of the box, and from `state.late_alerts` what is NTFY_LATE_KEEP_S old, no
    longer news, and sets `state.saved_at` to `now` (the gate's clock; time.time() by default). Root is refused
    (PermissionError) before anything is written: the gate's folder is spark's."""
    folder = Path(folder)
    path = folder / STATE_FILE
    if os.geteuid() == 0:
        raise PermissionError(f"root never writes in the gate's folder, so {path} isn't written")
    now = time.time() if now is None else now
    for name in [name for name, sent in state.notified.items() if now - sent > NOTIFIED_KEEP_S]:
        del state.notified[name]
    state.late_alerts = [alert for alert in state.late_alerts if now - alert.at < NTFY_LATE_KEEP_S]
    state.saved_at = now
    data = json.dumps(_encode(state), allow_nan=False).encode()
    folder.mkdir(mode=0o750, exist_ok=True)
    tmp = folder / f".{STATE_FILE}.{os.getpid()}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        try:
            while data:
                data = data[os.write(fd, data):]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    folder_fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(folder_fd)  # the rename itself
    finally:
        os.close(folder_fd)


# Settling the state at start.

def _footprint(registry: Registry, name: str) -> float:
    """The registry's footprint for `name`. A model llama-swap runs that the registry doesn't know counts at the
    registry's largest, so its memory is counted rather than left out."""
    model = registry.models.get(name)
    if model is not None:
        return float(model.footprint_gib)
    return float(max((m.footprint_gib for m in registry.models.values()), default=0.0))


def restore(state: GateState, running: list[Running], *, now: float, boot_id: str,
            registry: Registry) -> tuple[GateState, RoomHold | None]:
    """`state`, as loaded, settled against `/running` at the gate's start and this boot; `state` itself is left as it
    was. Returns the settled state, and the room hold it ended, if any, so the caller sends room_hold_ended.

    - Each model `/running` lists keeps its record, footprint included, or gets one at the registry's footprint (with
      no fall and no RssAnon, so *owed* errs safe), as does every model on another boot's state, since no engine
      outlives a boot (the controller's ruling at Task 13's review). Its `last_use` is `now`, so a restart can't cause
      an idle unload.
    - One `/running` shows stopping is `stopping`: its memory not freed until `/running` shows it gone, and its
      leaving the gate's own unload, never a crash (the controller's rulings at Task 12's re-reviews). One found
      stopping with no request recorded gets one, at `now`.
    - One in a state other than starting, ready or stopping is `starting` at its footprint, never served, until
      `/running` shows it ready or gone, and is logged (the controller's ruling at Task 13's review). If its unload was
      requested, it isn't resumed until `/running` shows a state the gate knows (Task 13's re-review; Task 18).
    - One whose unload was requested and that `/running` still shows ready (or starting) is `draining`, left for the
      drainer: the call may never have gone out, so the drainer drains it again, so the requests in flight finish,
      then unloads it; it counts as `stopping` only once that call is sent, never while it serves. Its leaving is
      still the gate's own (the controller's ruling at Task 13).
    - One `/running` shows starting is `starting`, and so is a load not yet settled (recorded starting, or its ticket
      in `issued` and not yet in `ticketed`) that finished while the gate was away: each takes Task 15's ready-or-gone
      path, so its started/ record moves into `ticketed` once it is ready.
    - Any other is `ready`.
    - A model `/running` doesn't list is dropped, with its ticketed engine and its ticket id in `issued`.
    - Every drain saved is resumed (resumable_drains: each model whose unload was requested and that `/running` still
      lists) or owed an `undrain` under its id (`state.undrains`): one whose unload wasn't requested, and one whose
      model is gone. The front holds a drained model until the gate says `unloaded` or `undrain` (Task 12's R-3), so
      no drain is left (the controller's ruling at Task 13).
    - A room hold from another boot ends. If the state is another boot's, so do apply's hold, the sessions, `ticketed`,
      `issued`, the drains and the undrains owed: none of their processes outlives the boot, and the front starts
      afresh. So does a damage noted in an earlier boot, but not one noted at this start (`fresh_after_damage`). Pins,
      the brake's marks, the refusals and the rest stay."""
    state = copy.deepcopy(state)
    ended = None
    if state.room_hold is not None and state.room_hold.boot_id != boot_id:
        ended, state.room_hold = state.room_hold, None
    other_boot = state.boot_id != boot_id
    if other_boot:
        state.applying, state.sessions, state.ticketed, state.issued, state.undrains = None, {}, {}, {}, {}
        if not state.fresh_after_damage:  # a damage noted in an earlier boot is no longer news
            state.damaged_at = state.damaged_kept_as = None
    listed = {entry.model: entry.state for entry in running}
    models: dict[str, ModelRecord] = {}
    for name, shown in listed.items():
        old = None if other_boot else state.models.get(name)  # no engine outlives a boot
        record = old or ModelRecord(name=name, footprint_gib=_footprint(registry, name), loaded_at=now,
                                    load_fall_gib=None, fall_flagged=False, rss_anon_at_load_gib=None, last_use=now,
                                    state="starting")
        ticket = state.issued.get(name)
        unsettled = ticket is not None and (name not in state.ticketed or state.ticketed[name].ticket_id != ticket)
        if shown == "stopping":
            record.state = "stopping"
            if record.unload_requested_at is None:
                record.unload_requested_at = now
        elif shown not in ("starting", "ready"):
            # Counted, never served; an unload requested waits for a state the gate knows (resumable_drains, Task 18).
            _log.warning("llama-swap's /running shows %r as %r, a state the gate doesn't know: counted as starting, "
                         "at %s GiB, until it shows it ready or gone", name, shown, record.footprint_gib)
            record.state = "starting"
        elif record.unload_requested_at is not None:
            record.state = "draining"  # left for the drainer, which drains it, then unloads it
        elif shown == "starting" or (old is not None and old.state == "starting") or unsettled:
            record.state = "starting"
        else:
            record.state = "ready"
        if record.unload_requested_at is None and record.drain_id is not None:
            state.undrains[name] = record.drain_id  # a drain not resumed: the front is told to serve it again
            record.drain_id = record.drain_why = None
        record.last_use = now
        models[name] = record
    for name, gone in state.models.items():
        if name not in listed and gone.drain_id is not None and not other_boot:
            state.undrains[name] = gone.drain_id
    state.models = models
    state.ticketed = {name: engine for name, engine in state.ticketed.items() if name in listed}
    # A model gone from /running: its load has gone, so its ticket id goes (the controller's ruling at Task 13's
    # review), and a later load around the gate is never taken for the one it was issued for.
    state.issued = {name: ticket for name, ticket in state.issued.items() if name in listed}
    state.boot_id = boot_id
    return state, ended
