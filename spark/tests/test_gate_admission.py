"""The gate's admission (website/design/phase-2a.md, Task 15): the queue, one load at a time, its tickets, and its
refusals in Task 6's words with the gate's own figures. Every load goes through rule 9's formula (plan.md, *Admission
and memory rules*): the reserve, the growth owed, the ceiling, a model still starting, make-room's hold for a group that
can't load into it, the brake's hold and its mark; Dan's keys go ahead of agent's, and the gate's own reloads ahead of
both.

The clock is injected, so waits of minutes run in milliseconds: a test moves it and calls `changed()`, as the core does
on every memory read. llama-swap is an in-process stand-in on that clock (StandIn): v257 as the gate's AsyncLlamaSwap
sees it, whose loads, `/running` and log lines each test scripts, and which runs `spark launch`'s part of a start, the
ticket claimed and the start recorded under `started/`, as launch does. Every time, boot id and pid here is a stand-in;
none was read from the box."""

import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import anyio
import pytest

from fake_llamaswap import _envelope
from spark import gateproto, messages, tickets
from spark.gate.admission import LOAD_FALL_MARGIN_GIB, Admitted, Admitter, AdmitRequest, Privileges, privileges
from spark.gate.state import BrakeMark, GateState, ModelRecord, RoomHold, Ticketed
from spark.gateproto import LOAD_CALL_TIMEOUT_S, PROGRESS_KINDS, RELEASE_AFTER_S
from spark.hold import Hold
from spark.llamaswap import LlamaSwapUnreachable, Running
from spark.llamaswap_async import LoadOutcome
from spark.memory import MemInfo
from spark.messages import Holder, Refusal
from spark.registry import ClientKey, KeyGroup, load_registry
from spark.render import HF_HOME, HEALTH_CHECK_TIMEOUT_S, model_path

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
STACK = load_registry(STACK_REGISTRY)
TZ = "PDT+7"  # the tests' own zone, seven hours behind UTC, as test_messages.py has it
ZONE = timezone(timedelta(hours=-7))
BOUND_S = 5  # every async test's bound
BOOT = "b1"
LOG = "spark.gate.admission"


def at(hour: int, minute: int, second: float = 0) -> float:
    return datetime(2026, 10, 8, hour, minute, tzinfo=ZONE).timestamp() + second


T = at(9, 0)
AT_0312 = at(3, 12)


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


GATES: list["Gate"] = []


@pytest.fixture(autouse=True)
def no_hidden_errors(caplog):
    """Every notification the admitter sends must be one Task 7's words can build, and nothing in it may fail unseen:
    the admitter logs an emit it couldn't make, or a pass that broke, at ERROR, and carries on, as the gate must."""
    GATES.clear()
    caplog.set_level(logging.WARNING, logger=LOG)
    yield
    assert [gate.emitted.errors for gate in GATES] == [[] for _ in GATES]
    assert [r.getMessage() for r in caplog.records if r.name == LOG and r.levelno >= logging.ERROR] == []


def name_of(label: str) -> str:
    return next(name for name, model in STACK.models.items() if model.label == label)


CODER, GEMMA, EMBED, WHISPER = name_of("the coder"), name_of("Gemma"), name_of("the embeddings"), name_of("whisper")
FOOTPRINT = {GEMMA: 32.0, EMBED: 8.0, WHISPER: 3.0, CODER: 41.0}


def extra(name: str, label: str, footprint: float):
    """An on-demand model beside the stack's four, for orders and sizes they don't give."""
    coder = STACK.models[CODER]
    return replace(coder, name=name, label=label, roles=(), footprint_gib=footprint,
                   source=replace(coder.source, file=f"{name}.gguf"))


EXTRAS = [extra(f"model-{x}", f"model {x.upper()}", 1.0) for x in "abcde"] + [
    extra("big-model", "the big model", 49.0), extra("nine-model", "the nine", 9.0),
    extra("thirty-model", "the thirty", 30.0)]
WEB = KeyGroup("web", 30, 0, True, True, "dan", True, 8, 32)  # a third group at queue 0
MIXED = KeyGroup("mixed", 30, 0, True, False, "dan", True, 8, 32)  # uses the hold, but can't load a marked model
PI = ClientKey("mac-pi", "dan", "pi on the Mac", "chendaniely")
AGENT = ClientKey("agent", "agent", "agent", "agent")
WEB_KEY = ClientKey("web", "web", "the web UI", None)
MIXED_KEY = ClientKey("mixed", "mixed", "the mixed key", None)
PYTHON = Holder("python3 (chendaniely)", 32, True)


def make_registry(*, coder_cold: float | None = 33.0, owed_reads_rss: bool = False):
    """The stack's registry with the coder at Qwen3.8-27B's estimate, 41 (Task 42's swap), its cold load as given,
    the extra models, and the two test groups."""
    models = dict(STACK.models)
    models[CODER] = replace(models[CODER], footprint_gib=41.0, cold_load_gib=coder_cold)
    models.update({m.name: m for m in EXTRAS})
    gate = replace(STACK.gate, owed_reads_rss=dict.fromkeys(STACK.engines, owed_reads_rss))
    return replace(STACK, models=models, key_groups=STACK.key_groups | {"web": WEB, "mixed": MIXED}, gate=gate)


REGISTRY = make_registry()


def record(name: str, footprint: float | None = None, *, fall: float | None = None, state: str = "ready",
           rss_anon_at_load_gib: float | None = None, **more) -> ModelRecord:
    """A model loaded before the test starts; by default its load took its whole footprint, so it is owed nothing."""
    footprint = FOOTPRINT.get(name, 0.0) if footprint is None else footprint
    return ModelRecord(name=name, footprint_gib=footprint, loaded_at=T - 3600,
                       load_fall_gib=footprint if fall is None else fall, fall_flagged=False,
                       rss_anon_at_load_gib=rss_anon_at_load_gib, last_use=T - 60, state=state, **more)


def loaded(*names: str) -> dict[str, ModelRecord]:
    return {name: record(name) for name in names}


def five_hundred(message: str) -> LoadOutcome:
    """A 500 as AsyncLlamaSwap.load reads v257's: the body's text, v257's JSON error."""
    return LoadOutcome.failed(500, _envelope(500, f"unspecific error: {message}").decode())


EXITED = "upstream command exited prematurely"  # internal/process/process_command.go:556-559
TIMED_OUT = "health check timed out after 3m0s"  # process_command.go:587-589, with render's 180 s
UNLOADED = "group: model unloaded"  # internal/router/scheduler/fifo.go:230-242
SHUTTING_DOWN = "group is shutting down"  # internal/router/base.go:598-602


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class StandIn:
    """llama-swap v257 as the admitter sees it through AsyncLlamaSwap, in process and on the tests' clock: each load
    call starts the model (`/running` shows it starting) and runs `spark launch`'s part, the ticket claimed and the
    start recorded under `started/`, unless the test holds the claim back (`claim_late`); a claim that fails ends the
    start as v257 ends one whose command exited. The call then waits for the test: `ready`, `exits`, `times_out` (each
    v257's answer, the model's state set as v257 sets it), or `drops` (the call ends unknown; the start goes on).
    `answer_next` answers a call at once with nothing started, as llama-swap's own refusals and a call never sent are.
    `unload` aborts a start under way, its call answered `group: model unloaded`; `shut_down` answers each call as
    llama-swap's shutdown does. `observe(model)` is called as each load call arrives."""

    def __init__(self, clock: Clock, launch: Path):
        self.clock, self.launch = clock, launch
        self.states: dict[str, str] = {}
        self.calls: list[tuple[str, str, float]] = []
        self.lines: dict[str, list[str]] = {}
        self.down = False
        self.claim_late: set[str] = set()
        self.observe = None
        self._calls: dict[str, asyncio.Future] = {}
        self._next: dict[str, LoadOutcome] = {}
        self._every: dict[str, LoadOutcome] = {}

    def loads(self) -> list[str]:
        return [model for kind, model, _ in self.calls if kind == "load"]

    def unloads(self) -> list[str]:
        return [model for kind, model, _ in self.calls if kind == "unload"]

    async def running(self) -> list[Running]:
        if self.down:
            raise LlamaSwapUnreachable("stand-in: not answering")
        return [Running(model, state) for model, state in sorted(self.states.items()) if state != "stopped"]

    async def load(self, model: str) -> LoadOutcome:
        self.calls.append(("load", model, self.clock()))
        if self.observe is not None:
            self.observe(model)
        if model in self._next:
            return self._next.pop(model)
        if model in self._every:
            return self._every[model]
        self.states[model] = "starting"
        call = asyncio.get_running_loop().create_future()
        self._calls[model] = call
        if model not in self.claim_late:
            self.claim(model)
        return await call

    def claim(self, model: str) -> None:
        """`spark launch`, as llama-swap runs it: the ticket claimed, then the start recorded (tickets.mark_started)."""
        claim = tickets.claim(self.launch, model, self.clock(), boot_id=BOOT)
        if not claim.ok:
            return self._end(model, "stopped", five_hundred(EXITED))
        tickets.mark_started(self.launch, claim.ticket, self.clock())

    def ready(self, model: str) -> None:
        self._end(model, "ready", LoadOutcome.READY)

    def exits(self, model: str) -> None:
        self._end(model, "stopped", five_hundred(EXITED))

    def times_out(self, model: str) -> None:
        self._end(model, "stopped", five_hundred(TIMED_OUT))

    def drops(self, model: str, why: str = "timeout") -> None:
        self._calls.pop(model).set_result(LoadOutcome.unknown(why))

    def answer_next(self, model: str, outcome: LoadOutcome) -> None:
        self._next[model] = outcome

    def answer_every(self, model: str, outcome: LoadOutcome | None) -> None:
        """Every call of `model` from now on answered `outcome` at once, nothing started; None ends it."""
        if outcome is None:
            self._every.pop(model, None)
        else:
            self._every[model] = outcome

    def shut_down(self) -> None:
        for model in list(self._calls):
            self._end(model, "stopped", five_hundred(SHUTTING_DOWN))

    async def unload(self, model: str) -> None:
        self.calls.append(("unload", model, self.clock()))
        if model in self._calls:
            self._end(model, "stopped", five_hundred(UNLOADED))
        self.states[model] = "stopped"

    async def last_lines(self, model: str, n: int = 20, read_s: float = 1.0) -> list[str]:
        return self.lines.get(model, [])[-n:]

    def _end(self, model: str, state: str, outcome: LoadOutcome) -> None:
        self.states[model] = state
        call = self._calls.pop(model, None)
        if call is not None and not call.done():
            call.set_result(outcome)


