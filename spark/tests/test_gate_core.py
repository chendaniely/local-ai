"""The gate's core (website/design/phase-2a.md, Task 18): its loops, the activity record, apply's hold and the bypass
check. It wires Tasks 13 to 17 together, and the rules they keep hold only if it wires them right: rule 9 counts every
model llama-swap runs, one load at a time, the sent-unload rule, rule 5's release bounds, and no request cut off.

The clock is injected and the loops aren't started: each test calls the core's steps as its loops would (`read_memory`
every 250 ms, `read_running` and `each_second` every second, `each_five_s` every 5 s). llama-swap is an in-process
stand-in on that clock (Swap), but for the residents' return after a restart, which runs on the v257 stand-in restarted
on its port (the deferred note at Task 12's review). The front is the gate's side of its events call, answering every
drain at once unless a test holds it; the units, nvidia-smi and /proc are stand-ins too. Every time, boot id, uid, pid
and key here is a stand-in; none was read from the box."""

import asyncio
import json
import logging
import os
import socket
import threading
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio
import pytest

from fake_llamaswap import FakeLlamaSwap, serve_fake
from spark import brakeevents, gateproto, messages, procs, tickets
from spark.brakeevents import BrakeEvent, append_event
from spark.gate.admission import AdmitRequest, Admitted
from spark.gate.core import ACTIVITY_FILE, FOUND_AFTER_DAMAGE, GateCore, engine_ports
from spark.gate.state import (STATE_FILE, ApplyHold, GateState, ModelRecord, Pin, RefusalRecord, RecentRecord,
                              RoomHold, Ticketed, load_state, save_state)
from spark.gate.units import UNITS, UnitState, UnitStateError
from spark.gateproto import APPLY_LAPSE_S, APPLY_RENEW_S
from spark.hold import FILE as HOLD_FILE
from spark.hold import Hold, read_hold, write_hold
from spark.llamaswap import Running
from spark.llamaswap_async import AsyncLlamaSwap, LlamaSwapNotSent, LoadOutcome
from spark.memory import MemInfo
from spark.registry import ClientKey, load_registry
from spark.render import HF_HOME, model_path
from test_procs import gib, process, start_of

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
REGISTRY = load_registry(STACK_REGISTRY)
TZ = "PDT+7"  # the tests' own zone, seven hours behind UTC, as test_messages.py has it
ZONE = timezone(timedelta(hours=-7))
BOUND_S = 5  # every async test's bound
BOOT = "b1"
IDLE = 117.0  # MemAvailable with nothing loaded: the registry's idle figure
KEY = "INTERNAL-KEY-STANDIN"  # stands for the gate's internal key
SPARK, DAN, AGENT = 2001, 1000, 1001  # stand-in uids
USERS = {SPARK: "spark", DAN: "chendaniely", AGENT: "agent"}
KEYS = {"dan-mac": ClientKey("dan-mac", "dan", "pi on the Mac", "chendaniely"),
        "agent": ClientKey("agent", "agent", "agent", "agent")}
LOGS = ("spark.gate.core", "spark.gate.room", "spark.gate.policy", "spark.gate.admission", "spark.gate.notify")


def at(hour: int, minute: int, second: float = 0) -> float:
    return datetime(2026, 10, 8, hour, minute, tzinfo=ZONE).timestamp() + second


T = at(9, 0)


def name_of(label: str) -> str:
    return next(name for name, model in REGISTRY.models.items() if model.label == label)


CODER, GEMMA, EMBED, WHISPER = name_of("the coder"), name_of("Gemma"), name_of("the embeddings"), name_of("whisper")
RESIDENTS = [GEMMA, EMBED, WHISPER]
FOOTPRINT = {name: model.footprint_gib for name, model in REGISTRY.models.items()}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def local_zone(monkeypatch):
    """The clock read in ZONE, on a box called brightroar, as the words read them; each uid by its stand-in name."""
    monkeypatch.setenv("TZ", TZ)
    time.tzset()
    monkeypatch.setattr(socket, "gethostname", lambda: "brightroar.local")
    monkeypatch.setattr(procs, "_user", lambda uid: USERS.get(uid, str(uid)))
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.fixture(autouse=True)
def no_hidden_errors(caplog):
    """Nothing may fail unseen: the gate logs what it couldn't do at ERROR and carries on, as it must. A test that
    makes something fail on purpose checks the line, then clears the log."""
    for name in LOGS:
        caplog.set_level(logging.INFO, logger=name)
    yield
    assert [r.getMessage() for r in caplog.records if r.name in LOGS and r.levelno >= logging.ERROR] == []


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Swap:
    """llama-swap v257 as the gate sees it through AsyncLlamaSwap, in process and on the tests' clock. `/running` lists
    every model that isn't stopped (internal/router/base.go:366-376), as it stands when the call is made. A load runs
    `spark launch`'s part (its ticket claimed, its start recorded under `started/`), then ends ready at once; with
    `hold_loads`, it waits for `ready` before anything shows. An unload stops the model, or, for a model in
    `slow_stops`, leaves it stopping until the test says. `down` makes every call fail unanswered; `pause_next` holds
    the next `/running` answer, made as the call came, until `release`."""

    def __init__(self, clock: Clock, launch: Path):
        self.clock, self.launch = clock, launch
        self.states: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.down = False
        self.hold_loads = False
        self.slow_stops: set[str] = set()
        self.pause_next = False
        self.reads = 0  # /running calls, answered or not
        self._paused: asyncio.Future | None = None
        self._loads: dict[str, asyncio.Future] = {}

    def loads(self) -> list[str]:
        return [model for kind, model in self.calls if kind == "load"]

    def unloads(self) -> list[str]:
        return [model for kind, model in self.calls if kind == "unload"]

    async def running(self) -> list[Running]:
        self.reads += 1
        if self.down:
            raise LlamaSwapNotSent("llama-swap unreachable at the stand-in: ConnectError")
        listed = [Running(model, state) for model, state in sorted(self.states.items()) if state != "stopped"]
        if self.pause_next:
            self.pause_next = False
            self._paused = asyncio.get_running_loop().create_future()
            await self._paused
        return listed

    def release(self) -> None:
        self._paused.set_result(None)

    async def load(self, model: str) -> LoadOutcome:
        if self.down:
            return LoadOutcome.unknown("refused")
        self.calls.append(("load", model))
        if self.hold_loads:
            call = asyncio.get_running_loop().create_future()
            self._loads[model] = call
            return await call
        self._launched(model)
        self.states[model] = "ready"
        return LoadOutcome.READY

    def _launched(self, model: str) -> None:
        claim = tickets.claim(self.launch, model, self.clock(), boot_id=BOOT)
        assert claim.ok, claim
        tickets.mark_started(self.launch, claim.ticket, self.clock())

    def ready(self, model: str) -> None:
        self._launched(model)
        self.states[model] = "ready"
        self._loads.pop(model).set_result(LoadOutcome.READY)

    async def unload(self, model: str) -> None:
        if self.down:
            raise LlamaSwapNotSent("llama-swap unreachable at the stand-in: ConnectError")
        self.calls.append(("unload", model))
        if model in self.slow_stops:
            self.states[model] = "stopping"
            return
        self.states[model] = "stopped"

    async def last_lines(self, model: str, n: int = 20, read_s: float = 1.0) -> list[str]:
        return []


class Units:
    """`systemctl show` for the four units, as units.unit_state reads it: each active since an hour before the tests'
    start, unless the test starts one anew or breaks its reading."""

    def __init__(self):
        self.states = {unit: UnitState(True, 0, T - 3600, None, "success") for unit in UNITS}
        self.broken: set[str] = set()
        self.reads: list[str] = []
        self.block: threading.Event | None = None  # llama-swap's reading waits for it, in its thread

    def __call__(self, unit: str) -> UnitState:
        self.reads.append(unit)
        if unit == "llama-swap" and self.block is not None:
            assert self.block.wait(BOUND_S)
        if unit in self.broken:
            raise UnitStateError(f"systemctl show {UNITS[unit]} didn't answer (TimeoutExpired)")
        return self.states[unit]

    def started(self, unit: str, when: float, *, crash: bool = False) -> None:
        """The unit started anew at `when`: by hand (apply's restart), or by systemd after a crash."""
        st = self.states[unit]
        self.states[unit] = UnitState(True, st.n_restarts + 1 if crash else 0, when, when - 1, "success")


class Published:
    """ntfy as the Notifier's publish sees it: each Notification taken."""

    def __init__(self):
        self.sent: list = []

    async def __call__(self, n) -> None:
        self.sent.append(n)

    def of(self, kind: str) -> list[str]:
        return [n.message for n in self.sent if n.type == kind]


def snapshot(*, draining=(), **inflight: dict) -> dict:
    """The front's whole in-flight report: each model given its counts, nothing in flight elsewhere."""
    models = {name: {"count": 0, "oldest_started_at": None, "last_end_at": None, "requests": []} | counts
              for name, counts in inflight.items()}
    return {"seq": 1, "front_started_at": T - 86400, "models": models, "draining": list(draining)}


class Front:
    """The front's side of its events call, as Task 22's front speaks it: what the gate sent, and, with `auto`, its
    `drained` for every drain at once, adopting a drain whose id it doesn't hold, as a front with nothing in flight
    does."""

    def __init__(self, core: GateCore, *, auto: bool = True):
        self.core, self.auto = core, auto
        self.seen: list[dict] = []
        self.queue = core.open_front_events()
        self._task = asyncio.get_running_loop().create_task(self._serve())

    async def _serve(self) -> None:
        while True:
            event = await self.queue.get()
            self.seen.append(event)
            if self.auto and event["op"] == "drain":
                self.core.front.drained(event["model"], event["drain_id"])

    def ops(self, op: str) -> list[dict]:
        return [event for event in self.seen if event["op"] == op]

    def about(self, model: str) -> list[tuple[str, str | None]]:
        return [(event["op"], event.get("drain_id")) for event in self.seen if event.get("model") == model]

    def report(self, *, draining=(), **inflight: dict) -> None:
        self.core.inflight(snapshot(draining=draining, **inflight))


async def settle(rounds: int = 300) -> None:
    """Let every task that can move, move."""
    for _ in range(rounds):
        await asyncio.sleep(0)


def record(name: str, *, state: str = "ready", last_use: float = T - 60, **more) -> ModelRecord:
    """A model loaded before the test starts, its load having taken its whole footprint, so it is owed nothing."""
    return ModelRecord(name=name, footprint_gib=FOOTPRINT[name], loaded_at=T - 3600, load_fall_gib=FOOTPRINT[name],
                       fall_flagged=False, rss_anon_at_load_gib=None, last_use=last_use, state=state, **more)


def loaded(*names: str, **more) -> GateState:
    return GateState(boot_id=BOOT, models={name: record(name) for name in names}, **more)


