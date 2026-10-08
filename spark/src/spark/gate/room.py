"""make-room and its hold, release, Dan's load and unload, the residents' preload, and the brake's release (Phase 2a;
plan.md, *Admission and memory rules* 4, 5 and 9, *Draining a model*, and *What you see in Phase 2a*).

**make-room** (rule 4). `plan` lists every loaded model it could unload, residents included, largest first, each
marked with its pin, the live sessions using it, its requests in flight and how long the oldest has run, and its idle
time. It works out what must unload so that the target is *free for a load*, the gate's own figure (rule 9's, the
admitter's for Dan), never MemAvailable less the reserve (the controller's ruling at Task 6). Nothing unloads until Dan
confirms: `execute` takes the plan by its id, once. It sets make-room's hold, and saves it, before the first drain, so
the room each unload frees is never open to `agent`'s requests or the gate's reloads, which count the hold. Then it
drains each model in the plan's order through Task 16's drainer, so no request is cut off, yielding a line as each
drain begins and the drainer's own lines as they come, then its result. A pin on a model it unloads ends, and a
session using it loses it: each is named in the result's `ended` and in `spark status`'s *recent*. A drain that ends
with its model still loaded ends the run with a refusal line in Dan's words. With something already unloaded, the hold
stays and the line says what was unloaded; with nothing, the hold that stood before is put back (the controller's
ruling at Task 17). A caller that goes is the same: a run whose drain under way then ends with its model loaded, or
that hadn't begun one, or that went during the first save, puts back the earlier hold; one that unloaded something
keeps its hold (the controller's rulings at Task 17's review and re-review). One run at a time: a second, while one
runs, is refused at once. The hold's size, once the run ends, is the target or what is free for a load then, whichever
is less; for everything, all of what is free for a load then.

**The hold's end.** `release(room=True)` (`spark make-room --done`), `expire` (its time) and `at_start` (a reboot, or a
start fresh after damage, when one may have stood) end it with `room_hold_ended`. The residents it unloaded reload
through admission (Task 15): ahead of every request, one at a time, every other hold counted. Dan's own loads using it
up end it in admission.

**The brake's hold** (rule 5). `brake_release_due` decides, and `check_brake`, which the core calls every 5 s with when
memory last rose above the warn line, acts. The hold lifts by itself only once memory has stayed above the warn line for
RELEASE_AFTER_S, counted from no earlier than the brake fired (one can fire above it, on the rate of fall) and no
earlier than the gate first saw the hold (the brake's time may read an hour early across a clock going back), and only
if the residents it would reload fit by rule 9's formula, every hold counted. At most one automatic release in
AUTO_RELEASE_EVERY_S; never a hold from another boot, which waits for Dan with one `brake_needs_release`; never
reloading the model that was loading when it fired, or any model the brake marked. The release is saved before the
hold file goes, and removes only the hold it judged, so a newer brake's hold stands. `release(brake=True)` is `make
brake-release`, which lifts whatever stands.

**The residents.** `Preloader.run` waits for llama-swap, then queues the residents' reloads in one step, so they load
one at a time, in order, ahead of any request; never a model the brake marked or the standing hold was loading, nor a
resident Dan unloaded with `spark unload` this boot. `resident_left` is the residents' rule for one that left
`/running` (Tasks 17 and 18; the controller's rulings at Task 17 and its review): not while the brake's hold stands
(its release reloads it); not after the gate's own unload that was Dan's choice (`spark unload`, make-room) or the idle
policy's; not for a start that ended by itself, last shown `starting` with no unload of the gate's, whose `load_failed`
already told Dan; but after an `unknown` unload (after damage) or a start the gate aborted (`late_start`), and after a
crash or an earlyoom kill. Those reload at most once in RESIDENT_RELOAD_EVERY_S each, and llama-swap's unplanned
restarts count in the same bound; a second within it sends `resident_waiting` saying the model won't reload by itself,
until Dan's `spark load` clears the bound."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from spark import budget, messages
from spark.budget import ALL, Candidate, RoomPlan
from spark.gate.admission import RELOAD_AFTER, AdmitRequest, Admitted, Admitter
from spark.gate.policy import SAVE_BOUND_S, DrainEnd, Drainer, FrontChannel
from spark.gate.state import Emit, GateState, ModelRecord, RecentRecord, RoomHold, Session
from spark.gateproto import LOAD_CALL_TIMEOUT_S, RELEASE_AFTER_S, RESIDENT_RELOAD_EVERY_S, InflightSnapshot
from spark.hold import UNREADABLE_SINCE, Hold, release_waits_for_dan
from spark.llamaswap import LlamaSwapError
from spark.memory import MemInfo
from spark.registry import Registry

_log = logging.getLogger(__name__)

PLANS_KEPT = 8  # the newest plans `execute` may take; each is used once
PROBE_FIRST_S, PROBE_MAX_S = 1.0, 30.0  # the preload's wait for llama-swap: 1, 2, 4 … s, up to 30
Due = Literal["release", "wait", "needs_dan"]
# The gate's own unloads after which a resident reloads by the residents' rule: one after damage, and a start it
# aborted (the controller's ruling at Task 17). Dan's `spark unload` and make-room's were his choice, and the idle
# policy never unloads a resident.
_RELOADS_AFTER = ("unknown", "late_start")


@dataclass(frozen=True)
class RoomResult:
    """make-room's result, as /v1/make-room's last line (gateproto.MakeRoomAnswer): what it unloaded, by label; what is
    free for a load and held after it; the hold's end; and the pins and sessions it ended, a phrase each."""

    unloaded: list[str]
    free_gib: float
    hold_gib: float
    until: float | None
    ended: list[str]


@dataclass(frozen=True)
class RoomDone:
    """The fields `room_done`'s words need (gateproto.RoomReleased): what the hold had left unused, the size it was set
    at, and the on-demand model that loads on its next request."""

    unused_gib: float
    total_gib: float
    next_label: str | None


@dataclass(frozen=True)
class ReleaseResult:
    """/v1/release's answer (gateproto.ReleaseAnswer): make-room's hold ended, if asked and one stood; whether the
    brake's hold was lifted; and the labels of what reloads, one at a time."""

    room: RoomDone | None
    brake: bool
    reloading: list[str]


@dataclass(frozen=True)
class _Plan:
    plan: RoomPlan
    target: Decimal | Any  # ALL, or the GiB asked for
    uid: int
    at: float


