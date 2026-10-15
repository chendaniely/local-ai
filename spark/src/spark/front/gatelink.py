"""The front's link to the gate (Phase 2a; plan.md, *The front and the gate*, *Draining a model*, and rules 4 and 8):
GateLink asks the gate for each load, follows the gate's events, holds what the gate drains or holds, and reports the
front's counts. It speaks gateproto's routes for the front over the gate's status socket, with httpx over the Unix
socket and nothing from the environment, its connections on asyncio's own streams (_GateNetwork says why).

**What it follows.** `GET /v1/front/events`, kept open, called again whenever it ends: the model list of each `hello`
and `state` replaces the front's (parse.ModelList), so a model the gate's registry gains is served without a restart,
and the last list stays while the gate is down; each `state`'s `ready` is what's loaded. `connected` is true from the
call's answer until it ends. The gate counts as down (`gate_up` false) only once that call has dropped and a new one
hasn't been answered within GATE_DOWN_AFTER_S; while it is down, what's loaded is what llama-swap's `/running` shows
ready, read for a second at a time.

**A request's way** (`clear`), each step a wait within the key's wait, the deadline the app gives (when the front
received the request, plus the key's wait):
1. **Apply's hold** (`hold_all`, or a `hello` with `applying`, so a front restarted inside an apply holds again): every
   new request waits. On `release_all`, or a `hello` without `applying`, it goes through admission. At its deadline it
   gets `restarting`, the front's own, never `gate_down`. Once the gate has been gone longer than the hold's `lapse_s`,
   the front drops the hold itself (rule 8), so a gate that fails at start inside an apply never holds the API.
2. **A drain the front holds**: the request waits. On `undrain` it is served; on `unloaded` it goes through admission;
   at its deadline it gets `draining`, worded by the drain's why, while the gate's call is open, and `gate_down` while
   it isn't.
3. **Loaded**: counted in flight (`enter`) in the same step as the checks before it, nothing awaited between, so no
   drain can find a count of 0 while a request is on its way to llama-swap.
4. **Admission** (`admit`): `POST /v1/admit`, its answer as the gate gives it. A request that comes to admission while
   the gate is down gets `gate_down` at once. One already asking keeps its key's wait: a 503 (the gate's stop,
   `GATE_STOPPING`) or a call that drops is the gate restarting, so it is asked again, under the same request id and
   its original deadline, once the gate's socket answers, RETRY_S apart, and gets `gate_down` at its deadline, the
   gate's call not open, or its asks still failing though it is (the controller's ruling, at Task 22's review). One
   whose call the gate holds past its deadline is waited for until LATE_ANSWER_S past it, while the gate's call stays
   open, since a load the gate started by the deadline is waited for to its own bound; then `gate_down`. A call
   cancelled, as when the client goes, closes, and the gate takes that as the cancel.
The app gives `waits`, called once before the request first waits (a hold, a drain or an admission): false, and the
request is refused `too_many_requests`, the key's waiting cap reached.

**The drain** (rule 4: no request is cut off), each event handled in one step, so the count it checks and the mark it
makes are one: a `drain` marks the model draining with its count checked. An idle drain finding a request in flight is
answered `busy`, and the model keeps serving. Otherwise `drained` is posted once the count is 0 (`InFlight.on_change`).
- A `drain` under the id the front holds is the same drain, asked again by a restarted gate: `drained` again once the
  count is 0. One under an id it doesn't hold, as after the front's own restart, or under a new id the gate gives
  after damage, is adopted, so every drain the gate sends gets an answer.
- An `undrain` releases the drain under its id, or whatever drain the front holds for the model when its id is None;
  one under an id the front doesn't hold changes nothing. `unloaded` releases it into admission.
- **The call dropping**: a drain not yet reported `drained` is released at once, the front's own undrain, since the
  gate sends no unload before `drained` (Task 16); a request for it then costs time, not memory, since `spark launch`
  refuses a start without a ticket. One reported `drained` is held until the gate, back again, says `unloaded` or
  `undrain`, since its unload may already be queued in llama-swap, and a request let through could be cut when the
  stop ran (the controller's rulings at Task 12's second re-review). `drained` counts as reported from the moment its
  post begins, so a post the gate took but whose answer was lost never leads to an undrain.
- **A `drained` the gate didn't take** (the post failed) is sent again at each snapshot's turn, while the drain is
  held and the gate's call is open, until the gate takes it; its answer to a repeat changes nothing (the controller's
  ruling, at Task 22's review). None is sent again while a post of it is under way, counted from the step that sends
  it, so a turn that ends before a new post has begun never sends that drain twice.

**What the front lists** (`running_models`): the models the gate's last `state` listed, ready, starting or draining,
as llama-swap v257 lists running models as loaded; the app's answer to `GET /v1/models` reads it (the controller's
ruling, at Task 22's review).

**Its two tasks**, the events call and the snapshots, never end but at `aclose()`: a step of either that breaks is
logged by its type, and tried again RETRY_S later.

**What it tells the gate**, each post bounded by REQUEST_TIMEOUT_S, a failure logged once until one goes through: a
whole `/v1/front/inflight` snapshot, every model with its count and the models held draining, on every change and at
least every SNAPSHOT_EVERY_S while the gate's call is open, and at once as it opens; `drained` and `busy`; and each
refusal of the front's own (`refused`, gateproto.FRONT_REFUSAL_CODES) while the gate's call is open, none while it
isn't, cut to what the gate keeps.

**Why a stream was cut** (`why_cut`): when the gate's events show the model leave llama-swap's `/running` (the brake, a
crash, a kill) at or after the request's forwarding, within CUT_WHY_S of the cut, the front's journal line for the cut
says so (the controller's ruling at Task 12's review). The gate reads /running every second, so its event comes after
the cut, and the line waits for it; the client's response never does."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import select
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpcore
import httpx

from spark.front.upstream import InFlight
from spark.gateproto import (APPLY_LAPSE_S, DRAIN_WHY, FRONT_REFUSAL_CODES, GATE_DOWN_AFTER_S, LOAD_CALL_TIMEOUT_S,
                             REQUEST_TIMEOUT_S)

POLL_S = 0.5  # a wait reads its clock again at least this often, whatever else wakes it
RETRY_S = 0.25  # the events call, or an admit the gate's restart ended, is tried again this long after
SNAPSHOT_EVERY_S = 1.0  # the in-flight snapshot, at least this often while the gate's call is open
RUNNING_FOR_S = 1.0  # llama-swap's /running, read while the gate is down, stands for this long
# An admit the gate holds past its deadline, its call open, is waited for this long past it, then refused gate_down.
# The gate answers at the deadline, but a load it started by then is waited for to its own bound, the gate's load
# call, LOAD_CALL_TIMEOUT_S (admission.py: a load's requests wait past their key's wait, bounded by the load's own
# deadline); and its answer's way back, REQUEST_TIMEOUT_S. A bound of the deadline and REQUEST_TIMEOUT_S alone would
# refuse a request whose load is under way, and leave that load for nobody (the controller's ruling, at Task 22's
# review, asked for a margin; this is the one that keeps a started load's requests).
LATE_ANSWER_S = LOAD_CALL_TIMEOUT_S + REQUEST_TIMEOUT_S
# A cut's journal line waits this long for the gate's events to show its model gone from /running: v257's
# unloadTimeout of 10 s for an engine that doesn't stop, the gate's read every second, and a margin.
CUT_WHY_S = 12.0
CUT_WHY = "the model stopped while it answered: the brake, a crash or a kill"
GATE = "http://gate"  # the Host the front's calls name; the socket is what reaches the gate
# The gate keeps a front refusal's words to this many characters (gate.app.TEXT_MAX) and reads no body over 64 KiB, so
# a refusal posted to it is cut to this first, words and model both: model_not_found's hold the client's own name.
REFUSED_TEXT_MAX = 2000
EVENTS_TIMEOUT = httpx.Timeout(REQUEST_TIMEOUT_S, read=None)  # kept open, with no read timeout
ADMIT_TIMEOUT = httpx.Timeout(REQUEST_TIMEOUT_S, read=None, pool=None)  # held for the key's wait, and a load
POST_TIMEOUT = httpx.Timeout(REQUEST_TIMEOUT_S)
_log = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass(frozen=True)
class Refused:
    """A request the gate link won't clear: the gate's own refusal (`answer`, its AdmitRefused, which reaches the client
    as the gate worded it), or one of the front's (`code`, with the fields its words take beyond the asking key's own,
    messages.Moment's, which the app adds)."""

    code: str
    fields: dict[str, Any] = field(default_factory=dict)
    answer: dict[str, Any] | None = None


