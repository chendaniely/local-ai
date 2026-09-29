"""The minimal memory brake: warn, then hold new loads and unload models before the freeze band."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from spark import paths
from spark.hold import UNREADABLE_SINCE, Hold, read_hold, release_hold, write_hold
from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, key_from_env
from spark.memory import MemInfo, read_meminfo
from spark.registry import BrakeThresholds, Registry, load_registry
from spark.render import write_atomic

# How long memory from an unload counts as on its way back before the brake stops waiting for it and tries the
# next model. llama-swap v257 gives an engine its unloadTimeout (10 s by default; the stack's config leaves it) to
# exit on SIGTERM before it SIGKILLs it, so a slow but normal stop takes up to 10 s. The 5 s beyond allow for the
# kill and for the memory to show in MemAvailable, neither yet measured on this box. The wait costs no safety: once
# memory falls more than FLOOR_TOLERANCE_GIB below where it was, it ends. Measured on brightroar, 2026-09-28 (Phase 1
# drills): an idle coder's process was gone 0.6 s after the brake's tick began. A busy engine's stop is not yet
# measured; Phase 1's close decides whether 15 s stays.
GRACE_S = 15.0

# How far MemAvailable may dip below where it was when an unload began before the brake stops waiting for that
# unload's memory: noise is not a fall. Measured on brightroar, 2026-09-28 (Phase 1 drills), polling every 250 ms
# while the brake held, with 93 GiB available: for the first 15 s, with nothing generating, MemAvailable moved within
# ±0.02 GiB; then, while a Gemma session generated, it fell 4.1 GiB over 45 s in steps up to 0.58 GiB, and 1 poll in
# 239 moved more than 0.5 GiB. That step matches one of Gemma's context checkpoints by arithmetic (~0.59 GiB), so it
# was probably a real allocation, which the brake is right to count as a fall; not verified. Phase 1's close decides
# whether 0.5 GiB stays.
FLOOR_TOLERANCE_GIB = 0.5

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


@dataclass(frozen=True)
class KeyCheck:
    at: str
    ok: bool | None  # None while the brake still waits for llama-swap to answer
    detail: str
    key_env: str


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
    """Whole (write_atomic). The unit's umask leaves a new record readable to spark-admin, the folder's group."""
    write_atomic(Path(state_dir) / KEY_CHECK_FILE, json.dumps(asdict(check)))


@dataclass(frozen=True)
class Action:
    kind: str
    model: str | None = None


def plan_brake(mem: MemInfo, thresholds: BrakeThresholds, registry: Registry, running: list[str],
               inflight_gib: float = 0.0) -> list[Action]:
    """`running` holds the models the brake may unload. `inflight_gib` is memory on its way back from unloads that
    haven't shown in MemAvailable yet: it counts against the shortfall, so one shortfall doesn't unload two models."""
    if mem.available_gib >= thresholds.warn_gib:
        return []
    if mem.available_gib >= thresholds.brake_gib:
        return [Action("warn")]

    def order(name: str) -> tuple[bool, float]:
        model = registry.models.get(name)
        return (model.resident if model else False, -(model.footprint_gib if model else 0.0))

    victims = sorted(running, key=order)
    short = mem.available_gib + inflight_gib < thresholds.brake_gib
    return [Action("hold")] + ([Action("unload", victims[0])] if victims and short else [])


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class _NoRegistry:
    """The brake's registry when the real one won't load: the plan's thresholds and no model table, so models go
    in the order /running lists them."""

    brake: BrakeThresholds = FALLBACK
    models: dict = field(default_factory=dict)


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


@dataclass
class _Returning:
    """Memory on its way back, from a model whose unload llama-swap accepted or that it shows `stopping`."""

    gib: float  # the model's registry footprint; 0 for a model the registry doesn't know
    floor_gib: float  # MemAvailable when it began: more than FLOOR_TOLERANCE_GIB below it, the brake stops waiting
    since_s: float  # when it began, on the brake's clock
    stage: int  # the furthest state (STAGES) the brake has seen its engine in
    counted: bool = True  # False once the brake has stopped waiting for it


class _Brake:
    """run_brake's state from poll to poll. An episode lasts until memory is back at the warn line or above.
    Within one, each engine is asked to unload once at most: a model is a new engine only after it has left
    /running and loaded again."""

    def __init__(self, registry, client, state_dir: Path, log, now):
        self.registry, self.client, self.state_dir, self.log, self.now = registry, client, Path(state_dir), log, now
        self.once = _Once(log)
        self.clock = 0.0  # seconds slept: a poll's own work is left out, so a grace can run long but never short
        self.warned = False
        self.asked: set[str] = set()
        self.returning: dict[str, _Returning] = {}

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
        listed = self.listed(mem)
        self.settle(mem, listed)
        running = [m for m, state in listed or () if state in ("starting", "ready") and m not in self.asked]
        states = dict(listed or ())
        inflight = sum(r.gib for r in self.returning.values() if r.counted)
        actions = plan_brake(mem, thresholds, self.registry, running, inflight)
        if not actions:
            self.warned = False
            self.asked.clear()
            # A credit no longer counted would keep a later one for the same model from being counted.
            self.returning = {m: r for m, r in self.returning.items() if r.counted}
        for action in actions:
            if action.kind == "warn" and not self.warned:
                self.log(f"brake: warning — {mem.available_gib:.1f} GiB available (warns below {thresholds.warn_gib:g})")
                self.warned = True
            elif action.kind == "hold" and read_hold(self.state_dir) is None:
                if self.write(Hold(self.now(), f"{mem.available_gib:.1f} GiB available", ())):
                    self.log("brake: holding new loads until `make brake-release`")
            elif action.kind == "unload":
                self.unload(action.model, mem, states.get(action.model))

    def listed(self, mem: MemInfo) -> list[tuple[str, str]] | None:
        """What llama-swap runs, asked only below the warn line or while memory is on its way back: every call
        sends the key."""
        if mem.available_gib >= self.registry.brake.warn_gib and not any(r.counted for r in self.returning.values()):
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

    def unload(self, model: str, mem: MemInfo, state: str | None) -> None:
        self.asked.add(model)
        try:
            self.client.unload(model)
        except LlamaSwapUnreachable as err:
            # v257 answers an unload only once the engine has exited, and carries on with it when the caller gives
            # up: no answer in 2 s is a slow stop, so its memory counts as on its way.
            self.log(f"brake: no answer in 2 s; counting {model} as on its way ({err})")
        except LlamaSwapError as err:
            self.log(f"brake: unloading {model} failed: {err}")
            return
        else:
            # This includes an engine still starting, which v257 stops at once: measured 2026-09-28, it answered 200 in
            # 0.01 s, left no process, and failed the request that started it with a 500.
            self.log(f"brake: unloaded {model} at {mem.available_gib:.1f} GiB available")
        self.returning[model] = _Returning(self.footprint(model), mem.available_gib, self.clock,
                                           STAGES.get(state, STAGES["ready"]))
        reason = f"{mem.available_gib:.1f} GiB available"
        hold = read_hold(self.state_dir)
        if hold is None:  # released while the unload was on its way, or never written: memory was below the line
            hold = Hold(self.now(), reason, ())
        elif hold.since == UNREADABLE_SINCE:  # read_hold's stand-in for a damaged file: never write it back
            hold = Hold(self.now(), f"{reason}; the hold file before this one was damaged ({hold.reason})", ())
        if model not in hold.unloaded:
            self.write(replace(hold, unloaded=hold.unloaded + (model,)))

    def write(self, hold: Hold) -> bool:
        try:
            write_hold(self.state_dir, hold)
        except OSError as err:  # a missing or read-only folder, a full disk: the unload must still go out
            self.once.failed("hold", f"brake: ALERT — can't write the hold in {self.state_dir}: {err}. Models are "
                                     "still unloaded, but new loads aren't held")
            return False
        self.once.cleared("hold", "brake: the hold is written again")
        return True

    def footprint(self, model: str) -> float:
        known = self.registry.models.get(model)
        return known.footprint_gib if known else 0.0


class StartCheck:
    """Whether llama-swap answers GET /running with the brake's key, asked at start. It is asked after each poll, not
    before the first, so braking never waits for it: again on the next poll while nothing answers, until
    KEY_CHECK_S have passed, and never once there is an answer. The record from the brake's last start is replaced
    at once, so a check that never finishes can't pass for this one."""

    def __init__(self, client, state_dir: Path, key_env: str, log, now=_now, clock=time.monotonic):
        self.client, self.state_dir, self.key_env, self.log, self.now, self.clock = (
            client, Path(state_dir), key_env, log, now, clock)
        self.deadline = clock() + KEY_CHECK_S
        self.done = False
        self.record(None, f"waiting for llama-swap to answer, for {KEY_CHECK_S:g} s at most")

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
            no_key = "" if getattr(self.client, "api_key", True) else f" (no key in ${self.key_env})"
            ok, detail = False, f"{err}{no_key}"
        else:
            ok, detail = True, f"llama-swap answers GET /running with the key in ${self.key_env}"
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
              log=print, now=_now, once: bool = False, start_check: StartCheck | None = None) -> None:
    brake = _Brake(registry, client, state_dir, log, now)
    brake.check_state_dir()
    while True:
        try:
            mem = read_mem()
        except (OSError, ValueError) as err:  # nothing to act on this poll; the next one tries again
            brake.once.failed("memory", f"brake: can't read memory: {err}")
        else:
            brake.once.cleared("memory", "brake: memory reads again")
            brake.poll(mem)
        if start_check is not None:
            start_check.ask()
        if once:
            return
        pause = registry.brake.poll_ms / 1000
        sleep(pause)
        brake.clock += pause