@dataclass
class _Run:
    """One make-room run: the caller, its hold and the one before, and what its drains unloaded and ended so far."""

    uid: int
    hold: RoomHold
    before: RoomHold | None
    target: Any  # ALL, or the GiB asked for
    labels: list[str] = field(default_factory=list)
    ended: list[str] = field(default_factory=list)
    recorded: set[str] = field(default_factory=set)  # the models whose drain has been counted
    drain: asyncio.Task | None = None  # the drain under way, or the last one
    current: tuple[str, list] | None = None  # that drain's model and its ends
    finished: bool = False  # the run reached its result or its refusal
    abandoned: bool = False  # its caller went before then
    settled: bool = False  # its hold was settled after it ended short


def _gib(value: float | Decimal) -> Decimal:
    """`value` exactly: a Decimal as it is, a float or an int as its repr reads, as budget's are."""
    return value if isinstance(value, Decimal) else Decimal(repr(value))


def _refusal(message: str) -> dict:
    """A refusal sent as a stream's line (gateproto.GateRefusalBody): the gate's own, with no code."""
    return {"message": message, "code": None}


def fired_at(hold: Hold) -> float | None:
    """When the brake fired, from the hold's own time (local, as the brake writes it); None when it doesn't parse."""
    try:
        return datetime.fromisoformat(hold.since).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def brake_release_due(now: float, above_since: float | None, hold: Hold, state: GateState, *, reloads_fit: bool,
                      boot_id: str, seen_at: float | None = None) -> Due:
    """Rule 5, for the brake's hold `hold`. `needs_dan` when hold.release_waits_for_dan says so (a hold from another
    boot, Phase 1's and an unreadable one's among them, or one that fired within AUTO_RELEASE_EVERY_S of the last
    automatic release), or when its time doesn't parse, since nothing can be counted from it; `release` only when
    memory has been above the warn line since `above_since` (None: not above it now) for RELEASE_AFTER_S, counted from
    no earlier than the brake fired and no earlier than `seen_at`, when the gate first saw the hold (the controller's
    ruling at Task 17's review: a time with no zone reads an hour early across a clock going back), and the reloads
    fit; else `wait`."""
    if release_waits_for_dan(hold, boot_id=boot_id, last_auto_release_at=state.last_auto_release_at, now=now):
        return "needs_dan"
    fired = fired_at(hold)
    if fired is None:
        return "needs_dan"
    if above_since is None or not reloads_fit:
        return "wait"
    since = max(above_since, min(fired, now))  # one ahead of now (a clock set back) counts from now
    if seen_at is not None:
        since = max(since, min(seen_at, now))
    return "release" if now - since >= RELEASE_AFTER_S else "wait"


def kept_out(name: str, state: GateState, registry: Registry, hold: Hold | None) -> str | None:
    """Why the gate's own reload leaves `name` out, or None: a model the registry doesn't have; the model the brake
    marked, or the one `hold` (the brake's, standing or last seen) was loading when it fired, which loads only when Dan
    asks (rule 5; its mark may not be read in yet); or a resident Dan unloaded with `spark unload` this boot, until his
    `spark load` (the controller's rulings at Task 17's review)."""
    if name not in registry.models:
        return "the registry has no such model"
    if name in state.brake_marks or (hold is not None and hold.loading == name):
        return "it was loading when the brake fired, so it loads only when Dan asks"
    if state.dans_unloads.get(name) == state.boot_id:
        return "Dan unloaded it with `spark unload`, so it loads only when he asks"
    return None


def backoff(n: int | None = None) -> Iterator[float]:
    """The preload's waits for llama-swap: 1, 2, 4 … s, then 30 s each; `n` of them, or for ever."""
    delay, given = PROBE_FIRST_S, 0
    while n is None or given < n:
        yield delay
        given += 1
        delay = min(PROBE_MAX_S, delay * 2)


