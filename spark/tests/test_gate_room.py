"""make-room and its hold, release, load and unload, the residents' preload, and the brake's release
(website/design/phase-2a.md, Task 17). Rules 4, 5 and 9 bind it (plan.md, *Admission and memory rules*): make-room
starts from the gate's own figure for free for a load, drains before every unload, and holds the room it frees for Dan,
counted against everyone else's loads; the brake's hold lifts by itself only once memory has stayed above the warn line
for 5 minutes and the reloads fit, at most once an hour, never after a reboot, and never reloading the model that was
loading when it fired.

The clock is injected, so waits of minutes run in milliseconds: a test moves it and calls `changed()`, as the core does
on every memory reading. llama-swap is an in-process stand-in on that clock (Swap): v257 as the gate's AsyncLlamaSwap
sees it, whose engines hold memory as they load and give it back as they stop, so MemAvailable follows them. The front
is the gate's side of its events call, answering every drain at once unless a test holds it. The brake's hold is a real
file in the test's folder. Every time, boot id, uid and pid here is a stand-in; none was read from the box."""

import asyncio
import logging
import socket
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import anyio
import pytest

from fake_llamaswap import FakeLlamaSwap, serve_fake
from spark import gateproto, messages, tickets
from spark.budget import ALL, Candidate
from spark.gate.admission import Admitted, Admitter
from spark.gate.policy import Drainer, FrontChannel
from spark.gate.room import Preloader, ReleaseResult, Room, RoomDone, backoff, brake_release_due
from spark.gate.state import BrakeMark, GateState, ModelRecord, Pin, RoomHold, Session
from spark.gateproto import (AUTO_RELEASE_EVERY_S, LOAD_CALL_TIMEOUT_S, PROGRESS_KINDS, RELEASE_AFTER_S,
                             RESIDENT_RELOAD_EVERY_S, STOPPING_EVERY_S)
from spark.hold import Hold, read_hold, release_hold, write_hold
from spark.llamaswap import LlamaSwapAnswered, Running
from spark.llamaswap_async import AsyncLlamaSwap, LlamaSwapNotSent, LoadOutcome
from spark.memory import MemInfo
from spark.registry import load_registry
from spark.render import HF_HOME, model_path

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
STACK = load_registry(STACK_REGISTRY)
TZ = "PDT+7"  # the tests' own zone, seven hours behind UTC, as test_messages.py has it
ZONE = timezone(timedelta(hours=-7))
BOUND_S = 5  # every async test's bound
BOOT = "b1"
IDLE = 117.0  # MemAvailable with nothing loaded: the registry's idle figure
KEY = "INTERNAL-KEY-STANDIN"  # stands for the gate's internal key
DAN, AGENT = 1000, 1001  # stand-in uids
LOGS = ("spark.gate.room", "spark.gate.policy", "spark.gate.admission")


def at(hour: int, minute: int, second: float = 0) -> float:
    return datetime(2026, 10, 8, hour, minute, tzinfo=ZONE).timestamp() + second


T = at(9, 0)


def name_of(label: str) -> str:
    return next(name for name, model in STACK.models.items() if model.label == label)


CODER, GEMMA, EMBED, WHISPER = name_of("the coder"), name_of("Gemma"), name_of("the embeddings"), name_of("whisper")
RESIDENTS = [GEMMA, EMBED, WHISPER]
# The stack's registry with the coder at Qwen3.8-27B's estimate, 41 GiB (Task 42's swap), as plan.md's examples have it.
REGISTRY = replace(STACK, models={**STACK.models, CODER: replace(STACK.models[CODER], footprint_gib=41.0)})
FOOTPRINT = {name: model.footprint_gib for name, model in REGISTRY.models.items()}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def local_zone(monkeypatch):
    """The clock read in ZONE, on a box called brightroar, as the words read them."""
    monkeypatch.setenv("TZ", TZ)
    time.tzset()
    monkeypatch.setattr(socket, "gethostname", lambda: "brightroar.local")
    yield
    monkeypatch.undo()
    time.tzset()


EMITTERS: list["Emitted"] = []


@pytest.fixture(autouse=True)
def no_hidden_errors(caplog):
    """Every notification sent must be one Task 7's words can build, and nothing may fail unseen: the gate's modules
    log what they couldn't do at ERROR and carry on, as the gate must."""
    EMITTERS.clear()
    for name in LOGS:
        caplog.set_level(logging.WARNING, logger=name)
    yield
    assert [emitted.errors for emitted in EMITTERS] == [[] for _ in EMITTERS]
    assert [r.getMessage() for r in caplog.records if r.name in LOGS and r.levelno >= logging.ERROR] == []


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Emitted:
    """A recording Emit: each notification checked against Task 7's words as the Notifier builds them."""

    def __init__(self, registry=REGISTRY):
        EMITTERS.append(self)
        self.registry = registry
        self.sent: list[tuple[str, str, dict]] = []
        self.errors: list[tuple[str, str]] = []

    def emit(self, type: str, event_key: str, /, **fields) -> None:  # noqa: A002 (the protocol's name)
        try:
            messages.notification(type, self.registry, **fields)
        except ValueError as err:
            self.errors.append((type, str(err)))
            raise
        self.sent.append((type, event_key, fields))

    def of(self, kind: str) -> list[tuple[str, dict]]:
        return [(key, fields) for sent, key, fields in self.sent if sent == kind]

    def texts(self, kind: str) -> list[str]:
        return [messages.notification(kind, self.registry, **fields).message for _, fields in self.of(kind)]


class Swap:
    """llama-swap v257 as the gate sees it through AsyncLlamaSwap, in process and on the tests' clock. `/running` lists
    every model that isn't stopped (internal/router/base.go:366-376). An engine holds memory once it is ready, its
    footprint unless `holds` says otherwise, and gives it back as it stops, so the World's MemAvailable follows it. A
    load starts the model and runs `spark launch`'s part (the ticket claimed, the start recorded under `started/`), then
    waits for the test: `ready`, or `drops` (the call ends unknown, the start going on, as v257's does). An unload goes
    as the test scripts it (`unload_as`), as v257's source gives each: "stops" (the default: stopping, then stopped,
    then OK), "holds" (the stop under way until `stop_done`), "not_sent" (no connection: LlamaSwapNotSent, nothing
    changes) and "404" (LlamaSwapAnswered before v257's Unload: nothing changes). `down` makes every call fail
    unanswered."""

    def __init__(self, clock: Clock, launch: Path):
        self.clock, self.launch = clock, launch
        self.states: dict[str, str] = {}
        self.held: dict[str, float] = {}  # what each engine holds now, in GiB
        self.holds: dict[str, float] = {}  # what a model's engine will hold once ready, when not its footprint
        self.calls: list[tuple[str, str, float]] = []
        self.down = False
        self.probes = 0  # /running calls, answered or not
        self._unload_as: dict[str, str] = {}
        self._held: dict[str, asyncio.Future] = {}
        self._loads: dict[str, asyncio.Future] = {}

    def loads(self) -> list[str]:
        return [model for kind, model, _ in self.calls if kind == "load"]

    def unloads(self) -> list[str]:
        return [model for kind, model, _ in self.calls if kind == "unload"]

    def unload_as(self, model: str, how: str) -> None:
        self._unload_as[model] = how

    def holding(self) -> float:
        return sum(self.held.get(model, 0.0) for model, state in self.states.items() if state != "stopped")

    async def running(self) -> list[Running]:
        self.probes += 1
        if self.down:
            raise LlamaSwapNotSent("llama-swap unreachable at the stand-in: ConnectError")
        return [Running(model, state) for model, state in sorted(self.states.items()) if state != "stopped"]

    def _stopped(self, model: str) -> None:
        self.states[model] = "stopped"
        self.held.pop(model, None)

    async def unload(self, model: str) -> None:
        if self.down:
            raise LlamaSwapNotSent("llama-swap unreachable at the stand-in: ConnectError")
        self.calls.append(("unload", model, self.clock()))
        how = self._unload_as.pop(model, "stops")
        if how == "not_sent":
            raise LlamaSwapNotSent("llama-swap unreachable at the stand-in: ConnectError")
        if how == "404":
            raise LlamaSwapAnswered(f"llama-swap POST /api/models/unload/{model}: HTTP 404")
        if self.states.get(model) in ("ready", "starting"):
            self.states[model] = "stopping"
        if how == "stops":
            self._stopped(model)
            call = self._loads.pop(model, None)
            if call is not None and not call.done():  # an unload aborts a start under way (fifo.go:230-242)
                call.set_result(LoadOutcome.failed(500, "unspecific error: group: model unloaded"))
            return
        held = asyncio.get_running_loop().create_future()
        self._held[model] = held
        await held

    def stop_done(self, model: str) -> None:
        self._stopped(model)
        held = self._held.pop(model, None)
        if held is not None:
            held.set_result("ok")

    async def load(self, model: str) -> LoadOutcome:
        if self.down:
            return LoadOutcome.unknown("refused")
        self.calls.append(("load", model, self.clock()))
        self.states[model] = "starting"
        claim = tickets.claim(self.launch, model, self.clock(), boot_id=BOOT)
        assert claim.ok, claim
        tickets.mark_started(self.launch, claim.ticket, self.clock())
        call = asyncio.get_running_loop().create_future()
        self._loads[model] = call
        return await call

    def ready(self, model: str) -> None:
        self.states[model] = "ready"
        self.held[model] = self.holds.get(model, FOOTPRINT[model])
        self._loads.pop(model).set_result(LoadOutcome.READY)

    def drops(self, model: str) -> None:
        """The load call ends unknown (the client's timeout, say); v257 goes on with the start."""
        self._loads.pop(model).set_result(LoadOutcome.unknown("timeout"))

    async def last_lines(self, model: str, n: int = 20, read_s: float = 1.0) -> list[str]:
        return []


