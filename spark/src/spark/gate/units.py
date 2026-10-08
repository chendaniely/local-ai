"""The four units' restarts (Phase 2a; plan.md, *Visibility and notifications*): the gate reads each unit from
`systemctl show` every 5 s, off the event loop (Task 18), and says when one that crashed is back.

- `back_up`, once a unit that crashed has run again for BACK_UP_AFTER_S, so a crash loop doesn't alternate it with the
  failure notifier's `*_down` alerts, and llama-swap's only once `/running` answers; the gate's own return after an
  unclean stop, which no running gate saw, its downtime from systemd within a boot, else from its last activity record.
- `llama_swap_down`, once per outage, when llama-swap hasn't answered for LLAMA_SWAP_HUNG_S while its unit is up: a
  crash is the failure notifier's to report, and apply's restart is expected, but a llama-swap back from a crash that
  still doesn't answer is a hang of its own (the controller's rulings at Task 14's review).

Read on the Spark (systemd 255, 2026-10-08, a transient user unit that crash-looped, stopped afterwards): a crash under
`Restart=` sets InactiveEnterTimestamp at the crash and ActiveEnterTimestamp once the unit is active again; NRestarts
rises at the restart, while a Type=notify unit can still be `activating` with the last run's ActiveEnterTimestamp. A
start by hand sets NRestarts back, and counts as no crash."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from spark.gate.state import ApplyHold, Emit, GateState
from spark.gateproto import BACK_UP_AFTER_S, LLAMA_SWAP_HUNG_S

# The four units the gate watches, by the names back_up's words take.
UNITS = {"gate": "local-ai-gate.service", "front": "local-ai-front.service",
         "llama-swap": "local-ai-llama-swap.service", "brake": "local-ai-brake.service"}
SYSTEMCTL = "/usr/bin/systemctl"
SHOW_TIMEOUT_S = 5.0  # a `systemctl show` that takes longer is an answer the gate doesn't wait for
_PROPERTIES = ("ActiveState", "NRestarts", "ActiveEnterTimestamp", "InactiveEnterTimestamp", "Result")


class UnitStateError(Exception):
    """`systemctl show` didn't answer, or answered something the gate can't read. Its text names the unit."""


@dataclass(frozen=True)
class UnitState:
    active: bool  # ActiveState is `active`
    n_restarts: int  # systemd's own restarts since the unit's last start by hand
    active_since: float | None  # ActiveEnterTimestamp, Unix seconds; None when it never was
    inactive_since: float | None  # InactiveEnterTimestamp, Unix seconds; None when it never was
    result: str  # its last run's end, as systemd words it: success, exit-code, signal, core-dump, watchdog …


def _stamp(unit: str, name: str, value: str) -> float | None:
    """`--timestamp=us+utc`'s form, `Thu 2026-10-08 16:14:12.500000 UTC`, as Unix seconds; empty is None."""
    if not value:
        return None
    try:
        when = datetime.strptime(value.partition(" ")[2], "%Y-%m-%d %H:%M:%S.%f UTC")  # the weekday goes first
    except ValueError:
        raise UnitStateError(f"systemctl show {unit}: {name} isn't a time it writes ({value!r})") from None
    return when.replace(tzinfo=timezone.utc).timestamp()