async def _lines_until(lines: asyncio.Queue, task: asyncio.Future) -> AsyncIterator[dict]:
    """Each line put on `lines` as it comes, until `task` is done and every line has gone. A caller that goes ends
    this iterator, never `task`."""
    while not task.done() or not lines.empty():
        if not lines.empty():
            yield lines.get_nowait()
            continue
        line = asyncio.ensure_future(lines.get())
        try:
            await asyncio.wait([task, line], return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not line.done():
                line.cancel()
        if line.done() and not line.cancelled():
            yield line.result()


class Preloader:
    """The residents' preload (plan.md, *Admission and memory rules* 4 and 5; Task 18 runs it): at start, after a
    hold ends, after apply's restart, after llama-swap restarted unannounced, and for a resident that left `/running`
    by itself. `admitter` is Task 15's, whose `reload` queues each ahead of every request, every hold counted, with no
    deadline; a resident that doesn't fit waits in its queue, shows as waiting, and sends `resident_waiting` once,
    worded by `after` (the admitter's own notification). `emit` is the Notifier, for the one `resident_waiting` of a
    second unplanned restart within the hour. Beside the plan's interface: `llamaswap` is the gate's AsyncLlamaSwap,
    whose `/running` the preload waits on; `read_hold` reads the brake's hold, whose `loading` model is left out;
    `on_change` is called when the preload changes the state (the residents' reload times), so the core saves it;
    `sleep` waits that long (the tests' own). `registry` is the admitter's, as last read."""

    def __init__(self, admitter: Admitter, registry: Registry, emit: Emit, clock: Callable[[], float], *,
                 llamaswap: Any, read_hold: Callable[[], Hold | None],
                 on_change: Callable[[], Awaitable[None] | None] | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._admitter = admitter
        self._emitter = emit
        self._clock = clock
        self._swap = llamaswap
        self._read_hold = read_hold
        self._on_change = on_change or (lambda: None)
        self._sleep = sleep
        self._said: set[str] = set()
        self._tasks: set[asyncio.Future] = set()

    @property
    def registry(self) -> Registry:
        return self._admitter.registry

    async def run(self, models: Iterable[str], after: str) -> dict[str, Admitted | messages.Refusal]:
        """Waits for llama-swap to answer (`/running`), backing off 1, 2, 4 … s up to 30 s; then queues each of
        `models` in one step, so they load one at a time in this order, and returns what each came to. Left out
        (kept_out): a model the brake marked, or the standing hold was loading, whose reload would wait in the queue
        for ever, and a resident Dan unloaded this boot. After an unplanned restart (`after: "restart"`), the hourly
        bound counts it: a resident reloaded by itself within RESIDENT_RELOAD_EVERY_S is left for Dan's `spark load`,
        and one `resident_waiting` names them; each reloaded is stamped. Boot and apply's planned restart reload as
        normal. Cancelling this ends only its wait: the reloads go on."""
        if after not in RELOAD_AFTER:
            raise ValueError(f"a reload's after is one of {', '.join(RELOAD_AFTER)}, not {after!r}")
        await self._wait_for_llama_swap()
        state = self._admitter.state
        try:
            hold = self._read_hold()
        except Exception as err:  # a hold that can't be read stands, but names no loading model
            _log.warning("the brake's hold can't be read (%s)", err)
            hold = None
        names: list[str] = []
        for name in dict.fromkeys(models):
            why = kept_out(name, state, self.registry, hold)
            if why is None:
                names.append(name)
            else:
                _log.info("not reloading %s: %s", name, why)
        if after == "restart":
            names = self._restart_bound(names)
        futures = {name: self._admitter.reload(name, after) for name in names}
        results: dict[str, Admitted | messages.Refusal] = {}
        for name, future in futures.items():
            try:
                results[name] = await asyncio.shield(future)
            except asyncio.CancelledError:
                if not future.cancelled():  # this wait was cancelled, not the reload, which goes on
                    raise
            except Exception as err:  # the registry read again without it, say
                _log.warning("the reload of %s came to nothing: %s", name, err)
        return results

    def _restart_bound(self, names: list[str]) -> list[str]:
        """An unplanned restart's reloads, under the residents' hourly bound (the controller's ruling at Task 17's
        review): those reloaded by themselves within the hour wait for Dan, in one `resident_waiting`."""
        state = self._admitter.state
        now = self._clock()
        # One already loaded or loading (a request beat the probe) isn't reloaded by this, so it is neither named nor
        # stamped (the controller's ruling at Task 17's re-review).
        out = [name for name in names if name not in state.models]
        held = [name for name in out if (last := state.resident_reloads.get(name)) is not None
                and now - last < RESIDENT_RELOAD_EVERY_S]
        go = [name for name in names if name not in held]
        for name in out:
            if name not in held:
                state.resident_reloads[name] = now
        if held:
            key = f"resident-again:restart:{max(state.resident_reloads[name] for name in held)}"
            _log.warning("llama-swap restarted unplanned, and %s already reloaded by itself within the hour: each "
                         "waits for Dan's spark load", ", ".join(held))
            if key not in self._said:
                self._said.add(key)
                known = [self.registry.models[name] for name in held]
                try:
                    self._emitter.emit("resident_waiting", key, after="restart", again=True,
                                       labels=[model.label for model in known],
                                       commands=[model.roles[0] if model.roles else model.name for model in known])
                except ValueError as err:
                    _log.error("the resident_waiting notification couldn't be made: %s", err)
                except Exception:
                    _log.exception("the resident_waiting notification couldn't be sent")
        if out:
            try:
                step = self._on_change()
            except Exception:
                _log.exception("saving the gate's state after the preload changed it failed")
            else:
                if inspect.isawaitable(step):
                    saving = asyncio.ensure_future(step)
                    self._tasks.add(saving)
                    saving.add_done_callback(self._tasks.discard)
        return go

    async def _wait_for_llama_swap(self) -> None:
        waits = backoff()
        while True:
            try:
                await self._swap.running()
                return
            except LlamaSwapError as err:
                delay = next(waits)
                _log.info("the residents' preload waits for llama-swap (%s); asking again in %s s", err, delay)
                await self._sleep(delay)


class Room:
    """make-room, its hold and release, Dan's load and unload, the brake's release, and the residents' rule. `state`
    is the gate's, and `registry` the admitter's, as last read (the argument is the plan's interface's); `admitter` is
    Task 15's and `drainer` Task 16's; `emit` the Notifier; `clock` gives Unix seconds. `read_mem` reads MemAvailable;
    `read_hold` the brake's hold, fresh, on every call; `release_hold(expected)` removes it, only if it is still
    `expected`, or whatever stands when `expected` is None (hold.release_hold). `boot_id` is this boot's. Beside the
    plan's interface: `front` is the FrontChannel, whose snapshot gives each model's requests in flight; `live` is
    Sessions.live, so a session that has ended marks nothing; `on_change` is called after every change to the state,
    so the core saves it, and may return an awaitable, awaited where a save must come first (make-room's hold before
    its first drain, the brake's release before its hold file goes, and a release rolled back), bounded by
    SAVE_BOUND_S."""

    def __init__(self, state: GateState, registry: Registry, admitter: Admitter, drainer: Drainer, emit: Emit,
                 clock: Callable[[], float], *, read_mem: Callable[[], MemInfo], read_hold: Callable[[], Hold | None],
                 release_hold: Callable[[Hold | None], bool], boot_id: str, front: FrontChannel | None = None,
                 live: Callable[[Session], bool] | None = None,
                 on_change: Callable[[], Awaitable[None] | None] | None = None):
        self.state = state
        self._admitter = admitter
        self._drainer = drainer
        self._emitter = emit
        self._clock = clock
        self._read_mem = read_mem
        self._read_hold = read_hold
        self._release_hold = release_hold
        self._boot_id = boot_id
        self._front = front
        self._live = live or (lambda session: True)
        self._on_change = on_change or (lambda: None)
        self._plans: dict[str, _Plan] = {}
        self._loads = 0
        # The residents that left /running while the brake's hold stood: its release reloads them with its own.
        self._left_while_held: set[str] = set()
        self._said: set[str] = set()  # the notifications sent once each, by their event key
        self._undated_logged = False
        self._last_hold: Hold | None = None  # the brake's hold as last read standing, and parsed
        self._recovered: Hold | None = None  # a hold the gate's start put back (hold.recover_release)
        self._run: _Run | None = None  # the make-room run under way, or the last
        self._tasks: set[asyncio.Future] = set()

    @property
    def registry(self) -> Registry:
        return self._admitter.registry

    # make-room.

    def plan(self, target: Decimal | Any, uid: int) -> tuple[str, RoomPlan]:
        """A plan id and Task 5's RoomPlan, as `/v1/make-room/plan` answers them: every model loaded and serving, each
        marked, the largest first, and what must unload so that `target` GiB (or ALL) is free for a load, starting
        from the admitter's figure for Dan. Nothing unloads until `execute`."""
        if target is not ALL:
            if isinstance(target, bool) or not isinstance(target, (Decimal, int, float)):
                raise ValueError("make-room's size is a number of GiB, or ALL")
            target = _gib(target)
            if not target.is_finite() or target <= 0:
                raise ValueError("make-room's size has to be a finite number of GiB above 0")
        now = self._clock()
        free_now = self._admitter.free_for_a_load(uses_hold=self._dans_uses_hold())
        snapshot = self._snapshot()
        candidates = [self._candidate(name, record, now, snapshot) for name, record in self.state.models.items()
                      if record.state == "ready" and record.unload_requested_at is None]
        plan = budget.make_room_plan(target, free_now, candidates)
        plan_id = secrets.token_hex(8)
        self._plans[plan_id] = _Plan(plan, target, uid, now)
        for old in list(self._plans)[:-PLANS_KEPT]:
            del self._plans[old]
        return plan_id, plan

    async def execute(self, plan_id: str, for_s: float | None, uid: int) -> AsyncIterator[dict]:
        """Carries out the plan `plan_id`, once: the hold set and saved first, then a drain for each model in order, a
        gateproto.MakeRoomProgress as each begins and the drainer's StoppingProgress and AfterLoadProgress lines as
        they come; then RoomResult's fields. A refusal on the way is the last line (GateRefusalBody). `for_s` ends the
        hold that long from now; None, at `--done` or the next boot. A caller that goes ends this iterator: the drain
        under way goes on, and what it unloads is held and recorded; the run is then settled as a refused one, so with
        nothing unloaded the earlier hold is put back, also when the caller goes during the first save (the
        controller's rulings at Task 17's review and re-review). One run at a time: a second, while one runs, is
        refused at once, its plan left for later."""
        if self._run is not None and not (self._run.finished or self._run.settled):
            # One run at a time (the controller's ruling at Task 17's re-review).
            yield _refusal(messages.room_running())
            return
        entry = self._plans.pop(plan_id, None) if isinstance(plan_id, str) else None
        if entry is None:
            yield _refusal(messages.room_plan_gone())
            return
        if for_s is not None and not (isinstance(for_s, (int, float)) and not isinstance(for_s, bool)
                                      and math.isfinite(for_s) and for_s > 0):
            yield _refusal(messages.room_for_s())
            return
        now = self._clock()
        plan, target = entry.plan, entry.target
        before = self.state.room_hold
        carried = list(before.unloaded) if before is not None and before.boot_id == self._boot_id else []
        size = float(target if target is not ALL and plan.enough else
                     plan.most_gib if target is ALL else min(target, plan.most_gib))
        hold = RoomHold(size_gib=max(0.0, size), until=None if for_s is None else now + float(for_s),
                        boot_id=self._boot_id, created_at=now, unloaded=carried, total_gib=max(0.0, size))
        self.state.room_hold = hold
        run = self._run = _Run(uid, hold, before, target)
        try:
            # Inside the try, so a caller that goes while this save is under way leaves the earlier hold (the
            # controller's ruling at Task 17's re-review).
            if not await self._saved_first():
                _log.error("the gate couldn't save make-room's hold before its first drain, so nothing was unloaded")
                run.finished = True
                if self.state.room_hold is hold:
                    self.state.room_hold = before
                yield _refusal(messages.room_not_saved(had_hold=before is not None))
                return
            self._admitter.changed()  # the hold counts from now, before any room is freed
            for name in plan.unload:
                if name not in self.state.models:
                    continue  # gone since the plan: its room is free already
                label = self._label(name)
                yield {"kind": "unloading", "model": name, "label": label, "inflight": self._inflight(name)}
                lines: asyncio.Queue = asyncio.Queue()
                ends: list[DrainEnd] = []
                drain = self._spawn(self._drainer.drain(name, "make-room", progress=lines.put_nowait,
                                                        ended=ends.append))
                run.drain, run.current = drain, (name, ends)
                drain.add_done_callback(
                    lambda task, name=name, ends=ends: self._room_drain_done(task, name, ends, run))
                async for line in _lines_until(lines, drain):
                    yield line
                self._room_record(run, name, ends, drain)  # inline too, so the result never misses it
                if not self._drained(drain):
                    run.finished = True
                    yield self._room_refused(run, ends, label)
                    return
            run.finished = True
            free_after = self._admitter.free_for_a_load(uses_hold=True)
            self._resize(run, free_after)
            self._note()
            self._admitter.changed()
            yield asdict(RoomResult(run.labels, float(free_after), hold.size_gib, hold.until, run.ended))
        finally:
            if not run.finished:
                # The caller went (Dan's Ctrl-C), or the gate is stopping: the drain under way goes on, and its
                # callback settles the run when it ends; with none under way, it is settled now.
                run.abandoned = True
                if run.drain is None or run.drain.done():
                    if run.current is not None:  # a drain that ended as the caller went: counted first
                        self._room_record(run, *run.current, run.drain)
                    self._settle(run)

    def _room_drain_done(self, task: asyncio.Future, name: str, ends: list[DrainEnd], run: _Run) -> None:
        """A make-room drain's end, whether or not its caller is still there: its model counted, and, for a run whose
        caller went, the run settled."""
        self._room_record(run, name, ends, task)
        if run.abandoned:
            self._settle(run)

    def _room_record(self, run: _Run, name: str, ends: list[DrainEnd], task: asyncio.Future) -> None:
        """A make-room drain that ended with its model gone, counted once: on the hold that stands, its pin ended and
        its sessions told, in *recent* (rule 4: `spark status` says whose they were). The hold that stands may not be
        this run's (another replaced it), and its --done then reloads it; with none standing, a resident reloads now,
        as the hold's end would have (the controller's ruling at Task 17's review)."""
        if name in run.recorded or not task.done() or not self._succeeded(task):
            return
        if not ends or ends[-1] not in ("unloaded", "gone"):
            return
        run.recorded.add(name)
        label = self._label(name)
        run.labels.append(label)
        current = self.state.room_hold
        if current is not None and current.boot_id == self._boot_id:
            if name not in current.unloaded:
                current.unloaded.append(name)
        elif name in self.registry.models and self.registry.models[name].resident:
            self._reload([name], "hold")
        run.ended += self._ends_with_unload(name, label, run.uid, by="make-room")
        self._note()

    def _ends_with_unload(self, name: str, label: str, uid: int, *, by: str) -> list[str]:
        """A model `by` unloaded: its pin ends, and each live session using it loses it, both in *recent*, by whose they
        were; the phrases for make-room's `ended`."""
        now = self._clock()
        pin = self.state.pins.pop(name, None)
        pinned = pin is not None and (pin.until is None or pin.until > now)
        sessions = [session for session in self.state.sessions.values()
                    if session.model == name and self._is_live(session)]
        if not pinned and not sessions:
            return []
        labels = [session.label for session in sessions]
        uids = [uid, *([pin.by_uid] if pinned else []), *(session.uid for session in sessions)]
        self.state.recent.append(RecentRecord(at=now, kind=by, model=name, uids=list(dict.fromkeys(uids)),
                                              text=messages.room_ended(label, pinned=pinned, sessions=labels, by=by)))
        return messages.room_ended_parts(label, pinned=pinned, sessions=labels)

    def _settle(self, run: _Run) -> None:
        """A run that ended short, refused or its caller gone (the controller's rulings at Task 17 and its review): with
        nothing unloaded, the hold that stood before is put back; with something, the hold stays, sized as a full run's,
        so it holds the room freed. Nothing, if another hold has replaced this run's."""
        if run.settled:
            return
        run.settled = True
        if self.state.room_hold is not run.hold:
            return
        if not run.labels:
            self.state.room_hold = run.before
        else:
            try:
                self._resize(run, self._admitter.free_for_a_load(uses_hold=True))
            except Exception as err:  # MemAvailable unread: the hold stays as set, which errs safe
                _log.warning("make-room's hold keeps its size: what is free for a load can't be read (%s)", err)
        self._note()
        self._admitter.changed()

    def _resize(self, run: _Run, free_after: Decimal) -> None:
        """The run's hold, once its unloads are done: the target or what is free for a load, whichever is less; for
        ALL, every GiB free for a load (the controller's rulings at Task 17's review)."""
        if self.state.room_hold is not run.hold:
            return
        room = max(Decimal(0), free_after)
        run.hold.size_gib = run.hold.total_gib = float(room if run.target is ALL else min(run.target, room))

    def _room_refused(self, run: _Run, ends: list[DrainEnd], label: str) -> dict:
        """A drain that left its model loaded ends the run: settled, and the line says what was unloaded, if anything,
        and that the room freed so far stays held."""
        refused = self._not_unloaded(ends, label)
        self._settle(run)
        if run.labels:
            return _refusal(messages.room_partly_held(run.labels, refused, run.hold.until))
        return _refusal(refused)

    def expire(self, now: float) -> RoomHold | None:
        """make-room's hold whose `until` has come ends (`room_hold_ended`, *its time ran out*), and the residents it
        unloaded reload. Returns the hold it ended, if any."""
        hold = self.state.room_hold
        if hold is None or hold.until is None or hold.until > now:
            return None
        self.state.room_hold = None
        self._hold_ended(hold, "time")
        return hold

    def at_start(self, ended: RoomHold | None, *, recovered: Hold | None = None) -> None:
        """At the gate's start, after restore (Task 18 calls it): `room_hold_ended` for the hold another boot left,
        which restore ended (`reboot`; the boot's preload reloads the residents), and, at a start fresh after damage,
        once, for a hold that may have stood (`damaged`: the controller's ruling at Task 13's review). `recovered` is
        the brake's hold hold.recover_release put back: should it wait for Dan, `brake_needs_release` says so, once,
        so it is never silent (the controller's ruling at Task 17's re-review)."""
        self._recovered = recovered
        if ended is not None:
            total = ended.total_gib if ended.total_gib is not None else ended.size_gib
            self._emit_once("room_hold_ended", f"room:{ended.created_at}", why="reboot", unused_gib=ended.size_gib,
                            total_gib=total, reloading=[], next_label=None)
        if self.state.fresh_after_damage:
            self._emit_once("room_hold_ended", f"room:damaged:{self.state.damaged_at}", why="damaged")

    def resumed_drain_ended(self, model: str, why: str | None, end: str) -> None:
        """A make-room drain a restarted gate resumed (Task 18 passes each resumed drain's end): its model, once gone,
        goes on the room hold that stands, so its `--done` reloads it, as the run killed with the gate would have
        recorded it (the controller's ruling at Task 17's re-review). Any other drain changes nothing here."""
        current = self.state.room_hold
        if why != "make-room" or end not in ("unloaded", "gone") or current is None:
            return
        if current.boot_id == self._boot_id and model not in current.unloaded:
            current.unloaded.append(model)
            self._note()

    async def release(self, uid: int, *, room: bool, brake: bool) -> ReleaseResult:
        """`spark make-room --done` asks `room`: make-room's hold ends (`room_hold_ended`, *`--done`*), and the
        residents it unloaded reload. `make brake-release` asks `brake`: the brake's hold, whatever stands, is lifted,
        and the residents it unloaded reload, never the model that was loading when it fired. Each only what it is
        asked; the CLI words each (`room_done`, `brake_released_by_dan`) from the answer."""
        done: RoomDone | None = None
        reloading: list[str] = []
        if room and self.state.room_hold is not None:
            hold, self.state.room_hold = self.state.room_hold, None
            done, labels = self._hold_ended(hold, "--done")
            reloading += labels
        lifted = False
        if brake:
            hold = self._hold()
            if hold is not None:
                try:
                    lifted = bool(self._release_hold(None))
                except OSError as err:
                    _log.error("the brake's hold couldn't be lifted (%s); it stands", err)
            if lifted:
                names = self._brake_reloads(hold)
                self._left_while_held.clear()
                labels = self._reload(names, "brake")
                reloading += [label for label in labels if label not in reloading]
                # A system event: empty uids, so every caller sees it (the controller's ruling at Task 17's review).
                self.state.recent.append(RecentRecord(at=self._clock(), kind="brake_released", model=None, uids=[],
                                                      text=messages.recent_brake_released(labels, by_dan=True)))
                self._note()
                self._admitter.changed()
        return ReleaseResult(done, lifted, reloading)

    def _hold_ended(self, hold: RoomHold, why: str) -> tuple[RoomDone, list[str]]:
        loaded = set(self.state.models)
        unloaded = [name for name in dict.fromkeys(hold.unloaded)
                    if name in self.registry.models and name not in loaded]
        residents = [name for name in unloaded
                     if self.registry.models[name].resident and name not in self.state.brake_marks]
        next_label = next((self._label(name) for name in unloaded if not self.registry.models[name].resident), None)
        total = hold.total_gib if hold.total_gib is not None else hold.size_gib
        self._note()
        self._admitter.changed()
        labels = self._reload(residents, "hold")
        self._emit("room_hold_ended", f"room:{hold.created_at}", why=why, unused_gib=hold.size_gib, total_gib=total,
                   reloading=labels, next_label=next_label)
        return RoomDone(hold.size_gib, total, next_label), labels

    # Dan's load and unload.

    async def load(self, model: str, uid: int) -> AsyncIterator[dict]:
        """`spark load`: admission as one of Dan's commands (`kind: "command"`, held up to LOAD_CALL_TIMEOUT_S), into
        his make-room hold if there is one. A WaitProgress as it starts waiting and whenever its reason changes, a
        LoadProgress as its load starts (none for a model already loaded), then gateproto.LoadConfirmation's fields; a
        refusal is the last line. It clears the residents' bound for `model`, and the mark of Dan's own `spark
        unload`. A caller that goes takes its request out of the queue; a load already started goes on."""
        if model not in self.registry.models:
            yield _refusal(messages.no_such_model(str(model), "load"))
            return
        self._loads += 1
        lines: asyncio.Queue = asyncio.Queue()
        req = AdmitRequest(id=f"load:{model}:{self._loads}", model=model, kind="command", key=None,
                           deadline=self._clock() + LOAD_CALL_TIMEOUT_S, uid=uid)
        admit = asyncio.ensure_future(self._admitter.admit(req, progress=lines.put_nowait))
        try:
            async for line in _lines_until(lines, admit):
                yield line
            result = admit.result()
        finally:
            if not admit.done():
                admit.cancel()
        if isinstance(result, messages.Refusal):
            yield {"message": result.message, "code": result.code}
            return
        cleared = self.state.resident_reloads.pop(model, None) is not None
        cleared = self.state.dans_unloads.pop(model, None) is not None or cleared
        if cleared:  # Dan's own load clears the residents' bound and his own unload's mark
            self._note()
        known = self.registry.models[model]
        yield {"label": known.label, "seconds": result.seconds if result.loaded_now else None,
               "idle_min": None if known.resident else self.registry.gate.idle_unload_min,
               "command": known.roles[0] if known.roles else model}

    async def unload(self, model: str, uid: int) -> AsyncIterator[dict]:
        """`spark unload`: a drain, why `unload`. gateproto.UnloadCount at once, the drainer's StoppingProgress and
        AfterLoadProgress lines as they come, then UnloadDone once /running shows the model gone; or a refusal line,
        in Dan's words for how the drain ended. A pin on the model ends with it, and its sessions lose it, in *recent*;
        a resident stays unloaded until Dan's `spark load` or the next boot, across a gate restart (the controller's
        rulings at Task 17's review). A caller that goes ends this iterator, never the unload, whose end is recorded
        all the same."""
        if model not in self.registry.models and model not in self.state.models:
            yield _refusal(messages.no_such_model(str(model), "unload"))
            return
        label = self._label(model)
        if model not in self.state.models:
            yield _refusal(messages.not_loaded(label))
            return
        yield {"kind": "unloading", "inflight": self._inflight(model)}
        lines: asyncio.Queue = asyncio.Queue()
        ends: list[DrainEnd] = []
        pinned: list[bool] = []
        drain = self._spawn(self._drainer.drain(model, "unload", progress=lines.put_nowait, ended=ends.append))
        drain.add_done_callback(lambda task: self._unload_done(task, model, label, ends, uid, pinned))
        async for line in _lines_until(lines, drain):
            yield line
        self._unload_done(drain, model, label, ends, uid, pinned)  # inline too, so the answer never misses it
        if self._drained(drain):
            yield {"unloaded": True, "pin_ended": bool(pinned and pinned[0])}
            return
        yield _refusal(self._not_unloaded(ends, label))

    def _unload_done(self, task: asyncio.Future, model: str, label: str, ends: list[DrainEnd], uid: int,
                     pinned: list[bool]) -> None:
        """`spark unload`'s drain, once it ended with its model gone, recorded once: its pin ended, its sessions told,
        and a resident marked as Dan's to load again."""
        if pinned or not task.done() or not self._succeeded(task) or not ends or ends[-1] not in ("unloaded", "gone"):
            return
        pin = self.state.pins.get(model)
        pinned.append(pin is not None and (pin.until is None or pin.until > self._clock()))
        self._ends_with_unload(model, label, uid, by="unload")
        known = self.registry.models.get(model)
        if known is not None and known.resident:
            self.state.dans_unloads[model] = self._boot_id
        self._note()

    # The brake's release (rule 5).

    async def check_brake(self, now: float, *, above_since: float | None) -> Due | None:
        """The brake's release check, which the core runs every 5 s: `above_since` is when MemAvailable last rose above
        the warn line, None while it is under it. None with no hold. `release`: lifted, `brake_released`, and the
        residents it unloaded reload. `needs_dan`: for a hold from another boot, one `brake_needs_release`. The five
        minutes count from no earlier than admission first saw the hold, or than this check when it hasn't. With no
        hold, a resident that left while one stood reloads now, never the model that hold was loading."""
        last = self._last_hold
        hold = self._hold()
        if hold is None:
            if self._left_while_held:  # the hold went some other way: the brake-release fallback, say
                names = self._brake_reloads(Hold("", "", (), loading=last.loading if last is not None else None))
                self._left_while_held.clear()
                self._reload(names, "brake")
            return None
        names = self._brake_reloads(hold)
        seen = self._admitter.hold_seen_at(hold)
        due = brake_release_due(now, above_since, hold, self.state, reloads_fit=self._fits(names),
                                boot_id=self._boot_id, seen_at=now if seen is None else seen)
        if due == "needs_dan":
            self._needs_release(hold)
        elif due == "release" and not await self._auto_release(hold, names, now):
            return "wait"
        return due

    async def _auto_release(self, hold: Hold, names: list[str], now: float) -> bool:
        """The automatic release: saved first, then only the hold judged removed; on either failing, nothing lifts and
        the last automatic release is as it was."""
        before = (self.state.last_auto_release_at, self.state.release_assumed)
        # A real release: the last automatic release is now, no longer assumed (Task 13's re-review).
        self.state.last_auto_release_at, self.state.release_assumed = now, False
        if not await self._saved_first():
            _log.error("the gate couldn't save its state before lifting the brake's hold, so the hold stands")
            self.state.last_auto_release_at, self.state.release_assumed = before
            return False
        try:
            released = bool(self._release_hold(hold))
        except OSError as err:
            _log.error("the brake's hold couldn't be lifted (%s); it stands", err)
            released = False
        if not released:
            _log.warning("the brake's hold the gate judged no longer stands as it was (the brake fired again, or it "
                         "was released meanwhile): nothing lifted")
            self.state.last_auto_release_at, self.state.release_assumed = before
            if not await self._saved_first():  # awaited, so no release that never happened stays recorded unseen
                _log.error("the gate couldn't save its rolled-back release: until its next save, its saved state names "
                           "an automatic release at %s that didn't happen", now)
            return False
        self._left_while_held.clear()
        self._admitter.changed()
        labels = self._reload(names, "brake")
        self.state.recent.append(RecentRecord(at=now, kind="brake_released", model=None, uids=[],
                                              text=messages.recent_brake_released(labels, by_dan=False)))
        self._note()
        reading: dict[str, Any]
        try:
            reading = {"available_gib": self._read_mem().available_gib}
        except OSError as err:  # sent all the same, saying so (the controller's ruling at Task 17's review)
            _log.warning("MemAvailable can't be read (%s): brake_released says so", err)
            reading = {"available_unknown": True}
        episode = hold.episode if hold.episode is not None else hold.since
        self._emit("brake_released", f"release:{hold.boot_id}:{episode}", at=now, reloading=labels,
                   loading_label=self._label(hold.loading) if hold.loading else None, **reading)
        return True

    def _needs_release(self, hold: Hold) -> None:
        """One `brake_needs_release`, high, for a hold from another boot (rule 5: it waits for Dan). A hold of this
        boot that waits for Dan is a brake within the hour after an automatic release, whose own `brake_fired` said so
        (Task 14). Keyed as Task 14 keys the same brake read from its events, so the two are one notification."""
        recovered = self._recovered is not None and hold == self._recovered
        if hold.boot_id is not None and hold.boot_id == self._boot_id and not recovered:
            return
        fired = fired_at(hold)
        if fired is None:  # an unreadable hold: held_by_brake's words and `spark status` say it waits for Dan
            if not self._undated_logged:
                self._undated_logged = True
                _log.warning("the brake's hold can't be dated (%s): it waits for Dan, and no alert names a time it "
                             "lacks", hold.reason)
            return
        key = (f"brake-needs:{hold.boot_id}:{hold.episode if hold.episode is not None else hold.since}"
               if hold.boot_id is not None else f"brake-needs:phase-1:{hold.since}")
        if recovered:
            key = key.replace("brake-needs:", "brake-needs:recovered:", 1)
        self._emit_once("brake_needs_release", key, fired_at=fired, available_gib=hold.available_gib,
                        unloaded=[self._label(name) for name in dict.fromkeys(hold.unloaded)],
                        **({"recovered": True} if recovered else {}))

    def _brake_reloads(self, hold: Hold) -> list[str]:
        """The residents a release reloads, in the registry's order: those the brake unloaded and those that left while
        its hold stood, not loaded now, never the model that was loading when it fired or any the brake marked."""
        named = set(hold.unloaded) | self._left_while_held
        return [name for name, model in self.registry.models.items()
                if name in named and model.resident and name != hold.loading and name not in self.state.brake_marks
                and name not in self.state.models]

    def _fits(self, names: list[str]) -> bool:
        """Whether `names` fit together now, by rule 9's formula with every hold counted, as their reloads count it."""
        if not names:
            return True
        try:
            free = self._admitter.free_for_a_load(uses_hold=False)
        except Exception as err:  # MemAvailable unread: nothing is known to fit
            _log.warning("the brake's release waits: what is free for a load can't be read (%s)", err)
            return False
        return sum((_gib(self.registry.models[name].footprint_gib) for name in names), Decimal(0)) <= free

    # The residents' rule (Tasks 17 and 18; the controller's rulings at Task 17).

    def resident_left(self, model: str, *, shown: str, since: float) -> bool:
        """A resident `/running` listed, as `shown`, in the read at `since` and no longer lists: the core asks this
        once for each. True when its reload was queued. Not while the brake's hold stands: its release reloads it.
        Not after the gate's own unload that was Dan's choice (`unload`, make-room's) or the idle policy's, nor for a
        model the brake marked, nor for a start that ended by itself (last shown `starting`, no unload of the gate's),
        whose load_failed already told Dan (Q1, the controller's ruling at Task 17's review). After an `unknown` unload
        or a start the gate aborted (`after: "gate"`), or a crash (`"crash"`), at most once in RESIDENT_RELOAD_EVERY_S:
        a second within it sends `resident_waiting` saying it won't reload by itself, until Dan's `spark load`."""
        known = self.registry.models.get(model)
        if known is None or not known.resident or model in self.state.brake_marks:
            return False
        if self._hold() is not None:
            self._left_while_held.add(model)
            return False
        why = self._drainer.unloaded_why(model, since)
        if why is not None and why not in _RELOADS_AFTER:
            return False
        if why is None and shown == "starting":
            _log.info("%s's start ended by itself, with no unload of the gate's: its load_failed said so, and the "
                      "residents' rule doesn't reload it", model)
            return False
        after = "gate" if why is not None else "crash"
        _log.info("%s left /running (last shown %s), %s: the residents' rule reloads it", model, shown,
                  f"the gate's own unload ({why})" if why else "with no unload of the gate's or the brake's")
        return self._bounded_reload(model, after)

    def _bounded_reload(self, model: str, after: str) -> bool:
        why = kept_out(model, self.state, self.registry, self._hold())
        if why is not None:  # nothing is queued, so nothing is stamped (the controller's ruling at Task 17's re-review)
            _log.info("not reloading %s: %s", model, why)
            return False
        now = self._clock()
        last = self.state.resident_reloads.get(model)
        if last is not None and now - last < RESIDENT_RELOAD_EVERY_S:
            _log.warning("%s stopped again within an hour of its last automatic reload: it waits for Dan's spark load",
                         model)
            known = self.registry.models[model]
            self._emit_once("resident_waiting", f"resident-again:{model}:{last}", label=known.label, after=after,
                            again=True, command=known.roles[0] if known.roles else model)
            return False
        self.state.resident_reloads[model] = now
        self._note()
        self._reload([model], after)
        return True

    # Small things.

    def _reload(self, names: list[str], after: str) -> list[str]:
        """Each of `names` queued for the gate's own reload, in this order (Task 15), and their labels; none that
        kept_out leaves out, with the brake's hold that stands."""
        labels = []
        hold = self._hold()
        for name in names:
            why = kept_out(name, self.state, self.registry, hold)
            if why is not None:
                _log.info("not reloading %s: %s", name, why)
                continue
            future = self._admitter.reload(name, after)
            future.add_done_callback(self._reloaded)
            labels.append(self._label(name))
        return labels

    @staticmethod
    def _reloaded(future: asyncio.Future) -> None:
        if future.cancelled():
            return
        err = future.exception()
        if err is not None:
            _log.warning("a resident's reload came to nothing: %s", err)
        elif isinstance(future.result(), messages.Refusal):
            _log.info("a resident's reload was refused (%s); admission has said why", future.result().code)

    def _hold(self) -> Hold | None:
        """The brake's hold, read fresh; one that can't be read still holds (hold.read_hold fails closed). The last one
        read standing is kept, for the model it was loading."""
        try:
            hold = self._read_hold()
        except Exception as err:
            _log.warning("the brake's hold can't be read (%s): taken as standing", err)
            hold = Hold(UNREADABLE_SINCE, f"the hold can't be read: {err}", ())
        if hold is not None and hold.since != UNREADABLE_SINCE:  # an unreadable read never replaces the last that
            self._last_hold = hold  # parsed, with its loading model (the controller's ruling at Task 17's re-review)
        return hold

    @staticmethod
    def _succeeded(task: asyncio.Future) -> bool:
        """A drain's result, quietly: True once its model is gone."""
        return not task.cancelled() and task.exception() is None and bool(task.result())

    @staticmethod
    def _not_unloaded(ends: list[DrainEnd], label: str) -> str:
        """Dan's words for how a drain ended with its model still loaded; one with no end it knows reads as broken."""
        try:
            return messages.not_unloaded(ends[-1] if ends else "broke", label)
        except ValueError:
            return messages.not_unloaded("broke", label)

    def _drained(self, task: asyncio.Future) -> bool:
        """A drain's result: True once its model is gone. One that broke is logged, and False."""
        if task.cancelled():
            return False
        if task.exception() is not None:
            _log.error("a drain of make-room's or `spark unload`'s broke", exc_info=task.exception())
            return False
        return bool(task.result())

    def _dans_uses_hold(self) -> bool:
        group = self.registry.key_groups.get("dan")  # Dan's commands take the dan group's privileges (Task 15)
        return True if group is None else group.uses_hold

    def _snapshot(self) -> InflightSnapshot | None:
        return None if self._front is None else self._front.snapshot

    def _inflight(self, name: str) -> int:
        counts = ((self._snapshot() or {}).get("models") or {}).get(name) or {}
        return int(counts.get("count") or 0)

    def _candidate(self, name: str, record: ModelRecord, now: float, snapshot: InflightSnapshot | None) -> Candidate:
        known = self.registry.models.get(name)
        counts = ((snapshot or {}).get("models") or {}).get(name) or {}
        inflight = int(counts.get("count") or 0)
        oldest = counts.get("oldest_started_at")
        last = record.last_use if counts.get("last_end_at") is None else max(record.last_use, counts["last_end_at"])
        pin = self.state.pins.get(name)
        sessions = [session.label for session in self.state.sessions.values()
                    if session.model == name and self._is_live(session)]
        return Candidate(model=name, label=self._label(name), gib=record.footprint_gib,
                         resident=bool(known and known.resident),
                         pinned=pin is not None and (pin.until is None or pin.until > now),
                         session=", ".join(sessions) or None, inflight=inflight,
                         oldest_s=max(0.0, now - oldest) if inflight and oldest is not None else None,
                         idle_min=None if inflight else max(0.0, now - last) / 60)

    def _is_live(self, session: Session) -> bool:
        """A session whose liveness can't be read counts as live, as an idle unload takes it."""
        try:
            return bool(self._live(session))
        except Exception as err:
            _log.warning("session %s can't be checked (%s); it counts as live", session.id, err)
            return True

    def _label(self, name: str) -> str:
        known = self.registry.models.get(name)
        return known.label if known else name

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _emit(self, kind: str, event_key: str, **fields: Any) -> None:
        """A notification, never at the cost of the room's work: a field its words refuse is logged, one plain line."""
        try:
            self._emitter.emit(kind, event_key, **fields)
        except ValueError as err:
            _log.error("the %s notification couldn't be made: %s", kind, err)
        except Exception:
            _log.exception("the %s notification couldn't be sent", kind)

    def _emit_once(self, kind: str, event_key: str, **fields: Any) -> None:
        """Sent once by this gate; the Notifier's own record keeps it once across a restart."""
        if event_key in self._said:
            return
        self._said.add(event_key)
        self._emit(kind, event_key, **fields)

    def _note(self) -> None:
        """A change whose save needn't come first: on_change, its awaitable, if any, left to finish by itself."""
        try:
            step = self._on_change()
        except Exception:
            _log.exception("saving the gate's state after a change in make-room or a release failed")
            return
        if inspect.isawaitable(step):
            saving = asyncio.ensure_future(step)
            self._tasks.add(saving)
            saving.add_done_callback(self._saved)

    def _saved(self, saving: asyncio.Future) -> None:
        self._tasks.discard(saving)
        if not saving.cancelled() and saving.exception() is not None:
            _log.error("saving the gate's state after a change in make-room or a release failed",
                       exc_info=saving.exception())

    async def _saved_first(self) -> bool:
        """on_change, and its awaitable, if any, awaited within SAVE_BOUND_S: a save that must come first. False when it
        raised, failed or didn't end in time."""
        try:
            step = self._on_change()
        except Exception as err:
            _log.warning("saving the gate's state failed: %s", err)
            return False
        if not inspect.isawaitable(step):
            return True
        try:
            async with asyncio.timeout(SAVE_BOUND_S):
                await step
        except TimeoutError:
            return False
        except Exception as err:
            _log.warning("saving the gate's state failed: %s", err)
            return False
        return True