class Front:
    """The front's side of its events call, as Task 22's front speaks it: what the gate sent, and, with `auto`, its
    `drained` for every drain at once, as a front with nothing in flight answers."""

    def __init__(self, channel: FrontChannel, *, auto: bool = True):
        self.channel = channel
        self.auto = auto
        self.seen: list[dict] = []
        self.queue = channel.open()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        self._serving = loop.create_task(self._serve()) if auto and loop is not None else None

    async def _serve(self) -> None:
        """The front reading its events call as they come, as Task 22's does."""
        while True:
            self._take(await self.queue.get())

    def _take(self, event: dict) -> None:
        self.seen.append(event)
        if self.auto and event["op"] == "drain":
            self.channel.drained(event["model"], event["drain_id"])

    def pump(self) -> None:
        while not self.queue.empty():
            self._take(self.queue.get_nowait())

    def drains(self) -> list[dict]:
        self.pump()
        return [event for event in self.seen if event["op"] == "drain"]


def record(name: str, *, fall: float | None = None, last_use: float = T - 60, state: str = "ready") -> ModelRecord:
    """A model loaded before the test starts; by default its load took its whole footprint, so it is owed nothing."""
    footprint = FOOTPRINT[name]
    return ModelRecord(name=name, footprint_gib=footprint, loaded_at=T - 3600,
                       load_fall_gib=footprint if fall is None else fall, fall_flagged=False,
                       rss_anon_at_load_gib=None, last_use=last_use, state=state)


def snapshot(**inflight: dict) -> dict:
    """The front's whole in-flight report: each model given its counts, nothing in flight elsewhere."""
    models = {name: {"count": 0, "oldest_started_at": None, "last_end_at": None, "requests": []} | counts
              for name, counts in inflight.items()}
    return {"seq": 1, "front_started_at": T - 86400, "models": models, "draining": []}


def fired_hold(fired: float, *, boot: str | None = BOOT, episode: int | None = 1, unloaded=(GEMMA, EMBED),
               loading: str | None = None, available: float | None = 19.6) -> Hold:
    """The brake's hold as Task 23's brake writes it: fired at `fired`, local time, to the second."""
    since = datetime.fromtimestamp(fired).isoformat(timespec="seconds")
    return Hold(since, f"{available} GiB available", tuple(unloaded), boot, episode, loading, available)


async def settle(world: "World | None" = None) -> None:
    """Let every task that can move, move: the room's, the drainer's, the admitter's, the stand-in's and the front's."""
    for _ in range(300):
        if world is not None and world.front is not None:
            world.front.pump()
        await asyncio.sleep(0)


class World:
    """Room with everything around it the test builds: the clock, launch's folder, a Hugging Face cache with every
    model's files, the state, the stand-in llama-swap (each record's model shown as its state, holding its load's fall),
    MemAvailable from what the engines hold and `other` (memory outside the stack), the admitter, the front's channel
    (answering every drain at once unless `auto` is false; none when `connected` is false), the drainer, the brake's
    hold folder, and each save (`saves`: make-room's hold as each save found it)."""

    def __init__(self, tmp_path: Path, *, models: dict[str, ModelRecord] | None = None, other: float = 0.0,
                 state: GateState | None = None, connected: bool = True, auto: bool = True, boot: str = BOOT,
                 hold: Hold | None = None):
        self.clock = Clock(T)
        self.launch = tmp_path / "launch"
        self.launch.mkdir(parents=True, exist_ok=True)
        self.hf_home = tmp_path / "hf"
        for model in REGISTRY.models.values():
            for file in filter(None, (model.source.file, model.source.mmproj)):
                path = self.hf_home / Path(model_path(model.source, file)).relative_to(HF_HOME)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"gguf")
        self.brake = tmp_path / "brake"
        self.brake.mkdir(exist_ok=True)
        if hold is not None:
            write_hold(self.brake, hold)
        self.state = state or GateState(boot_id=boot)
        self.state.models.update(models or {})
        self.other = other
        self.emitted = Emitted()
        self.swap = Swap(self.clock, self.launch)
        shown = {"draining": "ready"}
        for name, r in self.state.models.items():
            self.swap.states[name] = shown.get(r.state, r.state)
            self.swap.held[name] = r.load_fall_gib or 0.0
        self.saves: list[RoomHold | None] = []
        self.on_save = None
        self.admitter = Admitter(REGISTRY, self.state, self.swap, self.emitted, self.clock,
                                 read_mem=self.mem, read_hold=lambda: read_hold(self.brake), launch=self.launch,
                                 hf_home=str(self.hf_home), holders=lambda names: [], on_change=self.saved,
                                 poll_s=60.0)
        self.channel = FrontChannel()
        self.channel.report(snapshot())
        self.front = Front(self.channel, auto=auto) if connected else None
        self.drainer = Drainer(self.channel, self.swap, self.admitter, self.emitted, self.clock, on_change=self.saved,
                               poll_s=60.0)
        self.released: list[Hold | None] = []
        self.room = Room(self.state, REGISTRY, self.admitter, self.drainer, self.emitted, self.clock,
                         read_mem=self.mem, read_hold=lambda: read_hold(self.brake), release_hold=self.release,
                         boot_id=boot, front=self.channel, on_change=self.saved)

    @property
    def available(self) -> float:
        return IDLE - self.swap.holding() - self.other

    def mem(self) -> MemInfo:
        return MemInfo(121.6, self.available)

    def release(self, expected: Hold | None) -> bool:
        self.released.append(expected)
        return release_hold(self.brake, expected)

    def saved(self) -> None:
        self.saves.append(None if self.state.room_hold is None else replace(self.state.room_hold))
        if self.on_save is not None:
            self.on_save()

    async def at(self, when: float) -> None:
        """The clock at `when`, then a change, as the core reports every memory reading."""
        self.clock.now = when
        self.drainer.changed()
        self.admitter.changed()
        await settle(self)

    def task(self, coro) -> asyncio.Task:
        return asyncio.get_running_loop().create_task(coro)


async def collect(lines) -> list[dict]:
    return [line async for line in lines]


def all_loaded(**falls: float) -> dict[str, ModelRecord]:
    """The residents and the coder loaded, each holding its footprint unless `falls` says less."""
    return {name: record(name, fall=falls.get(name)) for name in (GEMMA, EMBED, WHISPER, CODER)}


def residents(**falls: float) -> dict[str, ModelRecord]:
    return {name: record(name, fall=falls.get(name)) for name in RESIDENTS}


def room_hold(size: float, *, unloaded=(), until: float | None = None, created: float = T - 60) -> RoomHold:
    return RoomHold(size_gib=size, until=until, boot_id=BOOT, created_at=created, unloaded=list(unloaded),
                    total_gib=size)


# make-room's plan (rule 4): everything it could unload, largest first, each marked, from the gate's own figure.

