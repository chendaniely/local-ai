"""The gate's policy (website/design/phase-2a.md, Task 16): the drain before every unload, the idle unload at 60
minutes, pins and sessions. Rule 4 binds it (plan.md, *Admission and memory rules*): no request is cut off, so a drain
waits for the front's `drained` however long its requests take, and the sent-unload rule ends a drain whose unload call
has begun only when `/running` shows the model gone, never with `undrain`.

The clock is injected, so waits of minutes run in milliseconds: a test moves it and calls `changed()`, as the core does
on every memory reading. llama-swap is an in-process stand-in on that clock (Swap): v257 as the gate's AsyncLlamaSwap
sees it, whose unloads each test scripts, changing `/running` as v257's own source does. The front is the gate's side
of its events call (FrontChannel), read and answered as Task 22's front will. `/proc` is a tree each test builds. Every
time, boot id, uid and pid here is a stand-in; none was read from the box."""

import asyncio
import logging
import socket
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio
import pytest

from spark import gateproto, messages, tickets
from spark.gate.admission import Admitted, Admitter, AdmitRequest
from spark.gate.ledger import Ledger
from spark.gate.policy import SAVE_BOUND_S, Drainer, FrontChannel, Pins, Refusal, Sessions, idle_due
from spark.gate.state import GateState, ModelRecord, Pin, Session, load_state, restore, resumable_drains, save_state
from spark.gateproto import (DRAIN_GRACE_S, LOAD_CALL_TIMEOUT_S, PROGRESS_KINDS, SESSION_TTL_S, SESSIONS_PER_UID,
                             STOPPING_EVERY_S, STUCK_STOPPING_S)
from spark.llamaswap import LlamaSwapAnswered, LlamaSwapUnreachable, Running
from spark.llamaswap_async import LlamaSwapNotSent, LoadOutcome
from spark.memory import MemInfo
from spark.registry import load_registry
from spark.render import HF_HOME, model_path
from test_gate_ledger import Rows as LedgerRows

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
REGISTRY = load_registry(STACK_REGISTRY)
TZ = "PDT+7"  # the tests' own zone, seven hours behind UTC, as test_messages.py has it
ZONE = timezone(timedelta(hours=-7))
BOUND_S = 5  # every async test's bound
BOOT = "b1"
LOG = "spark.gate.policy"
DAN, AGENT, SPARK, FRONT = 1000, 1001, 990, 991  # stand-in uids: Dan's, agent's, spark's and spark-front's


def at(hour: int, minute: int, second: float = 0) -> float:
    return datetime(2026, 10, 8, hour, minute, tzinfo=ZONE).timestamp() + second


T = at(9, 0)


def name_of(label: str) -> str:
    return next(name for name, model in REGISTRY.models.items() if model.label == label)


CODER, GEMMA, EMBED, WHISPER = name_of("the coder"), name_of("Gemma"), name_of("the embeddings"), name_of("whisper")


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
    """Every notification the policy sends must be one Task 7's words can build, and nothing in it may fail unseen: the
    drainer logs an emit it couldn't make, or a drain that broke, at ERROR, and carries on, as the gate must."""
    EMITTERS.clear()
    caplog.set_level(logging.WARNING, logger=LOG)
    yield
    assert [emitted.errors for emitted in EMITTERS] == [[] for _ in EMITTERS]
    assert [r.getMessage() for r in caplog.records if r.name == LOG and r.levelno >= logging.ERROR] == []


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

    def text(self, kind: str, fields: dict) -> str:
        return messages.notification(kind, self.registry, **fields).message


class Swap:
    """llama-swap v257 as the gate sees it through AsyncLlamaSwap, in process and on the tests' clock. `/running` lists
    every model that isn't stopped (internal/router/base.go:366-376). An unload goes as the test scripts it
    (`unload_as`), each way v257's source, or the client's own errors, give it:
    - "stops" (the default): a ready model goes stopping, then stopped, and the call is answered OK once it has stopped
      (internal/server/apigroup.go:149-164, internal/process/process_command.go:421-446);
    - "holds": the stop starts, and the call waits for the test: `stop_done` (stopped, then OK) or `times_out` (the
      client's LlamaSwapUnreachable at its 60 s, the model left stopping until `stop_done`, as v257 goes on with it);
    - "not_sent": no connection, so nothing was sent (LlamaSwapNotSent) and nothing changes;
    - "404": v257's 404 (or a 401), which comes before its Unload (apigroup.go:153-160, internal/server/auth.go:31-34):
      LlamaSwapAnswered, and nothing changes;
    - "broke_off": a 200 whose body broke off, which v257 sends only once its Unload has returned (apigroup.go:161-163):
      the model stopped, then LlamaSwapAnswered.
    A load starts the model and runs `spark launch`'s part (the ticket claimed, the start recorded under `started/`),
    then waits for the test's `ready`. `observe(kind, model)` is called as each call arrives."""

    def __init__(self, clock: Clock, launch: Path):
        self.clock, self.launch = clock, launch
        self.states: dict[str, str] = {}
        self.calls: list[tuple[str, str, float]] = []
        self.down = False
        self.observe = None
        self._unload_as: dict[str, str] = {}
        self._held: dict[str, asyncio.Future] = {}
        self._loads: dict[str, asyncio.Future] = {}

    def unloads(self) -> list[str]:
        return [model for kind, model, _ in self.calls if kind == "unload"]

    def loads(self) -> list[str]:
        return [model for kind, model, _ in self.calls if kind == "load"]

    def unload_as(self, model: str, how: str) -> None:
        self._unload_as[model] = how

    async def running(self) -> list[Running]:
        if self.down:
            raise LlamaSwapUnreachable("stand-in: not answering")
        return [Running(model, state) for model, state in sorted(self.states.items()) if state != "stopped"]

    async def unload(self, model: str) -> None:
        self.calls.append(("unload", model, self.clock()))
        if self.observe is not None:
            self.observe("unload", model)
        how = self._unload_as.pop(model, "stops")
        if how == "not_sent":
            raise LlamaSwapNotSent("llama-swap unreachable at the stand-in: ConnectError")
        if how == "404":
            raise LlamaSwapAnswered(f"llama-swap POST /api/models/unload/{model}: HTTP 404")
        if self.states.get(model) in ("ready", "starting"):
            self.states[model] = "stopping"
        if how == "stops":
            self.states[model] = "stopped"
            return
        if how == "broke_off":
            self.states[model] = "stopped"
            raise LlamaSwapAnswered(f"llama-swap POST /api/models/unload/{model}: an answer that broke off (ReadError)")
        held = asyncio.get_running_loop().create_future()
        self._held[model] = held
        if await held == "timeout":
            raise LlamaSwapUnreachable("llama-swap unreachable at the stand-in: no answer within 60 s")

    def times_out(self, model: str) -> None:
        self._held.pop(model).set_result("timeout")

    def stop_done(self, model: str) -> None:
        self.states[model] = "stopped"
        held = self._held.pop(model, None)
        if held is not None:
            held.set_result("ok")

    async def load(self, model: str) -> LoadOutcome:
        self.calls.append(("load", model, self.clock()))
        if self.observe is not None:
            self.observe("load", model)
        self.states[model] = "starting"
        claim = tickets.claim(self.launch, model, self.clock(), boot_id=BOOT)
        assert claim.ok, claim
        tickets.mark_started(self.launch, claim.ticket, self.clock())
        call = asyncio.get_running_loop().create_future()
        self._loads[model] = call
        return await call

    def ready(self, model: str) -> None:
        self.states[model] = "ready"
        self._loads.pop(model).set_result(LoadOutcome.READY)

    def exits(self, model: str) -> None:
        self.states[model] = "stopped"
        self._loads.pop(model).set_result(LoadOutcome.failed(500, "upstream command exited prematurely"))

    async def last_lines(self, model: str, n: int = 20, read_s: float = 1.0) -> list[str]:
        return []


class Front:
    """The front's side of its events call, as Task 22's front speaks it: what the gate sent, and its answers."""

    def __init__(self, channel: FrontChannel):
        self.channel = channel
        self.seen: list[dict] = []
        self.queue = channel.open()

    def events(self) -> list[dict]:
        while not self.queue.empty():
            self.seen.append(self.queue.get_nowait())
        return self.seen

    def ops(self) -> list[tuple[str, str]]:
        return [(event["op"], event["model"]) for event in self.events()]

    def drain_id(self, model: str) -> str:
        """The id of the last `drain` the front was sent for `model`."""
        return [e for e in self.events() if e["op"] == "drain" and e["model"] == model][-1]["drain_id"]

    def drop(self) -> None:
        """The front's events call drops (the front restarted, or its call broke)."""
        self.channel.close(self.queue)

    def reconnect(self) -> None:
        self.events()
        self.queue = self.channel.open()


def record(name: str, *, state: str = "ready", last_use: float = T - 60, **more) -> ModelRecord:
    footprint = REGISTRY.models[name].footprint_gib if name in REGISTRY.models else 8.0
    return ModelRecord(name=name, footprint_gib=footprint, loaded_at=T - 3600, load_fall_gib=footprint,
                       fall_flagged=False, rss_anon_at_load_gib=None, last_use=last_use, state=state, **more)


def snapshot(**inflight: dict) -> dict:
    """The front's whole in-flight report: each model given its counts, nothing in flight elsewhere."""
    models = {name: {"count": 0, "oldest_started_at": None, "last_end_at": None, "requests": []} | counts
              for name, counts in inflight.items()}
    return {"seq": 1, "front_started_at": T - 86400, "models": models, "draining": []}


def never_live(session: Session) -> bool:
    return False


async def settle() -> None:
    """Let every task that can move, move: the drainer's, the admitter's and the stand-in's."""
    for _ in range(200):
        await asyncio.sleep(0)