class Emitted:
    """A recording Emit: each notification checked against Task 7's words as the Notifier builds them."""

    def __init__(self, registry):
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


class Asked:
    """One admission under way: its request, its task and the progress lines it was sent."""

    def __init__(self, gate: "Gate", req: AdmitRequest):
        self.req = req
        self.progress: list[dict] = []
        self.finished_at: float | None = None

        async def run():
            try:
                return await gate.admitter.admit(req, progress=self.progress.append)
            finally:
                self.finished_at = gate.clock()

        self.task = asyncio.get_running_loop().create_task(run())

    @property
    def done(self) -> bool:
        return self.task.done()

    @property
    def result(self):
        assert self.task.done(), "still waiting"
        return self.task.result()


class Gate:
    """An admitter with everything around it the test builds: the clock, launch's folder, a Hugging Face cache with
    every model's files, the state, MemAvailable from `available`, the brake's hold from `hold`, the top holders, the
    stand-in llama-swap, and a record of each save (`saves`: `state.issued` and each model's record as saved)."""

    def __init__(self, tmp_path: Path, *, models: dict[str, ModelRecord] | None = None, available: float = 74.0,
                 registry=REGISTRY, state: GateState | None = None, holders: list[Holder] | None = None,
                 proc: Path | None = None):
        GATES.append(self)
        self.clock = Clock(T)
        self.launch = tmp_path / "launch"
        self.launch.mkdir(parents=True, exist_ok=True)  # bootstrap makes launch's folder (Task 36)
        self.hf_home = tmp_path / "hf"
        for model in registry.models.values():
            for file in filter(None, (model.source.file, model.source.mmproj)):
                path = self.hf_home / Path(model_path(model.source, file)).relative_to(HF_HOME)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"gguf")
        self.state = state or GateState(boot_id=BOOT)
        self.state.models.update(models or {})
        self.available = available
        self.hold: Hold | None = None
        self.holders = holders or []
        self.registry = registry
        self.emitted = Emitted(registry)
        self.swap = StandIn(self.clock, self.launch)
        self.saves: list[tuple[dict, dict]] = []
        more = {} if proc is None else {"proc": proc}
        self.admitter = Admitter(registry, self.state, self.swap, self.emitted, self.clock,
                                 read_mem=lambda: MemInfo(121.6, self.available), read_hold=lambda: self.hold,
                                 launch=self.launch, hf_home=str(self.hf_home), holders=lambda names: self.holders,
                                 on_change=self._saved, poll_s=60.0, **more)
        self._n = 0

    def _saved(self) -> None:
        self.saves.append((dict(self.state.issued), {name: replace(r) for name, r in self.state.models.items()}))

    def ask(self, key: ClientKey | None, model: str, *, kind: str = "key", deadline: float | None = None) -> Asked:
        self._n += 1
        if kind == "key" and deadline is None:
            deadline = self.clock() + self.registry.key_groups[key.group].wait_s
        return Asked(self, AdmitRequest(id=f"r{self._n}", model=model, kind=kind, key=key, deadline=deadline))

    async def at(self, when: float) -> None:
        """The clock at `when`, then a change, as the core reports every memory reading."""
        self.clock.now = when
        self.admitter.changed()
        await settle()

    def ticket(self, model: str) -> dict | None:
        path = tickets.record_path(self.launch, tickets.TICKETS, model)
        return json.loads(path.read_text()) if path.exists() else None

    def started(self) -> list[str]:
        folder = self.launch / tickets.STARTED
        return sorted(os.listdir(folder)) if folder.exists() else []


async def settle() -> None:
    """Let every task that can move, move: the admitter's pass, its load tasks and the stand-in."""
    for _ in range(200):
        await asyncio.sleep(0)


def hold_at_0312(*, waits_for_dan_boot: str = BOOT) -> Hold:
    return Hold(since=datetime.fromtimestamp(AT_0312).strftime("%Y-%m-%dT%H:%M:%S"), reason="memory fell to 19.6 GiB",
                unloaded=(GEMMA,), boot_id=waits_for_dan_boot, episode=1, loading=None, available_gib=19.6)


# Task 6's texts (test_messages.py's), which the gate's figures must give word for word.
NO_FIT_DAN_TEXT = (
    "The coder didn't load: it needs 41 GiB, and 18 GiB is free for a load (48 GiB available, less the 24 GiB reserve "
    "and the 6 GiB the loaded models may still grow into). Using memory now: python3 (chendaniely) 32 GiB, Gemma "
    "27 GiB. Free space with `spark make-room 41G` on the Spark, then try again.")
NO_FIT_DAN_BREAKDOWN = ("18 GiB is free for a load (48 GiB available, less the 24 GiB reserve and the 6 GiB the loaded "
                        "models may still grow into)")
NO_FIT_AGENT_TEXT = (
    "The coder didn't load: it needs 41 GiB, and 12 GiB is free for a load (106 GiB available, less the 24 GiB "
    "reserve and the 70 GiB make-room holds for Dan). Using memory now: the embeddings 8 GiB, whisper 3 GiB. The "
    "hold ends when Dan runs `spark make-room --done` on the Spark.")
NO_FIT_CLAMPED_TEXT = (
    "The coder didn't load: it needs 41 GiB, and nothing is free for a load while make-room holds 70 GiB for Dan "
    "(36 GiB available, less the 24 GiB reserve). Using memory now: a process of Dan's, 70 GiB, the embeddings 8 GiB. "
    "Only Dan can free memory for it, on the Spark; try again after that.")
LOADING_TEXT = (
    "The coder didn't start in time: it was waiting its turn while Gemma loads, since one model loads at a time, and "
    "your 30 s ran out. Try again in a minute.")
HELD_TEXT = (
    "Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads are paused. They resume "
    "by themselves after 5 minutes above 28 GiB available, if what would reload fits; on the Spark, "
    "`make brake-release` resumes them now.")
SUSPECT_TEXT = (
    "Not loading the coder for agent: it was loading when the brake fired at 03:12, so only Dan can load it again: "
    "`spark load coder` on the Spark, or one of Dan's requests from pi on the Mac or the web UI, which loads it if "
    "it fits.")
LOAD_FAILED_TEXT = (
    'The coder started loading but failed: the engine stopped with "failed to load model". On the Spark, '
    "`spark logs coder` shows the engine's last lines.")
DEADLINE_TEXT = ("The coder started loading but didn't finish within 3 minutes. On the Spark, `spark logs coder` "
                 "shows the engine's last lines.")
RESTARTING_TEXT = (
    "The model service on the Spark is restarting for a configuration change, and your 30 s ran out. Try again in a "
    "minute.")
DRAINING_TEXT = "The coder is being unloaded, and your 30 s ran out. Try again in a minute."
LLAMA_SWAP_DOWN_TEXT = (
    "The model service on the Spark isn't answering, and your 30 s ran out. Your phone has the alert; on the Spark, "
    "`make doctor` shows what's wrong.")


def refused(asked: Asked, code: str) -> str:
    """The refusal `asked` came to, which must be `code`'s; its message."""
    result = asked.result
    assert isinstance(result, Refusal), result
    assert result.code == code, (result.code, result.message)
    return result.message


# The tests.

@pytest.mark.anyio
async def test_a_model_that_fits_loads_once_with_a_ticket(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        seen = {}
        gate.swap.observe = lambda model: seen.update(ticket=gate.ticket(model), issued=dict(gate.state.issued),
                                                       saved=gate.saves[-1][0] if gate.saves else None)
        asked = gate.ask(PI, CODER)
        await settle()
        assert gate.swap.loads() == [CODER]
        ticket = seen["ticket"]
        # The footprint rule 9 admitted, never the cold load the registry has (33), MemAvailable then, and this boot.
        assert (ticket["model"], ticket["deadline"], ticket["footprint_gib"], ticket["available_gib"],
                ticket["boot_id"]) == (CODER, T + LOAD_CALL_TIMEOUT_S, 41.0, 74.0, BOOT)
        assert REGISTRY.models[CODER].cold_load_gib == 33
        # Its id went into state.issued, and was saved, before the load call.
        assert seen["issued"] == seen["saved"] == {CODER: ticket["id"]}
        assert gate.state.models[CODER].state == "starting"
        gate.available = 44.0
        gate.clock.now = T + 24
        gate.swap.ready(CODER)
        await settle()
        assert asked.result == Admitted(CODER, True, 24.0)
        assert gate.swap.loads() == [CODER]
        model = gate.state.models[CODER]
        assert (model.state, model.footprint_gib, model.load_fall_gib, model.fall_flagged) == ("ready", 41.0, 30.0,
                                                                                               False)
        assert gate.ticket(CODER) is None  # used up by launch's claim


@pytest.mark.anyio
async def test_a_loaded_model_is_admitted_at_once(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER, CODER) | {CODER: record(CODER)})
        asked = gate.ask(AGENT, CODER)
        await settle()
        assert asked.result == Admitted(CODER, False, None)
        assert gate.swap.loads() == [] and gate.ticket(CODER) is None and gate.emitted.sent == []


@pytest.mark.anyio
async def test_one_load_at_a_time(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(WHISPER), available=114)
        first, second = gate.ask(PI, CODER), gate.ask(PI, GEMMA)
        await settle()
        assert gate.swap.loads() == [CODER]  # Gemma fits too, but waits for the slot
        await gate.at(T + 20)
        assert gate.swap.loads() == [CODER] and not second.done
        gate.available = 73.0
        gate.swap.ready(CODER)
        await settle()
        assert first.result.model == CODER
        assert gate.swap.loads() == [CODER, GEMMA]  # only once the first call returned


