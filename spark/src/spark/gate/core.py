"""The gate's core (Phase 2a; plan.md, *The front and the gate*, *Admission and memory rules*, *Visibility and
notifications* and *Deploy workflow*): it builds Tasks 13 to 17's parts on the gate's saved state, runs their loops,
keeps the activity record the brake reads, holds apply's hold, and checks for loads around the gate. The rules those
parts keep hold only as the core wires them, so each wire here names the rule it serves.

**Start** (`start`), in this order, each step for a reason:
- the state, loaded with `set_aside` (a damaged file kept aside, a fresh state that errs safe);
- the last run's activity record read, then `BackUpWatch.gate_restarted` on the state as loaded, so the gate's own
  return is dated from its last moment alive;
- `hold.recover_release`, before anything reads the brake's hold: a hold a crash left aside is put back;
- `/running` read, then `restore`, the Notifier and the Admitter with nothing awaited between the read and the
  Admitter, so no ticket is claimed unseen between them. A read that doesn't answer is never taken as nothing
  running: the state's own records of this boot stand in for it, every one counted, and admission waits for llama-swap
  (no engine outlives a boot, so another boot's state stands for nothing);
- `room.at_start` (another boot's room hold, and a hold that may have stood before damage, end with `room_hold_ended`);
  `clean_shutdown` false, saved; after damage, the brake's events read from the file's end and the engines running now
  recorded as found after damage, so neither old alerts nor the gate's own engines come back as news;
- each saved drain resumed under its id (`resumable_drains`), its end passed to `room.resumed_drain_ended`; the
  undrains owed go once the front's events call opens (`open_front_events`, then `drainer.send_owed`);
- the Notifier's task, the residents' preload (`boot`), and the loops.

**The loops.** Memory every 250 ms (`read_memory`: when it last rose above the warn line, `memory_warning` once per
fall, and `changed()` to the admitter and the drainer); `/running` every second, one read at a time (`read_running`:
the brake's events ingested first, then each model's record settled against the read, which only a read that answered
does, then `observe_running`, llama-swap's watch, the residents' rule and apply's automatic end); every second
(`each_second`: the brake's events, apply's lapse, the activity record, each guarded on its own); every 5 s
(`each_five_s`: the four units, the idle check, the pins', sessions' and room hold's ends, the brake's release check,
then the holders and the bypass check, read off the event loop). The idle check, and the brake's automatic release, run
only while llama-swap answers, its last read sent within LLAMA_SWAP_HUNG_S: without it no unload could follow a drain,
and the gate can't verify a release's reloads fit (the controller's ruling at Task 18's review). Dan's own `make
brake-release` lifts the hold whatever llama-swap does. A step that breaks is logged and runs again at its next turn; a
reading that fails shows under `health`.

**Saving.** Every part's `on_change` is `save`, which writes the whole state and its folder to disk before it returns.
So saves land in the order they were made, each writing the state as it is then, and a caller that goes can't leave
one landing after a later one (the controller's ruling at Task 17's second re-review). A load call is never sent before
its ticket's id is on disk: a save that fails there refuses the load as the gate's own error (the controller's ruling
at Task 18's review). A save slower than SAVE_SLOW_S is logged with how long the loop waited.

**Apply's hold** (`drain_all`, `apply_renew`, `apply_begin`, `apply_end`, `undrain_all`): saved, so a restarted gate
holds as before. `end_apply` is its one end, however it ends, in one step of the event loop: the hold ended and saved,
the registry read again (the admitter's, which Room, the drainer and the Preloader read), `release_all`, the new model
list, and the residents' reloads queued ahead of anything else; `apply_restarted` once they have settled, when
llama-swap did restart. A llama-swap start that an apply announced (the first after its `begin`, while its hold stands
or within APPLY_LAPSE_S of its end) is never llama-swap's own `restart`, even after the hold has ended or lapsed.

**The bypass check.** Each engine found on an engine port is ticketed when its pid is the one its ticket's start
recorded (`state.ticketed`), or a live `started/` record of a ticket the gate issued holds it; any other is named in
`health.unticketed_engines`. launch's `no_ticket` and `ticket_expired` refusals of this boot are counted once each."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import os
import socket
import subprocess
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from spark import hold as holdfile
from spark import launch as launches
from spark import memory, messages, procs, render, sdnotify, tickets
from spark.brakeevents import EVENTS_FILE, read_events
from spark.gate import units as unitwatch
from spark.gate.admission import Admitted, Admitter
from spark.gate.notify import MemoryWarning, Notifier, ingest_brake_events
from spark.gate.policy import POLL_S, Drainer, FrontChannel, Pins, Sessions, idle_due
from spark.gate.room import Preloader, Room, fired_at, kept_out
from spark.gate.state import (ApplyHold, GateState, ModelRecord, RecentRecord, RefusalRecord, Session, Ticketed,
                              load_state, restore, resumable_drains, save_state)
from spark.gate.units import BackUpWatch, LlamaSwapWatch, UnitState, UnitStateError
from spark.gateproto import APPLY_LAPSE_S, LLAMA_SWAP_HUNG_S, RELEASE_AFTER_S, SESSION_TTL_S, STATUS_SCHEMA
from spark.llamaswap import LlamaSwapError, Running
from spark.memory import MemInfo
from spark.messages import Holder, Notification
from spark.registry import ClientKey, Registry
from spark.registry import load_registry as read_registry

_log = logging.getLogger(__name__)

ACTIVITY_FILE = "activity.json"  # in the gate's state folder: what the brake reads (Task 23)
MEMORY_EVERY_S = 0.25  # MemAvailable, as the brake polls it
RUNNING_EVERY_S = 1.0  # llama-swap's /running
SECOND_S = 1.0  # the brake's events, apply's lapse, the activity record (ACTIVITY_EVERY_S)
FIVE_S = 5.0  # the units, the idle check, the ends, the brake's release, the holders and the bypass check
# The ticket id the core records, in state.ticketed, for an engine found running at a start after damage: the damage
# lost which ticket started it, and it is the stack's, so the bypass check never reports it (the controller's ruling
# at Task 13's review).
FOUND_AFTER_DAMAGE = "found-after-damage"
# A starting model's MemAvailable at admission is kept this long after it stops starting, so the brake's unload of it,
# read in a second or two, still finds where its load started (the controller's ruling at Task 14's review).
STARTS_KEPT_S = 120.0
NVIDIA_TIMEOUT_S = 5.0
# A save is synchronous, its file and folder fsynced on the event loop; one that takes longer than this is logged, with
# how long the loop waited (an fsync behind other writes on the NVMe, say), so a stall shows in the journal.
SAVE_SLOW_S = 1.0
BACKSTOP_CODES = (tickets.NO_TICKET, tickets.TICKET_EXPIRED)  # launch refusing a start made around the gate
# apply_restarted's turns, each a reload settling or a second's wait for a /running read since llama-swap's start.
REPORT_POLLS = 120


def engine_ports(registry: Registry) -> dict[str, int]:
    """Each model's engine port, as llama-swap v257 hands them out: from startPort, render's ENGINE_START_PORT, one each
    to the models whose cmd holds ${PORT} (render gives every one), in their names' sorted order (v257's
    internal/config/macros.go:150-186)."""
    return {name: render.ENGINE_START_PORT + place for place, name in enumerate(sorted(registry.models))}


def _said(err: BaseException) -> str:
    """An error in a few words, for a log line or `health`: the OS's own words, or the error's text."""
    if isinstance(err, OSError) and err.strerror:
        return err.strerror
    return " ".join(str(err).split()) or type(err).__name__


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _names_llama_swap(restarting: Iterable[str]) -> bool:
    """Whether apply's `begin` named llama-swap among the units it restarts, by the watch's name or the unit's."""
    return any(unit in ("llama-swap", unitwatch.UNITS["llama-swap"]) for unit in restarting)