class World:
    """The drainer with everything around it the test builds: the clock, launch's folder, a Hugging Face cache with
    every model's files, the state, the stand-in llama-swap (each record's model shown as its state), the admitter,
    the front's channel (open, unless `connected` is false, its report nothing in flight), the saves (`saves`: each
    model's record as saved), and the gate's ledger, the admitter's, which the drainer takes from it, each row kept in
    `ledger_rows` (Task 44e). `live` is the drainer's, Sessions.live's stand-in."""

    def __init__(self, tmp_path: Path, *, models: dict[str, ModelRecord] | None = None, available: float = 117.0,
                 state: GateState | None = None, connected: bool = True, on_change=None,
                 running: list[Running] | None = None, live=None):
        self.clock = Clock(T)
        self.launch = tmp_path / "launch"
        self.launch.mkdir(parents=True, exist_ok=True)
        self.hf_home = tmp_path / "hf"
        for model in REGISTRY.models.values():
            for file in filter(None, (model.source.file, model.source.mmproj)):
                path = self.hf_home / Path(model_path(model.source, file)).relative_to(HF_HOME)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"gguf")
        self.state = state or GateState(boot_id=BOOT)
        self.state.models.update(models or {})
        self.available = available
        self.emitted = Emitted()
        self.swap = Swap(self.clock, self.launch)
        shown = {"draining": "ready"}
        self.swap.states.update({name: shown.get(r.state, r.state) for name, r in self.state.models.items()})
        self.saves: list[dict[str, ModelRecord]] = []
        self.ledger_file = LedgerRows()
        self.ledger_rows = self.ledger_file.rows
        self.admitter = Admitter(REGISTRY, self.state, self.swap, self.emitted, self.clock,
                                 read_mem=lambda: MemInfo(121.6, self.available), read_hold=lambda: None,
                                 launch=self.launch, hf_home=str(self.hf_home), holders=lambda names: [],
                                 on_change=self.saved, poll_s=60.0, **({} if running is None else {"running": running}),
                                 ledger=Ledger(self.ledger_file, self.clock, lambda: frozenset(REGISTRY.models)))
        self.channel = FrontChannel()
        self.channel.report(snapshot())
        self.front = Front(self.channel) if connected else None
        self.drainer = Drainer(self.channel, self.swap, self.admitter, self.emitted, self.clock,
                               on_change=on_change or self.saved, poll_s=60.0, live=live)

    def saved(self) -> None:
        self.saves.append({name: replace(r) for name, r in self.state.models.items()})

    async def at(self, when: float) -> None:
        """The clock at `when`, then a change, as the core reports every memory reading."""
        self.clock.now = when
        self.drainer.changed()
        self.admitter.changed()
        await settle()

    def drain(self, model: str, why: str, **more) -> asyncio.Task:
        return asyncio.get_running_loop().create_task(self.drainer.drain(model, why, **more))


def unloaded_text(world: World) -> list[str]:
    return [world.emitted.text("unloaded", fields) for _, fields in world.emitted.of("unloaded")]


# The idle unload (rule 3): on-demand models only, 60 minutes with no request, no live session and no pin.

def test_an_on_demand_model_idle_unloads_after_60_minutes():
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, last_use=T), GEMMA: record(GEMMA, last_use=T)})
    assert REGISTRY.gate.idle_unload_min == 60
    assert idle_due(T + 59 * 60, state, REGISTRY, snapshot(), never_live) == []
    assert idle_due(T + 60 * 60, state, REGISTRY, snapshot(), never_live) == [CODER]


def test_a_resident_never_idle_unloads():
    state = GateState(boot_id=BOOT, models={GEMMA: record(GEMMA, last_use=T - 86400)})
    assert REGISTRY.models[GEMMA].resident
    assert idle_due(T, state, REGISTRY, snapshot(), never_live) == []


def test_requests_in_flight_count_as_use():
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, last_use=T - 7200)})
    assert idle_due(T, state, REGISTRY, snapshot(**{CODER: {"count": 1, "oldest_started_at": T - 7200}}),
                    never_live) == []
    # A request that ended 10 minutes ago, by the front's count, is use too, whatever the gate's record says.
    assert idle_due(T, state, REGISTRY, snapshot(**{CODER: {"last_end_at": T - 600}}), never_live) == []
    assert idle_due(T, state, REGISTRY, snapshot(), never_live) == [CODER]


def test_with_no_report_from_the_front_nothing_is_idle():
    # The front reports what is in flight; without its report the gate can't know a model is idle.
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, last_use=T - 7200)})
    assert idle_due(T, state, REGISTRY, None, never_live) == []


def test_a_model_being_drained_or_held_drained_by_the_front_isnt_named_again():
    for going in (record(CODER, last_use=T - 7200, state="draining", drain_id="d1", drain_why="unload"),
                  record(CODER, last_use=T - 7200, state="stopping", unload_requested_at=T - 5),
                  record(CODER, last_use=T - 7200, state="starting")):
        assert idle_due(T, GateState(boot_id=BOOT, models={CODER: going}), REGISTRY, snapshot(), never_live) == []
    held = snapshot() | {"draining": [CODER]}
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, last_use=T - 7200)})
    assert idle_due(T, state, REGISTRY, held, never_live) == []


def a_process(proc: Path, pid: int, start: int, uid: int = AGENT, state: str = "S (sleeping)") -> None:
    """A stand-in /proc/<pid>, its status and stat as the kernel writes them, as far as liveness and owner read them."""
    folder = proc / str(pid)
    folder.mkdir(parents=True)
    uids = "\t".join([str(uid)] * 4)
    (folder / "status").write_text(f"Name:\tpi\nState:\t{state}\nPid:\t{pid}\nUid:\t{uids}\n")
    rest = [state[0], "1", str(pid), str(pid), "0", "-1", "4194560"] + ["0"] * 12 + [str(start)] + ["0"] * 30
    (folder / "stat").write_text(f"{pid} (pi) " + " ".join(rest) + "\n")


def gone(proc: Path, pid: int) -> None:
    for name in ("status", "stat"):
        (proc / str(pid) / name).unlink()
    (proc / str(pid)).rmdir()


def sessions_on(tmp_path: Path, state: GateState, clock: Clock) -> Sessions:
    return Sessions(state, clock, proc=tmp_path / "proc", may_hold=lambda uid: uid not in (SPARK, FRONT),
                    is_model=lambda name: name in REGISTRY.models)


def test_a_live_session_keeps_its_model(tmp_path):
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, last_use=T - 7200)})
    clock = Clock(T)
    a_process(tmp_path / "proc", 4242, 777_000)
    sessions = sessions_on(tmp_path, state, clock)
    session = sessions.register(AGENT, 4242, CODER, "agent's pi")
    assert isinstance(session, Session) and session.boot_id == BOOT and session.start_time == 777_000
    assert idle_due(T, state, REGISTRY, snapshot(), sessions.live) == []
    # A session on another model keeps nothing of the coder's.
    state.sessions[session.id].model = GEMMA
    assert idle_due(T, state, REGISTRY, snapshot(), sessions.live) == [CODER]


# Sessions: each uid's own, tied to one of its live processes, 8 at most, ending with its process or 12 h after its
# last renewal.

def test_a_session_ends_with_its_process(tmp_path):
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, last_use=T - 7200)})
    clock = Clock(T)
    proc = tmp_path / "proc"
    a_process(proc, 4242, 777_000)
    sessions = sessions_on(tmp_path, state, clock)
    session = sessions.register(AGENT, 4242, CODER, "agent's pi")
    assert sessions.prune(T + 60) == [] and session.id in state.sessions
    gone(proc, 4242)
    clock.now = T + 120
    assert sessions.prune(T + 120) == [session] and state.sessions == {}
    # Its end counts as the coder's last use: it unloads 60 minutes after that, not at once.
    assert state.models[CODER].last_use == T + 120
    assert idle_due(T + 120 + 59 * 60, state, REGISTRY, snapshot(), sessions.live) == []
    # The pid handed out again within the boot, to another process of agent's, keeps nothing either.
    a_process(proc, 4243, 888_000)
    again = sessions.register(AGENT, 4243, CODER, "agent's pi")
    gone(proc, 4243)
    a_process(proc, 4243, 999_000)
    assert sessions.prune(T + 180) == [again]


def test_a_session_expires_12_hours_after_its_last_heartbeat(tmp_path):
    state = GateState(boot_id=BOOT)
    clock = Clock(T - 3600)
    a_process(tmp_path / "proc", 4242, 777_000)
    sessions = sessions_on(tmp_path, state, clock)
    session = sessions.register(AGENT, 4242, CODER, "agent's pi")
    clock.now = T
    assert sessions.renew(session.id, AGENT) is session and session.renewed_at == T
    assert SESSION_TTL_S == 12 * 3600
    clock.now = T + SESSION_TTL_S - 1
    assert sessions.live(session) and sessions.prune(clock.now) == []
    clock.now = T + SESSION_TTL_S + 1
    assert not sessions.live(session)
    assert sessions.prune(clock.now) == [session] and state.sessions == {}


def test_a_uid_holds_8_sessions_at_most(tmp_path):
    state = GateState(boot_id=BOOT)
    proc = tmp_path / "proc"
    for pid in range(4200, 4210):
        a_process(proc, pid, 700_000 + pid)
    a_process(proc, 5000, 800_000, uid=DAN)
    sessions = sessions_on(tmp_path, state, Clock(T))
    held = [sessions.register(AGENT, 4200 + n, CODER, f"pi {n}") for n in range(SESSIONS_PER_UID)]
    assert all(isinstance(s, Session) for s in held) and SESSIONS_PER_UID == 8
    ninth = sessions.register(AGENT, 4208, CODER, "pi 8")
    assert isinstance(ninth, Refusal) and ninth.status == 409
    assert "8 sessions" in ninth.message and ninth.body() == {"message": ninth.message, "code": None}
    assert len(state.sessions) == SESSIONS_PER_UID
    assert isinstance(sessions.register(DAN, 5000, CODER, "Dan's pi"), Session)  # another uid's count is its own
    assert sessions.end(held[0].id, AGENT, admin=False) is held[0]
    assert isinstance(sessions.register(AGENT, 4208, CODER, "pi 8"), Session)