@pytest.mark.anyio
@pytest.mark.parametrize("third_group", [False, True])
async def test_dans_keys_go_ahead_of_agents_and_first_come_within_each(tmp_path, third_group):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER), available=106)
        holding = gate.ask(None, GEMMA, kind="command", deadline=T + 200)
        await settle()
        assert gate.swap.loads() == [GEMMA]
        for key, model in [(AGENT, "model-a"), (PI, "model-b"), (AGENT, "model-c"), (PI, "model-d")] + (
                [(WEB_KEY, "model-e")] if third_group else []):
            gate.ask(key, model)
            await settle()
        order = ["model-b", "model-d"] + (["model-e"] if third_group else []) + ["model-a", "model-c"]
        for model in [GEMMA] + order[:-1]:
            gate.swap.ready(model)
            await settle()
        gate.swap.ready(order[-1])
        await settle()
        assert holding.result.model == GEMMA
        assert gate.swap.loads() == [GEMMA] + order


@pytest.mark.anyio
async def test_the_first_request_that_fits_takes_the_slot(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=60)  # 36 free for a load: the coder can't
        dans = gate.ask(PI, CODER)
        agents = gate.ask(AGENT, EMBED)
        later = gate.ask(AGENT, "model-a")
        await settle()
        assert gate.swap.loads() == [EMBED]  # the first in the queue that fits
        gate.available = 117.0  # memory frees while the embeddings load
        gate.swap.ready(EMBED)
        await settle()
        assert agents.result.model == EMBED
        assert gate.swap.loads() == [EMBED, CODER]  # Dan's kept its place, ahead of agent's later one
        gate.swap.ready(CODER)
        await settle()
        assert dans.result.model == CODER and gate.swap.loads() == [EMBED, CODER, "model-a"] and not later.done


@pytest.mark.anyio
async def test_a_residents_reload_goes_ahead_of_dans_request(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(WHISPER), available=114)
        gate.ask(AGENT, "model-a")
        await settle()
        dans = gate.ask(PI, CODER)
        await settle()
        reload = gate.admitter.reload(GEMMA, "boot")
        await settle()
        gate.swap.ready("model-a")
        await settle()
        assert gate.swap.loads() == ["model-a", GEMMA]
        gate.swap.ready(GEMMA)
        await settle()
        assert (await reload) == Admitted(GEMMA, True, 0.0)
        assert gate.swap.loads() == ["model-a", GEMMA, CODER] and not dans.done


@pytest.mark.anyio
async def test_a_reload_counts_every_hold_and_waits_without_a_deadline(tmp_path):
    with anyio.fail_after(BOUND_S):
        # make-room's hold: Gemma fits only inside it (66 free for a load, 26 with the hold counted).
        gate = Gate(tmp_path / "room", models=loaded(EMBED, WHISPER), available=90)
        gate.state.room_hold = RoomHold(size_gib=40.0, until=None, boot_id=BOOT, created_at=T - 60, unloaded=[])
        reload = gate.admitter.reload(GEMMA, "boot")
        for minute in range(0, 60, 10):
            await gate.at(T + 60 * minute)
        assert gate.swap.loads() == [] and not reload.done()
        assert [gate.emitted.text("resident_waiting", fields) for _, fields in gate.emitted.of("resident_waiting")] == [
            "Gemma didn't fit at boot: it needs 32 GiB, and 26 GiB is free for a load. It loads by itself once "
            "there's room."]
        gate.state.room_hold = None
        await gate.at(T + 3600)
        assert gate.swap.loads() == [GEMMA]
        gate.swap.ready(GEMMA)
        await settle()
        assert (await reload).model == GEMMA

        # The brake's hold: the same, with room to spare; it isn't a fit, so no resident_waiting.
        gate = Gate(tmp_path / "brake", models=loaded(EMBED, WHISPER), available=106)
        gate.hold = hold_at_0312()
        reload = gate.admitter.reload(GEMMA, "brake")
        for minute in range(0, 60, 10):
            await gate.at(T + 60 * minute)
        assert gate.swap.loads() == [] and not reload.done() and gate.emitted.of("resident_waiting") == []
        gate.hold = None
        await gate.at(T + 3600)
        assert gate.swap.loads() == [GEMMA]


@pytest.mark.anyio
async def test_the_groups_parts_decide_apart(tmp_path):
    with anyio.fail_after(BOUND_S):
        # uses_hold: it loads into Dan's hold, which it doesn't count (82 free for a load, 12 with the hold).
        gate = Gate(tmp_path / "hold", models=loaded(EMBED, WHISPER), available=106)
        gate.state.room_hold = RoomHold(size_gib=70.0, until=None, boot_id=BOOT, created_at=T - 60, unloaded=[])
        asked = gate.ask(MIXED_KEY, CODER)
        await settle()
        assert gate.swap.loads() == [CODER]
        gate.swap.ready(CODER)
        await settle()
        assert asked.result.model == CODER

        # No reloads_marked: footprint_suspect for the model the brake marked, though it fits.
        gate = Gate(tmp_path / "mark", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.state.brake_marks[CODER] = BrakeMark(CODER, AT_0312, 26.0)
        asked = gate.ask(MIXED_KEY, CODER)
        await gate.at(T + 30)
        assert refused(asked, "footprint_suspect") == SUSPECT_TEXT.replace("for agent", "for the mixed key")
        assert gate.swap.loads() == []


@pytest.mark.anyio
async def test_a_request_for_a_loading_model_joins_its_load_and_waits_past_its_wait(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        first = gate.ask(AGENT, CODER)
        await settle()
        await gate.at(T + 5)
        dans = gate.ask(PI, CODER)  # wait 30 s, to T + 35
        await settle()
        assert dans.progress == [{"kind": "loading", "model": CODER, "label": "the coder", "last_s": None}]
        for second in (35, 40, 49):
            await gate.at(T + second)
        assert not dans.done and gate.swap.loads() == [CODER]
        gate.clock.now = T + 50
        gate.swap.ready(CODER)
        await settle()
        assert dans.result == first.result == Admitted(CODER, True, 50.0)
        assert dans.finished_at == T + 50


@pytest.mark.anyio
async def test_no_fit_after_the_keys_wait_carries_the_moments_numbers(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The examples' moment: Gemma owed 6, 48 available, a python job of Dan's holding 32.
        moment = loaded(EMBED, WHISPER) | {GEMMA: record(GEMMA, fall=26.0)}
        gate = Gate(tmp_path / "moment", models=moment, available=48, holders=[PYTHON, Holder("Gemma", 27, False)])
        asked = gate.ask(PI, CODER)
        await gate.at(T + 29.9)
        assert not asked.done
        await gate.at(T + 30)
        assert refused(asked, "no_fit") == NO_FIT_DAN_TEXT
        assert (asked.result.status, asked.result.retry_after_s) == (409, 30)
        history = gate.state.refusals[-1]
        assert (history.at, history.model, history.key, history.code, history.message) == (
            T + 30, CODER, "mac-pi", "no_fit", NO_FIT_DAN_TEXT)
        [(key, fields)] = gate.emitted.of("refused")
        assert key == f"refusal:{asked.req.id}" and fields["code"] == "no_fit"
        assert fields["moment"].free_gib == Decimal(18)
        assert gate.swap.loads() == [] and gate.ticket(CODER) is None

        # Where the ceiling binds (92 of the 102 committed), the figure is the admitter's own, and so is the breakdown.
        gate = Gate(tmp_path / "ceiling", models=moment | {"big-model": record("big-model", 49.0)}, available=48,
                    holders=[PYTHON, Holder("Gemma", 27, False)])
        assert gate.admitter.free_for_a_load(uses_hold=True) == Decimal(10)
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "no_fit") == NO_FIT_DAN_TEXT.replace(
            NO_FIT_DAN_BREAKDOWN,
            "10 GiB is free for a load (the 102 GiB the GPU can allocate, less the 92 GiB the loaded models may grow "
            "to)")


@pytest.mark.anyio
async def test_room_appearing_within_the_wait_loads_the_model(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER) | {GEMMA: record(GEMMA, fall=26.0)}, available=48)
        asked = gate.ask(PI, CODER)
        await gate.at(T + 5)
        assert gate.swap.loads() == []
        gate.available = 90.0
        await gate.at(T + 10)
        assert gate.swap.calls == [("load", CODER, T + 10)]
        gate.swap.ready(CODER)
        await settle()
        assert asked.result.model == CODER


@pytest.mark.anyio
async def test_loading_means_the_wait_ran_out_in_the_queue_for_the_slot(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER), available=106)
        gate.admitter.reload(GEMMA, "boot")
        await settle()
        asked = gate.ask(PI, CODER)  # it fits, Gemma's 32 counted as starting: 50 free for a load
        await settle()
        assert gate.admitter.free_for_a_load(uses_hold=True) == Decimal(50)
        await gate.at(T + 30)
        assert refused(asked, "loading") == LOADING_TEXT
        gate.clock.now = T + 60
        gate.swap.ready(GEMMA)
        await settle()
        assert gate.swap.loads() == [GEMMA]


