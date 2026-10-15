"""The memory brake: warn, then hold new loads and unload models before the freeze band.

Phase 1's minimal brake, and from Phase 2a (website/design/phase-2a.md, Task 23; plan.md, rule 5) a unit of its own
still, a synchronous loop that never shares an event loop with the gate and never waits on it:

- **Its order** comes from the gate's activity record, `GATE_STATE/activity.json`, written every second: a loading
  engine first, then the models with nothing in flight, of any class, the longest unused first, then the rest, least
  recently used first. With the record missing, damaged or stale, Phase 1's order: on-demand first, then the largest.
- **The rate-of-fall watch** acts early: when the fall over the last FALL_WINDOW_S, carried UNLOAD_LEAD_S ahead, would
  pass the brake line, the brake holds and unloads as at the line. A fall the gate knowingly admitted is not a crash:
  the loads in progress (the gate's record's `starting` models, and launch's `started/` records, bounded, since an
  engine can write those) give a floor that much fall may reach, which stands for the window after they end; a fall
  past it, or one nothing explains, counts, once memory has fallen MIN_FALL_GIB within the window.
- **Each step is recorded for the gate** in `brake/events.jsonl` (brakeevents): fired, each unload, and each unload
  llama-swap refused, with the line it acted on and its episode, fsynced, as the hold is. In a poll, the hold is
  written first, then the unload is sent, and only then are the steps written: no lock another process can hold comes
  before either, and a poll's tries for it wait LOCK_WAIT_S at most in all (Task 23's review, and its second
  re-review). A step held up waits, in order, for a poll that can write it.
- **While the gate is down** (its record missing or stale) it sends its own `brake_fired`, and each follow-up, to ntfy
  through Ubuntu's curl, never waited on, its address, topic and text on curl's stdin, never a command line. Until ntfy
  is set up (Task 2, Phase 5) it sends nothing, says so in its journal once an episode, and the gate, once back, tries.
- **Its hold** carries the boot, the episode, the model that was loading and the reading, and its `since` its UTC
  offset; `make brake-release` goes through the gate, and lifts the hold file itself only when the gate isn't there or
  gives no whole answer within 5 s, saying which.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from spark import messages, paths, procs, render, tickets
from spark.brakeevents import EVENTS_FILE, BrakeEvent, EventsFileNotRegular, append_event, episode_for
from spark.credentials import CredentialError, read_credential, read_header_credential
from spark.gate.state import load_state
from spark.gateclient import GateClient, GateError, GateForbidden, GateUnavailable
from spark.gateproto import ACTIVITY_STALE_S, RELEASE_AFTER_S
from spark.hold import UNREADABLE_SINCE, Hold, read_hold, release_hold, release_waits_for_dan, write_hold
from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, _url_ok, key_from_env
from spark.memory import MemInfo, read_meminfo
from spark.memory import boot_id as read_boot_id
from spark.registry import BrakeThresholds, Registry, load_registry
from spark.render import write_atomic

# How long memory from an unload counts as on its way back before the brake stops waiting for it and tries the
# next model. llama-swap v257 gives an engine its unloadTimeout (10 s by default; the stack's config leaves it) to
# exit on SIGTERM before it SIGKILLs it, so a slow but normal stop takes up to 10 s. The 5 s beyond allow for the
# kill and for the memory to show in MemAvailable, neither yet measured on this box. The wait costs no safety: once
# memory falls more than FLOOR_TOLERANCE_GIB below where it was, it ends. Measured on brightroar, 2026-09-28 (Phase 1
# drills): an idle coder's process was gone 0.6 s after the brake's tick began. Decided at Phase 1's close
# (2026-09-28, its council): 15 s stays. The measured stop never reaches it, since leaving /running ends the wait;
# its ceiling is v257's 10 s unloadTimeout and the kill; and it costs nothing while memory is flat. A busy engine's
# stop is still unmeasured: Phase 2's drill times it.
GRACE_S = 15.0

# How far MemAvailable may dip below where it was when an unload began before the brake stops waiting for that
# unload's memory: noise is not a fall. Measured on brightroar, 2026-09-28 (Phase 1 drills), polling every 250 ms
# while the brake held, with 93 GiB available: for the first 15 s, with nothing generating, MemAvailable moved within
# ±0.02 GiB; then, while a Gemma session generated, it fell 4.1 GiB over 45 s in steps up to 0.58 GiB, and 1 poll in
# 239 moved more than 0.5 GiB. That step matches one of Gemma's context checkpoints by arithmetic (~0.59 GiB), so it
# was probably a real allocation; not verified. Decided at Phase 1's close (2026-09-28, its council): 1.0 GiB, up from
# 0.5. At 0.5, one Gemma checkpoint allocated during a slow stop ends the wait, and the brake unloads a second model,
# a resident, though nothing says the first unload failed. 1.0 clears one checkpoint and is still only 1/8 of the 8
# GiB between the brake line (20) and earlyoom's (12). The cost: the brake waits through a real fall of up to 1 GiB.
# Not yet measured: how long after an engine leaves /running its memory shows in MemAvailable. settle() drops the
# credit the moment it leaves, so if that lags, the brake unloads a second model. Phase 2's drill measures it.
FLOOR_TOLERANCE_GIB = 1.0

# An engine's state only moves on: starting, ready, stopping. A model listed at an earlier state than the brake last
# saw it in is a new engine.
STAGES = {"starting": 0, "ready": 1, "stopping": 2}

# The plan's starting thresholds (Admission and memory rules, 5), for when the registry won't load.
FALLBACK = BrakeThresholds(warn_gib=28, brake_gib=20, poll_ms=250)

# The start check (Phase 1's council, 2026-09-28). The brake asks llama-swap nothing while memory is above the warn
# line, so a key llama-swap refuses would first show in an emergency, as an unload that fails. So at start it asks
# once, with its key, what runs, logs the answer, and records it here, in its state folder (2770 spark:spark-admin),
# where `spark status` and `spark doctor` read it.
KEY_CHECK_FILE = "key-check.json"
# How long the check waits for llama-swap to answer at all: at boot the brake starts as soon as llama-swap's binary
# runs (both units are Type=exec), before llama-swap listens. Any answer, an error included, ends the wait.
KEY_CHECK_S = 30.0
# The pause between asks while nothing answers. A refused connection comes back at once; a llama-swap that takes the
# connection and never answers holds each ask for the client's timeout, 2 s, off the poll path.
KEY_CHECK_RETRY_S = 1.0

# The rate-of-fall watch (Task 23), the plan's starting values: Phase 1's loads fell 2.0 to 2.6 GiB/s, and v257 gives an
# engine 10 s to stop. Task 47 sets both from measurements; the drills' hog and the ceiling script pace themselves under
# them (Tasks 40 and 41), and their tests import these.
FALL_WINDOW_S = 2.0  # the fall is read over this long: a least-squares slope of MemAvailable against time
UNLOAD_LEAD_S = 10.0  # and carried this far ahead
LOAD_FLOOR_MARGIN_GIB = 2.0  # below where the loads in progress should leave memory, before a fall is past them
# The watch acts only when memory has actually fallen this much within its window, the window's highest reading less the
# latest (the controller's ruling at Task 23's review, I2). Near the 24 GiB reserve, where memory sits after an admitted
# load, noise of ±0.5 GiB, a 1 GiB step, the drills' 0.1 GiB/s hog and a context checkpoint of ~0.6 GiB, each carried 10
# s ahead, would pass the line; none falls 1.5 GiB in 2 s, and a runaway faster than ~0.75 GiB/s does. Task 47 sets it
# from the measured noise and the soak's growth bursts.
MIN_FALL_GIB = 1.5
# A poll's tries for the events file's lock, which another process can hold, wait this long at most in all, the waits
# that succeed counted too (Task 23's review, I1; the controller's ruling on its second re-review). The steps are
# written only after a poll's hold and unload, so neither waits on it; and once a try has found the lock held, the
# polls after it try without waiting until one takes it, so a lock held for long costs one wait.
LOCK_WAIT_S = 0.2
# A hold the brake couldn't write, with none standing, is tried again at most this often while its episode stands in
# the brake's memory (the controller's ruling on Task 23's second re-review).
HOLD_RETRY_S = 1.0
# At most this many steps for the gate wait, in order, for the events file's lock: past it the oldest go, and the
# journal says how many (the controller's ruling on Task 23's fix round 1).
PENDING_MAX = 64
ACTIVITY_FILE = "activity.json"  # the gate's record, in its state folder (gate/core.py's ACTIVITY_FILE)
# A started/ record's process must have been forked when the record says its load started. Launch writes its own start
# time (the controller's ruling, at Task 23): the engine's, exactly, since it is exec'd in place, and the load's age is
# then read by the boot's clock, /proc/uptime less that start time, at most STARTED_EXPIRES_S and LAUNCH_SLACK_S (the
# moments launch runs before its exec), never by the wall clock. A record from before it has only `started_at`: the
# process may then be up to LAUNCH_SLACK_S older than the record, and no more than AGE_TOLERANCE_S younger (the clocks'
# grain), the stricter way, since a clock stepped since the record can reject it. Either way, an engine that has run
# an hour isn't a load just begun, whatever a record says.
LAUNCH_SLACK_S = 30.0
AGE_TOLERANCE_S = 2.0
# The brake's own readings, kept as long as a started/ record lasts, so a record's start is read from them, never from
# the record (the controller's ruling at Task 11's review).
HISTORY_S = tickets.STARTED_EXPIRES_S + 10
HISTORY_MAX = 4 * HISTORY_S + 100  # four a second, and a bound should the wall clock go back
# The brake's own alert while the gate is down: Ubuntu's curl, outside the app's venv, its config on stdin.
CURL = "/usr/bin/curl"
# -q first, so curl reads no .curlrc: the brake runs as spark, the engines' account, which could plant one (Task 23's
# review, M10). curl runs with an empty environment: it needs nothing of the brake's.
CURL_ARGS = (CURL, "-q", "--fail", "--silent", "--max-time", "10", "--config", "-")
# curl's exit codes, in the journal's words (Task 23's review, M6); any other is said by its number.
CURL_FAILURES = {22: "ntfy answered with an error", 28: "ntfy didn't answer within 10 s", 6: "it couldn't reach ntfy",
                 7: "it couldn't reach ntfy"}
ALERT_WAIT_S = 15.0  # a --once run waits this long at most for its alerts: curl's --max-time 10, and its exit
NTFY_TOKEN = "ntfy-token"  # the credential: one `Authorization: Bearer …` line, which curl reads with `header = @file`
NTFY_TOPIC = re.compile(r"[-_A-Za-z0-9]{1,64}")  # ntfy's own rule for a topic's name
REASON_MAX = 200  # a failed unload's reason, in its event: one line, this long at most
UNKNOWN_BOOT = "unknown-boot"  # the boot id when /proc won't give it: the gate takes it for another boot's
_UNREAD = object()  # the activity record not yet read this poll


@dataclass(frozen=True)
class KeyCheck:
    at: str
    ok: bool | None  # None while the brake still waits for llama-swap to answer
    detail: str
    key_env: str  # where the key came from: the variable's name, or `credential <name>` (Task 23)


def parse_key_check(text: str) -> KeyCheck:
    """A start check as the brake records it; ValueError for anything else."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a start check: expected a JSON object")
    at, ok, detail, key_env = (data.get(name) for name in ("at", "ok", "detail", "key_env"))
    if not (isinstance(at, str) and (ok is None or isinstance(ok, bool)) and isinstance(detail, str)
            and isinstance(key_env, str)):
        raise ValueError("not a start check: at, detail and key_env must be strings, ok true, false or null")
    return KeyCheck(at, ok, detail, key_env)