def test_a_session_must_name_a_live_process_of_its_caller(tmp_path):
    state = GateState(boot_id=BOOT)
    proc = tmp_path / "proc"
    a_process(proc, 5000, 800_000, uid=DAN)
    a_process(proc, 4243, 800_001, state="Z (zombie)")
    sessions = sessions_on(tmp_path, state, Clock(T))
    others = sessions.register(AGENT, 5000, CODER, "agent's pi")
    assert isinstance(others, Refusal) and others.status == 403
    assert others.message == "Process 5000 isn't one of yours, so it can't hold your session."
    for pid in (4242, 4243, 0, -1):  # gone, exited, and no process at all
        refused = sessions.register(AGENT, pid, CODER, "agent's pi")
        assert isinstance(refused, Refusal) and refused.status == 409
        assert refused.message == f"Process {pid} isn't running, so it can't hold a session."
    assert state.sessions == {}


def test_only_the_owner_or_spark_admin_ends_a_session(tmp_path):
    state = GateState(boot_id=BOOT)
    proc = tmp_path / "proc"
    for pid in (4242, 4243, 4244):
        a_process(proc, pid, 700_000 + pid)
    sessions = sessions_on(tmp_path, state, Clock(T))
    own, others, dans = (sessions.register(AGENT, pid, CODER, "agent's pi") for pid in (4242, 4243, 4244))
    assert sessions.end(own.id, AGENT, admin=False) is own and own.id not in state.sessions
    refused = sessions.end(others.id, DAN + 7, admin=False)
    assert isinstance(refused, Refusal) and refused.status == 403 and others.id in state.sessions
    assert refused.message == f"Only the account that began session {others.id}, or Dan, can end it."
    assert sessions.end(dans.id, DAN, admin=True) is dans and dans.id not in state.sessions
    # Only its owner renews it, and a session that has ended is said to be gone.
    refused = sessions.renew(others.id, DAN + 7)
    assert isinstance(refused, Refusal) and refused.status == 403
    missing = sessions.renew(own.id, AGENT)
    assert isinstance(missing, Refusal) and missing.status == 404
    assert missing.message == f"There's no session {own.id}: it has ended, or it never began."


def test_spark_and_spark_front_may_not_hold_sessions(tmp_path):
    state = GateState(boot_id=BOOT)
    proc = tmp_path / "proc"
    a_process(proc, 4242, 777_000, uid=SPARK)
    a_process(proc, 4243, 777_001, uid=FRONT)
    sessions = sessions_on(tmp_path, state, Clock(T))
    for uid, pid in ((SPARK, 4242), (FRONT, 4243)):
        refused = sessions.register(uid, pid, CODER, "an engine")
        assert isinstance(refused, Refusal) and refused.status == 403
        assert refused.message == ("This account runs the stack itself, so it can't hold a session: a session keeps a "
                                   "model loaded for an agent's own process.")
    assert state.sessions == {}


def test_a_sessions_label_is_one_line_and_short(tmp_path):
    # It reaches Dan's terminal (`spark status`, make-room's list), so it carries no control character and no screenful.
    state = GateState(boot_id=BOOT)
    a_process(tmp_path / "proc", 4242, 777_000)
    sessions = sessions_on(tmp_path, state, Clock(T))
    session = sessions.register(AGENT, 4242, CODER, "agent's\x1b[2J pi\n" + "x" * 500)
    assert session.label.startswith("agent's [2J pi x") and len(session.label) <= 80 and session.label.isprintable()


def test_a_sessions_model_is_a_registry_models_name(tmp_path):
    # The controller's ruling at Task 16's review: a name the registry has, or a plain refusal, never stored.
    state = GateState(boot_id=BOOT)
    a_process(tmp_path / "proc", 4242, 777_000)
    sessions = sessions_on(tmp_path, state, Clock(T))
    for model in ("no-such-model", "coder", "x" * 500 + "\x1b[2J"):  # an unknown name; a role, not a name; junk
        refused = sessions.register(AGENT, 4242, model, "agent's pi")
        assert isinstance(refused, Refusal) and refused.status == 409
        assert refused.message.startswith("There's no model ") and refused.message.endswith(
            " in the registry, so no session can keep it loaded.")
        assert refused.message.isprintable() and len(refused.message) < 200
    assert state.sessions == {}
    assert sessions.register(AGENT, 4242, CODER, "agent's pi").model == CODER


# Pins: Dan's alone; a model not loaded is loaded first, through admission as one of his commands.

async def collect(lines) -> list[dict]:
    return [line async for line in lines]


def pins_in(world: World) -> Pins:
    return Pins(world.state, world.admitter, world.emitted, world.clock, may_pin=lambda uid: uid in (DAN, 0))


@pytest.mark.anyio
async def test_a_pin_keeps_its_model_until_its_time_then_pin_ended(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER, last_use=T - 60)})
        pins = pins_in(world)
        [confirmed] = await collect(pins.pin(CODER, T + 8 * 3600, DAN))
        assert confirmed == {"label": "the coder", "until": T + 8 * 3600, "loaded_s": None, "command": "coder"}
        assert world.state.pins[CODER] == Pin(model=CODER, until=T + 8 * 3600, by_uid=DAN)
        assert idle_due(T + 2 * 3600, world.state, REGISTRY, snapshot(), never_live) == []
        assert pins.expire(T + 8 * 3600 - 1) == [] and CODER in world.state.pins
        [ended] = pins.expire(T + 8 * 3600)
        assert ended.model == CODER and world.state.pins == {}
        [(key, fields)] = world.emitted.of("pin_ended")
        assert world.emitted.text("pin_ended", fields) == (
            "The pin on the coder ended at 17:00; it unloads after 60 min idle.")
        assert key == f"pin:{CODER}:{T + 8 * 3600:.3f}"
        # And it does: 60 minutes after the pin's end, not at once.
        assert idle_due(T + 8 * 3600 + 59 * 60, world.state, REGISTRY, snapshot(), never_live) == []
        assert idle_due(T + 9 * 3600, world.state, REGISTRY, snapshot(), never_live) == [CODER]


@pytest.mark.anyio
async def test_pins_are_dans_only(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path)
        pins = pins_in(world)
        assert await collect(pins.pin(CODER, None, AGENT)) == [
            {"message": "Only Dan can pin a model, with `spark pin` on the Spark.", "code": None}]
        assert world.state.pins == {} and world.swap.loads() == []
        refused = pins.unpin(CODER, AGENT)
        assert isinstance(refused, Refusal) and refused.status == 403


@pytest.mark.anyio
async def test_pinning_a_model_that_isnt_loaded_loads_it_first(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path)
        asked: list[AdmitRequest] = []
        admit = world.admitter.admit

        async def spy(req, **more):
            asked.append(req)
            return await admit(req, **more)

        world.admitter.admit = spy
        pins = pins_in(world)
        lines = asyncio.get_running_loop().create_task(collect(pins.pin(CODER, None, DAN)))
        await settle()
        [req] = asked
        assert (req.model, req.kind, req.key, req.deadline, req.uid) == (CODER, "command", None,
                                                                         T + LOAD_CALL_TIMEOUT_S, DAN)
        assert world.swap.loads() == [CODER] and world.state.pins == {}  # the pin is set once the load is in
        world.clock.now = T + 24
        world.swap.ready(CODER)
        await settle()
        assert lines.result()[-1] == {"label": "the coder", "until": None, "loaded_s": 24.0, "command": "coder"}
        assert world.state.pins[CODER] == Pin(model=CODER, until=None, by_uid=DAN)


@pytest.mark.anyio
async def test_a_pin_that_loads_yields_its_start_first(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The coder not loaded: its LoadProgress as the load starts, then the confirmation with loaded_s.
        world = World(tmp_path)
        pins = pins_in(world)
        lines = asyncio.get_running_loop().create_task(collect(pins.pin(CODER, T + 3600, DAN)))
        await settle()
        world.clock.now = T + 24
        world.swap.ready(CODER)
        await settle()
        assert lines.result() == [
            {"kind": "loading", "model": CODER, "label": "the coder", "last_s": None},
            {"label": "the coder", "until": T + 3600, "loaded_s": 24.0, "command": "coder"}]
        # Loaded: the confirmation alone, loaded_s None.
        assert await collect(pins.pin(CODER, T + 7200, DAN)) == [
            {"label": "the coder", "until": T + 7200, "loaded_s": None, "command": "coder"}]


@pytest.mark.anyio
async def test_a_pin_whose_load_waits_says_why_before_its_start(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The coder's load waiting for Gemma's: a WaitProgress, why slot, loading_label Gemma, first.
        world = World(tmp_path)
        gemma = world.admitter.reload(GEMMA, "boot")
        await settle()
        pins = pins_in(world)
        lines = asyncio.get_running_loop().create_task(collect(pins.pin(CODER, None, DAN)))
        await settle()
        world.swap.ready(GEMMA)
        await settle()
        assert isinstance(gemma.result(), Admitted)
        world.clock.now = T + 24
        world.swap.ready(CODER)
        await settle()
        first, start, result = lines.result()
        assert first == dict.fromkeys(gateproto.WaitProgress.__annotations__) | {
            "kind": "waiting", "model": CODER, "label": "the coder", "why": "slot", "loading_label": "Gemma"}
        assert start["kind"] == "loading" and result["loaded_s"] == 24.0
        assert {line.get("kind") for line in (first, start)} <= set(PROGRESS_KINDS) and "kind" not in result


@pytest.mark.anyio
async def test_a_pin_whose_load_is_refused_says_so_last_and_pins_nothing(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, available=40)  # 40 less the 24 GiB reserve: 16 free for a load, and the coder is 41
        pins = pins_in(world)
        lines = asyncio.get_running_loop().create_task(collect(pins.pin(CODER, None, DAN)))
        await settle()
        await world.at(T + LOAD_CALL_TIMEOUT_S)
        *_, last = lines.result()
        assert last["code"] == "no_fit" and last["message"].startswith("The coder didn't load: it needs 41 GiB")
        assert set(last) == set(gateproto.GateRefusalBody.__annotations__) and world.state.pins == {}


@pytest.mark.anyio
async def test_unpin_ends_the_pin_and_says_when_it_unloads(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER, last_use=T - 7200)})
        world.state.pins[CODER] = Pin(model=CODER, until=None, by_uid=DAN)
        pins = pins_in(world)
        assert pins.unpin(CODER, DAN) == {"label": "the coder", "idle_min": 60, "command": "coder"}
        assert world.state.pins == {} and world.state.models[CODER].last_use == T
        assert messages.unpinned("the coder", 60) == "The pin on the coder ended; it unloads after 60 min idle."
        refused = pins.unpin(CODER, DAN)
        assert isinstance(refused, Refusal) and refused.message == "The coder isn't pinned, so there is no pin to end."