class Gate:
    """Everything around the core that the test builds: the clock, the registry's file (a copy the test may change),
    the gate's state folder (a saved state, or a damaged file), the brake's folder, launch's folder, a Hugging Face
    cache with every model's files, /proc, the stand-in llama-swap (`shown`), the units, ntfy, nvidia-smi and
    MemAvailable (what the engines hold, and `other`)."""

    def __init__(self, tmp_path: Path, *, state: GateState | None = None, shown: dict[str, str] | None = None,
                 damaged: bool = False, other: float = 0.0):
        self.tmp = tmp_path
        self.clock = Clock(T)
        self.registry_path = tmp_path / "models.yaml"
        self.registry_path.write_text(STACK_REGISTRY.read_text())
        self.state_dir = tmp_path / "gate"
        self.state_dir.mkdir()
        self.brake = tmp_path / "brake"
        self.brake.mkdir()
        self.launch = tmp_path / "launch"
        self.launch.mkdir()
        self.hf_home = tmp_path / "hf"
        for model in REGISTRY.models.values():
            for file in filter(None, (model.source.file, model.source.mmproj)):
                path = self.hf_home / Path(model_path(model.source, file)).relative_to(HF_HOME)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"gguf")
        self.proc = tmp_path / "proc"
        self.proc.mkdir()
        self.swap = Swap(self.clock, self.launch)
        self.swap.states.update(shown or {})
        self.units = Units()
        self.published = Published()
        self.other = other
        self.available: float | None = None
        self.nvidia = lambda: ""
        if state is not None:
            save_state(self.state_dir, state, now=T - 10)
        if damaged:
            (self.state_dir / STATE_FILE).write_text("{not the gate's state")

    def mem(self) -> MemInfo:
        if self.available is not None:
            return MemInfo(121.6, self.available)
        held = sum(FOOTPRINT.get(name, 0.0) for name, state in self.swap.states.items()
                   if state in ("ready", "stopping"))
        return MemInfo(121.6, IDLE - held - self.other)

    def core(self, **overrides) -> GateCore:
        given = dict(registry_path=self.registry_path, llamaswap=self.swap, publish=self.published,
                     brake_dir=self.brake, launch=self.launch, hf_home=str(self.hf_home), keys=KEYS, spark_uid=SPARK,
                     dan_uids={DAN}, may_pin=lambda uid: uid in (DAN, 0), may_hold=lambda uid: uid != SPARK,
                     clock=self.clock, read_meminfo=self.mem, boot_id=BOOT, boot_time=T - 86400,
                     read_unit=self.units, nvidia_smi=lambda: self.nvidia(), proc=self.proc, host="brightroar",
                     poll_s=60.0, sleep=lambda seconds: asyncio.sleep(0.001))
        return GateCore(load_registry(self.registry_path), self.state_dir, **(given | overrides))

    async def started(self, **overrides) -> GateCore:
        core = self.core(**overrides)
        await core.start(loops=False)
        await settle()
        return core

    def saved(self) -> GateState:
        state, problem = load_state(self.state_dir)
        assert problem is None, problem
        return state

    def activity(self) -> dict:
        return json.loads((self.state_dir / ACTIVITY_FILE).read_text())

    def relabel(self, old: str, new: str) -> None:
        text = self.registry_path.read_text()
        assert f"label: {old}\n" in text
        self.registry_path.write_text(text.replace(f"label: {old}\n", f"label: {new}\n"))


def task(coro) -> asyncio.Task:
    return asyncio.get_running_loop().create_task(coro)


async def until(check, *, core: GateCore | None = None, rounds: int = 200) -> None:
    """Until `check()` holds: each round lets the tasks move, the core reading /running when given."""
    for _ in range(rounds):
        if check():
            return
        if core is not None:
            await core.read_running()
        await settle(50)
    assert check()


async def published(gate: Gate, core: GateCore, kind: str, count: int = 1) -> list[str]:
    """The notifications of `kind` ntfy has taken, once there are `count` of them (the Notifier runs in a task)."""
    for _ in range(500):
        if len(gate.published.of(kind)) >= count:
            break
        await asyncio.sleep(0.002)
    return gate.published.of(kind)


def admit(core: GateCore, rid: str, model: str, deadline: float, *, key: str | None = None) -> asyncio.Task:
    """A request for `model` through admission: a key's, through the front, or one of Dan's commands."""
    req = (AdmitRequest(rid, model, "key", KEYS[key], deadline) if key is not None
           else AdmitRequest(rid, model, "command", None, deadline, uid=DAN))
    return task(core.admitter.admit(req))


# The activity record (Task 23's brake reads it).

@pytest.mark.anyio
async def test_the_activity_record_is_written_every_second(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, CODER), shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report(**{GEMMA: {"count": 2, "oldest_started_at": T - 5,
                                "requests": [{"key": "dan-mac", "started_at": T - 5}] * 2},
                        CODER: {"last_end_at": T + 2}})
        for tick in (1, 2, 3):
            gate.clock.now = T + tick
            core.each_second()
            assert gate.activity()["written_at"] == T + tick
        # restore dates each model's last use at the gate's start, T; the front's last end of the coder's, T + 2, is
        # later.
        assert gate.activity() == {
            "written_at": T + 3, "boot_id": BOOT,
            "models": {GEMMA: {"state": "ready", "inflight": 2, "last_use": T},
                       EMBED: {"state": "ready", "inflight": 0, "last_use": T},
                       WHISPER: {"state": "ready", "inflight": 0, "last_use": T},
                       CODER: {"state": "ready", "inflight": 0, "last_use": T + 2}}}
        await core.stop()