def test_make_room_lists_everything_largest_first_with_marks_and_requests_in_flight(tmp_path):
    world = World(tmp_path, models={**all_loaded(), CODER: record(CODER, last_use=T - 12 * 60)})
    world.state.pins[CODER] = Pin(model=CODER, until=None, by_uid=DAN)
    world.state.sessions["s1"] = Session(id="s1", uid=AGENT, pid=4242, model=CODER, label="agent's pi",
                                         started_at=T - 1200, renewed_at=T - 60, boot_id=BOOT, start_time=777)
    world.channel.report(snapshot(**{GEMMA: {"count": 1, "oldest_started_at": T - 180}}))
    plan_id, plan = world.room.plan(Decimal(40), DAN)
    assert plan_id and isinstance(plan_id, str)
    coder, gemma, embed, whisper = plan.candidates
    assert coder == Candidate(CODER, "the coder", 41.0, False, True, "agent's pi", 0, None, 12.0)
    assert (gemma.model, gemma.gib, gemma.resident, gemma.inflight, gemma.oldest_s, gemma.idle_min) == (
        GEMMA, 32.0, True, 1, 180.0, None)
    assert [(c.model, c.gib) for c in (embed, whisper)] == [(EMBED, 8.0), (WHISPER, 3.0)]
    # Its words, Task 7's, take it as it comes.
    rows = messages.room_list(40, 9, plan.candidates, plan.unload, plan.free_after_gib, registry=REGISTRY).splitlines()
    assert rows[1].endswith("loads when asked · pinned · session: agent's pi · idle 12 min")
    assert rows[2].endswith("always loaded · the web UI and photos use it · 1 request in flight for 3 min")
    # A session that isn't live, or a pin whose time has passed, marks nothing (Sessions.live, through `live`).
    world.room = Room(world.state, REGISTRY, world.admitter, world.drainer, world.emitted, world.clock,
                      read_mem=world.mem, read_hold=lambda: None, release_hold=world.release, boot_id=BOOT,
                      front=world.channel, live=lambda session: False)
    world.state.pins[CODER] = Pin(model=CODER, until=T - 1, by_uid=DAN)
    _, plan = world.room.plan(Decimal(40), DAN)
    assert (plan.candidates[0].pinned, plan.candidates[0].session) == (False, None)


def test_make_room_frees_until_its_size_is_free_for_a_load(tmp_path):
    # The residents and the coder loaded, nothing else running: 117 − 84 = 33 available, so 9 free for a load
    # (plan.md's make-room example).
    world = World(tmp_path, models=all_loaded())
    assert world.admitter.free_for_a_load(uses_hold=True) == 9
    _, plan = world.room.plan(Decimal(40), DAN)
    assert plan.unload == [CODER] and plan.free_after_gib == 50 and plan.enough


def test_make_room_starts_from_the_gates_own_figure_not_memavailable_less_the_reserve(tmp_path):
    # Where the CUDA ceiling binds, MemAvailable less the reserve overstates what is free for a load: make-room starts
    # from rule 9's figure (the controller's ruling at Task 6), so its retry isn't refused again.
    ceiling = replace(REGISTRY, budget=replace(REGISTRY.budget, allocatable_gib=90))
    world = World(tmp_path, models=all_loaded())
    world.admitter.registry = ceiling
    world.room.registry = ceiling
    assert world.admitter.free_for_a_load(uses_hold=True) == 6  # 90 − 84, not 33 − 24
    _, plan = world.room.plan(Decimal(40), DAN)
    assert plan.free_after_gib == 47 and plan.unload == [CODER]


def test_make_room_asked_too_much_shows_the_most_and_unloads_nothing_unconfirmed(tmp_path):
    # The examples' moment: the residents holding 27, 7 and 3 of their 43, Dan's python job 32: 48 available, 18 free
    # for a load.
    world = World(tmp_path, models=residents(**{GEMMA: 27, EMBED: 7}), other=32)
    assert world.available == 48 and world.admitter.free_for_a_load(uses_hold=True) == 18
    _, plan = world.room.plan(Decimal(70), DAN)
    assert not plan.enough and plan.most_gib == 61 and plan.unload == RESIDENTS
    assert world.swap.unloads() == [] and world.front.drains() == [] and world.state.room_hold is None


@pytest.mark.anyio
async def test_make_room_holds_the_room_until_done_its_time_or_the_next_boot(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=all_loaded())
        plan_id, _ = world.room.plan(Decimal(40), DAN)
        lines = await collect(world.room.execute(plan_id, 8 * 3600, DAN))
        assert lines[-1] == {"unloaded": ["the coder"], "free_gib": 50.0, "hold_gib": 40.0, "until": T + 8 * 3600,
                             "ended": []}
        assert set(lines[-1]) == set(gateproto.MakeRoomAnswer.__annotations__)
        hold = world.state.room_hold
        assert (hold.size_gib, hold.until, hold.boot_id, hold.unloaded, hold.total_gib) == (
            40.0, T + 8 * 3600, BOOT, [CODER], 40.0)
        assert messages.room_held(lines[-1]["unloaded"], lines[-1]["free_gib"], lines[-1]["hold_gib"], None) == (
            "Unloaded the coder. 50 GiB is free for a load, and 40 GiB of it is held for you until `spark make-room "
            "--done` or a reboot; your own requests can load into it, agent's and automatic reloads can't.")
        assert world.room.expire(T + 8 * 3600 - 1) is None and world.state.room_hold is hold
        assert world.room.expire(T + 8 * 3600) is hold and world.state.room_hold is None
        [(key, _)] = world.emitted.of("room_hold_ended")
        assert key == f"room:{hold.created_at}"
        assert world.emitted.texts("room_hold_ended") == [
            "make-room's hold for you ended (its time ran out), all 40 GiB of it unused. Nothing to reload: the coder "
            "loads on its next request."]
        # Without for_s, no end but --done or a reboot.
        world.channel.report(snapshot())
        plan_id, _ = world.room.plan(Decimal(40), DAN)
        await collect(world.room.execute(plan_id, None, DAN))
        assert world.state.room_hold.until is None
        assert world.room.expire(T + 365 * 86400) is None and world.state.room_hold is not None


@pytest.mark.anyio
async def test_make_room_all_holds_the_whole_box(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=all_loaded())
        plan_id, plan = world.room.plan(ALL, DAN)
        assert plan.unload == [CODER, GEMMA, EMBED, WHISPER] and plan.most_gib == 93
        world.other = 10  # a job of Dan's starts before he confirms
        lines = await collect(world.room.execute(plan_id, None, DAN))
        assert world.swap.unloads() == [CODER, GEMMA, EMBED, WHISPER] and world.state.models == {}
        # Every GiB then free for a load: 117 − 10 − 24 = 83, not the plan's 93.
        assert world.admitter.free_for_a_load(uses_hold=True) == 83
        assert world.state.room_hold.size_gib == 83.0 and lines[-1]["hold_gib"] == 83.0
        assert world.state.room_hold.total_gib == 83.0
        assert lines[-1]["unloaded"] == ["the coder", "Gemma", "the embeddings", "whisper"]
        assert world.admitter.free_for_a_load(uses_hold=False) == 0  # nothing for agent's keys or the reloads


@pytest.mark.anyio
async def test_unloading_a_pinned_model_ends_its_pin_and_status_says_whose(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=all_loaded())
        world.state.pins[CODER] = Pin(model=CODER, until=None, by_uid=DAN)
        world.state.sessions["s1"] = Session(id="s1", uid=AGENT, pid=4242, model=CODER, label="agent's pi",
                                             started_at=T - 1200, renewed_at=T - 60, boot_id=BOOT, start_time=777)
        plan_id, _ = world.room.plan(Decimal(40), DAN)
        lines = await collect(world.room.execute(plan_id, None, DAN))
        assert world.state.pins == {} and "s1" in world.state.sessions  # the session loses its model, not its life
        assert lines[-1]["ended"] == ["your pin on the coder", "the session agent's pi"]
        [recent] = world.state.recent
        assert (recent.at, recent.kind, recent.model, recent.uids) == (T, "make-room", CODER, [DAN, AGENT])
        assert recent.text == "make-room unloaded the coder: your pin on it ended, and agent's pi lost it."