@pytest.mark.anyio
async def test_held_by_brake_while_the_hold_stands(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path / "held", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.hold = hold_at_0312()
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "held_by_brake") == HELD_TEXT
        assert gate.swap.loads() == []

        gate = Gate(tmp_path / "lifted", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.hold = hold_at_0312()
        asked = gate.ask(PI, CODER)
        await gate.at(T + 5)
        assert gate.swap.loads() == []
        gate.hold = None
        await gate.at(T + 10)
        assert gate.swap.calls == [("load", CODER, T + 10)]


@pytest.mark.anyio
async def test_held_by_brake_says_when_the_pause_waits_for_dan(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.hold = hold_at_0312(waits_for_dan_boot="b0")  # a hold from before the reboot
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "held_by_brake") == (
            "Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads stay paused until "
            "you release them: on the Spark, `make brake-release`.")


@pytest.mark.anyio
async def test_footprint_suspect_for_agent_and_dans_load_clears_the_mark(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.state.brake_marks[CODER] = BrakeMark(CODER, AT_0312, 26.0)
        agents = gate.ask(AGENT, CODER)
        await gate.at(T + 599)
        assert not agents.done
        await gate.at(T + 600)
        assert refused(agents, "footprint_suspect") == SUSPECT_TEXT
        [(key, fields)] = gate.emitted.of("footprint_suspect")
        assert key == f"refusal:{agents.req.id}"
        assert gate.emitted.text("footprint_suspect", fields) == (
            "Didn't load the coder for agent: it was loading when the brake fired at 03:12. On the Spark, `spark load "
            "coder` allows it again; your own requests load it if it fits.")
        [(_, waiting)] = gate.emitted.of("waiting")
        assert gate.emitted.text("waiting", waiting) == (
            "Waiting for you: the coder for agent, up to 10 min. It was loading when the brake fired; on the Spark, "
            "`spark load coder` allows it again.")
        assert gate.swap.loads() == []

        dans = gate.ask(PI, CODER)
        await settle()
        assert gate.swap.loads() == [CODER] and CODER in gate.state.brake_marks
        gate.swap.ready(CODER)
        await settle()
        assert dans.result.model == CODER and CODER not in gate.state.brake_marks


@pytest.mark.anyio
async def test_a_room_hold_counts_for_agent_and_not_for_dan(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER), available=106,
                    holders=[Holder("the embeddings", 8, False), Holder("whisper", 3, False)])
        gate.state.room_hold = RoomHold(size_gib=70.0, until=None, boot_id=BOOT, created_at=T - 60, unloaded=[])
        agents = gate.ask(AGENT, CODER)
        await gate.at(T + 600)
        assert refused(agents, "no_fit") == NO_FIT_AGENT_TEXT
        assert gate.swap.loads() == []
        dans = gate.ask(PI, CODER)
        await settle()
        assert gate.swap.loads() == [CODER]
        gate.swap.ready(CODER)
        await settle()
        assert dans.result.model == CODER


@pytest.mark.anyio
async def test_with_dans_job_in_the_hold_agent_reads_nothing_free(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER), available=36,
                    holders=[Holder("python3 (chendaniely)", 70, True), Holder("the embeddings", 8, False)])
        gate.state.room_hold = RoomHold(size_gib=70.0, until=None, boot_id=BOOT, created_at=T - 60, unloaded=[])
        assert gate.admitter.free_for_a_load(uses_hold=False) == Decimal(-58)
        agents = gate.ask(AGENT, CODER)
        await gate.at(T + 600)
        assert refused(agents, "no_fit") == NO_FIT_CLAMPED_TEXT


@pytest.mark.anyio
async def test_dans_load_shrinks_the_hold_by_what_outside_couldnt_cover(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER), available=74, registry=make_registry(coder_cold=None))
        gate.state.room_hold = RoomHold(size_gib=41.0, until=None, boot_id=BOOT, created_at=T - 60, unloaded=[])
        assert gate.admitter.free_for_a_load(uses_hold=True) == Decimal(50)
        coder = gate.ask(PI, CODER)
        await settle()
        assert gate.state.room_hold.size_gib == 41  # until the load is done
        gate.available = 33.0
        gate.swap.ready(CODER)
        await settle()
        assert coder.result.model == CODER
        assert gate.state.room_hold.size_gib == 9  # the 41 less what the 9 outside the hold couldn't cover
        nine = gate.ask(PI, "nine-model")
        await settle()
        gate.available = 24.0
        gate.swap.ready("nine-model")
        await settle()
        assert nine.result.model == "nine-model" and gate.state.room_hold is None
        [(key, fields)] = gate.emitted.of("room_hold_ended")
        assert key == f"room:{T - 60}"
        assert gate.emitted.text("room_hold_ended", fields) == (
            "make-room's hold for you ended: your own loads used it up. Nothing to reload.")


@pytest.mark.anyio
async def test_a_client_gone_leaves_the_queue_and_nothing_loads_for_it(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER) | {GEMMA: record(GEMMA, fall=26.0)}, available=48)
        cancelled = gate.ask(AGENT, CODER)
        dropped = gate.ask(AGENT, "model-a")  # 1 GiB, which fits: let it be the one whose call drops first
        gate.admitter.set_llamaswap_up(False)  # nothing loads yet
        await settle()
        assert [row["model"] for row in gate.admitter.waiting()] == [CODER, "model-a"]
        gate.admitter.cancel(cancelled.req.id)  # the front's dropped call, as the route takes it
        dropped.task.cancel()  # the route's own task, cancelled as its client goes
        await settle()
        assert cancelled.done and dropped.task.cancelled()
        assert gate.admitter.waiting() == []
        gate.available = 117.0
        gate.admitter.set_llamaswap_up(True)
        await gate.at(T + 10)
        assert gate.swap.loads() == [] and gate.ticket(CODER) is None and gate.ticket("model-a") is None


@pytest.mark.anyio
async def test_a_load_started_for_a_client_that_went_finishes(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(AGENT, CODER)
        await settle()
        assert gate.swap.loads() == [CODER]
        asked.task.cancel()
        await settle()
        assert asked.task.cancelled()
        gate.available = 40.0
        gate.swap.ready(CODER)
        await settle()
        assert gate.state.models[CODER].state == "ready" and gate.started() == []
        assert CODER in gate.state.ticketed


@pytest.mark.anyio
async def test_a_load_call_that_times_out_keeps_the_slot_until_running_says_ready_or_gone(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=82)
        coder = gate.ask(PI, CODER)
        await settle()
        embed = gate.ask(AGENT, EMBED)
        await settle()
        assert gate.started() == [f"{CODER}.json"]  # launch claimed the ticket
        gate.clock.now = T + 20
        gate.swap.drops(CODER, "dropped")
        await settle()
        assert gate.ticket(CODER) is None and gate.swap.states[CODER] == "starting"
        await gate.at(T + 25)
        assert gate.swap.loads() == [CODER] and not embed.done and not coder.done
        gate.available = 41.0
        gate.swap.states[CODER] = "ready"
        await gate.at(T + 30)
        assert coder.result == Admitted(CODER, True, 30.0)
        assert gate.swap.loads() == [CODER, EMBED]
        assert gate.state.models[CODER].load_fall_gib == 35.0  # 41 fell, capped at the cold load and its margin
        assert gate.state.models[CODER].fall_flagged


@pytest.mark.anyio
async def test_an_unknown_load_whose_ticket_was_unused_frees_the_slot(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=82)
        gate.swap.answer_next(CODER, LoadOutcome.unknown("refused"))
        tickets_at_call = []
        gate.swap.observe = lambda model: tickets_at_call.append(gate.ticket(model))
        gate.ask(PI, CODER)
        gate.ask(AGENT, EMBED)
        await settle()
        assert tickets_at_call[0]["model"] == CODER  # the call found it on disk, unclaimed
        assert gate.ticket(CODER) is None  # withdrawn
        assert gate.swap.loads() == [CODER, EMBED]  # the slot freed at once
        assert CODER not in gate.state.models and CODER not in gate.state.issued


def refusal_from_llama_swap(status: int) -> LoadOutcome:
    return LoadOutcome.failed(status, _envelope(status, "refused").decode())


SWAP_LOGS = "On the Spark, `make logs s=llama-swap` shows why."


@pytest.mark.anyio
@pytest.mark.parametrize("status, why", [(404, "its config may not match the registry"),
                                         (401, "it didn't accept the gate's key")])
async def test_a_404_or_401_from_llama_swap_is_refused_at_once(tmp_path, status, why):
    with anyio.fail_after(BOUND_S):
        # The controller's ruling at Task 15: llama-swap refusing the call is no failed start, and no retry helps.
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=82)
        gate.swap.answer_next(CODER, refusal_from_llama_swap(status))
        coder = gate.ask(PI, CODER)
        gate.ask(AGENT, EMBED)
        await settle()
        assert refused(coder, "load_failed") == (
            f"llama-swap refused to load the coder (HTTP {status}): {why}. {SWAP_LOGS}")
        assert gate.ticket(CODER) is None and gate.swap.loads() == [CODER, EMBED]  # withdrawn; the slot freed
        [(key, fields)] = gate.emitted.of("load_failed")
        assert gate.emitted.text("load_failed", fields) == (
            f"llama-swap refused to load the coder (HTTP {status}): {why}. {SWAP_LOGS}")
        await gate.at(T + 30)
        assert gate.swap.loads() == [CODER, EMBED]  # never tried again


@pytest.mark.anyio
@pytest.mark.parametrize("status", [429, 503])
async def test_a_429_or_503_retries_within_the_wait_then_says_llama_swap_kept_refusing(tmp_path, status):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.swap.answer_every(CODER, refusal_from_llama_swap(status))
        coder = gate.ask(PI, CODER)
        await settle()
        for second in (1, 3, 7, 15):  # each retry once its wait is over, 1 s doubling
            await gate.at(T + second - 0.1)
            assert len(gate.swap.loads()) == {1: 1, 3: 2, 7: 3, 15: 4}[second]
            await gate.at(T + second)
        assert [at for kind, _, at in gate.swap.calls] == [T, T + 1, T + 3, T + 7, T + 15]
        assert gate.ticket(CODER) is None  # each withdrawn, unused
        await gate.at(T + 30)
        assert refused(coder, "load_failed") == (
            f"llama-swap kept refusing to load the coder, and your 30 s ran out. {SWAP_LOGS}")
        [(_, fields)] = gate.emitted.of("load_failed")
        assert gate.emitted.text("load_failed", fields) == (
            f"llama-swap kept refusing to load the coder (HTTP {status}). {SWAP_LOGS}")
        # llama-swap answered every time: nothing says it isn't answering.
        assert all(r.code != "llama_swap_down" for r in gate.state.refusals)
        assert gate.emitted.of("llama_swap_down") == []