def _boot_time(proc: Path) -> float | None:
    """When this boot began, Unix seconds, from /proc/stat's btime; None when it can't be read."""
    try:
        for line in (proc / "stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _nvidia_smi() -> str:
    """nvidia-smi's per-process list (procs.NVIDIA_APPS), run with a bound; it raises when it can't be read."""
    return subprocess.run(procs.NVIDIA_APPS, capture_output=True, text=True, timeout=NVIDIA_TIMEOUT_S,
                          check=True, env=sdnotify.child_env()).stdout


class _CoreEmit:
    """Emit for the unit watches, which call it bare: through the core, so `emit`'s ValueError is one plain line."""

    def __init__(self, core: GateCore):
        self._core = core

    def emit(self, type: str, event_key: str, /, **fields: Any) -> None:  # noqa: A002 (Emit's name)
        self._core._emit(type, event_key, **fields)


@dataclass
class _ApplyReport:
    """apply_restarted for one apply's hold: the reloads it waits on, and what was loaded when the hold began."""

    since: float
    begun_at: float | None
    restarting: list[str]
    loaded: list[str]
    futures: dict[str, asyncio.Future] = field(default_factory=dict)
    task: asyncio.Task | None = None
    sent: bool = False


@dataclass(frozen=True)
class _Gathered:
    """The 5 s background read, taken off the event loop."""

    engines: dict[str, tuple[int, int, bool]] | None  # model → (port, pid, ticketed by its recorded start); None: unread
    holders: list[Holder] | None  # None: unread, the last reading stands
    started: list[dict] | None  # launch's live started/ records; None: unread, so none counts
    refusals: list[dict]  # launch's refusal records of the backstop's codes
    problems: dict[str, str | None]  # each source read: what went wrong, or None when it read


class GateCore:
    """The gate's core. `registry` is the deployed registry as `spark gate` read it, and `registry_path` where it is
    read again at apply's end; `state_dir` is the gate's state folder (GATE_STATE). `llamaswap` is the gate's
    AsyncLlamaSwap; `publish` sends one Notification (an NtfyPublisher); `brake_dir` is the brake's folder (its hold and
    its events); `launch` is launch's folder; `hf_home` the Hugging Face cache the engines read; `keys` the private key
    list, each key by its name. `spark_uid` is the account the engines run as (this one's, by default); `dan_uids` the
    accounts whose processes are Dan's, or what gives them, asked at each read of the holders, so a member of
    spark-admin added is named as Dan's without a restart (the controller's ruling at Task 19's review); `may_pin` and
    `may_hold` are Pins' and Sessions'. The rest are the readings the
    tests stand in for: the clock, /proc/meminfo, the boot id and when the boot began, each unit's `systemctl show`,
    nvidia-smi, /proc, the host's name, the registry's reader, the drain's and admission's poll, and `sleep`."""

    def __init__(self, registry: Registry, state_dir: Path, *, registry_path: Path, llamaswap: Any,
                 publish: Callable[[Notification], Awaitable[None]], brake_dir: Path, launch: Path, hf_home: str,
                 may_pin: Callable[[int], bool], may_hold: Callable[[int], bool],
                 keys: Mapping[str, ClientKey] | None = None, spark_uid: int | None = None,
                 dan_uids: Iterable[int] | Callable[[], Iterable[int]] = (), clock: Callable[[], float] = time.time,
                 read_meminfo: Callable[[], MemInfo] = memory.read_meminfo, boot_id: str | None = None,
                 boot_time: float | None = None, read_unit: Callable[[str], UnitState] = unitwatch.unit_state,
                 nvidia_smi: Callable[[], str] = _nvidia_smi, proc: Path = procs.PROC, host: str | None = None,
                 load_registry: Callable[[Path], Registry] = read_registry, poll_s: float = POLL_S,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self._registry = registry
        self.state_dir = Path(state_dir)
        self._registry_path = Path(registry_path)
        self._swap = llamaswap
        self._publish = publish
        self.brake_dir = Path(brake_dir)
        self._events_path = self.brake_dir / EVENTS_FILE
        self._launch = Path(launch)
        self._hf_home = hf_home
        self._keys = dict(keys or {})
        self._spark_uid = spark_uid if spark_uid is not None else os.geteuid()
        self._dan_uids = dan_uids if callable(dan_uids) else frozenset(dan_uids)
        self._may_pin = may_pin
        self._may_hold = may_hold
        self._clock = clock
        self._read_meminfo = read_meminfo
        self.boot_id = boot_id if boot_id is not None else memory.boot_id()
        self._proc = Path(proc)
        self._boot_time = boot_time if boot_time is not None else _boot_time(self._proc)
        self._read_unit = read_unit
        self._nvidia_smi = nvidia_smi
        self._host = host or socket.gethostname().split(".")[0]
        self._load_registry = load_registry
        self._poll_s = poll_s
        self._sleep = sleep
        self.front = FrontChannel()
        self.state = GateState()  # until start() loads it
        self.notifier: Notifier | None = None
        self.admitter: Admitter | None = None
        self.drainer: Drainer | None = None
        self.sessions: Sessions | None = None
        self.pins: Pins | None = None
        self.room: Room | None = None
        self.preloader: Preloader | None = None
        self._backup = BackUpWatch(_CoreEmit(self), clock)
        self._llama_watch = LlamaSwapWatch(_CoreEmit(self), clock)
        self._warning = MemoryWarning()
        self._started_at: float | None = None
        self._state_problem: str | None = None
        self._registry_problem: str | None = None
        self._read_problems: dict[str, str] = {}  # each reading that failed at its last try, by its source
        self._mem: MemInfo | None = None
        self._above_since: float | None = None  # when MemAvailable last rose above the warn line; None at or under
        self._answering = False  # whether /running answered at the last read
        self._answered_at: float | None = None  # when the last read that answered was sent
        self._reading: asyncio.Task | None = None  # the /running read in flight, which every caller awaits
        self._read_seq = 0  # each read's place, as it is sent
        self._done_seq = 0  # the place of the last read whose answer, or failure, was taken
        self._listed: dict[str, tuple[str, float]] = {}  # each model the last answered read listed: its state, sent at
        self._units: dict[str, UnitState] = {}  # each unit's last reading
        self._apply_starts: dict[float, float] = {}  # each apply hold's (by its since) llama-swap start, once seen
        self._apply_ended: dict[float, float] = {}  # when each apply hold ended, in this run
        self._apply_loaded: dict[float, list[str]] = {}  # what was loaded as each apply hold began
        self._reports: dict[float, _ApplyReport] = {}
        self._starts: dict[str, tuple[float, float]] = {}  # model → (MemAvailable at admission, when last starting)
        self._holders: list[Holder] = []
        self._unticketed: list[dict] = []
        self._reported: set[tuple[str, int]] = set()  # the unticketed engines already logged
        self._backstop: set[tuple[str, str, str, str]] = set()  # launch's backstop refusals seen this boot
        self._activity_at: float | None = None
        self._sent_state: tuple | None = None
        self._settle_front_drains = False
        self._tasks: set[asyncio.Task] = set()
        self._loops: list[asyncio.Task] = []
        self._notifier_task: asyncio.Task | None = None

    @property
    def registry(self) -> Registry:
        """The registry as last read: the admitter's, which Room, the drainer, the Preloader and Pins all read."""
        return self.admitter.registry if self.admitter is not None else self._registry

    @property
    def keys(self) -> Mapping[str, ClientKey]:
        """The private key list, each key by its name, as `spark gate` read it: the routes' (Task 19), for an admit's
        key, a front refusal's words and the status socket's filter."""
        return self._keys

    @property
    def llamaswap(self) -> Any:
        """The gate's llama-swap client, which `spark gate` closes once `stop()` is done; `/v1/logs` reads with it."""
        return self._swap

    @property
    def launch(self) -> Path:
        """launch's folder (tickets, started/, refusal records), which `/v1/canary` searches."""
        return self._launch

    # Start and stop.

    async def start(self, *, loops: bool = True) -> None:
        """Everything the gate keeps, settled against llama-swap and this boot, and every part built on it, in the
        order the module's docstring gives; then the loops, unless `loops` is false (the tests run each step)."""
        now = self._clock()
        self._started_at = now
        loaded, self._state_problem = load_state(self.state_dir, now=now, set_aside=True)
        if self._state_problem:
            _log.warning("%s", self._state_problem)
        self._backup.gate_restarted(loaded, now, last_alive=self._last_alive())
        try:
            recovered = holdfile.recover_release(self.brake_dir)
        except OSError as err:  # what is aside stays aside; a later start tries again
            recovered = None
            self._unread("hold", f"the brake's hold left aside in {self.brake_dir} couldn't be put back "
                                 f"({_said(err)})")
        sent_at = self._clock()
        try:
            running = await self._swap.running()
            answered = True
        except LlamaSwapError as err:
            running, answered = self._as_counted(loaded), False
            _log.warning("llama-swap's /running didn't answer as the gate started (%s): what the saved state counts "
                         "stands, and nothing loads until it answers", err)
        # Nothing is awaited from the read to the Admitter, so no claim lands unseen between them (the controller's
        # ruling at Task 15's re-review).
        state, ended = restore(loaded, running, now=self._clock(), boot_id=self.boot_id, registry=self._registry)
        self.state = state
        self.notifier = Notifier(self._publish, self._registry, state, self._clock, on_change=self._save_quietly)
        self.admitter = Admitter(self._registry, state, self._swap, self.notifier, self._clock,
                                 read_mem=self._read_meminfo, read_hold=self._read_hold, launch=self._launch,
                                 hf_home=self._hf_home, holders=self.holders, on_change=self.save,
                                 poll_s=self._poll_s, proc=self._proc, running=running)
        if not answered:
            self.admitter.set_llamaswap_up(False)
        else:
            self._answered_at = sent_at
        self._answering = answered
        self._listed = {entry.model: (entry.state, sent_at) for entry in running}
        self.sessions = Sessions(state, self._clock, proc=self._proc, may_hold=self._may_hold,
                                 is_model=lambda name: name in self.registry.models, on_change=self.save)
        self.drainer = Drainer(self.front, self._swap, self.admitter, self.notifier, self._clock,
                               on_change=self.save, poll_s=self._poll_s, live=self.sessions.live)
        self.pins = Pins(state, self.admitter, self.notifier, self._clock, may_pin=self._may_pin,
                         on_change=self.save)
        self.room = Room(state, self._registry, self.admitter, self.drainer, self.notifier, self._clock,
                         read_mem=self._read_meminfo, read_hold=self._read_hold, release_hold=self._release_hold,
                         boot_id=self.boot_id, front=self.front, live=self.sessions.live, on_change=self.save)
        self.preloader = Preloader(self.admitter, self._registry, self.notifier, self._clock, llamaswap=self._swap,
                                   read_hold=self._read_hold, on_change=self.save, sleep=self._sleep)
        self.room.at_start(ended, recovered=recovered)
        state.clean_shutdown = False
        if state.fresh_after_damage:
            await self._after_damage(found_here=self._state_problem is not None)
        self._save_quietly()
        for drain in resumable_drains(state):
            self._resume(drain.model, drain.drain_id, drain.why)
        self.read_memory()
        self._notifier_task = self._spawn(self.notifier.run())
        # Only the residents with no record: one restore counts (loading, or being drained, its unload resumed above)
        # is never queued, since a reload queued behind a drain would load it again once the drain is done.
        residents = [name for name, model in self.registry.models.items()
                     if model.resident and name not in state.models]
        self._spawn(self._preload(residents, "boot"))
        if loops:
            for every_s, step in ((MEMORY_EVERY_S, self.read_memory), (RUNNING_EVERY_S, self.read_running),
                                  (SECOND_S, self.each_second), (FIVE_S, self.each_five_s)):
                self._loops.append(asyncio.get_running_loop().create_task(self._every(every_s, step)))

    async def stop(self) -> None:
        """A clean stop: the loops, then every load and drain cancelled before the llama-swap client closes (each
        stays saved, for the next start to resume), the Notifier flushed and drained (by 5 s, within uvicorn's
        GRACEFUL_S), and `clean_shutdown` saved true. Before a start that built the parts, nothing is saved: the state
        on disk is the last run's. After a start cut short before the Notifier's task ran (`spark gate` stopped while
        it started), there is no queue to wait on: nothing sends it (the controller's ruling at Task 19's review)."""
        await self._cancel(self._loops)
        if self.admitter is not None:
            await self.admitter.stop()
        if self.drainer is not None:
            await self.drainer.stop()
        await self._cancel([t for t in self._tasks if t is not self._notifier_task]
                           + ([self._reading] if self._reading is not None else []))
        if self.notifier is not None and self._notifier_task is not None:
            self.notifier.flush()
            if not await self.notifier.drained():
                _log.warning("ntfy didn't take every notification before the gate stopped")
        if self._notifier_task is not None:
            await self._cancel([self._notifier_task])
        if self.admitter is not None:
            self.state.clean_shutdown = True
            self._save_quietly()

    @staticmethod
    async def _cancel(tasks: list[asyncio.Task]) -> None:
        for each in tasks:
            each.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _as_counted(self, loaded: GateState) -> list[Running]:
        """What stands for a /running read that didn't answer at the start: each model the saved state of this boot
        counts, as it counts it (a drain's model as still listed), so restore keeps every one. Nothing for another
        boot's state, since no engine outlives a boot, nor for a fresh one after damage, whose boot isn't known (its
        `boot_id` is empty): what runs is unknown then, which is safe only because admission waits for llama-swap,
        and the first read that answers is reconciled, each model it lists counted, before llama-swap counts as up."""
        if loaded.boot_id != self.boot_id:
            return []
        shown = {"draining": "ready"}
        return [Running(name, shown.get(record.state, record.state)) for name, record in sorted(loaded.models.items())]

    async def _after_damage(self, *, found_here: bool) -> None:
        """A start fresh from a damaged state file (the controller's rulings at Task 13's review and re-review): the
        brake's events are read from the file's end, since which were notified was lost, and, at the start whose load
        found the damage (`found_here`), each engine running then is recorded as found after damage, since which
        tickets started them was lost. A later start before the front's first snapshot, the flag still set, records
        none: an engine started around the gate meanwhile is reported (the controller's ruling at Task 18's review).
        The front's drains are settled from its first snapshot (`inflight`)."""
        state = self.state
        if state.brake_events_after is None:  # set at the first start after the damage, kept by a second
            try:
                events = read_events(self._events_path, None)
            except (OSError, ValueError) as err:
                events = []
                self._unread("brake events", f"the brake's events {self._events_path} couldn't be read ({_said(err)})")
            if events:
                last = events[-1]
                state.brake_events_after = (last.boot_id, last.seq, last.at)
                _log.warning("the gate's state was damaged, so the brake's %d earlier events aren't read: any alert of "
                             "theirs that hadn't gone isn't sent", len(events))
        found = await asyncio.to_thread(self._engines_running, engine_ports(self.registry)) if found_here else {}
        for model, (port, pid, started) in sorted(found.items()):
            if model in state.ticketed:
                continue
            state.ticketed[model] = Ticketed(model=model, pid=pid, ticket_id=FOUND_AFTER_DAMAGE, at=self._clock(),
                                             start_time=started)
            _log.warning("%s's engine (pid %s, port %s) was running when the gate started after damage: recorded as "
                         "found after damage, never reported as a load around the gate", model, pid, port)
        self._settle_front_drains = True

    def _engines_running(self, ports: dict[str, int]) -> dict[str, tuple[int, int, int | None]]:
        """Off the event loop: each engine on an engine port, with its pid's start time."""
        found: dict[str, tuple[int, int, int | None]] = {}
        try:
            for model, port in ports.items():
                pid = procs.engine_pid(port, spark_uid=self._spark_uid, proc=self._proc)
                if pid is not None:
                    found[model] = (port, pid, procs.start_time(pid, proc=self._proc))
        except OSError as err:
            _log.warning("the engines running at the start after damage couldn't be read (%s)", _said(err))
        return found

    def _resume(self, model: str, drain_id: str | None, why: Any) -> None:
        """A drain the gate saved, or one it finishes after damage, resumed through the drainer under its id (a new
        one when None), its end passed to Room, so a make-room drain's model goes on the room hold that stands (the
        controller's ruling at Task 17's re-review)."""
        async def resume() -> None:
            await self.drainer.resume(model, drain_id, why,
                                      ended=lambda end: self.room.resumed_drain_ended(model, why, end))

        self._spawn(resume())

    async def _preload(self, names: list[str], after: str) -> None:
        try:
            await self.preloader.run(names, after)
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("the residents' preload (%s) broke", after)

    async def _every(self, every_s: float, step: Callable[[], Any]) -> None:
        """One loop: `step`, then `every_s`, for ever; a step that breaks is logged and runs again at its next turn."""
        while True:
            try:
                result = step()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("the gate's %s step broke; it runs again in %g s", step.__name__, every_s)
            await self._sleep(every_s)

    # Saving.

    def save(self) -> None:
        """Every part's on_change: the whole state written, and on disk, before this returns, so saves land in call
        order (the module's docstring). A save that fails raises, so a part that must save first (a load before its
        call, a drain before `drain`, make-room's hold before its first drain) sends nothing; it shows under `health`
        too. One slower than SAVE_SLOW_S is logged with how long it took."""
        began = time.monotonic()
        try:
            save_state(self.state_dir, self.state, now=self._clock())
        except (OSError, ValueError) as err:
            self._unread("state", f"the gate's state couldn't be saved in {self.state_dir} ({_said(err)})")
            raise
        finally:
            took = time.monotonic() - began
            if took > SAVE_SLOW_S:
                _log.warning("saving the gate's state took %.1f s, and the gate's event loop waited that long", took)
        self._read_ok("state")
        if self.admitter is not None:
            self._note_starts()
            self._send_state_if_changed()

    def _save_quietly(self) -> None:
        """A save whose failure is shown under `health`, and logged, and needn't stop what made the change."""
        try:
            self.save()
        except (OSError, ValueError) as err:
            _log.error("the gate's state couldn't be saved (%s); the next change tries again", _said(err))

    # The loops' steps.

    def read_memory(self) -> None:
        """MemAvailable, read fresh: when it last rose above the warn line (the brake's release counts from there, so a
        dip between two checks restarts its five minutes), `memory_warning` once per fall under it, and `changed()` to
        the admitter and the drainer, so each request and each drain looks again."""
        try:
            mem = self._read_meminfo()
        except (OSError, ValueError) as err:
            self._unread("memory", f"/proc/meminfo couldn't be read ({_said(err)})")
            return
        self._read_ok("memory")
        self._mem = mem
        now = self._clock()
        brake = self.registry.brake
        if mem.available_gib > brake.warn_gib:
            if self._above_since is None:
                self._above_since = now
        else:
            self._above_since = None
        if self._warning.check(mem.available_gib, brake.warn_gib):
            self._emit("memory_warning", f"memory:{now:.3f}", available_gib=mem.available_gib,
                       warn_gib=brake.warn_gib, brake_gib=brake.brake_gib)
        self.admitter.changed()
        self.drainer.changed()

    async def read_running(self) -> None:
        """llama-swap's /running, every second, one read at a time: a caller while one is in flight (apply's end,
        beside the loop) awaits that one's answer (the controller's ruling at Task 18's review)."""
        reading = self._reading
        if reading is None or reading.done():
            reading = self._reading = asyncio.get_running_loop().create_task(self._read_once())
        await asyncio.shield(reading)

    async def _read_once(self) -> None:
        """One read. A read that doesn't answer changes no record, and never reaches `observe_running`, least of all
        as `[]`, which would free the one-load slot blind (the controller's ruling at Task 15's second re-review):
        llama-swap counts as down. One that answers: the brake's events first, so a starting model's reading is still
        there when its unload by the brake is read (Task 14's review); then each record settled against the read
        (`_reconcile`); then the admitter and the drainer, llama-swap's watch, the residents that left, and apply's
        automatic end. An answer that comes after a newer read's is dropped, so what a newer read showed gone is
        never put back."""
        before = {name: (record, record.state, record.unload_requested_at)
                  for name, record in self.state.models.items()}
        sent_at = self._clock()
        self._read_seq += 1
        seq = self._read_seq
        try:
            running = await self._swap.running()
        except LlamaSwapError as err:
            if seq < self._done_seq:
                return
            self._done_seq = seq
            if self._answering:
                _log.warning("llama-swap's /running doesn't answer (%s)", err)
            self._answering = False
            self.admitter.set_llamaswap_up(False)
            self._watch_llama_swap(False)
            return
        if seq < self._done_seq:
            _log.info("an answer of /running came after a newer read's, so it is dropped")
            return
        self._done_seq = seq
        self._ingest_brake()
        gone = self._reconcile(running, before, sent_at)
        self._answering = True
        self._answered_at = sent_at
        self.admitter.observe_running(running)
        self.admitter.set_llamaswap_up(True)
        self.drainer.changed()
        self._watch_llama_swap(True)
        left = self._note_listed(running, sent_at, gone)
        if left:
            await self._residents_left(left)
        self._check_apply_end()
        self._send_state_if_changed()

    def each_second(self) -> None:
        """The brake's events (whatever /running does), apply's lapse, and the activity record: each in turn, one
        that breaks never keeping the others from running (the controller's ruling at Task 18's review)."""
        now = self._clock()
        for name, duty in (("the brake's events", self._ingest_brake), ("apply's lapse", lambda: self._lapse(now)),
                           ("the activity record", lambda: (self._note_starts(), self._write_activity(now))),
                           ("the front's state", self._send_state_if_changed)):
            try:
                duty()
            except Exception:
                _log.exception("%s broke; it runs again in a second", name)

    def _lapse(self, now: float) -> None:
        hold = self.state.applying
        if hold is not None and not hold.ended and now - hold.renewed_at >= APPLY_LAPSE_S:
            self.end_apply("lapsed")

    async def each_five_s(self) -> None:
        """The four units; the idle check (never while apply's hold stands, under which nothing unloads, nor while
        llama-swap isn't answering, when no unload could follow a drain); the pins', sessions' and make-room hold's
        ends; the brake's release check; then the holders and the bypass check, read off the event loop.

        The brake's hold lifts by itself only while llama-swap answers (`_answering_now`): without it the gate can't
        verify its reloads fit (the controller's ruling at Task 18's review). Otherwise the check is given no time
        above the warn line, so Room never releases, and still says when a hold waits for Dan. Dan's own `make
        brake-release` (Room.release) lifts it whatever llama-swap does."""
        await self._watch_units()
        now = self._clock()
        answering = self._answering_now(now)
        hold = self.state.applying
        if (hold is None or hold.ended) and answering:
            self._idle_check(now)
        for step in (self.pins.expire, self.sessions.prune, self.room.expire):
            try:
                step(now)
            except Exception:
                _log.exception("the gate's %s broke; it runs again in 5 s", step.__name__)
        try:
            await self.room.check_brake(now, above_since=self._above_since if answering else None)
        except Exception:
            _log.exception("the brake's release check broke; it runs again in 5 s")
        await self._background()

    # /running's records (rule 9 counts every model llama-swap runs).

    def _reconcile(self, running: list[Running], before: dict[str, tuple], sent_at: float) -> dict[str, ModelRecord]:
        """Each record no load of the admitter's and no drain holds, settled against a read that answered. A record
        that changed while the read was in flight is left for the next read, which can't be stale for it. A model the
        read lists that the state lacks is counted, `starting` at the registry's footprint (its largest for a model it
        doesn't know), with no fall, so *owed* errs safe: a start around the gate, or one claimed between the start's
        read and the Admitter's withdraw (the controller's ruling at Task 15's re-review). One the read no longer
        lists has gone: its memory with it. A record restore left `starting` with its unload requested, in a state the
        gate didn't know, is resumed once /running shows one it knows (Task 13's re-review). Returns the records it
        dropped as gone."""
        listed = {entry.model: entry.state for entry in running}
        now = self._clock()
        changed = False
        gone: dict[str, ModelRecord] = {}
        for name in sorted(set(self.state.models) | set(listed)):
            if self.admitter.has_load(name) or self.drainer.draining(name):
                continue
            record = self.state.models.get(name)
            was = before.get(name)
            if (was is None and record is not None) or (was is not None and (
                    record is not was[0] or record.state != was[1] or record.unload_requested_at != was[2])):
                continue
            shown = listed.get(name)
            if record is None:
                if shown is None:
                    continue
                footprint = self._footprint(name)
                self.state.models[name] = ModelRecord(
                    name=name, footprint_gib=footprint, loaded_at=now, load_fall_gib=None, fall_flagged=False,
                    rss_anon_at_load_gib=None, last_use=now, state="stopping" if shown == "stopping" else "starting")
                _log.warning("llama-swap runs %s (%s), which the gate didn't load: counted at %s GiB", name, shown,
                             footprint)
                changed = True
            elif record.unload_requested_at is not None:
                if record.state != "starting" or shown not in (None, "ready", "stopping"):
                    continue  # a drain's, resumed at the start; or still in no state the gate knows
                if shown is None:
                    del self.state.models[name]
                    self.state.ticketed.pop(name, None)
                    self.state.issued.pop(name, None)
                else:
                    record.state = "draining" if shown == "ready" else "stopping"
                self._resume(name, record.drain_id, record.drain_why or "unknown")
                changed = True
            elif shown is None:
                _log.info("%s left /running (it was %s), with no load or unload of the gate's under way", name,
                          record.state)
                del self.state.models[name]
                self.state.ticketed.pop(name, None)
                self.state.issued.pop(name, None)
                gone[name] = record
                changed = True
            elif shown != record.state and (shown, record.state) in (
                    ("ready", "starting"), ("stopping", "ready"), ("stopping", "starting"), ("starting", "ready")):
                record.state = shown  # a start it counted came ready; the brake's unload, or one around the gate
                changed = True
        if changed:
            self._save_quietly()
        return gone

    def _footprint(self, name: str) -> float:
        known = self.registry.models.get(name)
        if known is not None:
            return float(known.footprint_gib)
        return float(max((model.footprint_gib for model in self.registry.models.values()), default=0.0))

    def _note_listed(self, running: list[Running], sent_at: float,
                     gone: Mapping[str, ModelRecord]) -> list[tuple[str, str, float]]:
        """The residents the last answered read listed and this one doesn't, each with its state in the last read that
        listed it and when that read was *sent*, never when its answer came, so an unload the drainer saw finish while
        the read was in flight still counts as the gate's own (the controller's ruling at Task 17's review). A resident
        whose record this read dropped, loaded since the last read that answered, so never listed, left too: as its
        record had it, since its load."""
        listed = {entry.model: entry.state for entry in running}
        left = []
        for name, (shown, since) in list(self._listed.items()):
            if name in listed:
                continue
            del self._listed[name]
            left.append((name, shown, since))
        left += [(name, record.state, record.loaded_at) for name, record in gone.items()
                 if name not in listed and all(name != each for each, _, _ in left)]
        for name, shown in listed.items():
            self._listed[name] = (shown, sent_at)
        return [(name, shown, since) for name, shown, since in left
                if name in self.registry.models and self.registry.models[name].resident]

    async def _residents_left(self, left: list[tuple[str, str, float]]) -> None:
        """The residents' return (Dan's decision, 2026-10-07). Never during apply's hold: its end queues every
        resident. Otherwise llama-swap's unit is read first, so a restart is known before the residents' rule: a
        resident gone with a llama-swap start since it was last listed reloads with that start, `apply` when an apply
        announced it, even after its hold ended or lapsed, else as llama-swap's own `restart`, through the Preloader,
        in the residents' hourly bound; any other is asked of Room's residents' rule, once."""
        hold = self.state.applying
        if hold is not None and not hold.ended:
            _log.info("%s left /running during make apply's hold: its end reloads them",
                      ", ".join(name for name, _, _ in left))
            return
        await self._read_unit_now("llama-swap")
        started = self._llama_started()
        announced, unplanned = [], []
        for name, shown, since in left:
            if started is not None and started >= since:
                (announced if self._announced(started) else unplanned).append(name)
                continue
            try:
                self.room.resident_left(name, shown=shown, since=since)
            except Exception:
                _log.exception("the residents' rule broke for %s", name)
        if announced:
            hold = self.state.applying
            self._report_apply(hold, self._queue(announced, "apply"))
        if unplanned:
            _log.warning("llama-swap started anew with no apply announced, and %s went with it: reloading them",
                         ", ".join(unplanned))
            self._spawn(self._preload(unplanned, "restart"))

    def _announced(self, started: float) -> bool:
        """Whether llama-swap's start at `started` is the one an apply announced (the controller's ruling at Task 17's
        re-review): the first start at or after its `begin`, which named llama-swap, while its hold stands or within
        APPLY_LAPSE_S of its end, as when the hold lapsed while the restart was still under way. Any later start is
        llama-swap's own. A hold that ended before this gate started has no end time here, and claims none."""
        hold = self.state.applying
        if hold is None or hold.begun_at is None or not _names_llama_swap(hold.restarting) or started < hold.begun_at:
            return False
        if hold.ended:
            ended_at = self._apply_ended.get(hold.since)
            if ended_at is None or started > ended_at + APPLY_LAPSE_S:
                return False
        seen = self._apply_starts.setdefault(hold.since, started)
        return seen == started

    def _queue(self, names: Iterable[str], after: str) -> dict[str, asyncio.Future]:
        """Each of `names` queued for the gate's own reload, ahead of every request, in this order (Task 15), none that
        kept_out leaves out; their futures."""
        hold = self._read_hold()
        futures = {}
        for name in names:
            why = kept_out(name, self.state, self.registry, hold)
            if why is not None:
                _log.info("not reloading %s: %s", name, why)
                continue
            futures[name] = self.admitter.reload(name, after)
        return futures

    # Apply's hold.

    def drain_all(self, uid: int) -> dict:
        """make apply's `drain-all`: apply's hold, written to the state and saved before this answers (a save that
        fails takes it back, and raises, so apply stops); then the admitter's `restarting`, and the front told to hold
        every new request. A hold that stands is renewed. Returns its `since`, which apply renews it by."""
        now = self._clock()
        before = self.state.applying
        hold = before
        if hold is None or hold.ended:
            hold = ApplyHold(since=now, by_uid=uid, renewed_at=now, begun_at=None, restarting=[], ended=False)
            self.state.applying = hold
        else:
            hold.renewed_at = now
        try:
            self.save()
        except (OSError, ValueError):
            if hold is not before:
                self.state.applying = before
            raise
        self._apply_loaded.setdefault(hold.since, list(self.state.models))
        self.admitter.set_restarting(True)
        self.front.send({"op": "hold_all", "lapse_s": APPLY_LAPSE_S})
        return {"since": hold.since}

    def apply_renew(self, since: float, uid: int) -> dict:
        """`apply/renew`: `{ok: true}` while the hold stands, its renewal moved to now; `{ended: true}` once it has
        ended, so a renewal never starts a hold again. A renewal of a hold the gate doesn't know at all, as after a
        start fresh from a damaged state file, re-creates it, `since` the renewal's, so the restart stays held (the
        controller's ruling at Task 13's re-review)."""
        hold = self.state.applying
        now = self._clock()
        if hold is None:
            self.state.applying = ApplyHold(since=float(since), by_uid=uid, renewed_at=now, begun_at=None,
                                            restarting=[], ended=False)
            _log.warning("a renewal of an apply hold the gate doesn't know (since %s): re-created, so the restart "
                         "stays held", since)
            self._save_quietly()
            self.admitter.set_restarting(True)
            self.front.send({"op": "hold_all", "lapse_s": APPLY_LAPSE_S})
            return {"ok": True}
        if hold.ended:
            return {"ended": True}
        if hold.since != since:
            _log.warning("a renewal names apply's hold of %s, and the one standing began at %s: that one is renewed",
                         since, hold.since)
        hold.renewed_at = now
        self._save_quietly()
        return {"ok": True}

    def apply_begin(self, restarting: list[str]) -> dict:
        """`apply/begin`, before the first restart: the units it restarts, saved with the hold. `{ended: true}` when no
        hold stands, so apply restarts nothing it doesn't hold for. A save that fails takes it back, and raises."""
        hold = self.state.applying
        if hold is None or hold.ended:
            return {"ended": True}
        before = (hold.begun_at, hold.restarting)
        hold.begun_at, hold.restarting = self._clock(), [str(unit) for unit in restarting]
        try:
            self.save()
        except (OSError, ValueError):  # not saved, so not begun: apply stops before its first restart
            hold.begun_at, hold.restarting = before
            raise
        self._apply_loaded.setdefault(hold.since, list(self.state.models))
        return {"ok": True}

    async def apply_end(self) -> dict:
        """`apply/end`, once llama-swap answers again: /running read first, so the residents the restart took are
        known gone, then the one end."""
        await self._read_now()
        self.end_apply("end")
        return {}

    async def undrain_all(self) -> dict:
        """`undrain-all`, apply's cleanup on any failure: the same one end."""
        await self._read_now()
        self.end_apply("undrain-all")
        return {}

    async def _read_now(self) -> None:
        try:
            await self.read_running()
        except Exception:
            _log.exception("reading /running before apply's end broke; the hold ends all the same")

    def end_apply(self, why: str) -> bool:
        """Apply's hold's one end, whatever ends it (`end`, `undrain-all`, `answered`, `lapsed`), in one step of the
        event loop, with nothing admitted in between: the hold marked ended and saved, the registry read again,
        `release_all` and the new model list sent, and the residents' reloads queued ahead of anything else (Task
        15's order); a line in *recent* naming why; then `apply_restarted` once the reloads have settled, when
        llama-swap did restart. False, doing nothing, when no hold stands: a second end."""
        hold = self.state.applying
        if hold is None or hold.ended:
            return False
        now = self._clock()
        hold.ended = True
        self._save_quietly()
        self._apply_ended[hold.since] = now
        started = self._llama_started()
        if hold.begun_at is not None and started is not None and started >= hold.begun_at:
            self._apply_starts.setdefault(hold.since, started)  # the restart it announced, seen already
        self._reread_registry()
        self.admitter.set_restarting(False)  # its pass runs in the next step, after the reloads below are queued
        self.front.send({"op": "release_all"})
        self._send_state_if_changed(force=True)
        residents = [name for name, model in self.registry.models.items()
                     if model.resident and name not in self.state.models]
        futures = self._queue(residents, "apply")
        self.state.recent.append(RecentRecord(at=now, kind="apply", model=None, uids=[],
                                              text=messages.recent_apply_ended(why, APPLY_LAPSE_S)))
        self._save_quietly()
        _log.info("make apply's hold ended (%s); reloading %s", why, ", ".join(futures) or "nothing")
        self._report_apply(hold, futures)
        return True

    def _reread_registry(self) -> None:
        """The registry read again from disk, as an apply's end has it: the admitter's (which Room, the drainer, the
        Preloader, Pins and Sessions read) and the Notifier's. One that won't read leaves the one the gate had."""
        try:
            fresh = self._load_registry(self._registry_path)
        except Exception as err:  # a registry apply validated, read back unreadable: keep serving with the last one
            self._registry_problem = (f"the registry {self._registry_path} couldn't be read again at make apply's end "
                                      f"({_said(err)}), so the gate keeps the one it had")
            _log.error("%s", self._registry_problem)
            return
        self._registry_problem = None
        self._registry = fresh
        self.admitter.registry = fresh
        self.notifier.registry = fresh

    def _check_apply_end(self) -> None:
        """Apply's automatic end: llama-swap's unit started after `begin` named it, and a /running read sent since
        then answered."""
        hold = self.state.applying
        if hold is None or hold.ended or hold.begun_at is None or not _names_llama_swap(hold.restarting):
            return
        started = self._llama_started()
        if started is None or started < hold.begun_at or self._answered_at is None or self._answered_at < started:
            return
        self.end_apply("answered")

    def _report_apply(self, hold: ApplyHold | None, futures: dict[str, asyncio.Future]) -> None:
        if hold is None:
            return
        report = self._reports.get(hold.since)
        if report is None:
            report = self._reports[hold.since] = _ApplyReport(hold.since, hold.begun_at, list(hold.restarting),
                                                              self._apply_loaded.get(hold.since, []))
        report.futures.update(futures)
        if report.task is None or report.task.done():
            report.task = self._spawn(self._apply_restarted(report))

    async def _apply_restarted(self, report: _ApplyReport) -> None:
        """`apply_restarted`, which says the residents reloaded: sent only once each reload has settled (Admitted, or
        refused), never as they are queued (the controller's ruling at Task 17's review), and only when llama-swap
        started after `begin` named it and a /running read since has shown what the restart took."""
        if report.begun_at is None or not _names_llama_swap(report.restarting):
            return
        started = None
        for _ in range(REPORT_POLLS):
            pending = [future for future in report.futures.values() if not future.done()]
            if pending:
                await asyncio.wait(pending)
                continue
            await self._read_unit_now("llama-swap")
            started = self._llama_started()
            if started is None or started < report.begun_at:
                return  # no restart yet: the reloads a later one queues report it
            if self._answered_at is not None and self._answered_at >= started:
                if all(future.done() for future in report.futures.values()):
                    break
                continue
            await self._sleep(SECOND_S)
        else:
            _log.warning("make apply restarted the model service, and /running hasn't answered since: no "
                         "apply_restarted")
            return
        if report.sent:
            return
        report.sent = True
        registry = self.registry
        reloaded = [registry.models[name].label if name in registry.models else name
                    for name, future in report.futures.items()
                    if not future.cancelled() and future.exception() is None and isinstance(future.result(), Admitted)]
        on_demand = [registry.models[name].label for name in report.loaded
                     if name in registry.models and not registry.models[name].resident
                     and name not in self.state.models]
        self._emit("apply_restarted", f"apply:{report.since:.3f}", at=started, reloaded=reloaded, on_demand=on_demand)

    # The units.

    async def _watch_units(self) -> None:
        for unit in unitwatch.UNITS:
            await self._read_unit_now(unit)

    async def _read_unit_now(self, unit: str) -> UnitState | None:
        """`unit`'s `systemctl show`, off the event loop, into the watches; a reading that fails shows under `health`,
        and the last good one stands."""
        try:
            st = await asyncio.to_thread(self._read_unit, unit)
        except UnitStateError as err:
            self._unread(f"unit:{unit}", str(err))
            return None
        except Exception as err:  # noqa: BLE001 (a reading that fails never ends the loop)
            self._unread(f"unit:{unit}", f"{unitwatch.UNITS.get(unit, unit)} couldn't be read ({_said(err)})")
            return None
        self._read_ok(f"unit:{unit}")
        self._units[unit] = st
        try:
            self._backup.observe(unit, st, answering=self._answering if unit == "llama-swap" else None)
        except Exception:
            _log.exception("the unit watch broke on %s", unit)
        if unit == "llama-swap":
            self._check_apply_end()
        return st

    def _answering_now(self, now: float) -> bool:
        """Whether llama-swap counts as answering now: its last /running read answered, and was sent within
        LLAMA_SWAP_HUNG_S, so a read that hangs, or a loop that stopped reading, never counts as answering."""
        return self._answering and self._answered_at is not None and now - self._answered_at <= LLAMA_SWAP_HUNG_S

    def _llama_started(self) -> float | None:
        """When llama-swap's unit last started, by its last good reading; None while it isn't active."""
        st = self._units.get("llama-swap")
        return st.active_since if st is not None and st.active else None

    def _watch_llama_swap(self, answering: bool) -> None:
        """LlamaSwapWatch, fed each /running read with llama-swap's unit as last read and apply's hold."""
        st = self._units.get("llama-swap")
        if st is None:
            return
        try:
            self._llama_watch.observe(answering, st, applying=self.state.applying)
        except Exception:
            _log.exception("llama-swap's watch broke")

    # The brake's events, the activity record, and the starts.

    def _ingest_brake(self) -> None:
        """The brake's new events, as the notifications they send (Task 14), with this boot's id, the gate's clock,
        and each starting model's MemAvailable at admission as `starts`; its marks and its place saved."""
        starts = {name: available for name, (available, _) in self._starts.items()}
        after, marks = self.state.brake_events_after, dict(self.state.brake_marks)
        try:
            found = ingest_brake_events(self._events_path, self.state, self.registry, boot_id=self.boot_id,
                                        now=self._clock(), starts=starts)
        except (OSError, ValueError) as err:
            self._unread("brake events", f"the brake's events {self._events_path} couldn't be read ({_said(err)})")
            return
        self._read_ok("brake events")
        if found or self.state.brake_events_after != after or self.state.brake_marks != marks:
            self._save_quietly()
        if self.state.brake_marks != marks:
            self.admitter.changed()
        for kind, key, fields in found:
            self._emit(kind, key, **fields)

    def _note_starts(self) -> None:
        """Each starting model's MemAvailable at admission, kept STARTS_KEPT_S past its start, for the brake's
        events read after it."""
        now = self._clock()
        for name, load in self.admitter.starting_loads().items():
            if load.available_at_start is not None:
                self._starts[name] = (load.available_at_start, now)
        for name in [name for name, (_, seen) in self._starts.items() if now - seen > STARTS_KEPT_S]:
            del self._starts[name]

    def _write_activity(self, now: float) -> None:
        """`activity.json`, whole: each model's state, its requests in flight and its last use, from the front's
        snapshot; for a model `starting` (from the moment its ticket is issued), its ticket's figures, what the brake
        reads (Task 23)."""
        counts_by = (self.front.snapshot or {}).get("models") or {}
        loads = self.admitter.starting_loads()
        models: dict[str, dict] = {}
        for name, record in self.state.models.items():
            counts = counts_by.get(name) or {}
            last = record.last_use
            end = counts.get("last_end_at")
            if _finite(end) and end > last:
                last = float(end)
            entry: dict[str, Any] = {"state": record.state, "inflight": int(counts.get("count") or 0),
                                     "last_use": last}
            if record.state == "starting":
                load = loads.get(name)
                entry |= {"admitted_gib": load.admitted_gib if load is not None else record.footprint_gib,
                          "started_at": load.started_at if load is not None and load.started_at is not None
                          else record.loaded_at,
                          "available_at_start": load.available_at_start if load is not None else None}
            models[name] = entry
        try:
            tickets.write_whole(self.state_dir / ACTIVITY_FILE, {"written_at": now, "boot_id": self.boot_id,
                                                                 "models": models})
        except (OSError, ValueError) as err:
            self._unread("activity", f"the activity record in {self.state_dir} couldn't be written ({_said(err)})")
            return
        self._read_ok("activity")
        self._activity_at = now

    def _last_alive(self) -> float | None:
        """The last run's activity record's `written_at`: the last moment it was alive, read before this run writes."""
        try:
            record = tickets.read_record(self.state_dir / ACTIVITY_FILE, own=True)
        except (OSError, ValueError):
            return None
        written = record.get("written_at") if isinstance(record, dict) else None
        return float(written) if _finite(written) else None

    # The idle check.

    def _idle_check(self, now: float) -> None:
        """Each on-demand model idle_due names drained with why `idle`, each in a task of its own, so a pin, a session
        or a request that comes meanwhile is checked again right before it is marked (Task 16's review)."""
        try:
            due = idle_due(now, self.state, self.registry, self.front.snapshot, self.sessions.live)
        except Exception:
            _log.exception("the idle check broke; it runs again in 5 s")
            return
        for name in due:
            if not self.drainer.draining(name):
                self._spawn(self.drainer.drain(name, "idle"))

    # The holders and the bypass check, every 5 s, off the event loop.

    async def _background(self) -> None:
        loaded: dict[str, tuple[str, float]] = {}
        for name, record in self.state.models.items():
            if record.state == "starting":
                continue
            known = self.registry.models.get(name)
            held = self.admitter.held_gib(name) if record.load_fall_gib is not None else None
            loaded[name] = (known.label if known else name,
                            float(held) if held is not None else float(record.footprint_gib))
        ticketed = dict(self.state.ticketed)
        now = self._clock()
        gathered = await asyncio.to_thread(self._gather, loaded, ticketed, engine_ports(self.registry), now)
        for source, problem in gathered.problems.items():
            if problem is None:
                self._read_ok(source)
            else:
                self._unread(source, problem)
        if gathered.holders is not None:
            self._holders = gathered.holders
        if gathered.engines is not None:
            self._check_bypass(gathered.engines, gathered.started)
        for record in gathered.refusals:
            try:
                when = datetime.fromisoformat(record["at"]).timestamp()
            except (ValueError, OverflowError, OSError):
                continue
            if self._boot_time is not None and when < self._boot_time:
                continue  # another boot's
            if self._boot_time is None and self._started_at is not None and when < self._started_at:
                continue  # this boot's start unknown: only what came since the gate started
            self._backstop.add((record["model"], record["at"], record["code"], record["reason"]))

    def _gather(self, loaded: dict[str, tuple[str, float]], ticketed: dict[str, Ticketed], ports: dict[str, int],
                now: float) -> _Gathered:
        """Off the event loop: nvidia-smi, the engines on their ports, the top holders, launch's started/ records and
        refusal records. Each read that fails is caught, PermissionError and OSError alike, and said, so a failed
        read never ends the loop (the controller's ruling at Task 8's review); none is read as nothing."""
        problems: dict[str, str | None] = {}
        try:
            nvidia = procs.parse_nvidia_apps(self._nvidia_smi())
            problems["nvidia-smi"] = None
        except Exception as err:  # noqa: BLE001 (not installed, timed out, failed: GPU figures unknown this time)
            nvidia = {}
            problems["nvidia-smi"] = f"nvidia-smi couldn't be read ({_said(err)}): GPU memory isn't in the holders"
        engines: dict[str, tuple[int, int, bool]] | None = {}
        holders = None
        try:
            for model, port in ports.items():
                engine = ticketed.get(model)
                pid = procs.engine_pid(port, spark_uid=self._spark_uid, proc=self._proc,
                                       recorded=engine.pid if engine is not None else None,
                                       recorded_start=engine.start_time if engine is not None else None)
                if pid is None:
                    continue
                ours = (engine is not None and pid == engine.pid and engine.start_time is not None
                        and procs.still_running(pid, engine.start_time, proc=self._proc))
                engines[model] = (port, pid, ours)
            dans = set(self._dan_uids()) if callable(self._dan_uids) else set(self._dan_uids)
            holders = procs.top_holders(loaded, nvidia, engine_pids={pid for _, pid, _ in engines.values()},
                                        dan_uids=dans, proc=self._proc)
            problems["processes"] = None
        except OSError as err:
            engines = None
            problems["processes"] = (f"the processes in {self._proc} couldn't be read ({_said(err)}): the holders and "
                                     "the bypass check wait for the next reading")
        try:
            started = tickets.started(self._launch, now=now, boot_id=self.boot_id)
            problems["started"] = None
        except OSError as err:
            started = None
            problems["started"] = (f"launch's started records in {self._launch} couldn't be read ({_said(err)}): the "
                                   "bypass check counts none as ticketed meanwhile")
        refusals = []
        for model in ports:
            record, problem = launches.check_refusal(self._launch, model)
            problems[f"refusal:{model}"] = problem
            if record is not None and record["code"] in BACKSTOP_CODES:
                refusals.append(record)
        return _Gathered(engines, holders, started, refusals, problems)

    def _check_bypass(self, engines: dict[str, tuple[int, int, bool]], started: list[dict] | None) -> None:
        """Each engine on an engine port ticketed by its recorded start, or by a live started/ record of a ticket the
        gate issued (an engine can write started/, so a record of an id the gate never issued hides nothing); with the
        records unread, none counts. Any other is a load around the gate."""
        unticketed = []
        for model, (port, pid, ours) in sorted(engines.items()):
            if ours:
                continue
            if started is not None and any(record["pid"] == pid and record["model"] == model
                                           and self.state.issued.get(model) == record["id"] for record in started):
                continue
            unticketed.append({"model": model, "port": port, "pid": pid})
            if (model, pid) not in self._reported:
                self._reported.add((model, pid))
                _log.warning("an engine of %s (pid %s, port %s) runs with no ticket of the gate's: a load around the "
                             "gate", model, pid, port)
        self._unticketed = unticketed

    def holders(self, names_processes: bool) -> list[Holder]:
        """The top memory holders as last read, off the event loop (rule 7); the words mask Dan's processes for a group
        that doesn't name them."""
        return list(self._holders)

    # The front.

    def _model_list(self) -> list[dict]:
        return [{"name": name, "roles": list(model.roles), "label": model.label, "resident": model.resident}
                for name, model in self.registry.models.items()]

    def hello(self) -> dict:
        """The events call's first line: when the gate started, whether apply's hold stands (so a restarted front
        holds again), its bound, and the registry's model list."""
        hold = self.state.applying
        return {"op": "hello", "gate_started_at": self._started_at, "applying": hold is not None and not hold.ended,
                "lapse_s": APPLY_LAPSE_S, "models": self._model_list()}

    def state_event(self) -> dict:
        """What's loaded, as the front serves it: `ready` only a model serving with no unload requested; with the
        model list, so a model added or renamed reaches the front without its restart."""
        ready, starting, draining = [], [], []
        for name, record in self.state.models.items():
            if record.state == "ready" and record.unload_requested_at is None:
                ready.append(name)
            elif record.state == "starting":
                starting.append(name)
            else:
                draining.append(name)
        return {"op": "state", "ready": sorted(ready), "starting": sorted(starting), "draining": sorted(draining),
                "models": self._model_list()}

    @staticmethod
    def _state_key(event: dict) -> tuple:
        return (tuple(event["ready"]), tuple(event["starting"]), tuple(event["draining"]),
                tuple((m["name"], m["label"], tuple(m["roles"]), m["resident"]) for m in event["models"]))

    def _send_state_if_changed(self, *, force: bool = False) -> None:
        if not self.front.connected:
            return
        event = self.state_event()
        key = self._state_key(event)
        if key != self._sent_state or force:
            self._sent_state = key
            self.front.send(event)

    def open_front_events(self) -> asyncio.Queue:
        """The front's events call opened: its queue, `hello` and `state` at its head; then every word the front is
        owed (the undrains restore saved, and those a dropped call left), once the call is open (Task 16)."""
        event = self.state_event()
        queue = self.front.open([self.hello(), event])
        self._sent_state = self._state_key(event)
        self.drainer.send_owed()
        return queue

    def inflight(self, snapshot: dict) -> None:
        """The front's whole in-flight report. Its first after a start fresh from damage settles every drain it
        holds: each finished as a drain with why `unknown`, then unloaded, never undrained, since an undrain could cut
        a request on a model whose unload may be queued (the controller's ruling at Task 13's re-review); then the
        damage is no longer news to the front, and the state says so."""
        self.front.report(snapshot)
        if not self._settle_front_drains:
            return
        self._settle_front_drains = False
        for model in snapshot.get("draining") or []:
            if isinstance(model, str):
                self._resume(model, None, "unknown")
        if self.state.fresh_after_damage:
            self.state.fresh_after_damage = False
            self._save_quietly()

    def quiet(self) -> dict:
        """`/v1/quiet`: how long nothing has been in flight, by the front's counts (0 while anything is), the model
        that answered last, and each request in flight with who asked, its key by its label."""
        now = self._clock()
        snapshot = self.front.snapshot
        if snapshot is None:  # what is in flight isn't known: never quiet
            return {"quiet_for_s": 0.0, "last_label": None, "inflight": []}
        inflight, busy = [], False
        last_end, last_model = None, None
        for name, counts in (snapshot.get("models") or {}).items():
            busy = busy or (counts.get("count") or 0) > 0
            for request in counts.get("requests") or []:
                key = self._keys.get(request.get("key"))
                inflight.append({"model": name, "model_label": self._label(name),
                                 "key_label": key.label if key is not None else str(request.get("key")),
                                 "age_s": max(0.0, now - float(request.get("started_at") or now))})
            end = counts.get("last_end_at")
            if _finite(end) and (last_end is None or end > last_end):
                last_end, last_model = float(end), name
        if busy or inflight:
            quiet = 0.0
        else:
            since = last_end if last_end is not None else snapshot.get("front_started_at")
            quiet = max(0.0, now - float(since)) if _finite(since) else 0.0
        inflight.sort(key=lambda row: -row["age_s"])
        return {"quiet_for_s": quiet, "last_label": self._label(last_model) if last_model else None,
                "inflight": inflight}

    def _label(self, name: str) -> str:
        known = self.registry.models.get(name)
        return known.label if known is not None else name

    # The status view.

    def status_view(self, *, sees: Callable[[RefusalRecord | RecentRecord], bool] | None = None,
                    uses_hold: bool | None = None) -> dict:
        """The StatusView (gateproto), from the last readings: nothing slow (nvidia-smi, the /proc scan, systemctl) is
        read here, only the brake's hold and each engine's RssAnon, as admission reads them. The control socket's in
        full; `sees` keeps the refusals and events a status-socket caller may see (Task 19), and `uses_hold` is whether
        its group loads into make-room's hold (Dan's group's by default)."""
        now = self._clock()
        state, registry = self.state, self.registry
        mem = self._mem
        if mem is None:  # no reading yet: one taken here, and kept nowhere, so the view changes nothing
            try:
                mem = self._read_meminfo()
            except (OSError, ValueError):
                mem = MemInfo(0.0, 0.0)
        if uses_hold is None:
            group = registry.key_groups.get("dan")
            uses_hold = True if group is None else group.uses_hold
        try:
            free = float(self.admitter.free_for_a_load(uses_hold=uses_hold))
        except (OSError, ValueError):
            free = 0.0
        footprints = sum(record.footprint_gib for record in state.models.values())
        memory_view = {"total_gib": mem.total_gib, "available_gib": mem.available_gib,
                       "brake_gib": registry.brake.brake_gib, "warn_gib": registry.brake.warn_gib,
                       "above_brake_gib": mem.available_gib - registry.brake.brake_gib,
                       "reserve_gib": registry.budget.reserve_gib, "owed_gib": float(self.admitter.owed()),
                       "held_gib": state.room_hold.size_gib if state.room_hold is not None else 0.0,
                       "free_for_a_load_gib": free,
                       "unaccounted_gib": registry.budget.idle_available_gib - mem.available_gib - footprints}
        hold = self.state.applying
        return {"schema": STATUS_SCHEMA, "host": self._host, "at": now, "memory": memory_view,
                "models": self._model_views(now), "waiting": self.admitter.waiting(), "paused": self._paused(now),
                "held": None if state.room_hold is None else {"size_gib": state.room_hold.size_gib,
                                                              "until": state.room_hold.until},
                "pins": [{"model": pin.model, "label": self._label(pin.model), "until": pin.until}
                         for pin in state.pins.values() if pin.until is None or pin.until > now],
                "sessions": [{"id": s.id, "model": s.model, "label": s.label, "started_at": s.started_at,
                              "expires_at": s.renewed_at + SESSION_TTL_S} for s in state.sessions.values()],
                "recent": self._recent(sees), "health": self._health(now),
                "applying": None if hold is None or hold.ended else {"since": hold.since,
                                                                     "restarting": list(hold.restarting)},
                "problems": self._problems()}

    def _model_views(self, now: float) -> list[dict]:
        registry, state = self.registry, self.state
        counts_by = (self.front.snapshot or {}).get("models") or {}
        views = []
        for name in [*registry.models, *(name for name in state.models if name not in registry.models)]:
            known, record = registry.models.get(name), state.models.get(name)
            counts = counts_by.get(name) or {}
            inflight = int(counts.get("count") or 0)
            oldest, end = counts.get("oldest_started_at"), counts.get("last_end_at")
            if record is None:
                shown, last = "not_loaded", float(end) if _finite(end) else None
            else:
                shown = ("draining" if record.state == "ready" and record.unload_requested_at is not None
                         else record.state)
                last = max(record.last_use, float(end)) if _finite(end) else record.last_use
            pin = state.pins.get(name)
            pinned = pin is not None and (pin.until is None or pin.until > now)
            mark = state.brake_marks.get(name)
            views.append({"name": name, "label": known.label if known else name,
                          "resident": bool(known and known.resident),
                          "footprint_gib": record.footprint_gib if record is not None else known.footprint_gib,
                          "state": shown, "inflight": inflight,
                          "oldest_request_s": max(0.0, now - oldest) if inflight and _finite(oldest) else None,
                          "last_use": last, "pinned": pinned, "pinned_until": pin.until if pinned else None,
                          "sessions": [s.label for s in state.sessions.values() if s.model == name and self._live(s)],
                          "brake_mark": None if mark is None else {"at": mark.at, "seen_gib": mark.seen_gib}})
        return views

    def _live(self, session: Session) -> bool:
        try:
            return bool(self.sessions.live(session))
        except Exception:  # noqa: BLE001 (a session that can't be checked counts as live, as an idle unload takes it)
            return True

    def _paused(self, now: float) -> dict | None:
        """The brake's hold, read fresh: when it fired, what was available then, when it lifts by itself if memory
        stays above the warn line and nothing else holds it (None while it waits for Dan or memory is under the
        line), and the last automatic release (none while it is only assumed, after damage)."""
        hold = self._read_hold()
        if hold is None:
            return None
        fired = fired_at(hold)
        waits = holdfile.release_waits_for_dan(hold, boot_id=self.boot_id,
                                               last_auto_release_at=self.state.last_auto_release_at, now=now)
        releases_at = None
        if not waits and fired is not None and self._above_since is not None:
            since = max(self._above_since, min(fired, now))
            seen = self.admitter.hold_seen_at(hold)
            releases_at = max(since, min(seen, now) if seen is not None else now) + RELEASE_AFTER_S
        return {"since": fired, "available_gib": hold.available_gib, "releases_at": releases_at,
                "waits_for_dan": waits, "last_fired": fired,
                "last_released": None if self.state.release_assumed else self.state.last_auto_release_at}

    def _recent(self, sees: Callable[[Any], bool] | None) -> list[dict]:
        """The refusals and the gate's other events, all in one list by time, each its words (Task 29)."""
        items: list[RefusalRecord | RecentRecord] = [*self.state.refusals, *self.state.recent]
        if sees is not None:
            items = [item for item in items if sees(item)]
        rows = []
        for item in sorted(items, key=lambda each: each.at):
            if isinstance(item, RefusalRecord):
                key = self._keys.get(item.key) if item.key else None
                rows.append({"at": item.at, "text": item.message, "code": item.code, "model": item.model,
                             "key_label": key.label if key is not None else item.key})
            else:
                rows.append({"at": item.at, "text": item.text, "code": None, "model": item.model, "key_label": None})
        return rows

    def _health(self, now: float) -> dict:
        state = self.state
        brake = self._units.get("brake")
        return {"front": "ok" if self.front.connected else "not connected", "gate": "ok",
                "brake": {"status": "unknown" if brake is None else "ok" if brake.active else "down",
                          "key_checked_at": None},
                "llama_swap": "ok" if self._answering else "not answering",
                "ntfy": {"status": "ok" if state.notify_failing_since is None else "failing",
                         "failing_since": state.notify_failing_since, "dropped": self.notifier.dropped},
                "activity_age_s": None if self._activity_at is None else max(0.0, now - self._activity_at),
                "unticketed_engines": [dict(engine) for engine in self._unticketed],
                "no_ticket_refusals": len(self._backstop),
                "damaged_at": state.damaged_at, "damaged_kept_as": state.damaged_kept_as,
                "read_problems": list(self._read_problems.values())}

    def _problems(self) -> list[str]:
        problems = []
        if self._state_problem and self.state.damaged_at is not None:
            problems.append(self._state_problem)
        for name, record in self.state.models.items():
            if name not in self.registry.models:  # counted at the registry's largest footprint, which errs safe
                problems.append(f"llama-swap runs {name}, which the registry doesn't know; it is counted at "
                                f"{record.footprint_gib:g} GiB, the largest footprint the registry has.")
        if self.state.damaged_at is not None:
            problems.append("The brake's events from before the damage weren't read, so any alert of theirs that "
                            "hadn't gone wasn't sent.")
        if self._registry_problem:
            problems.append(self._registry_problem)
        return problems

    # Small things.

    def _read_hold(self) -> holdfile.Hold | None:
        """The brake's hold, read fresh on every call, never a copy older than a pass (the controller's ruling at Task
        15's review); one that can't be read still holds."""
        return holdfile.read_hold(self.brake_dir)

    def _release_hold(self, expected: holdfile.Hold | None) -> bool:
        return holdfile.release_hold(self.brake_dir, expected)

    def _emit(self, kind: str, event_key: str, **fields: Any) -> None:
        """A notification through the Notifier, never at the cost of the gate: a field its words refuse is one plain
        line naming the type and the error, and the gate carries on (the controller's ruling at Task 14's review)."""
        try:
            self.notifier.emit(kind, event_key, **fields)
        except ValueError as err:
            _log.error("the %s notification couldn't be made: %s", kind, err)
        except Exception:
            _log.exception("the %s notification couldn't be sent", kind)

    def _unread(self, source: str, problem: str) -> None:
        """A reading that failed, shown under `health` until it reads again; logged once while it stays the same."""
        if self._read_problems.get(source) != problem:
            _log.warning("%s", problem)
        self._read_problems[source] = problem

    def _read_ok(self, source: str) -> None:
        if self._read_problems.pop(source, None) is not None:
            _log.info("%s reads again", source)

    def _spawn(self, coro: Any) -> asyncio.Task:
        spawned = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(spawned)
        spawned.add_done_callback(self._ended)
        return spawned

    def _ended(self, spawned: asyncio.Task) -> None:
        self._tasks.discard(spawned)
        if not spawned.cancelled() and spawned.exception() is not None:
            _log.error("a task of the gate's broke", exc_info=spawned.exception())