def release(state_dir: Path) -> int:
    try:
        released = release_hold(state_dir)
    except OSError as err:  # say what stands in the way, not a traceback
        if isinstance(err, PermissionError) and not os.access(state_dir, os.W_OK | os.X_OK):
            why = f"this account can't write {state_dir}; spark-admin can"
        else:
            why = str(err)
        print(f"brake: can't release the hold: {why}", file=sys.stderr)
        return 1
    print("brake: hold released" if released else "brake: no hold to release")
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("brake", help="watch memory; unload models before the box freezes")
    p.add_argument("--once", action="store_true", help="one check, then exit")
    p.add_argument("--release", action="store_true", help="clear the hold so loads can start again")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if args.release:
        return release(paths.STATE)

    def log(line: str) -> None:
        print(line, flush=True)

    try:
        registry = load_registry(paths.REGISTRY)
    except Exception as err:  # whatever stops the registry loading, the brake must still run
        log(f"brake: ALERT — the registry {paths.REGISTRY} didn't load ({err}). Braking on the plan's thresholds "
            f"(warn below {FALLBACK.warn_gib:g} GiB, brake below {FALLBACK.brake_gib:g} GiB, every {FALLBACK.poll_ms} "
            "ms) with no model table: models go in the order /running lists them")
        registry = _NoRegistry()
    # 2 s, not the default 10: a hung llama-swap must not hold a tick that acts on memory it just read.
    client = LlamaSwap(paths.LLAMASWAP_URL, key_from_env(args.key_env), timeout=2)
    # Only the brake that keeps running checks its key: a --once run, such as a drill's with another key, would
    # replace the unit's record with its own.
    start_check = None if args.once else StartCheck(client, paths.STATE, args.key_env, log)
    run_brake(registry, client, paths.STATE, log=log, once=args.once, start_check=start_check)
    return 0