@pytest.mark.anyio
async def test_execute_yields_each_drain_as_it_begins(tmp_path):
    with anyio.fail_after(BOUND_S):
        # A plan unloading the coder (1 request in flight) and Gemma: each drain's line as it begins, the result last.
        world = World(tmp_path, models=all_loaded(), auto=False)
        world.channel.report(snapshot(**{CODER: {"count": 1, "oldest_started_at": T - 30}}))
        plan_id, plan = world.room.plan(Decimal(60), DAN)
        assert plan.unload == [CODER, GEMMA]
        lines: list[dict] = []

        async def run():
            async for line in world.room.execute(plan_id, None, DAN):
                lines.append(line)

        task = world.task(run())
        await settle(world)
        first = {"kind": "unloading", "model": CODER, "label": "the coder", "inflight": 1}
        assert lines == [first] and set(first) == set(gateproto.MakeRoomProgress.__annotations__)
        [coder_drain] = world.front.drains()
        world.channel.report(snapshot())
        world.channel.drained(CODER, coder_drain["drain_id"])
        await settle(world)
        assert lines[1] == {"kind": "unloading", "model": GEMMA, "label": "Gemma", "inflight": 0}
        assert not task.done()
        world.channel.drained(GEMMA, world.front.drains()[-1]["drain_id"])
        await settle(world)
        assert task.done() and len(lines) == 3 and lines[-1]["unloaded"] == ["the coder", "Gemma"]
        assert {line.get("kind") for line in lines[:2]} <= set(PROGRESS_KINDS) and "kind" not in lines[-1]