class GateError(Exception):
    """The gate answered the front in a way its protocol doesn't have. Its text names the route and the status, nothing
    of a request."""


class _Restarting(Exception):
    """An admit call ended without the gate's answer: a 503 (the gate stopping), or its connection gone."""


@dataclass(eq=False)
class _Drain:
    drain_id: str
    why: str
    reported: bool = False  # `drained` posted for it, from the post's start
    taken: bool = False  # the gate took that post, under the drain's id now
    posting: int = 0  # posts of `drained` under way, each counted from the step that spawned it
    ended: str | None = None  # how it ended: undrain, unloaded, or dropped (the front's own undrain)


@dataclass(eq=False)
class _Hold:
    lapse_s: float
    released: bool = False  # by the gate: its requests go through admission


class GateLink:
    """The front's link to the gate at `status_socket`, every time by `clock` (Unix seconds, as the gate's are).
    `inflight` is the front's count, the app's own, whose changes the link hears (InFlight.on_change); `models` the
    front's model list, which the gate's replaces; `running` gives the models llama-swap's `/running` shows ready
    (Upstream.ready_models), read while the gate is down. `start()` in the serving loop, `aclose()` at the end."""

    def __init__(self, status_socket: Path, clock: Callable[[], float] = time.time, *, inflight: InFlight,
                 models: Any, running: Callable[[], Awaitable[Iterable[str]]] | None = None,
                 down_after_s: float = GATE_DOWN_AFTER_S, poll_s: float = POLL_S, retry_s: float = RETRY_S,
                 snapshot_every_s: float = SNAPSHOT_EVERY_S, cut_why_s: float = CUT_WHY_S,
                 late_answer_s: float = LATE_ANSWER_S):
        self.status_socket = Path(status_socket)
        self.clock = clock
        self.inflight = inflight
        self.models = models
        self.down_after_s, self.poll_s, self.retry_s = down_after_s, poll_s, retry_s
        self.snapshot_every_s, self.cut_why_s, self.late_answer_s = snapshot_every_s, cut_why_s, late_answer_s
        self.connected = False
        self.events_read = 0
        self._closing = False
        self._running = running
        self._dropped_at = clock()  # since the front started, or the last call dropped
        self._ready: set[str] = set()
        self._listed: set[str] = set()  # every model the gate's last state listed: ready, starting or draining
        self._left: dict[str, float] = {}  # when the gate's events showed each model gone from /running
        self._drains: dict[str, _Drain] = {}
        self._hold: _Hold | None = None
        self._running_ready: set[str] = set()
        self._running_at: float | None = None
        self._running_read: asyncio.Future | None = None
        self._pulse = asyncio.Event()
        self._dirty = asyncio.Event()
        self._client: httpx.AsyncClient | None = None
        self._loops: list[asyncio.Task] = []
        self._tasks: set[asyncio.Task] = set()
        self._failing: set[str] = set()
        self._handlers: dict[str, Callable[[dict], None]] = {
            "hello": self._on_hello, "state": self._on_state, "drain": self._on_drain, "undrain": self._on_undrain,
            "unloaded": self._on_unloaded, "hold_all": self._on_hold_all, "release_all": self._on_release_all}
        inflight.on_change = self._count_changed

    # The state, as the app and the tests read it.

    @property
    def gate_up(self) -> bool:
        """False only once the events call has dropped, or never been answered since the front started, and a new one
        hasn't been answered within `down_after_s`."""
        return self.connected or self.clock() - self._dropped_at < self.down_after_s

    @property
    def holding(self) -> bool:
        return self._holding()

    @property
    def draining(self) -> frozenset[str]:
        """The models the front holds as draining."""
        return frozenset(self._drains)

    def running_models(self) -> frozenset[str]:
        """The models the gate's last `state` listed, ready, starting or draining: those llama-swap's `/running`
        showed it, the last list kept while the gate is down."""
        return frozenset(self._listed)

    # Its life.

    async def start(self) -> None:
        """Following the gate's events and reporting the counts, in tasks of their own; returns at once, so the front
        serves whether the gate answers or not."""
        if self._client is None:
            self._client = httpx.AsyncClient(transport=_GateTransport(self.status_socket), base_url=GATE,
                                             trust_env=False, follow_redirects=False, timeout=POST_TIMEOUT)
        if not self._loops:
            self._loops = [asyncio.create_task(self._follow()), asyncio.create_task(self._snapshots())]

    async def aclose(self) -> None:
        self._closing = True
        tasks = [*self._loops, *self._tasks]
        self._loops = []
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            client, self._client = self._client, None
            await client.aclose()

    # A request's way.

    async def clear(self, model: str, key: str, deadline: float, request_id: str, *, enter: Callable[[], _T],
                    admit: bool = False, waits: Callable[[], bool] = lambda: True) -> _T | Refused:
        """`model`'s request for `key` cleared to go to llama-swap, counted in flight by `enter()` in the step that
        cleared it, which it returns; or its refusal. `admit` sends it through admission, loaded or not (a resend, or a
        request apply's hold or an unload released). `waits` is called once, before it first waits: false, and it is
        refused too_many_requests. Each wait ends by `deadline`, by the clock."""
        waited = False
        while True:
            if self._holding():
                if not (waited or (waited := waits())):
                    return Refused("too_many_requests")
                hold = self._hold
                while self._holding():
                    if self.clock() >= deadline:
                        return Refused("restarting")
                    await self._changed(deadline)
                admit = admit or hold.released
                continue
            drain = self._drains.get(model)
            if drain is not None:
                if not (waited or (waited := waits())):
                    return Refused("too_many_requests")
                while drain.ended is None:
                    if self.clock() >= deadline:
                        if self.connected:
                            return Refused("draining", {"drain_for": drain.why, "inflight": self.inflight.count(model)})
                        return Refused("gate_down")
                    await self._changed(deadline)
                continue  # undrained: loaded again; unloaded: no longer loaded, so through admission
            if not admit:
                loaded = await self.loaded(model)
                if self._blocked(model):
                    continue
                if loaded:
                    return enter()
            if not self.gate_up:
                return Refused("gate_down")  # a load while the gate is down (rule 8)
            if not (waited or (waited := waits())):
                return Refused("too_many_requests")
            answer = await self.admit(model, key, deadline, request_id)
            if isinstance(answer, Refused):
                return answer
            if not answer["ok"]:
                return Refused(answer["code"], answer=answer)
            admit = False
            if self._blocked(model):
                continue
            return enter()

    async def loaded(self, model: str) -> bool:
        """Whether `model` is loaded: by the gate's last `state` while it is up, by llama-swap's `/running` while it is
        down."""
        if self.gate_up:
            return model in self._ready
        now = self.clock()
        if self._running_at is None or not 0 <= now - self._running_at < RUNNING_FOR_S:
            if self._running_read is None:
                self._running_read = asyncio.ensure_future(self._read_running())
            await asyncio.shield(self._running_read)
        return model in self._running_ready

    async def admit(self, model: str, key: str, deadline: float, request_id: str) -> dict | Refused:
        """The gate's answer for `model`'s admission for `key`, as `/v1/admit` gives it (gateproto's AdmitAnswer); or
        Refused gate_down: once `deadline` has passed with the gate's call not open, or its asks still failing; or
        `late_answer_s` past it, the gate holding the call. A 503 or a call that drops is the gate restarting: asked
        again under the same request id and deadline, `retry_s` apart, once the gate's socket answers. Cancelled, as
        when the client goes, its call closes, which the gate takes as the cancel."""
        body = {"model": model, "key": key, "deadline": deadline, "request_id": request_id}
        while True:
            while not self.connected:
                if self.clock() >= deadline:
                    return Refused("gate_down")
                await self._changed(deadline)
            call = asyncio.ensure_future(self._post_admit(body))
            down = asyncio.ensure_future(self._down_by(deadline))
            try:
                await asyncio.wait((call, down), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (call, down):
                    if not task.done():
                        task.cancel()
                await asyncio.wait((call, down))
                for task in (call, down):  # each end taken, so none is reported later as never retrieved
                    if not task.cancelled():
                        task.exception()
            if not call.cancelled():
                error = call.exception()
                if error is None:
                    return call.result()
                if not isinstance(error, _Restarting):
                    raise error
                if self.clock() >= deadline:
                    return Refused("gate_down")  # asked until its deadline, and never answered
                await asyncio.sleep(self.retry_s)
                continue
            return Refused("gate_down")

    async def why_cut(self, model: str, since: float) -> str | None:
        """Why `model`'s stream, forwarded at `since`, was cut, from the gate's events: CUT_WHY once they show the model
        gone from llama-swap's `/running` at or after `since`, within `cut_why_s`; else None."""
        loop = asyncio.get_running_loop()
        end = loop.time() + self.cut_why_s
        while self._left.get(model, -math.inf) < since:
            remaining = end - loop.time()
            if remaining <= 0:
                return None
            pulse = self._pulse
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(remaining):
                    await pulse.wait()
        return CUT_WHY

    # What the front tells the gate.

    async def drained(self, model: str, drain_id: str) -> bool:
        """`drained` posted for `model`'s drain `drain_id`: whether the gate took it."""
        return await self._post("/v1/front/drained", {"model": model, "drain_id": drain_id})

    async def busy(self, model: str, drain_id: str) -> bool:
        return await self._post("/v1/front/busy", {"model": model, "drain_id": drain_id})

    def refused(self, code: str, model: str | None, key: str, message: str) -> None:
        """A refusal of the front's own, posted to the gate while its call is open (gateproto.FrontRefusal), its words
        and its model each cut to REFUSED_TEXT_MAX characters, an ellipsis ending one that was cut, as the gate cuts
        them."""
        if self.connected and code in FRONT_REFUSAL_CODES:
            self._spawn(self._post("/v1/front/refused", {"at": self.clock(), "code": code, "model": _cut(model),
                                                         "key": key, "message": _cut(message)}))

    # The waits.

    def _poke(self) -> None:
        """Something changed: every wait looks again."""
        self._pulse.set()
        self._pulse = asyncio.Event()

    async def _changed(self, until: float | None = None) -> None:
        """Until something changes, `poll_s` passes, or the clock reaches `until`."""
        timeout = self.poll_s if until is None else max(0.0, min(self.poll_s, until - self.clock()))
        pulse = self._pulse
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(timeout):
                await pulse.wait()

    async def _down_by(self, deadline: float) -> None:
        """Until `deadline` has passed with the gate's call not open, or `late_answer_s` past it whatever the call."""
        late = deadline + self.late_answer_s
        while (self.connected or self.clock() < deadline) and self.clock() < late:
            await self._changed(deadline if self.clock() < deadline else late)

    def _holding(self) -> bool:
        """Whether apply's hold stands. Dropped here once the gate has been gone longer than its `lapse_s`."""
        hold = self._hold
        if hold is None:
            return False
        if not self.connected and self.clock() - self._dropped_at > hold.lapse_s:
            self._hold = None
            _log.warning("the gate has been gone longer than apply's hold allows (%s s): the front holds no more "
                         "requests, and serves what's loaded", hold.lapse_s)
            self._poke()
            return False
        return True

    def _blocked(self, model: str) -> bool:
        return self._holding() or model in self._drains

    # The calls.

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("the gate link isn't started")
        return self._client

    async def _post_admit(self, body: dict) -> dict:
        try:
            response = await self._http().post("/v1/admit", json=body, timeout=ADMIT_TIMEOUT)
        except httpx.TransportError:
            raise _Restarting from None
        if response.status_code == 503:
            raise _Restarting
        if response.status_code != 200:
            _log.warning("the gate answered the front's admit with %s", response.status_code)
            raise GateError(f"the gate answered /v1/admit with {response.status_code}")
        return _admit_answer(response)

    async def _post(self, path: str, body: dict) -> bool:
        """`body` posted to `path`: whether the gate took it. A failure is logged once until one goes through."""
        try:
            response = await self._http().post(path, json=body, timeout=POST_TIMEOUT)
            if 200 <= response.status_code < 300:
                self._failing.discard(path)
                return True
            why = str(response.status_code)
        except httpx.TransportError as err:
            why = type(err).__name__
        if path not in self._failing:
            self._failing.add(path)
            _log.warning("the front's post to the gate's %s failed (%s)", path, why)
        return False

    def _spawn(self, work: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._ended)

    def _ended(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            error = task.exception()
            _log.error("the front's call to the gate broke: %s", type(error).__name__)

    async def _read_running(self) -> None:
        try:
            ready = set(await self._running()) if self._running is not None else set()
        except Exception as err:  # llama-swap not answering: nothing counts as loaded
            _log.info("llama-swap's /running didn't answer the front (%s)", type(err).__name__)
            ready = set()
        finally:
            self._running_read = None
        self._running_ready, self._running_at = ready, self.clock()

    # The events.

    async def _follow(self) -> None:
        """The gate's events call, kept open, called again whenever it ends; a step that breaks is logged by its type,
        and the call made again, so the task never ends but at `aclose()`."""
        while True:
            try:
                async with self._http().stream("GET", "/v1/front/events", timeout=EVENTS_TIMEOUT) as response:
                    if response.status_code != 200:
                        if "events" not in self._failing:
                            self._failing.add("events")
                            _log.warning("the gate refused the front's events call (%s)", response.status_code)
                    else:
                        self._failing.discard("events")
                        self._opened()
                        try:
                            async for line in response.aiter_lines():
                                self._line(line)
                        finally:
                            self._dropped()
            except httpx.TransportError:
                pass
            except Exception as err:
                _log.error("the front's events call to the gate broke: %s; it calls again", type(err).__name__)
            await asyncio.sleep(self.retry_s)

    def _opened(self) -> None:
        self.connected = True
        self._dirty.set()  # a snapshot at once: the gate settles the drains the front holds from it
        _log.info("the gate answers the front's events call")
        self._poke()

    def _dropped(self) -> None:
        self.connected = False
        self._dropped_at = self.clock()
        for model, drain in list(self._drains.items()):
            if not drain.reported:  # the gate sends no unload before drained: served again at once
                self._end_drain(model, "dropped")
        if not self._closing:
            _log.warning("the front's events call to the gate ended; it counts the gate as down after %s s without "
                         "an answer", self.down_after_s)
        self._poke()

    def _line(self, line: str) -> None:
        if not line.strip():
            return
        try:
            event = json.loads(line)
            handler = self._handlers.get(event.get("op")) if isinstance(event, dict) else None
            if handler is not None:
                handler(event)
        except (ValueError, KeyError, TypeError, AttributeError) as err:
            _log.warning("the front skipped an event from the gate it couldn't read (%s)", type(err).__name__)
        self.events_read += 1
        self._poke()

    def _replace_models(self, models: Any) -> None:
        if models is None:
            return
        try:
            self.models.replace(models)
        except ValueError:
            _log.warning("the front kept its model list: the gate's has an entry it can't read")

    def _on_hello(self, event: dict) -> None:
        self._replace_models(event.get("models"))
        if event.get("applying") is True:
            self._hold_all(_seconds(event.get("lapse_s")))
        elif event.get("applying") is False:
            self._release_all()

    def _on_state(self, event: dict) -> None:
        ready, starting, draining = (_names(event, name) for name in ("ready", "starting", "draining"))
        listed = ready | starting | draining
        now = self.clock()
        for model in self._listed - listed:
            self._left[model] = now  # gone from /running, by the gate's read
        self._listed, self._ready = listed, ready
        self._replace_models(event.get("models"))

    def _on_drain(self, event: dict) -> None:
        model, drain_id = _text(event, "model"), _text(event, "drain_id")
        why = event.get("why") if event.get("why") in DRAIN_WHY else "unknown"
        count = self.inflight.count(model)
        held = self._drains.get(model)
        if held is not None:  # the same drain asked again, or adopted under the gate's new id
            held.drain_id, held.why, held.taken = drain_id, why, False
            if count == 0:
                self._report_drained(model, held)
            return
        if why == "idle" and count > 0:  # a request in flight: it keeps serving, and that is use
            self._spawn(self.busy(model, drain_id))
            return
        drain = self._drains[model] = _Drain(drain_id, why)
        self._dirty.set()
        if count == 0:
            self._report_drained(model, drain)

    def _on_undrain(self, event: dict) -> None:
        model = _text(event, "model")
        drain_id = event.get("drain_id")
        held = self._drains.get(model)
        if held is None or (drain_id is not None and drain_id != held.drain_id):
            return  # an undrain under an id the front doesn't hold changes nothing
        self._end_drain(model, "undrain")

    def _on_unloaded(self, event: dict) -> None:
        model = _text(event, "model")
        self._ready.discard(model)
        if model in self._drains:
            self._end_drain(model, "unloaded")

    def _on_hold_all(self, event: dict) -> None:
        self._hold_all(_seconds(event.get("lapse_s")))

    def _on_release_all(self, event: dict) -> None:
        self._release_all()

    def _hold_all(self, lapse_s: float) -> None:
        if self._hold is None:
            self._hold = _Hold(lapse_s)
            _log.info("the front holds every new request: apply's restart")
        else:
            self._hold.lapse_s = lapse_s

    def _release_all(self) -> None:
        if self._hold is not None:
            self._hold.released = True
            self._hold = None
            _log.info("the front's hold for apply's restart ended")

    # The drains and the counts.

    def _end_drain(self, model: str, how: str) -> None:
        drain = self._drains.pop(model)
        drain.ended = how
        if how != "unloaded":
            self._ready.add(model)  # serving again
        self._dirty.set()
        self._poke()

    def _report_drained(self, model: str, drain: _Drain) -> None:
        drain.reported = True  # from the post's start: an answer lost on the way never undrains it
        self._send_drained(model, drain)

    def _send_drained(self, model: str, drain: _Drain) -> None:
        """`drained` posted for `drain`, in a task of its own, counted as under way from this step, not from the task's
        first step: a snapshot's turn that ends in between, in the same turn of the loop, finds it under way and doesn't
        send it again."""
        drain.posting += 1
        self._spawn(self._post_drained(model, drain))

    async def _post_drained(self, model: str, drain: _Drain) -> None:
        """`drained` posted for `drain`; `taken` once the gate took it under the id the drain still has."""
        drain_id = drain.drain_id
        try:
            taken = await self.drained(model, drain_id)
        finally:
            drain.posting -= 1
        if taken and drain.drain_id == drain_id:
            drain.taken = True

    def _drained_again(self) -> None:
        """Each held drain whose `drained` the gate didn't take, posted again (the controller's ruling, at Task 22's
        review), but none with a post under way."""
        for model, drain in list(self._drains.items()):
            if drain.reported and not drain.taken and not drain.posting:
                self._send_drained(model, drain)

    def _count_changed(self, model: str) -> None:
        """InFlight's hook, on every enter and leave, in its step."""
        self._dirty.set()
        drain = self._drains.get(model)
        if drain is not None and not drain.reported and self.inflight.count(model) == 0:
            self._report_drained(model, drain)

    async def _snapshots(self) -> None:
        """The whole snapshot, on every change and at least every `snapshot_every_s`, while the gate's call is open,
        and each `drained` the gate didn't take, again. A turn that breaks is logged by its type, and the next tried
        `retry_s` later, so the task never ends but at `aclose()`."""
        while True:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self.snapshot_every_s):
                    await self._dirty.wait()
            self._dirty.clear()
            try:
                if self.connected:
                    await self._post("/v1/front/inflight", self.inflight.snapshot(
                        models=[entry["name"] for entry in self.models], draining=list(self._drains)))
                    self._drained_again()
            except Exception as err:
                _log.error("the front's in-flight report to the gate broke: %s; it reports again", type(err).__name__)
                await asyncio.sleep(self.retry_s)


# The gate's socket.

class _GateTransport(httpx.AsyncHTTPTransport):
    """httpx's transport to the gate's socket at `path`, its connections _GateNetwork's: as many at once as there are
    calls, since an admit holds its own for its key's wait, and 8 kept open. An httpx client given a transport never
    reads its own `limits`, so they are set here (until 2026-10-14 they were the client's, unread, and httpx's defaults
    held: 100 at once, 20 kept)."""

    def __init__(self, path: Path):
        super().__init__(uds=str(path), trust_env=False)
        if not isinstance(getattr(self, "_pool", None), httpcore.AsyncConnectionPool):
            raise RuntimeError("httpx's transport no longer keeps the pool the gate link replaces")
        self._pool = httpcore.AsyncConnectionPool(uds=str(path), network_backend=_GateNetwork(), max_connections=None,
                                                  max_keepalive_connections=8, keepalive_expiry=5.0)


class _GateNetwork(httpcore.AsyncNetworkBackend):
    """httpcore's network for the gate link: each connection to the gate's socket on asyncio's own streams. httpx's
    default for a socket, httpcore's AnyIOBackend, uses anyio's UNIX socket stream (4.15), which waits for its socket
    to be readable on a future whose own `set_result` is the loop's read callback. A call cancelled in the turn of the
    loop whose select found its socket readable, before that turn's read callback ran, had that callback set the
    cancelled future's result, and the loop logged InvalidStateError with its traceback: the gate's answer landing as
    an admit's bound ended its call, or as its client went, or an event or a post's answer as aclose() ended it.
    asyncio's streams wake a wait only if it isn't cancelled, and what comes after a cancel stays in the stream's
    buffer, so a cancel at any turn ends the call and nothing else."""

    async def connect_unix_socket(self, path: str, timeout: float | None = None,
                                  socket_options: Iterable[Any] | None = None) -> httpcore.AsyncNetworkStream:
        """A connection to the socket at `path`; `socket_options`, which _GateTransport's pool never gives, unused."""
        try:
            async with asyncio.timeout(timeout):
                reader, writer = await asyncio.open_unix_connection(path)
        except TimeoutError:
            raise httpcore.ConnectTimeout("the gate's socket didn't answer in time") from None
        except OSError as err:
            raise httpcore.ConnectError(err) from err
        return _GateStream(reader, writer)

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _GateStream(httpcore.AsyncNetworkStream):
    """One connection to the gate's socket, as httpcore's AnyIOStream maps its ends: a timeout to httpcore's, a
    socket's error to its ReadError or WriteError, the gate's close to b""."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader, self._writer = reader, writer

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            async with asyncio.timeout(timeout):
                return await self._reader.read(max_bytes)
        except TimeoutError:
            raise httpcore.ReadTimeout("the gate didn't answer in time") from None
        except OSError as err:
            raise httpcore.ReadError(err) from err

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        if not buffer:
            return
        try:
            async with asyncio.timeout(timeout):
                self._writer.write(buffer)
                await self._writer.drain()
        except TimeoutError:
            raise httpcore.WriteTimeout("the gate didn't take the front's call in time") from None
        except OSError as err:
            raise httpcore.WriteError(err) from err

    async def aclose(self) -> None:
        # At once, as anyio's stream closes its socket: what's left unsent waits for no gate that has stopped reading.
        self._writer.transport.abort()

    def get_extra_info(self, info: str) -> Any:
        if info == "is_readable":  # an idle connection's end, the gate's close or a break, read by the loop or not yet
            reader = self._reader
            return reader.at_eof() or reader.exception() is not None or _readable(self._writer.get_extra_info("socket"))
        if info == "socket":
            return self._writer.get_extra_info("socket")
        return None


def _readable(sock: Any) -> bool:
    """Whether `sock` has something to read now, or is closed, as httpcore's own check reads it."""
    if sock is None or sock.fileno() < 0:
        return True
    poll = select.poll()
    poll.register(sock.fileno(), select.POLLIN)
    return bool(poll.poll(0))


def _admit_answer(response: httpx.Response) -> dict:
    """`/v1/admit`'s answer, checked as gateproto's AdmitAnswer shapes it, and copied."""
    try:
        answer = response.json()
    except ValueError:
        raise GateError("the gate's admit answer isn't JSON") from None
    if isinstance(answer, dict) and answer.get("ok") is True:
        return {"ok": True, "model": answer.get("model")}
    if isinstance(answer, dict) and answer.get("ok") is False:
        code, status, message, retry = (answer.get(name) for name in ("code", "status", "message", "retry_after_s"))
        if (isinstance(code, str) and code and _whole(status) and 400 <= status <= 599 and isinstance(message, str)
                and (retry is None or (_whole(retry) and retry >= 0))):
            return {"ok": False, "code": code, "status": status, "message": message, "retry_after_s": retry}
    raise GateError("the gate's admit answer isn't one its protocol has")


def _cut(text: str | None) -> str | None:
    if text is None or len(text) <= REFUSED_TEXT_MAX:
        return text
    return text[:REFUSED_TEXT_MAX - 1] + "…"


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(event: dict, name: str) -> str:
    value = event[name]
    if not isinstance(value, str) or not value:
        raise ValueError(f"an event's {name} isn't text")
    return value


def _names(event: dict, name: str) -> set[str]:
    value = event.get(name)
    if not isinstance(value, list) or not all(isinstance(each, str) for each in value):
        raise ValueError(f"an event's {name} isn't a list of names")
    return set(value)


def _seconds(value: Any) -> float:
    """A hold's lapse_s as the gate gives it; APPLY_LAPSE_S for one it gives that isn't a positive number."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return float(value)
    return float(APPLY_LAPSE_S)