# The drain (rule 4): it waits for the front's `drained` however long, then unloads; the sent-unload rule ends it.

@pytest.mark.anyio
async def test_a_drain_waits_for_requests_in_flight_however_long(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER)})
        drain = world.drain(CODER, "make-room")
        await settle()
        drain_id = world.front.drain_id(CODER)
        assert world.front.events() == [{"op": "drain", "model": CODER, "drain_id": drain_id, "why": "make-room"}]
        assert world.state.models[CODER].state == "draining"
        await world.at(T + 3600)  # a request in flight for an hour
        assert world.swap.unloads() == [] and not drain.done()
        assert world.channel.drained(CODER, drain_id)
        await settle()
        assert world.swap.unloads() == [CODER] and drain.result() is True
        assert world.front.ops() == [("drain", CODER), ("unloaded", CODER)]
        assert CODER not in world.state.models
        assert unloaded_text(world) == ["Unloaded the coder for make-room."]
        assert world.emitted.of("unloaded")[0][0] == f"drain:{drain_id}"


@pytest.mark.anyio
async def test_the_unload_comes_only_after_drained(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER)})
        drain = world.drain(CODER, "unload")
        await settle()
        drain_id = world.front.drain_id(CODER)
        await world.at(T + 600)
        assert not world.channel.drained(CODER, "another-drain")  # an answer for a drain that isn't this one
        assert not world.channel.drained(GEMMA, drain_id)
        await world.at(T + 1200)
        assert not world.channel.busy(CODER, drain_id)  # only an idle drain takes busy: this one keeps waiting
        await world.at(T + 1800)
        assert world.swap.unloads() == [] and not drain.done() and world.state.models[CODER].state == "draining"
        assert world.channel.drained(CODER, drain_id)
        await settle()
        assert world.swap.unloads() == [CODER] and drain.result() is True


@pytest.mark.anyio
async def test_a_drain_whose_unload_is_never_sent_goes_back_to_serving_at_90s(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The gate's own step before the call, saving the unload as requested, held up: the call never begins.
        held = asyncio.Event()

        def on_change():
            coder = world.state.models.get(CODER)
            if coder is not None and coder.state == "draining" and coder.unload_requested_at is not None:
                return held.wait()
            return None

        world = World(tmp_path, models={CODER: record(CODER)}, on_change=on_change)
        ends: list[str] = []
        drain = world.drain(CODER, "make-room", ended=ends.append)
        await settle()
        drain_id = world.front.drain_id(CODER)
        world.channel.drained(CODER, drain_id)
        await settle()
        assert world.state.models[CODER].unload_requested_at == T  # the step under way: the unload saved as requested
        await world.at(T + DRAIN_GRACE_S - 1)
        assert not drain.done() and world.front.ops() == [("drain", CODER)]
        await world.at(T + DRAIN_GRACE_S)
        assert drain.result() is False and world.swap.unloads() == [] and ends == ["held"]
        assert world.front.events()[-1] == {"op": "undrain", "model": CODER, "drain_id": drain_id}
        coder = world.state.models[CODER]
        assert (coder.state, coder.unload_requested_at, coder.drain_id, coder.drain_why) == ("ready", None, None, None)
        assert DRAIN_GRACE_S == 90 and world.emitted.of("unloaded") == []


@pytest.mark.anyio
@pytest.mark.parametrize("how", ["not_sent", "404"])
async def test_an_unload_llama_swap_never_took_goes_back_to_serving_at_once(tmp_path, how):
    with anyio.fail_after(BOUND_S):
        # Refused at connect (LlamaSwapNotSent), or answered 404 with /running still listing the model.
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, how)
        drain = world.drain(CODER, "unload")
        await settle()
        drain_id = world.front.drain_id(CODER)
        world.channel.drained(CODER, drain_id)
        await settle()
        assert world.swap.unloads() == [CODER] and drain.result() is False
        assert world.front.events()[-1] == {"op": "undrain", "model": CODER, "drain_id": drain_id}
        coder = world.state.models[CODER]
        assert (coder.state, coder.unload_requested_at, coder.drain_id, coder.drain_why) == ("ready", None, None, None)
        assert world.swap.states[CODER] == "ready" and world.emitted.of("unloaded") == []


@pytest.mark.anyio
async def test_an_answered_unload_is_settled_by_running(tmp_path):
    with anyio.fail_after(BOUND_S):
        # A 200 that broke off: v257 sent it once its Unload had returned, and /running no longer lists the model.
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, "broke_off")
        drain = world.drain(CODER, "unload")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is True and world.front.ops()[-1] == ("unloaded", CODER)
        assert CODER not in world.state.models
        assert unloaded_text(world) == ["Unloaded the coder, as `spark unload` asked."]
        call_at = world.swap.calls[-1][2]
        assert world.drainer.own_unload(CODER, since=call_at)  # the residents' rule never reloads it
        assert not world.drainer.own_unload(GEMMA, since=call_at)