@pytest.mark.anyio
async def test_the_hold_is_set_and_saved_before_the_first_drain_so_no_reload_takes_the_room(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Gemma's reload waits for memory while make-room unloads the coder. The hold stands, saved, before the first
        # drain is sent, so the room each unload frees is never open to the reload (rule 4).
        world = World(tmp_path, models={EMBED: record(EMBED), WHISPER: record(WHISPER), CODER: record(CODER)},
                      other=40, auto=False)
        assert world.admitter.free_for_a_load(uses_hold=False) == 1  # 117 − 52 − 40 − 24
        gemma = world.admitter.reload(GEMMA, "hold")
        await settle(world)
        assert world.swap.loads() == []
        plan_id, plan = world.room.plan(Decimal(42), DAN)
        assert plan.unload == [CODER]
        task = world.task(collect(world.room.execute(plan_id, None, DAN)))
        await settle(world)
        [drain] = world.front.drains()
        assert world.saves and world.saves[-1] is not None and world.saves[-1].size_gib == 42.0
        world.channel.drained(CODER, drain["drain_id"])
        await settle(world)
        assert task.done() and CODER not in world.state.models
        await world.at(T + 1)
        assert world.swap.loads() == [] and not gemma.done()  # 42 free for a load, all of it held for Dan
        [(_, waiting)] = world.emitted.of("resident_waiting")
        assert waiting["after"] == "hold"


@pytest.mark.anyio
async def test_no_automatic_reload_takes_the_held_room(tmp_path):
    with anyio.fail_after(BOUND_S):
        # 82 free for a load (106 available, less the reserve), 70 of it held: the preloader's Gemma (32) waits, and
        # says so once.
        world = World(tmp_path, other=11)
        world.state.room_hold = room_hold(70)
        assert world.admitter.free_for_a_load(uses_hold=True) == 82
        preloader = Preloader(world.admitter, REGISTRY, world.emitted, world.clock, llamaswap=world.swap)
        run = world.task(preloader.run([GEMMA], "boot"))
        await settle(world)
        await world.at(T + 30)
        assert world.swap.loads() == [] and not run.done()
        assert world.emitted.texts("resident_waiting") == [
            "Gemma didn't fit at boot: it needs 32 GiB, and 12 GiB is free for a load. It loads by itself once there's "
            "room."]
        run.cancel()


# The hold's end: --done, its time, a reboot or damage; the residents make-room unloaded reload, one at a time.

@pytest.mark.anyio
async def test_done_reloads_what_make_room_unloaded_one_at_a_time_if_they_fit(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={WHISPER: record(WHISPER)})
        world.state.room_hold = room_hold(40, unloaded=[GEMMA, EMBED, CODER])
        result = await world.room.release(DAN, room=True, brake=False)
        assert result == ReleaseResult(room=RoomDone(40.0, 40.0, "the coder"), brake=False,
                                       reloading=["Gemma", "the embeddings"])
        assert world.state.room_hold is None
        await settle(world)
        assert world.swap.loads() == [GEMMA]  # one at a time: the embeddings after Gemma
        world.swap.ready(GEMMA)
        await settle(world)
        assert world.swap.loads() == [GEMMA, EMBED]
        world.swap.ready(EMBED)
        await settle(world)
        assert world.swap.loads() == [GEMMA, EMBED] and CODER not in world.state.models  # the coder waits to be asked
        assert world.emitted.texts("room_hold_ended") == [
            "make-room's hold for you ended (`--done`), all 40 GiB of it unused. Reloading Gemma, then the embeddings; "
            "the coder loads on its next request."]
        assert messages.room_done(40, 40, result.reloading, "the coder") == (
            "Hold ended, all 40 GiB of it unused. Reloading Gemma, then the embeddings; the coder loads on its next "
            "request.")


@pytest.mark.anyio
async def test_done_with_nothing_to_reload_says_so(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=residents())
        world.state.room_hold = room_hold(40, unloaded=[CODER])
        result = await world.room.release(DAN, room=True, brake=False)
        assert result.reloading == [] and world.swap.loads() == []
        assert world.emitted.texts("room_hold_ended") == [
            "make-room's hold for you ended (`--done`), all 40 GiB of it unused. Nothing to reload: the coder loads "
            "on its next request."]


@pytest.mark.anyio
async def test_release_answers_the_fields_its_words_need(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=residents())
        world.state.room_hold = room_hold(40, unloaded=[CODER])
        result = await world.room.release(DAN, room=True, brake=False)
        assert result.room == RoomDone(40.0, 40.0, "the coder")
        assert set(vars(result.room)) == set(gateproto.RoomReleased.__annotations__)
        assert set(vars(result)) == set(gateproto.ReleaseAnswer.__annotations__)
        # A hold Dan's own loads shrank keeps the size it was set at, for *9 of its 41 GiB unused*.
        world2 = World(tmp_path / "2", models={EMBED: record(EMBED), WHISPER: record(WHISPER)})
        world2.state.room_hold = replace(room_hold(41, unloaded=[GEMMA]), size_gib=9.0)
        result = await world2.room.release(DAN, room=True, brake=False)
        assert result.room == RoomDone(9.0, 41.0, None) and result.reloading == ["Gemma"]
        assert messages.room_done(9, 41, result.reloading, None) == (
            "Hold ended, 9 of its 41 GiB unused. Reloading Gemma.")
        # Nothing held: nothing to end.
        assert await world.room.release(DAN, room=True, brake=False) == ReleaseResult(None, False, [])


@pytest.mark.anyio
async def test_a_hold_lost_at_a_reboot_or_to_damage_says_so_once(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path)
        # Another boot's hold, which restore ended (Task 13): the Spark rebooted.
        ended = replace(room_hold(40, unloaded=[GEMMA]), boot_id="b0", size_gib=9.0, total_gib=41.0)
        world.room.at_start(ended)
        assert world.emitted.texts("room_hold_ended") == [
            "make-room's hold for you ended (the Spark rebooted), 9 of its 41 GiB unused. Nothing to reload."]
        # A start fresh after damage: the gate can't tell whether a hold stood, so it says one may have, once.
        world.state.fresh_after_damage, world.state.damaged_at = True, T - 5
        world.room.at_start(None)
        world.room.at_start(None)
        [(key, fields)] = world.emitted.of("room_hold_ended")[1:]
        assert key == f"room:damaged:{T - 5}" and fields == {"why": "damaged"}
        assert world.emitted.texts("room_hold_ended")[1] == (
            "If make-room was holding room for you, that hold has ended: the gate's saved state was damaged, so it "
            "can't tell whether one stood. On the Spark, `spark make-room` holds room again.")
        world.state.fresh_after_damage = False
        world.room.at_start(None)
        assert len(world.emitted.of("room_hold_ended")) == 2


# A make-room run that a drain refuses part-way (the controller's ruling at Task 17).

@pytest.mark.anyio
async def test_a_run_refused_part_way_keeps_the_hold_and_says_what_it_unloaded(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=all_loaded())
        world.swap.unload_as(GEMMA, "not_sent")
        plan_id, plan = world.room.plan(Decimal(60), DAN)
        assert plan.unload == [CODER, GEMMA]
        lines = await collect(world.room.execute(plan_id, None, DAN))
        assert lines[-1] == {"code": None, "message": (
            "Unloaded the coder. Gemma wasn't unloaded: llama-swap didn't take the unload, so Gemma went on serving. "
            "On the Spark, `make logs s=llama-swap` shows why. The room freed so far stays held for you until "
            "`spark make-room --done` or a reboot.")}
        assert set(lines[-1]) == set(gateproto.GateRefusalBody.__annotations__)
        hold = world.state.room_hold
        assert (hold.size_gib, hold.unloaded) == (60.0, [CODER]) and GEMMA in world.state.models


@pytest.mark.anyio
async def test_a_run_that_unloaded_nothing_puts_back_the_hold_that_stood_before(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=all_loaded(), connected=False)
        before = room_hold(20, unloaded=[EMBED], created=T - 600)
        world.state.room_hold = before
        plan_id, _ = world.room.plan(Decimal(40), DAN)
        lines = await collect(world.room.execute(plan_id, None, DAN))
        assert lines == [{"kind": "unloading", "model": CODER, "label": "the coder", "inflight": 0},
                         {"code": None, "message": messages.front_not_connected("the coder")}]
        assert world.state.room_hold == before and world.swap.unloads() == []
        # With none before, none after.
        world.state.room_hold = None
        plan_id, _ = world.room.plan(Decimal(40), DAN)
        await collect(world.room.execute(plan_id, None, DAN))
        assert world.state.room_hold is None


@pytest.mark.anyio
async def test_a_plan_is_used_once_and_an_unknown_one_is_refused_in_words(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=all_loaded())
        plan_id, _ = world.room.plan(Decimal(40), DAN)
        await collect(world.room.execute(plan_id, None, DAN))
        again = await collect(world.room.execute(plan_id, None, DAN))
        assert again == [{"code": None, "message": messages.room_plan_gone()}]
        assert again[0]["message"] == ("That make-room plan isn't one the gate has: it was used already, or the gate "
                                       "restarted since. Run `spark make-room` again.")
        assert await collect(world.room.execute("nope", None, DAN)) == again
        for bad in (0, -5, float("nan"), float("inf")):
            plan_id, _ = world.room.plan(Decimal(40), DAN)
            assert await collect(world.room.execute(plan_id, bad, DAN)) == [
                {"code": None, "message": "A make-room hold's time has to be more than 0 seconds."}]
        with pytest.raises(ValueError):
            world.room.plan(Decimal(0), DAN)


# Dan's own load and unload.

@pytest.mark.anyio
async def test_unload_yields_the_count_first(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=all_loaded(), auto=False)
        world.channel.report(snapshot(**{CODER: {"count": 1, "oldest_started_at": T - 30}}))
        lines: list[dict] = []

        async def run():
            async for line in world.room.unload(CODER, DAN):
                lines.append(line)

        task = world.task(run())
        await settle(world)
        assert lines == [{"kind": "unloading", "inflight": 1}]
        assert set(lines[0]) == set(gateproto.UnloadCount.__annotations__)
        [drain] = world.front.drains()
        assert drain["why"] == "unload"
        world.channel.drained(CODER, drain["drain_id"])
        await settle(world)
        assert task.done() and lines[-1] == {"unloaded": True} and CODER not in world.state.models
        assert set(lines[-1]) == set(gateproto.UnloadDone.__annotations__)


@pytest.mark.anyio
async def test_an_unload_left_stopping_says_so_every_15_s(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The unload call begun, /running showing the coder stopping for 40 s: lines at 15 and 30 s from the call's
        # start, then the result once it is gone. The call's timeout is never llama_swap_down.
        world = World(tmp_path, models=all_loaded())
        world.swap.unload_as(CODER, "holds")
        lines: list[dict] = []

        async def run():
            async for line in world.room.unload(CODER, DAN):
                lines.append(line)

        task = world.task(run())
        await settle(world)
        stopping = {"kind": "stopping", "model": CODER, "label": "the coder"}
        assert STOPPING_EVERY_S == 15
        await world.at(T + 14)
        assert lines == [{"kind": "unloading", "inflight": 0}]
        await world.at(T + 15)
        await world.at(T + 30)
        assert lines[1:] == [stopping, stopping]
        await world.at(T + 39)
        world.swap.stop_done(CODER)
        await world.at(T + 40)
        assert task.done() and lines[1:] == [stopping, stopping, {"unloaded": True}]
        assert world.emitted.of("llama_swap_down") == []


@pytest.mark.anyio
async def test_an_unload_with_the_front_away_says_why_in_words(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=all_loaded(), connected=False)
        lines = await collect(world.room.unload(CODER, DAN))
        assert lines == [{"kind": "unloading", "inflight": 0},
                         {"code": None, "message": (
                             "The coder wasn't unloaded: the front isn't connected to the gate, so the gate can't wait "
                             "for the coder's requests in flight to finish first. On the Spark, `make logs s=front` "
                             "shows why.")}]
        assert world.swap.unloads() == [] and CODER in world.state.models


@pytest.mark.anyio
@pytest.mark.parametrize("how, end", [("not_sent", "not_taken"), ("404", "not_taken")])
async def test_an_unload_llama_swap_never_took_says_so(tmp_path, how, end):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=all_loaded())
        world.swap.unload_as(CODER, how)
        lines = await collect(world.room.unload(CODER, DAN))
        assert lines[-1] == {"code": None, "message": messages.not_unloaded(end, "the coder")}
        assert CODER in world.state.models and world.state.models[CODER].state == "ready"


@pytest.mark.anyio
async def test_an_unload_of_a_model_not_loaded_or_unknown_is_refused_never_said_done(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=residents())
        assert await collect(world.room.unload(CODER, DAN)) == [
            {"code": None, "message": "The coder isn't loaded, so there's nothing to unload."}]
        assert await collect(world.room.unload("nope", DAN)) == [
            {"code": None, "message": "There's no model 'nope' in the registry, so there's nothing to unload."}]
        assert await collect(world.room.load("nope", DAN)) == [
            {"code": None, "message": "There's no model 'nope' in the registry, so there's nothing to load."}]
        assert world.swap.unloads() == [] and world.swap.loads() == []


@pytest.mark.anyio
async def test_load_yields_its_start_then_its_result(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The coder's last load took 24 s: its LoadProgress says so as the load starts, then the confirmation's fields.
        world = World(tmp_path, models=residents())
        first = world.task(collect(world.room.load(CODER, DAN)))
        await settle(world)
        world.clock.now = T + 24
        world.swap.ready(CODER)
        await settle(world)
        assert first.result()[-1] == {"label": "the coder", "seconds": 24.0, "idle_min": 60, "command": "coder"}
        await collect(world.room.unload(CODER, DAN))
        assert CODER not in world.state.models
        second = world.task(collect(world.room.load(CODER, DAN)))
        await settle(world)
        world.clock.now = T + 50
        world.swap.ready(CODER)
        await settle(world)
        start, result = second.result()
        assert start == {"kind": "loading", "model": CODER, "label": "the coder", "last_s": 24.0}
        assert set(result) == set(gateproto.LoadConfirmation.__annotations__) and result["seconds"] == 26.0
        # Loaded already: the confirmation alone, seconds None; a resident has no idle time.
        assert await collect(world.room.load(CODER, DAN)) == [
            {"label": "the coder", "seconds": None, "idle_min": 60, "command": "coder"}]
        assert await collect(world.room.load(GEMMA, DAN)) == [
            {"label": "Gemma", "seconds": None, "idle_min": None, "command": "small"}]


@pytest.mark.anyio
async def test_load_yields_its_wait_before_its_start(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The coder's load queued behind Gemma's: a WaitProgress, why slot, loading_label Gemma, while Gemma loads.
        world = World(tmp_path, models={EMBED: record(EMBED), WHISPER: record(WHISPER)})
        gemma = world.admitter.reload(GEMMA, "boot")
        await settle(world)
        lines = world.task(collect(world.room.load(CODER, DAN)))
        await settle(world)
        world.swap.ready(GEMMA)
        await settle(world)
        assert isinstance(gemma.result(), Admitted)
        world.clock.now = T + 24
        world.swap.ready(CODER)
        await settle(world)
        wait, start, result = lines.result()
        assert wait == dict.fromkeys(gateproto.WaitProgress.__annotations__) | {
            "kind": "waiting", "model": CODER, "label": "the coder", "why": "slot", "loading_label": "Gemma"}
        assert start == {"kind": "loading", "model": CODER, "label": "the coder", "last_s": None}
        assert result["seconds"] == 24.0


@pytest.mark.anyio
async def test_a_load_is_one_of_dans_commands_and_may_load_into_his_hold(tmp_path):
    with anyio.fail_after(BOUND_S):
        # 50 free for a load, 41 of it held for Dan: his load takes the 9 outside the hold first, and the hold shrinks
        # to 9 (rule 4); agent's keys couldn't have.
        world = World(tmp_path, models=residents(), other=24)
        world.state.room_hold = room_hold(41)
        assert world.admitter.free_for_a_load(uses_hold=True) == 26
        lines = world.task(collect(world.room.load(CODER, DAN)))
        await settle(world)
        assert world.swap.loads() == []  # 26 isn't 41
        world.other = 0
        await world.at(T + 1)
        world.swap.ready(CODER)
        await settle(world)
        assert lines.result()[-1]["label"] == "the coder"
        assert world.state.room_hold.size_gib == 9.0 and world.state.room_hold.total_gib == 41.0
        assert not world.state.refusals


@pytest.mark.anyio
async def test_a_refused_load_says_so_last(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=residents(), other=40)
        lines = world.task(collect(world.room.load(CODER, DAN)))
        await settle(world)
        await world.at(T + LOAD_CALL_TIMEOUT_S)
        *_, last = lines.result()
        assert last["code"] == "no_fit" and last["message"].startswith("The coder didn't load: it needs 41 GiB")
        assert set(last) == set(gateproto.GateRefusalBody.__annotations__)


# The residents' preload: one at a time, through admission, after llama-swap answers.

@pytest.mark.anyio
async def test_the_preload_waits_for_llama_swap_then_loads_one_at_a_time(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path)
        world.swap.down = True
        slept: list[float] = []
        asked: list[tuple[str, str]] = []
        reload = world.admitter.reload

        def spy(model, after):
            asked.append((model, after))
            return reload(model, after)

        world.admitter.reload = spy

        async def sleep(seconds: float) -> None:
            slept.append(seconds)
            if len(slept) == 3:
                world.swap.down = False  # llama-swap answers on the fourth try
            await asyncio.sleep(0)

        preloader = Preloader(world.admitter, REGISTRY, world.emitted, world.clock, llamaswap=world.swap, sleep=sleep)
        run = world.task(preloader.run(RESIDENTS, "boot"))
        await settle(world)
        assert slept == [1, 2, 4] and world.swap.probes == 4
        assert asked == [(GEMMA, "boot"), (EMBED, "boot"), (WHISPER, "boot")]  # queued in one step, in this order
        assert world.swap.loads() == [GEMMA]
        world.swap.ready(GEMMA)
        await settle(world)
        assert world.swap.loads() == [GEMMA, EMBED]
        world.swap.ready(EMBED)
        await settle(world)
        world.swap.ready(WHISPER)
        await settle(world)
        results = run.result()
        assert list(results) == RESIDENTS and all(isinstance(r, Admitted) for r in results.values())


def test_the_preloads_backoff_doubles_to_30_s():
    assert list(backoff(8)) == [1, 2, 4, 8, 16, 30, 30, 30]


@pytest.mark.anyio
async def test_the_preload_skips_the_model_the_brake_marked(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Rule 5: the model loading when the brake fired doesn't reload by itself, and a reload of it would wait in
        # the queue for ever, so the preload leaves it out.
        world = World(tmp_path)
        world.state.brake_marks[GEMMA] = BrakeMark(GEMMA, T - 3600, 26.0)
        preloader = Preloader(world.admitter, REGISTRY, world.emitted, world.clock, llamaswap=world.swap)
        run = world.task(preloader.run(RESIDENTS, "boot"))
        await settle(world)
        assert world.swap.loads() == [EMBED]
        world.swap.ready(EMBED)
        await settle(world)
        world.swap.ready(WHISPER)
        await settle(world)
        assert list(run.result()) == [EMBED, WHISPER]


@pytest.mark.anyio
async def test_a_preload_that_goes_leaves_its_reloads_queued_and_ends_quietly_when_the_gate_stops(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Its caller going ends only its wait: the reload stays queued. The gate stopping cancels the reloads
        # themselves, and the preload ends with what it has, never raising the gate's own cancellation.
        world = World(tmp_path, other=100)  # nothing fits: Gemma waits for memory
        preloader = Preloader(world.admitter, REGISTRY, world.emitted, world.clock, llamaswap=world.swap)
        run = world.task(preloader.run([GEMMA], "boot"))
        await settle(world)
        run.cancel()
        await settle(world)
        assert run.cancelled() and [row["model"] for row in world.admitter.waiting()] == [GEMMA]
        run = world.task(preloader.run([EMBED], "boot"))
        await settle(world)
        await world.admitter.stop()
        await settle(world)
        assert run.result() == {}


@pytest.mark.anyio
async def test_the_residents_come_back_after_an_unplanned_llama_swap_restart(tmp_path):
    with anyio.fail_after(BOUND_S * 2):
        # The v257 stand-in restarted on its port (Task 12's note): every engine gone, no apply announced. The
        # preloader waits for it, then reloads the three residents one at a time through admission.
        launch, hf_home = tmp_path / "launch", tmp_path / "hf"
        launch.mkdir()
        for model in REGISTRY.models.values():
            for file in filter(None, (model.source.file, model.source.mmproj)):
                path = hf_home / Path(model_path(model.source, file)).relative_to(HF_HOME)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"gguf")
        fake = FakeLlamaSwap({KEY}, list(REGISTRY.models))
        for model in RESIDENTS:
            fake.set_state(model, "ready")
            fake.script_start(model, delay_s=0.05)
        emitted = Emitted()
        state = GateState(boot_id=BOOT)
        async with serve_fake(fake) as url, AsyncLlamaSwap(url, KEY, load_timeout_s=5) as swap:
            spans: list[tuple[str, float, float]] = []
            load = swap.load

            async def timed(model):
                began = time.monotonic()
                try:
                    return await load(model)
                finally:
                    spans.append((model, began, time.monotonic()))

            swap.load = timed
            admitter = Admitter(REGISTRY, state, swap, emitted, time.time,
                                read_mem=lambda: MemInfo(121.6, IDLE), read_hold=lambda: None, launch=launch,
                                hf_home=str(hf_home), holders=lambda names: [], poll_s=0.05)
            slept: list[float] = []

            async def sleep(seconds: float) -> None:
                slept.append(seconds)
                await asyncio.sleep(0.05)

            restart = asyncio.create_task(fake.restart(down_s=0.3))
            while True:  # until it has stopped answering, so the preloader meets it down
                try:
                    await swap.running()
                except LlamaSwapNotSent:
                    break
                await asyncio.sleep(0.01)
            preloader = Preloader(admitter, REGISTRY, emitted, time.time, llamaswap=swap, sleep=sleep)
            results = await preloader.run(RESIDENTS, "restart")
            await restart
            assert slept[:2] == [1, 2]  # it waited for llama-swap to answer again, backing off
            assert all(isinstance(result, Admitted) and result.loaded_now for result in results.values())
            assert [model for model, _, _ in spans] == RESIDENTS
            assert all(spans[i][2] <= spans[i + 1][1] for i in range(len(spans) - 1))  # each after the last
            assert [m.model for m in await swap.running()] == sorted(RESIDENTS)
            await admitter.stop()


# The residents' rule (Tasks 17 and 18; the controller's rulings at Task 17): a resident that leaves /running without
# the gate or the brake unloading it reloads, at most once an hour after a crash or a start the gate aborted.

@pytest.mark.anyio
async def test_a_resident_killed_outside_the_gate_reloads(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models=residents())
        # Gemma leaves /running with no drain and no brake: an earlyoom kill, a crash.
        world.swap._stopped(GEMMA)
        del world.state.models[GEMMA]
        assert world.room.resident_left(GEMMA, shown="ready", since=T - 1) is True
        await settle(world)
        assert world.swap.loads() == [GEMMA] and world.state.resident_reloads == {GEMMA: T}
        world.swap.ready(GEMMA)
        await settle(world)
        # The embeddings unloaded by the brake: not now, but at the brake's release.
        write_hold(world.brake, fired_hold(T + 60, unloaded=(EMBED,)))
        world.swap._stopped(EMBED)
        del world.state.models[EMBED]
        world.clock.now = T + 61
        assert world.room.resident_left(EMBED, shown="ready", since=T + 59) is False
        # Nor whisper, which a crash took while the hold stood: the release reloads it with the brake's.
        world.swap._stopped(WHISPER)
        del world.state.models[WHISPER]
        assert world.room.resident_left(WHISPER, shown="ready", since=T + 59) is False
        await settle(world)
        assert world.swap.loads() == [GEMMA]
        result = await world.room.release(DAN, room=False, brake=True)
        assert result == ReleaseResult(None, True, ["the embeddings", "whisper"])
        assert world.released == [None] and read_hold(world.brake) is None
        assert [(e.kind, e.text, e.uids) for e in world.state.recent] == [
            ("brake_released", "brake released with `make brake-release`; reloading the embeddings and whisper", [DAN])]
        await settle(world)
        assert world.swap.loads() == [GEMMA, EMBED]
        assert messages.brake_released_by_dan(result.reloading) == (
            "New loads resume. Reloading the embeddings, then whisper…")


@pytest.mark.anyio
@pytest.mark.parametrize("why, reloads", [("unload", False), ("make-room", False), ("unknown", True),
                                          ("late_start", True)])
async def test_the_gates_own_unload_reloads_a_resident_only_when_it_wasnt_dans_choice(tmp_path, why, reloads):
    with anyio.fail_after(BOUND_S):
        # Dan's `spark unload` and make-room's unloads were his choice (make-room's reload comes when its hold ends);
        # an unload after damage (`unknown`, Task 18) or a start the gate aborted (`late_start`, Task 15) wasn't.
        world = World(tmp_path, models=residents())
        if why == "late_start":  # Task 15's abort of a start past its deadline, resumed after a restart (Task 16)
            gemma = world.state.models[GEMMA]
            gemma.state, gemma.unload_requested_at, gemma.drain_why = "draining", T - 5, "late_start"
            world.swap.states[GEMMA] = "starting"
            drain = world.task(world.drainer.resume(GEMMA, None, "late_start"))
        else:
            drain = world.task(world.drainer.drain(GEMMA, why))
        await settle(world)
        assert drain.result() is True and GEMMA not in world.state.models
        assert world.room.resident_left(GEMMA, shown="ready", since=T - 1) is reloads
        await settle(world)
        assert world.swap.loads() == ([GEMMA] if reloads else [])
        assert (GEMMA in world.state.resident_reloads) is reloads


def test_a_model_that_isnt_a_resident_never_reloads_by_the_rule(tmp_path):
    world = World(tmp_path)
    assert world.room.resident_left(CODER, shown="ready", since=T - 1) is False
    assert world.room.resident_left("nope", shown="ready", since=T - 1) is False


@pytest.mark.anyio
async def test_two_late_starts_in_an_hour_reload_once_then_wait_for_dan(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Gemma's start hangs past its deadline: the gate aborts it (Task 15), and the residents' rule reloads it once.
        # Its second start hangs too, within the hour: no reload, and resident_waiting says it won't reload by itself.
        world = World(tmp_path, models={EMBED: record(EMBED), WHISPER: record(WHISPER)})
        first = world.admitter.reload(GEMMA, "boot")
        asked: list[tuple[str, str]] = []
        reload = world.admitter.reload

        def spy(model, after):
            asked.append((model, after))
            return reload(model, after)

        world.admitter.reload = spy
        await settle(world)
        world.swap.drops(GEMMA)
        await settle(world)
        await world.at(T + 201)  # past its start's deadline: 180 s, and 20 more
        assert first.result().code == "load_failed" and GEMMA not in world.swap.held
        assert world.room.resident_left(GEMMA, shown="starting", since=T + 200) is True
        await settle(world)
        assert world.swap.loads() == [GEMMA, GEMMA] and world.state.resident_reloads == {GEMMA: T + 201}
        assert asked == [(GEMMA, "gate")]  # the gate's own abort, never read as a crash
        world.swap.drops(GEMMA)
        await settle(world)
        await world.at(T + 402)
        assert world.room.resident_left(GEMMA, shown="starting", since=T + 401) is False
        assert world.room.resident_left(GEMMA, shown="starting", since=T + 401) is False
        await settle(world)
        assert world.swap.loads() == [GEMMA, GEMMA]
        [(key, fields)] = [(k, f) for k, f in world.emitted.of("resident_waiting") if f.get("again")]
        assert key == f"resident-again:{GEMMA}:{T + 201}" and fields["after"] == "gate"
        [text] = [messages.notification("resident_waiting", REGISTRY, **fields).message]
        assert text == ("Gemma stopped again within an hour of its last automatic reload, so it won't reload by "
                        "itself. On the Spark, `spark load small` loads it.")
        assert messages.pi_retry_match(text) is None
        # An hour after its reload, it reloads by itself again.
        assert RESIDENT_RELOAD_EVERY_S == 3600
        world.clock.now = T + 201 + 3600
        assert world.room.resident_left(GEMMA, shown="starting", since=T + 401) is True
        await settle(world)
        assert world.swap.loads() == [GEMMA, GEMMA, GEMMA]


@pytest.mark.anyio
async def test_dans_load_clears_the_residents_bound(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={EMBED: record(EMBED), WHISPER: record(WHISPER)})
        world.state.resident_reloads[GEMMA] = T - 60
        assert world.room.resident_left(GEMMA, shown="ready", since=T - 1) is False
        lines = world.task(collect(world.room.load(GEMMA, DAN)))
        await settle(world)
        world.swap.ready(GEMMA)
        await settle(world)
        assert lines.result()[-1]["label"] == "Gemma" and GEMMA not in world.state.resident_reloads
        world.swap._stopped(GEMMA)
        del world.state.models[GEMMA]
        assert world.room.resident_left(GEMMA, shown="ready", since=T - 1) is True


# The brake's release (rule 5): after 5 minutes above the warn line, if the reloads fit; at most once an hour; never
# after a reboot; never the model that was loading when it fired.

def test_brake_release_due_decides_by_rule_5():
    state = GateState(boot_id=BOOT)
    hold = fired_hold(T - 600)
    assert RELEASE_AFTER_S == 300 and AUTO_RELEASE_EVERY_S == 3600
    due = brake_release_due
    assert due(T + 299, T, hold, state, reloads_fit=True, boot_id=BOOT) == "wait"
    assert due(T + 300, T, hold, state, reloads_fit=True, boot_id=BOOT) == "release"
    assert due(T + 300, T, hold, state, reloads_fit=False, boot_id=BOOT) == "wait"
    assert due(T + 300, None, hold, state, reloads_fit=True, boot_id=BOOT) == "wait"  # under the line now
    assert due(T + 300, T, hold, state, reloads_fit=True, boot_id="b2") == "needs_dan"
    assert due(T + 300, T, replace(hold, boot_id=None), state, reloads_fit=True, boot_id=BOOT) == "needs_dan"
    # Fired above the line (the rate-of-fall watch): the five minutes count from the brake, never from before it.
    assert due(T + 300, T, fired_hold(T + 60), state, reloads_fit=True, boot_id=BOOT) == "wait"
    assert due(T + 360, T, fired_hold(T + 60), state, reloads_fit=True, boot_id=BOOT) == "release"
    # A time that doesn't parse can't be counted from: it waits for Dan.
    assert due(T + 900, T, replace(hold, since="garbled"), state, reloads_fit=True, boot_id=BOOT) == "needs_dan"
    # Within the hour after an automatic release, Dan's.
    state.last_auto_release_at = T - 1800
    assert due(T + 300, T, hold, state, reloads_fit=True, boot_id=BOOT) == "needs_dan"


@pytest.mark.anyio
async def test_the_brake_releases_after_5_minutes_above_28_if_the_reloads_fit(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={WHISPER: record(WHISPER)}, hold=fired_hold(T - 600))
        # The last automatic release was assumed, after a damaged state: a real one clears that (Task 13's re-review).
        world.state.last_auto_release_at, world.state.release_assumed = T - 7200, True
        world.clock.now = T + 299
        assert await world.room.check_brake(T + 299, above_since=T) == "wait"
        assert read_hold(world.brake) is not None
        world.clock.now = T + 300
        assert await world.room.check_brake(T + 300, above_since=T) == "release"
        assert read_hold(world.brake) is None and world.released == [fired_hold(T - 600)]
        assert (world.state.last_auto_release_at, world.state.release_assumed) == (T + 300, False)
        [(key, _)] = world.emitted.of("brake_released")
        assert key == f"release:{BOOT}:1"
        assert world.emitted.texts("brake_released") == [
            "Brake released at 09:05, 114 GiB available. Reloading Gemma, then the embeddings."]
        # *recent* says so, as plan.md's `spark status` example has it (the controller's ruling at Task 17).
        [event] = world.state.recent
        assert (event.at, event.kind, event.text) == (T + 300, "brake_released",
                                                      "brake released; reloading Gemma and the embeddings")
        await settle(world)
        assert world.swap.loads() == [GEMMA]
        world.swap.ready(GEMMA)
        await settle(world)
        assert world.swap.loads() == [GEMMA, EMBED]
        assert await world.room.check_brake(T + 600, above_since=T) is None  # no hold, nothing to do


@pytest.mark.anyio
async def test_the_brake_waits_while_the_reloads_wouldnt_fit(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={WHISPER: record(WHISPER)}, other=74, hold=fired_hold(T - 600))
        assert world.admitter.free_for_a_load(uses_hold=False) == 16  # 117 − 3 − 74 − 24: Gemma is 32
        world.clock.now = T + 300
        assert await world.room.check_brake(T + 300, above_since=T) == "wait"
        assert read_hold(world.brake) is not None and world.emitted.of("brake_released") == []
        # A make-room hold counts too: rule 9's formula, with any hold of Dan's.
        world.other = 0
        world.state.room_hold = room_hold(60)
        assert await world.room.check_brake(T + 300, above_since=T) == "wait"
        world.state.room_hold = None
        assert await world.room.check_brake(T + 300, above_since=T) == "release"


@pytest.mark.anyio
async def test_at_most_one_automatic_release_an_hour(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={WHISPER: record(WHISPER)}, hold=fired_hold(T + 1800))
        world.state.last_auto_release_at = T
        world.clock.now = T + 2100
        assert await world.room.check_brake(T + 2100, above_since=T + 1800) == "needs_dan"
        assert read_hold(world.brake) is not None
        # That brake's own brake_fired said it waits for Dan (Task 14): no brake_needs_release.
        assert world.emitted.of("brake_needs_release") == [] and world.emitted.of("brake_released") == []


@pytest.mark.anyio
async def test_a_hold_from_a_previous_boot_waits_for_dan(tmp_path):
    with anyio.fail_after(BOUND_S):
        hold = fired_hold(at(3, 12), boot="b0", unloaded=(GEMMA,))
        world = World(tmp_path, hold=hold, boot="b2")
        for _ in range(2):
            assert await world.room.check_brake(T + 900, above_since=T) == "needs_dan"
        [(key, fields)] = world.emitted.of("brake_needs_release")
        assert key == "brake-needs:b0:1"  # Task 14's key for the same brake, read from its events
        assert world.emitted.texts("brake_needs_release") == [
            "After the reboot, new loads are still paused from the brake at 03:12 (19.6 GiB available; it unloaded "
            "Gemma). On the Spark, `make brake-release` resumes them."]
        assert read_hold(world.brake) == hold
        # A Phase 1 hold: no boot, no episode, no reading.
        phase_1 = Hold(datetime.fromtimestamp(at(2, 58)).isoformat(timespec="seconds"), "low", (EMBED,))
        write_hold(world.brake, phase_1)
        assert await world.room.check_brake(T + 900, above_since=T) == "needs_dan"
        assert world.emitted.of("brake_needs_release")[1][0] == f"brake-needs:phase-1:{phase_1.since}"
        assert world.emitted.texts("brake_needs_release")[1] == (
            "After the reboot, new loads are still paused from the brake at 02:58 (it unloaded the embeddings). On the "
            "Spark, `make brake-release` resumes them.")
        # A hold file that can't be read says when nothing: it waits for Dan, and isn't worded with a time it lacks.
        (world.brake / "hold.json").write_text("{not json")
        assert await world.room.check_brake(T + 900, above_since=T) == "needs_dan"
        assert len(world.emitted.of("brake_needs_release")) == 2


@pytest.mark.anyio
async def test_the_model_loading_when_the_brake_fired_isnt_reloaded(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={WHISPER: record(WHISPER)},
                      hold=fired_hold(T - 600, unloaded=(CODER, GEMMA, EMBED), loading=CODER))
        world.clock.now = T + 300
        assert await world.room.check_brake(T + 300, above_since=T) == "release"
        assert world.emitted.texts("brake_released") == [
            "Brake released at 09:05, 114 GiB available. Reloading Gemma, then the embeddings. The coder was loading "
            "when it fired, so it loads again only when you ask."]
        await settle(world)
        world.swap.ready(GEMMA)
        await settle(world)
        world.swap.ready(EMBED)
        await settle(world)
        assert world.swap.loads() == [GEMMA, EMBED]
        # A resident that was loading is left out too, and so is any model the brake marked.
        world2 = World(tmp_path / "2", hold=fired_hold(T - 600, unloaded=(GEMMA, EMBED, WHISPER), loading=GEMMA))
        world2.state.brake_marks[EMBED] = BrakeMark(EMBED, T - 7200, None)
        world2.clock.now = T + 300
        assert await world2.room.check_brake(T + 300, above_since=T) == "release"
        await settle(world2)
        assert world2.swap.loads() == [WHISPER]
        assert world2.emitted.texts("brake_released")[0].endswith(
            "Reloading whisper. Gemma was loading when it fired, so it loads again only when you ask.")


@pytest.mark.anyio
async def test_the_release_never_lifts_a_newer_brakes_hold(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The brake fires again between the gate's judgement and its removal of the hold: the new hold stands, the
        # release counts as none, and nothing reloads.
        world = World(tmp_path, models={WHISPER: record(WHISPER)}, hold=fired_hold(T - 600))
        newer = fired_hold(T + 299, episode=2, unloaded=(WHISPER,))

        def brake_fires_again():
            if read_hold(world.brake) != newer:
                write_hold(world.brake, newer)

        world.on_save = brake_fires_again
        world.clock.now = T + 300
        assert await world.room.check_brake(T + 300, above_since=T) == "wait"
        assert read_hold(world.brake) == newer
        assert world.state.last_auto_release_at is None and world.emitted.of("brake_released") == []
        await settle(world)
        assert world.swap.loads() == []


@pytest.mark.anyio
async def test_a_release_whose_save_fails_lifts_nothing(tmp_path, caplog):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={WHISPER: record(WHISPER)}, hold=fired_hold(T - 600))

        def fails():
            raise OSError("disk full")

        world.room._on_change = fails
        world.clock.now = T + 300
        assert await world.room.check_brake(T + 300, above_since=T) == "wait"
        assert read_hold(world.brake) is not None and world.state.last_auto_release_at is None
        assert any("couldn't save" in r.getMessage() for r in caplog.records if r.name == "spark.gate.room")
        caplog.clear()


@pytest.mark.anyio
async def test_done_ends_only_the_room_hold(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={WHISPER: record(WHISPER)}, hold=fired_hold(T - 600))
        world.state.room_hold = room_hold(40, unloaded=[GEMMA])
        result = await world.room.release(DAN, room=True, brake=False)
        assert world.state.room_hold is None and read_hold(world.brake) is not None
        assert result == ReleaseResult(RoomDone(40.0, 40.0, None), False, ["Gemma"])
        assert messages.room_done(40, 40, result.reloading, None) == (
            "Hold ended, all 40 GiB of it unused. Reloading Gemma.")
        await settle(world)
        assert world.swap.loads() == []  # queued, but the brake's hold stands: nothing loads until it lifts


@pytest.mark.anyio
async def test_brake_release_ends_only_the_brakes_hold(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Dan's hold stands, 80 GiB of the 90 free for a load: Gemma's reload, after the brake's release, is kept out
        # of it, and waits.
        world = World(tmp_path, models={WHISPER: record(WHISPER)}, hold=fired_hold(T - 600, unloaded=(GEMMA,)))
        world.state.room_hold = room_hold(80)
        result = await world.room.release(DAN, room=False, brake=True)
        assert result == ReleaseResult(None, True, ["Gemma"])
        assert read_hold(world.brake) is None and world.state.room_hold.size_gib == 80.0
        await world.at(T + 1)
        assert world.swap.loads() == []
        [(_, fields)] = world.emitted.of("resident_waiting")
        assert fields["after"] == "brake"
        # Nothing held: a release changes nothing and says so.
        assert await world.room.release(DAN, room=False, brake=True) == ReleaseResult(None, False, [])