def read_key_check(state_dir: Path) -> tuple[KeyCheck | None, str | None]:
    """The brake's last start check, or None when it recorded none; and, for a record that can't be read, why."""
    path = Path(state_dir) / KEY_CHECK_FILE
    try:
        return parse_key_check(path.read_text()), None
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, RecursionError) as err:  # RecursionError: JSON nested too deep
        return None, f"the brake's start check {path} can't be read: {err}"


def write_key_check(state_dir: Path, check: KeyCheck) -> None:
    """Whole (write_atomic). The unit's umask leaves a new record readable to spark-admin, the folder's group. The
    folder is bootstrap's: a missing one is an error, never made here, where it would get neither its group nor its
    mode."""
    if not Path(state_dir).is_dir():
        raise FileNotFoundError(f"{state_dir} doesn't exist or isn't a folder")
    write_atomic(Path(state_dir) / KEY_CHECK_FILE, json.dumps(asdict(check)))


@dataclass(frozen=True)
class Action:
    kind: str
    model: str | None = None


def _phase_1_key(registry, name: str) -> tuple[bool, float]:
    """Phase 1's order: on-demand first, then the largest. A model the registry doesn't know goes as /running lists
    it."""
    model = registry.models.get(name)
    return (model.resident if model else False, -(model.footprint_gib if model else 0.0))


def plan_brake(mem: MemInfo, thresholds: BrakeThresholds, registry: Registry, running: list[str],
               inflight_gib: float = 0.0, *, projected_gib: float | None = None, ordered: bool = False) -> list[Action]:
    """`running` holds the models the brake may unload, in Phase 1's order unless `ordered` says it is already in
    brake_order's. `inflight_gib` is memory on its way back from unloads that haven't shown in MemAvailable yet: it
    counts against the shortfall, so one shortfall doesn't unload two models. `projected_gib` is the rate-of-fall
    watch's reading UNLOAD_LEAD_S ahead when it would pass the line: the brake then holds and unloads as at the line,
    the projection standing in for the reading (Task 23)."""
    breach = projected_gib is not None and projected_gib < thresholds.brake_gib
    if mem.available_gib >= thresholds.warn_gib and not breach:
        return []
    if mem.available_gib >= thresholds.brake_gib and not breach:
        return [Action("warn")]
    victims = list(running) if ordered else sorted(running, key=lambda name: _phase_1_key(registry, name))
    level = min(mem.available_gib, projected_gib) if breach else mem.available_gib
    short = level + inflight_gib < thresholds.brake_gib
    return [Action("hold")] + ([Action("unload", victims[0])] if victims and short else [])


def _boottime() -> float:
    """CLOCK_BOOTTIME, the clock /proc/uptime and a process's start time count by (Linux); the monotonic clock where
    there is none, as on the Mac, which has no /proc to compare it with."""
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    return time.clock_gettime(clock) if clock is not None else time.monotonic()


def _now() -> str:
    """The hold's `since`: local time to the second, with its UTC offset (the controller's ruling at Task 17's review),
    so the gate reads when the brake fired unambiguously across a clock going back."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class _NoRegistry:
    """The brake's registry when the real one won't load: the plan's thresholds and no model table, so models go
    in the order /running lists them."""

    brake: BrakeThresholds = FALLBACK
    models: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------------------------
# The gate's activity record, and the loads in progress (Task 23)

_RECORD_STATES = ("starting", "ready", "draining", "stopping")  # the gate's state.RecordState


@dataclass(frozen=True)
class ModelActivity:
    """A model in the gate's activity record: its state, its requests in flight by the front's counts, its last use;
    and for a model `starting`, from its ticket, the footprint admitted, when it started, and MemAvailable then."""

    state: str
    inflight: int
    last_use: float
    admitted_gib: float | None = None
    started_at: float | None = None
    available_at_start: float | None = None


@dataclass(frozen=True)
class Activity:
    written_at: float
    boot_id: str
    models: dict[str, ModelActivity]


def _number(value: Any) -> bool:
    return tickets._is_number(value)


def _model_activity(raw: Any) -> ModelActivity:
    if not isinstance(raw, dict):
        raise ValueError("a model's entry isn't an object")
    state, inflight, last = raw.get("state"), raw.get("inflight"), raw.get("last_use")
    if (state not in _RECORD_STATES or not (isinstance(inflight, int) and not isinstance(inflight, bool))
            or inflight < 0 or not _number(last)):
        raise ValueError("a model's state, inflight or last_use isn't the gate's")
    if state != "starting":
        return ModelActivity(state, inflight, float(last))
    admitted, started, start = raw.get("admitted_gib"), raw.get("started_at"), raw.get("available_at_start")
    if not (_number(admitted) and _number(started) and (start is None or _number(start))):
        raise ValueError("a starting model's admitted_gib, started_at or available_at_start isn't a number")
    return ModelActivity(state, inflight, float(last), float(admitted), float(started),
                         None if start is None else float(start))


def read_activity(path: Path, now: float, *, boot_id: str | None = None) -> Activity | None:
    """The gate's activity record (`GATE_STATE/activity.json`); None when it is missing, damaged, another boot's, or
    more than ACTIVITY_STALE_S from `now` either way (written before the clock went back). It is read as launch's
    records are: never through a link or a FIFO, only a regular file of spark's of bounded size."""
    try:
        data = tickets.read_record(Path(path), own=True)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    written, boot, models = data.get("written_at"), data.get("boot_id"), data.get("models")
    if not (_number(written) and isinstance(boot, str) and boot and isinstance(models, dict)):
        return None
    try:
        parsed = {str(name): _model_activity(raw) for name, raw in models.items()}
    except ValueError:
        return None
    if abs(now - written) > ACTIVITY_STALE_S or (boot_id is not None and boot != boot_id):
        return None
    return Activity(float(written), boot, parsed)


def brake_order(running: list[tuple[str, str]], activity: Activity | None, registry) -> list[str]:
    """The models the brake may unload, (name, /running's state), in the order it unloads them. With the gate's record:
    a loading engine first (`starting` in the record or in /running), then the models with nothing in flight, of any
    class, the longest unused first, then the rest, least recently used first; a model the record doesn't hold counts
    as idle and unused longest (a load the gate never saw). Without it: Phase 1's order, on-demand first, then the
    largest."""
    names = [name for name, _ in running]
    if activity is None:
        return sorted(names, key=lambda name: _phase_1_key(registry, name))
    states = dict(running)

    def key(name: str) -> tuple:
        seen = activity.models.get(name)
        loading = states.get(name) == "starting" or (seen is not None and seen.state == "starting")
        inflight = seen.inflight if seen is not None else 0
        group = 0 if loading else 1 if inflight == 0 else 2
        return (group, seen.last_use if seen is not None else float("-inf"), *_phase_1_key(registry, name))

    return sorted(names, key=key)


@dataclass(frozen=True)
class LoadInProgress:
    model: str
    admitted_gib: float  # the footprint admission admitted, never the cold load; at most the registry's
    available_at_start: float  # MemAvailable at its start
    started_at: float  # wall clock


def engine_ports(registry) -> dict[str, int]:
    """Each model's engine port, as llama-swap v257 hands them out: from startPort, render's ENGINE_START_PORT, one each
    in the models' sorted names. The gate's core.engine_ports, kept here so the brake never imports the gate's core
    (and with it httpx and uvicorn); both read render's one constant."""
    return {name: render.ENGINE_START_PORT + place for place, name in enumerate(sorted(registry.models))}


def _nearest(readings: Iterable[tuple[float, float]], when: float) -> float | None:
    """The reading taken nearest `when`, of (time, GiB) pairs; None with none."""
    best: tuple[float, float] | None = None
    for at, gib in readings:
        if best is None or abs(at - when) < best[0]:
            best = (abs(at - when), gib)
    return None if best is None else best[1]


