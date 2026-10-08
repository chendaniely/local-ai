"""The four units' restarts (Phase 2a; plan.md, *Visibility and notifications*): the gate reads each unit from
`systemctl show` every 5 s, off the event loop (Task 18), and says when one that crashed is back.

- `back_up`, once a unit that crashed has run again for BACK_UP_AFTER_S, so a crash loop doesn't alternate it with the
  failure notifier's `*_down` alerts; the gate's own return after an unclean stop, which no running gate saw, from
  its saved state.
- `llama_swap_down`, once per outage, when llama-swap hasn't answered for LLAMA_SWAP_HUNG_S while its unit stays up
  with no new restart: a crash is the failure notifier's to report, and apply's restart is expected.

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


class BackUpWatch:
    """`back_up` for each unit that crashed, once it has run again for BACK_UP_AFTER_S, with how long it was down
    (systemd's ActiveEnterTimestamp less its InactiveEnterTimestamp); a further crash first starts it over. `emit` is
    the Notifier; `clock` gives Unix seconds, systemd's clock."""

    def __init__(self, emit: Emit, clock: Callable[[], float] = time.time):
        self._emit = emit
        self._clock = clock
        self._restarts: dict[str, int] = {}  # each unit's NRestarts, as last read
        self._crashed: set[str] = set()  # the units back from a crash that haven't run BACK_UP_AFTER_S yet
        self._gate_due: tuple[float, str, dict[str, Any]] | None = None  # the gate's own return: when, key, fields

    def observe(self, unit: str, st: UnitState) -> None:
        """A reading of `unit` (gate, front, llama-swap or brake). The first is where its count starts: no crash."""
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
                and st.active_since >= st.inactive_since)
        if not back or self._clock() - st.active_since < BACK_UP_AFTER_S:
            return
        self._crashed.discard(unit)
        self._emit.emit("back_up", f"back:{unit}:{_key(st.inactive_since)}", unit=unit,
                        down_s=st.active_since - st.inactive_since)

    def gate_restarted(self, state: GateState, now: float | None = None) -> None:
        """At the gate's start, on the state as it was loaded, before the core sets `clean_shutdown` false and clears
        `fresh_after_damage`: when the last run didn't stop cleanly, `back_up` for the gate BACK_UP_AFTER_S after
        `now`, its downtime `now − saved_at`. After a damaged state, `saved_at` is this start's own save (load_state
        saves the fail-safe state at once), so the downtime isn't known, and the words say so (the controller's
        rulings, at Task 13's re-review and Task 14)."""
        now = self._clock() if now is None else now
        if state.clean_shutdown:
            return
        if state.fresh_after_damage or state.saved_at <= 0:
            self._gate_due = (now + BACK_UP_AFTER_S, f"back:gate:{_key(now)}", {"unit": "gate", "state_damaged": True})
        else:
            self._gate_due = (now + BACK_UP_AFTER_S, f"back:gate:{_key(state.saved_at)}",
                              {"unit": "gate", "down_s": max(0.0, now - state.saved_at)})

    def tick(self) -> None:
        """Send the gate's own return once it is due. `observe` calls it; the core may too."""
        if self._gate_due is not None and self._clock() >= self._gate_due[0]:
            _, key, fields = self._gate_due
            self._gate_due = None
            self._emit.emit("back_up", key, **fields)


class LlamaSwapWatch:
    """`llama_swap_down` once per outage: llama-swap hasn't answered for LLAMA_SWAP_HUNG_S while its unit stays active
    with no new restart, and apply's hold doesn't stand. Re-armed once it answers again. An outage starts at the first
    reading it didn't answer; while apply's hold stands none is counted, and one starts afresh after the hold ends."""

    def __init__(self, emit: Emit, clock: Callable[[], float] = time.time):
        self._emit = emit
        self._clock = clock
        self._since: float | None = None  # when this outage began
        self._restarts = 0  # the unit's NRestarts when it began
        self._done = False  # this outage's alert went, or it is the failure notifier's

    def observe(self, answering: bool, st: UnitState, *, applying: ApplyHold | None) -> None:
        """One reading: whether llama-swap answered `/running`, its unit's state, and apply's hold, `state.applying`."""
        if answering or (applying is not None and not applying.ended):
            self._since, self._done = None, False
            return
        now = self._clock()
        if self._since is None:
            self._since, self._restarts, self._done = now, st.n_restarts, False
        if self._done:
            return
        if not st.active or st.n_restarts != self._restarts:  # a crash: the failure notifier's alert says it
            self._done = True
            return
        if now - self._since >= LLAMA_SWAP_HUNG_S:
            self._done = True
            self._emit.emit("llama_swap_down", f"llama-swap:{_key(self._since)}", at=self._since, result_words=None)