@pytest.mark.anyio
async def test_a_load_llama_swap_refused_once_loads_on_its_retry(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.swap.answer_next(CODER, refusal_from_llama_swap(503))
        coder = gate.ask(PI, CODER)
        await settle()
        assert gate.swap.loads() == [CODER] and not coder.done
        await gate.at(T + 1)
        assert gate.swap.loads() == [CODER, CODER]
        gate.available = 40.0
        gate.swap.ready(CODER)
        await settle()
        assert coder.result.model == CODER


@pytest.mark.anyio
async def test_a_claimed_start_that_leaves_running_unready_is_a_failed_start(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        dans, agents = gate.ask(PI, CODER), gate.ask(AGENT, CODER)
        await settle()
        gate.swap.drops(CODER, "dropped")  # launch had claimed the ticket
        await settle()
        gate.swap.states[CODER] = "stopped"
        await gate.at(T + 40)
        assert refused(dans, "load_failed") == (
            "The coder's start stopped before it was ready: `spark logs coder` on the Spark shows why.")
        assert refused(agents, "load_failed") == (
            "The coder's start stopped before it was ready: Dan can see why with `spark logs coder` on the Spark.")
        [(key, fields)] = gate.emitted.of("load_failed")
        assert key.startswith("load-failed:") and fields["start_ended"] is True


@pytest.mark.anyio
async def test_a_load_starting_past_its_deadline_and_20_s_is_unloaded(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=82)
        gate.swap.claim_late.add(CODER)
        coder = gate.ask(PI, CODER)
        await settle()
        gate.clock.now = T + 50
        gate.swap.claim(CODER)  # launch's start, 50 s after the ticket was issued
        embed = gate.ask(AGENT, EMBED)
        await settle()
        gate.clock.now = T + 200
        gate.swap.drops(CODER, "timeout")
        await settle()
        saved_at_unload = []
        original = gate.swap.unload

        async def unload(model):
            saved_at_unload.append(gate.saves[-1][1][model])
            await original(model)

        gate.swap.unload = unload
        # Healthy at the ticket's deadline plus 20 s: its own start's deadline is 30 s later.
        await gate.at(T + LOAD_CALL_TIMEOUT_S + 20)
        assert gate.swap.unloads() == [] and not coder.done and not embed.done
        await gate.at(T + 50 + HEALTH_CHECK_TIMEOUT_S + 20 - 0.1)
        assert gate.swap.unloads() == []
        await gate.at(T + 50 + HEALTH_CHECK_TIMEOUT_S + 20)
        assert gate.swap.unloads() == [CODER]
        [before] = saved_at_unload  # saved before the call, so a restarted gate resumes it as what it was
        assert before.unload_requested_at == T + 250 and before.drain_why == "late_start" and before.drain_id is None
        await settle()
        assert refused(coder, "load_failed") == DEADLINE_TEXT
        [(key, fields)] = gate.emitted.of("unloaded")
        assert gate.emitted.text("unloaded", fields) == "Unloaded the coder: its start ran past its deadline."
        assert gate.swap.loads() == [CODER, EMBED]  # the slot freed
        assert CODER not in gate.state.models and CODER not in gate.state.issued
        assert gate.started() == [f"{EMBED}.json"]  # the coder's record cleared; the embeddings' load is under way


@pytest.mark.anyio
async def test_a_start_with_no_record_is_unloaded_20_s_past_its_tickets_deadline(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=82)
        gate.swap.claim_late.add(CODER)
        coder = gate.ask(PI, CODER)
        await settle()
        # launch claimed the ticket and died before it recorded the start: no started/ record.
        assert tickets.claim(gate.launch, CODER, T + 1, boot_id=BOOT).ok
        gate.clock.now = T + 100
        gate.swap.drops(CODER, "dropped")
        await settle()
        await gate.at(T + LOAD_CALL_TIMEOUT_S + 20 - 0.1)
        assert gate.swap.unloads() == []
        await gate.at(T + LOAD_CALL_TIMEOUT_S + 20)
        assert gate.swap.unloads() == [CODER] and refused(coder, "load_failed") == DEADLINE_TEXT


@pytest.mark.anyio
async def test_a_started_record_of_another_ticket_is_never_ticketed(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(PI, CODER)
        await settle()
        # An engine runs as spark and can write started/: a record whose id the gate never issued hides nothing.
        path = tickets.record_path(gate.launch, tickets.STARTED, CODER)
        tickets.write_whole(path, json.loads(path.read_text()) | {"id": "forged"})
        gate.available = 40.0
        gate.swap.ready(CODER)
        await settle()
        assert asked.result.model == CODER
        assert CODER not in gate.state.ticketed and gate.started() == [] and CODER not in gate.state.issued


@pytest.mark.anyio
async def test_stop_cancels_the_load_tasks_and_the_waiting_requests(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER) | {EMBED: record(EMBED)}, available=82)
        loading = gate.ask(PI, CODER)
        await settle()
        waiting = gate.ask(AGENT, "model-a")
        await settle()
        assert gate.swap.loads() == [CODER] and not waiting.done
        await gate.admitter.stop()
        await settle()
        assert loading.task.cancelled() and waiting.task.cancelled()
        assert gate.admitter.waiting() == []
        gate.admitter.changed()  # a stopped admitter starts nothing more
        await settle()
        assert gate.swap.loads() == [CODER]


@pytest.mark.anyio
async def test_a_request_whose_model_left_the_registry_never_holds_up_the_queue(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.admitter.set_llamaswap_up(False)
        gone = gate.ask(PI, "model-a")
        later = gate.ask(PI, CODER)
        await settle()
        # The registry read again (an apply's end) without model A.
        gate.admitter.registry = replace(REGISTRY, models={n: m for n, m in REGISTRY.models.items() if n != "model-a"})
        gate.admitter.set_llamaswap_up(True)
        await settle()
        assert isinstance(gone.task.exception(), ValueError)
        assert gate.swap.loads() == [CODER] and not later.done


@pytest.mark.anyio
async def test_a_late_start_that_turns_ready_before_its_unload_is_kept(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=82)
        coder = gate.ask(PI, CODER)
        await settle()
        gate.clock.now = T + 100
        gate.swap.drops(CODER, "dropped")
        await settle()
        await gate.at(T + 150)
        # Past its start's deadline (claimed at T, so T + 200), but /running, read right before the unload, shows it
        # ready: it is kept.
        gate.swap.states[CODER] = "ready"
        gate.available = 50.0
        await gate.at(T + 201)
        assert gate.swap.unloads() == [] and coder.result.model == CODER


@pytest.mark.anyio
async def test_load_failed_carries_launchs_record_or_the_engines_last_lines(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path / "record", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(PI, CODER)
        await settle()
        gate.clock.now = T + 5
        # launch claimed the ticket, then refused, leaving its record as launch.record_refusal does.
        tickets.write_whole(gate.launch / "refusals" / f"{CODER}.json", {
            "at": datetime.fromtimestamp(T + 5).isoformat(timespec="seconds"), "model": CODER,
            "code": "not_downloaded", "reason": "failed to load model"})
        gate.swap.exits(CODER)
        await settle()
        assert refused(asked, "load_failed") == LOAD_FAILED_TEXT
        [(key, fields)] = gate.emitted.of("load_failed")
        assert key.startswith("load-failed:") and gate.emitted.text("load_failed", fields) == (
            'The coder failed to load: the engine stopped with "failed to load model". On the Spark, '
            "`spark logs coder` shows the engine's last lines.")

        gate = Gate(tmp_path / "lines", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.swap.lines[CODER] = ["llama_model_load: error loading model", "failed to load model"]
        asked = gate.ask(PI, CODER)
        await settle()
        gate.swap.exits(CODER)
        await settle()
        assert refused(asked, "load_failed") == LOAD_FAILED_TEXT


@pytest.mark.anyio
async def test_a_stale_launch_record_is_never_taken_for_this_load(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        tickets.write_whole(gate.launch / "refusals" / f"{CODER}.json", {
            "at": datetime.fromtimestamp(T - 86400).isoformat(timespec="seconds"), "model": CODER,
            "code": "not_downloaded", "reason": "an old refusal"})
        gate.swap.lines[CODER] = ["failed to load model"]
        asked = gate.ask(PI, CODER)
        await settle()
        gate.swap.exits(CODER)
        await settle()
        assert refused(asked, "load_failed") == LOAD_FAILED_TEXT


@pytest.mark.anyio
async def test_a_load_past_its_deadline_says_so(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.swap.lines[CODER] = ["still loading tensors"]
        asked = gate.ask(PI, CODER)
        await settle()
        gate.swap.times_out(CODER)
        await settle()
        assert refused(asked, "load_failed") == DEADLINE_TEXT


@pytest.mark.anyio
async def test_a_failed_load_refuses_each_request_that_joined_it_and_sends_one_alert(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.swap.lines[CODER] = ["failed to load model"]
        dans, agents = gate.ask(PI, CODER), gate.ask(AGENT, CODER)
        await settle()
        assert gate.swap.loads() == [CODER]
        gate.swap.exits(CODER)
        await settle()
        assert refused(dans, "load_failed") == LOAD_FAILED_TEXT
        assert refused(agents, "load_failed") == LOAD_FAILED_TEXT.replace(
            "On the Spark, `spark logs coder` shows the engine's last lines.",
            "Dan can see why with `spark logs coder` on the Spark.")
        assert len(gate.emitted.of("load_failed")) == 1
        assert [r.key for r in gate.state.refusals] == ["mac-pi", "agent"]
        assert CODER not in gate.state.models and CODER not in gate.state.issued


@pytest.mark.anyio
async def test_an_aborted_load_is_worded_by_its_cause(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The brake's unload aborts it: held_by_brake, never load_failed.
        gate = Gate(tmp_path / "brake", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(PI, CODER)
        await settle()
        gate.clock.now = T + 40
        gate.hold = hold_at_0312()  # the brake writes its hold, then unloads
        await gate.swap.unload(CODER)
        await settle()
        assert refused(asked, "held_by_brake") == HELD_TEXT
        assert gate.emitted.of("load_failed") == [] and CODER not in gate.state.models

        # Another unload aborts it, no hold standing: the model was being unloaded, never llama_swap_down, since
        # llama-swap answered.
        gate = Gate(tmp_path / "unload", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(PI, CODER)
        await settle()
        gate.clock.now = T + 40
        await gate.swap.unload(CODER)
        await settle()
        assert refused(asked, "draining") == DRAINING_TEXT

        # llama-swap shutting down: restarting while apply restarts it, llama_swap_down otherwise.
        for restarting, code in ((True, "restarting"), (False, "llama_swap_down")):
            gate = Gate(tmp_path / code, models=loaded(GEMMA, EMBED, WHISPER), available=74)
            asked = gate.ask(PI, CODER)
            await settle()
            gate.admitter.set_restarting(restarting)
            gate.clock.now = T + 40
            gate.swap.shut_down()
            await settle()
            assert refused(asked, code) == (RESTARTING_TEXT if restarting else LLAMA_SWAP_DOWN_TEXT)
            assert gate.emitted.of("load_failed") == [] and gate.swap.loads() == [CODER]


@pytest.mark.anyio
async def test_a_request_for_a_model_being_unloaded_waits_and_says_so(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The controller's ruling at Task 15: `draining` is a reason of its own, with its own line and words.
        going = record(CODER, state="draining", unload_requested_at=T - 5, drain_id="d1", drain_why="idle")
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER) | {CODER: going}, available=74)
        command = gate.ask(None, CODER, kind="command", deadline=T + LOAD_CALL_TIMEOUT_S)
        key = gate.ask(PI, CODER)
        await gate.at(T + 10)
        assert command.progress == [dict.fromkeys(gateproto.WaitProgress.__annotations__) | {
            "kind": "waiting", "model": CODER, "label": "the coder", "why": "draining"}]
        [(wait_key, fields)] = gate.emitted.of("waiting")
        assert wait_key == f"wait:{key.req.id}" and gate.emitted.text("waiting", fields) == (
            "Waiting while the coder finishes unloading, for pi on the Mac, up to 30 s. It loads again once the unload "
            "is done, if it fits.")
        assert [row["why"] for row in gate.admitter.waiting()] == ["draining", "draining"]
        await gate.at(T + 30)
        assert refused(key, "draining") == DRAINING_TEXT
        del gate.state.models[CODER]  # the drain's unload done (Task 16), then a change
        await gate.at(T + 40)
        assert gate.swap.loads() == [CODER] and command.progress[-1]["kind"] == "loading"


@pytest.mark.anyio
async def test_a_brake_hold_without_its_reading_is_worded_as_seen(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The controller's ruling at Task 15: a hold that doesn't say when it fired or how low memory ran is worded
        # as the gate saw it, never as fired, with no reading the hold didn't record.
        gate = Gate(tmp_path / "before", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.hold = Hold(since="2026-10-08T03:12:00 or so", reason="malformed", unloaded=(GEMMA,))
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "held_by_brake") == (
            "Not loading the coder now: new loads are paused by a brake hold from before the gate started (seen at "
            "09:00); they stay paused until you release them: on the Spark, `make brake-release`.")

        # Phase 1's hold has its time but no reading: dated by its time, whenever the gate saw it (the controller's
        # ruling at Task 15's review).
        gate = Gate(tmp_path / "phase1", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.ask(PI, GEMMA)  # a first look, with no hold
        await settle()
        gate.hold = Hold(since=datetime.fromtimestamp(AT_0312).strftime("%Y-%m-%dT%H:%M:%S"), reason="Phase 1's",
                         unloaded=(GEMMA,))  # Phase 1's: no boot, no reading
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "held_by_brake") == (
            "Not loading the coder now: new loads are paused by a brake hold from 03:12 (no reading recorded); they "
            "stay paused until you release them: on the Spark, `make brake-release`.")

        from spark.hold import UNREADABLE_SINCE
        gate = Gate(tmp_path / "later", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.ask(PI, GEMMA)  # a first look, with no hold
        await gate.at(T + 60)
        gate.hold = Hold(UNREADABLE_SINCE, "hold file can't be read", ())  # read_hold's for a file it can't read
        asked = gate.ask(PI, CODER)
        await gate.at(T + 120)
        await gate.at(T + 150)
        assert refused(asked, "held_by_brake") == (
            "Not loading the coder now: new loads are paused by a brake hold the gate can't date (seen at 09:02); "
            "they stay paused until you release them: on the Spark, `make brake-release`.")


UNCONFIRMED_TEXT = ("The gate couldn't confirm the coder's load ticket was withdrawn; on the Spark, `make doctor` "
                    "shows why.")


def withdraw_fails(monkeypatch, times: int | None = None) -> list[int]:
    """tickets.withdraw raising OSError, `times` times (None: every time), then as it is; the calls counted."""
    real, calls = tickets.withdraw, []

    def withdraw(folder, model):
        calls.append(1)
        if times is None or len(calls) <= times:
            raise OSError(5, "Input/output error")
        return real(folder, model)

    monkeypatch.setattr(tickets, "withdraw", withdraw)
    return calls


@pytest.mark.anyio
async def test_a_restarted_gate_withdraws_every_ticket_it_has_no_load_for(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The review's I-1: the gate died between issuing a ticket and the start's `starting`, so restore dropped the
        # model and its id; the ticket must not stay live for a start that comes later.
        launch = tmp_path / "launch"
        launch.mkdir()
        tickets.issue(launch, CODER, T - 5 + LOAD_CALL_TIMEOUT_S, now=T - 5, footprint_gib=41.0, available_gib=74.0,
                      boot_id=BOOT)
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        await gate.at(T)
        claim = tickets.claim(gate.launch, CODER, T + 1, boot_id=BOOT)
        assert not claim.ok and claim.code == tickets.NO_TICKET


@pytest.mark.anyio
async def test_a_load_that_goes_withdraws_its_ticket_first(tmp_path):
    with anyio.fail_after(BOUND_S):
        # A load a restarted gate found starting holds a live ticket the constructor leaves alone; when the model goes,
        # the ticket goes too.
        launch = tmp_path / "launch"
        launch.mkdir()
        ticket_id = tickets.issue(launch, CODER, T + 100, now=T - 100, footprint_gib=41.0, available_gib=74.0,
                                  boot_id=BOOT)
        state = GateState(boot_id=BOOT, issued={CODER: ticket_id})
        state.models = loaded(GEMMA, EMBED, WHISPER) | {CODER: record(CODER, state="starting")}
        gate = Gate(tmp_path, available=74, state=state)
        assert gate.ticket(CODER) is not None
        await gate.at(T)  # /running doesn't list it: gone
        assert gate.ticket(CODER) is None and CODER not in gate.state.models


@pytest.mark.anyio
async def test_a_withdraw_that_fails_once_is_tried_again(tmp_path, monkeypatch, caplog):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=82)
        calls = withdraw_fails(monkeypatch, times=1)
        gate.swap.answer_next(CODER, LoadOutcome.unknown("refused"))
        gate.ask(PI, CODER)
        gate.ask(AGENT, EMBED)
        await settle()
        assert len(calls) == 2 and gate.ticket(CODER) is None
        assert gate.swap.loads() == [CODER, EMBED]  # the second try withdrew it: no start used it, the slot is free


@pytest.mark.anyio
async def test_a_ticket_that_cant_be_withdrawn_holds_the_slot_until_its_deadline_and_says_so(tmp_path, monkeypatch,
                                                                                               caplog):
    with anyio.fail_after(BOUND_S):
        # Never read as claimed: nothing showed in /running, so no request is told a start stopped. The slot is held
        # until the ticket can no longer be claimed (launch refuses deadline < now).
        gate = Gate(tmp_path / "deadline", models=loaded(GEMMA, WHISPER), available=82)
        withdraw_fails(monkeypatch)
        gate.swap.answer_next(CODER, LoadOutcome.unknown("refused"))
        coder = gate.ask(PI, CODER)
        embed = gate.ask(AGENT, EMBED)
        await settle()
        for second in (30, 100, LOAD_CALL_TIMEOUT_S):
            await gate.at(T + second)
            assert gate.swap.loads() == [CODER] and not coder.done and not embed.done
        await gate.at(T + LOAD_CALL_TIMEOUT_S + 0.5)
        assert refused(coder, "load_failed") == UNCONFIRMED_TEXT
        assert gate.swap.loads() == [CODER, EMBED]
        assert any("withdrawn" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
        caplog.clear()

        # The model shows in /running meanwhile: the ticket was used, and the load is settled as a start.
        gate = Gate(tmp_path / "shown", models=loaded(GEMMA, WHISPER), available=82)
        gate.swap.claim_late.add(CODER)
        coder = gate.ask(PI, CODER)
        await settle()
        gate.swap.drops(CODER, "dropped")  # /running shows it starting
        await settle()
        gate.clock.now = T + 20
        gate.swap.claim(CODER)
        gate.swap.states[CODER] = "ready"
        gate.available = 41.0
        await gate.at(T + 21)
        assert coder.result.model == CODER
        caplog.clear()


@pytest.mark.anyio
async def test_a_start_that_goes_with_a_ticket_left_it_cant_withdraw_holds_the_slot(tmp_path, monkeypatch, caplog):
    with anyio.fail_after(BOUND_S):
        # Launch claimed its ticket, so the request hears the start stopped; the gate can't withdraw what is left in
        # tickets/, so the slot waits until that can no longer be claimed.
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=82)
        coder = gate.ask(PI, CODER)
        await settle()
        gate.swap.drops(CODER, "dropped")
        await settle()
        withdraw_fails(monkeypatch)
        embed = gate.ask(AGENT, EMBED)
        gate.swap.states[CODER] = "stopped"
        await gate.at(T + 40)
        assert refused(coder, "load_failed") == (
            "The coder's start stopped before it was ready: `spark logs coder` on the Spark shows why.")
        await gate.at(T + LOAD_CALL_TIMEOUT_S)
        assert gate.swap.loads() == [CODER] and not embed.done
        await gate.at(T + LOAD_CALL_TIMEOUT_S + 0.5)
        assert gate.swap.loads() == [CODER, EMBED]
        caplog.clear()


@pytest.mark.anyio
async def test_a_ticket_that_cant_be_written_is_refused_as_the_gates_own_error(tmp_path, monkeypatch, caplog):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)

        def full(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(tickets, "issue", full)
        asked = gate.ask(PI, CODER)
        await settle()
        assert refused(asked, "load_failed") == (
            "The gate couldn't write the coder's load ticket: `make doctor` on the Spark shows why.")
        assert gate.swap.loads() == [] and CODER not in gate.state.models and gate.state.issued == {}
        [(_, fields)] = gate.emitted.of("load_failed")
        assert fields["ticket_unwritten"] is True
        caplog.clear()


@pytest.mark.anyio
async def test_a_starting_record_with_no_load_holds_the_slot(tmp_path):
    with anyio.fail_after(BOUND_S):
        # What restore leaves when a late unload's first save landed: `starting`, its unload requested, and no _Load
        # of the admitter's (the review's I-2a). No second start runs beside it.
        going = record(CODER, state="starting", unload_requested_at=T - 5, drain_why="late_start")
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER) | {CODER: going}, available=82)
        embed = gate.ask(AGENT, EMBED)
        await gate.at(T + 5)
        assert gate.swap.loads() == [] and not embed.done
        del gate.state.models[CODER]  # the drainer saw it gone
        await gate.at(T + 10)
        assert gate.swap.loads() == [EMBED]


@pytest.mark.anyio
async def test_a_model_stopping_still_counts_against_the_ceiling(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Its memory isn't freed until /running shows it gone (the review's I-2b): the ceiling counts it.
        moment = loaded(EMBED, WHISPER) | {GEMMA: record(GEMMA, fall=26.0),
                                            "big-model": record("big-model", 49.0, state="stopping")}
        gate = Gate(tmp_path, models=moment, available=48, holders=[PYTHON, Holder("Gemma", 27, False)])
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "no_fit") == NO_FIT_DAN_TEXT.replace(
            NO_FIT_DAN_BREAKDOWN,
            "10 GiB is free for a load (the 102 GiB the GPU can allocate, less the 92 GiB the loaded models may grow "
            "to)")


@pytest.mark.anyio
async def test_a_model_being_unloaded_is_still_owed_its_growth(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Draining or stopping, it still holds what it held (the review's I-2c).
        for state in ("draining", "stopping"):
            gate = Gate(tmp_path / state, models={GEMMA: record(GEMMA, fall=25.0, state=state)})
            assert gate.admitter.owed() == Decimal(7)


@pytest.mark.anyio
async def test_a_wait_after_llama_swap_refused_says_so(tmp_path):
    with anyio.fail_after(BOUND_S):
        # The review's I-3: waiting to try again, never "waiting for the model service to answer" while it answers.
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.swap.answer_next(CODER, refusal_from_llama_swap(429))
        command = gate.ask(None, CODER, kind="command", deadline=T + LOAD_CALL_TIMEOUT_S)
        key = gate.ask(PI, CODER)
        await settle()
        assert command.progress[-1] == dict.fromkeys(gateproto.WaitProgress.__annotations__) | {
            "kind": "waiting", "model": CODER, "label": "the coder", "why": "swap_refused"}
        assert [row["why"] for row in gate.admitter.waiting()] == ["swap_refused", "swap_refused"]
        assert gate.emitted.of("waiting") == []  # no notification: the phone gets load_failed if it lasts
        await gate.at(T + 1)
        assert gate.swap.loads() == [CODER, CODER] and not key.done


@pytest.mark.anyio
async def test_a_start_another_unload_ended_is_worded_by_that_unload(tmp_path):
    with anyio.fail_after(BOUND_S):
        # make-room's drain unloaded it while the gate waited for it to be ready (Minor 3 of Task 15's review): the
        # requests read `draining`, worded by make-room, never a start that stopped by itself.
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(PI, CODER)
        await settle()
        gate.swap.drops(CODER, "dropped")
        await settle()
        model = gate.state.models[CODER]
        model.state, model.unload_requested_at, model.drain_id, model.drain_why = "draining", T + 10, "d7", "make-room"
        gate.swap.states[CODER] = "stopped"
        await gate.at(T + 40)
        assert refused(asked, "draining") == (
            "The coder is being unloaded for make-room, and your 30 s ran out. Try again in a minute: your request can "
            "load it again, into the room make-room holds for you, if it fits.")
        assert gate.emitted.of("load_failed") == [] and CODER not in gate.state.models


@pytest.mark.anyio
async def test_not_downloaded_is_refused_before_a_ticket(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        source = REGISTRY.models[CODER].source
        (gate.hf_home / Path(model_path(source, source.file)).relative_to(HF_HOME)).unlink()
        asked = gate.ask(PI, CODER)
        await settle()
        assert refused(asked, "not_downloaded") == (
            "The coder isn't downloaded yet. On the Spark, `make pull` fetches it.")
        assert gate.ticket(CODER) is None and gate.swap.loads() == [] and gate.state.issued == {}
        assert [fields["code"] for _, fields in gate.emitted.of("refused")] == ["not_downloaded"]


@pytest.mark.anyio
async def test_restarting_during_applys_restart_window(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path / "held", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.admitter.set_restarting(True)
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "restarting") == RESTARTING_TEXT and gate.swap.loads() == []

        gate = Gate(tmp_path / "ended", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.admitter.set_restarting(True)
        asked = gate.ask(PI, CODER)
        await gate.at(T + 5)
        assert gate.swap.loads() == []
        gate.clock.now = T + 10
        gate.admitter.set_restarting(False)
        await settle()
        assert gate.swap.calls == [("load", CODER, T + 10)]


@pytest.mark.anyio
async def test_a_gate_restarted_inside_apply_starts_restarting(tmp_path):
    with anyio.fail_after(BOUND_S):
        from spark.gate.state import ApplyHold
        state = GateState(boot_id=BOOT, applying=ApplyHold(since=T - 30, by_uid=1000, renewed_at=T - 15,
                                                          begun_at=T - 10, restarting=[], ended=False))
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74, state=state)
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "restarting") == RESTARTING_TEXT


@pytest.mark.anyio
async def test_llama_swap_down_after_the_keys_wait(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.admitter.set_llamaswap_up(False)
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "llama_swap_down") == LLAMA_SWAP_DOWN_TEXT and gate.swap.loads() == []


@pytest.mark.anyio
async def test_while_restarting_a_down_llama_swap_reads_restarting(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.admitter.set_restarting(True)
        gate.admitter.set_llamaswap_up(False)
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "restarting") == RESTARTING_TEXT
        assert gate.emitted.of("llama_swap_down") == []
        assert [fields["code"] for _, fields in gate.emitted.of("refused")] == ["restarting"]


@pytest.mark.anyio
async def test_every_outcome_clears_the_started_record(tmp_path):
    with anyio.fail_after(BOUND_S):
        # Ready: the engine's pid, launch's, which the engine keeps across its exec, is ticketed.
        gate = Gate(tmp_path / "ready", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(PI, CODER)
        await settle()
        assert gate.started() == [f"{CODER}.json"]
        ticket_id = gate.state.issued[CODER]
        gate.available = 40.0
        gate.swap.ready(CODER)
        await settle()
        assert asked.result.model == CODER and gate.started() == []
        engine = gate.state.ticketed[CODER]
        assert (engine.pid, engine.ticket_id, engine.at) == (os.getpid(), ticket_id, T)
        assert CODER not in gate.state.issued

        # Failed: nothing kept.
        gate = Gate(tmp_path / "failed", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(PI, CODER)
        await settle()
        gate.swap.exits(CODER)
        await settle()
        assert asked.done and gate.started() == []
        assert CODER not in gate.state.ticketed and CODER not in gate.state.issued and CODER not in gate.state.models

        # Unknown, then gone from /running.
        gate = Gate(tmp_path / "gone", models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(PI, CODER)
        await settle()
        gate.swap.drops(CODER, "dropped")
        await settle()
        assert gate.started() == [f"{CODER}.json"] and not asked.done
        gate.swap.states[CODER] = "stopped"
        await gate.at(T + 30)
        assert asked.done and gate.started() == []
        assert CODER not in gate.state.ticketed and CODER not in gate.state.issued and CODER not in gate.state.models


@pytest.mark.anyio
async def test_a_started_record_that_cant_be_read_or_cleared_leaves_the_outcome_standing(tmp_path, monkeypatch,
                                                                                        caplog):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        asked = gate.ask(PI, CODER)
        await settle()

        def broken(*args, **kwargs):
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(tickets, "read_record", broken)
        monkeypatch.setattr(tickets, "clear_started", broken)
        gate.available = 40.0
        gate.swap.ready(CODER)
        await settle()
        assert asked.result == Admitted(CODER, True, 0.0)
        assert gate.state.models[CODER].state == "ready" and CODER not in gate.state.ticketed
        assert any("started" in r.getMessage() for r in caplog.records if r.name == LOG)


@pytest.mark.anyio
async def test_a_load_whose_settling_breaks_answers_its_requests_and_holds_the_slot_until_running_settles(
        tmp_path, monkeypatch, caplog):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=82)
        asked = gate.ask(PI, CODER)
        await settle()
        waiting = gate.ask(AGENT, EMBED)
        await settle()

        def broken(*args, **kwargs):
            raise RuntimeError("a bug")

        monkeypatch.setattr(Admitter, "_fall", broken)
        gate.swap.drops(CODER, "dropped")  # claimed: the slot waits for /running
        gate.swap.states[CODER] = "stopping"
        await gate.at(T + 5)
        gate.swap.states[CODER] = "ready"
        await gate.at(T + 6)  # ready, but settling it breaks: the request hears of it, never waits for ever
        assert isinstance(asked.task.exception(), RuntimeError)
        assert any("broke" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
        caplog.clear()
        await gate.at(T + 7)
        assert gate.state.models[CODER].state == "ready"  # as /running has it, its fall not known
        assert gate.state.models[CODER].load_fall_gib is None
        assert gate.swap.loads() == [CODER, EMBED] and not waiting.done


@pytest.mark.anyio
async def test_a_load_restored_as_starting_is_ticketed_when_ready(tmp_path):
    with anyio.fail_after(BOUND_S):
        launch = tmp_path / "launch"
        launch.mkdir()
        ticket_id = tickets.issue(launch, CODER, T - 20 + LOAD_CALL_TIMEOUT_S, now=T - 20, footprint_gib=41.0,
                                  available_gib=74.0, boot_id=BOOT)
        claim = tickets.claim(launch, CODER, T - 18, boot_id=BOOT)  # launch, before the gate restarted
        tickets.mark_started(launch, claim.ticket, T - 18)
        state = GateState(boot_id=BOOT, issued={CODER: ticket_id})
        state.models = loaded(GEMMA, EMBED, WHISPER) | {CODER: record(CODER, fall=None, state="starting")}
        state.models[CODER].load_fall_gib = None
        gate = Gate(tmp_path, available=34, state=state)
        gate.swap.states.update({GEMMA: "ready", EMBED: "ready", WHISPER: "ready", CODER: "starting"})
        await gate.at(T)
        assert gate.state.models[CODER].state == "starting" and gate.started() == [f"{CODER}.json"]
        gate.swap.states[CODER] = "ready"
        await gate.at(T + 6)
        assert gate.state.models[CODER].state == "ready" and gate.started() == []
        assert (gate.state.ticketed[CODER].pid, gate.state.ticketed[CODER].ticket_id) == (os.getpid(), ticket_id)
        assert gate.state.models[CODER].load_fall_gib == 35.0  # from the ticket's MemAvailable, 74, capped
        assert CODER not in gate.state.issued
        await gate.at(T + 400)
        assert gate.state.ticketed[CODER].pid == os.getpid()  # still counted ticketed after the record would expire


@pytest.mark.anyio
async def test_owed_ignores_rss_growth_until_the_registry_turns_it_on(tmp_path):
    with anyio.fail_after(BOUND_S):
        proc = tmp_path / "proc"

        def engine(pid: int, rss_kib: int | None) -> None:
            (proc / str(pid)).mkdir(parents=True, exist_ok=True)
            rss = "" if rss_kib is None else f"RssAnon:\t{rss_kib} kB\n"
            (proc / str(pid) / "status").write_text(
                f"Name:\tllama-server\nState:\tS (sleeping)\nUid:\t990\t990\t990\t990\n{rss}")
            (proc / str(pid) / "stat").write_text(f"{pid} (llama-server) S " + " ".join(map(str, range(1, 19)))
                                                  + " 777\n")

        engine(4242, int(6.5 * 1024 * 1024))  # RssAnon up 5 GiB since its load, at 1.5
        state = GateState(boot_id=BOOT, ticketed={GEMMA: Ticketed(GEMMA, 4242, "t-gemma", T - 3600, 777)})
        state.models = {GEMMA: record(GEMMA, fall=25.0, rss_anon_at_load_gib=1.5)}
        off = Gate(tmp_path / "off", state=state, proc=proc)
        assert off.admitter.owed() == Decimal(7)
        on = Gate(tmp_path / "on", state=state, proc=proc, registry=make_registry(owed_reads_rss=True))
        assert on.admitter.owed() == Decimal(2)
        # A reading the gate can't take credits no growth: None is never read as 0.
        engine(4242, None)
        assert on.admitter.owed() == Decimal(7)
        engine(4242, int(6.5 * 1024 * 1024))
        state.models[GEMMA].rss_anon_at_load_gib = None
        assert on.admitter.owed() == Decimal(7)
        state.models[GEMMA].rss_anon_at_load_gib = 1.5
        state.ticketed[GEMMA] = Ticketed(GEMMA, 4242, "t-gemma", T - 3600, 778)  # the pid handed to another process
        assert on.admitter.owed() == Decimal(7)


@pytest.mark.anyio
async def test_a_loads_fall_is_capped_and_flagged_when_other_memory_moved(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path / "cold", models=loaded(GEMMA, EMBED, WHISPER), available=74,
                    registry=make_registry(coder_cold=30.0))
        asked = gate.ask(PI, CODER)
        await settle()
        gate.available = 34.0  # a fall of 40
        gate.swap.ready(CODER)
        await settle()
        assert asked.result.model == CODER
        assert (gate.state.models[CODER].load_fall_gib, gate.state.models[CODER].fall_flagged) == (
            30.0 + LOAD_FALL_MARGIN_GIB, True)

        gate = Gate(tmp_path / "none", models=loaded(GEMMA, EMBED, WHISPER), available=90,
                    registry=make_registry(coder_cold=None))
        asked = gate.ask(PI, CODER)
        await settle()
        gate.available = 45.0  # a fall of 45
        gate.swap.ready(CODER)
        await settle()
        assert (gate.state.models[CODER].load_fall_gib, gate.state.models[CODER].fall_flagged) == (41.0, True)


@pytest.mark.anyio
async def test_a_ready_answer_with_its_ticket_unused_withdraws_it(tmp_path):
    with anyio.fail_after(BOUND_S):
        # llama-swap ran the model already (a record the gate didn't have): its health answers 200 with no start, so
        # launch never claimed the ticket, which must not stay to start the model around the gate later.
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.swap.answer_next(CODER, LoadOutcome.READY)
        asked = gate.ask(PI, CODER)
        await settle()
        assert asked.result.model == CODER and gate.ticket(CODER) is None
        assert CODER not in gate.state.ticketed  # no start was recorded for it


@pytest.mark.anyio
async def test_an_unused_ticket_is_withdrawn_after_a_failed_load(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, EMBED, WHISPER), available=74)
        gate.swap.claim_late.add(CODER)  # llama-swap's start fails before launch runs
        asked = gate.ask(PI, CODER)
        await settle()
        assert gate.ticket(CODER) is not None
        gate.swap.exits(CODER)
        await settle()
        assert gate.ticket(CODER) is None and asked.result.code == "load_failed"


@pytest.mark.anyio
async def test_a_starting_model_and_the_ceiling_count(tmp_path):
    with anyio.fail_after(BOUND_S):
        models = loaded(GEMMA, EMBED, WHISPER) | {"thirty-model": record("thirty-model", 30.0, state="starting")}
        gate = Gate(tmp_path, models=models, available=100)
        assert gate.admitter.starting_gib() == Decimal(30)
        assert gate.admitter.free_for_a_load(uses_hold=False) == min(Decimal(100 - 24), Decimal(59)) - 30
        gate.available = 70.0
        assert gate.admitter.free_for_a_load(uses_hold=False) == min(Decimal(70 - 24), Decimal(59)) - 30


@pytest.mark.anyio
async def test_a_model_still_starting_is_in_a_refusals_figure(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(GEMMA, WHISPER), available=70, holders=[Holder("Gemma", 27, False)])
        gate.ask(AGENT, EMBED)
        await settle()
        asked = gate.ask(PI, CODER)
        await gate.at(T + 30)
        assert refused(asked, "no_fit") == (
            "The coder didn't load: it needs 41 GiB, and 38 GiB is free for a load (70 GiB available, less the 24 GiB "
            "reserve and the 8 GiB the model still starting may take). Using memory now: Gemma 27 GiB. Free space with "
            "`spark make-room 41G` on the Spark, then try again.")


@pytest.mark.anyio
async def test_waiting_load_started_and_loaded_are_emitted(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER) | {GEMMA: record(GEMMA, fall=26.0)}, available=48)
        asked = gate.ask(PI, CODER)
        await gate.at(T + 5)
        gate.available = 90.0
        await gate.at(T + 10)
        gate.clock.now = T + 34
        gate.available = 66.0
        gate.swap.ready(CODER)
        await settle()
        assert asked.result.seconds == 24
        events = [(kind, key) for kind, key, _ in gate.emitted.sent if kind in ("waiting", "load_started", "loaded")]
        ticket_id = gate.state.ticketed[CODER].ticket_id
        assert events == [("waiting", f"wait:{asked.req.id}"), ("load_started", f"load:{ticket_id}"),
                          ("loaded", f"load:{ticket_id}")]
        texts = [gate.emitted.text(kind, fields) for kind, _, fields in gate.emitted.sent
                 if kind in ("waiting", "load_started", "loaded")]
        assert texts == ["Waiting for memory: the coder for pi on the Mac, up to 30 s. It needs 41 GiB, and 18 GiB is "
                         "free for a load.", "Loading the coder for pi on the Mac…", "Loaded the coder in 24 s."]


@pytest.mark.anyio
async def test_progress_lines_name_their_kind_and_follow_the_reason(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER), available=106)
        gate.admitter.reload(GEMMA, "boot")
        await settle()
        asked = gate.ask(None, CODER, kind="command", deadline=T + LOAD_CALL_TIMEOUT_S)
        await gate.at(T + 5)
        gate.clock.now = T + 24
        gate.available = 60.0  # Gemma took 46, capped at its footprint: 36 free for a load, not enough
        gate.swap.ready(GEMMA)
        await settle()
        gate.available = 90.0
        await gate.at(T + 40)
        gate.clock.now = T + 64
        gate.swap.ready(CODER)
        await settle()
        assert asked.result == Admitted(CODER, True, 24.0)
        waiting = dict.fromkeys(gateproto.WaitProgress.__annotations__)
        assert asked.progress == [
            waiting | {"kind": "waiting", "model": CODER, "label": "the coder", "why": "slot",
                       "loading_label": "Gemma"},
            waiting | {"kind": "waiting", "model": CODER, "label": "the coder", "why": "memory", "needed_gib": 41.0,
                       "free_gib": 36.0},
            {"kind": "loading", "model": CODER, "label": "the coder", "last_s": None}]
        assert all(line["kind"] in PROGRESS_KINDS for line in asked.progress)
        assert set(asked.progress[-1]) == set(gateproto.LoadProgress.__annotations__)
        # A command has no key: load_started names no asker, and no `waiting` notification goes, its stream having it.
        assert gate.emitted.of("waiting") == []
        [(_, started)] = [(key, fields) for key, fields in gate.emitted.of("load_started")
                          if fields["label"] == "the coder"]
        assert started["key_label"] is None and started["last_s"] is None


@pytest.mark.anyio
async def test_waiting_rows_show_each_queued_request(tmp_path):
    with anyio.fail_after(BOUND_S):
        gate = Gate(tmp_path, models=loaded(EMBED, WHISPER) | {GEMMA: record(GEMMA, fall=26.0)}, available=48)
        gate.ask(AGENT, CODER)
        await gate.at(T + 120)
        assert gate.admitter.waiting() == [
            {"key_label": "agent", "model": CODER, "waited_s": 120.0, "wait_s": 600, "why": "memory"}]


def test_privileges_are_the_groups_and_dans_commands_get_dans():
    def req(kind, key=None):
        return AdmitRequest(id="r", model=CODER, kind=kind, key=key, deadline=None if kind == "reload" else T + 30)

    assert privileges(req("key", AGENT), REGISTRY) == Privileges(1, False, False, "agent", False)
    assert privileges(req("key", PI), REGISTRY) == Privileges(0, True, True, "dan", True)
    assert privileges(req("command"), REGISTRY) == privileges(req("key", PI), REGISTRY)
    assert privileges(req("key", MIXED_KEY), REGISTRY) == Privileges(0, True, False, "dan", True)
    reload = privileges(req("reload"), REGISTRY)
    assert reload.queue < 0 and not reload.uses_hold and not reload.reloads_marked