def unit_state(unit: str, run: Callable[..., Any] = subprocess.run) -> UnitState:
    """`unit`'s state (gate, front, llama-swap or brake), from `systemctl show`. It blocks for up to SHOW_TIMEOUT_S:
    the gate calls it off the event loop. UnitStateError when systemctl fails or answers what it can't read."""
    if unit not in UNITS:
        raise ValueError(f"{unit!r} isn't a unit the gate watches: gate, front, llama-swap or brake")
    name = UNITS[unit]
    argv = [SYSTEMCTL, "show", "--no-pager", "--timestamp=us+utc", f"--property={','.join(_PROPERTIES)}", name]
    try:
        done = run(argv, capture_output=True, text=True, timeout=SHOW_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as err:
        raise UnitStateError(f"systemctl show {name} didn't answer ({type(err).__name__})") from None
    if done.returncode != 0:
        raise UnitStateError(f"systemctl show {name} failed (exit {done.returncode})")
    props = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
    restarts = props.get("NRestarts", "")
    if not restarts.isdigit():
        raise UnitStateError(f"systemctl show {name}: NRestarts isn't a count ({restarts!r})")
    return UnitState(active=props.get("ActiveState") == "active", n_restarts=int(restarts),
                     active_since=_stamp(name, "ActiveEnterTimestamp", props.get("ActiveEnterTimestamp", "")),
                     inactive_since=_stamp(name, "InactiveEnterTimestamp", props.get("InactiveEnterTimestamp", "")),
                     result=props.get("Result", ""))


def _key(when: float) -> str:
    return f"{when:.3f}"


@dataclass(frozen=True)
class _GateReturn:
    """The gate's own return, owed BACK_UP_AFTER_S after its start."""

    due: float
    started: float
    last_alive: float | None  # the activity record's written_at: the last moment the last run was alive
    damaged: bool  # the saved state was damaged, so its clean_shutdown, false, says nothing of the last stop


class BackUpWatch:
    """`back_up` for each unit that crashed, once it has run again for BACK_UP_AFTER_S, with how long it was down
    (systemd's ActiveEnterTimestamp less its InactiveEnterTimestamp); a further crash first starts it over; llama-swap's
    only once `/running` answers. `emit` is the Notifier; `clock` gives Unix seconds, systemd's clock."""

    def __init__(self, emit: Emit, clock: Callable[[], float] = time.time):
        self._emit = emit
        self._clock = clock
        self._restarts: dict[str, int] = {}  # each unit's NRestarts, as last read
        self._last: dict[str, UnitState] = {}  # each unit's last reading
        self._crashed: set[str] = set()  # the units back from a crash that haven't run BACK_UP_AFTER_S yet
        self._gate: _GateReturn | None = None

    def observe(self, unit: str, st: UnitState, *, answering: bool | None = None) -> None:
        """A reading of `unit` (gate, front, llama-swap or brake). The first is where its count starts: no crash. For
        llama-swap, `answering` says whether `/running` answered at the gate's last read: its return (*Models answer
        again*) waits for it (the controller's ruling at Task 14's review)."""
        if unit == "llama-swap" and answering is None:
            raise ValueError("llama-swap's return needs answering: whether /running answered at the last read")
        self._last[unit] = st
        self.tick()
        before = self._restarts.get(unit)
        self._restarts[unit] = st.n_restarts
        if before is None:
            return
        if st.n_restarts > before:  # systemd restarted it: a crash, which starts the minute over
            self._crashed.add(unit)
        elif st.n_restarts < before:  # a start by hand sets the count back: no crash, and nothing owed
            self._crashed.discard(unit)
        if unit not in self._crashed:
            return
        back = (st.active and st.active_since is not None and st.inactive_since is not None
                and st.active_since >= st.inactive_since and answering is not False)
        if not back or self._clock() - st.active_since < BACK_UP_AFTER_S:
            return
        self._crashed.discard(unit)
        self._emit.emit("back_up", f"back:{unit}:{_key(st.inactive_since)}", unit=unit,
                        down_s=st.active_since - st.inactive_since)

    def gate_restarted(self, state: GateState, now: float | None = None, *, last_alive: float | None = None) -> None:
        """At the gate's start, on the state as it was loaded, before the core sets `clean_shutdown` false and clears
        `fresh_after_damage`: when the last run didn't stop cleanly, `back_up` for the gate BACK_UP_AFTER_S after `now`.
        `last_alive` is the activity record's `written_at`, the last moment the last run was alive. The downtime (the
        controller's ruling at Task 14's review; saved_at, written only on a change, can be hours old):

        - within a boot, systemd's own, from the gate's last reading through `observe`: InactiveEnterTimestamp, the
          crash, to ActiveEnterTimestamp, its return;
        - across a reboot, where its unit has no inactive time this boot, from `last_alive` to its return;
        - neither known: the words say it isn't, and after a damaged state, that the state was damaged."""
        now = self._clock() if now is None else now
        if not state.clean_shutdown:
            self._gate = _GateReturn(now + BACK_UP_AFTER_S, now, last_alive, state.fresh_after_damage)

    def tick(self) -> None:
        """Send the gate's own return once it is due. `observe` calls it; the core may too."""
        owed = self._gate
        if owed is None or self._clock() < owed.due:
            return
        self._gate = None
        st = self._last.get("gate")
        back_at = st.active_since if st is not None and st.active and st.active_since is not None else None
        fields: dict[str, Any]
        if back_at is not None and st is not None and st.inactive_since is not None and back_at >= st.inactive_since:
            down_at, fields = st.inactive_since, {"unit": "gate", "down_s": back_at - st.inactive_since}
        elif owed.last_alive is not None:
            back = back_at if back_at is not None and back_at >= owed.last_alive else owed.started
            down_at, fields = owed.last_alive, {"unit": "gate", "down_s": max(0.0, back - owed.last_alive)}
        else:
            down_at = owed.started
            fields = {"unit": "gate", "state_damaged": True} if owed.damaged else {"unit": "gate", "down_unknown": True}
        self._emit.emit("back_up", f"back:gate:{_key(down_at)}", **fields)


class LlamaSwapWatch:
    """`llama_swap_down` once per outage: llama-swap hasn't answered for LLAMA_SWAP_HUNG_S while its unit is up, and
    apply's hold doesn't stand. Re-armed once it answers again. An outage starts at the first reading it didn't answer
    with its unit up. While its unit is down, a crash, the failure notifier's alert says it; once it is up again, with
    its new NRestarts, a hang counts from then (the controller's ruling at Task 14's review). While apply's hold stands
    none is counted, and one starts afresh after the hold ends."""

    def __init__(self, emit: Emit, clock: Callable[[], float] = time.time):
        self._emit = emit
        self._clock = clock
        self._since: float | None = None  # when this outage began
        self._restarts = 0  # the unit's NRestarts when it began
        self._done = False  # this outage's alert went, or it is the failure notifier's

    def observe(self, answering: bool, st: UnitState, *, applying: ApplyHold | None) -> None:
        """One reading: whether llama-swap answered `/running`, its unit's state, and apply's hold, `state.applying`."""
        if answering or (applying is not None and not applying.ended) or not st.active:
            self._since, self._done = None, False  # answering, expected, or down: the notifier's; a hang counts afresh
            return
        now = self._clock()
        if self._since is None or st.n_restarts != self._restarts:  # restarted between two readings: from now
            self._since, self._restarts, self._done = now, st.n_restarts, False
        if self._done:
            return
        if now - self._since >= LLAMA_SWAP_HUNG_S:
            self._done = True
            self._emit.emit("llama_swap_down", f"llama-swap:{_key(self._since)}", at=self._since, result_words=None)