def _uptime_s(proc: Path) -> float | None:
    try:
        return float((proc / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _is_its_engine(record: dict, port: int, *, now: float, proc: Path, spark_uid: int) -> bool:
    """Whether a started/ record's pid is its model's engine, read as procs.engine_pid reads one: the process on the
    model's engine port, run as spark, with an engine's comm, and its start time launch's own, exactly, where the record
    holds it (the controller's ruling, at Task 23); and one forked when its load began, by the boot's clock, so a record
    naming a long-running engine isn't a load just begun. A record from before launch wrote its start time is judged by
    its age against the process's instead, the stricter way. A /proc that can't be read is no engine: no load in
    progress, the stricter way."""
    try:
        pid = procs.engine_pid(port, spark_uid=spark_uid, proc=proc)
        if pid is None or pid != record["pid"]:
            return False
        started = procs.start_time(pid, proc=proc)
    except OSError:
        return False
    uptime = _uptime_s(proc)
    if started is None or uptime is None:
        return False
    process_age = uptime - started / os.sysconf("SC_CLK_TCK")
    if "start_time" in record:  # tickets.started has checked it is whole ticks
        return started == record["start_time"] and 0 <= process_age <= tickets.STARTED_EXPIRES_S + LAUNCH_SLACK_S
    record_age = now - record["started_at"]
    return record_age - AGE_TOLERANCE_S <= process_age <= record_age + LAUNCH_SLACK_S


def loads_in_progress(activity: Activity | None, launch: Path | None, *, now: float, boot_id: str, registry,
                      readings: Iterable[tuple[float, float]] = (), boot_readings: Iterable[tuple[float, float]] = (),
                      proc: Path = procs.PROC, spark_uid: int | None = None) -> list[LoadInProgress]:
    """The loads in progress, oldest first: the union, by model, of the gate's record's `starting` models (when the
    record is fresh, `activity`) and the records under `launch/started/` that tickets.started returns (this boot's,
    under STARTED_EXPIRES_S old), whether or not the gate's record is fresh. The gate's record is the gate's own; an
    engine runs as spark and can write started/, so what a record gives is bounded (the controller's ruling at Task
    11's review): its start is the brake's own reading nearest its `started_at` (`readings`, (wall time, GiB) pairs),
    never its `available_gib`; its footprint at most the registry's; and it counts only while its pid is its model's
    engine (_is_its_engine). For a record with launch's start time, its start reading is the brake's own nearest the
    engine's start on the boot's clock (`boot_readings`, (CLOCK_BOOTTIME, GiB) pairs; the start time in ticks over
    CLK_TCK), which a stepped wall clock doesn't move (Task 23's review, M4); a record from before it keeps the wall
    clock's nearest `started_at`. started/ unreadable (tickets.started's OSError) is no record, no load in progress, the
    stricter way; the brake's watch says so in its journal."""
    readings, boot_readings = list(readings), list(boot_readings)
    loads: dict[str, LoadInProgress] = {}
    for name, seen in (activity.models.items() if activity is not None else ()):
        if seen.state != "starting" or seen.admitted_gib is None or seen.started_at is None:
            continue
        model = registry.models.get(name)
        admitted = max(0.0, seen.admitted_gib if model is None else min(seen.admitted_gib, model.footprint_gib))
        start = seen.available_at_start if seen.available_at_start is not None else _nearest(readings, seen.started_at)
        if start is not None:
            loads[name] = LoadInProgress(name, admitted, start, seen.started_at)
    if launch is not None:
        try:
            records = tickets.started(launch, now=now, boot_id=boot_id)
        except OSError:
            records = []
        uid = os.geteuid() if spark_uid is None else spark_uid
        ports = engine_ports(registry)
        for record in records:
            name = record["model"]
            model = registry.models.get(name)
            if name in loads or model is None:
                continue
            if not _is_its_engine(record, ports[name], now=now, proc=proc, spark_uid=uid):
                continue
            if "start_time" in record and boot_readings:
                start = _nearest(boot_readings, record["start_time"] / os.sysconf("SC_CLK_TCK"))
            else:
                start = _nearest(readings, record["started_at"])
            if start is not None:
                admitted = max(0.0, min(float(record["footprint_gib"]), model.footprint_gib))
                loads[name] = LoadInProgress(name, admitted, start, float(record["started_at"]))
    return sorted(loads.values(), key=lambda load: load.started_at)


def expected_floor(loads: list[LoadInProgress]) -> float | None:
    """Where the loads in progress should leave memory: the earliest start's reading, less every load's admitted
    footprint, less LOAD_FLOOR_MARGIN_GIB; None with nothing loading."""
    if not loads:
        return None
    earliest = min(loads, key=lambda load: load.started_at)
    return earliest.available_at_start - sum(load.admitted_gib for load in loads) - LOAD_FLOOR_MARGIN_GIB


def projected_gib(readings: Iterable[tuple[float, float]], *, lead_s: float, window_s: float,
                  floor_gib: float | None, min_fall_gib: float = MIN_FALL_GIB) -> float | None:
    """The reading `lead_s` ahead, from the fall over the last `window_s` of (time, GiB) readings: a least-squares slope
    of MemAvailable against time, carried on from the last reading. None for a rise or a flat line, for a fall of less
    than `min_fall_gib` within the window (its highest reading less the last), or with too little to judge: fewer than
    two readings, or ones spanning less than half the window. With a floor, while the last reading is still above it,
    the projection stops at the floor, since that much fall was admitted; past it, the fall is unexplained, and goes
    on as it is."""
    points = list(readings)
    if not points:
        return None
    last_at, last = points[-1]
    points = [(at, gib) for at, gib in points if 0 <= last_at - at <= window_s]
    if len(points) < 2 or last_at - points[0][0] < window_s / 2:
        return None
    if max(gib for _, gib in points) - last < min_fall_gib:  # noise, a small step, a model's growth (I2)
        return None
    mean_at = sum(at for at, _ in points) / len(points)
    mean_gib = sum(gib for _, gib in points) / len(points)
    spread = sum((at - mean_at) ** 2 for at, _ in points)
    if spread <= 0:
        return None
    slope = sum((at - mean_at) * (gib - mean_gib) for at, gib in points) / spread
    if slope >= 0:
        return None
    ahead = last + slope * lead_s
    if floor_gib is not None and last > floor_gib:
        ahead = max(ahead, floor_gib)
    return ahead


def predict_breach(readings: deque[tuple[float, float]], *, brake_gib: float, lead_s: float, window_s: float,
                   floor_gib: float | None, min_fall_gib: float = MIN_FALL_GIB) -> bool:
    """Whether the fall over the last `window_s`, carried `lead_s` ahead, passes under `brake_gib` (projected_gib). A
    rise, a flat line, or a fall of less than `min_fall_gib` within the window is false. Since admission leaves the
    reserve (24) and the growth owed free, an admitted load's floor is above the brake line (20), so an ordinary load
    never trips it; a load that runs past its floor does."""
    ahead = projected_gib(readings, lead_s=lead_s, window_s=window_s, floor_gib=floor_gib, min_fall_gib=min_fall_gib)
    return ahead is not None and ahead < brake_gib


# ---------------------------------------------------------------------------------------------------------------------
# The brake's own alert while the gate is down (Task 23)

def _curl_quoted(value: str) -> str:
    """`value` in curl's config quoting: within double quotes, a backslash and a quote each escaped with a backslash,
    and a line break, tab, carriage return or vertical tab as curl's own escape, so nothing ends the line early."""
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
               .replace("\r", "\\r").replace("\v", "\\v"))
    return f'"{escaped}"'


def curl_config(url: str, header_file: Path, priority: str, text: str) -> bytes:
    """curl's --config for one ntfy alert: the URL (ntfy's address and the brake's topic), the token's header from its
    credential's file, the priority, and the text as the body, by `data-raw`, which takes a leading @ as itself, never
    as a file to read (Task 23's review, M9). `globoff` first, so curl takes the URL as it is: a {a,b} or [1-2] in
    NTFY_URL, which `_url_ok` passes, never fans one alert out into several POSTs (Task 26's review)."""
    lines = ["globoff", f"url = {_curl_quoted(url)}", f"header = {_curl_quoted('@' + str(header_file))}",
             f"header = {_curl_quoted('Priority: ' + priority)}", f"data-raw = {_curl_quoted(text)}"]
    return ("\n".join(lines) + "\n").encode("utf-8")


class Alerter:
    """The brake's own ntfy alert, sent only while the gate is down: `alert_without_gate` starts Ubuntu's curl, with
    `-q` first and an empty environment, so it reads no `.curlrc` and nothing of the brake's, and writes its config to
    curl's stdin, so neither ntfy's address, its topic nor the text is on a command line `agent` can read; it is never
    waited on by the running brake (`reap` drops the finished each poll), and a --once run waits for it (`wait`),
    since systemd ends a oneshot unit's leftover processes. ntfy is set up when NTFY_URL and NTFY_TOPIC_BRAKE are set
    (the values file) and the `ntfy-token` credential is one header line: Task 25's placeholder isn't. `env`, `popen`
    and `curl_ok` are for the tests."""

    def __init__(self, *, env: Mapping[str, str] | None = None, popen: Callable[..., Any] | None = None,
                 curl_ok: Callable[[], bool] | None = None):
        self.env = os.environ if env is None else env
        self.popen = subprocess.Popen if popen is None else popen
        self.curl_ok = curl_ok or (lambda: os.access(CURL, os.X_OK))
        self.children: list[Any] = []

    def problem(self) -> str | None:
        """Why ntfy isn't set up, naming what is missing by its variable's or credential's name, never a value; None
        when it is."""
        url, topic = self.env.get("NTFY_URL"), self.env.get("NTFY_TOPIC_BRAKE")
        if not url:
            return "NTFY_URL isn't set"
        if not topic:
            return "NTFY_TOPIC_BRAKE isn't set"
        if not _url_ok(url):
            return "NTFY_URL isn't an http(s) URL with a host"
        if not NTFY_TOPIC.fullmatch(topic):
            return "NTFY_TOPIC_BRAKE isn't 1 to 64 letters, digits, - or _"
        try:
            read_header_credential(NTFY_TOKEN, self.env)
        except CredentialError as err:  # its text names the credential, never its content
            return str(err)
        return None

    def alert_without_gate(self, text: str, priority: str) -> str | None:
        """Start curl with this alert, and go on: None once it is started, else why it couldn't be."""
        url = self.env["NTFY_URL"].rstrip("/") + "/" + self.env["NTFY_TOPIC_BRAKE"]
        config = curl_config(url, Path(self.env["CREDENTIALS_DIRECTORY"]) / NTFY_TOKEN, priority, text)
        try:
            child = self.popen(list(CURL_ARGS), stdin=subprocess.PIPE, start_new_session=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env={})
        except OSError as err:
            return f"curl couldn't be started ({err.strerror or type(err).__name__})"
        self.children.append(child)
        try:
            child.stdin.write(config)
        except OSError as err:  # curl gone already: it reports, and is reaped, as any finished alert
            return f"curl took no config ({err.strerror or type(err).__name__})"
        finally:
            try:
                child.stdin.close()
            except OSError:
                pass
        return None

    def reap(self) -> list[int]:
        """Drop the alerts whose curl has finished; the exit codes of those that failed."""
        failed, running = [], []
        for child in self.children:
            code = child.poll()
            if code is None:
                running.append(child)
            elif code != 0:
                failed.append(code)
        self.children = running
        return failed

    def wait(self, timeout_s: float = ALERT_WAIT_S) -> list[int]:
        """Wait for the alerts started, up to `timeout_s` in all (curl's --max-time bounds each); then reap them."""
        deadline = time.monotonic() + timeout_s
        for child in list(self.children):
            try:
                child.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                pass
        return self.reap()


# ---------------------------------------------------------------------------------------------------------------------
# The brake

class _Once:
    """Logs a run of the same failure once, and again when it changes or clears, not four lines a second."""

    def __init__(self, log):
        self._log, self._last = log, {}

    def failed(self, what: str, line: str) -> None:
        if self._last.get(what) != line:
            self._last[what] = line
            self._log(line)

    def cleared(self, what: str, line: str) -> None:
        if self._last.pop(what, None) is not None:
            self._log(line)

    def forget(self, what: str) -> None:
        self._last.pop(what, None)

    def mark(self, what: str, line: str) -> None:
        """A failure said some other way (once an episode): its clearing is still said, once."""
        self._last[what] = line


@dataclass
class _Returning:
    """Memory on its way back, from a model whose unload llama-swap accepted or that it shows `stopping`."""

    gib: float  # the model's registry footprint; 0 for a model the registry doesn't know
    floor_gib: float  # MemAvailable when it began: more than FLOOR_TOLERANCE_GIB below it, the brake stops waiting
    since_s: float  # when it began, on the brake's clock
    stage: int  # the furthest state (STAGES) the brake has seen its engine in
    counted: bool = True  # False once the brake has stopped waiting for it


@dataclass
class _Unwritten:
    """A hold the brake fired but couldn't write, with none standing (the controller's ruling on Task 23's second
    re-review): its episode stands in the brake's memory, for firing, until a write succeeds or memory has stayed above
    the warn line for RELEASE_AFTER_S. No pause stands meanwhile: nothing on disk tells the gate to refuse a load."""

    hold: Hold  # what is written once a write succeeds: the fired hold, with the models unloaded since
    tried_s: float  # the last try to write it, on the brake's monotonic clock
    above_since_s: float | None = None  # since when memory has stayed above the warn line, or None


@dataclass
class _Fired:
    """The brake fired: what its first alert says, once the poll's unload is known; kept for the episode, with every
    model unloaded since, so a first alert the brake sends late has them all (first_alert_instead)."""

    at: float  # wall clock
    available_gib: float
    line_gib: float
    hold: Hold
    falling: bool
    sending: bool  # the brake sends the first alert itself, the gate being down; or, since it did late, has sent it
    written: bool  # a hold of this episode stands (write)
    waits: bool = False  # release_waits_for_dan for its hold: only `make brake-release` lifts it (after the unload)
    saved: Any = None  # the gate's saved state, read after the unload (gate.state.GateState), or None with no gate
    unloaded: list[tuple[str, str | None]] = field(default_factory=list)  # (label, state)
    failed: list[tuple[str, str]] = field(default_factory=list)  # (label, reason): unloads llama-swap didn't do


class _Brake:
    """run_brake's state from poll to poll. An episode lasts until memory is back at the warn line or above.
    Within one, each engine is asked to unload once at most: a model is a new engine only after it has left
    /running and loaded again. The events' episode is the hold's while a hold of this boot stands (brakeevents)."""

    def __init__(self, registry, client, state_dir: Path, log, now, *, wall=time.time, monotonic=time.monotonic,
                 boottime=None, boot_id: str = UNKNOWN_BOOT, gate_state: Path | None = None,
                 launch: Path | None = None, proc: Path = procs.PROC, spark_uid: int | None = None,
                 alerter: Alerter | None = None):
        self.registry, self.client, self.state_dir, self.log, self.now = registry, client, Path(state_dir), log, now
        self.wall, self.monotonic, self.boot_id = wall, monotonic, boot_id
        self.boottime = boottime or _boottime
        self.gate_state = None if gate_state is None else Path(gate_state)
        self.launch = None if launch is None else Path(launch)
        self.proc, self.spark_uid, self.alerter = proc, spark_uid, alerter
        self.events = self.state_dir / EVENTS_FILE
        self.once = _Once(log)
        self.clock = 0.0  # seconds slept: a poll's own work is left out, so a grace can run long but never short
        self.warned = False
        self.asked: set[str] = set()
        self.returning: dict[str, _Returning] = {}
        self.readings: deque[tuple[float, float]] = deque()  # (monotonic, GiB): the rate-of-fall watch's window
        self.history: deque[tuple[float, float]] = deque(maxlen=HISTORY_MAX)  # (wall, GiB): the brake's own readings
        self.boot_history: deque[tuple[float, float]] = deque(maxlen=HISTORY_MAX)  # (CLOCK_BOOTTIME, GiB): the same
        # The floor of the loads last in progress, with when it was last read (monotonic): it stands FALL_WINDOW_S and a
        # poll after they leave, while the window still holds their fall (Task 23's review, C1).
        self.floor_kept: tuple[float, float] | None = None
        self.lock_held = False  # a try found the events lock held elsewhere, and none has taken it since (I1)
        self.pending: deque[BrakeEvent] = deque()  # steps not yet written for the gate, in order (record)
        self.dropped = 0  # steps dropped from `pending` since it last emptied, the oldest first
        self.unrecorded_why = ""  # why the last write of `pending` failed, for the journal
        self.episode: int | None = None  # the events' episode this brake is in, once it has fired or unloaded
        self.said: set[tuple[str, int]] = set()  # what its journal said once an episode: (what, episode)
        self.unwritten: _Unwritten | None = None  # a fired hold it couldn't write, with none standing
        self.written_late: tuple[Hold, float] | None = None  # a hold a retry wrote this poll, with the reading
        self.first: _Fired | None = None  # the last fire, its episode's first alert's words (first_of)
        self.first_due = False  # this poll sends that first alert late, in place of a follow-up (first_alert_instead)
        self.loaded = True  # whether something was loaded, by this poll's /running, for a first alert's words
        self._activity: Any = _UNREAD

    def check_state_dir(self) -> None:
        try:
            if not self.state_dir.is_dir():
                why = "it doesn't exist or isn't a folder"
            elif not os.access(self.state_dir, os.W_OK | os.X_OK):
                why = "this user can't write to it"
            else:
                return
        except OSError as err:  # Python 3.12's is_dir raises on EACCES on the way to the folder, or EIO
            why = f"it can't be looked at ({err})"
        self.log(f"brake: ALERT — no hold can be written in {self.state_dir}: {why}. Models are still unloaded, "
                 "but new loads won't be held")

    def poll(self, mem: MemInfo) -> None:
        thresholds = self.registry.brake
        wall, mono = self.wall(), self.monotonic()
        self.remember(mono, wall, self.boottime(), mem.available_gib)
        self._activity = _UNREAD
        projected = self.watch(wall, mono) if mem.available_gib >= thresholds.brake_gib else None
        falling = projected is not None
        listed = self.listed(mem, falling)
        self.settle(mem, listed)
        states = dict(listed or ())
        candidates = [(m, s) for m, s in listed or () if s in ("starting", "ready") and m not in self.asked]
        activity = self.activity(wall) if listed is not None else None
        order = brake_order(candidates, activity, self.registry)
        inflight = sum(r.gib for r in self.returning.values() if r.counted)
        actions = plan_brake(mem, thresholds, self.registry, order, inflight, projected_gib=projected, ordered=True)
        if not actions:
            self.warned = False
            self.asked.clear()
            self.episode = None  # an episode whose hold couldn't be written stands on all the same (episode_now)
            # A credit no longer counted would keep a later one for the same model from being counted.
            self.returning = {m: r for m, r in self.returning.items() if r.counted}
        if self.unwritten is not None:  # before the unload, where the hold comes
            self.unwritten_episode(mem, wall, mono)
        fired: _Fired | None = None
        for action in actions:
            if action.kind == "warn" and not self.warned:
                self.log(f"brake: warning — {mem.available_gib:.1f} GiB available (warns below {thresholds.warn_gib:g})")
                self.warned = True
            elif action.kind == "hold" and self.unwritten is None and read_hold(self.state_dir) is None:
                fired = self.fire(mem, wall, mono, falling, self.loading(order, states, activity))
            elif action.kind == "unload":
                self.unload(action.model, mem, states.get(action.model), wall=wall, falling=falling, fired=fired)
        # After the hold and the unload: the journal's line, the brake's own alert, then the steps for the gate, so
        # neither the gate's saved state, which can be large, nor a lock another process can hold comes before either
        # (Task 23's re-review, Minors 8 and 9); a hold a retry wrote this poll included (its third re-review, Minor 2).
        self.loaded = listed is None or bool(listed)
        if fired is not None:
            self.after_fire(fired, wall)
            if fired.sending:
                self.first_alert(fired, something_loaded=self.loaded)
        if self.written_late is not None:
            (hold, gib), self.written_late = self.written_late, None
            self.hold_stands_late(hold, gib, wall)
        if self.first_due:
            self.first_alert_instead(wall)
        if self.pending:
            self.flush(0.0 if self.lock_held else LOCK_WAIT_S)

    def remember(self, mono: float, wall: float, boot: float, gib: float) -> None:
        self.readings.append((mono, gib))
        while self.readings and mono - self.readings[0][0] > 2 * FALL_WINDOW_S:
            self.readings.popleft()
        for history, at in ((self.history, wall), (self.boot_history, boot)):
            history.append((at, gib))
            while history and at - history[0][0] > HISTORY_S:
                history.popleft()

    def activity(self, wall: float) -> Activity | None:
        """The gate's record, read once a poll and only when needed; None while it is missing or stale, or with no
        gate."""
        if self._activity is _UNREAD:
            self._activity = (None if self.gate_state is None
                              else read_activity(self.gate_state / ACTIVITY_FILE, wall, boot_id=self.boot_id))
        return self._activity

    def watch(self, wall: float, mono: float) -> float | None:
        """The rate-of-fall watch: the reading UNLOAD_LEAD_S ahead when it would pass the brake line, else None. The
        loads in progress are read on every poll it runs, so a load's floor is known from the first poll that finds it
        in progress, whatever its pace: a load too slow for the window, then a step the gate marks ready as it lands,
        included (the controller's ruling on Task 23's second re-review, Minor 1). Whatever fails in reading them is no
        load in progress, the stricter way, and is said once. Once they have left (the gate marks a load ready as its
        fall ends, or a moment after), their floor stands FALL_WINDOW_S and a poll more, while the window still holds
        their fall (Task 23's review, C1)."""
        line = self.registry.brake.brake_gib
        floor = self.load_floor(wall, mono)
        rough = projected_gib(self.readings, lead_s=UNLOAD_LEAD_S, window_s=FALL_WINDOW_S, floor_gib=None)
        if rough is None or rough >= line:  # no fall of MIN_FALL_GIB in the window, or none that would pass the line:
            return None  # their floor can only hold the projection back
        ahead = projected_gib(self.readings, lead_s=UNLOAD_LEAD_S, window_s=FALL_WINDOW_S, floor_gib=floor)
        return ahead if ahead is not None and ahead < line else None

    def load_floor(self, wall: float, mono: float) -> float | None:
        """The floor of the loads in progress, or of the last ones while it stands (watch)."""
        if self.launch is not None:
            try:
                tickets.started(self.launch, now=wall, boot_id=self.boot_id)
            except OSError as err:
                self.once.failed("started", f"brake: launch's started records in {self.launch} can't be read ({err}): "
                                            "none of them counts as a load in progress")
            else:
                self.once.cleared("started", "brake: launch's started records read again")
        try:
            loads = loads_in_progress(self.activity(wall), self.launch, now=wall, boot_id=self.boot_id,
                                      registry=self.registry, readings=self.history, boot_readings=self.boot_history,
                                      proc=self.proc, spark_uid=self.spark_uid)
        except Exception as err:  # noqa: BLE001 (a load misread must never stop the brake: none, the stricter way)
            self.once.failed("loads", f"brake: the loads in progress couldn't be read ({err}): counting none")
            loads = []
        floor = expected_floor(loads)
        if floor is not None:
            self.floor_kept = (floor, mono)
        elif (self.floor_kept is not None
              and mono - self.floor_kept[1] <= FALL_WINDOW_S + self.registry.brake.poll_ms / 1000):
            floor = self.floor_kept[0]
        return floor

    def listed(self, mem: MemInfo, falling: bool = False) -> list[tuple[str, str]] | None:
        """What llama-swap runs, asked only below the warn line, while memory is on its way back, or when the rate of
        fall would pass the line: every call sends the key."""
        if (not falling and mem.available_gib >= self.registry.brake.warn_gib
                and not any(r.counted for r in self.returning.values())):
            self.once.forget("llama-swap")  # not asked: a failure next time starts a new run
            return None
        try:
            listed = [(r.model, r.state) for r in self.client.running()]
        except LlamaSwapError as err:
            self.once.failed("llama-swap", f"brake: {err}")
            return None
        self.once.cleared("llama-swap", "brake: llama-swap answers again")
        return listed

    def settle(self, mem: MemInfo, listed: list[tuple[str, str]] | None) -> None:
        """Memory on its way back has arrived once its model has left /running, or is back at an earlier state (a
        new engine). The brake stops waiting for it when memory falls more than FLOOR_TOLERANCE_GIB below where it
        was, or when the grace is over."""
        if listed is not None:
            names = {m for m, _ in listed}
            for model in [m for m in self.returning if m not in names]:
                del self.returning[model]
            self.asked &= names  # a model that went and came back is a new engine
            for model, state in listed:
                r, stage = self.returning.get(model), STAGES.get(state)
                if r is not None and stage is not None and stage < r.stage:  # went and came back between polls
                    del self.returning[model]
                    self.asked.discard(model)
                    r = None
                elif r is not None and stage is not None:
                    r.stage = stage
                if r is None and state == "stopping":
                    self.returning[model] = _Returning(self.footprint(model), mem.available_gib, self.clock,
                                                       STAGES["stopping"])
        for model, r in self.returning.items():
            if r.counted and mem.available_gib < r.floor_gib - FLOOR_TOLERANCE_GIB:
                r.counted = False
                self.log(f"brake: memory still falling ({mem.available_gib:.1f} GiB, {r.floor_gib:.1f} when {model} "
                         "began to unload): not waiting for it")
            elif r.counted and self.clock - r.since_s >= GRACE_S:
                r.counted = False
                self.log(f"brake: {model} still hasn't given its memory back after {GRACE_S:g} s: not waiting for it")

    @staticmethod
    def loading(order: list[str], states: dict[str, str], activity: Activity | None) -> str | None:
        """The model that was starting when the brake fired, for its hold: the first loading engine in its order, or
        one the gate's record shows starting."""
        starting = {name for name, seen in (activity.models.items() if activity else ()) if seen.state == "starting"}
        for name in order:
            if states.get(name) == "starting" or name in starting:
                return name
        return min(starting) if starting else None

    def fire(self, mem: MemInfo, wall: float, mono: float, falling: bool, loading: str | None) -> _Fired:
        """No hold stands: write one, of a new episode, with this boot, the model loading and the reading, before
        anything that could wait: its episode comes from an unlocked read of the events (Task 23's review, I1). Then
        queue `fired` for the gate, saying whether the brake sends the alert itself; it is written after the unload. A
        hold it can't write stands in its memory instead (unwritten_episode)."""
        line = self.registry.brake.brake_gib
        episode = self.episode = self.next_episode()
        reason = f"{mem.available_gib:.1f} GiB available"
        if falling:
            reason += f", falling fast toward the {line:g} GiB line"
        hold = Hold(self.now(), reason, (), self.boot_id, episode, loading, mem.available_gib)
        written = self.write(hold)
        if not written:
            self.unwritten = _Unwritten(hold, mono)
        sending = self.will_alert(wall, episode)
        self.record(BrakeEvent(1, wall, self.boot_id, episode, "fired", None, None, mem.available_gib, line, sending,
                               falling=falling, hold_written=written))
        self.first = _Fired(wall, mem.available_gib, line, hold, falling, sending, written)
        return self.first

    def after_fire(self, fired: _Fired, wall: float) -> None:
        """After the poll's unload: the gate's saved state, read once for the journal's line and the alert, and the
        line saying how the pause ends (Task 23's re-review, Minor 9: a file of up to 16 MiB is never decoded before the
        unload)."""
        fired.saved, fired.waits = self.how_it_ends(fired.hold, wall)
        if fired.written:
            self.log(self.paused(fired.waits, fired.saved))

    def how_it_ends(self, hold: Hold, wall: float) -> tuple[Any, bool]:
        """The gate's saved state (None with no gate), and whether the hold's release waits for Dan by rule 5."""
        saved = None if self.gate_state is None else load_state(self.gate_state, now=wall)[0]  # never raises
        waits = release_waits_for_dan(hold, boot_id=self.boot_id, now=wall,
                                      last_auto_release_at=None if saved is None else saved.last_auto_release_at)
        return saved, waits

    def unwritten_episode(self, mem: MemInfo, wall: float, mono: float) -> None:
        """An episode whose hold couldn't be written, with none standing (the controller's ruling on Task 23's second
        re-review). It stands in the brake's memory, for firing: no new episode and no new first alert, and Phase 1's
        grace and unload rules go on as with a hold. The write is tried again at most every HOLD_RETRY_S; once one
        succeeds, the hold stands as usual, and the rest waits until after the poll's unload (hold_stands_late, Task
        23's third re-review, Minor 2). Should memory stay above the warn line for the gate's RELEASE_AFTER_S first,
        the episode ends, and the journal says no pause ever stood."""
        unwritten = self.unwritten
        if mem.available_gib > self.registry.brake.warn_gib:  # the gate's test (core): above, never at
            if unwritten.above_since_s is None:
                unwritten.above_since_s = mono
        else:
            unwritten.above_since_s = None
        if mono - unwritten.tried_s >= HOLD_RETRY_S:
            unwritten.tried_s = mono
            if self.write(unwritten.hold):
                self.unwritten = None
                self.written_late = (unwritten.hold, mem.available_gib)
                return
        if unwritten.above_since_s is not None and mono - unwritten.above_since_s >= RELEASE_AFTER_S:
            self.unwritten = None
            self.episode = None
            self.log(f"brake: memory has stayed above the warn line for {messages.duration(RELEASE_AFTER_S)}, so "
                     f"episode {unwritten.hold.episode} is over; its hold was never written, so no pause ever stood")

    def hold_stands_late(self, hold: Hold, available_gib: float, wall: float) -> None:
        """A hold a later try wrote, after its fired said it wasn't (the controller's ruling on Task 23's fix round 3),
        after the poll's unload: the journal's line for the pause, a `hold_written` step for the gate, queued as every
        step is, so the words catch up, and, while the gate is down, the brake's own follow-up: its pause now stands,
        and how it ends by rule 5. When the brake never sent the episode's first alert itself, it sends that instead,
        saying new loads are paused (first_alert_instead)."""
        saved, waits = self.how_it_ends(hold, wall)
        self.log(self.paused(waits, saved))
        sending = self.will_alert(wall, hold.episode)
        self.record(BrakeEvent(1, wall, self.boot_id, hold.episode, "hold_written", None, None, available_gib,
                               self.registry.brake.brake_gib, sending))
        if sending and self.instead_of_follow_up(hold.episode):
            return
        if sending:  # will_alert is false with no gate, so its saved state was read
            fields: dict[str, Any] = {
                "at": wall, "follow_up": True, "pause_stands": True, "by_brake": True, "release_waits_for_dan": waits,
                "release_after_s": RELEASE_AFTER_S, "release_assumed": saved.release_assumed}
            if waits and not saved.release_assumed:
                fields["released_at"] = saved.last_auto_release_at
            self.send("brake_fired", fields)

    def next_episode(self) -> int:
        """The next episode: one past the last in the file for this boot, read without the lock, and past any of the
        steps still waiting to be written, so a pause after a release never takes the number of one queued (Task 23's
        re-review, Minor 4)."""
        try:
            from_file = episode_for(self.events, None, boot_id=self.boot_id, locked=False)
        except EventsFileNotRegular:  # said once an episode, as its steps can't be written (flush)
            from_file = (self.episode or 0) + 1
        except (OSError, ValueError) as err:  # the events file can't be read: carry on, numbered from this brake
            self.once.failed("episode", f"brake: its steps in {self.events} can't be read ({err})")
            from_file = (self.episode or 0) + 1
        queued = max((event.episode for event in self.pending if event.boot_id == self.boot_id), default=0) + 1
        return max(from_file, queued)

    def paused(self, waits: bool, saved: Any) -> str:
        """The journal's line for a hold just written: how the pause ends in 2a (Task 23's review, I3). By rule 5 the
        gate lifts it, at most once an hour, unless it waits for Dan: a brake within the hour after an automatic
        release, or after the gate's saved state was damaged. *Paused* is the brake's word."""
        if waits:
            why = ("the gate's saved state was damaged, so it can't tell when its last automatic release was"
                   if saved is not None and saved.release_assumed
                   else "it fired within an hour of the gate's last automatic release")
            return f"brake: new loads paused until `make brake-release`: {why}"
        # No figure: a drill's copy of the registry raises its own lines, never the gate's (the re-review's Minor 5).
        return (f"brake: new loads paused; the gate lifts the pause once memory has stayed above the gate's warn line "
                f"for {messages.duration(RELEASE_AFTER_S)}, at most once an hour, or `make brake-release` lifts it now")

    def unload(self, model: str, mem: MemInfo, state: str | None, *, wall: float, falling: bool = False,
               fired: _Fired | None = None) -> None:
        self.asked.add(model)
        line = self.registry.brake.brake_gib
        said = self.unload_state(model, state, wall)
        try:
            self.client.unload(model)
        except LlamaSwapUnreachable as err:
            # v257 answers an unload only once the engine has exited, and carries on with it when the caller gives
            # up: no answer in 2 s is a slow stop, so its memory counts as on its way.
            self.log(f"brake: no answer in 2 s; counting {model} as on its way ({err})")
        except LlamaSwapError as err:
            self.log(f"brake: unloading {model} failed: {err}")
            # The controller's ruling at Task 14: a failed or refused unload is an event with its reason, so the gate
            # can word the cause (messages, the controller's ruling at Task 23): `HTTP <status>` for a refusal, else the
            # error's own line, which never holds the key (LlamaSwap's errors don't).
            status = getattr(err, "status", None)
            reason = (f"HTTP {status}" if isinstance(status, int)
                      else messages._one_line(str(err))[:REASON_MAX] or type(err).__name__)
            episode = self.episode_now()
            sending = fired.sending if fired is not None else self.will_alert(wall, episode)
            self.record(BrakeEvent(1, wall, self.boot_id, episode, "unload_failed", model, said, mem.available_gib,
                                   line, sending, reason=reason, falling=falling))
            first = fired if fired is not None else self.first_of(episode)
            if first is not None:
                first.failed.append((self.label(model), reason))
            if fired is None and sending and not self.instead_of_follow_up(episode):
                self.follow_up(wall, model, said, reason=reason)
            return
        else:
            # This includes an engine still starting, which v257 stops at once: measured 2026-09-28, it answered 200 in
            # 0.01 s, left no process, and failed the request that started it with a 500.
            self.log(f"brake: unloaded {model} at {mem.available_gib:.1f} GiB available")
        self.returning[model] = _Returning(self.footprint(model), mem.available_gib, self.clock,
                                           STAGES.get(state, STAGES["ready"]))
        reason = f"{mem.available_gib:.1f} GiB available"
        unwritten = self.unwritten
        if unwritten is not None:  # the hold it couldn't write lists the model, for the write's next try
            if model not in unwritten.hold.unloaded:
                unwritten.hold = replace(unwritten.hold, unloaded=unwritten.hold.unloaded + (model,))
        elif (hold := read_hold(self.state_dir)) is None:  # released while the unload was on its way: memory was
            # below the line, or falling fast toward it
            self.write(replace(self.fresh_hold(reason, mem, fired), unloaded=(model,)))
        elif hold.since == UNREADABLE_SINCE:  # read_hold's stand-in for a damaged file: never write it back
            self.write(replace(self.fresh_hold(f"{reason}; the hold file before this one was damaged ({hold.reason})",
                                               mem, fired), unloaded=(model,)))
        elif model not in hold.unloaded:
            self.write(replace(hold, unloaded=hold.unloaded + (model,)))
        if state == "starting" or said == "starting":
            self.forget_started(model)
        episode = self.episode_now()
        sending = fired.sending if fired is not None else self.will_alert(wall, episode)
        self.record(BrakeEvent(1, wall, self.boot_id, episode, "unload", model, said, mem.available_gib, line, sending,
                               falling=falling))
        first = fired if fired is not None else self.first_of(episode)
        if first is not None:
            first.unloaded.append((self.label(model), said))
        if fired is None and sending and not self.instead_of_follow_up(episode):
            self.follow_up(wall, model, said)

    def first_of(self, episode: int) -> _Fired | None:
        """The episode's fire, as this brake keeps it; None for an episode it didn't fire, such as one whose hold stood
        when it started."""
        first = self.first
        return first if first is not None and first.hold.episode == episode else None

    def instead_of_follow_up(self, episode: int) -> bool:
        """Whether a follow-up the brake would send itself is the episode's first alert instead: the gate was up when it
        fired, so the brake never sent that first alert, and the gate, down since, may not have either (the
        controller's ruling on Task 23's fix round 4). It goes after the poll's unload (first_alert_instead)."""
        first = self.first_of(episode)
        if first is None or first.sending:
            return False
        self.first_due = True
        return True

    def first_alert_instead(self, wall: float) -> None:
        """The episode's first alert, sent late by the brake in place of a follow-up: the fire's reading, every model
        unloaded and every unload refused since, and the pause as it stands now, written, or not while its hold still
        can't be. Then a `first_alert_by_brake` step, so the gate, once back, sends no first alert of its own for the
        episode, only what comes after (the controller's ruling on Task 23's fix round 4)."""
        self.first_due = False
        first = self.first
        first.saved, first.waits = self.how_it_ends(first.hold, wall)
        first.written = self.unwritten is None
        first.sending = True
        self.first_alert(first, something_loaded=self.loaded)
        self.record(BrakeEvent(1, wall, self.boot_id, first.hold.episode, "first_alert_by_brake", None, None,
                               first.available_gib, first.line_gib, True))

    def fresh_hold(self, reason: str, mem: MemInfo, fired: _Fired | None) -> Hold:
        """A hold written again by an unload (Phase 1's I1 and M1), with 2a's fields: the fired one's when the brake
        fired this poll."""
        if fired is not None:
            return replace(fired.hold, since=self.now(), reason=reason, unloaded=())
        return Hold(self.now(), reason, (), self.boot_id, self.episode_now(), None, mem.available_gib)

    def unload_state(self, model: str, state: str | None, wall: float) -> str | None:
        """The model's state as the brake's events name it: starting, idle or answering by the gate's record; without
        it, `starting` for a model llama-swap shows starting, and None for the rest, whose requests only the front
        counts."""
        activity = self.activity(wall)
        seen = activity.models.get(model) if activity is not None else None
        if seen is not None:
            return "starting" if seen.state == "starting" else "answering" if seen.inflight > 0 else "idle"
        return "starting" if state == "starting" else None

    def episode_now(self) -> int:
        """The events' episode: the standing hold's while a hold of this boot stands, or the one it couldn't write while
        that episode stands in its memory (unwritten_episode); else this brake's own since it fired or first unloaded;
        else the next in the file (episode_for)."""
        hold = read_hold(self.state_dir)
        if hold is not None and hold.boot_id == self.boot_id and hold.episode is not None:
            self.episode = hold.episode
        elif self.unwritten is not None:
            self.episode = self.unwritten.hold.episode
        elif self.episode is None:
            self.episode = self.next_episode()
        return self.episode

    def forget_started(self, model: str) -> None:
        """The load the brake stopped is no longer in progress: its started/ record goes. One that can't go stops
        nothing, and is said once."""
        if self.launch is None:
            return
        try:
            tickets.clear_started(self.launch, model)
        except (OSError, ValueError) as err:
            self.once.failed("clear", f"brake: can't clear {model}'s started record in {self.launch}: {err}")
        else:
            self.once.cleared("clear", "brake: started records clear again")

    def record(self, event: BrakeEvent) -> None:
        """A step for the gate: queued behind any still waiting, and written at the poll's end, after its hold and
        unload (flush). Up to PENDING_MAX of them, no step is lost to a lock another process holds (the controller's
        ruling on Task 23's fix round 1): what can't be written then waits, in order, for the next poll that gets the
        lock. Past PENDING_MAX the oldest go, said once, and how many once the rest are written."""
        if len(self.pending) >= PENDING_MAX:
            self.pending.popleft()
            self.dropped += 1
            if self.dropped == 1:
                self.log(f"brake: ALERT — more than {PENDING_MAX} of its steps for the gate are waiting to be "
                         "recorded, so the oldest are dropped")
        self.pending.append(event)  # written at the poll's end, after its hold and unload (poll)

    def flush(self, wait_s: float) -> None:
        """Write the waiting steps, in order, each fsynced (brakeevents.append_event, whose seq comes from the file
        under its lock, so it goes on across restarts). The tries for the lock wait `wait_s` at most in all, the waits
        that succeed counted too: any process that can read the file can hold it, and take it back after each step
        (Task 23's review, I1; the controller's ruling on its second re-review). Once a try finds it held, the polls
        after it try without waiting until one takes it. A write that fails leaves that step and those after it waiting,
        for the next poll, and never stops the brake; it is said once, and an events file that isn't a regular file
        once for each episode whose steps wait on it (the re-review's Minor 3, and its second's), and once the steps are
        recorded again. A step the file would never take (any other ValueError) is dropped, and said."""
        deadline = time.monotonic() + wait_s
        while self.pending:
            try:
                append_event(self.events, self.pending[0], wait_s=max(0.0, deadline - time.monotonic()))
            except EventsFileNotRegular:  # the file's fault, not the step's: they wait, as for a lock
                self.unrecorded_why = f"the brake's events file {self.events} isn't a regular file"
                line = (f"brake: ALERT — the brake's events file {self.events} isn't a regular file, so its steps "
                        "can't be recorded for the gate")
                self.say_once("events-file", self.pending[-1].episode, line)  # the newest step's: fired or recorded
                self.once.mark("events", line)
                return
            except ValueError as err:  # never a whole event: no wait will make it one
                self.pending.popleft()
                self.log(f"brake: ALERT — a step for the gate isn't a whole event ({err}), so it isn't recorded")
                continue
            except OSError as err:  # BlockingIOError among them: the lock, held elsewhere
                if isinstance(err, BlockingIOError):
                    self.lock_held = True  # no more waiting for it until a try takes it
                    self.unrecorded_why = f"another process held the lock on {self.events}"
                else:
                    self.unrecorded_why = str(err)
                self.once.failed("events", f"brake: ALERT — can't record its steps for the gate in {self.events} yet: "
                                           f"{err}. They wait, in order, for a poll that can; the brake goes on")
                return
            self.lock_held = False
            self.pending.popleft()
        self.once.cleared("events", "brake: its steps are recorded for the gate again")
        if self.dropped:
            self.log(f"brake: ALERT — {self.dropped} of its steps for the gate were dropped, the oldest first, while "
                     f"it couldn't record them; the newest {PENDING_MAX} are recorded")
            self.dropped = 0

    def hold_last(self) -> None:
        """A --once run's end: one last try for a hold it couldn't write, with the models unloaded since, as Phase 1's
        unload tried it again in the same poll. This run won't poll again, so the once-a-second limit gives way."""
        if self.unwritten is not None and self.write(self.unwritten.hold):
            hold, self.unwritten = self.unwritten.hold, None
            self.hold_stands_late(hold, self.readings[-1][1] if self.readings else hold.available_gib, self.wall())
            if self.first_due:
                self.first_alert_instead(self.wall())

    def flush_last(self) -> None:
        """A --once run's end: one last bounded try for the steps still waiting, then the journal says what went
        unrecorded, since this run won't poll again."""
        if not self.pending:
            return
        self.flush(LOCK_WAIT_S)
        if self.pending:
            steps = ", ".join(event.kind if event.model is None else f"{event.kind} {event.model}"
                              for event in self.pending)
            dropped = f", and {self.dropped} older ones were dropped" if self.dropped else ""
            self.log(f"brake: ALERT — {len(self.pending)} of its steps for the gate went unrecorded ({steps})"
                     f"{dropped}: {self.unrecorded_why}")

    def say_once(self, what: str, episode: int, line: str) -> None:
        if (what, episode) not in self.said:
            self.said.add((what, episode))
            self.log(line)

    def will_alert(self, wall: float, episode: int) -> bool:
        """Whether the brake sends this step's alert itself: only while the gate's record is missing or stale, with
        ntfy set up, curl there to run, and brake_fired on in the registry. Otherwise the event says it didn't, so
        the gate, once back, still tries it; why it couldn't is said once an episode."""
        if self.alerter is None or self.gate_state is None or self.activity(wall) is not None:
            return False
        notifications = getattr(self.registry, "notifications", None)
        if notifications is None:
            self.say_once("registry", episode, "brake: the registry didn't load, so its own alert can't be worded and "
                                               "wasn't sent; the gate tries it once it's back")
            return False
        if notifications.get("brake_fired") == "off":
            return False
        try:
            problem = self.alerter.problem()
        except Exception as err:  # noqa: BLE001 (a check that breaks must never stop the brake: no alert, said)
            problem = f"its settings couldn't be read ({err})"
        if problem is not None:
            self.say_once("ntfy", episode, f"brake: ntfy isn't set up (Task 2, Phase 5), so its own alert wasn't "
                                           f"sent: {problem}. The gate tries it once it's back")
            return False
        if not self.alerter.curl_ok():
            self.say_once("curl", episode, f"brake: ALERT — {CURL} can't be run, so its own alert wasn't sent; the "
                                           "gate tries it once it's back")
            return False
        return True

    def first_alert(self, fired: _Fired, *, something_loaded: bool) -> None:
        """Task 7's brake_fired, `by_brake`: what the fire's poll unloaded (or, sent late, every unload since: the
        controller's ruling on Task 23's fix round 4), and why llama-swap didn't unload a model it refused (the
        controller's ruling, at Task 23); or, with none, that nothing of the stack's was loaded, or that llama-swap
        hasn't confirmed its unload; and how the pause ends, by rule 5, from the last automatic release in the gate's
        saved state, or, with its hold never written, that new loads weren't paused (the controller's ruling on Task
        23's fix round 2)."""
        state = fired.saved or load_state(self.gate_state, now=fired.at)[0]  # never raises; damage: release_assumed
        waits = fired.waits
        fields: dict[str, Any] = {
            "at": fired.at, "available_gib": fired.available_gib, "line_gib": fired.line_gib,
            "unloaded": list(fired.unloaded), "by_brake": True, "release_waits_for_dan": waits,
            "release_after_s": RELEASE_AFTER_S, "release_assumed": state.release_assumed,
            "unload_unanswered": not fired.unloaded and (something_loaded or bool(fired.failed)),
            "falling": fired.falling, "not_unloaded": list(fired.failed),
            "hold_written": fired.written}  # False: no hold of its stood when it fired, so new loads weren't paused
        if waits and not state.release_assumed:
            fields["released_at"] = state.last_auto_release_at
        self.send("brake_fired", fields)

    def follow_up(self, wall: float, model: str, said: str | None, *, reason: str | None = None) -> None:
        """A later unload's follow-up, or, with a `reason`, why llama-swap didn't do it (Task 23)."""
        if reason is None:
            fields: dict[str, Any] = {"unloaded": [(self.label(model), said)]}
        else:
            fields = {"unloaded": [], "not_unloaded": [(self.label(model), reason)]}
        self.send("brake_fired", fields | {"at": wall, "follow_up": True, "by_brake": True})

    def send(self, kind: str, fields: dict[str, Any]) -> None:
        """Words the alert and starts curl with it; neither ever stops the brake. Once the event says the brake sent
        it, the gate won't, so a failure here is an ALERT line in the journal."""
        try:
            note = messages.notification(kind, self.registry, **fields)
        except Exception as err:  # noqa: BLE001 (words that can't be built must never stop the brake)
            self.log(f"brake: ALERT — its own alert couldn't be worded ({err}), and the gate won't send it")
            return
        if note is None:
            return
        try:
            trouble = self.alerter.alert_without_gate(note.message, note.priority)
        except Exception as err:  # noqa: BLE001 (an alert that breaks must never stop the brake)
            trouble = f"it broke ({err})"
        if trouble is not None:
            self.log(f"brake: ALERT — its own alert wasn't sent: {trouble}; the gate won't send it")

    def write(self, hold: Hold) -> bool:
        """Write the hold: True when a hold of its episode stands afterwards, so new loads are paused. A write that
        failed after its rename (the folder's fsync) left it standing (the controller's ruling on Task 23's second
        re-review, Minor 2), and one that failed before it left any earlier hold of the episode standing."""
        try:
            write_hold(self.state_dir, hold)
        except OSError as err:  # a missing or read-only folder, a full disk: the unload must still go out
            stands = read_hold(self.state_dir)
            if stands == hold:
                self.once.failed("hold", f"brake: ALERT — the hold stands in {self.state_dir}, but its folder's fsync "
                                         f"failed ({err}), so a power cycle may lose it. New loads are paused")
                return True
            if stands is not None and (stands.boot_id, stands.episode) == (hold.boot_id, hold.episode):
                self.once.failed("hold", f"brake: ALERT — can't write the hold in {self.state_dir}: {err}. The hold "
                                         "that stands still pauses new loads, but doesn't list every model unloaded")
                return True
            self.once.failed("hold", f"brake: ALERT — can't write the hold in {self.state_dir}: {err}. Models are "
                                     "still unloaded, but new loads aren't held")
            return False
        self.once.cleared("hold", "brake: the hold is written again")
        return True

    def footprint(self, model: str) -> float:
        known = self.registry.models.get(model)
        return known.footprint_gib if known else 0.0

    def label(self, model: str) -> str:
        known = self.registry.models.get(model)
        return known.label if known is not None else model


class StartCheck:
    """Whether llama-swap answers GET /running with the brake's key, asked at start. It asks from a thread of its own
    (start), off the poll path, so braking never waits for it, even on a llama-swap that takes the connection and
    never answers (the re-review of Phase 1's council fixes, N4): again every KEY_CHECK_RETRY_S while nothing answers,
    until KEY_CHECK_S have passed, and never once there is an answer. It needs a client of its own, one the polls
    don't use. The record from the brake's last start is replaced at once, so a check that never finishes can't pass
    for this one. `key_env` is where the key came from: a variable's name, or `credential <name>` (Task 23), never the
    key."""

    def __init__(self, client, state_dir: Path, key_env: str, log, now=_now, clock=time.monotonic, sleep=time.sleep):
        self.client, self.state_dir, self.key_env, self.log, self.now, self.clock, self.sleep = (
            client, Path(state_dir), key_env, log, now, clock, sleep)
        self.where = key_env if key_env.startswith("credential ") else f"${key_env}"
        self.deadline = clock() + KEY_CHECK_S
        self.done = False
        self.thread: threading.Thread | None = None
        self.record(None, f"waiting for llama-swap to answer, for {KEY_CHECK_S:g} s at most")

    def start(self) -> None:
        """Runs the check in a daemon thread, which ends with its answer; the brake runs on meanwhile."""
        self.thread = threading.Thread(target=self.run, name="start-check", daemon=True)
        self.thread.start()

    def run(self) -> None:
        while True:
            self.ask()
            if self.done:
                return
            self.sleep(KEY_CHECK_RETRY_S)

    def ask(self) -> None:
        if self.done:
            return
        try:
            self.client.running()
        except LlamaSwapUnreachable as err:
            if self.clock() < self.deadline:
                return
            ok, detail = False, f"llama-swap didn't answer in {KEY_CHECK_S:g} s ({err})"
        except LlamaSwapError as err:  # its text never holds the key
            no_key = "" if getattr(self.client, "api_key", True) else f" (no key in {self.where})"
            ok, detail = False, f"{err}{no_key}"
        else:
            ok, detail = True, f"llama-swap answers GET /running with the key in {self.where}"
        self.done = True
        if ok:
            self.log(f"brake: {detail}")
        else:
            self.log(f"brake: ALERT — its start check failed: {detail}. Until llama-swap answers it with its key, the "
                     "brake can hold new loads but can't unload a model")
        self.record(ok, detail)

    def record(self, ok: bool | None, detail: str) -> None:
        try:
            write_key_check(self.state_dir, KeyCheck(self.now(), ok, detail, self.key_env))
        except OSError as err:  # a missing or read-only folder: check_state_dir has said so too
            self.log(f"brake: ALERT — can't record its start check in {self.state_dir}: {err}")


def run_brake(registry, client, state_dir: Path, *, read_mem=read_meminfo, sleep=time.sleep,
              log=print, now=_now, once: bool = False, start_check: StartCheck | None = None,
              monotonic=time.monotonic, wall=time.time, boottime=None, boot_id: str | None = None,
              gate_state: Path | None = None, launch: Path | None = None, proc: Path = procs.PROC,
              spark_uid: int | None = None, alerter: Alerter | None = None) -> None:
    """The brake's loop, one poll every `poll_ms`, and one model unloaded a poll at most, so a --once run unloads one.
    `gate_state` and `launch` are the gate's folder and launch's (None: no gate, no records, Phase 1's brake), `alerter`
    the brake's own alert while the gate is down (None: none). `monotonic` times the rate-of-fall watch, `wall` the
    records and events, and `boottime` (CLOCK_BOOTTIME by default) the readings a started record's start time is
    matched to; `proc`, `spark_uid` and `boot_id` are for the tests (this boot's, read when None)."""
    if boot_id is None:
        try:
            boot_id = read_boot_id()
        except OSError as err:
            log(f"brake: this boot's id can't be read ({err}): its hold and steps say {UNKNOWN_BOOT}")
            boot_id = UNKNOWN_BOOT
    brake = _Brake(registry, client, state_dir, log, now, wall=wall, monotonic=monotonic, boottime=boottime,
                   boot_id=boot_id, gate_state=gate_state, launch=launch, proc=proc, spark_uid=spark_uid,
                   alerter=alerter)
    brake.check_state_dir()
    if start_check is not None:
        start_check.start()
    while True:
        if alerter is not None:
            for code in alerter.reap():
                log(_alert_failed(code))
        try:
            mem = read_mem()
        except (OSError, ValueError) as err:  # nothing to act on this poll; the next one tries again
            brake.once.failed("memory", f"brake: can't read memory: {err}")
        else:
            brake.once.cleared("memory", "brake: memory reads again")
            brake.poll(mem)
        if once:
            brake.hold_last()
            if alerter is not None:  # systemd ends a oneshot unit's leftover processes: its alerts go first
                for code in alerter.wait(ALERT_WAIT_S):
                    log(_alert_failed(code))
            brake.flush_last()
            return
        pause = registry.brake.poll_ms / 1000
        sleep(pause)
        brake.clock += pause


def _alert_failed(code: int) -> str:
    """The journal's line for the brake's own alert whose curl failed, its exit code in words (Task 23's review, M6).
    The event already says the brake sent it, so the gate won't send it either."""
    why = CURL_FAILURES.get(code, f"curl exited {code}")
    return (f"brake: its own alert didn't go through: {why}; the gate won't send it either, since the brake's event "
            "records it as sent by the brake")


def release(state_dir: Path, *, without_gate: bool | str = False) -> int:
    """Phase 1's release: the hold file removed directly. `without_gate`: as `make brake-release`'s fallback, when the
    gate isn't answering, worded so (messages.BRAKE_RELEASED_WITHOUT_GATE), or with the words given."""
    try:
        released = release_hold(state_dir)
    except OSError as err:  # say what stands in the way, not a traceback
        if isinstance(err, PermissionError) and not os.access(state_dir, os.W_OK | os.X_OK):
            why = f"this account can't write {state_dir}; spark-admin can"
        else:
            why = str(err)
        print(f"brake: can't release the hold: {why}", file=sys.stderr)
        return 1
    if not released:
        print("brake: no hold to release")
    elif isinstance(without_gate, str):
        print(without_gate)
    else:
        print(messages.BRAKE_RELEASED_WITHOUT_GATE if without_gate else "brake: hold released")
    return 0


GATE_RELEASE_TIMEOUT_S = 5  # `make brake-release`'s wait for the gate's answer


def release_through_gate(socket_path: Path, state_dir: Path, *, client=GateClient) -> int:
    """`make brake-release`: through the gate's control socket (`POST /v1/release` with `brake`), which ends the
    brake's pause, never Dan's own hold, and queues the reloads; worded as messages.brake_released_by_dan. Only when
    the gate isn't there to ask, or gives no whole answer in time (GateUnavailable, the brief's mapping), is the hold
    file removed directly, Phase 1's way. Its words say what is true of each (Task 23's review, M7): with nothing
    reached, nothing reloads until the gate is back; with the connection or the request taken (a timeout, or an answer
    broken off), the gate may be up, finds the hold gone and reloads as usual, and if not, nothing reloads until it
    answers. A gate that answers is never bypassed: a refused login (GateForbidden) or an answer the client can't take
    (GateError) is said, and the file kept."""
    try:
        answer = client(socket_path, GATE_RELEASE_TIMEOUT_S).post("/v1/release", {"brake": True})
    except GateUnavailable as err:
        if isinstance(err.__cause__, TimeoutError):
            return release(state_dir, without_gate=messages.brake_released_without_answer(GATE_RELEASE_TIMEOUT_S))
        if getattr(err, "sent", False):
            return release(state_dir, without_gate=messages.brake_released_without_answer(None))
        return release(state_dir, without_gate=True)
    except GateError as err:  # GateForbidden among them: the gate is there, and has its say
        print(err, file=sys.stderr)
        return 1
    reloading = answer.get("reloading")
    if not (isinstance(reloading, list) and all(isinstance(label, str) for label in reloading)):
        reloading = []
    if answer.get("brake") is True:
        print(messages.brake_released_by_dan(reloading))
        return 0
    if read_hold(state_dir) is None:
        print("brake: no hold to release")
        return 0
    print("brake: the gate couldn't lift the hold, so it still stands; `make logs s=gate` shows why", file=sys.stderr)
    return 1


def register(subparsers) -> None:
    p = subparsers.add_parser("brake", help="watch memory; unload models before the box freezes")
    p.add_argument("--once", action="store_true", help="one check, then exit")
    p.add_argument("--release", action="store_true", help="clear the hold so loads can start again")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key (for drills)")
    p.add_argument("--key-credential", default=None,
                   help="the credential holding the brake's llama-swap key, as its unit gives it (LoadCredential=)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if args.release:
        return release_through_gate(paths.GATE_CONTROL_SOCKET, paths.STATE)

    lines = threading.Lock()  # the start check logs from its own thread: one whole line at a time

    def log(line: str) -> None:
        with lines:
            print(line, flush=True)

    try:
        registry = load_registry(paths.REGISTRY)
    except Exception as err:  # whatever stops the registry loading, the brake must still run
        log(f"brake: ALERT — the registry {paths.REGISTRY} didn't load ({err}). Braking on the plan's thresholds "
            f"(warn below {FALLBACK.warn_gib:g} GiB, brake below {FALLBACK.brake_gib:g} GiB, every {FALLBACK.poll_ms} "
            "ms) with no model table: models go in the order /running lists them")
        registry = _NoRegistry()
    name = getattr(args, "key_credential", None)
    if name:  # the unit's: the key from its credential, recorded by the credential's name, never the value
        source = f"credential {name}"
        try:
            key = read_credential(name)
        except CredentialError as err:  # its text names the credential, never its content
            log(f"brake: ALERT — {err}. Until it can be read, the brake can hold new loads but can't unload a model")
            key = None
    else:
        source, key = args.key_env, key_from_env(args.key_env)
    # 2 s, not the default 10: a hung llama-swap must not hold a tick that acts on memory it just read.
    client = LlamaSwap(paths.LLAMASWAP_URL, key, timeout=2)
    # Only the brake that keeps running checks its key: a --once run, such as a drill's with another key, would
    # replace the unit's record with its own. The check asks from its own thread, with a client of its own.
    start_check = None if args.once else StartCheck(LlamaSwap(paths.LLAMASWAP_URL, key, timeout=2), paths.STATE,
                                                    source, log)
    run_brake(registry, client, paths.STATE, read_mem=read_meminfo, log=log, once=args.once, start_check=start_check,
              gate_state=paths.GATE_STATE, launch=paths.LAUNCH, alerter=Alerter())
    return 0