@pytest.mark.anyio
async def test_a_model_is_starting_in_the_record_from_its_ticket(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.swap.hold_loads = True
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        asked = admit(core, "r1", CODER, T + 200)
        await settle()
        assert CODER not in {entry.model for entry in await gate.swap.running()}  # /running doesn't show it yet
        gate.clock.now = T + 1
        await core.read_running()  # a read that doesn't list it leaves the admitter's load alone: it is counted
        assert core.state.models[CODER].state == "starting"
        core.each_second()
        # Its ticket's figures: the footprint rule 9 admitted, its issue, MemAvailable then (117 less the residents').
        assert gate.activity()["models"][CODER] == {"state": "starting", "inflight": 0, "last_use": T,
                                                    "admitted_gib": FOOTPRINT[CODER], "started_at": T,
                                                    "available_at_start": IDLE - 43}
        gate.swap.ready(CODER)
        assert await asked == Admitted(CODER, True, 1.0)
        gate.clock.now = T + 2
        core.each_second()
        assert gate.activity()["models"][CODER] == {"state": "ready", "inflight": 0, "last_use": T + 1}
        await core.stop()


# /v1/quiet (make apply's wait for quiet).

@pytest.mark.anyio
async def test_quiet_reports_whats_in_flight_and_who_asked(tmp_path):
    gate = Gate(tmp_path, state=loaded(GEMMA, CODER), shown={GEMMA: "ready", CODER: "ready"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report(**{CODER: {"count": 1, "oldest_started_at": T - 20,
                                "requests": [{"key": "agent", "started_at": T - 20}]}})
        # Task 32's review (C1): and whether the front holds, its call is open, and when its snapshot was made.
        fresh = {"front_connected": True, "holding": False, "snapshot_at": None}
        assert core.quiet() == {"quiet_for_s": 0.0, "last_label": None, "inflight": [
            {"model": CODER, "model_label": "the coder", "key_label": "agent", "age_s": 20.0}]} | fresh
        front.report(**{CODER: {"last_end_at": T - 45}, GEMMA: {"last_end_at": T - 300}})
        assert core.quiet() == {"quiet_for_s": 45.0, "last_label": "the coder", "inflight": []} | fresh
        await core.stop()


@pytest.mark.anyio
async def test_quiet_passes_on_whether_the_front_holds_and_is_connected(tmp_path):
    # Task 32's review (C1): make apply counts "drained" only from a reading the front made while holding new
    # requests, its call to the gate open, and made after apply's drain-all.
    gate = Gate(tmp_path, state=loaded(GEMMA), shown={GEMMA: "ready"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        assert core.quiet() | {"quiet_for_s": 0.0} == {"quiet_for_s": 0.0, "last_label": None, "inflight": [],
                                                      "front_connected": False, "holding": False, "snapshot_at": None}
        front = Front(core)
        core.inflight(snapshot(**{GEMMA: {"last_end_at": T - 10}}) | {"holding": True, "at": T - 1})
        reading = core.quiet()
        assert (reading["front_connected"], reading["holding"], reading["snapshot_at"]) == (True, True, T - 1)
        core.inflight(snapshot(**{GEMMA: {"last_end_at": T - 10}}) | {"holding": False, "at": T})
        assert core.quiet()["holding"] is False
        core.inflight(snapshot(**{GEMMA: {"last_end_at": T - 10}}) | {"holding": True, "at": T})
        core.front.close(front.queue)  # the front's call drops: its last snapshot stays, and holds nothing
        reading = core.quiet()
        assert (reading["front_connected"], reading["holding"], reading["snapshot_at"]) == (False, False, T)
        await core.stop()


@pytest.mark.anyio
async def test_a_reopened_call_holds_nothing_until_the_front_reports_on_it(tmp_path):
    # Task 32's re-review (N1): the gate keeps the last snapshot across a dropped call. When the call opens again, that
    # snapshot, idle and holding, describes the call before: the front may have dropped its hold meanwhile (the gate
    # gone longer than its lapse) and let requests through that no snapshot here shows. Until the front reports on the
    # new call, /v1/quiet says it holds nothing, so make apply's `drained` waits.
    from spark.apply import drained

    gate = Gate(tmp_path, state=loaded(GEMMA), shown={GEMMA: "ready"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        held = snapshot(**{GEMMA: {"last_end_at": T - 10}}) | {"holding": True, "at": T}
        core.inflight(held)
        assert core.quiet()["holding"] is True and drained(core.quiet(), after=T)
        core.front.close(front.queue)
        again = Front(core)  # the call opens again; the old snapshot is still the gate's latest
        reading = core.quiet()
        assert (reading["front_connected"], reading["holding"]) == (True, False)
        assert not drained(reading, after=T)  # apply's read before the first post on the new call: nothing restarts
        core.inflight(held | {"seq": 2})  # the front's first post on the new call
        assert core.quiet()["holding"] is True and drained(core.quiet(), after=T)
        core.front.close(again.queue)
        core.inflight(held | {"seq": 3})  # a report with no call open, as one handled after the call dropped
        Front(core)
        assert core.quiet()["holding"] is False
        await core.stop()


# Apply's hold: drain-all, begin, renew and its one end.

@pytest.mark.anyio
async def test_apply_begin_and_end_reread_the_registry_and_reload_the_residents(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, CODER), shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    gate.swap.hold_loads = True
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        assert core.drain_all(DAN) == {"since": T, "at": T}  # Task 32's review: and when the gate took it
        assert core.apply_begin(["llama-swap"]) == {"ok": True}
        await settle()
        assert core.hello()["applying"] is True and front.ops("hold_all") == [{"op": "hold_all", "lapse_s": 60}]
        assert gate.saved().applying == ApplyHold(since=T, by_uid=DAN, renewed_at=T, begun_at=T,
                                                  restarting=["llama-swap"], ended=False)
        # make apply restarts llama-swap: every engine gone, its unit started anew; the registry gained a label.
        gate.swap.states.clear()
        gate.units.started("llama-swap", T + 3)
        gate.relabel("Gemma", "Gemma 4")
        gate.clock.now = T + 6
        await core.read_running()
        assert core.state.models == {} and gate.swap.loads() == []  # nothing reloads while the hold stands
        # Admission holds: a load waits, and gets `restarting` at its deadline.
        held = admit(core, "r1", CODER, T + 7, key="dan-mac")
        await settle()
        gate.clock.now = T + 7
        core.admitter.changed()
        assert (await held).code == "restarting"
        await core.apply_end()
        await settle()
        # The registry read again from disk, sent to the front with release_all, and the residents queued.
        assert core.registry.models[GEMMA].label == "Gemma 4" and core.room.registry is core.registry
        assert front.ops("release_all") == [{"op": "release_all"}]
        assert {m["name"]: m["label"] for m in front.ops("state")[-1]["models"]}[GEMMA] == "Gemma 4"
        assert gate.saved().applying.ended is True
        # apply_restarted says the residents reloaded: it waits until they have.
        for name in RESIDENTS:
            await until(lambda name=name: name in gate.swap._loads)
            assert gate.published.of("apply_restarted") == []
            gate.swap.ready(name)
            await settle()
        await core.read_running()
        words = messages.notification("apply_restarted", core.registry, at=T + 3,
                                      reloaded=["Gemma 4", "the embeddings", "whisper"],
                                      on_demand=["the coder"]).message
        assert await published(gate, core, "apply_restarted") == [words]
        assert gate.swap.loads() == RESIDENTS
        await core.stop()


@pytest.mark.anyio
async def test_the_apply_hold_ends_once_llama_swap_answers_again(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        core.drain_all(DAN)
        core.apply_begin(["llama-swap"])
        gate.swap.states.clear()
        gate.units.started("llama-swap", T + 15)
        gate.clock.now = T + 20
        await core.each_five_s()  # the unit watch sees the start; /running hasn't answered since it
        assert core.state.applying.ended is False
        await core.read_running()  # /running answers, with every engine gone: the one end, with no apply/end
        assert core.state.applying.ended is True
        await until(lambda: len(gate.swap.loads()) == 3, core=core)
        assert gate.swap.loads() == RESIDENTS and len(front.ops("release_all")) == 1
        assert [r.text for r in core.state.recent if r.kind == "apply"] == [
            messages.recent_apply_ended("answered", APPLY_LAPSE_S)]
        await settle()
        seen = len(front.seen)
        await core.apply_end()  # the later apply/end: nothing
        await settle()
        assert front.seen[seen:] == [] and gate.swap.loads() == RESIDENTS
        assert len([r for r in core.state.recent if r.kind == "apply"]) == 1
        await core.stop()


@pytest.mark.anyio
async def test_drain_all_holds_and_unloads_nothing(tmp_path):
    # The coder idle for two hours; whisper not loaded (Dan unloaded it, so nothing reloads it by itself).
    state = loaded(GEMMA, EMBED, CODER, dans_unloads={WHISPER: BOOT})
    gate = Gate(tmp_path, state=state, shown={GEMMA: "ready", EMBED: "ready", CODER: "ready"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report(**{CODER: {"last_end_at": T - 7200}})
        base = T + 7200
        gate.clock.now = base
        since = core.drain_all(DAN)["since"]
        assert gate.saved().applying.since == since  # on disk before it answered
        await settle()
        assert front.ops("hold_all") == [{"op": "hold_all", "lapse_s": APPLY_LAPSE_S}]
        held = admit(core, "r1", WHISPER, base + 30, key="dan-mac")
        for t in range(5, 31, 5):  # renewed, so it stands; the idle check finds the coder idle, and leaves it
            gate.clock.now = base + t
            assert core.apply_renew(since, DAN) == {"ok": True}
            core.each_second()
            await core.read_running()  # llama-swap answers: only the hold keeps the idle check from unloading
            await core.each_five_s()
        core.admitter.changed()
        assert (await held).code == "restarting"
        await settle()
        assert gate.swap.unloads() == [] and front.ops("drain") == [] and gate.swap.loads() == []
        assert CODER in core.state.models
        await core.apply_end()
        await settle()
        assert front.ops("release_all") == [{"op": "release_all"}]
        assert (await admit(core, "r2", WHISPER, gate.clock() + 30, key="dan-mac")).model == WHISPER
        assert gate.swap.loads() == [WHISPER]
        await core.stop()


@pytest.mark.anyio
async def test_a_drain_all_that_cant_be_saved_holds_nothing_and_says_so(tmp_path, monkeypatch):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        import spark.gate.core as core_module

        def full(*args, **kwargs):
            raise OSError(28, "No space left on device")

        real = core_module.save_state
        monkeypatch.setattr(core_module, "save_state", full)
        with pytest.raises(OSError):
            core.drain_all(DAN)  # make apply stops, and writes nothing
        assert core.state.applying is None and core.hello()["applying"] is False
        await settle()
        assert front.ops("hold_all") == []
        assert any("couldn't be saved" in p for p in core.status_view()["health"]["read_problems"])
        monkeypatch.setattr(core_module, "save_state", real)
        await core.stop()


@pytest.mark.parametrize("end", ["apply/end", "undrain-all", "automatic", "lapse"])
@pytest.mark.anyio
async def test_every_end_does_the_same_work_once(tmp_path, end):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        gate.swap.hold_loads = True
        front = Front(core)
        core.drain_all(DAN)
        core.apply_begin(["llama-swap"])
        # make apply restarts llama-swap: Gemma, with the others, isn't loaded; Dan's request for the coder is held.
        gate.swap.states.clear()
        gate.units.started("llama-swap", T + 2)
        gate.relabel("the embeddings", "the embedder")
        gate.clock.now = T + 3
        await core.read_running()
        coder = admit(core, "r1", CODER, T + 200)
        await settle()
        assert gate.swap.loads() == [] and core.state.applying.ended is False
        if end == "apply/end":
            await core.apply_end()
        elif end == "undrain-all":
            await core.undrain_all()
        elif end == "automatic":
            await core.each_five_s()  # the unit watch sees llama-swap started after begin, and /running answered
        else:
            gate.clock.now = T + APPLY_LAPSE_S
            core.each_second()
        await settle()
        assert core.state.applying.ended is True and core.registry.models[EMBED].label == "the embedder"
        assert front.ops("release_all") == [{"op": "release_all"}]
        assert gate.swap.loads() == [GEMMA]  # Gemma's reload takes the slot before the coder's
        for name in RESIDENTS:
            await until(lambda name=name: name in gate.swap._loads)
            gate.swap.ready(name)
        await until(lambda: CODER in gate.swap._loads)
        gate.swap.ready(CODER)
        assert (await coder).model == CODER
        assert gate.swap.loads() == [*RESIDENTS, CODER]
        seen, calls = len(front.seen), list(gate.swap.calls)
        await core.apply_end()  # a second end: nothing sent, nothing queued
        await core.undrain_all()
        gate.clock.now = T + 2 * APPLY_LAPSE_S
        core.each_second()
        await core.each_five_s()
        await settle()
        assert [e for e in front.seen[seen:] if e["op"] != "state"] == [] and gate.swap.calls == calls
        assert len([r for r in core.state.recent if r.kind == "apply"]) == 1
        await core.stop()


@pytest.mark.anyio
async def test_a_hold_whose_renewal_lapses_ends_before_begin_too(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        since = core.drain_all(DAN)["since"]
        for t in range(APPLY_RENEW_S, 601, APPLY_RENEW_S):  # renewed every 15 s for 10 minutes: it stands
            gate.clock.now = T + t - 0.5
            core.each_second()
            gate.clock.now = T + t
            assert core.apply_renew(since, DAN) == {"ok": True}
            core.each_second()
            assert core.state.applying.ended is False
        gate.clock.now = T + 600 + APPLY_LAPSE_S - 0.5
        core.each_second()
        assert core.state.applying.ended is False
        gate.clock.now = T + 600 + APPLY_LAPSE_S  # 60 s with no renewal, and no begin: it ends
        core.each_second()
        assert core.state.applying.ended is True and gate.saved().applying.ended is True
        assert [r.text for r in core.state.recent if r.kind == "apply"] == [
            messages.recent_apply_ended("lapsed", APPLY_LAPSE_S)]
        assert core.apply_renew(since, DAN) == {"ended": True}  # a renewal never starts it again
        assert core.state.applying.ended is True and core.hello()["applying"] is False
        await core.stop()


@pytest.mark.anyio
async def test_a_renewal_of_an_unknown_apply_hold_recreates_it(tmp_path):
    # A start fresh from a damaged state file lost apply's hold: the renewal re-creates it, `since` its own.
    gate = Gate(tmp_path, damaged=True, shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        assert core.state.applying is None
        # Task 32's review (M7): the answer says the hold was re-created, so apply can tell it from one that stood: this
        # gate's hello released the front's held requests meanwhile.
        assert core.apply_renew(T - 30, DAN) == {"ok": True, "recreated": True}
        assert core.apply_renew(T - 30, DAN) == {"ok": True}  # the next renewal, of the hold it now knows
        assert core.state.applying == ApplyHold(since=T - 30, by_uid=DAN, renewed_at=T, begun_at=None, restarting=[],
                                                ended=False)
        assert gate.saved().applying == core.state.applying and core.hello()["applying"] is True
        await settle()
        assert front.ops("hold_all") == [{"op": "hold_all", "lapse_s": APPLY_LAPSE_S}]
        held = admit(core, "r1", CODER, T + 1, key="dan-mac")
        gate.clock.now = T + 1
        core.admitter.changed()
        assert (await held).code == "restarting"
        await core.undrain_all()
        assert core.apply_renew(T - 30, DAN) == {"ended": True}
        await core.stop()


@pytest.mark.anyio
async def test_a_gate_restarted_mid_apply_keeps_holding(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, CODER), shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    with anyio.fail_after(BOUND_S):
        first = await gate.started()
        first.drain_all(DAN)
        first.apply_begin(["llama-swap"])
        await first.stop()
        # llama-swap restarts while the gate is away.
        gate.swap.states.clear()
        gate.units.started("llama-swap", T + 20)
        gate.clock.now = T + 30
        core = await gate.started()
        assert core.hello()["applying"] is True
        front = Front(core)
        await settle()
        assert front.seen[0]["op"] == "hello" and front.seen[0]["applying"] is True
        refused = admit(core, "r1", CODER, T + 40, key="dan-mac")  # the coder's reload, asked for after the restart
        waiting = admit(core, "r2", CODER, T + 200, key="dan-mac")
        gate.clock.now = T + 40
        core.admitter.changed()
        assert (await refused).code == "restarting"
        assert gate.swap.loads() == []
        await core.apply_end()
        await settle()
        assert front.ops("release_all") == [{"op": "release_all"}]
        assert (await waiting).model == CODER
        assert gate.swap.loads()[:3] == RESIDENTS and gate.swap.loads()[-1] == CODER
        await core.stop()


@pytest.mark.anyio
async def test_an_announced_start_reloads_with_apply_even_after_the_hold_lapsed(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        core.drain_all(DAN)
        core.apply_begin(["llama-swap"])
        gate.clock.now = T + APPLY_LAPSE_S
        core.each_second()  # make apply died: the hold lapsed before llama-swap's restart
        assert core.state.applying.ended is True
        gate.swap.states.clear()
        gate.units.started("llama-swap", T + 70)
        gate.clock.now = T + 75
        await core.read_running()
        await until(lambda: all(name in core.state.models for name in RESIDENTS), core=core)
        assert gate.swap.loads() == RESIDENTS
        assert core.state.resident_reloads == {}  # apply's reloads count in no hourly bound
        assert len(await published(gate, core, "apply_restarted")) == 1
        # A later start, unannounced, is llama-swap's own restart: its reloads count in the bound.
        gate.swap.states.clear()
        gate.units.started("llama-swap", T + 200, crash=True)
        gate.clock.now = T + 205
        await core.read_running()
        await until(lambda: all(name in core.state.models for name in RESIDENTS), core=core)
        assert core.state.resident_reloads == dict.fromkeys(RESIDENTS, T + 205)
        await core.stop()


# The residents' return (Dan's decision, 2026-10-07).

@pytest.mark.anyio
async def test_the_residents_come_back_after_llama_swap_restarts_unplanned(tmp_path):
    fake = FakeLlamaSwap({KEY}, list(REGISTRY.models))
    for name in RESIDENTS:
        fake.set_state(name, "ready")
    gate = Gate(tmp_path, state=loaded(*RESIDENTS))
    gate.available = IDLE
    with anyio.fail_after(BOUND_S):
        async with serve_fake(fake) as url, AsyncLlamaSwap(url, KEY, load_timeout_s=30) as swap:
            core = await gate.started(llamaswap=swap)
            await fake.restart(0.05)  # every engine stopped, as v257 starts
            gate.units.started("llama-swap", T + 5, crash=True)
            gate.clock.now = T + 10
            await core.read_running()
            for _ in range(200):
                if all(core.state.models.get(name) is not None and core.state.models[name].state == "ready"
                       for name in RESIDENTS):
                    break
                await asyncio.sleep(0.01)
            loads = [seen.path for seen in fake.requests if seen.path.endswith("/health")]
            assert loads == [f"/upstream/{name}/health" for name in RESIDENTS]
            assert core.state.resident_reloads == dict.fromkeys(RESIDENTS, T + 10)  # the restart's, in the bound
            await core.stop()


@pytest.mark.anyio
async def test_a_resident_that_crashed_comes_back_but_not_one_the_gate_unloaded(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report()
        # Whisper crashes (an earlyoom kill): the residents' rule reloads it, once in the hour.
        gate.swap.states.pop(WHISPER)
        gate.clock.now = T + 5
        await core.read_running()
        await until(lambda: WHISPER in core.state.models, core=core)
        assert gate.swap.loads() == [WHISPER] and core.state.resident_reloads == {WHISPER: T + 5}
        # make-room's unload of the embeddings, finished while a /running read that still listed it was in flight:
        # that read was sent before the unload, so its leaving is the gate's own (`since` is when it was sent),
        # never a crash for the residents' rule.
        gate.clock.now = T + 10
        gate.swap.pause_next = True
        reading = task(core.read_running())
        await settle()
        gate.clock.now = T + 11
        assert await core.drainer.drain(EMBED, "make-room") is True
        gate.clock.now = T + 12
        gate.swap.release()
        await reading
        assert EMBED not in core.state.models  # the stale read left the drainer's work alone
        gate.clock.now = T + 13
        await core.read_running()
        await settle()
        assert EMBED not in core.state.models and gate.swap.loads() == [WHISPER]
        await core.stop()


# Start-up: the state, the hold, the Admitter, the drains and the front.

NEW = "a new id"  # a drain the gate finishes under an id of its own, as the front adopts it (Task 22)


@pytest.mark.parametrize("saved, shown, requested, heard", [
    ("draining", "ready", None, [("undrain", "d1")]),  # drained, its unload not yet requested: the front serves it
    ("draining", "ready", T - 3, [("drain", "d1"), ("unloaded", None)]),  # drained again under its id, then unloaded
    ("draining", "stopping", T - 3, [("unloaded", None)]),  # its unload under way: unloaded once /running shows it gone
    ("draining", "stopping", None, [("unloaded", None)]),
    ("draining", None, None, [("undrain", "d1")]),  # gone from /running: the front holds it no longer
    ("draining", None, T - 3, [("undrain", "d1")]),
    # Task 15's abort of a start past its deadline, saved with no drain id: ready by now, it is drained under a new
    # id, why `unknown`, and answered; still starting, it is unloaded without the front (Task 16's rule).
    ("late_start", "ready", T - 3, [("drain", NEW), ("unloaded", None)]),
    ("late_start", "starting", T - 3, [("unloaded", None)]),
])
@pytest.mark.anyio
async def test_a_restart_resumes_or_undrains_every_saved_drain(tmp_path, saved, shown, requested, heard):
    state = loaded(*RESIDENTS, undrains={GEMMA: "d0"})
    if saved == "late_start":
        state.models[CODER] = record(CODER, state="starting", unload_requested_at=requested, drain_why="late_start")
    else:
        state.models[CODER] = record(CODER, state="draining", unload_requested_at=requested, drain_id="d1",
                                     drain_why="idle")
    shows = dict.fromkeys(RESIDENTS, "ready") | ({} if shown is None else {CODER: shown})
    gate = Gate(tmp_path, state=state, shown=shows)
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        # The front restarted too, holding nothing: it adopts each drain it hears, and answers it.
        front = Front(core)
        await settle()
        assert front.about(GEMMA) == [("undrain", "d0")]  # owed, and sent as the call opens, before any reading
        gate.swap.states[CODER] = "stopped"  # whatever was stopping stops
        await until(lambda: CODER not in core.state.models, core=core)
        await settle()
        drains = front.ops("drain")
        new_ids = {d["drain_id"] for d in drains if d["drain_id"] != "d1"}
        assert all(new_ids) and [d["why"] for d in drains if d["drain_id"] in new_ids] == ["unknown"] * len(new_ids)
        assert [(op, NEW if drain_id in new_ids else drain_id) for op, drain_id in front.about(CODER)] == heard
        assert core.state.undrains == {} and gate.saved().undrains == {}
        await core.stop()


@pytest.mark.anyio
async def test_a_model_restored_with_its_unload_requested_is_drained_then_unloaded(tmp_path):
    state = loaded(*RESIDENTS)
    state.models[CODER] = record(CODER, state="draining", unload_requested_at=T - 3, drain_id="d1",
                                 drain_why="make-room")
    gate = Gate(tmp_path, state=state, shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core, auto=False)  # its request in flight still runs
        await settle()
        assert front.about(CODER) == [("drain", "d1")]
        assert core.state.models[CODER].state == "draining" and gate.saved().models[CODER].state == "draining"
        assert gate.swap.unloads() == []
        assert core.state_event()["draining"] == [CODER]
        core.front.drained(CODER, "d1")  # the request finished
        await until(lambda: CODER not in core.state.models, core=core)
        assert gate.swap.unloads() == [CODER] and front.about(CODER) == [("drain", "d1"), ("unloaded", None)]
        await core.stop()


@pytest.mark.parametrize("then", ["ready", "stopping", "gone"])
@pytest.mark.anyio
async def test_a_model_restored_in_a_state_it_didnt_know_waits_for_one_it_does(tmp_path, then):
    # Its unload requested, and /running showing it in a state the gate doesn't know: counted as starting, never
    # served, not resumed; then, once /running shows one it knows, drained and unloaded (ready), held until gone
    # (stopping), or released (gone), under its saved id (the controller's ruling at Task 13's review).
    state = loaded(*RESIDENTS, issued={CODER: "t9"})
    state.models[CODER] = record(CODER, state="draining", unload_requested_at=T - 3, drain_id="d1",
                                 drain_why="unload")
    gate = Gate(tmp_path, state=state, shown=dict.fromkeys(RESIDENTS, "ready") | {CODER: "shutdown"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        await core.read_running()
        await settle()
        assert core.state.models[CODER].state == "starting" and front.about(CODER) == []
        asked = admit(core, "r1", CODER, T + 1, key="dan-mac")
        await settle()
        assert not asked.done()  # never served meanwhile
        gate.clock.now = T + 1
        core.admitter.changed()
        assert (await asked).code == "draining" and gate.swap.unloads() == [] and gate.swap.loads() == []
        if then == "gone":
            gate.swap.states.pop(CODER)
        else:
            gate.swap.states[CODER] = then
        await core.read_running()
        if then == "stopping":
            await settle()
            assert core.state.models[CODER].state == "stopping" and front.about(CODER) == []
            gate.swap.states[CODER] = "stopped"
        await until(lambda: CODER not in core.state.models, core=core)
        await settle()
        heard = {"ready": [("drain", "d1"), ("unloaded", None)], "stopping": [("unloaded", None)],
                 "gone": [("unloaded", None)]}[then]
        assert front.about(CODER) == heard
        assert gate.swap.unloads() == ([CODER] if then == "ready" else [])
        if then == "gone":
            assert CODER not in core.state.issued  # its load has gone, and its ticket's id with it
        await core.stop()


@pytest.mark.anyio
async def test_a_resumed_make_room_drain_puts_its_model_on_the_hold(tmp_path):
    # A gate killed mid-run: the make-room drain it resumes ends with the model on the room hold that stands, so its
    # --done reloads it.
    state = loaded(EMBED, WHISPER, room_hold=RoomHold(size_gib=40.0, until=None, boot_id=BOOT, created_at=T - 60,
                                                      unloaded=[], total_gib=40.0))
    state.models[GEMMA] = record(GEMMA, state="draining", unload_requested_at=T - 3, drain_id="d1",
                                 drain_why="make-room")
    gate = Gate(tmp_path, state=state, shown={GEMMA: "ready", EMBED: "ready", WHISPER: "ready"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        Front(core)
        await until(lambda: GEMMA not in core.state.models, core=core)
        assert core.state.room_hold.unloaded == [GEMMA] and gate.saved().room_hold.unloaded == [GEMMA]
        assert GEMMA not in gate.swap.loads()  # held for Dan, not reloaded
        await core.stop()


@pytest.mark.anyio
async def test_a_hold_left_aside_is_put_back_before_admission_reads_it(tmp_path):
    # The gate stopped inside release_hold's window, the brake's hold moved aside: put back first, so nothing loads,
    # and, from another boot, it waits for Dan and says so (room.at_start's `recovered`).
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    fired = datetime.fromtimestamp(T - 600).isoformat(timespec="seconds")
    write_hold(gate.brake, Hold(fired, "19.6 GiB available", (CODER,), "b0", 1, None, 19.6))
    (gate.brake / HOLD_FILE).rename(gate.brake / f"{HOLD_FILE}.releasing-1-ab12")
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        assert read_hold(gate.brake) is not None
        asked = admit(core, "r1", CODER, T + 5, key="dan-mac")
        await core.each_five_s()
        gate.clock.now = T + 5
        core.admitter.changed()
        assert (await asked).code == "held_by_brake" and gate.swap.loads() == []
        [words] = await published(gate, core, "brake_needs_release")
        assert "while it was lifting that hold" in words
        await core.stop()


@pytest.mark.anyio
async def test_the_admitter_is_built_from_the_start_read_with_nothing_awaited_after_it(tmp_path, monkeypatch):
    # A ticket issued before the gate stopped, its model not yet started. launch, another process, may claim it at any
    # moment; were anything awaited between /running's read and the Admitter's withdraw, a claim could land there and
    # start a model nothing counts (the controller's ruling at Task 15's re-review).
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, issued={CODER: "t9"}), shown=dict.fromkeys(RESIDENTS, "ready"))
    tickets.issue(gate.launch, CODER, T + 200, now=T - 1, footprint_gib=33.0, available_gib=74.0, boot_id=BOOT)
    claims: list = []
    read = gate.swap.running

    async def running():
        listed = await read()
        if not claims:  # launch's claim, made the moment the start's read has answered: in the next step of the loop
            claims.append(None)
            asyncio.get_running_loop().call_soon(
                lambda: claims.__setitem__(0, tickets.claim(gate.launch, CODER, T, boot_id=BOOT)))
        return listed

    gate.swap.running = running
    built: list = []
    import spark.gate.core as core_module

    class Seen(core_module.Admitter):
        def __init__(self, *args, **kwargs):
            built.append(kwargs["running"])
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(core_module, "Admitter", Seen)
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        assert [[entry.model for entry in read] for read in built] == [sorted(RESIDENTS)]  # the start's own read
        assert [claim.ok for claim in claims] == [False]  # the Admitter withdrew it first: no start went unseen
        await core.stop()


@pytest.mark.anyio
async def test_the_brakes_unload_of_a_loading_model_is_marked_from_its_start(tmp_path):
    # The coder loading, admitted with 74 GiB available; the brake unloads it at 40 GiB: its mark says it was seen
    # using 34, though the admitter settled the load as gone before the brake's events were read.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.swap.hold_loads = True
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        asked = admit(core, "r1", CODER, T + 200)
        await settle()
        core.each_second()
        call = gate.swap._loads.pop(CODER)
        call.set_result(LoadOutcome.failed(500, "unspecific error: group: model unloaded"))  # the brake's unload
        events = gate.brake / brakeevents.EVENTS_FILE
        append_event(events, BrakeEvent(1, T + 1, BOOT, 1, "fired", None, None, 40.0, 20.0, False))
        append_event(events, BrakeEvent(1, T + 1.5, BOOT, 1, "unload", CODER, "starting", 40.0, 20.0, False))
        await settle()
        assert CODER not in core.state.models and not asked.done()
        gate.clock.now = T + 2
        core.each_second()
        assert core.state.brake_marks[CODER].seen_gib == 34.0
        asked.cancel()
        await core.stop()


@pytest.mark.anyio
async def test_admission_reads_the_brakes_hold_fresh(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        fired = datetime.fromtimestamp(T).isoformat(timespec="seconds")
        write_hold(gate.brake, Hold(fired, "19.6 GiB available", (), BOOT, 1, None, 19.6))
        asked = admit(core, "r1", CODER, T + 1, key="dan-mac")
        gate.clock.now = T + 1
        core.read_memory()
        assert (await asked).code == "held_by_brake"
        (gate.brake / HOLD_FILE).unlink()
        assert (await admit(core, "r2", CODER, T + 30, key="dan-mac")).model == CODER
        await core.stop()


@pytest.mark.anyio
async def test_a_model_the_registry_doesnt_know_is_counted_and_named(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready") | {"mystery": "ready"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        assert core.state.models["mystery"].footprint_gib == 41.0  # the registry's largest, the coder's
        assert core.status_view()["problems"] == [
            "llama-swap runs mystery, which the registry doesn't know; it is counted at 41 GiB, the largest footprint "
            "the registry has."]
        await core.stop()


@pytest.mark.anyio
async def test_a_start_with_running_unanswered_keeps_what_it_counted(tmp_path):
    # /running doesn't answer at the start: the state's own records stand, counted, and nothing loads until it does.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.swap.down = True
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        assert set(core.state.models) == set(RESIDENTS)
        assert core.admitter.free_for_a_load(uses_hold=True) == min(IDLE - 43 - 24, 102 - 43)
        asked = admit(core, "r1", CODER, T + 5, key="dan-mac")
        gate.clock.now = T + 5
        core.admitter.changed()
        assert (await asked).code == "llama_swap_down" and gate.swap.loads() == []
        gate.swap.down = False
        gate.swap.states.pop(WHISPER)  # it went while llama-swap couldn't be read: the residents' rule reloads it
        await core.read_running()
        await until(lambda: WHISPER in core.state.models, core=core)
        assert gate.swap.loads() == [WHISPER] and core.state.resident_reloads == {WHISPER: T + 5}
        await core.stop()


# After damage: the gate's saved state was lost.

@pytest.mark.anyio
async def test_after_damage_a_drained_ready_model_is_drained_again_then_unloaded(tmp_path):
    gate = Gate(tmp_path, damaged=True, shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        assert core.state.fresh_after_damage is True
        assert len(await published(gate, core, "room_hold_ended")) == 1  # why `damaged`: a hold may have stood
        front = Front(core)
        front.report(draining=[CODER])  # its first snapshot: it holds the coder drained
        await until(lambda: CODER not in core.state.models, core=core)
        [drain] = front.ops("drain")
        assert drain["why"] == "unknown" and drain["drain_id"]
        assert front.about(CODER) == [("drain", drain["drain_id"]), ("unloaded", None)]
        assert gate.swap.unloads() == [CODER] and front.ops("undrain") == []
        assert core.state.fresh_after_damage is False and gate.saved().fresh_after_damage is False
        await core.stop()


@pytest.mark.anyio
async def test_after_damage_a_drained_stopping_model_is_held_until_gone(tmp_path):
    gate = Gate(tmp_path, damaged=True, shown=dict.fromkeys(RESIDENTS, "ready") | {CODER: "stopping"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report(draining=[CODER])
        await core.read_running()
        await settle()
        assert front.about(CODER) == [] and core.state.models[CODER].state == "stopping"  # never undrained
        gate.swap.states[CODER] = "stopped"
        await core.read_running()
        await settle()
        assert front.about(CODER) == [("unloaded", None)] and gate.swap.unloads() == []
        await core.stop()


@pytest.mark.anyio
async def test_after_damage_a_drained_model_gone_is_released_with_unloaded(tmp_path):
    gate = Gate(tmp_path, damaged=True, shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report(draining=[CODER])
        await settle()
        assert front.about(CODER) == [("unloaded", None)] and gate.swap.unloads() == []
        await core.stop()


@pytest.mark.parametrize("becomes", ["ready", "gone"])
@pytest.mark.anyio
async def test_after_damage_a_drained_starting_model_waits_for_ready_or_gone(tmp_path, becomes):
    gate = Gate(tmp_path, damaged=True, shown=dict.fromkeys(RESIDENTS, "ready") | {CODER: "starting"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report(draining=[CODER])
        await core.read_running()
        await settle()
        assert front.about(CODER) == [] and core.state.models[CODER].state == "starting"
        if becomes == "ready":
            gate.swap.states[CODER] = "ready"
        else:
            gate.swap.states.pop(CODER)
        await until(lambda: CODER not in core.state.models, core=core)
        if becomes == "ready":
            [drain] = front.ops("drain")
            assert front.about(CODER) == [("drain", drain["drain_id"]), ("unloaded", None)]
            assert gate.swap.unloads() == [CODER]
        else:
            assert front.about(CODER) == [("unloaded", None)] and gate.swap.unloads() == []
        assert front.ops("undrain") == []
        await core.stop()


@pytest.mark.anyio
async def test_after_damage_the_brakes_old_events_and_the_engines_found_are_passed_over(tmp_path, caplog):
    gate = Gate(tmp_path, damaged=True, shown=dict.fromkeys(RESIDENTS, "ready"))
    events = gate.brake / brakeevents.EVENTS_FILE
    append_event(events, BrakeEvent(1, T - 120, BOOT, 1, "fired", None, None, 19.6, 20.0, False))
    append_event(events, BrakeEvent(1, T - 119, BOOT, 1, "unload", CODER, "idle", 19.6, 20.0, False))
    port = engine_ports(REGISTRY)[EMBED]
    process(gate.proc, 4242, "llama-server", uid=SPARK, rss_anon_kib=gib(2),
            argv=("llama-server", "--host", "127.0.0.1", "--port", str(port)))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        assert core.state.brake_events_after[:2] == (BOOT, 2)  # started at the file's end
        assert core.state.ticketed[EMBED].ticket_id == FOUND_AFTER_DAMAGE
        assert any("found after damage" in r.getMessage() for r in caplog.records)
        gate.clock.now = T + 1
        core.each_second()
        await core.each_five_s()
        assert core.status_view()["health"]["unticketed_engines"] == []
        append_event(events, BrakeEvent(1, T + 2, BOOT, 2, "fired", None, None, 19.0, 20.0, False))
        append_event(events, BrakeEvent(1, T + 2.5, BOOT, 2, "unload", GEMMA, "idle", 19.0, 20.0, False))
        gate.clock.now = T + 3
        core.each_second()
        assert len(await published(gate, core, "brake_fired")) == 1  # the new episode, never the old one
        assert "brake's events from before the damage weren't read" in " ".join(core.status_view()["problems"])
        await core.stop()


# The bypass check and the holders.

@pytest.mark.anyio
async def test_an_engine_without_a_ticket_is_reported(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    port = engine_ports(REGISTRY)[EMBED]
    assert port == 801
    process(gate.proc, 4242, "llama-server", uid=SPARK, rss_anon_kib=gib(2),
            argv=("llama-server", "--host", "127.0.0.1", "--port", "801"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        await core.each_five_s()
        assert core.status_view()["health"]["unticketed_engines"] == [{"model": EMBED, "port": 801, "pid": 4242}]
        # Its pid in state.ticketed, started when the record says: ticketed.
        core.state.ticketed[EMBED] = Ticketed(EMBED, 4242, "t1", T - 3600, start_of(4242))
        await core.each_five_s()
        assert core.status_view()["health"]["unticketed_engines"] == []
        # A live started/ record with its pid: ticketed only when its ticket's id is one the gate issued.
        del core.state.ticketed[EMBED]
        started = {"model": EMBED, "id": "t2", "issued_at": T - 5, "deadline": T + 195, "nonce": "n" * 32,
                   "boot_id": BOOT, "footprint_gib": 8.0, "available_gib": 80.0, "started_at": T - 4, "pid": 4242}
        tickets.write_whole(tickets.record_path(gate.launch, tickets.STARTED, EMBED), started)
        await core.each_five_s()
        assert core.status_view()["health"]["unticketed_engines"] == [{"model": EMBED, "port": 801, "pid": 4242}]
        core.state.issued[EMBED] = "t2"
        await core.each_five_s()
        assert core.status_view()["health"]["unticketed_engines"] == []
        # launch's refusals for want of a ticket, each counted once as its record appears this boot.
        assert core.status_view()["health"]["no_ticket_refusals"] == 0

        def refused(model: str, when: float, code: str) -> None:
            tickets.write_whole(tickets.record_path(gate.launch, "refusals", model), {
                "at": datetime.fromtimestamp(when).isoformat(timespec="seconds"), "model": model, "code": code,
                "reason": "no admission ticket from the gate"})

        refused(CODER, T - 86400 - 60, "no_ticket")  # before this boot: not counted
        await core.each_five_s()
        assert core.status_view()["health"]["no_ticket_refusals"] == 0
        for model, when, code in ((CODER, T + 1, "no_ticket"), (CODER, T + 2, "no_ticket"),
                                  (GEMMA, T + 3, "ticket_expired")):
            refused(model, when, code)
            await core.each_five_s()
            await core.each_five_s()
        refused(WHISPER, T + 4, "no_fit")  # another refusal: not the backstop
        await core.each_five_s()
        assert core.status_view()["health"]["no_ticket_refusals"] == 3
        await core.stop()


@pytest.mark.anyio
async def test_the_holders_never_count_a_ticketed_engine(tmp_path):
    state = loaded(*RESIDENTS)
    state.ticketed[EMBED] = Ticketed(EMBED, 4242, "t1", T - 3600, start_of(4242))
    gate = Gate(tmp_path, state=state, shown=dict.fromkeys(RESIDENTS, "ready"))
    # A spark process of a comm no engine has, on the embeddings' port, its pid the one its ticket started.
    process(gate.proc, 4242, "python3", uid=SPARK, rss_anon_kib=gib(30), argv=("python3", "--port", "801"))
    process(gate.proc, 5000, "python3", uid=DAN, rss_anon_kib=gib(20))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        await core.each_five_s()
        names = [holder.name for holder in core.holders(True)]
        assert names == ["Gemma", "python3 (chendaniely)", "the embeddings"]
        assert core.status_view()["health"]["unticketed_engines"] == []
        await core.stop()


@pytest.mark.anyio
async def test_dans_processes_follow_spark_admins_members(tmp_path):
    # `dan_uids` may be what gives spark-admin's members, asked at each holders' read (the controller's ruling at Task
    # 19's review), so a member added is named as Dan's without a restart.
    members: set[int] = set()
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    process(gate.proc, 5000, "python3", uid=DAN, rss_anon_kib=gib(40))
    with anyio.fail_after(BOUND_S):
        core = await gate.started(dan_uids=lambda: set(members))
        await core.each_five_s()
        assert [holder.dans for holder in core.holders(True) if holder.name.startswith("python3")] == [False]
        members.add(DAN)
        await core.each_five_s()
        assert [holder.dans for holder in core.holders(True) if holder.name.startswith("python3")] == [True]
        await core.stop()


@pytest.mark.anyio
async def test_a_stop_after_a_start_cut_short_is_prompt(tmp_path, monkeypatch, caplog):
    # `spark gate`'s SIGTERM during its start cancels core.start() (the controller's ruling at Task 19's review). Cut
    # where its parts are built but the Notifier's task isn't yet running, the stop never waits for a queue nothing
    # sends, and says nothing of ntfy: a start after damage, cut in its scan of the engines running.
    gate = Gate(tmp_path, damaged=True, shown=dict.fromkeys(RESIDENTS, "ready"))
    scanning, free = threading.Event(), threading.Event()

    def scan(ports):
        scanning.set()
        free.wait(BOUND_S)
        return {}

    with anyio.fail_after(BOUND_S):
        core = gate.core()
        monkeypatch.setattr(core, "_engines_running", scan)
        starting = task(core.start(loops=False))
        while not scanning.is_set():
            await asyncio.sleep(0.01)
        assert core.notifier is not None and core.notifier.queued  # room_hold_ended (damaged), queued, unsent
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)
        began = time.monotonic()
        await core.stop()
        assert time.monotonic() - began < 1.0
        free.set()
    assert not [r for r in caplog.records if "ntfy didn't take" in r.getMessage()]


@pytest.mark.anyio
async def test_a_blocking_read_never_holds_the_loop(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.nvidia = lambda: time.sleep(1.0) or "5000, 20480\n"
    process(gate.proc, 5000, "python3", uid=DAN, rss_anon_kib=gib(1))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        reading = task(core.each_five_s())
        await asyncio.sleep(0.05)
        began = time.monotonic()
        view = core.status_view()
        assert time.monotonic() - began < 0.3
        assert set(view) == gateproto.StatusView.__required_keys__
        began = time.monotonic()
        await asyncio.sleep(0.01)
        assert time.monotonic() - began < 0.3  # the loop still turns while nvidia-smi runs
        assert "python3 (chendaniely)" not in [h.name for h in core.holders(True)]  # the last reading, until done
        await reading
        assert "python3 (chendaniely)" in [h.name for h in core.holders(True)]
        await core.stop()


@pytest.mark.anyio
async def test_a_read_that_fails_is_reported_and_never_ends_the_loop(tmp_path, caplog):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    process(gate.proc, 5000, "python3", uid=DAN, rss_anon_kib=gib(1))
    (gate.proc / "5000" / "status").chmod(0)  # /proc lists it, but won't let the gate read it
    (gate.launch / tickets.STARTED).write_text("not a folder")
    gate.units.broken.add("brake")
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        await core.each_five_s()
        problems = core.status_view()["health"]["read_problems"]
        assert any("processes" in p for p in problems) and any("started" in p for p in problems)
        assert any("brake" in p for p in problems)
        (gate.proc / "5000" / "status").chmod(0o644)
        (gate.launch / tickets.STARTED).unlink()
        gate.units.broken.clear()
        await core.each_five_s()
        assert core.status_view()["health"]["read_problems"] == []
        await core.stop()


# The loops' readings.

@pytest.mark.anyio
async def test_a_failed_running_read_never_reaches_observe_running(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready") | {CODER: "starting"})
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        seen: list = []
        core.admitter.observe_running = seen.append
        gate.swap.down = True
        await core.read_running()
        assert seen == [] and CODER in core.state.models
        gate.swap.down = False
        await core.read_running()
        assert [[entry.model for entry in read] for read in seen] == [sorted([*RESIDENTS, CODER])]
        await core.stop()


@pytest.mark.anyio
async def test_a_model_running_lists_that_the_state_lacks_is_counted(tmp_path):
    state = loaded(GEMMA, EMBED, dans_unloads={WHISPER: BOOT})  # whisper not loaded, and not reloaded by itself
    gate = Gate(tmp_path, state=state, shown={GEMMA: "ready", EMBED: "ready"})
    gate.available = IDLE - 40  # the coder's start hasn't taken memory yet
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        free = core.admitter.free_for_a_load(uses_hold=True)
        gate.swap.states[CODER] = "starting"  # a start the gate never admitted, seen by its next read
        await core.read_running()
        assert core.state.models[CODER].state == "starting"
        assert core.state.models[CODER].footprint_gib == FOOTPRINT[CODER]
        assert core.admitter.free_for_a_load(uses_hold=True) == free - 41  # counted while it starts
        asked = admit(core, "r1", WHISPER, T + 5, key="dan-mac")  # whisper waits: one load at a time
        await settle()
        assert gate.swap.loads() == [] and not asked.done()
        gate.swap.states[CODER] = "ready"
        await core.read_running()
        assert core.state.models[CODER].state == "ready"
        await until(lambda: asked.done(), core=core)
        assert gate.swap.loads() == [WHISPER]
        await core.stop()


@pytest.mark.anyio
async def test_memory_readings_drive_admission_the_drains_and_the_warning(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        calls: list[str] = []
        admitter_changed, drainer_changed = core.admitter.changed, core.drainer.changed
        core.admitter.changed = lambda: calls.append("admitter") or admitter_changed()
        core.drainer.changed = lambda: calls.append("drainer") or drainer_changed()
        core.read_memory()
        assert calls == ["admitter", "drainer"]
        gate.other = 50.0  # 74 − 50 = 24 GiB available: under the 28 GiB warning line
        for t in (1, 2, 3):
            gate.clock.now = T + t
            core.read_memory()
        gate.other = 0.0
        gate.clock.now = T + 4
        core.read_memory()
        gate.other = 50.0
        gate.clock.now = T + 5
        core.read_memory()
        assert len(await published(gate, core, "memory_warning", 2)) == 2  # once per fall
        await core.stop()


@pytest.mark.anyio
async def test_the_brakes_release_counts_from_the_last_rise_above_the_warn_line(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        asked: list = []

        async def check_brake(now, *, above_since):
            asked.append((now, above_since))

        core.room.check_brake = check_brake
        gate.clock.now = T + 1
        core.read_memory()  # 74 available: above the 28 GiB line since the start's own reading, at T
        await core.each_five_s()
        gate.other = 50.0
        gate.clock.now = T + 2
        core.read_memory()  # a dip under it
        gate.other = 0.0
        gate.clock.now = T + 3
        core.read_memory()
        gate.clock.now = T + 6
        await core.each_five_s()
        assert asked == [(T + 1, T), (T + 6, T + 3)]  # the dip restarted the five minutes
        await core.stop()


@pytest.mark.anyio
async def test_the_five_second_checks_unload_the_idle_and_end_what_expired(tmp_path):
    state = loaded(*RESIDENTS, CODER)
    state.pins[GEMMA] = Pin(model=GEMMA, until=T + 100, by_uid=DAN)
    state.room_hold = RoomHold(size_gib=10.0, until=T + 100, boot_id=BOOT, created_at=T - 60, unloaded=[],
                               total_gib=10.0)
    gate = Gate(tmp_path, state=state, shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report()
        gate.clock.now = T + 60 * 60 + 5  # the coder, an on-demand model, idle an hour
        await core.read_running()  # as the loop reads it every second: llama-swap answers
        await core.each_five_s()
        await until(lambda: CODER not in core.state.models, core=core)
        assert [(d["model"], d["why"]) for d in front.ops("drain")] == [(CODER, "idle")]
        assert gate.swap.unloads() == [CODER]
        assert core.state.pins == {} and core.state.room_hold is None
        assert len(await published(gate, core, "pin_ended")) == 1
        assert len(await published(gate, core, "room_hold_ended")) == 1
        await core.stop()


# The front's view, the status view, saving and stopping.

@pytest.mark.anyio
async def test_hello_and_state_carry_the_model_list(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        await settle()
        hello, state = front.seen[:2]
        listed = [{"name": name, "roles": list(m.roles), "label": m.label, "resident": m.resident}
                  for name, m in REGISTRY.models.items()]
        assert hello == {"op": "hello", "gate_started_at": T, "applying": False, "lapse_s": APPLY_LAPSE_S,
                         "models": listed}
        assert state == {"op": "state", "ready": sorted(RESIDENTS), "starting": [], "draining": [],
                         "models": listed}
        gate.swap.states[CODER] = "starting"
        await core.read_running()
        await settle()
        assert front.ops("state")[-1]["starting"] == [CODER]
        await core.stop()


@pytest.mark.anyio
async def test_the_status_view_merges_recent_and_says_what_it_holds(tmp_path):
    state = loaded(*RESIDENTS)
    state.refusals.append(RefusalRecord(at=T - 30, model=CODER, key="agent", uid=None, code="no_fit",
                                        message="The coder didn't load."))
    state.recent.append(RecentRecord(at=T - 60, kind="brake_released", text="brake released; nothing to reload",
                                     model=None, uids=[]))
    state.recent.append(RecentRecord(at=T - 10, kind="unload", text="`spark unload` unloaded the coder.",
                                     model=CODER, uids=[DAN]))
    gate = Gate(tmp_path, state=state, shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        core.notifier.dropped = 3
        view = core.status_view()
        assert [(r["at"], r["code"], r["key_label"]) for r in view["recent"]] == [
            (T - 60, None, None), (T - 30, "no_fit", "agent"), (T - 10, None, None)]
        assert view["health"]["ntfy"] == {"status": "ok", "failing_since": None, "dropped": 3}
        assert view["health"]["damaged_at"] is None and view["health"]["damaged_kept_as"] is None
        # Through the status socket, the caller's own: Task 19 passes what it may see.
        mine = core.status_view(sees=lambda item: getattr(item, "uids", None) == [] or getattr(item, "key", None)
                                == "agent")
        assert [r["at"] for r in mine["recent"]] == [T - 60, T - 30]
        models = {m["name"]: m for m in view["models"]}
        assert models[GEMMA]["state"] == "ready" and models[CODER]["state"] == "not_loaded"
        assert view["memory"]["available_gib"] == IDLE - 43 and view["applying"] is None
        await core.stop()


@pytest.mark.anyio
async def test_a_gate_without_ntfy_reports_not_set_up(tmp_path):
    # Task 29 (2026-10-14, Dan's decision: ntfy optional until Phase 5): the publisher of a gate without ntfy says it is
    # that one, so `health.ntfy.status` reads not_set_up, before any notification and after one has failed; a publisher
    # that fails as ntfy out of reach does reads failing.
    from spark.gate.main import _NoNtfy
    from spark.gate.notify import NtfyError

    class Unreached:
        async def __call__(self, n) -> None:
            raise NtfyError("ntfy didn't answer", "unreached")

    for publish, then in ((_NoNtfy("NTFY_URL or NTFY_TOPIC_GATE isn't set"), "not_set_up"), (Unreached(), "failing")):
        (tmp_path / then).mkdir()
        gate = Gate(tmp_path / then, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
        with anyio.fail_after(BOUND_S):
            core = await gate.started(publish=publish)
            assert core.status_view()["health"]["ntfy"]["status"] == ("not_set_up" if then == "not_set_up" else "ok")
            core.notifier.emit("loaded", "load:t1", label="the coder", seconds=24)
            await until(lambda: core.state.notify_failing_since is not None)
            ntfy = core.status_view()["health"]["ntfy"]
            assert ntfy["status"] == then and ntfy["failing_since"] == T  # carried as it is, for 2b's menu bar
            await core.stop()


def _check_at(when: float) -> str:
    """A start check's time as the brake records it: local, to the second, with its offset (brake._now)."""
    return datetime.fromtimestamp(when, ZONE).isoformat(timespec="seconds")


@pytest.mark.anyio
async def test_the_brakes_start_check_reaches_health(tmp_path):
    # Task 29 (the controller's ruling, 2026-10-14): the brake's check at start that llama-swap takes its key moved into
    # the gate (plan.md, *Visibility and notifications*). The gate reads the brake's record on each view: a passed check
    # is `ok` with its time; a failed one, one still waiting, none recorded or one that can't be read says so, never ok.
    from spark.brake import KEY_CHECK_FILE, KeyCheck, write_key_check

    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()

        def brake() -> dict:
            return core.status_view()["health"]["brake"]

        write_key_check(gate.brake, KeyCheck(_check_at(T - 60), True, "passed", "credential llamaswap-key"))
        assert brake() == {"status": "unknown", "key_checked_at": T - 60}  # the brake's unit not read yet
        await core.each_five_s()  # the units read: the brake's is up
        assert brake() == {"status": "ok", "key_checked_at": T - 60}
        write_key_check(gate.brake, KeyCheck(_check_at(T), None, "waiting for llama-swap to answer, for 30 s at most",
                                             "credential llamaswap-key"))
        assert brake() == {"status": "start check waiting for llama-swap", "key_checked_at": None}
        write_key_check(gate.brake, KeyCheck(_check_at(T), False, "llama-swap GET /running:\nHTTP 401",
                                             "credential llamaswap-key"))
        assert brake() == {"status": "start check failed: llama-swap GET /running: HTTP 401", "key_checked_at": None}
        (gate.brake / KEY_CHECK_FILE).write_text("{not json")
        assert brake() == {"status": "start check unreadable", "key_checked_at": None}
        (gate.brake / KEY_CHECK_FILE).unlink()
        assert brake() == {"status": "start check not recorded", "key_checked_at": None}
        # A record from before the brake wrote its offset (Phase 1's) reads as local time.
        naive = datetime.fromtimestamp(T - 60, ZONE).replace(tzinfo=None).isoformat(timespec="seconds")
        write_key_check(gate.brake, KeyCheck(naive, True, "passed", "LLAMASWAP_KEY_SPARK"))
        assert brake() == {"status": "ok", "key_checked_at": T - 60}
        gate.units.states["brake"] = UnitState(False, 0, T - 3600, None, "exit-code")
        await core.each_five_s()
        assert brake() == {"status": "down", "key_checked_at": T - 60}
        await core.stop()


@pytest.mark.anyio
async def test_a_waiting_rows_figure_is_its_own_groups(tmp_path):
    # Task 29's review, I1: each waiting row carries its own group's free for a load, never the viewer's. make-room's
    # hold of 35 GiB is Dan's to load into, so his view reads 37; agent's request and the gate's own reload of whisper
    # (its start's, whisper being a resident not loaded) can't load into it, so each reads 37 − 35 = 2.
    hold = RoomHold(size_gib=35.0, until=None, boot_id=BOOT, created_at=T - 60, unloaded=[], total_gib=35.0)
    gate = Gate(tmp_path, state=loaded(GEMMA, EMBED, room_hold=hold), shown={GEMMA: "ready", EMBED: "ready"})
    gate.available = 61.0
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        core.read_memory()
        asked = admit(core, "r-agent", CODER, T + 600, key="agent")
        await settle()
        view = core.status_view()
        assert view["memory"]["free_for_a_load_gib"] == 37.0
        assert [(row["key_label"], row["model"], row["why"], row["free_gib"]) for row in view["waiting"]] == [
            ("the gate", WHISPER, "memory", 2.0), ("agent", CODER, "memory", 2.0)]
        asked.cancel()
        await core.stop()


@pytest.mark.anyio
async def test_free_for_a_load_and_owed_come_from_one_reading(tmp_path):
    # Task 29's review, I2: the figure, *owed* and *available* are one reading's, so the breakdown status shows adds
    # up; a fresh read in between (here 0.008 GiB lower, then failing) changes nothing in the view. With no reading at
    # all, the view says memory can't be read, never a figure of 0.
    reads = []

    def meminfo() -> MemInfo:
        reads.append(len(reads))
        if len(reads) == 1:
            return MemInfo(121.6, 48.004)
        if len(reads) == 2:
            return MemInfo(121.6, 47.996)
        raise OSError(5, "Input/output error")

    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started(read_meminfo=meminfo)
        reads.clear()
        core.read_memory()  # 48.004, the reading the view keeps
        memory = core.status_view()["memory"]
        assert memory["available_gib"] == 48.004
        assert memory["free_for_a_load_gib"] == pytest.approx(48.004 - 24 - memory["owed_gib"])
        assert (memory["ceiling_binds"], memory["starting_gib"], memory["ceiling_gib"], memory["committed_gib"]) == (
            False, 0.0, 102.0, 43.0)
        await core.stop()

    def unreadable() -> MemInfo:
        raise OSError(5, "Input/output error")

    (tmp_path / "unread").mkdir()
    gate = Gate(tmp_path / "unread", state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started(read_meminfo=unreadable)
        view = core.status_view()
        memory = view["memory"]
        assert (memory["available_gib"], memory["total_gib"], memory["free_for_a_load_gib"],
                memory["above_brake_gib"], memory["unaccounted_gib"], memory["ceiling_binds"]) == (None,) * 6
        assert all(row["free_gib"] is None for row in view["waiting"])
        await core.stop()


@pytest.mark.anyio
async def test_the_brakes_records_are_read_without_following_a_link_or_waiting(tmp_path):
    # Task 29's review, I3: the gate reads the brake's start check and its hold on its event loop, for any caller of
    # the status socket and in admission, so neither read follows a link, reads past a cap or waits; and a time at the
    # calendar's edge is a record that can't be read, never a view that fails. The reason shows under health.
    from spark.brake import KEY_CHECK_FILE, KeyCheck, write_key_check

    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    write_key_check(elsewhere, KeyCheck(_check_at(T - 60), True, "passed", "credential llamaswap-key"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        await core.each_five_s()
        record = gate.brake / KEY_CHECK_FILE
        record.symlink_to(elsewhere / KEY_CHECK_FILE)  # a passed check, through a link: not read
        health = core.status_view()["health"]
        assert health["brake"] == {"status": "start check unreadable", "key_checked_at": None}
        assert [p for p in health["read_problems"] if p.startswith(f"the brake's start check {record} can't be read")]
        record.unlink()
        passed = (elsewhere / KEY_CHECK_FILE).read_text()
        record.write_text(passed[:-1] + ", \"pad\": \"" + "x" * 70_000 + "\"}")  # past the cap
        assert core.status_view()["health"]["brake"]["status"] == "start check unreadable"
        for edge in ("0001-01-01T00:00:00", "9999-12-31T23:59:59"):  # Phase 1's naive form, at the calendar's edges
            record.unlink()
            write_key_check(gate.brake, KeyCheck(edge, True, "passed", "LLAMASWAP_KEY_SPARK"))
            health = core.status_view()["health"]
            assert health["brake"] == {"status": "start check unreadable", "key_checked_at": None}
            assert any("time" in p and KEY_CHECK_FILE in p for p in health["read_problems"])
        # The brake's hold, the same: a link to a hold isn't followed, and the view says the file can't be read.
        write_hold(elsewhere, Hold(_check_at(T - 60), "19.6 GiB available", (), boot_id=BOOT, episode=1,
                                   available_gib=19.6))
        (gate.brake / HOLD_FILE).symlink_to(elsewhere / HOLD_FILE)
        paused = core.status_view()["paused"]
        assert paused["since"] is None and paused["available_gib"] is None
        assert paused["problem"].startswith(f"hold file {gate.brake / HOLD_FILE} can't be read: ")
        await core.stop()


@pytest.mark.anyio
async def test_the_status_view_carries_the_damage(tmp_path):
    gate = Gate(tmp_path, damaged=True, shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        health = core.status_view()["health"]
        assert health["damaged_at"] == T and health["damaged_kept_as"].startswith(str(gate.state_dir / STATE_FILE))
        assert any("can't be read" in p for p in core.status_view()["problems"])
        await core.stop()


@pytest.mark.anyio
async def test_every_save_lands_in_call_order(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        first = RoomHold(size_gib=10.0, until=None, boot_id=BOOT, created_at=T, unloaded=[], total_gib=10.0)
        core.state.room_hold = first
        assert core.save() is None  # written before it returns: nothing left to land later
        assert gate.saved().room_hold == first
        core.state.room_hold = None
        core.save()
        assert gate.saved().room_hold is None
        await core.stop()


@pytest.mark.anyio
async def test_an_emit_that_raises_never_stops_the_gate(tmp_path, caplog):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()

        def refuse(kind, key, /, **fields):
            raise ValueError(f"{kind}'s words need something")

        await core.each_five_s()  # the units, as last read, which llama-swap's watch goes by
        core.notifier.emit = refuse
        append_event(gate.brake / brakeevents.EVENTS_FILE,
                     BrakeEvent(1, T - 1, BOOT, 1, "fired", None, None, 19.6, 20.0, False))
        append_event(gate.brake / brakeevents.EVENTS_FILE,
                     BrakeEvent(1, T - 0.5, BOOT, 1, "unload", GEMMA, "idle", 19.6, 20.0, False))
        gate.clock.now = T + 1
        core.each_second()  # the brake's ingest
        gate.swap.down = True
        for t in (2, 13):  # llama-swap hung with its unit up: the unit watch's llama_swap_down
            gate.clock.now = T + t
            await core.read_running()
        await core.each_five_s()
        lines = [r.getMessage() for r in caplog.records if r.name == "spark.gate.core" and r.levelno >= logging.ERROR]
        assert "the brake_fired notification couldn't be made: brake_fired's words need something" in lines
        assert ("the llama_swap_down notification couldn't be made: llama_swap_down's words need something"
                in lines)
        caplog.clear()
        gate.swap.down = False
        await core.read_running()
        assert core.state.brake_events_after[:2] == (BOOT, 2)
        await core.stop()


@pytest.mark.anyio
async def test_a_clean_stop_flushes_the_notifier_and_says_so(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        assert gate.saved().clean_shutdown is False
        flushed: list[str] = []
        flush = core.notifier.flush
        core.notifier.flush = lambda: flushed.append("flush") or flush()
        await core.stop()
        assert flushed == ["flush"] and gate.saved().clean_shutdown is True


@pytest.mark.anyio
async def test_the_gates_own_return_is_dated_from_its_last_activity_record(tmp_path):
    # The last run stopped uncleanly; its last activity record is the last moment it was alive.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, clean_shutdown=False), shown=dict.fromkeys(RESIDENTS, "ready"))
    (gate.state_dir / ACTIVITY_FILE).write_text(json.dumps({"written_at": T - 40, "boot_id": BOOT, "models": {}}))
    gate.units.states["gate"] = UnitState(True, 0, T - 1, None, "success")
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        gate.clock.now = T + 61
        await core.each_five_s()
        [words] = await published(gate, core, "back_up")
        assert words == messages.notification("back_up", REGISTRY, unit="gate", down_s=39.0).message
        await core.stop()


# The rulings at Task 18's review.

def this_boots_hold(gate: Gate) -> None:
    """The brake's hold of this boot, fired at T, reloading nothing: one rule 5 lets lift by itself."""
    fired = datetime.fromtimestamp(T).isoformat(timespec="seconds")
    write_hold(gate.brake, Hold(fired, "19.6 GiB available", (), BOOT, 1, None, 19.6))


@pytest.mark.anyio
async def test_no_automatic_release_while_llama_swap_isnt_answering(tmp_path):
    # Memory above the warn line for more than five minutes, and the reloads fit, but llama-swap doesn't answer: the
    # gate can't verify its reloads fit, so the hold stands (the controller's ruling at Task 18's review). Once
    # /running answers again, the next check lifts it.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    this_boots_hold(gate)
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        gate.swap.down = True
        for t in (0, 100, 200, 301, 306):
            gate.clock.now = T + t
            core.read_memory()
            await settle()
            await core.read_running()
            await core.each_five_s()
        assert read_hold(gate.brake) is not None and list(core.state.recent) == []
        assert core.state.last_auto_release_at is None
        gate.swap.down = False
        gate.clock.now = T + 310
        await core.read_running()
        await core.each_five_s()
        assert read_hold(gate.brake) is None
        assert [r.kind for r in core.state.recent] == ["brake_released"]
        assert len(await published(gate, core, "brake_released")) == 1
        await core.stop()


@pytest.mark.anyio
async def test_a_hung_read_never_counts_as_answering(tmp_path):
    # /running answered at the start, then nothing more answered (a read that hangs): after LLAMA_SWAP_HUNG_S the
    # gate counts llama-swap as not answering, and the hold stands; Dan's own release lifts it whatever llama-swap does.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    this_boots_hold(gate)
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        for t in (100, 200, 306):
            gate.clock.now = T + t
            core.read_memory()
            await settle()
            await core.each_five_s()
        assert read_hold(gate.brake) is not None and list(core.state.recent) == []
        released = await core.room.release(DAN, room=False, brake=True)  # make brake-release
        assert released.brake is True and read_hold(gate.brake) is None
        await core.stop()


@pytest.mark.anyio
async def test_the_idle_check_waits_while_llama_swap_isnt_answering(tmp_path):
    # No drain is begun that its unload couldn't follow: no drain and undrain every 5 s (the controller's ruling at
    # Task 18's review).
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, CODER), shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report()
        gate.swap.down = True
        gate.clock.now = T + 3605
        await core.read_running()
        await core.each_five_s()
        await settle()
        assert front.ops("drain") == [] and CODER in core.state.models
        gate.swap.down = False
        await core.read_running()
        await core.each_five_s()
        await until(lambda: CODER not in core.state.models, core=core)
        assert [(d["model"], d["why"]) for d in front.ops("drain")] == [(CODER, "idle")]
        await core.stop()


@pytest.mark.anyio
async def test_a_load_whose_ticket_cant_be_saved_is_never_sent(tmp_path, monkeypatch, caplog):
    # A load call is never sent before its ticket's id is on disk: a save that fails refuses the load as the gate's
    # own error (the controller's ruling at Task 18's review).
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        import spark.gate.core as core_module

        def full(*args, **kwargs):
            raise OSError(28, "No space left on device")

        real = core_module.save_state
        monkeypatch.setattr(core_module, "save_state", full)
        asked = admit(core, "r1", CODER, T + 30, key="dan-mac")
        result = await asked
        assert (result.code, result.message) == (
            "load_failed", "The gate couldn't save its state before loading the coder, so it didn't load it: "
                           "`make logs s=gate` on the Spark shows why.")
        assert gate.swap.loads() == [] and CODER not in core.state.models and core.state.issued == {}
        assert not tickets.record_path(gate.launch, tickets.TICKETS, CODER).exists()  # withdrawn: nothing to claim
        monkeypatch.setattr(core_module, "save_state", real)
        caplog.clear()
        await core.stop()


def agents_pi(gate: Gate, pid: int = 6000) -> None:
    """A live process of agent's, as a session needs one."""
    process(gate.proc, pid, "pi", uid=AGENT, rss_anon_kib=gib(0.5))


@pytest.mark.anyio
async def test_the_idle_check_leaves_a_model_a_live_session_keeps(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, CODER), shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    agents_pi(gate)
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report()
        session = core.sessions.register(AGENT, 6000, CODER, "agent's pi")
        assert session.model == CODER
        begun: list[tuple[str, str]] = []
        drain = core.drainer.drain
        core.drainer.drain = lambda model, why, **more: begun.append((model, why)) or drain(model, why, **more)
        gate.clock.now = T + 3605  # the coder idle an hour, but agent's session is live: no drain is even begun
        await core.read_running()
        await core.each_five_s()
        await settle()
        assert begun == [] and front.ops("drain") == [] and CODER in core.state.models
        await core.stop()


@pytest.mark.anyio
async def test_an_idle_drain_checks_each_session_as_sessions_do(tmp_path):
    # Right before it marks the model, an idle drain checks its sessions again with Sessions.live (Task 16's review):
    # one whose process has exited keeps nothing loaded.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, CODER), shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    agents_pi(gate)
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        front = Front(core)
        front.report()
        core.sessions.register(AGENT, 6000, CODER, "agent's pi")
        for entry in (gate.proc / "6000").iterdir():
            entry.unlink()
        (gate.proc / "6000").rmdir()  # agent's pi exited; its session isn't pruned yet
        gate.clock.now = T + 3605
        core._idle_check(gate.clock())
        await until(lambda: CODER not in core.state.models, core=core)
        assert [(d["model"], d["why"]) for d in front.ops("drain")] == [(CODER, "idle")]
        await core.stop()


@pytest.mark.anyio
async def test_sessions_name_only_the_registrys_models(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    agents_pi(gate)
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        refused = core.sessions.register(AGENT, 6000, "not-a-model", "agent's pi")
        assert (refused.status, refused.message) == (
            409, "There's no model 'not-a-model' in the registry, so no session can keep it loaded.")
        assert core.sessions.register(AGENT, 6000, CODER, "agent's pi").model == CODER
        await core.stop()


@pytest.mark.anyio
async def test_a_stop_ends_every_drain_before_the_client_closes(tmp_path):
    # Every task that uses llama-swap's client is cancelled before stop() returns, the drains among them (Task 19's
    # shutdown; each stays saved, for the next start).
    gate = Gate(tmp_path, state=loaded(*RESIDENTS, CODER), shown=dict.fromkeys([*RESIDENTS, CODER], "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        Front(core, auto=False)  # its request in flight still runs
        draining = task(core.drainer.drain(CODER, "unload"))
        await settle()
        assert core.drainer.draining(CODER) and not draining.done()
        await core.stop()
        assert draining.done() and draining.result() is False
        assert gate.saved().models[CODER].drain_id is not None  # saved, to resume


@pytest.mark.anyio
async def test_a_loop_whose_step_breaks_runs_it_again(tmp_path, caplog):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = gate.core()
        turns: list[float] = []
        each_second = core.each_second

        def flaky() -> None:
            turns.append(gate.clock())
            if len(turns) == 1:
                raise RuntimeError("a bug of the gate's own")
            each_second()

        flaky.__name__ = "each_second"
        core.each_second = flaky
        await core.start(loops=True)
        for _ in range(500):
            if len(turns) >= 3:
                break
            await asyncio.sleep(0.002)
        assert len(turns) >= 3 and (gate.state_dir / ACTIVITY_FILE).exists()
        assert any("the gate's each_second step broke" in r.getMessage() for r in caplog.records)
        caplog.clear()
        await core.stop()


@pytest.mark.anyio
async def test_each_seconds_duties_run_whichever_breaks(tmp_path, caplog):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        core.drain_all(DAN)

        def broken() -> None:
            raise RuntimeError("a bug of the gate's own")

        core._ingest_brake = broken
        gate.clock.now = T + APPLY_LAPSE_S
        core.each_second()  # the brake's events break; apply's lapse and the activity record still run
        assert core.state.applying.ended is True and gate.activity()["written_at"] == T + APPLY_LAPSE_S
        assert any("the brake's events broke" in r.getMessage() for r in caplog.records)
        caplog.clear()
        await core.stop()


@pytest.mark.anyio
async def test_reads_of_running_go_one_at_a_time(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        before = gate.swap.reads
        gate.swap.pause_next = True
        first = task(core.read_running())
        await settle()
        second = task(core.read_running())  # apply/end's, say, while the loop's is in flight: it waits for that one
        await settle()
        assert gate.swap.reads == before + 1 and not second.done()
        gate.swap.release()
        await first
        await second
        assert gate.swap.reads == before + 1
        await core.stop()


@pytest.mark.anyio
async def test_an_answer_that_comes_out_of_order_is_dropped(tmp_path):
    # Should two reads ever overlap, an older one answering after a newer never puts back what the newer showed gone.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        gate.swap.hold_loads = True
        gate.swap.pause_next = True
        older = task(core._read_once())  # sent at T, listing whisper
        await settle()
        gate.swap.states.pop(WHISPER)
        gate.clock.now = T + 1
        await core._read_once()  # the newer read: whisper gone, the residents' rule asked
        assert WHISPER not in core._listed and core.state.resident_reloads == {WHISPER: T + 1}
        gate.swap.release()
        await older  # the older answer comes last: dropped
        assert WHISPER not in core._listed and core._answered_at == T + 1
        await core.stop()


@pytest.mark.anyio
async def test_the_engines_found_after_damage_are_those_of_the_start_that_found_it(tmp_path):
    # A gate restarted before the front's first snapshot still has fresh_after_damage; an engine started around the
    # gate between the two starts is never recorded as found after damage, and is reported.
    gate = Gate(tmp_path, damaged=True, shown=dict.fromkeys(RESIDENTS, "ready"))
    process(gate.proc, 4242, "llama-server", uid=SPARK, rss_anon_kib=gib(2), argv=("llama-server", "--port", "801"))
    with anyio.fail_after(BOUND_S):
        first = await gate.started()
        assert first.state.ticketed[EMBED].ticket_id == FOUND_AFTER_DAMAGE
        await first.stop()
        process(gate.proc, 4343, "llama-server", uid=SPARK, rss_anon_kib=gib(2),
                argv=("llama-server", "--port", "802"))
        gate.swap.states[CODER] = "ready"
        core = await gate.started()
        assert core.state.fresh_after_damage is True and CODER not in core.state.ticketed
        await core.each_five_s()
        assert core.status_view()["health"]["unticketed_engines"] == [{"model": CODER, "port": 802, "pid": 4343}]
        await core.stop()


@pytest.mark.anyio
async def test_the_status_view_changes_nothing(tmp_path):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    gate.other = 50.0  # under the warn line
    with anyio.fail_after(BOUND_S):
        core = gate.core()
        await core.start(loops=False)
        core._mem = None  # no reading yet
        calls: list[str] = []
        core.admitter.changed = lambda: calls.append("changed")
        warned = len(core.state.notified)
        view = core.status_view()
        assert view["memory"]["available_gib"] == IDLE - 43 - 50
        assert calls == [] and core._mem is None and len(core.state.notified) == warned
        await core.stop()


@pytest.mark.anyio
async def test_a_slow_save_is_said(tmp_path, monkeypatch, caplog):
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        import spark.gate.core as core_module

        monkeypatch.setattr(core_module, "SAVE_SLOW_S", -1.0)  # every save counts as slow
        core.save()
        assert any("saving the gate's state took" in r.getMessage() for r in caplog.records)
        await core.stop()


@pytest.mark.anyio
async def test_apply_restarted_waits_for_a_reload_queued_while_it_checks(tmp_path):
    # A resident's reload queued while apply_restarted checks llama-swap's unit (one that left /running just then)
    # settles before the report, which names it.
    gate = Gate(tmp_path, state=loaded(*RESIDENTS), shown=dict.fromkeys(RESIDENTS, "ready"))
    with anyio.fail_after(BOUND_S):
        core = await gate.started()
        core.drain_all(DAN)
        core.apply_begin(["llama-swap"])
        gate.swap.states.clear()
        gate.units.started("llama-swap", T + 2)
        gate.clock.now = T + 3
        await core.read_running()  # every resident gone, its end to reload them
        gate.units.block = threading.Event()
        core.end_apply("end")
        await until(lambda: len(gate.swap.loads()) == 3)  # each reload settled; the report checks the unit
        late = asyncio.get_running_loop().create_future()
        core._report_apply(core.state.applying, {"late": late})
        gate.units.block.set()
        for _ in range(50):
            await asyncio.sleep(0.002)
        assert gate.published.of("apply_restarted") == []  # one reload still to settle
        late.set_result(Admitted(CODER, True, 1.0))
        assert len(await published(gate, core, "apply_restarted")) == 1
        await core.stop()


def test_engine_ports_follow_llama_swaps_own_order():
    # v257 gives ${PORT} to each model in its names' sorted order, from startPort (internal/config/macros.go:150-186).
    assert engine_ports(REGISTRY) == {GEMMA: 800, EMBED: 801, CODER: 802, WHISPER: 803}