@pytest.mark.anyio
async def test_the_residents_rule_hears_why_the_gate_unloaded_a_model(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The controller's ruling at Task 17: the residents' rule reloads a resident after an `unknown` or `late_start`
        # unload, never after Dan's `spark unload` or make-room's, so it asks the drainer why, not only whether.
        world = World(tmp_path, models={CODER: record(CODER), GEMMA: record(GEMMA)})
        assert world.drainer.unloaded_why(CODER, since=T - 1) is None
        drain = world.drain(CODER, "unload")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is True and CODER not in world.state.models
        assert world.drainer.unloaded_why(CODER, since=T - 1) == "unload"
        assert world.drainer.unloaded_why(CODER, since=T + 1) is None  # one before `since` isn't this leaving
        assert world.drainer.unloaded_why(GEMMA, since=T - 1) is None and not world.drainer.own_unload(GEMMA, T - 1)
        # Task 15's own abort of a start past its deadline is the gate's own, though no drain made it.
        world.admitter.late_unloads[GEMMA] = T
        assert world.drainer.unloaded_why(GEMMA, since=T - 1) == "late_start"
        assert world.drainer.own_unload(GEMMA, since=T - 1) and not world.drainer.own_unload(GEMMA, since=T + 1)
        # A resumed one, the drainer's: late_start too; and one under way, its record's why.
        late = late_start_world(tmp_path / "late", "starting")
        assert late.drainer.unloaded_why(CODER, since=T - 1) == "late_start"  # its unload requested, saved
        await settle()
        resumed = resume_saved(late)
        await late.at(T + 5)
        assert resumed.result() is True and CODER not in late.state.models
        assert late.drainer.unloaded_why(CODER, since=T) == "late_start"


@pytest.mark.anyio
async def test_an_answered_unload_with_the_model_stopping_waits_for_gone(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Never undrain a model whose unload may be in flight: /running showing it stopping after an answered error
        # keeps the drain until it is gone.
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, "404")
        world.swap.observe = lambda kind, model: world.swap.states.update({CODER: "stopping"})
        drain = world.drain(CODER, "unload")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await world.at(T + 30)
        assert not drain.done() and [op for op, _ in world.front.ops()] == ["drain"]
        world.swap.stop_done(CODER)
        await world.at(T + 31)
        assert drain.result() is True and world.front.ops()[-1] == ("unloaded", CODER)


@pytest.mark.anyio
async def test_a_model_stopping_for_5_min_sends_one_alert(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, "holds")
        states = []
        world.swap.observe = lambda kind, model: states.append(world.state.models[CODER].state)
        drain = world.drain(CODER, "unload")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert states == ["stopping"]  # from the call's start, not from its timeout
        await world.at(T + 60)
        world.swap.times_out(CODER)
        await world.at(T + STUCK_STOPPING_S - 1)
        assert world.emitted.of("llama_swap_down") == [] and world.state.models[CODER].state == "stopping"
        await world.at(T + STUCK_STOPPING_S)
        [(key, fields)] = world.emitted.of("llama_swap_down")
        assert key == f"stuck:{CODER}:{T:.3f}" and fields["why"] == "stuck_stopping" and fields["since"] == T
        assert world.emitted.text("llama_swap_down", fields) == (
            "llama-swap has been stopping the coder for 5 min; its engine may be stuck. On the Spark, "
            "`make logs s=llama-swap` shows why.")
        assert messages.notification("llama_swap_down", REGISTRY, **fields).priority == "high"
        await world.at(T + 360)
        assert len(world.emitted.of("llama_swap_down")) == 1 and not drain.done()
        world.swap.stop_done(CODER)
        await world.at(T + 361)
        assert drain.result() is True and len(world.emitted.of("llama_swap_down")) == 1


@pytest.mark.anyio
async def test_an_unload_that_times_out_waits_for_gone_and_never_undrains(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, "holds")
        drain = world.drain(CODER, "make-room")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        world.swap.times_out(CODER)
        for second in range(10, 101, 10):  # /running shows it stopping for 100 s
            await world.at(T + second)
            assert world.swap.states[CODER] == "stopping" and not drain.done()
        assert [op for op, _ in world.front.ops()] == ["drain"] and world.state.models[CODER].state == "stopping"
        world.swap.stop_done(CODER)
        await world.at(T + 101)
        assert drain.result() is True and world.front.ops() == [("drain", CODER), ("unloaded", CODER)]
        assert CODER not in world.state.models


@pytest.mark.anyio
async def test_a_channel_drop_after_the_unload_was_sent_ends_at_gone(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, "holds")
        drain = world.drain(CODER, "unload")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        call_at = world.swap.calls[-1][2]
        world.front.drop()
        await world.at(T + 30)
        world.swap.times_out(CODER)  # and the call comes to nothing: the stop goes on in llama-swap
        await world.at(T + 60)
        assert not drain.done() and world.state.models[CODER].state == "stopping"
        world.swap.stop_done(CODER)
        await world.at(T + 61)
        assert drain.result() is True and world.drainer.own_unload(CODER, since=call_at)
        assert unloaded_text(world) == ["Unloaded the coder, as `spark unload` asked."]
        assert [op for op, _ in world.front.ops()] == ["drain"]  # no undrain, ever
        # The front, back again, still hears `unloaded`, which releases a hold it kept.
        world.front.reconnect()
        await world.at(T + 62)
        assert world.front.events()[-1] == {"op": "unloaded", "model": CODER} and world.state.undrains == {}


@pytest.mark.anyio
async def test_every_drain_goes_back_if_the_fronts_channel_drops(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Each before its unload is sent: two waiting for `drained`, and one drained whose step before the call is
        # held up.
        held = asyncio.Event()

        def on_change():
            whisper = world.state.models.get(WHISPER)
            if whisper is not None and whisper.state == "draining" and whisper.unload_requested_at is not None:
                return held.wait()
            return None

        world = World(tmp_path, models={CODER: record(CODER), GEMMA: record(GEMMA), WHISPER: record(WHISPER)},
                      on_change=on_change)
        ends: list[str] = []
        drains = [world.drain(CODER, "make-room"), world.drain(GEMMA, "make-room"),
                  world.drain(WHISPER, "unload", ended=ends.append)]
        await settle()
        ids = {model: world.front.drain_id(model) for model in (CODER, GEMMA, WHISPER)}
        world.channel.drained(WHISPER, ids[WHISPER])
        await settle()
        world.front.drop()
        await settle()
        assert [d.result() for d in drains] == [False, False, False] and world.swap.unloads() == []
        assert ends == ["front_dropped"]  # dropped in its step before the call, not held past the grace
        assert {name: (r.state, r.drain_id, r.unload_requested_at) for name, r in world.state.models.items()} == {
            name: ("ready", None, None) for name in (CODER, GEMMA, WHISPER)}
        # The front may hold any of them drained: each is owed its undrain, saved, and sent once the front is back.
        assert world.state.undrains == ids
        world.front.reconnect()
        await world.at(T + 1)
        undrains = sorted((e for e in world.front.events() if e["op"] == "undrain"), key=lambda e: e["model"])
        assert undrains == [{"op": "undrain", "model": model, "drain_id": ids[model]} for model in sorted(ids)]
        assert world.state.undrains == {}


@pytest.mark.anyio
async def test_an_idle_drain_that_finds_a_request_unloads_nothing(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER, last_use=T - 7200)})
        world.channel.report(snapshot())
        assert idle_due(T, world.state, REGISTRY, world.channel.snapshot, never_live) == [CODER]
        drain = world.drain(CODER, "idle")
        await settle()
        assert world.front.events()[-1]["why"] == "idle"
        world.clock.now = T + 2
        assert world.channel.busy(CODER, world.front.drain_id(CODER))  # a request landed before the front marked it
        await settle()
        assert drain.result() is False and world.swap.unloads() == []
        coder = world.state.models[CODER]
        assert (coder.state, coder.last_use, coder.drain_id, coder.drain_why) == ("ready", T + 2, None, None)
        assert world.front.ops() == [("drain", CODER)] and world.state.undrains == {}  # the front never marked it
        assert world.emitted.of("unloaded") == []


@pytest.mark.anyio
async def test_an_idle_unload_says_after_60_minutes(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER, last_use=T - 7200)})
        drain = world.drain(CODER, "idle")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is True and unloaded_text(world) == ["Unloaded the coder after 60 min idle."]


@pytest.mark.anyio
async def test_the_drains_id_and_why_are_saved_before_drain_is_sent(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER)})
        at_send, at_call = [], []
        send = world.channel.send
        world.channel.send = lambda event: at_send.append((event, world.saves[-1][CODER])) or send(event)
        world.swap.observe = lambda kind, model: at_call.append(list(world.saves))
        world.swap.unload_as(CODER, "holds")
        world.drain(CODER, "make-room")
        await settle()
        [(event, saved)] = at_send
        assert (saved.drain_id, saved.drain_why, saved.unload_requested_at, saved.state) == (
            event["drain_id"], "make-room", None, "draining")
        world.clock.now = T + 5
        world.channel.drained(CODER, event["drain_id"])
        await settle()
        [saves] = at_call
        coder = [(s[CODER].state, s[CODER].unload_requested_at, s[CODER].drain_id) for s in saves if CODER in s]
        # unload_requested_at saved before the call, while it is still draining; stopping as the call begins.
        assert coder[-2:] == [("draining", T + 5, event["drain_id"]), ("stopping", T + 5, event["drain_id"])]
        assert ("stopping", None, event["drain_id"]) not in coder and world.state.models[CODER].state == "stopping"


@pytest.mark.anyio
@pytest.mark.parametrize("why, words", [("make-room", "Unloaded the coder for make-room."),
                                        ("unload", "Unloaded the coder, as `spark unload` asked."),
                                        ("idle", "Unloaded the coder after 60 min idle.")])
async def test_each_drain_saves_its_own_why(tmp_path, why, words):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER, last_use=T - 7200)})
        drain = world.drain(CODER, why)
        await settle()
        assert world.state.models[CODER].drain_why == why and world.front.events()[-1]["why"] == why
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is True and unloaded_text(world) == [words]


@pytest.mark.anyio
async def test_stopping_lines_go_every_15_s_from_the_calls_start(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, "holds")
        lines: list[dict] = []
        drain = world.drain(CODER, "unload", progress=lines.append)
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert STOPPING_EVERY_S == 15
        await world.at(T + 14)
        assert lines == []
        await world.at(T + 15)
        stopping = {"kind": "stopping", "model": CODER, "label": "the coder"}
        assert lines == [stopping] and set(stopping) == set(gateproto.StoppingProgress.__annotations__)
        world.swap.times_out(CODER)  # its timeout changes nothing: the lines count from the call's start
        await world.at(T + 29)
        assert lines == [stopping]
        await world.at(T + 30)
        assert lines == [stopping, stopping]
        world.swap.stop_done(CODER)
        await world.at(T + 31)
        await world.at(T + 60)
        assert drain.result() is True and lines == [stopping, stopping]


@pytest.mark.anyio
async def test_a_caller_that_goes_ends_its_wait_never_the_unload(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Dan's Ctrl-C: the stream ends; the drain, and the unload once drained, go on.
        world = World(tmp_path, models={CODER: record(CODER)})
        lines: list[dict] = []
        caller = world.drain(CODER, "unload", progress=lines.append)
        await settle()
        caller.cancel()
        await settle()
        assert caller.cancelled() and world.swap.unloads() == []
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert world.swap.unloads() == [CODER] and CODER not in world.state.models
        assert world.front.ops()[-1] == ("unloaded", CODER) and lines == []


@pytest.mark.anyio
async def test_a_second_unload_of_a_model_joins_its_drain(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER)})
        first, second = world.drain(CODER, "make-room"), world.drain(CODER, "unload")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert first.result() is True and second.result() is True
        assert world.front.ops() == [("drain", CODER), ("unloaded", CODER)] and world.swap.unloads() == [CODER]


@pytest.mark.anyio
async def test_an_unload_joined_to_an_idle_drain_the_front_found_busy_drains_for_itself(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER, last_use=T - 7200)})
        idle = world.drain(CODER, "idle")
        await settle()
        assert world.front.events()[-1]["why"] == "idle"  # the front marks an idle drain, or answers busy
        unload = world.drain(CODER, "unload")  # joins it, its why now the unload's
        await settle()
        world.channel.busy(CODER, world.front.drain_id(CODER))
        await settle()
        assert idle.result() is False and not unload.done()
        assert world.front.events()[-1]["why"] == "unload"
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert unload.result() is True and world.swap.unloads() == [CODER]


