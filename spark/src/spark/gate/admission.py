"""The gate's admission (Phase 2a; plan.md, *The front and the gate*, *A request that needs a load*, and *Admission and
memory rules* 1, 4, 5, 7 and 9): its queue, one load at a time, the admission ticket each load gets, and every refusal,
in Task 6's words with the gate's own figures.

A request is the front's, for a key (`kind: "key"`, with the key and its deadline); one of Dan's commands (`"command"`:
`spark load`, `spark pin`, with no key); or the gate's own reload of a resident (`"reload"`, Tasks 17 and 18, with
neither, which never times out). A model already ready is admitted at once; one already loading is joined, and its
requests wait for that load, past their key's wait, bounded by the load's own deadline; one whose files aren't under
hf_home is refused `not_downloaded` before any ticket. Every other request queues: the gate's reloads first, then each
by its group's `queue`, lower first (Dan's commands at 0), first come first served within each. When the one-load slot
is free, the first request in that order that may load and fits takes it; one that doesn't fit keeps its place. Each is
rechecked whenever memory, the brake's hold, a load, a drain or the queue changes (`changed()`, which the core calls on
every memory reading), and at least every `poll_s`; at its deadline it is refused with the code for what it was waiting
on, in this order: apply's restart (`restarting`), llama-swap down (`llama_swap_down`), the brake's hold
(`held_by_brake`), its model being unloaded (`draining`), the brake's mark for a group without reloads_marked
(`footprint_suspect`), llama-swap refusing its load each time (`load_failed`, *kept refusing*), a load ticket of its
own model's that the gate couldn't withdraw (`load_failed`, *couldn't confirm*), memory (`no_fit`), and the one-load
slot (`loading`, worded for a ticket the gate is settling when only that holds the slot).

A model fits by rule 9: footprint ≤ min(MemAvailable − reserve − owed, ceiling − committed) − starting − held, with
`held` make-room's hold for a group without uses_hold, and the gate's reloads count it too. *owed* is each loaded
model's footprint less what it holds now: its load's fall, plus its engine's RssAnon growth since only where the
registry's gate.owed_reads_rss is on for its engine kind; a reading it can't take credits no growth. Nothing loads while
a model is still starting, so `starting` counts in the figure a request is refused with, and in make-room's and
status's.

A load: its ticket issued (the footprint rule 9 admitted, MemAvailable then, this boot), its id saved in
`state.issued` before the call, the model recorded `starting`, then `load_started`; the call runs in a task of its own,
which no requester's leaving cancels. Its outcome, settled with the ticket (the controller's rulings at Task 12's
reviews):
- READY: the model is recorded with its load's fall, capped at the registry's cold load plus LOAD_FALL_MARGIN_GIB, else
  at its footprint, and flagged when capped; `loaded`; every request on it admitted.
- A 500 (the start failed): `load_failed` once, its words launch's refusal record for this load, else the engine's last
  line, or the deadline's for `health check timed out`; every request on it refused so. A 500 that says an unload
  aborted the start (`group: model unloaded`, the brake's) or llama-swap is shutting down is no failed start: its
  requests go back to their places, and are worded by that cause.
- Unknown, or llama-swap's own refusal (any status but 500): the ticket is withdrawn first. If it was still there, no
  start used it, and the slot is freed: a 404 or a 401 is `load_failed` at once; a 429 or a 503 sends the requests back
  to their places, to try again within their wait, then `load_failed` (*kept refusing*), never `llama_swap_down` while
  llama-swap answers; nothing answering sends them back to wait for llama-swap. If launch had claimed it, the slot stays
  held until `/running` shows the model ready or gone; one gone without coming ready is a failed start (*stopped before
  it was ready*); one still starting past its own start's deadline (the claimed ticket's `started/` time, plus
  healthCheckTimeout and PAST_THE_DEADLINE_S) is unloaded, `/running` read right before, and counted as a failed load.
  (The controller's rulings at Task 15.)
On every outcome the model's `started/` record is read, then cleared; a ready engine's pid goes into `state.ticketed`
only when the record is this ticket's. A model a restarted gate's `restore` left starting takes the ready-or-gone path.

No ticket stays live past its load (the controller's rulings at Task 15's review and re-review). At construction the
admitter withdraws the ticket of every model it has no restored load for. A withdraw that raises is tried once more;
one that fails again is never read as claimed: a watch holds the slot until `/running` shows the model, then settled
as a start, or until the ticket's deadline has passed and a `/running` read that answered doesn't list it, never
while `/running` can't be read. A watch has no requests of its own: each waits in the queue, on its own blocker, to its
own deadline, and one for the watched model hears the gate couldn't confirm the ticket was withdrawn. A constructor's
watch for a model that already has a record keeps it whole. A ticket that can't be written is the gate's own error,
refused at once. A model `/running` shows starting holds the slot whatever its record reads.

`cancel(request_id)`: a client gone leaves the queue at once, and nothing loads for it; a load already started finishes.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from spark import budget, launch, messages, procs, tickets
from spark.gate.state import Emit, GateState, ModelRecord, RefusalRecord, RoomHold, Ticketed
from spark.gateproto import LOAD_CALL_TIMEOUT_S, RELEASE_AFTER_S, WAITING_WHY
from spark.hold import Hold, release_waits_for_dan
from spark.llamaswap import LlamaSwapAnswered, LlamaSwapError, Running
from spark.llamaswap_async import PAST_THE_DEADLINE_S, AsyncLlamaSwap, LlamaSwapNotSent, LoadOutcome
from spark.memory import MemInfo
from spark.messages import Holder, Moment, Refusal
from spark.registry import ClientKey, Registry
from spark.render import HEALTH_CHECK_TIMEOUT_S

_log = logging.getLogger(__name__)

# A load's fall is capped at the model's measured cold load plus this (this plan's value), and flagged when capped:
# memory that moved elsewhere during the load would otherwise inflate the fall and shrink what the model is owed.
LOAD_FALL_MARGIN_GIB = 2
POLL_S = 1.0  # a waiting request is rechecked, and a load being settled reads /running, at least this often
# A request whose load llama-swap didn't take waits this long before it tries again, doubling to the most.
RETRY_FIRST_S, RETRY_MAX_S = 1.0, 30.0
# llama-swap's refusals of a load that a retry may change, tried again within the request's wait; any other status but
# 500 (a 404, a 401) is refused at once (the controller's ruling at Task 15).
RETRY_STATUSES = (429, 503)
# Why the gate reloads a resident (the controller's ruling at Task 7): resident_waiting's `after`.
RELOAD_AFTER = ("hold", "brake", "apply", "boot", "restart", "crash")
# What a 500 says when it ended no start of the model's own (the controller's ruling at Task 12's re-review): an unload
# aborted it (internal/router/scheduler/fifo.go:230-242), or llama-swap is shutting down (internal/router/base.go:
# 598-602).
ABORTED_BY_UNLOAD = "group: model unloaded"
SHUTTING_DOWN = "group is shutting down"
DEADLINE_PASSED = "health check timed out"  # process_command.go:587-589

Kind = Literal["reload", "command", "key"]
# What a waiting request waits on, in the order the first that applies is its reason, and the refusal each gives.
# swap_refused: llama-swap answered its load with a 429 or a 503, and it waits to try again (Task 15's ruling).
_CODES = {"restart": "restarting", "llama_swap": "llama_swap_down", "brake": "held_by_brake", "draining": "draining",
          "dan": "footprint_suspect", "swap_refused": "load_failed", "memory": "no_fit", "slot": "loading"}
_WAITING_WORDS = ("memory", "brake", "slot", "dan", "draining", "ticket")  # those `waiting`'s words take (Task 7)
# Why a request whose load came to nothing waits before it tries again, and what it then waits on: llama-swap not
# answering or shutting down; llama-swap answering with a refusal; or an unload aborting its start.
_RETRY_WHY = {"llama_swap": "llama_swap", "swap_refused": "swap_refused", "aborted": "draining"}
_UNREAD = object()  # /running couldn't be read


def _gib(value: float | Decimal) -> Decimal:
    """`value` exactly: a Decimal as it is, a float or an int as its repr reads, as budget's and messages' are."""
    return value if isinstance(value, Decimal) else Decimal(repr(value))


@dataclass(frozen=True)
class AdmitRequest:
    """One request for a load. `after`, for a reload only, says why it reloads (resident_waiting's words); `uid`, the
    asker's when the gate knows it, goes into the refusal history."""

    id: str
    model: str
    kind: Kind
    key: ClientKey | None
    deadline: float | None
    after: str | None = None
    uid: int | None = None


@dataclass(frozen=True)
class Privileges:
    """A key group's five settings (Task 4), apart."""

    queue: int
    uses_hold: bool
    reloads_marked: bool
    words: Literal["dan", "agent"]
    names_processes: bool


# The gate's own reloads rank ahead of every group, count every hold, and never load the model the brake marked.
RELOAD = Privileges(queue=-1, uses_hold=False, reloads_marked=False, words="dan", names_processes=True)


@dataclass(frozen=True)
class Admitted:
    model: str
    loaded_now: bool  # False: it was loaded already
    seconds: float | None  # the load's, from its ticket to ready; None for a model loaded already


def privileges(req: AdmitRequest, registry: Registry) -> Privileges:
    """A key's group's; Dan's commands get the `dan` group's; the gate's reloads, RELOAD."""
    if req.kind == "reload":
        return RELOAD
    name = "dan" if req.kind == "command" else req.key.group
    group = registry.key_groups.get(name)
    if group is None:
        raise ValueError(f"the registry has no key group {name!r}")
    return Privileges(group.queue, group.uses_hold, group.reloads_marked, group.words, group.names_processes)


@dataclass(eq=False)
class _Waiter:
    req: AdmitRequest
    priv: Privileges
    seq: int  # its place: kept when its load goes back to the queue
    entered_at: float
    future: asyncio.Future
    progress: Callable[[dict], Any] | None
    why: str | None = None  # what it waited on at the last look
    notified_waiting: bool = False
    resident_waiting_sent: bool = False
    retry_at: float | None = None
    retry_s: float = 0.0
    retry_why: str = "llama_swap"  # _RETRY_WHY's key
    refused_status: int | None = None  # llama-swap's status, when it refused its last load (a 429 or a 503)
    refused_ticket: str | None = None  # that load's ticket, which keys its load_failed
    drain_for: str | None = None  # the drain's why, when an unload aborted its load: its `draining` words


@dataclass(eq=False)
class _Load:
    """The load that holds the one-load slot: the gate's own, or one a restarted gate found starting."""

    model: str
    footprint: float  # the registry's at admission: what rule 9 admitted
    ticket_id: str | None
    issued_at: float | None
    deadline: float  # the ticket's; for one found starting, LOAD_CALL_TIMEOUT_S after the gate found it
    available_at: float | None  # MemAvailable at admission
    # watching: a ticket the gate couldn't withdraw holds the slot until it can no longer be claimed, or until
    # /running shows the model (the controller's ruling at Task 15's review).
    phase: Literal["calling", "settling", "unloading", "watching"]
    starter: _Waiter | None = None
    joiners: list[_Waiter] = field(default_factory=list)
    hold_after: tuple[float, Decimal] | None = None  # (make-room's hold's created_at, its size once this load is in)
    # A watch of the constructor's for a model that already has a record (ready, draining, stopping, or starting with
    # its unload requested): its record is never the watch's to settle (the controller's ruling at Task 15's re-review).
    keeps_record: bool = False
    clears_mark: bool = False
    started_at: float | None = None  # launch's, from this ticket's started/ record
    task: asyncio.Task | None = None


@dataclass(frozen=True)
class _Figures:
    """Rule 9's terms at one reading."""

    available: Decimal
    reserve: Decimal
    owed: Decimal
    ceiling: Decimal
    committed: Decimal
    starting: Decimal
    held: Decimal

    def free(self, uses_hold: bool) -> Decimal:
        return budget.free_for_a_load(available=self.available, reserve=self.reserve, owed=self.owed,
                                      ceiling=self.ceiling, committed=self.committed, starting=self.starting,
                                      held=0 if uses_hold else self.held)

    @property
    def ceiling_binds(self) -> bool:
        """Whether ceiling − committed is the smaller term of min(), so the words show its breakdown."""
        return self.ceiling - self.committed < self.available - self.reserve - self.owed


class Admitter:
    """The gate's admission. `llamaswap` is the gate's AsyncLlamaSwap; `emit` its Notifier; `clock` gives Unix
    seconds; `read_mem`, `read_hold` and `holders` read MemAvailable, the brake's hold and the top memory holders (the
    core's, each cheap: the holders as last read off the event loop), `holders` given whether the asking group names
    Dan's processes. `launch` is launch's folder (tickets, `started/`, refusal records), `hf_home` the Hugging Face
    cache the engines read. Beside the plan's interface: `on_change` is called after every change to `state`, so the
    core saves it, before a load call with the ticket's id in `state.issued` among them; `poll_s` bounds how long a
    request or a load being settled goes unchecked without a `changed()`; `proc` is where the engines' RssAnon is read
    (a tree the tests build). `registry` is replaced when the registry is read again."""

    def __init__(self, registry: Registry, state: GateState, llamaswap: AsyncLlamaSwap, emit: Emit,
                 clock: Callable[[], float], *, read_mem: Callable[[], MemInfo], read_hold: Callable[[], Hold | None],
                 launch: Path, hf_home: str, holders: Callable[[bool], list[Holder]],
                 on_change: Callable[[], None] | None = None, poll_s: float = POLL_S, proc: Path = procs.PROC,
                 running: list[Running] | None = None):
        self.registry = registry
        self.state = state
        self._swap = llamaswap
        self._emitter = emit
        self._clock = clock
        self._read_mem = read_mem
        self._read_hold = read_hold
        self._launch = Path(launch)
        self._hf_home = hf_home
        self._holders = holders
        self._on_change = on_change or (lambda: None)
        self._poll_s = poll_s
        self._proc = proc
        self._queue: list[_Waiter] = []
        self._loads: dict[str, _Load] = {}
        self._seq = 0
        self._restarting = state.applying is not None and not state.applying.ended
        self._llamaswap_up = True
        self._pass_due = False
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._ticker: asyncio.Task | None = None
        self._last_s: dict[str, float] = {}
        # The brake's hold as the gate first saw it: the hold, when, and whether at the gate's first look.
        self._hold_seen: tuple[Hold, float, bool] | None = None
        self._looked = False
        self._stopped = False
        # The models the last /running read showed starting, which hold the one-load slot whatever their record says
        # (the controller's ruling at Task 15's re-review): `running`, restore's own read; then each read the core
        # passes to observe_running, and the admitter's own.
        self._running_starting = {entry.model for entry in running or () if entry.state == "starting"}
        found = clock()
        for name, record in state.models.items():
            # A load a restarted gate found starting: the same ready-or-gone path. One whose unload was requested is
            # the drainer's (Task 18), and never made servable here.
            if record.state == "starting" and record.unload_requested_at is None:
                self._loads[name] = _Load(name, record.footprint_gib, state.issued.get(name), None,
                                          found + LOAD_CALL_TIMEOUT_S, None, "settling")
        # A gate that just started owns no load it didn't restore, so no ticket of another may stay live: one issued
        # just before a restart, its model not yet in /running, would start a model rule 9 doesn't count (the
        # controller's ruling at Task 15's review). One it can't withdraw holds the slot until it can't be claimed.
        # A model that already has a record keeps it whole: the watch only holds the slot (the controller's ruling at
        # Task 15's re-review).
        for name, model in registry.models.items():
            if name not in self._loads and self._withdraw(name) is None:
                deadline = self._ticket_deadline(name, found)
                if deadline is not None:
                    self._loads[name] = _Load(name, model.footprint_gib, None, None, deadline, None, "watching",
                                              keeps_record=name in state.models)

    # What the core and the routes call.

    async def admit(self, req: AdmitRequest, *, progress: Callable[[dict], Any] | None = None) -> Admitted | Refusal:
        """Admitted once the model is ready, or the refusal. `progress` is given each progress line as it happens
        (gateproto's WaitProgress as the request starts waiting and whenever its reason changes, then a LoadProgress as
        its load starts), for /v1/load's and a pin's streams. Cancelled, as when the front drops its call: the request
        leaves the queue at once, and a load already started for it goes on."""
        future = self._submit(req, progress)
        try:
            return await future
        except asyncio.CancelledError:
            self._forget(req.id)
            raise

    def reload(self, model: str, after: str) -> asyncio.Future:
        """Queues the gate's own reload of `model`, a resident, ahead of every request, and returns what it comes to
        (Admitted, or the refusal: only not_downloaded or load_failed, since a reload never times out). Queued at once,
        so the residents a caller queues in one step all go ahead of any request held meanwhile, and load one at a
        time, in that order. `after` is why it reloads, which resident_waiting says."""
        if after not in RELOAD_AFTER:
            raise ValueError(f"a reload's after is one of {', '.join(RELOAD_AFTER)}, not {after!r}")
        self._seq += 1
        return self._submit(AdmitRequest(f"reload:{model}:{self._seq}", model, "reload", None, None, after=after), None)

    def cancel(self, request_id: str) -> None:
        """A client gone: out of the queue at once, and nothing loads for it; a load already started finishes."""
        self._forget(request_id)

    def set_restarting(self, on: bool) -> None:
        """On while apply's hold stands: nothing loads, and a deadline gives `restarting`, even with llama-swap down."""
        self._restarting = bool(on)
        self.changed()

    def set_llamaswap_up(self, up: bool) -> None:
        if up and not self._llamaswap_up:
            for waiter in self._queue:  # it answers again: what waited for it tries at once
                waiter.retry_at, waiter.retry_s = None, 0.0
        self._llamaswap_up = bool(up)
        self.changed()

    def observe_running(self, running: list[Running]) -> None:
        """llama-swap's `/running`, as the core reads it every second: a model it shows starting holds the one-load
        slot whatever the gate's record says, as a late unload found across a restart (`draining`) whose start is still
        under way (the controller's ruling at Task 15's re-review)."""
        self._running_starting = {entry.model for entry in running if entry.state == "starting"}
        self.changed()

    def changed(self) -> None:
        """Memory, the hold, a drain or the state changed: every request is rechecked, and every load being settled
        reads /running again."""
        self._kick()
        self._wake.set()
        self._wake = asyncio.Event()

    async def stop(self) -> None:
        """The gate's shutdown: every task cancelled, its loads' included, before the llama-swap client closes (a load
        on a closed client raises instead of coming to an outcome), and every request still waiting cancelled."""
        self._stopped = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for waiter in self._queue + [w for load in self._loads.values() for w in self._requesters(load)]:
            waiter.future.cancel()
        self._queue.clear()

    def free_for_a_load(self, *, uses_hold: bool) -> Decimal:
        """Rule 9's figure now, for a group that does or doesn't load into make-room's hold."""
        return self._figures(self._read_mem().available_gib).free(uses_hold)

    def owed(self) -> Decimal:
        """The growth the loaded models are still owed (budget.owed_gib)."""
        return budget.owed_gib(budget.Loaded(record.name, record.footprint_gib, self._held_now(record))
                               for record in self.state.models.values() if record.state != "starting")

    def starting_gib(self) -> Decimal:
        """The footprints of the models still starting."""
        return sum((_gib(r.footprint_gib) for r in self.state.models.values() if r.state == "starting"), Decimal(0))

    def waiting(self) -> list[dict]:
        """StatusView's waiting rows, in the queue's order: who, for what, how long of how long, and why."""
        now = self._clock()
        rows = []
        for waiter in self._ordered():
            key, wait_s = waiter.req.key, self._wait_s(waiter)
            received = waiter.req.deadline - wait_s if key is not None else waiter.entered_at
            label = key.label if key is not None else "Dan" if waiter.req.kind == "command" else "the gate"
            why = waiter.why or "slot"
            rows.append({"key_label": label, "model": waiter.req.model, "waited_s": now - received, "wait_s": wait_s,
                         "why": why})
        return rows

    # The queue.

    def _submit(self, req: AdmitRequest, progress: Callable[[dict], Any] | None) -> asyncio.Future:
        """Everything up to the queue, at once and in order: a model ready is admitted, one loading joined, one not
        downloaded refused before any ticket; any other queued."""
        self._check(req)
        future = asyncio.get_running_loop().create_future()
        if self._stopped:  # the gate is shutting down: nothing more is admitted
            future.cancel()
            return future
        self._seq += 1
        waiter = _Waiter(req, privileges(req, self.registry), self._seq, self._clock(), future, progress)
        if self._servable(req.model):
            future.set_result(Admitted(req.model, False, None))
            return future
        load = self._loads.get(req.model)
        if load is not None and load.phase in ("calling", "settling"):  # never a watch: it keeps its own deadline
            self._join(load, waiter)
            self._kick()
            return future
        missing = launch._not_downloaded(self.registry.models[req.model], self._hf_home)
        if missing:
            _log.warning("not loading %s: %s", req.model, missing)
            self._refuse(waiter, "not_downloaded")
            return future
        self._queue.append(waiter)
        self._kick()
        return future

    def _check(self, req: AdmitRequest) -> None:
        if req.kind not in ("key", "command", "reload"):
            raise ValueError(f"a request's kind is key, command or reload, not {req.kind!r}")
        if req.model not in self.registry.models:
            raise ValueError(f"{req.model!r} isn't a model the registry has")
        if req.kind == "key" and (req.key is None or req.deadline is None):
            raise ValueError("a key's request carries its key and its deadline")
        if req.kind == "command" and req.key is not None:
            raise ValueError("one of Dan's commands carries no key")
        if req.kind == "reload" and (req.key is not None or req.deadline is not None):
            raise ValueError("the gate's reload carries no key and no deadline")

    def _forget(self, request_id: str) -> None:
        for waiter in self._queue:
            if waiter.req.id == request_id:
                self._queue.remove(waiter)
                waiter.future.cancel()
                self._kick()
                return
        for load in self._loads.values():
            if load.starter is not None and load.starter.req.id == request_id:
                load.starter.future.cancel()
                load.starter = None  # the load goes on
                return
            for waiter in load.joiners:
                if waiter.req.id == request_id:
                    load.joiners.remove(waiter)
                    waiter.future.cancel()
                    return

    def _ordered(self) -> list[_Waiter]:
        return sorted(self._queue, key=lambda w: (w.req.kind != "reload", w.priv.queue, w.seq))

    def _kick(self) -> None:
        """A pass of the queue, once, in the next step of the event loop: so whatever else this step does (a reload
        queued after a hold's end, say) is in it."""
        if self._stopped:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        for load in self._loads.values():
            if load.task is None and load.phase == "settling":  # one a restarted gate found starting
                load.task = self._spawn(self._guarded(load, self._ready_or_gone(load)))
            elif load.task is None and load.phase == "watching":  # a ticket the restarted gate couldn't withdraw
                load.task = self._spawn(self._guarded(load, self._watch_ticket(load)))
        if not self._pass_due:
            self._pass_due = True
            loop.call_soon(self._run_pass)

    def _run_pass(self) -> None:
        self._pass_due = False
        if self._stopped:
            return
        try:
            self._pass()
        except Exception:
            _log.exception("admission's pass broke; it runs again at the next change")
        if self._queue and (self._ticker is None or self._ticker.done()):
            self._ticker = self._spawn(self._tick())

    def _pass(self) -> None:
        """One look at every waiting request, with one reading of memory and the hold."""
        now = self._clock()
        hold = self._read_hold()
        available = self._read_mem().available_gib
        self._note_hold(hold, now)
        figures = self._figures(available)
        self._looked = True
        for waiter in list(self._queue):
            if waiter.req.model not in self.registry.models:  # the registry, read again, no longer has it
                self._dequeue(waiter)
                if not waiter.future.done():
                    waiter.future.set_exception(ValueError(f"{waiter.req.model!r} isn't a model the registry has now"))
                continue
            if self._servable(waiter.req.model):
                self._dequeue(waiter)
                self._resolve(waiter, Admitted(waiter.req.model, False, None))
                continue
            load = self._loads.get(waiter.req.model)
            if load is not None and load.phase in ("calling", "settling"):
                self._dequeue(waiter)
                self._join(load, waiter)
        if not self._slot_busy():
            for waiter in self._ordered():
                if self._why(waiter, now, hold, figures) is None:
                    if self._start(waiter, now, figures):
                        figures = self._figures(available)  # it counts as starting now
                        load = self._loads[waiter.req.model]
                        for other in [w for w in self._queue if w.req.model == waiter.req.model]:
                            self._dequeue(other)
                            self._join(load, other)
                    break
        for waiter in self._ordered():
            why = self._why(waiter, now, hold, figures) or "slot"
            try:
                self._note_wait(waiter, why, hold, figures)
                if waiter.req.deadline is not None and now >= waiter.req.deadline:
                    self._dequeue(waiter)
                    if why == "swap_refused":  # llama-swap kept refusing it: never llama_swap_down while it answers
                        self._refuse(waiter, "load_failed", swap_status=waiter.refused_status, swap_kept=True,
                                     ticket_id=waiter.refused_ticket)
                    elif why == "ticket":  # a ticket the gate couldn't withdraw (Task 15's re-review)
                        watch = self._loads.get(waiter.req.model)
                        if watch is not None and watch.phase == "watching":  # its own model's
                            self._refuse(waiter, "load_failed", ticket_unconfirmed=True, ticket_id=watch.ticket_id)
                        else:
                            self._refuse(waiter, "loading", slot_ticket=True)
                    else:
                        self._refuse(waiter, _CODES[why], hold=hold, figures=figures)
            except Exception as err:
                _log.exception("admission couldn't answer request %s", waiter.req.id)
                self._dequeue(waiter)
                if not waiter.future.done():
                    waiter.future.set_exception(err)

    def _why(self, waiter: _Waiter, now: float, hold: Hold | None, figures: _Figures) -> str | None:
        """What `waiter` waits on now, the first that applies, or None when it may take the slot now."""
        name = waiter.req.model
        if self._restarting:
            return "restart"
        if not self._llamaswap_up:
            return "llama_swap"
        if hold is not None:
            return "brake"
        record = self.state.models.get(name)
        load = self._loads.get(name)
        if self._going(record) or (load is not None and load.phase == "unloading"):
            return "draining"
        if name in self.state.brake_marks and not waiter.priv.reloads_marked:
            return "dan"
        if waiter.retry_at is not None and now < waiter.retry_at:
            return _RETRY_WHY[waiter.retry_why]
        watch = self._loads.get(name)
        if watch is not None and watch.phase == "watching":  # its own model's ticket, which the gate is settling
            return "ticket"
        if record is not None and record.state == "starting":
            return "slot"  # a start the gate didn't make: it waits for that one to end
        if _gib(self._footprint(name)) > figures.free(waiter.priv.uses_hold):
            return "memory"
        if self._slot_holders():
            return "slot"
        if self._slot_busy():  # only a ticket the gate couldn't withdraw holds it: nothing loads
            return "ticket"
        return None

    def _servable(self, name: str) -> bool:
        record = self.state.models.get(name)
        return record is not None and record.state == "ready" and record.unload_requested_at is None

    @staticmethod
    def _going(record: ModelRecord | None) -> bool:
        return record is not None and (record.state in ("draining", "stopping")
                                       or record.unload_requested_at is not None)

    def _slot_busy(self) -> bool:
        return bool(self._loads) or bool(self._slot_holders())

    def _slot_holders(self) -> list[str]:
        """The models that hold the one-load slot with a start: a load of the admitter's other than a ticket's watch, a
        record `starting`, and any model /running last showed starting, whatever its record says (the controller's
        ruling at Task 15's re-review). A model whose ticket the gate is watching has shown no start: its own record
        stays `starting` meanwhile, counted in the figures, but it is named nowhere as loading."""
        watched = {name for name, load in self._loads.items() if load.phase == "watching"}
        names = [name for name, load in self._loads.items() if load.phase != "watching"]
        names += [name for name, record in self.state.models.items() if record.state == "starting"]
        names += sorted(self._running_starting)
        return [name for name in dict.fromkeys(names) if name not in watched]

    def _footprint(self, name: str) -> float:
        """The registry's; for a model it no longer knows, its largest, which errs safe."""
        model = self.registry.models.get(name)
        return model.footprint_gib if model else max(m.footprint_gib for m in self.registry.models.values())

    def _dequeue(self, waiter: _Waiter) -> None:
        if waiter in self._queue:
            self._queue.remove(waiter)

    def _join(self, load: _Load, waiter: _Waiter) -> None:
        load.joiners.append(waiter)
        self._progress(waiter, self._load_progress(load.model))

    async def _tick(self) -> None:
        """While requests wait: a pass at the nearest deadline or retry, and at least every poll_s."""
        while self._queue and not self._stopped:
            now = self._clock()
            due = [self._poll_s] + [t - now for w in self._queue for t in (w.req.deadline, w.retry_at)
                                    if t is not None and t > now]
            await self._sleep(min(due))
            self._kick()

    async def _sleep(self, timeout: float) -> None:
        """Until `timeout` has passed or changed() is called, whichever comes first."""
        wake = self._wake
        try:
            async with asyncio.timeout(max(0.01, timeout)):
                await wake.wait()
        except TimeoutError:
            pass

    def _spawn(self, coro: Coroutine) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # The figures.

    def _figures(self, available: float) -> _Figures:
        records = self.state.models.values()
        hold = self.state.room_hold
        b = self.registry.budget
        return _Figures(available=_gib(available), reserve=_gib(b.reserve_gib), owed=self.owed(),
                        ceiling=_gib(b.allocatable_gib),
                        committed=sum((_gib(r.footprint_gib) for r in records if r.state != "starting"), Decimal(0)),
                        starting=self.starting_gib(), held=_gib(hold.size_gib) if hold is not None else Decimal(0))

    def _held_now(self, record: ModelRecord) -> Decimal:
        """What the model holds now: its load's fall (none when unknown), plus its engine's RssAnon growth since, only
        where the registry turns that on for its engine kind, and only when both readings are there."""
        held = _gib(record.load_fall_gib) if record.load_fall_gib is not None else Decimal(0)
        model = self.registry.models.get(record.name)
        if model is None or not self.registry.gate.owed_reads_rss.get(model.engine, False):
            return held
        growth = self._rss_growth(record)
        return held if growth is None else held + growth

    def _rss_growth(self, record: ModelRecord) -> Decimal | None:
        """The engine's RssAnon now less at its load; None when either can't be read, or the pid recorded is no longer
        the engine's process, so None is never read as 0 (the controller's ruling at Task 8's review)."""
        engine = self.state.ticketed.get(record.name)
        if record.rss_anon_at_load_gib is None or engine is None or engine.start_time is None:
            return None
        try:
            if not procs.still_running(engine.pid, engine.start_time, proc=self._proc):
                return None
            now = procs.rss_anon_gib(engine.pid, proc=self._proc)
        except OSError:
            return None
        return None if now is None else _gib(now) - _gib(record.rss_anon_at_load_gib)

    # A load.

    def _start(self, waiter: _Waiter, now: float, figures: _Figures) -> bool:
        """`waiter` takes the slot: its ticket, the model recorded starting, both saved; load_started; the call, in a
        task of its own."""
        name = waiter.req.model
        model = self.registry.models[name]
        try:
            ticket_id = tickets.issue(self._launch, name, now + LOAD_CALL_TIMEOUT_S, now=now,
                                      footprint_gib=model.footprint_gib, available_gib=float(figures.available),
                                      boot_id=self.state.boot_id)
        except (OSError, ValueError) as err:
            _log.error("no ticket could be written for %s (%s), so it doesn't load", name, err)
            self._dequeue(waiter)  # the gate's own error, said as it is (the controller's ruling at Task 15's review)
            self._refuse(waiter, "load_failed", ticket_unwritten=True)
            return False
        hold_after = None
        if waiter.priv.uses_hold and self.state.room_hold is not None:
            held = _gib(self.state.room_hold.size_gib)
            hold_after = (self.state.room_hold.created_at, budget.hold_after_dans_load(
                free_outside_hold=figures.free(True) - held, hold=held, footprint=model.footprint_gib))
        self._dequeue(waiter)
        load = _Load(name, model.footprint_gib, ticket_id, now, now + LOAD_CALL_TIMEOUT_S, float(figures.available),
                     "calling", starter=waiter, hold_after=hold_after, clears_mark=waiter.priv.reloads_marked)
        self._loads[name] = load
        self.state.issued[name] = ticket_id
        self.state.models[name] = ModelRecord(name=name, footprint_gib=model.footprint_gib, loaded_at=now,
                                              load_fall_gib=None, fall_flagged=False, rss_anon_at_load_gib=None,
                                              last_use=now, state="starting")
        self._on_change_safe()  # saved before the load call
        self._emit("load_started", f"load:{ticket_id}", label=model.label,
                   key_label=waiter.req.key.label if waiter.req.kind == "key" else None,
                   last_s=self._last_s.get(name))
        self._progress(waiter, self._load_progress(name))
        load.task = self._spawn(self._guarded(load, self._call(load)))
        return True

    async def _guarded(self, load: _Load, settle: Coroutine) -> None:
        """A load's task: whatever breaks in it never leaves a request waiting for ever, and the slot is freed only
        once /running shows the model isn't starting."""
        try:
            await settle
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("settling the load of %s broke; it is settled by /running alone", load.model)
            for waiter in self._requesters(load):
                waiter.future.set_exception(RuntimeError(f"the gate's load of {load.model} broke"))
            load.starter, load.joiners = None, []
            await self._abandon(load)

    async def _call(self, load: _Load) -> None:
        try:
            outcome = await self._swap.load(load.model)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # httpx's RuntimeError on a closed client, say: the call's end isn't known
            _log.warning("the load call for %s raised %s; its ticket settles it", load.model, type(err).__name__)
            outcome = LoadOutcome.unknown("dropped")
        if outcome.kind == "ready":
            return self._ready(load)
        withdrawn = self._withdraw(load.model)
        if withdrawn is None:  # never read as claimed (the controller's ruling at Task 15's review)
            for waiter in self._requesters(load):  # each keeps its own deadline (Task 15's re-review)
                self._queue.append(waiter)
            load.starter, load.joiners = None, []
            self._kick()
            return await self._watch_ticket(load)
        if outcome.kind == "failed" and outcome.status == 500:
            self._take_started(load)
            if ABORTED_BY_UNLOAD in outcome.text or SHUTTING_DOWN in outcome.text:
                _log.warning("the load of %s was aborted (%s): its requests wait again", load.model, outcome.text)
                return self._requeue(load, "aborted" if ABORTED_BY_UNLOAD in outcome.text else "llama_swap")
            if DEADLINE_PASSED in outcome.text:
                return self._failed(load, None, HEALTH_CHECK_TIMEOUT_S)
            return self._failed(load, await self._engine_said(load), None)
        what = (f"came to nothing ({outcome.why})" if outcome.kind == "unknown"
                else f"was refused by llama-swap (HTTP {outcome.status}: {outcome.text[:200]})")
        if withdrawn:
            _log.warning("the load call for %s %s; its ticket was unused, so no start used it", load.model, what)
            self._take_started(load)
            if outcome.kind == "failed" and outcome.status not in RETRY_STATUSES:
                return self._failed(load, None, None, swap_status=outcome.status)  # a 404, a 401: at once
            if outcome.kind == "failed" or outcome.why in ("too big", "unreadable"):  # it answered, with a refusal
                return self._requeue(load, "swap_refused", outcome.status)
            return self._requeue(load, "llama_swap")  # nothing answered
        _log.warning("the load call for %s %s, and launch had claimed its ticket: the slot stays held until /running "
                     "shows it ready or gone", load.model, what)
        await self._ready_or_gone(load)

    async def _watch_ticket(self, load: _Load) -> None:
        """A ticket the gate couldn't withdraw (the controller's rulings at Task 15's review and re-review). The slot is
        held, with no requests of its own: each waits in the queue on its own blocker, to its own deadline. Held until:
        - /running shows the model, when the ticket was used and the load is settled as a start, the model recorded
          `starting` at its footprint if it has no record, as `restore` records one;
        - or the ticket's deadline has passed, when launch can no longer claim it, and a /running read that answered
          doesn't list the model. While /running can't be read, it holds on, and leaves `started/` alone, as every
          settle path does: a start claimed after the last read it saw may be under way.
        A watch of the constructor's for a model that already has a record keeps that record whole: it only holds the
        slot, and frees it past the deadline on any read that answered."""
        load.phase = "watching"
        while True:
            shown = await self._shown(load.model)
            if shown is _UNREAD:  # never freed blind
                await self._sleep(self._poll_s)
                continue
            now = self._clock()
            if shown is not None and not load.keeps_record:
                if load.model not in self.state.models:  # counted, as restore counts what it finds
                    self.state.models[load.model] = ModelRecord(
                        name=load.model, footprint_gib=load.footprint, loaded_at=now, load_fall_gib=None,
                        fall_flagged=False, rss_anon_at_load_gib=None, last_use=now,
                        state="stopping" if shown == "stopping" else "starting")
                    self._on_change_safe()
                return await self._ready_or_gone(load)
            left = load.deadline - now
            if left < 0:  # launch refuses a ticket whose deadline < now
                if not load.keeps_record:
                    self._take_started(load)
                    self._drop(load)
                else:
                    self._pop(load)
                return self._kick()
            await self._sleep(min(self._poll_s, left) if left > 0 else self._poll_s)

    def _hold_for_ticket(self, load: _Load) -> None:
        """After a load's end whose ticket couldn't be withdrawn: the slot held for that ticket until it can't be
        claimed or /running shows the model (_watch_ticket). The load's own requests were answered or sent back to the
        queue, where each waits on its own blocker, to its own deadline (the controller's ruling at Task 15's
        re-review)."""
        load.starter, load.joiners, load.phase, load.task = None, [], "watching", None
        self._loads[load.model] = load
        self._kick()  # its watch starts

    async def _ready_or_gone(self, load: _Load) -> None:
        """Until /running shows the model ready or gone; a start still under way past its own start's deadline is
        unloaded, /running read right before."""
        load.phase = "settling"
        while True:
            shown = await self._shown(load.model)
            if load.started_at is None:
                load.started_at = self._started_at(load)
            if shown == "ready":
                return self._ready(load)
            if shown is None:
                return await self._gone(load)
            left = self._start_deadline(load) - self._clock()
            if shown not in (_UNREAD, "stopping") and left <= 0:
                if await self._late_unload(load):
                    return
            await self._sleep(self._poll_s if left <= 0 else min(self._poll_s, left))

    def _start_deadline(self, load: _Load) -> float:
        """The start's own deadline plus PAST_THE_DEADLINE_S: from the claimed ticket's started/ record, as v257 counts
        from the start itself (process_command.go:577), else from the ticket's deadline."""
        if load.started_at is not None:
            return load.started_at + HEALTH_CHECK_TIMEOUT_S + PAST_THE_DEADLINE_S
        return load.deadline + PAST_THE_DEADLINE_S

    async def _late_unload(self, load: _Load) -> bool:
        """A start past its deadline: v257's health poll can hold it there (internal/config/model_config.go:181,
        process_command.go:587-596), and an unload aborts it (process_command.go:384-393). The unload is saved first,
        with why late_start, so a gate restarted before it is done resumes it as what it was. True once the load has
        ended; False when llama-swap never took the unload, to be tried again."""
        record = self.state.models.get(load.model)
        if record is not None:
            record.unload_requested_at, record.drain_id, record.drain_why = self._clock(), None, "late_start"
            self._on_change_safe()
        load.phase = "unloading"
        self._kick()
        if record is not None:
            record.state = "stopping"  # from the moment its unload call begins
            self._on_change_safe()
        _log.warning("%s is still starting past its deadline: unloading it", load.model)
        answered = False
        try:
            await self._swap.unload(load.model)
        except LlamaSwapNotSent:
            self._undo_unload(load, record)
            return False
        except LlamaSwapError as err:  # timed out, the stop going on in llama-swap; or answered: /running settles it
            _log.warning("unloading %s: %s", load.model, err)
            answered = isinstance(err, LlamaSwapAnswered)
        while True:
            shown = await self._shown(load.model)
            if shown is None:
                break
            if answered and shown in ("ready", "starting"):  # its 401 or 404 came before the unload (apigroup.go)
                self._undo_unload(load, record)
                if shown == "ready":
                    self._ready(load)
                    return True
                return False
            await self._sleep(self._poll_s)
        self._take_started(load)
        model = self.registry.models.get(load.model)
        self._emit("unloaded", f"drain:late-start-{load.ticket_id or load.model}",
                   label=model.label if model else load.model, why="late_start")
        self._failed(load, None, HEALTH_CHECK_TIMEOUT_S)
        return True

    def _undo_unload(self, load: _Load, record: ModelRecord | None) -> None:
        if record is not None:
            record.state, record.unload_requested_at, record.drain_why = "starting", None, None
            self._on_change_safe()
        load.phase = "settling"
        self._kick()

    async def _gone(self, load: _Load) -> None:
        """The model left /running without coming ready: the brake's unload or llama-swap's restart send its requests
        back to their places, as does an unload of the drainer's (make-room's, `spark unload`'s), worded by that
        unload (the controller's ruling at Task 15's review); any other end is a failed start, worded as one that
        stopped before it was ready. Its ticket is withdrawn first, and held for if it can't be."""
        unconfirmed = self._withdraw(load.model) is None
        self._take_started(load)
        record = self.state.models.get(load.model)
        if self._read_hold() is not None:
            self._requeue(load, "aborted")
        elif record is not None and record.unload_requested_at is not None and load.phase != "unloading":
            self._requeue(load, "aborted")
        elif not self._llamaswap_up or self._restarting:
            self._requeue(load, "llama_swap")
        else:
            self._failed(load, start_ended=True)  # a failed start, though no 500 said so (Task 15's ruling)
        if unconfirmed:
            self._hold_for_ticket(load)

    async def _abandon(self, load: _Load) -> None:
        """After a break: the slot held until /running shows the model neither starting nor stopping, then the record as
        /running has it, with no fall known, so *owed* errs safe. Its ticket is withdrawn, or held for."""
        load.phase = "settling"
        unconfirmed = self._withdraw(load.model) is None
        while (shown := await self._shown(load.model)) not in (None, "ready"):
            await self._sleep(self._poll_s)
        record = self.state.models.get(load.model)
        if shown is None:
            self._drop(load)
            if unconfirmed:
                return self._hold_for_ticket(load)
        elif record is not None:
            record.state = "ready"
            self._pop(load)
            self._on_change_safe()
        self._kick()

    async def _shown(self, name: str) -> Any:
        """The model's state as /running shows it: None when it isn't listed, _UNREAD when /running didn't answer."""
        try:
            running = await self._swap.running()
        except LlamaSwapError:
            return _UNREAD
        self._running_starting = {entry.model for entry in running if entry.state == "starting"}
        return {entry.model: entry.state for entry in running}.get(name)

    def _ready(self, load: _Load) -> None:
        """READY: the ticket withdrawn if no start used it; the record with its load's fall, capped and flagged; its
        engine ticketed when the started record is this ticket's; the mark gone, the hold shrunk, `loaded`, and every
        request on it admitted."""
        now = self._clock()
        name = load.model
        model = self.registry.models.get(name)
        if self._withdraw(name):  # it answered ready with no start of its own: the model ran already
            _log.warning("%s answered ready without using its ticket, which is withdrawn", name)
        started = self._take_started(load)
        available_at = load.available_at
        if available_at is None and started is not None and _finite(started.get("available_gib")):
            available_at = started["available_gib"]  # a load found starting: MemAvailable when its ticket was issued
        fall, flagged = self._fall(load, model, available_at)
        pid = start_time = rss = None
        if started is not None:
            pid = started["pid"]
            try:
                start_time = procs.start_time(pid, proc=self._proc)
                rss = procs.rss_anon_gib(pid, proc=self._proc) if start_time is not None else None
            except OSError as err:
                _log.warning("the engine of %s (pid %s) can't be read (%s)", name, pid, err)
        began = load.issued_at if load.issued_at is not None else started["started_at"] if started else None
        seconds = None if began is None else now - began
        self.state.models[name] = ModelRecord(name=name, footprint_gib=load.footprint, loaded_at=now,
                                              load_fall_gib=None if fall is None else float(fall),
                                              fall_flagged=flagged, rss_anon_at_load_gib=rss, last_use=now,
                                              state="ready")
        if started is not None:
            self.state.ticketed[name] = Ticketed(model=name, pid=pid, ticket_id=load.ticket_id, at=now,
                                                 start_time=start_time)
        self.state.issued.pop(name, None)
        if load.clears_mark:
            self.state.brake_marks.pop(name, None)
        ended = self._shrink_hold(load)
        if seconds is not None:
            self._last_s[name] = seconds
        self._pop(load)
        self._running_starting.discard(name)  # seen ready: no longer starting
        self._on_change_safe()
        if seconds is not None and load.ticket_id is not None:
            self._emit("loaded", f"load:{load.ticket_id}", label=model.label if model else name, seconds=seconds)
        if ended is not None:
            self._hold_used_up(ended)
        for waiter in self._requesters(load):
            self._resolve(waiter, Admitted(name, True, seconds))
        self._kick()

    def _fall(self, load: _Load, model, available_at: float | None) -> tuple[Decimal | None, bool]:
        """MemAvailable at admission less now, capped at the model's cold load plus LOAD_FALL_MARGIN_GIB when the
        registry has one, else at its footprint, and flagged when capped; one below 0 (memory freed elsewhere) is 0,
        flagged too. None when either reading is missing."""
        if available_at is None:
            return None, False
        try:
            after = self._read_mem().available_gib
        except OSError as err:
            _log.warning("MemAvailable can't be read after the load of %s (%s): its fall isn't known", load.model, err)
            return None, False
        fall = _gib(available_at) - _gib(after)
        cold = model.cold_load_gib if model is not None else None
        cap = _gib(cold) + LOAD_FALL_MARGIN_GIB if cold is not None else _gib(load.footprint)
        if fall > cap:
            return cap, True
        if fall < 0:
            return Decimal(0), True
        return fall, False

    def _shrink_hold(self, load: _Load) -> RoomHold | None:
        """A load into make-room's hold shrinks it by the part the room outside it couldn't cover (rule 4); one that
        uses it up ends it, and the hold is returned."""
        hold = self.state.room_hold
        if load.hold_after is None or hold is None or hold.created_at != load.hold_after[0]:
            return None
        after = load.hold_after[1]
        if after > 0:
            hold.size_gib = float(after)
            return None
        self.state.room_hold = None
        return hold

    def _hold_used_up(self, hold: RoomHold) -> None:
        """room_hold_ended, *used up by your own loads*; the residents make-room unloaded reload, one at a time."""
        here = set(self.state.models)
        unloaded = [self.registry.models[n] for n in hold.unloaded if n in self.registry.models and n not in here]
        residents = [m for m in unloaded if m.resident]
        on_demand = next((m.label for m in unloaded if not m.resident), None)
        self._emit("room_hold_ended", f"room:{hold.created_at}", why="used", unused_gib=0, total_gib=hold.size_gib,
                   reloading=[m.label for m in residents], next_label=on_demand)
        for model in residents:
            self.reload(model.name, "hold")

    def _failed(self, load: _Load, engine_said: str | None = None, deadline_s: float | None = None, **how: Any) -> None:
        """A failed load: one load_failed, whoever joined it; each request refused in its own group's words. A start
        that failed or passed its deadline; one that stopped before it was ready (`start_ended`); llama-swap's refusal
        at once (`swap_status`); or a ticket the gate couldn't confirm it withdrew (`ticket_unconfirmed`)."""
        self._drop(load)
        model = self.registry.models.get(load.model)
        label = model.label if model else load.model
        command = model.roles[0] if model and model.roles else load.model
        _, key = messages.refusal_notification("load_failed", request_id="",
                                               ticket_id=load.ticket_id or f"{load.model}-found-starting")
        how = {"engine_said": engine_said, "deadline_s": deadline_s} | how
        self._emit("load_failed", key, model_label=label, command=command, **how)
        for waiter in self._requesters(load):
            self._refuse(waiter, "load_failed", notify=False, **how)
        self._kick()

    def _requeue(self, load: _Load, why: str, status: int | None = None) -> None:
        """No start of the model's own ended: its requests go back to their places, each waiting a while before it
        tries again, so a llama-swap that refuses is never asked in a loop. `why` (_RETRY_WHY's) is what each waits on
        meanwhile, and so its refusal should its wait run out first; `status`, llama-swap's, when it refused."""
        record = self.state.models.get(load.model)
        drain_for = (record.drain_why if record is not None else None) or "unknown"
        self._drop(load)
        now = self._clock()
        for waiter in self._requesters(load):
            self._retry(waiter, now, why)
            waiter.drain_for = drain_for if why == "aborted" else None
            waiter.refused_status, waiter.refused_ticket = (status, load.ticket_id) if why == "swap_refused" else (
                None, None)
            self._queue.append(waiter)
        self._kick()

    def _retry(self, waiter: _Waiter, now: float, why: str) -> None:
        waiter.retry_s = min(RETRY_MAX_S, max(RETRY_FIRST_S, 2 * waiter.retry_s))
        waiter.retry_at, waiter.retry_why = now + waiter.retry_s, why

    def _drop(self, load: _Load) -> None:
        """The load is over and nothing of it stays: the slot, its record (not ready, so its model never served), its
        ticket's id."""
        self._pop(load)
        self._running_starting.discard(load.model)  # seen gone, or never started
        record = self.state.models.get(load.model)
        if record is not None and record.state != "ready":
            del self.state.models[load.model]
        if load.ticket_id is not None and self.state.issued.get(load.model) == load.ticket_id:
            del self.state.issued[load.model]
        self._on_change_safe()

    def _pop(self, load: _Load) -> None:
        """`load` out of the slot, only if it is still the one there."""
        if self._loads.get(load.model) is load:
            del self._loads[load.model]

    def _withdraw(self, name: str) -> bool | None:
        """`name`'s ticket taken back: True if it was still there, so no start used it; False if it wasn't. None when
        it couldn't be done, tried twice: never read as claimed (the controller's ruling at Task 15's review)."""
        for attempt in (1, 2):
            try:
                return tickets.withdraw(self._launch, name)
            except OSError as err:
                failed = err
        _log.error("the ticket of %s couldn't be withdrawn (%s, twice): the one-load slot is held until it can't be "
                   "claimed", name, failed)
        return None

    def _ticket_deadline(self, name: str, now: float) -> float | None:
        """The deadline of `name`'s ticket, or None when there is none; LOAD_CALL_TIMEOUT_S from `now`, the most a
        ticket the gate issued lives, when it can't be read, and never later than that (the controller's ruling at
        Task 15's re-review), so a bogus far deadline can't hold the slot."""
        try:
            ticket = tickets.read_record(tickets.record_path(self._launch, tickets.TICKETS, name), own=False)
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return now + LOAD_CALL_TIMEOUT_S
        deadline = ticket.get("deadline") if isinstance(ticket, dict) else None
        most = now + LOAD_CALL_TIMEOUT_S
        return min(float(deadline), most) if _finite(deadline) else most

    def _read_started(self, load: _Load) -> dict | None:
        """This ticket's started/ record, as launch wrote it, or None."""
        try:
            record = tickets.read_record(tickets.record_path(self._launch, tickets.STARTED, load.model), own=True)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as err:
            _log.warning("the started record of %s can't be read (%s); the load's outcome stands", load.model, err)
            return None
        ours = (isinstance(record, dict) and load.ticket_id is not None and record.get("id") == load.ticket_id
                and record.get("model") == load.model and _finite(record.get("started_at"))
                and isinstance(record.get("pid"), int) and not isinstance(record["pid"], bool) and record["pid"] > 0)
        return record if ours else None

    def _take_started(self, load: _Load) -> dict | None:
        """The started record read, then cleared, on every outcome: an error in either leaves the outcome standing,
        since a record left uncleared stops counting STARTED_EXPIRES_S after its start."""
        record = self._read_started(load)
        try:
            tickets.clear_started(self._launch, load.model)
        except OSError as err:
            _log.warning("the started record of %s can't be cleared (%s); it stops counting %s s after its start",
                         load.model, err, tickets.STARTED_EXPIRES_S)
        return record

    def _started_at(self, load: _Load) -> float | None:
        record = self._read_started(load)
        return None if record is None else float(record["started_at"])

    async def _engine_said(self, load: _Load) -> str | None:
        """launch's refusal record for this load, if there is one, else the engine's last line."""
        record = launch.read_refusal(self._launch, load.model)
        if record is not None and self._fresh(record, load):
            return record["reason"]
        try:
            lines = await self._swap.last_lines(load.model)
        except LlamaSwapError as err:
            _log.warning("the last lines of %s can't be read (%s)", load.model, err)
            return None
        return lines[-1] if lines else None

    @staticmethod
    def _fresh(record: dict, load: _Load) -> bool:
        """A refusal record written for this load, not one an older start left: launch dates it to the second, local
        time."""
        began = load.issued_at if load.issued_at is not None else load.started_at
        if began is None:
            return True
        try:
            written = datetime.fromisoformat(record["at"]).timestamp()
        except (ValueError, OverflowError, OSError):
            return False
        return written >= math.floor(began) - 1

    @staticmethod
    def _requesters(load: _Load) -> list[_Waiter]:
        return [w for w in ([load.starter] if load.starter else []) + load.joiners if not w.future.done()]

    # The words.

    def _refuse(self, waiter: _Waiter, code: str, *, notify: bool = True, hold: Hold | None = None,
                figures: _Figures | None = None, ticket_id: str | None = None, **how: Any) -> Refusal:
        """`code`'s refusal for `waiter`, in its group's words, recorded in the history; its notification, unless the
        caller sends one for several (a failed load's). `how` is load_failed's: engine_said, deadline_s, start_ended,
        swap_status, swap_kept, ticket_unwritten and ticket_unconfirmed; `ticket_id` keys its notification."""
        moment = self._moment(waiter, code, hold, figures, how)
        refusal = messages.refusal(code, moment)
        req = waiter.req
        self.state.refusals.append(RefusalRecord(at=self._clock(), model=req.model,
                                                 key=req.key.name if req.key else None, uid=req.uid, code=code,
                                                 message=refusal.message))
        self._on_change_safe()
        if notify and code != "load_failed":
            sent = messages.refusal_notification(code, request_id=req.id)
            if sent is not None:
                kind, key = sent
                if kind == "refused":
                    self._emit(kind, key, code=code, moment=moment)
                elif kind == "footprint_suspect":
                    mark = self.state.brake_marks.get(req.model)
                    self._emit(kind, key, model_label=moment.model_label, key_label=moment.key_label,
                               fired_at=mark.at if mark else self._clock(), command=moment.model_command)
        if notify and code == "load_failed":  # keyed by its load's ticket (messages.refusal_notification)
            _, key = messages.refusal_notification(code, request_id=req.id,
                                                   ticket_id=ticket_id or f"{req.model}-{req.id}")
            self._emit(code, key, model_label=moment.model_label, command=moment.model_command,
                       engine_said=moment.engine_said, deadline_s=moment.deadline_s, start_ended=moment.start_ended,
                       swap_status=moment.swap_status, swap_kept=moment.swap_kept,
                       ticket_unwritten=moment.ticket_unwritten, ticket_unconfirmed=moment.ticket_unconfirmed)
        self._resolve(waiter, refusal)
        return refusal

    def _moment(self, waiter: _Waiter, code: str, hold: Hold | None, figures: _Figures | None,
                how: dict[str, Any]) -> Moment:
        name = waiter.req.model
        model = self.registry.models.get(name)
        key = waiter.req.key
        words: dict[str, Any] = {
            "model_label": model.label if model else name,
            "model_command": model.roles[0] if model and model.roles else name,
            "key_label": key.label if key else "", "words": waiter.priv.words,
            "names_processes": waiter.priv.names_processes, "wait_s": self._wait_s(waiter)}
        if code == "no_fit":
            words |= self._fit_words(waiter, figures or self._figures(self._read_mem().available_gib))
        elif code == "loading":  # behind a ticket the gate is settling: no model's label (Task 15's re-review)
            words |= {"loading_label": "" if how.get("slot_ticket") else self._slot_label()} | how
        elif code == "held_by_brake":
            words |= self._brake_words(hold or self._read_hold())
        elif code == "footprint_suspect":
            mark = self.state.brake_marks.get(name)
            words["brake_at"] = datetime.fromtimestamp(mark.at if mark else self._clock()).astimezone()
        elif code == "draining":
            record = self.state.models.get(name)
            load = self._loads.get(name)
            words["drain_for"] = ("late_start" if load is not None and load.phase == "unloading"
                                  else (record.drain_why if record is not None else None) or waiter.drain_for
                                  or "unknown")
        elif code == "load_failed":
            words |= how
        return Moment(**words)

    def _fit_words(self, waiter: _Waiter, figures: _Figures) -> dict[str, Any]:
        """no_fit's figures, the gate's own: free for a load as rule 9 gave it for this group, then the breakdown of
        the term that gave it (the ceiling's only where it was the smaller), a model starting, and the hold where it
        is counted."""
        words = {"needed_gib": self._footprint(waiter.req.model), "free_gib": figures.free(waiter.priv.uses_hold),
                 "available_gib": figures.available, "reserve_gib": figures.reserve, "owed_gib": figures.owed,
                 "starting_gib": figures.starting, "held_gib": figures.held,
                 "hold_counted": not waiter.priv.uses_hold and figures.held > 0,
                 "holders": list(self._holders(waiter.priv.names_processes))}
        if figures.ceiling_binds:
            words |= {"ceiling_gib": figures.ceiling, "committed_gib": figures.committed}
        return words

    def _brake_words(self, hold: Hold | None) -> dict[str, Any]:
        """held_by_brake's: when the brake fired and what was available then, from the hold; whether the pause waits
        for Dan; the warn line and the release time. A hold that doesn't say both (Phase 1's, or one that can't be
        read) is worded as seen, never as fired, with no reading the hold didn't record: by its own time when that
        parses, else by when the gate first saw it and whether it stood at the gate's first look (the controller's
        rulings at Task 15 and its review)."""
        now = self._clock()
        at = None
        if hold is not None:
            try:
                fired = datetime.fromisoformat(hold.since)
                at = fired if fired.tzinfo else fired.astimezone()
            except ValueError:
                at = None
        waits = True if hold is None else release_waits_for_dan(
            hold, boot_id=self.state.boot_id, last_auto_release_at=self.state.last_auto_release_at, now=now)
        words = {"release_waits_for_dan": waits, "warn_gib": self.registry.brake.warn_gib,
                 "release_after_s": RELEASE_AFTER_S}
        if at is not None:  # dated by its own time, whatever else it lacks (the controller's ruling, Task 15's review)
            return words | {"brake_at": at, "brake_available_gib": hold.available_gib}
        seen_at, before = (self._hold_seen[1], self._hold_seen[2]) if self._hold_seen else (now, False)
        return words | {"brake_seen_at": datetime.fromtimestamp(seen_at).astimezone(),
                        "brake_hold_before_gate": before}

    def _note_hold(self, hold: Hold | None, now: float) -> None:
        if hold is None:
            self._hold_seen = None
        elif self._hold_seen is None or self._hold_seen[0] != hold:
            self._hold_seen = (hold, now, not self._looked)

    def _slot_label(self) -> str:
        """The model holding the one-load slot with a start, by its label."""
        names = self._slot_holders()
        if not names:
            return "another model"
        model = self.registry.models.get(names[0])
        return model.label if model else names[0]

    def _wait_s(self, waiter: _Waiter) -> float | None:
        """The asking key's wait; for one of Dan's commands, the wait it was given; a reload has none."""
        if waiter.req.key is not None:
            return self.registry.key_groups[waiter.req.key.group].wait_s
        if waiter.req.deadline is not None:
            return max(0.0, waiter.req.deadline - waiter.entered_at)
        return None

    def _note_wait(self, waiter: _Waiter, why: str, hold: Hold | None, figures: _Figures) -> None:
        """A request's reason, as it starts waiting and whenever it changes: a WaitProgress line; `waiting` once, for a
        key's request; `resident_waiting` once, for a reload that doesn't fit."""
        if why != waiter.why:
            waiter.why = why
            if why in WAITING_WHY:
                self._progress(waiter, self._wait_progress(waiter, why, hold, figures))
        name = waiter.req.model
        model = self.registry.models.get(name)
        label = model.label if model else name
        if waiter.req.kind == "key" and why in _WAITING_WORDS and not waiter.notified_waiting:
            waiter.notified_waiting = True
            fields: dict[str, Any] = {"label": label, "key_label": waiter.req.key.label, "why": why,
                                      "wait_s": self._wait_s(waiter)}
            if why == "memory":
                fields |= {"needed_gib": self._footprint(name), "free_gib": figures.free(waiter.priv.uses_hold)}
            elif why == "brake":
                brake = self._brake_words(hold)
                fields |= {"release_waits_for_dan": brake["release_waits_for_dan"], "release_after_s": RELEASE_AFTER_S}
            elif why == "slot":
                fields["loading_label"] = self._slot_label()
            elif why == "dan":
                fields["command"] = model.roles[0] if model and model.roles else name
            self._emit("waiting", f"wait:{waiter.req.id}", **fields)
        if waiter.req.kind == "reload" and why == "memory" and not waiter.resident_waiting_sent:
            waiter.resident_waiting_sent = True
            self._emit("resident_waiting", f"resident:{name}:{waiter.entered_at}", label=label,
                       needed_gib=self._footprint(name), free_gib=figures.free(False), after=waiter.req.after or "boot")

    def _wait_progress(self, waiter: _Waiter, why: str, hold: Hold | None, figures: _Figures) -> dict:
        name = waiter.req.model
        model = self.registry.models.get(name)
        line: dict[str, Any] = {"kind": "waiting", "model": name, "label": model.label if model else name, "why": why,
                                "needed_gib": None, "free_gib": None, "loading_label": None,
                                "release_waits_for_dan": None, "release_after_s": None}
        if why == "memory":
            line |= {"needed_gib": float(self._footprint(name)),
                     "free_gib": float(figures.free(waiter.priv.uses_hold))}
        elif why == "slot":
            line["loading_label"] = self._slot_label()
        elif why == "brake":
            waits = self._brake_words(hold)["release_waits_for_dan"]
            line |= {"release_waits_for_dan": waits, "release_after_s": None if waits else float(RELEASE_AFTER_S)}
        return line

    def _load_progress(self, name: str) -> dict:
        model = self.registry.models.get(name)
        return {"kind": "loading", "model": name, "label": model.label if model else name,
                "last_s": self._last_s.get(name)}

    # Small things.

    def _progress(self, waiter: _Waiter, line: dict) -> None:
        if waiter.progress is None:
            return
        try:
            waiter.progress(line)
        except Exception as err:  # a stream that went: the request still stands, or the cancel takes it
            _log.warning("a progress line for request %s couldn't be sent (%s)", waiter.req.id, err)

    def _emit(self, kind: str, event_key: str, **fields: Any) -> None:
        """A notification, never at the cost of the admission: a field its words refuse is logged, one plain line,
        and the gate carries on (Task 18's ruling on emit's ValueError)."""
        try:
            self._emitter.emit(kind, event_key, **fields)
        except ValueError as err:
            _log.error("the %s notification couldn't be made: %s", kind, err)
        except Exception:
            _log.exception("the %s notification couldn't be sent", kind)

    def _on_change_safe(self) -> None:
        try:
            self._on_change()
        except Exception:
            _log.exception("saving the gate's state after a change in admission failed")

    @staticmethod
    def _resolve(waiter: _Waiter, value: Admitted | Refusal) -> None:
        if not waiter.future.done():
            waiter.future.set_result(value)


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False

