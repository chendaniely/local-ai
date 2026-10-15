"""The gate's policy (Phase 2a; plan.md, *The front and the gate*, *Draining a model*, and *Admission and memory
rules* 3 and 4): the drain before every unload, the idle unload, pins and sessions.

**The drain** (rule 4: no request is cut off). The gate asks the front to drain a model over the front's events call
(FrontChannel). The front marks it draining, so its new requests wait, and answers `drained` once its requests in
flight have finished, however long they take. Only then does the gate unload it, through llama-swap's `POST
/api/models/unload/<model>` (Task 12's AsyncLlamaSwap.unload, bounded by UNLOAD_CALL_TIMEOUT_S), and tell the front
`unloaded`. How a drain ends (the controller's rulings at Tasks 12 and 13 and their reviews):
- Before `drained`: the front's call dropping sends `undrain` (owed until the front is back), and False; the front
  answering `busy`, for an idle drain only (a request was in flight when it marked the drain), ends it with nothing
  unloaded, the model's last use now, and False.
- DRAIN_GRACE_S runs from `drained` to the moment the unload call begins, and no further: the gate's own step between
  them (saving the unload as requested) held up that long, or the front's call dropping meanwhile, sends `undrain`, and
  False.
- **The sent-unload rule.** From the moment the unload call begins it counts as sent, and the drain ends only when
  `/running` shows the model gone: then `unloaded`, and True. v257 runs an unload it has taken to the end, whatever
  happens to the call (internal/server/apigroup.go:161, internal/router/base.go:432-440), and a stop ends only in
  `stopped` (internal/process/process_command.go:422-434). A dropped channel then changes nothing: the front still
  hears `unloaded`, once it is back. The exceptions are an unload llama-swap never took, each of which sends `undrain`,
  and False: one never sent (LlamaSwapNotSent), and one answered with an error (LlamaSwapAnswered) after which
  `/running`, read again at once, still lists the model, and not as stopping (a 401 or a 404 comes before v257's
  Unload: internal/server/auth.go:31-34, apigroup.go:153-160). One answered whose model `/running` no longer lists is
  the gate's unload done (a 200 that broke off comes only after v257's Unload returned, apigroup.go:161-163), and one
  whose model it shows stopping waits for it to go, so no undrain ever reaches a model whose stop may be under way
  (confirmed, the controller's ruling at Task 16).
- From the call's start, each caller waiting on the drain gets a StoppingProgress every STOPPING_EVERY_S, and at
  STUCK_STOPPING_S one `llama_swap_down` alert goes, with why `stuck_stopping`, keyed by the model and the call's start:
  v257 waits on a killed engine's exit with no bound (process_command.go:710-712), while `/running` still answers.
  The stop's identity is its call's start, saved with the record (`unload_call_at`), so a restarted gate sends the
  same key, and the Notifier never a second alert for the same stop (the controller's ruling at Task 16's review).

A drain runs in a task of its own, so a caller that goes (Dan's Ctrl-C) ends its wait, never the drain or its unload; a
second drain asked of a model joins the one under way (but an idle one the front found busy, after which a drain that
isn't idle drains for itself), and Dan's command joining an idle drain gives it its why, the stronger reason. Its id and
why are saved on the model's record before `drain` is sent, within SAVE_BOUND_S, or the drain ends with nothing sent
(`not_saved`); `unload_requested_at` before the unload call; the record turns `stopping` as that call begins, the call's
start saved; an undrain clears them all. Each origin saves its own why: make-room's `make-room`, `spark unload`'s
`unload`, the idle check's `idle`; a resumed drain keeps the why it saved, Task 15's `late_start` among them, and one
with none is `unknown`, as are the drains the gate finishes after damage (Task 18), whose words name no command. A
drain never marks a model the admitter has `starting`, since the admitter's `_ready` replaces that record whole: it
waits until that load ends, ready (then it drains the ready record) or gone (then there is nothing to unload), each
caller hearing so once (AfterLoadProgress). A drain that found the admitter's own abort stopping, a stop not its
own, goes back through its steps should that stop be undone while `/running` still lists the model, so it never waits
for ever on a stop that ended in `ready` (the controller's ruling at Task 16's review). A word the front is owed and
can't take now (`undrain`, `unloaded`) is saved in GateState.undrains and sent once its call is open again, and ahead
of any new `drain` (send_owed).

With the front's call closed a drain sends nothing and ends False, `front_away`, which Dan's commands word with
messages.front_not_connected (Tasks 17 and 30); an idle one skips, with one journal line while the front stays away.
Each caller hears how its drain ended (`ended`, a DrainEnd), so Room words it, `gate_stopping` too when the gate stops
under it. A fresh idle drain checks again, right before it marks the model, that no pin, live session or use came since
idle_due named it; one that did ends it as `busy`, nothing marked. A resumed `late_start` drain, Task 15's abort of a
start past its deadline, never waits on the front, as the admitter's own abort doesn't: no request can be in flight to
a model that never became ready (the controller's ruling at Task 16; Drainer._late_start).

**The idle unload** (rule 3): idle_due names each on-demand model with no request in flight, no live session and no
pin standing, idle at least the registry's idle_unload_min; never a resident. The core drains each with why `idle`.

**Pins** (Pins) are Dan's alone: a pin keeps its model loaded until its time; a model not loaded is loaded first,
through admission as one of his commands, its progress streamed as `/v1/load`'s is. **Sessions** (Sessions) are each
uid's own, tied to one of its live processes (procs.owner_uid), SESSIONS_PER_UID at most, each ending with its process
or SESSION_TTL_S after its last renewal; `spark` and `spark-front` hold none; a session names a model the registry has,
by its name, and its label is one line of at most LABEL_MAX characters. A pin's or a session's end counts as its
model's last use, so the model unloads idle_unload_min after it, as `pin_ended` and `unpinned` say (both accepted, the
controller's ruling at Task 16)."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

from spark import messages, procs
from spark.gate.admission import AdmitRequest, Admitter
from spark.gate.state import Emit, GateState, ModelRecord, Pin, Session, session_live
from spark.gateproto import (DRAIN_GRACE_S, DRAIN_WHY, LOAD_CALL_TIMEOUT_S, SESSION_TTL_S, SESSIONS_PER_UID,
                             STOPPING_EVERY_S, STUCK_STOPPING_S, DrainWhy, GateRefusalBody, InflightSnapshot)
from spark.llamaswap import LlamaSwapAnswered, LlamaSwapError
from spark.llamaswap_async import AsyncLlamaSwap, LlamaSwapNotSent
from spark.registry import Registry

_log = logging.getLogger(__name__)

POLL_S = 1.0  # a drain reads /running, and rechecks its clock, at least this often without a changed()
FINAL_WORDS = ("undrain", "unloaded")  # the words that end a drain the front may hold: owed until it takes them
LABEL_MAX = 80  # a session's label, on one line, in Dan's terminal (`spark status`, make-room's list)
# The refusal of a session to an account that runs the stack itself, spark or spark-front: Sessions.register's, and
# `/v1/sessions`' before it reads the body (Task 19).
HOLDS_NO_SESSION = ("This account runs the stack itself, so it can't hold a session: a session keeps a model loaded "
                    "for an agent's own process.")
# The save of a drain's id and why before `drain` is sent is bounded by this; unsaved, nothing is sent and the drain
# ends (the controller's ruling at Task 16's review).
SAVE_BOUND_S = 5.0
Answer = Literal["drained", "busy", "dropped"]
# How a drain ended, which `ended` hears as drain() or resume() returns, so Room words it (Task 17; the controller's
# ruling at Task 16): unloaded (its unload done, the model gone); not_loaded (nothing to unload); gone (it left /running
# meanwhile: the brake, a crash); front_away (the front's call wasn't open, so nothing was sent); front_dropped (that
# call dropped before the unload was sent); busy (an idle drain found a request in flight); not_taken (an unload
# llama-swap never took); held (the gate's step before the call held past DRAIN_GRACE_S); loaded_again (loaded anew
# meanwhile); broke (the drain broke before its unload call, and went back to serving). Added at Task 16's review, the
# controller's rulings: not_saved (the drain's id couldn't be saved within SAVE_BOUND_S, so nothing was sent); and
# gate_stopping (the gate stopped under it, or was stopping when it was asked); `busy` also ends an idle drain that a
# pin, a live session or a use reached before it marked the model.
DrainEnd = Literal["unloaded", "not_loaded", "gone", "front_away", "front_dropped", "busy", "not_taken", "held",
                   "loaded_again", "broke", "not_saved", "gate_stopping"]
DRAIN_ENDS: tuple[str, ...] = get_args(DrainEnd)
_UNREAD = object()  # /running couldn't be read


def _new_id() -> str:
    return secrets.token_hex(8)


@dataclass(frozen=True)
class Refusal:
    """The gate's own refusal of a session or a pin: the status its route answers (403 for who may, 404 for a session
    that isn't there, 409 for the rest) and GateRefusalBody's fields, `code` None, since none of these is a refusal
    code of messages'. Not messages.Refusal, which is a client's refusal through the front."""

    status: int
    message: str
    code: str | None = None

    def body(self) -> GateRefusalBody:
        return {"message": self.message, "code": self.code}


class FrontChannel:
    """The gate's side of the front's calls (Tasks 19 and 22): `snapshot`, the latest whole in-flight report
    (`report`, from `/v1/front/inflight`); `connected`, while the front's `/v1/front/events` call is open; `send`, onto
    that call's stream; and the front's answers to a drain, `drained` and `busy`, which the drainer waits on
    (`expect`).

    The events route takes its queue from `open`, with the core's `hello` and `state` at its head, streams what comes
    onto it, and calls `close` with it once the call has ended. Only one call is open at a time: a new one drops the
    last. A drop answers every drain still waiting with `dropped`, and the words that end a drain still in the queue,
    unsent, go into `undelivered`, which the drainer owes the front (Drainer.send_owed); so does one the call took off
    the queue and never wrote (its send broke, or the call ended while it waited), ahead of them, where it was (since
    2026-10-14)."""

    def __init__(self) -> None:
        self.snapshot: InflightSnapshot | None = None
        self.undelivered: list[dict] = []
        self._queue: asyncio.Queue[dict] | None = None
        self._dropped: asyncio.Event | None = None
        self._opened = asyncio.Event()
        self._answers: dict[tuple[str, str], tuple[asyncio.Future, bool]] = {}  # each drain's answer, and takes busy

    @property
    def connected(self) -> bool:
        return self._queue is not None

    def open(self, first: Iterable[dict] = ()) -> asyncio.Queue[dict]:
        """The front's events call opened: a queue of its own, `first` at its head. A call still open is dropped."""
        if self._queue is not None:
            self.close(self._queue)
        queue: asyncio.Queue[dict] = asyncio.Queue()
        for event in first:
            queue.put_nowait(event)
        self._queue, self._dropped = queue, asyncio.Event()
        self._opened.set()
        return queue

    def close(self, queue: asyncio.Queue[dict], unsent: Iterable[dict] = ()) -> None:
        """That call ended. Nothing if it isn't the one open (a newer one replaced it). `unsent` is what the call took
        off the queue and never wrote: it is owed as if still at the queue's head, so the order holds."""
        if queue is not self._queue:
            return
        left = list(unsent)
        while not queue.empty():
            left.append(queue.get_nowait())
        for event in left:
            if event.get("op") in FINAL_WORDS:
                self.undelivered.append(event)
        self._queue = None
        self._opened.clear()
        answers, self._answers = self._answers, {}
        for future, _ in answers.values():
            if not future.done():
                future.set_result("dropped")
        if self._dropped is not None:
            self._dropped.set()

    async def opened(self) -> None:
        """Until the front's events call is open."""
        await self._opened.wait()

    def dropped(self) -> asyncio.Event:
        """Set once the call open now ends; set already when none is open."""
        if self._queue is None or self._dropped is None:
            gone = asyncio.Event()
            gone.set()
            return gone
        return self._dropped

    def send(self, event: dict) -> bool:
        """`event` onto the open call's stream; False, and nothing sent, when none is open."""
        if self._queue is None:
            return False
        self._queue.put_nowait(event)
        return True

    def report(self, snapshot: InflightSnapshot) -> None:
        """The front's whole in-flight report, as `/v1/front/inflight` takes it."""
        self.snapshot = snapshot

    def expect(self, model: str, drain_id: str, *, takes_busy: bool) -> asyncio.Future:
        """The front's answer to the drain `drain_id` of `model`, as a future: `drained`, `busy` (only when it
        `takes_busy`, an idle drain's), or `dropped` once the call drops (at once, when none is open). Asked before the
        drain is sent, so no answer comes before it."""
        future = asyncio.get_running_loop().create_future()
        if self._queue is None:
            future.set_result("dropped")
        else:
            self._answers[(model, drain_id)] = (future, takes_busy)
        return future

    def forget(self, model: str, drain_id: str) -> None:
        self._answers.pop((model, drain_id), None)

    def drained(self, model: str, drain_id: str) -> bool:
        """`/v1/front/drained`: the front has no request in flight for `model` and holds its new ones. False when no
        drain waits for that answer."""
        return self._answer(model, drain_id, "drained")

    def busy(self, model: str, drain_id: str) -> bool:
        """`/v1/front/busy`: an idle drain found a request in flight, and the front kept serving. A drain that isn't
        idle doesn't take it, and goes on waiting for `drained`."""
        return self._answer(model, drain_id, "busy")

    def _answer(self, model: str, drain_id: str, answer: Answer) -> bool:
        future, takes_busy = self._answers.get((model, drain_id), (None, False))
        if future is None or future.done():
            _log.info("the front answered %s for %s under drain %s, which no drain waits for", answer, model, drain_id)
            return False
        if answer == "busy" and not takes_busy:
            _log.warning("the front answered busy for %s under drain %s, which isn't idle: it waits on for drained",
                         model, drain_id)
            return False
        del self._answers[(model, drain_id)]
        future.set_result(answer)
        return True