@pytest.mark.anyio
async def test_a_drain_with_the_front_away_sends_nothing_and_says_so(tmp_path, caplog):
    with anyio.fail_after(BOUND_S):
        # The controller's ruling at Task 16: Dan's commands hear why (messages.front_not_connected, from the drain's
        # end); an idle drain just skips, with one journal line while the front stays away.
        world = World(tmp_path, models={CODER: record(CODER, last_use=T - 7200)}, connected=False)
        ends: list[str] = []
        assert await world.drainer.drain(CODER, "unload", ended=ends.append) is False and ends == ["front_away"]
        coder = world.state.models[CODER]
        assert (coder.state, coder.drain_id, coder.drain_why) == ("ready", None, None) and world.state.undrains == {}
        caplog.clear()
        for _ in range(3):
            assert await world.drainer.drain(CODER, "idle") is False
        skipped = [r.getMessage() for r in caplog.records if r.name == LOG]
        assert skipped == [f"not idle-unloading {CODER}: the front's call to the gate isn't open, so nothing can "
                           "drain it; it is tried again once the front is back"]
        world.front = Front(world.channel)
        drain = world.drain(CODER, "idle")
        await settle()
        world.channel.busy(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is False
        world.front.drop()
        caplog.clear()
        assert await world.drainer.drain(CODER, "idle") is False  # away again: said again
        assert len([r for r in caplog.records if r.name == LOG]) == 1


@pytest.mark.anyio
async def test_each_drain_says_how_it_ended(tmp_path):
    with anyio.fail_after(BOUND_S):
        # What Room words a drain's end by (Task 17): the drain's own end, passed to `ended` as it returns.
        world = World(tmp_path, models={CODER: record(CODER), GEMMA: record(GEMMA), WHISPER: record(WHISPER)})
        ends: dict[str, list[str]] = {name: [] for name in (CODER, GEMMA, WHISPER, EMBED)}
        world.swap.unload_as(GEMMA, "not_sent")
        drains = [world.drain(name, "make-room", ended=ends[name].append) for name in (CODER, GEMMA, WHISPER)]
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        world.channel.drained(GEMMA, world.front.drain_id(GEMMA))
        await settle()
        world.front.drop()
        await settle()
        assert [d.result() for d in drains] == [True, False, False]
        assert await world.drainer.drain(EMBED, "unload", ended=ends[EMBED].append) is True
        assert ends == {CODER: ["unloaded"], GEMMA: ["not_taken"], WHISPER: ["front_dropped"], EMBED: ["not_loaded"]}


# The gate's ledger (Task 44e; its review, I2): a drain's own unload is one row, with its why; a drain that unloads
# nothing writes none; and one whose model left meanwhile writes `gone`.

def unload_rows(world: World) -> list[tuple[float, str, str]]:
    return [(row["t"], row["model"], row["why"]) for row in world.ledger_rows if row["kind"] == "unload"]


@pytest.mark.anyio
async def test_a_drains_own_unload_is_one_ledger_row_with_its_why_and_one_that_unloads_nothing_none(tmp_path):
    with anyio.fail_after(BOUND_S):
        # make-room's and `spark unload`'s: unloaded, not taken, dropped before its unload, nothing loaded.
        world = World(tmp_path / "ends", models={CODER: record(CODER), GEMMA: record(GEMMA), WHISPER: record(WHISPER)})
        world.swap.unload_as(GEMMA, "not_sent")
        drains = [world.drain(CODER, "make-room"), world.drain(GEMMA, "unload"), world.drain(WHISPER, "make-room")]
        await settle()
        world.clock.now = T + 4
        world.channel.drained(CODER, world.front.drain_id(CODER))
        world.channel.drained(GEMMA, world.front.drain_id(GEMMA))
        await settle()
        world.front.drop()
        await settle()
        assert [d.result() for d in drains] == [True, False, False]
        assert await world.drainer.drain(EMBED, "unload") is True  # nothing loaded
        assert unload_rows(world) == [(T + 4, CODER, "make-room")]

        # The idle check's: one the front found busy is none, one it drained is `idle`.
        world = World(tmp_path / "idle", models={CODER: record(CODER, last_use=T - 7200)})
        drain = world.drain(CODER, "idle")
        await settle()
        assert world.channel.busy(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is False and unload_rows(world) == []
        world = World(tmp_path / "drained", models={CODER: record(CODER, last_use=T - 7200)})
        drain = world.drain(CODER, "idle")
        await settle()
        world.clock.now = T + 9
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is True and unload_rows(world) == [(T + 9, CODER, "idle")]

        # The front away: nothing sent, nothing unloaded, no row.
        world = World(tmp_path / "away", models={CODER: record(CODER)}, connected=False)
        assert await world.drainer.drain(CODER, "unload") is False and world.ledger_rows == []

        # The model gone while it drained (the brake, a crash): `gone`, at once.
        world = World(tmp_path / "gone", models={CODER: record(CODER)})
        ends: list[str] = []
        drain = world.drain(CODER, "make-room", ended=ends.append)
        await settle()
        del world.state.models[CODER]
        world.swap.states.pop(CODER)
        world.clock.now = T + 2
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is True and ends == ["gone"] and world.swap.unloads() == []
        assert unload_rows(world) == [(T + 2, CODER, "gone")]


@pytest.mark.anyio
async def test_a_drain_that_only_waited_on_the_admitters_late_start_writes_no_row_of_its_own(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Dan's `spark unload` while the coder's start hangs past its deadline: the admitter unloads it itself, its
        # row `late_start`; the drain behind it, which waited on a stop not its own, writes none.
        world = World(tmp_path)
        load = asyncio.get_running_loop().create_task(world.admitter.admit(
            AdmitRequest("r1", CODER, "command", None, T + LOAD_CALL_TIMEOUT_S)))
        await settle()
        ends: list[str] = []
        drain = world.drain(CODER, "unload", ended=ends.append)
        await settle()
        world.swap._loads.pop(CODER).set_result(LoadOutcome.unknown("timeout"))  # the call ends; the start runs on
        await settle()
        world.swap.unload_as(CODER, "holds")
        await world.at(T + 3600)  # past the start's deadline: the admitter's abort, its stop under way
        await world.at(T + 3601)  # the drain looks again, and waits on that stop
        assert world.state.models[CODER].state == "stopping" and not drain.done()
        world.clock.now = T + 3605
        world.swap.stop_done(CODER)
        await settle()
        await world.at(T + 3606)
        assert drain.result() is True and ends == ["unloaded"] and load.done()
        assert unload_rows(world) == [(T + 3605, CODER, "late_start")]


@pytest.mark.anyio
async def test_an_unload_asked_of_a_starting_model_waits_for_its_load(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The admitter's _ready replaces a starting record whole: nothing is marked on it until the load ends.
        world = World(tmp_path)
        load = asyncio.get_running_loop().create_task(world.admitter.admit(
            AdmitRequest("r1", CODER, "command", None, T + LOAD_CALL_TIMEOUT_S)))
        await settle()
        assert world.state.models[CODER].state == "starting"
        drain = world.drain(CODER, "unload")
        await world.at(T + 5)
        assert world.front.events() == [] and world.state.models[CODER].drain_id is None
        world.clock.now = T + 24
        world.swap.ready(CODER)
        await settle()
        assert isinstance(load.result(), Admitted) and world.state.models[CODER].state == "ready"
        await world.at(T + 25)
        drain_id = world.front.drain_id(CODER)
        coder = world.state.models[CODER]
        assert (coder.state, coder.drain_id, coder.drain_why) == ("draining", drain_id, "unload")
        world.channel.drained(CODER, drain_id)
        await world.at(T + 26)
        assert drain.result() is True and world.swap.unloads() == [CODER]


@pytest.mark.anyio
async def test_an_unload_asked_of_a_start_that_fails_has_nothing_to_unload(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path)
        load = asyncio.get_running_loop().create_task(world.admitter.admit(
            AdmitRequest("r1", CODER, "command", None, T + LOAD_CALL_TIMEOUT_S)))
        await settle()
        drain = world.drain(CODER, "unload")
        await settle()
        world.swap.exits(CODER)
        await world.at(T + 5)
        assert load.result().code == "load_failed" and drain.result() is True
        assert world.front.events() == [] and world.swap.unloads() == []


@pytest.mark.anyio
async def test_a_resumed_drain_goes_on_under_its_saved_id(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Restore left the coder draining, its unload requested: the drain is sent again under its saved id, once the
        # front's call is open, so the requests in flight finish; then the unload.
        going = record(CODER, state="draining", unload_requested_at=T - 5, drain_id="d1", drain_why="make-room")
        world = World(tmp_path, models={CODER: going}, connected=False)
        [saved] = resumable_drains(world.state)
        drain = asyncio.get_running_loop().create_task(world.drainer.resume(saved.model, saved.drain_id, saved.why))
        await world.at(T + 10)
        assert not drain.done() and world.swap.unloads() == []
        world.front = Front(world.channel)
        await world.at(T + 11)
        assert world.front.events() == [{"op": "drain", "model": CODER, "drain_id": "d1", "why": "make-room"}]
        world.channel.drained(CODER, "d1")
        await settle()
        assert drain.result() is True and world.swap.unloads() == [CODER]
        assert unloaded_text(world) == ["Unloaded the coder for make-room."]


@pytest.mark.anyio
async def test_a_resumed_stopping_drain_with_no_why_waits_for_gone_and_is_worded_neutrally(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Restore found the coder stopping with no record of it: an unload with no why of the gate's.
        going = record(CODER, state="stopping", unload_requested_at=T - 5)
        world = World(tmp_path, models={CODER: going})
        [saved] = resumable_drains(world.state)
        assert saved.why == "unknown"
        drain = asyncio.get_running_loop().create_task(world.drainer.resume(saved.model, saved.drain_id, saved.why))
        await world.at(T + 10)
        assert not drain.done() and world.front.events() == [] and world.swap.unloads() == []
        world.swap.stop_done(CODER)
        await world.at(T + 11)
        assert drain.result() is True and world.front.ops() == [("unloaded", CODER)]
        assert unloaded_text(world) == ["Unloaded the coder."]


@pytest.mark.anyio
async def test_an_unload_that_meets_a_saved_drain_is_that_drain(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Dan's `spark unload` reaches a model restore left stopping under make-room's saved drain before the core
        # resumes it: it waits for that unload, under its id and why, and the front, which may hold it, hears
        # `unloaded`. The core's resume then joins it.
        going = record(CODER, state="stopping", unload_requested_at=T - 5, drain_id="d1", drain_why="make-room")
        world = World(tmp_path, models={CODER: going})
        unload = world.drain(CODER, "unload")
        await settle()
        resumed = asyncio.get_running_loop().create_task(world.drainer.resume(CODER, "d1", "make-room"))
        await world.at(T + 10)
        assert not unload.done() and world.front.events() == [] and world.swap.unloads() == []
        world.swap.stop_done(CODER)
        await world.at(T + 11)
        assert unload.result() is True and resumed.result() is True
        assert world.front.ops() == [("unloaded", CODER)]
        assert unloaded_text(world) == ["Unloaded the coder for make-room."]


@pytest.mark.anyio
async def test_a_resumed_drain_with_no_why_names_no_command(tmp_path):
    with anyio.fail_after(BOUND_S):
        going = record(CODER, state="draining", unload_requested_at=T - 5, drain_id="d1")
        world = World(tmp_path, models={CODER: going})
        [saved] = resumable_drains(world.state)
        drain = asyncio.get_running_loop().create_task(world.drainer.resume(saved.model, saved.drain_id, saved.why))
        await settle()
        assert world.front.events() == [{"op": "drain", "model": CODER, "drain_id": "d1", "why": "unknown"}]
        world.channel.drained(CODER, "d1")
        await settle()
        assert drain.result() is True and unloaded_text(world) == ["Unloaded the coder."]


def late_start_world(tmp_path: Path, shown: str, *, connected: bool = True) -> World:
    """A late unload found across a restart (Task 15's abort of a start past its deadline, saved with why late_start
    and no drain id): restore's `draining`, /running showing the coder `shown`."""
    going = record(CODER, state="draining", unload_requested_at=T - 5, drain_why="late_start")
    world = World(tmp_path, models={CODER: going}, connected=connected,
                  running=[Running(CODER, "starting")] if shown == "starting" else None)
    if shown == "gone":
        del world.swap.states[CODER]
    else:
        world.swap.states[CODER] = shown
    return world


def resume_saved(world: World) -> asyncio.Task:
    [saved] = resumable_drains(world.state)
    assert (saved.model, saved.drain_id, saved.why) == (CODER, None, "late_start")
    return asyncio.get_running_loop().create_task(world.drainer.resume(saved.model, saved.drain_id, saved.why))


@pytest.mark.anyio
async def test_a_resumed_late_start_still_starting_is_unloaded_without_the_front(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The controller's ruling at Task 16: no request can be in flight to a model that never became ready (its
        # waiters are in the gate's queue), so the abort needs no `drained`, as Task 15's own doesn't: it unloads at
        # once, with the front's call closed. Its start holds the one-load slot until /running shows it gone.
        world = late_start_world(tmp_path, "starting", connected=False)
        world.swap.unload_as(CODER, "holds")
        drain = resume_saved(world)
        embed = asyncio.get_running_loop().create_task(world.admitter.admit(
            AdmitRequest("r1", EMBED, "command", None, T + LOAD_CALL_TIMEOUT_S)))
        await world.at(T + 5)
        assert world.swap.unloads() == [CODER] and world.state.models[CODER].state == "stopping"
        assert world.swap.loads() == [] and world.admitter.waiting()[0]["why"] == "slot"
        world.swap.stop_done(CODER)
        await world.at(T + 6)
        assert drain.result() is True and CODER not in world.state.models
        assert unloaded_text(world) == ["Unloaded the coder: its start ran past its deadline."]
        await world.at(T + 7)
        assert world.swap.loads() == [EMBED] and not embed.done()
        # No `drain` was ever sent; the `unloaded` that ends it waits, owed, for the front's call.
        world.front = Front(world.channel)
        await world.at(T + 8)
        assert world.front.ops() == [("unloaded", CODER)]


@pytest.mark.anyio
@pytest.mark.parametrize("how", ["not_sent", "404"])
async def test_a_resumed_late_start_whose_unload_isnt_taken_tries_again_never_back_to_ready(tmp_path, how):
    with anyio.fail_after(BOUND_S):
        # An unload llama-swap never took, the model still starting: never undone to ready, which would serve a model
        # still starting; tried again at the next look, as Task 15's abort does.
        world = late_start_world(tmp_path, "starting")
        world.swap.unload_as(CODER, how)
        drain = resume_saved(world)
        await world.at(T + 5)
        assert world.swap.unloads() == [CODER] and not drain.done()
        coder = world.state.models[CODER]
        assert (coder.state, coder.unload_requested_at, coder.drain_why) == ("draining", T - 5, "late_start")
        assert world.front.events() == [] and world.state.undrains == {}
        world.front.drop()  # a channel drop changes nothing: the abort never waited on the front
        await world.at(T + 6)
        assert world.swap.unloads() == [CODER, CODER] and drain.result() is True
        assert unloaded_text(world) == ["Unloaded the coder: its start ran past its deadline."]


@pytest.mark.anyio
async def test_a_resumed_late_start_found_ready_is_drained_as_unknown(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Ready by then: requests may be in flight, so it is a drain as any other, its why unknown, under a new id.
        world = late_start_world(tmp_path, "ready")
        drain = resume_saved(world)
        await settle()
        [event] = world.front.events()
        assert event["op"] == "drain" and event["why"] == "unknown" and event["drain_id"]
        assert world.state.models[CODER].drain_why == "unknown" and world.swap.unloads() == []
        world.channel.drained(CODER, event["drain_id"])
        await settle()
        assert drain.result() is True and world.swap.unloads() == [CODER]
        assert unloaded_text(world) == ["Unloaded the coder."]


@pytest.mark.anyio
async def test_a_resumed_late_start_found_gone_sends_unloaded(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = late_start_world(tmp_path, "gone")
        drain = resume_saved(world)
        await settle()
        assert drain.result() is True and world.swap.unloads() == []
        assert world.front.ops() == [("unloaded", CODER)] and CODER not in world.state.models


@pytest.mark.anyio
async def test_a_model_gone_while_draining_is_released_not_unloaded(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The brake took it while its requests finished: nothing to unload, and the front's hold is released.
        world = World(tmp_path, models={CODER: record(CODER)})
        drain = world.drain(CODER, "make-room")
        await settle()
        del world.state.models[CODER]  # the core saw it leave /running
        world.swap.states[CODER] = "stopped"
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is True and world.swap.unloads() == []
        assert world.front.ops()[-1] == ("unloaded", CODER) and world.emitted.of("unloaded") == []


@pytest.mark.anyio
async def test_the_gates_stop_leaves_each_drain_saved_for_its_restart(tmp_path):
    with anyio.fail_after(BOUND_S):
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, "holds")
        ends: list[str] = []
        drain = world.drain(CODER, "make-room", ended=ends.append)
        await settle()
        drain_id = world.front.drain_id(CODER)
        world.channel.drained(CODER, drain_id)
        await settle()
        await world.drainer.stop()
        await settle()
        # The caller hears it as a drain the gate stopped under (the controller's ruling at Task 16's review).
        assert drain.result() is False and ends == ["gate_stopping"]
        coder = world.state.models[CODER]
        assert (coder.state, coder.drain_id, coder.drain_why, coder.unload_requested_at, coder.unload_call_at) == (
            "stopping", drain_id, "make-room", T, T)
        ends.clear()
        # Nothing new once the gate is stopping, and the caller hears why.
        assert await world.drainer.drain(GEMMA, "make-room", ended=ends.append) is False
        assert ends == ["gate_stopping"]


# The review's findings (the controller's rulings, at Task 16's review).

@pytest.mark.anyio
async def test_a_drain_behind_an_abort_that_was_undone_drains_as_any_other(tmp_path):
    with anyio.fail_after(BOUND_S):
        # I-1: Dan's `spark unload` while the coder's start hangs past its deadline. The admitter's own abort marks the
        # record stopping before its call; llama-swap never takes it, so the admitter undoes it, and the model turns
        # ready, which that abort keeps. The drain that waited on a stop never its own goes back through its steps and
        # drains the model as any other: no still-stopping line and no stuck alert once the stop is undone, and no
        # later drain is joined to a stuck one.
        world = World(tmp_path)
        load = asyncio.get_running_loop().create_task(world.admitter.admit(
            AdmitRequest("r1", CODER, "command", None, T + LOAD_CALL_TIMEOUT_S)))
        await settle()
        lines: list[dict] = []
        drain = world.drain(CODER, "unload", progress=lines.append)
        await settle()
        assert world.state.models[CODER].state == "starting"
        world.swap._loads.pop(CODER).set_result(LoadOutcome.unknown("timeout"))  # the call ends; the start runs on
        await settle()
        connect = asyncio.get_running_loop().create_future()

        async def unload(model):
            world.swap.calls.append(("unload", model, world.clock()))
            await connect  # the connect not yet made
            raise LlamaSwapNotSent("stand-in: connect timed out")

        world.swap.unload = unload
        await world.at(T + 3600)  # past the start's deadline: the admitter's abort
        await world.at(T + 3601)  # the drainer looks again
        assert world.state.models[CODER].state == "stopping" and world.front.events() == []
        connect.set_result(None)
        await settle()
        world.swap.states[CODER] = "ready"
        await world.at(T + 3602)
        await world.at(T + 3603)
        assert isinstance(load.result(), Admitted) and world.state.models[CODER].state == "draining"
        [event] = world.front.events()
        assert (event["op"], event["why"]) == ("drain", "unload")
        for second in range(15, 15 * 25, 15):  # six minutes on
            await world.at(T + 3603 + second)
        # Only the true line that it waits for the load, from when it began: never a still-stopping one.
        assert [line["kind"] for line in lines] == ["after_load"] and world.emitted.of("llama_swap_down") == []
        del world.swap.unload  # llama-swap takes the drainer's own unload
        world.channel.drained(CODER, event["drain_id"])
        await world.at(T + 4000)
        assert drain.result() is True and world.swap.states[CODER] == "stopped"
        assert unloaded_text(world)[-1] == "Unloaded the coder, as `spark unload` asked."


@pytest.mark.anyio
@pytest.mark.parametrize("how", ["stops", "not_sent"])
async def test_a_word_still_queued_when_the_fronts_call_drops_is_sent_on_the_next(tmp_path, how):
    with anyio.fail_after(BOUND_S):
        # I-2: a drain's last word, `unloaded` or `undrain`, still in the queue, unread, when the front's call drops:
        # the front may hold the model drained, so the word is owed and sent once it is back.
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, how)
        drain = world.drain(CODER, "unload")
        await settle()
        drain_id = world.front.drain_id(CODER)
        world.channel.drained(CODER, drain_id)
        await settle()
        assert drain.done()
        world.front.drop()  # before the front read it
        assert [event["op"] for event in world.front.seen] == ["drain"]
        world.front.reconnect()
        await world.at(T + 1)
        word = ({"op": "unloaded", "model": CODER} if how == "stops"
                else {"op": "undrain", "model": CODER, "drain_id": drain_id})
        assert world.front.events()[1:] == [word] and world.state.undrains == {}


@pytest.mark.anyio
async def test_an_owed_word_goes_ahead_of_a_new_drain(tmp_path):
    with anyio.fail_after(BOUND_S):
        # I-2: `unloaded` carries no drain id, so an owed one that reached the front after a new `drain` of the same
        # model would release that new drain's hold while the gate unloads. A new drain sends it first, even before
        # any changed().
        world = World(tmp_path, models={CODER: record(CODER)})
        world.swap.unload_as(CODER, "holds")
        first = world.drain(CODER, "unload")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        world.front.drop()
        world.swap.stop_done(CODER)
        await world.at(T + 1)
        assert first.result() is True and CODER in world.state.undrains  # its `unloaded` owed
        world.state.models[CODER] = record(CODER)  # loaded again
        world.swap.states[CODER] = "ready"
        world.front.reconnect()  # back, and no changed() since
        second = world.drain(CODER, "unload")
        await settle()
        assert [(event["op"], event["model"]) for event in world.front.events()[1:]] == [
            ("unloaded", CODER), ("drain", CODER)]
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert second.result() is True


@pytest.mark.anyio
async def test_a_stuck_stop_alerts_once_across_a_restart(tmp_path):
    with anyio.fail_after(BOUND_S):
        # A minor of the review: the stuck alert is keyed by the stop's own identity, its call's start, saved with
        # the record, so a restarted gate sends the same key, and the Notifier, which keeps what it sent with the
        # state, never sends a second high alert for the same hung stop.
        held = asyncio.Event()

        def on_change():
            coder = world.state.models.get(CODER)
            if coder is not None and coder.state == "draining" and coder.unload_requested_at is not None:
                return held.wait()
            return None

        world = World(tmp_path / "before", models={CODER: record(CODER)}, on_change=on_change)
        world.swap.unload_as(CODER, "holds")
        drain = world.drain(CODER, "make-room")
        await settle()
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        world.clock.now = T + 20  # the step before the call took 20 s: the call begins later than `drained`
        held.set()
        await settle()
        coder = world.state.models[CODER]
        assert (coder.state, coder.unload_requested_at, coder.unload_call_at) == ("stopping", T, T + 20)
        await world.at(T + 20 + STUCK_STOPPING_S)
        [(key, fields)] = world.emitted.of("llama_swap_down")
        assert key == f"stuck:{CODER}:{T + 20:.3f}" and fields["since"] == T + 20
        # The gate restarts; the stop still hangs.
        await world.drainer.stop()
        await settle()
        assert drain.result() is False
        folder = tmp_path / "gate"
        save_state(folder, world.state, now=T + 400)
        loaded, problem = load_state(folder, now=T + 400)
        assert problem is None
        restored, _ = restore(loaded, [Running(CODER, "stopping")], now=T + 400, boot_id=BOOT, registry=REGISTRY)
        again = World(tmp_path / "after", state=restored)
        again.clock.now = T + 400
        [saved] = resumable_drains(again.state)
        resumed = asyncio.get_running_loop().create_task(again.drainer.resume(saved.model, saved.drain_id, saved.why))
        await again.at(T + 401)
        [(key_again, fields_again)] = again.emitted.of("llama_swap_down")
        assert key_again == key and fields_again["since"] == T + 20
        again.swap.stop_done(CODER)
        await again.at(T + 402)
        assert resumed.result() is True


@pytest.mark.anyio
async def test_a_pin_or_session_landing_before_an_idle_drain_marks_the_model_keeps_it(tmp_path):
    with anyio.fail_after(BOUND_S):
        # A minor of the review: an idle drain checks again, right before it marks the model, what idle_due checked.
        # A pin, a live session or a use that landed in between ends it as busy, and nothing is drained or unloaded.
        live: list[Session] = []
        world = World(tmp_path, models={CODER: record(CODER, last_use=T - 7200)},
                      live=lambda session: session in live)
        assert idle_due(T, world.state, REGISTRY, world.channel.snapshot, never_live) == [CODER]
        ends: list[str] = []
        drain = world.drain(CODER, "idle", ended=ends.append)
        world.state.pins[CODER] = Pin(model=CODER, until=T + 3600, by_uid=DAN)  # Dan's pin, before the drain's step
        await settle()
        assert drain.result() is False and ends == ["busy"]
        coder = world.state.models[CODER]
        assert (coder.state, coder.drain_id, coder.drain_why) == ("ready", None, None)
        assert world.front.events() == [] and world.swap.unloads() == []
        del world.state.pins[CODER]
        session = Session(id="s1", uid=AGENT, pid=4242, model=CODER, label="agent's pi", started_at=T, renewed_at=T,
                          boot_id=BOOT, start_time=777_000)
        world.state.sessions[session.id] = session
        live.append(session)
        assert await world.drainer.drain(CODER, "idle") is False and world.front.events() == []
        del world.state.sessions[session.id]
        world.channel.report(snapshot(**{CODER: {"last_end_at": T - 30}}))  # a request ended 30 s ago
        assert await world.drainer.drain(CODER, "idle") is False and world.front.events() == []
        world.channel.report(snapshot())
        drain = world.drain(CODER, "idle")
        await settle()
        assert world.front.events()[-1]["why"] == "idle"  # idle after all: drained


@pytest.mark.anyio
async def test_an_unload_behind_a_load_says_it_waits_for_it(tmp_path):
    with anyio.fail_after(BOUND_S):
        # A minor of the review: while the model loads, Dan's command hears why nothing happens yet, once, and so does
        # a caller that joins the drain then.
        world = World(tmp_path)
        asyncio.get_running_loop().create_task(world.admitter.admit(
            AdmitRequest("r1", CODER, "command", None, T + LOAD_CALL_TIMEOUT_S)))
        await settle()
        lines: list[dict] = []
        joined: list[dict] = []
        drain = world.drain(CODER, "unload", progress=lines.append)
        await world.at(T + 5)
        world.drain(CODER, "make-room", progress=joined.append)
        await world.at(T + 10)
        line = {"kind": "after_load", "model": CODER, "label": "the coder"}
        assert lines == [line] and joined == [line] and set(line) == set(gateproto.AfterLoadProgress.__annotations__)
        assert line["kind"] in PROGRESS_KINDS
        world.swap.ready(CODER)
        await world.at(T + 11)
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert drain.result() is True and lines == [line]


@pytest.mark.anyio
@pytest.mark.parametrize("how", ["hangs", "raises"])
async def test_a_save_that_fails_or_hangs_before_drain_is_sent_ends_the_drain(tmp_path, how, caplog):
    with anyio.fail_after(BOUND_S):
        # A minor of the review: the save of the drain's id before `drain` is sent is bounded, at SAVE_BOUND_S. Unsaved,
        # nothing is sent, and the drain ends, `not_saved`, with true words in the journal.
        held = asyncio.Event()

        def on_change():
            coder = world.state.models.get(CODER)
            if coder is not None and coder.state == "draining" and coder.unload_requested_at is None:
                if how == "raises":
                    raise OSError(28, "No space left on device")
                return held.wait()
            return None

        world = World(tmp_path, models={CODER: record(CODER)}, on_change=on_change)
        ends: list[str] = []
        drain = world.drain(CODER, "unload", ended=ends.append)
        await settle()  # the save begins at T
        await world.at(T + SAVE_BOUND_S - 1)
        if how == "hangs":
            assert not drain.done() and world.front.events() == []
            await world.at(T + SAVE_BOUND_S)
        assert drain.result() is False and ends == ["not_saved"] and world.front.events() == []
        coder = world.state.models[CODER]
        assert (coder.state, coder.drain_id, coder.drain_why) == ("ready", None, None)
        assert SAVE_BOUND_S == 5.0
        said = [r.getMessage() for r in caplog.records if r.name == LOG and r.levelno == logging.ERROR]
        assert f"the gate couldn't save its state before draining {CODER}, so no drain was sent" in said
        caplog.clear()


@pytest.mark.anyio
async def test_an_unload_that_joins_an_idle_drain_is_reported_as_the_unload(tmp_path):
    with anyio.fail_after(BOUND_S):
        # A minor of the review: Dan's command is the stronger reason, so the drain takes its why.
        world = World(tmp_path, models={CODER: record(CODER, last_use=T - 7200)})
        idle = world.drain(CODER, "idle")
        await settle()
        assert world.front.events()[-1]["why"] == "idle"
        unload = world.drain(CODER, "unload")
        await settle()
        assert world.state.models[CODER].drain_why == "unload" and world.saves[-1][CODER].drain_why == "unload"
        world.channel.drained(CODER, world.front.drain_id(CODER))
        await settle()
        assert idle.result() is True and unload.result() is True
        assert unloaded_text(world) == ["Unloaded the coder, as `spark unload` asked."]