@dataclass(eq=False)
class _Drain:
    model: str
    why: DrainWhy
    drain_id: str | None
    resume: bool  # a drain the gate saved before a restart (resume): the front may hold it already
    record: ModelRecord | None = None  # the record it marked; a record replaced meanwhile is never touched
    listeners: list[list[Callable[[dict], Any]]] = field(default_factory=list)  # one box per caller waiting
    task: asyncio.Task | None = None
    ticker: asyncio.Task | None = None
    drain_sent: bool = False  # the front was sent `drain` under drain_id
    ours: bool = False  # the unload is this drain's own: it notifies `unloaded` with its why
    busy: bool = False  # it ended at the front's `busy`
    call_started_at: float | None = None
    lines_sent: int = 0
    end: DrainEnd | None = None
    after_load: bool = False  # its line that it waits for the model's load was sent
    settling: bool = False  # it waits now for the model's load to end


class Drainer:
    """The gate's drain before every unload (rule 4). `front` is the FrontChannel; `llamaswap` the gate's
    AsyncLlamaSwap; `admitter` Task 15's Admitter, whose `state` and `registry` the drainer reads and changes, and
    which hears of every change (`changed`, `observe_running` with each `/running` read that answered); `emit` the
    Notifier; `clock` gives Unix seconds. Beside the plan's interface: `on_change` is called after every change to the
    state, so the core saves it; it may return an awaitable, which the drainer awaits where a save must come first
    (before `drain` is sent, bounded by SAVE_BOUND_S, and before the unload call, bounded there by DRAIN_GRACE_S from
    `drained`); `poll_s` bounds how long a drain goes without reading /running or its clock when no `changed()` comes;
    `live` is Sessions.live, with which an idle drain checks again, right before it marks the model, that it is still
    idle (the controller's ruling at Task 16's review)."""

    def __init__(self, front: FrontChannel, llamaswap: AsyncLlamaSwap, admitter: Admitter, emit: Emit,
                 clock: Callable[[], float], *, on_change: Callable[[], Awaitable[None] | None] | None = None,
                 poll_s: float = POLL_S, live: Callable[[Session], bool] | None = None):
        self._front = front
        self._swap = llamaswap
        self._admitter = admitter
        self._emitter = emit
        self._clock = clock
        self._on_change = on_change or (lambda: None)
        self._poll_s = poll_s
        self._live = live or (lambda session: True)  # with no Sessions.live, every session counts as live
        self._drains: dict[str, _Drain] = {}
        # Each model the gate's own unload took: when /running showed it gone, and the drain's why.
        self._gone_own: dict[str, tuple[float, DrainWhy]] = {}
        self._owed_unloaded: dict[str, str] = {}  # the undrains owed (GateState.undrains) that are an `unloaded`
        self._last_final: dict[str, str] = {}  # each model's drain id of the last word that ended a drain
        self._idle_skipped: set[str] = set()  # the idle drains skipped, said once while the front stays away
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._stopped = False

    @property
    def state(self) -> GateState:
        return self._admitter.state

    @property
    def registry(self) -> Registry:
        return self._admitter.registry

    # What the core, Room and the routes call.

    async def drain(self, model: str, why: DrainWhy, *, progress: Callable[[dict], Any] | None = None,
                    ended: Callable[[DrainEnd], Any] | None = None) -> bool:
        """Drains `model`, then unloads it: True once /running shows it gone (or it was never loaded), False when it
        went back to serving. `progress` is given each StoppingProgress line; `ended`, as it returns, how it ended
        (DrainEnd). A drain of the model already under way is joined, but an idle one the front found busy, which a
        drain that isn't idle then follows with its own; an idle one joined by Dan's command takes the command's why,
        the stronger reason. With the front's call closed nothing is sent (`front_away`); an idle one skips, with a
        journal line, once while the front stays away. With the gate stopping, False, `gate_stopping`."""
        if why not in DRAIN_WHY:
            raise ValueError(f"a drain's why is one of {', '.join(DRAIN_WHY)}, not {why!r}")
        while not self._stopped:
            current = self._drains.get(model)
            if current is None:
                current = self._start(model, why, None, resume=False)
            elif current.why == "idle" and why != "idle":
                self._upgrade(current, why)
            result = await self._wait(current, progress)
            if result is None:  # the gate stopped under it
                break
            if result or not current.busy or why == "idle":
                self._tell(ended, current)
                return result
        self._tell_end(ended, "gate_stopping")
        return False

    async def resume(self, model: str, drain_id: str | None, why: DrainWhy | None, *,
                     progress: Callable[[dict], Any] | None = None,
                     ended: Callable[[DrainEnd], Any] | None = None) -> bool:
        """A drain a restarted gate saved (gate.state.resumable_drains), which Task 18 resumes at start: `drain` sent
        again under its saved id (a new one when None), once the front's call is open, and on as any drain; for a
        model already `stopping`, until /running shows it gone. A drain with no why saved is `unknown`, whose words
        name no command. A `late_start` one, Task 15's abort of a start past its deadline, needs no `drained`, since no
        request can be in flight to a model that never became ready (the controller's ruling at Task 16;
        _late_start)."""
        if self._stopped:
            self._tell_end(ended, "gate_stopping")
            return False
        current = self._drains.get(model) or self._start(model, why or "unknown", drain_id, resume=True)
        result = await self._wait(current, progress)
        if result is None:
            self._tell_end(ended, "gate_stopping")
            return False
        self._tell(ended, current)
        return result

    def changed(self) -> None:
        """Memory, a state or the clock moved (the core calls it on every reading): each drain looks again, and any
        word the front is owed goes once its call is open."""
        self._wake.set()
        self._wake = asyncio.Event()
        if self._front.connected:
            self._idle_skipped.clear()
        if self.state.undrains or self._front.undelivered:
            self.send_owed()

    def send_owed(self) -> None:
        """Every word the front is owed, in GateState.undrains (restore's, and those it couldn't take when sent), sent
        now if its call is open, and each dropped once sent: an `unloaded` for a drain whose model went, else an
        `undrain` under the saved id. The words left unsent in a call that dropped are owed first."""
        undelivered, self._front.undelivered = self._front.undelivered, []
        for event in undelivered:
            model = event["model"]
            self._owe(event, event.get("drain_id") or self._last_final.get(model) or _new_id())
        if not self._front.connected:
            return
        sent = False
        for model, drain_id in list(self.state.undrains.items()):
            if self._owed_unloaded.get(model) == drain_id:
                event = {"op": "unloaded", "model": model}
            else:
                event = {"op": "undrain", "model": model, "drain_id": drain_id}
            if not self._front.send(event):
                break
            del self.state.undrains[model]
            self._owed_unloaded.pop(model, None)
            sent = True
        if sent:
            self._note()

    def draining(self, model: str) -> bool:
        """Whether a drain of `model` is under way: its record is the drainer's to settle, never the core's (Task 18)."""
        return model in self._drains

    def own_unload(self, model: str, since: float) -> bool:
        """Whether the gate's own unload accounts for `model` leaving /running after `since` (unloaded_why)."""
        return self.unloaded_why(model, since) is not None

    def unloaded_why(self, model: str, since: float) -> DrainWhy | None:
        """The why of the gate's own unload that accounts for `model` leaving /running after `since`: its record's,
        while its unload is requested (`unknown` when none was saved); else the last drain of the drainer's own that saw
        it gone at or after `since`; else the admitter's own abort of its start past its deadline at or after `since`
        (`late_start`, Task 15's, which no drain makes); None when no unload of the gate's does. The residents' rule
        (Room.resident_left, Task 17) reloads a resident after `unknown` and `late_start`, never after Dan's `unload` or
        `make-room` (the controller's ruling at Task 17)."""
        record = self.state.models.get(model)
        if record is not None and record.unload_requested_at is not None:
            return record.drain_why or "unknown"
        found: list[tuple[float, DrainWhy]] = []
        gone = self._gone_own.get(model)
        if gone is not None and gone[0] >= since:
            found.append(gone)
        late = self._admitter.late_unloads.get(model, -math.inf)
        if late >= since:
            found.append((late, "late_start"))
        return max(found, key=lambda each: each[0])[1] if found else None

    async def stop(self) -> None:
        """The gate's shutdown: every drain's task cancelled before the llama-swap client closes. Each drain stays
        saved on its record, so the restarted gate resumes it (Task 18)."""
        self._stopped = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # A drain's steps.

    def _start(self, model: str, why: DrainWhy, drain_id: str | None, *, resume: bool) -> _Drain:
        d = _Drain(model, why, drain_id, resume)
        self._drains[model] = d
        d.task = self._spawn(self._run(d))
        return d

    async def _wait(self, d: _Drain, progress: Callable[[dict], Any] | None) -> bool:
        """The drain's result, as one caller waits for it: a caller that goes ends only its own wait."""
        box = [progress] if progress is not None else None
        if box is not None:
            d.listeners.append(box)
            if d.settling:  # it waits for the model's load: so does this caller, and hears so
                self._progress(d, self._after_load_line(d.model), only=box)
        try:
            return await asyncio.shield(d.task)
        except asyncio.CancelledError:
            caller = asyncio.current_task()
            if d.task.cancelled() and self._stopped and not (caller is not None and caller.cancelling()):
                return None  # the gate stopped under the drain, not the caller going: drain() says so
            raise
        finally:
            if box is not None:
                d.listeners = [other for other in d.listeners if other is not box]

    @classmethod
    def _tell(cls, ended: Callable[[DrainEnd], Any] | None, d: _Drain) -> None:
        if d.end is not None:
            cls._tell_end(ended, d.end)

    @staticmethod
    def _tell_end(ended: Callable[[DrainEnd], Any] | None, end: DrainEnd) -> None:
        if ended is not None:
            try:
                ended(end)
            except Exception as err:
                _log.warning("how a drain ended (%s) couldn't be passed on (%s)", end, err)

    def _upgrade(self, d: _Drain, why: DrainWhy) -> None:
        """Dan's command joining an idle drain is the stronger reason: the drain takes its why, which its record and
        its `unloaded` notification then carry (the controller's ruling at Task 16's review)."""
        d.why = why
        record = self.state.models.get(d.model)
        if record is not None and record is d.record and record.drain_why == "idle":
            record.drain_why = why
            self._note()

    async def _run(self, d: _Drain) -> bool:
        try:
            return await self._steps(d)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("the drain of %s broke; it is settled by /running alone", d.model)
            return await self._recover(d)
        finally:
            self._stop_ticker(d)
            if self._drains.get(d.model) is d:
                del self._drains[d.model]

    async def _steps(self, d: _Drain) -> bool:
        while True:
            record = await self._settled(d)
            if record is None:
                return self._not_loaded(d)
            d.record = record
            saved = record.unload_requested_at is not None and (record.state == "draining"
                                                                or record.drain_id is not None)
            if saved and not d.resume:
                # A drain restore saved (Task 13), which nothing has resumed yet: this is that drain, under its id
                # and why.
                d.resume, d.drain_id, d.why = True, record.drain_id, record.drain_why or "unknown"
            if record.state != "stopping":
                break
            # Its unload already under way: a resumed one, or the admitter's late start's. Dated from its call's start
            # as saved (unload_call_at), so its stuck alert keeps its key across a restart.
            d.ours = d.resume
            d.drain_id = d.drain_id or record.drain_id or _new_id()
            d.call_started_at = self._stop_started(record)
            d.lines_sent = 0
            self._start_ticker(d)
            if d.resume:
                return await self._until_gone(d)
            done = await self._until_gone(d, own=False)
            if done is not None:
                return done
            # A stop that wasn't the drainer's own was undone (the admitter's abort, never taken): no more lines and no
            # stuck alert; back through its steps, and drained as any drain (the controller's ruling at Task 16's
            # review). No `drain` was sent, so the sent-unload rule doesn't bind it.
            self._stop_ticker(d)
            d.call_started_at = None
            _log.warning("the stop of %s that its drain waited on was undone, and /running still lists it: it is "
                         "drained as any other", d.model)
        if d.resume and d.why == "late_start" and record.state == "draining":
            done = await self._late_start(d, record)
            if done is not None:
                return done
        if d.resume:
            await self._front.opened()  # the front may hold it drained: it hears the drain again once it's back
            if self.state.models.get(d.model) is not record:
                return self._lost(d)
        elif not self._front.connected:
            d.end = "front_away"
            if d.why != "idle":
                _log.warning("not draining %s: the front's call to the gate isn't open, so nothing can drain it",
                             d.model)
            elif d.model not in self._idle_skipped:
                self._idle_skipped.add(d.model)
                _log.warning("not idle-unloading %s: the front's call to the gate isn't open, so nothing can drain it; "
                             "it is tried again once the front is back", d.model)
            return False
        if d.why == "idle" and not d.resume and not self._still_idle(d.model):
            # A pin, a live session or a use came between idle_due and here: nothing is marked or unloaded (the
            # controller's ruling at Task 16's review).
            _log.info("not idle-unloading %s: a pin, a session or a request came since it was found idle", d.model)
            d.busy, d.end = True, "busy"
            return False
        d.drain_id = d.drain_id or _new_id()
        d.ours = True
        record.drain_id, record.drain_why, record.state = d.drain_id, d.why, "draining"
        # Saved before `drain` is sent (the controller's ruling at Task 13's review), within SAVE_BOUND_S, or nothing is
        # sent (at Task 16's review).
        if not await self._saved_first():
            _log.error("the gate couldn't save its state before draining %s, so no drain was sent", d.model)
            return self._undo(d, undrain=d.resume, end="not_saved")
        if self.state.models.get(d.model) is not record:
            return self._lost(d)
        self.send_owed()  # an owed undrain goes ahead of this drain
        dropped = self._front.dropped()
        answer = self._front.expect(d.model, d.drain_id, takes_busy=d.why == "idle")
        if not self._front.send({"op": "drain", "model": d.model, "drain_id": d.drain_id, "why": d.why}):
            self._front.forget(d.model, d.drain_id)
            return self._undo(d, undrain=d.resume, end="front_away")
        d.drain_sent = True
        self._idle_skipped.discard(d.model)
        self._admitter.changed()
        got = await answer
        if got == "busy":  # a request was in flight when the front marked it: it keeps serving, and that is use
            if self.state.models.get(d.model) is record:
                record.last_use = self._clock()
            d.busy = True
            return self._undo(d, undrain=False, end="busy")
        if got == "dropped":  # the channel-drop clause: the unload isn't sent
            return self._undo(d, undrain=True, end="front_dropped")
        drained_at = self._clock()
        if self.state.models.get(d.model) is not record:
            return self._lost(d)
        if record.unload_requested_at is None:
            record.unload_requested_at = drained_at
        if not await self._before_call(d, drained_at, dropped):
            return self._undo(d, undrain=True, end="front_dropped" if dropped.is_set() else "held")
        if self.state.models.get(d.model) is not record:
            return self._lost(d)
        return await self._unload(d, record)

    async def _late_start(self, d: _Drain, record: ModelRecord) -> bool | None:
        """A resumed `late_start` drain, Task 15's abort of a start past its deadline (the controller's ruling at Task
        16). No request can be in flight to a model that never became ready: its waiters are in the gate's queue, not
        the front's forwarded set. So, as the admitter's own abort does (`_late_unload`), it never waits on the front.
        `/running` shows it starting: unloaded at once, and an unload llama-swap never took is tried again at the next
        look, never undone to ready while it still starts. Ready by then: None, and it is drained as any drain, its
        why `unknown`, since requests may now be in flight. Gone: `unloaded`."""
        d.ours = True
        d.drain_id = d.drain_id or _new_id()
        while True:
            shown = await self._shown_answered(d.model)
            if self.state.models.get(d.model) is not record:
                return self._lost(d)
            if shown is None:
                return self._gone(d)
            if shown == "ready":
                _log.warning("%s, whose start ran past its deadline, is ready now: it is drained as any other",
                             d.model)
                d.why = "unknown"
                return None
            if shown == "stopping":  # a stop under way already (the brake's, say): it ends at gone
                record.state = "stopping"
                d.call_started_at = self._clock()
                self._note()
                self._start_ticker(d)
                return await self._until_gone(d)
            if await self._call(d, record):
                return await self._until_gone(d)
            # Its unload still requested: never ready while it starts.
            record.state, record.unload_call_at = "draining", None
            self._note()
            self._admitter.changed()
            await self._sleep(self._poll_s)

    async def _settled(self, d: _Drain) -> ModelRecord | None:
        """The model's record once no load of the admitter's has it starting; None when there is none. While it waits,
        each caller hears so, once: an AfterLoadProgress (the controller's ruling at Task 16's review)."""
        try:
            while (record := self.state.models.get(d.model)) is not None and record.state == "starting":
                d.settling = True
                if not d.after_load:
                    d.after_load = True
                    self._progress(d, self._after_load_line(d.model))
                await self._sleep(self._poll_s)
        finally:
            d.settling = False
        return record

    def _after_load_line(self, model: str) -> dict:
        return {"kind": "after_load", "model": model, "label": self._label(model)}

    @staticmethod
    def _stop_started(record: ModelRecord) -> float | None:
        """When the stop under way began: its call's start as saved, else when its unload was requested."""
        return record.unload_call_at if record.unload_call_at is not None else record.unload_requested_at

    def _still_idle(self, model: str) -> bool:
        """idle_due's rule, for one model, now: no request, pin, live session or use since it was found idle."""
        return _idle(self._clock(), self.state, self.registry, self._front.snapshot, self._live, model)

    async def _before_call(self, d: _Drain, drained_at: float, dropped: asyncio.Event) -> bool:
        """The gate's own step between `drained` and the unload call, saving the unload as requested: True once done
        within DRAIN_GRACE_S of `drained` with the front's call still open; False, the call never begun, otherwise."""
        step = self._save()
        if step is not None:
            saving = self._saving(step)
            try:
                while not saving.done() and not dropped.is_set():
                    left = drained_at + DRAIN_GRACE_S - self._clock()
                    if left <= 0:
                        break
                    await self._wait_any(left, [saving], [dropped])
            finally:
                if not saving.done():
                    saving.cancel()
        held = self._clock() - drained_at
        if dropped.is_set():
            _log.warning("the front's call dropped before the unload of %s was sent: back to serving", d.model)
            return False
        if held >= DRAIN_GRACE_S:
            _log.warning("the unload of %s wasn't sent %.0f s after the front's drained, the gate's step before it "
                         "held up: back to serving", d.model, held)
            return False
        return True

    async def _unload(self, d: _Drain, record: ModelRecord) -> bool:
        """The unload call, then gone; or, for an unload llama-swap never took, back to serving."""
        if not await self._call(d, record):
            return self._undo(d, undrain=True, end="not_taken")
        return await self._until_gone(d)

    async def _call(self, d: _Drain, record: ModelRecord) -> bool:
        """The unload call: True once it counts as sent, from the moment it begins, so only /running showing the model
        gone ends the drain; False for an unload llama-swap never took: never sent (LlamaSwapNotSent), or answered with
        an error while /running, read again at once, still lists the model, and not as stopping. An answer whose
        /running shows it stopping is an unload under way, which is never undone (the controller's ruling at Task
        16)."""
        d.call_started_at = self._clock()
        # From the moment its unload call begins (gateproto.ModelState), and that moment saved, the stop's identity.
        record.state, record.unload_call_at = "stopping", d.call_started_at
        self._note()
        self._admitter.changed()
        d.lines_sent = 0
        self._start_ticker(d)
        try:
            await self._swap.unload(d.model)
        except LlamaSwapNotSent as err:
            _log.warning("the unload of %s was never sent (%s)", d.model, err)
            self._stop_ticker(d)
            return False
        except LlamaSwapAnswered as err:
            shown = await self._shown_answered(d.model)
            if shown is not None and shown != "stopping":
                _log.warning("llama-swap answered the unload of %s with an error (%s), and /running still shows it %s: "
                             "it never took the unload", d.model, err, shown)
                self._stop_ticker(d)
                return False
            _log.warning("llama-swap answered the unload of %s with an error (%s), and /running shows it %s: the "
                         "unload is its own", d.model, err, "gone" if shown is None else shown)
        except LlamaSwapError as err:
            _log.warning("the unload call for %s came to nothing (%s): llama-swap goes on with it, so the gate waits "
                         "for /running to show it gone", d.model, err)
        except Exception as err:  # httpx's RuntimeError on a closed client, say: the call's end isn't known
            _log.warning("the unload call for %s raised %s: the gate waits for /running to show it gone", d.model,
                         type(err).__name__)
        return True

    async def _until_gone(self, d: _Drain, *, own: bool = True) -> bool | None:
        """Until /running shows the model gone, never decided on a read that failed. For a stop that isn't the
        drainer's own (`own` false: the admitter's abort), None once the record it marked leaves `stopping` while
        /running still lists the model: that stop was undone."""
        while (shown := await self._shown(d.model)) is not None:
            if not own and shown is not _UNREAD:
                record = self.state.models.get(d.model)
                if record is not d.record or record.state != "stopping":
                    return None
            await self._sleep(self._poll_s)
        return self._gone(d)

    async def _recover(self, d: _Drain) -> bool:
        """After a break: a drain whose unload call may have begun ends at gone, as the sent-unload rule says; any
        other goes back to serving."""
        try:
            if d.call_started_at is not None:
                return await self._until_gone(d)
            return self._undo(d, undrain=d.drain_sent or d.resume, end="broke")
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("settling the drain of %s broke too; its record stays as it is for the next start", d.model)
            return False

    # How a drain ends.

    def _gone(self, d: _Drain) -> bool:
        """/running shows the model gone: its record goes, the front hears `unloaded`, and Dan `unloaded` with its
        why when the unload was this drain's own."""
        self._stop_ticker(d)
        d.end = "unloaded"
        now = self._clock()
        record = self.state.models.get(d.model)
        if record is not None and record is d.record:
            del self.state.models[d.model]
            self.state.ticketed.pop(d.model, None)  # its engine went with it
        if d.ours or (d.record is not None and d.record.unload_requested_at is not None):
            self._gone_own[d.model] = (now, d.why)
        self._note()
        if d.drain_sent or d.resume:
            self._final({"op": "unloaded", "model": d.model}, d.drain_id)
        if d.ours:
            fields: dict[str, Any] = {"label": self._label(d.model), "why": d.why}
            if d.why == "idle":
                fields["idle_min"] = self.registry.gate.idle_unload_min
            self._emit("unloaded", f"drain:{d.drain_id}", **fields)
        self._admitter.changed()
        return True

    def _undo(self, d: _Drain, *, undrain: bool, end: DrainEnd) -> bool:
        """Back to serving, nothing unloaded: the record it marked ready again, its drain's id, why and unload
        request cleared, and, when the front may hold the drain, `undrain`."""
        d.end = end
        record = self.state.models.get(d.model)
        if record is not None and record is d.record:
            record.state = "ready"
            record.unload_requested_at = record.drain_id = record.drain_why = record.unload_call_at = None
            self._note()
        if undrain and d.drain_id is not None:
            self._final({"op": "undrain", "model": d.model, "drain_id": d.drain_id}, d.drain_id)
        self._admitter.changed()
        return False

    def _lost(self, d: _Drain) -> bool:
        """The record the drain marked is gone, as when the model left /running meanwhile (the brake, a crash): nothing
        to unload, and the front's hold released, True; or replaced by a new load: back to serving."""
        if self.state.models.get(d.model) is None:
            _log.warning("%s left /running while it was being drained: nothing to unload", d.model)
            d.end = "gone"
            if d.drain_sent or d.resume:
                self._final({"op": "unloaded", "model": d.model}, d.drain_id or _new_id())
            self._admitter.changed()
            return True
        return self._undo(d, undrain=d.drain_sent or d.resume, end="loaded_again")

    def _not_loaded(self, d: _Drain) -> bool:
        """Nothing loaded to drain. A resumed drain's front may still hold it: `unloaded` releases it."""
        d.end = "not_loaded"
        if d.resume:
            self._final({"op": "unloaded", "model": d.model}, d.drain_id or _new_id())
        return True

    def _final(self, event: dict, drain_id: str) -> None:
        """A word that ends a drain the front may hold: sent, or owed until its call is open again."""
        self._last_final[event["model"]] = drain_id
        if not self._front.send(event):
            self._owe(event, drain_id)

    def _owe(self, event: dict, drain_id: str) -> None:
        model = event["model"]
        self.state.undrains[model] = drain_id
        if event["op"] == "unloaded":
            self._owed_unloaded[model] = drain_id
        else:
            self._owed_unloaded.pop(model, None)
        self._note()

    # While it stops.

    def _start_ticker(self, d: _Drain) -> None:
        if d.ticker is None:
            d.ticker = self._spawn(self._tick_stopping(d))

    def _stop_ticker(self, d: _Drain) -> None:
        if d.ticker is not None:
            d.ticker.cancel()
            d.ticker = None

    async def _tick_stopping(self, d: _Drain) -> None:
        """A StoppingProgress to each caller every STOPPING_EVERY_S from the call's start (one, however many marks the
        clock passed at once), and one `llama_swap_down` at STUCK_STOPPING_S."""
        started = d.call_started_at
        label = self._label(d.model)
        alerted = False
        while True:
            now = self._clock()
            marks = int((now - started) // STOPPING_EVERY_S)
            if marks > d.lines_sent:
                d.lines_sent = marks
                self._progress(d, {"kind": "stopping", "model": d.model, "label": label})
            if not alerted and now - started >= STUCK_STOPPING_S:
                alerted = True
                self._emit("llama_swap_down", f"stuck:{d.model}:{started:.3f}", at=now, why="stuck_stopping",
                           model_label=label, since=started)
            due = started + (marks + 1) * STOPPING_EVERY_S
            if not alerted:
                due = min(due, started + STUCK_STOPPING_S)
            await self._sleep(due - now)

    # Small things.

    async def _shown(self, model: str) -> Any:
        """The model's state as /running shows it: None when it isn't listed, _UNREAD when /running didn't answer.
        Each read that answered goes to the admitter, so a model shown starting holds its one-load slot."""
        try:
            running = await self._swap.running()
        except LlamaSwapError:
            return _UNREAD
        self._admitter.observe_running(running)
        return {entry.model: entry.state for entry in running}.get(model)

    async def _shown_answered(self, model: str) -> Any:
        while (shown := await self._shown(model)) is _UNREAD:
            await self._sleep(self._poll_s)
        return shown

    async def _sleep(self, timeout: float) -> None:
        """Until `timeout` has passed, at most poll_s, or changed() is called, whichever comes first."""
        await self._wait_any(timeout, [], [])

    async def _wait_any(self, timeout: float, futures: list[asyncio.Future], events: list[asyncio.Event]) -> None:
        waiters = [asyncio.ensure_future(event.wait()) for event in (*events, self._wake)]
        try:
            await asyncio.wait([*futures, *waiters], timeout=max(0.01, min(self._poll_s, timeout)),
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                waiter.cancel()

    def _save(self) -> Awaitable[None] | None:
        """on_change, called; what it returned when that is awaitable."""
        try:
            result = self._on_change()
        except Exception:
            _log.exception("saving the gate's state after a change in a drain failed")
            return None
        return result if inspect.isawaitable(result) else None

    async def _saved_first(self) -> bool:
        """on_change, and its awaitable, if any, awaited, within SAVE_BOUND_S: a save that must come first. False when
        it raised, failed, or didn't end in time (cancelled then)."""
        try:
            step = self._on_change()
        except Exception:
            _log.exception("saving the gate's state after a change in a drain failed")
            return False
        if not inspect.isawaitable(step):
            return True
        saving = self._saving(step)
        deadline = self._clock() + SAVE_BOUND_S
        try:
            while not saving.done():
                left = deadline - self._clock()
                if left <= 0:
                    return False
                await self._wait_any(left, [saving], [])
        finally:
            if not saving.done():
                saving.cancel()
        return not saving.cancelled() and saving.exception() is None

    def _note(self) -> None:
        """A change whose save needn't come first: on_change, its awaitable, if any, left to finish by itself."""
        step = self._save()
        if step is not None:
            self._saving(step)

    def _saving(self, step: Awaitable[None]) -> asyncio.Future:
        """The awaitable on_change returned, under way, its failure logged when it ends."""
        saving = asyncio.ensure_future(step)
        self._tasks.add(saving)
        saving.add_done_callback(self._saved)
        return saving

    def _saved(self, saving: asyncio.Future) -> None:
        self._tasks.discard(saving)
        if not saving.cancelled() and saving.exception() is not None:
            _log.error("saving the gate's state after a change in a drain failed", exc_info=saving.exception())

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _label(self, model: str) -> str:
        known = self.registry.models.get(model)
        return known.label if known else model

    def _progress(self, d: _Drain, line: dict, *, only: list | None = None) -> None:
        for box in [only] if only is not None else list(d.listeners):
            try:
                box[0](line)
            except Exception as err:  # a stream that went: the drain goes on
                _log.warning("a progress line for the drain of %s couldn't be sent (%s)", d.model, err)

    def _emit(self, kind: str, event_key: str, **fields: Any) -> None:
        """A notification, never at the cost of the drain: a field its words refuse is logged, and the drain goes on."""
        try:
            self._emitter.emit(kind, event_key, **fields)
        except ValueError as err:
            _log.error("the %s notification couldn't be made: %s", kind, err)
        except Exception:
            _log.exception("the %s notification couldn't be sent", kind)


# The idle unload (rule 3).

def idle_due(now: float, state: GateState, registry: Registry, snapshot: InflightSnapshot | None,
             live: Callable[[Session], bool]) -> list[str]:
    """The models to idle-unload now, in the registry's order: each on-demand model `ready` with no drain or unload of
    its own, no request in flight and none the front holds, no pin standing, no live session (`live`: Sessions.live),
    and no use for idle_unload_min, its last use the later of its record's and the front's last end. A resident never;
    nor a model the registry doesn't know. With no report from the front (`snapshot` None), none: what is in flight
    isn't known."""
    if snapshot is None:
        return []
    return [name for name, model in registry.models.items()
            if not model.resident and _idle(now, state, registry, snapshot, live, name)]


def _idle(now: float, state: GateState, registry: Registry, snapshot: InflightSnapshot | None,
          live: Callable[[Session], bool], name: str) -> bool:
    """idle_due's rule for one model, which a fresh idle drain checks again before it marks the model."""
    record = state.models.get(name)
    if snapshot is None or record is None or record.state != "ready" or name in (snapshot.get("draining") or ()):
        return False
    if record.unload_requested_at is not None or record.drain_id is not None:
        return False
    counts = (snapshot.get("models") or {}).get(name) or {}
    if (counts.get("count") or 0) > 0:
        return False
    pin = state.pins.get(name)
    if pin is not None and (pin.until is None or pin.until > now):
        return False
    if any(session.model == name and _live(live, session) for session in state.sessions.values()):
        return False
    last = record.last_use
    if counts.get("last_end_at") is not None:
        last = max(last, counts["last_end_at"])
    return now - last >= registry.gate.idle_unload_min * 60


def _live(live: Callable[[Session], bool], session: Session) -> bool:
    """A session whose liveness can't be read keeps its model: an idle unload waits for it."""
    try:
        return bool(live(session))
    except Exception as err:
        _log.warning("session %s can't be checked (%s); it keeps %s for now", session.id, err, session.model)
        return True


def _last_use(state: GateState, model: str, at: float) -> None:
    """A pin's or a session's end counts as its model's last use."""
    record = state.models.get(model)
    if record is not None:
        record.last_use = max(record.last_use, at)


# Pins.

class Pins:
    """Dan's pins (rule 3, *The front and the gate*): only a uid `may_pin` allows (spark-admin's, or root) sets or ends
    one. `admitter` is Task 15's Admitter, whose registry and queue a pin that loads goes through; `emit` the Notifier;
    `on_change` is called after every change to the state, so the core saves it."""

    def __init__(self, state: GateState, admitter: Admitter, emit: Emit, clock: Callable[[], float], *,
                 may_pin: Callable[[int], bool], on_change: Callable[[], Any] | None = None):
        self.state = state
        self._admitter = admitter
        self._emitter = emit
        self._clock = clock
        self._may_pin = may_pin
        self._on_change = on_change or (lambda: None)
        self._n = 0

    async def pin(self, model: str, until: float | None, uid: int) -> AsyncIterator[dict]:
        """Pins `model` until `until` (Unix seconds; None: no end), as `/v1/pin` streams it. A model already loaded:
        PinConfirmation's fields alone, loaded_s None. Otherwise it is loaded first, as one of Dan's commands (`kind:
        "command"`, held up to LOAD_CALL_TIMEOUT_S): a WaitProgress as it starts waiting and whenever its reason
        changes, a LoadProgress as its load starts, then the confirmation, with the load's seconds. A refusal on the
        way is the last line, as a GateRefusalBody, and pins nothing. A caller that goes takes its request out of the
        queue; a load already started goes on, and nothing is pinned."""
        refused = self._check(model, until, uid)
        if refused is not None:
            yield refused.body()
            return
        record = self.state.models.get(model)
        if record is not None and record.state == "ready" and record.unload_requested_at is None:
            self._set(model, until, uid)
            yield self._confirmation(model, until, None)
            return
        self._n += 1
        lines: asyncio.Queue[dict] = asyncio.Queue()
        req = AdmitRequest(id=f"pin:{model}:{self._n}", model=model, kind="command", key=None,
                           deadline=self._clock() + LOAD_CALL_TIMEOUT_S, uid=uid)
        admit = asyncio.ensure_future(self._admitter.admit(req, progress=lines.put_nowait))
        try:
            while not admit.done() or not lines.empty():
                if not lines.empty():
                    yield lines.get_nowait()
                    continue
                line = asyncio.ensure_future(lines.get())
                try:
                    await asyncio.wait([admit, line], return_when=asyncio.FIRST_COMPLETED)
                finally:
                    if not line.done():
                        line.cancel()
                if line.done() and not line.cancelled():
                    yield line.result()
            result = admit.result()
        finally:
            if not admit.done():
                admit.cancel()
        if isinstance(result, messages.Refusal):
            yield {"message": result.message, "code": result.code}
            return
        self._set(model, until, uid)
        yield self._confirmation(model, until, result.seconds if result.loaded_now else None)

    def unpin(self, model: str, uid: int) -> dict | Refusal:
        """Ends `model`'s pin: `{label, idle_min, command}`, what messages.unpinned takes (idle_min None for a
        resident), its end counting as the model's last use; or a Refusal."""
        if not self._may_pin(uid):
            return Refusal(403, "Only Dan can end a pin, with `spark unpin` on the Spark.")
        label = self._label(model)
        if self.state.pins.pop(model, None) is None:
            return Refusal(409, f"{messages._start(label)} isn't pinned, so there is no pin to end.")
        _last_use(self.state, model, self._clock())
        self._changed()
        return {"label": label, "idle_min": self._idle_min(model), "command": self._command(model)}

    def expire(self, now: float) -> list[Pin]:
        """Each pin whose time has come ends (`pin_ended`), its end counting as the model's last use."""
        ended = [pin for pin in self.state.pins.values() if pin.until is not None and pin.until <= now]
        for pin in ended:
            del self.state.pins[pin.model]
            _last_use(self.state, pin.model, pin.until)
            self._emit("pin_ended", f"pin:{pin.model}:{pin.until:.3f}", label=self._label(pin.model), at=pin.until,
                       idle_min=self._idle_min(pin.model))
        if ended:
            self._changed()
        return ended

    def _check(self, model: str, until: float | None, uid: int) -> Refusal | None:
        if not self._may_pin(uid):
            return Refusal(403, "Only Dan can pin a model, with `spark pin` on the Spark.")
        if model not in self._admitter.registry.models:
            return Refusal(409, f"There's no model {messages._one_line(model)!r} in the registry, so there's nothing "
                                "to pin.")
        if until is not None and not (isinstance(until, (int, float)) and not isinstance(until, bool)
                                      and math.isfinite(until) and until > self._clock()):
            return Refusal(409, "A pin's end has to be a time later than now.")
        return None

    def _set(self, model: str, until: float | None, uid: int) -> None:
        self.state.pins[model] = Pin(model=model, until=None if until is None else float(until), by_uid=uid)
        self._changed()

    def _confirmation(self, model: str, until: float | None, loaded_s: float | None) -> dict:
        """PinConfirmation's fields."""
        return {"label": self._label(model), "until": None if until is None else float(until), "loaded_s": loaded_s,
                "command": self._command(model)}

    def _label(self, model: str) -> str:
        known = self._admitter.registry.models.get(model)
        return known.label if known else model

    def _command(self, model: str) -> str:
        known = self._admitter.registry.models.get(model)
        return known.roles[0] if known and known.roles else model

    def _idle_min(self, model: str) -> int | None:
        known = self._admitter.registry.models.get(model)
        return None if known is not None and known.resident else self._admitter.registry.gate.idle_unload_min

    def _changed(self) -> None:
        try:
            self._on_change()
        except Exception:
            _log.exception("saving the gate's state after a pin changed failed")

    def _emit(self, kind: str, event_key: str, **fields: Any) -> None:
        try:
            self._emitter.emit(kind, event_key, **fields)
        except ValueError as err:
            _log.error("the %s notification couldn't be made: %s", kind, err)
        except Exception:
            _log.exception("the %s notification couldn't be sent", kind)


# Sessions.

class Sessions:
    """Each uid's sessions (*The front and the gate*): a session keeps its model loaded while it is live, tied to one
    of its uid's live processes, recorded this boot with its pid's start time, so a pid handed out again keeps
    nothing (gate.state.session_live). `proc` is where processes are read (a tree the tests build); `may_hold(uid)` is
    false for `spark` and `spark-front`, which run the stack itself; `is_model(name)` is true for a model the registry
    has, the only names a session may name (the controller's ruling at Task 16's review); `on_change` is called after
    every change to the state, so the core saves it."""

    def __init__(self, state: GateState, clock: Callable[[], float], *, proc: Path = procs.PROC,
                 may_hold: Callable[[int], bool], is_model: Callable[[str], bool],
                 on_change: Callable[[], Any] | None = None):
        self.state = state
        self._clock = clock
        self._proc = Path(proc)
        self._may_hold = may_hold
        self._is_model = is_model
        self._on_change = on_change or (lambda: None)

    def register(self, uid: int, pid: int, model: str, label: str) -> Session | Refusal:
        """A new session for `uid` on `model`, a model the registry has by its name, held by its process `pid`: one
        that is running, and the caller's by its real uid (its `/proc` `Uid:`, procs.owner_uid). At most
        SESSIONS_PER_UID per uid, counted once the uid's ended ones are gone. The label goes on one line of at most
        LABEL_MAX characters, since Dan's terminal shows it."""
        now = self._clock()
        if not self._may_hold(uid):
            return Refusal(403, HOLDS_NO_SESSION)
        if not (isinstance(model, str) and self._is_model(model)):
            return Refusal(409, f"There's no model {_shown_id(str(model))!r} in the registry, so no session can "
                                "keep it loaded.")
        whole = isinstance(pid, int) and not isinstance(pid, bool)
        owner = self._owner(pid) if whole and pid >= 1 else None
        if owner is None:
            return Refusal(409, f"Process {pid} isn't running, so it can't hold a session.")
        uid_of, started = owner
        if uid_of != uid:
            return Refusal(403, f"Process {pid} isn't one of yours, so it can't hold your session.")
        self.prune(now)
        if sum(1 for session in self.state.sessions.values() if session.uid == uid) >= SESSIONS_PER_UID:
            return Refusal(409, f"This account already holds {SESSIONS_PER_UID} sessions, the most one account "
                                "may. End one with `spark session end <id>`; each also ends with its process, or "
                                f"{messages.duration(SESSION_TTL_S)} after its last renewal.")
        session = Session(id=_new_id(), uid=uid, pid=pid, model=model, label=_label(label),
                          started_at=now, renewed_at=now, boot_id=self.state.boot_id, start_time=started)
        self.state.sessions[session.id] = session
        self._changed()
        return session

    def may_hold(self, uid: int) -> bool:
        """Whether `uid` may hold a session at all, which `/v1/sessions` asks before it reads the body (Task 19)."""
        return bool(self._may_hold(uid))

    def renew(self, session_id: str, uid: int) -> Session | Refusal:
        """The owner's renewal: the session lives SESSION_TTL_S from now. One whose process has gone ends here."""
        session = self.state.sessions.get(session_id)
        if session is None:
            return _no_session(session_id)
        if session.uid != uid:
            return Refusal(403, f"Only the account that began session {_shown_id(session_id)} can renew it.")
        if not self.live(session):
            self._end(session, self._clock())
            return Refusal(404, f"Session {_shown_id(session_id)} has ended: its process has exited, or it went "
                                f"{messages.duration(SESSION_TTL_S)} without a renewal.")
        session.renewed_at = self._clock()
        self._changed()
        return session

    def end(self, session_id: str, uid: int, *, admin: bool) -> Session | Refusal:
        """Ends a session: its owner, or spark-admin (`admin`)."""
        session = self.state.sessions.get(session_id)
        if session is None:
            return _no_session(session_id)
        if session.uid != uid and not admin:
            return Refusal(403, f"Only the account that began session {_shown_id(session_id)}, or Dan, can end it.")
        self._end(session, self._clock())
        return session

    def prune(self, now: float) -> list[Session]:
        """Ends each session whose process has exited (or was another boot's), or not renewed for SESSION_TTL_S."""
        ended = [session for session in self.state.sessions.values()
                 if now - session.renewed_at > SESSION_TTL_S or not self._process_live(session)]
        for session in ended:
            self._end(session, min(now, session.renewed_at + SESSION_TTL_S))
        return ended

    def live(self, session: Session) -> bool:
        """Whether `session` keeps its model now: renewed within SESSION_TTL_S, and its process the one recorded,
        this boot. One whose process can't be read is taken as live, so nothing unloads under it meanwhile."""
        return self._clock() - session.renewed_at <= SESSION_TTL_S and self._process_live(session)

    def _owner(self, pid: int) -> tuple[int, int] | None:
        """The real uid and start time of the process `pid`, or None when it isn't running (gone, or exited and not
        yet reaped), read so that a pid handed to another process between the reads is never taken for it."""
        try:
            started = procs.start_time(pid, proc=self._proc)
            uid = procs.owner_uid(pid, proc=self._proc)
            if started is None or uid is None or not procs.still_running(pid, started, proc=self._proc):
                return None
        except OSError as err:
            _log.warning("process %s can't be read (%s): it holds no session", pid, err)
            return None
        return uid, started

    def _process_live(self, session: Session) -> bool:
        try:
            return session_live(session, boot_id=self.state.boot_id, proc=self._proc)
        except OSError as err:
            _log.warning("session %s's process %s can't be read (%s); the session stays for now", session.id,
                         session.pid, err)
            return True

    def _end(self, session: Session, at: float) -> None:
        self.state.sessions.pop(session.id, None)
        _last_use(self.state, session.model, at)
        self._changed()

    def _changed(self) -> None:
        try:
            self._on_change()
        except Exception:
            _log.exception("saving the gate's state after a session changed failed")


def _label(label: str) -> str:
    line = messages._one_line(label)
    return line if len(line) <= LABEL_MAX else line[:LABEL_MAX - 1] + "…"


def _shown_id(text: str) -> str:
    """A session id, or a model name, as a refusal shows it: the caller's text, on one line and short."""
    return messages._one_line(text)[:64]


def _no_session(session_id: str) -> Refusal:
    return Refusal(404, f"There's no session {_shown_id(session_id)}: it has ended, or it never began.")
