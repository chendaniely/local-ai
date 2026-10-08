"""The four units' restarts (website/design/phase-2a.md, Task 14): read from `systemctl show`; `back_up` once a unit
that crashed has run again for BACK_UP_AFTER_S, and the gate's own return after an unclean stop; `llama_swap_down`
once per outage when llama-swap hangs with its unit up."""

import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from spark import messages
from spark.gate.state import ApplyHold, GateState, load_state
from spark.gate.units import BackUpWatch, LlamaSwapWatch, UnitState, UnitStateError, unit_state
from spark.gateproto import BACK_UP_AFTER_S, LLAMA_SWAP_HUNG_S
from spark.registry import load_registry

REGISTRY = load_registry(Path(__file__).resolve().parents[2] / "stack" / "models.yaml")
T = datetime(2026, 10, 8, 9, 14, tzinfo=timezone(timedelta(hours=-7))).timestamp()


@pytest.fixture(autouse=True)
def local_zone(monkeypatch):
    """The clock read seven hours behind UTC, on a box called brightroar, as test_messages.py reads them."""
    monkeypatch.setenv("TZ", "PDT+7")
    time.tzset()
    monkeypatch.setattr(socket, "gethostname", lambda: "brightroar.local")
    yield
    monkeypatch.undo()
    time.tzset()


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Emitted:
    """A recording Emit: each (type, event key, fields)."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    def emit(self, type, event_key, /, **fields):  # noqa: A002 (Emit's name)
        self.calls.append((type, event_key, fields))

    def texts(self) -> list[str]:
        return [messages.notification(kind, REGISTRY, **fields).message for kind, _, fields in self.calls]


# `systemctl show --timestamp=us+utc`, as systemd 255 writes it on the Spark: its own order, not the one asked for.
SHOW = ("Result=success\nNRestarts=2\nActiveState=active\n"
        "ActiveEnterTimestamp=Thu 2026-10-08 16:14:12.500000 UTC\n"
        "InactiveEnterTimestamp=Thu 2026-10-08 16:14:00.250000 UTC\n")


def answering(stdout: str, calls: list | None = None, returncode: int = 0):
    def run(argv, **kwargs):
        if calls is not None:
            calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")
    return run


def test_unit_state_reads_systemctl_show():
    calls: list = []
    st = unit_state("gate", run=answering(SHOW, calls))
    assert st == UnitState(True, 2, T + 12.5, T + 0.25, "success")
    (argv, kwargs), = calls
    assert argv[:2] == ["/usr/bin/systemctl", "show"] and argv[-1] == "local-ai-gate.service"
    assert "--timestamp=us+utc" in argv and kwargs.get("timeout")
    # A timestamp systemd never set is empty: None. A unit restarting is `activating`, not active.
    never = (SHOW.replace("Thu 2026-10-08 16:14:00.250000 UTC", "")
             .replace("ActiveState=active", "ActiveState=activating"))
    assert unit_state("brake", run=answering(never)) == UnitState(False, 2, T + 12.5, None, "success")
    assert unit_state("llama-swap", run=answering(SHOW, calls := []))
    assert calls[0][0][-1] == "local-ai-llama-swap.service"
    with pytest.raises(ValueError, match="gate, front, llama-swap or brake"):
        unit_state("sshd", run=answering(SHOW))


def test_unit_state_says_when_systemctl_cant_answer():
    for run in (answering(SHOW, returncode=1), answering("NRestarts=x\nActiveState=active\n"),
                answering(SHOW.replace("16:14:12.500000 UTC", "soon"))):
        with pytest.raises(UnitStateError, match="local-ai-front.service"):
            unit_state("front", run=run)

    def hangs(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(UnitStateError, match="local-ai-front.service"):
        unit_state("front", run=hangs)


def up(n: int, active_since: float, inactive_since: float | None) -> UnitState:
    return UnitState(True, n, active_since, inactive_since, "success")


def restarting(n: int, active_since: float, inactive_since: float) -> UnitState:
    """Between the crash and the start: NRestarts has risen, its active time is still the last run's."""
    return UnitState(False, n, active_since, inactive_since, "exit-code")


def test_back_up_waits_for_60_seconds_of_running():
    clock, emitted = Clock(T - 5), Emitted()
    watch = BackUpWatch(emitted, clock)
    watch.observe("front", up(0, T - 3600, None))  # the first reading is where it starts from: no crash
    clock.now = T + 1
    watch.observe("front", restarting(1, T - 3600, T))
    watch.observe("front", up(1, T - 3600, T))  # an active time older than the crash is the last run's: not back
    for now in (T + 13, T + 30, T + 12 + BACK_UP_AFTER_S - 1):
        clock.now = now
        watch.observe("front", up(1, T + 12, T))
    assert emitted.calls == []
    clock.now = T + 73
    watch.observe("front", up(1, T + 12, T))
    assert emitted.calls == [("back_up", f"back:front:{T:.3f}", {"unit": "front", "down_s": 12})]
    assert emitted.texts() == ["The front on brightroar has been running again for a minute, after 12 s down. "
                               "Requests go through again."]
    clock.now = T + 200
    watch.observe("front", up(1, T + 12, T))
    assert len(emitted.calls) == 1  # once per return

    # A second crash before the minute is up resets it: no back_up for the first.
    clock, emitted = Clock(T - 5), Emitted()
    watch = BackUpWatch(emitted, clock)
    watch.observe("front", up(0, T - 3600, None))
    for now, st in ((T + 1, restarting(1, T - 3600, T)), (T + 13, up(1, T + 12, T)), (T + 30, up(1, T + 12, T)),
                    (T + 40.5, restarting(2, T + 12, T + 40)), (T + 42, up(2, T + 41, T + 40)),
                    (T + 73, up(2, T + 41, T + 40))):
        clock.now = now
        watch.observe("front", st)
    assert emitted.calls == []
    clock.now = T + 41 + BACK_UP_AFTER_S
    watch.observe("front", up(2, T + 41, T + 40))
    assert emitted.calls == [("back_up", f"back:front:{T + 40:.3f}", {"unit": "front", "down_s": 1})]


def test_a_restart_by_hand_is_no_crash():
    # NRestarts counts only systemd's own restarts, and a start by hand sets it back: neither is a crash.
    clock, emitted = Clock(T), Emitted()
    watch = BackUpWatch(emitted, clock)
    watch.observe("llama-swap", up(3, T - 3600, T - 3700), answering=True)
    clock.now = T + 1000
    watch.observe("llama-swap", up(0, T + 900, T + 899), answering=True)
    watch.observe("llama-swap", up(0, T + 900, T + 899), answering=True)
    assert emitted.calls == []


def test_llama_swaps_return_waits_for_it_to_answer():
    # "Models answer again" goes only once /running answers, however long its unit has been up (the controller's
    # ruling at Task 14's review); the core says whether it answered, and must.
    clock, emitted = Clock(T - 5), Emitted()
    watch = BackUpWatch(emitted, clock)
    watch.observe("llama-swap", up(0, T - 3600, None), answering=True)
    clock.now = T + 1
    watch.observe("llama-swap", restarting(1, T - 3600, T), answering=False)
    for now in (T + 13, T + 80, T + 200):
        clock.now = now
        watch.observe("llama-swap", up(1, T + 12, T), answering=False)
    assert emitted.calls == []
    clock.now = T + 201
    watch.observe("llama-swap", up(1, T + 12, T), answering=True)
    assert emitted.texts() == ["The model service on brightroar has been running again for a minute, after 12 s down. "
                               "Models answer again."]
    with pytest.raises(ValueError, match="llama-swap's return needs answering"):
        watch.observe("llama-swap", up(1, T + 12, T))


def test_the_gate_reads_its_own_downtime_from_systemd_within_a_boot():
    # The state is saved only on a change, so saved_at can be hours old after an idle night: the downtime is systemd's
    # own, from the crash to the return (the controller's ruling at Task 14's review).
    clock, emitted = Clock(T), Emitted()
    watch = BackUpWatch(emitted, clock)
    watch.gate_restarted(GateState(clean_shutdown=False, saved_at=T - 7 * 3600), T, last_alive=T - 7 * 3600)
    watch.observe("gate", restarting(1, T - 9000, T - 2))  # Type=notify: activating until it is ready
    clock.now = T + 1
    watch.observe("gate", up(1, T + 1, T - 2))
    clock.now = T + BACK_UP_AFTER_S - 1
    watch.tick()
    assert emitted.calls == []
    clock.now = T + 60
    watch.tick()
    assert emitted.texts() == ["The gate on brightroar has been running again for a minute, after 3 s down. New "
                               "loads work again."]
    assert emitted.calls[0][1] == f"back:gate:{T - 2:.3f}"
    clock.now = T + 120
    watch.observe("front", up(0, T - 3600, None))  # observe checks it too, and it went once
    assert len(emitted.calls) == 1
    # A clean stop: nothing to announce.
    quiet = Emitted()
    later = Clock(T)
    clean = BackUpWatch(quiet, later)
    clean.gate_restarted(GateState(clean_shutdown=True, saved_at=T - 12), T)
    later.now = T + 60
    clean.tick()
    assert quiet.calls == []


def test_after_a_reboot_the_gates_downtime_runs_from_its_last_activity_record():
    # The gate's unit has no inactive time this boot: its last run ended in the boot before, and the activity record's
    # written_at, every second, is when it was last alive.
    clock, emitted = Clock(T), Emitted()
    watch = BackUpWatch(emitted, clock)
    watch.gate_restarted(GateState(clean_shutdown=False, saved_at=T - 7 * 3600), T, last_alive=T - 90)
    watch.observe("gate", up(0, T + 1, None))
    clock.now = T + 60
    watch.tick()
    assert emitted.calls == [("back_up", f"back:gate:{T - 90:.3f}", {"unit": "gate", "down_s": 91})]
    assert emitted.texts() == ["The gate on brightroar has been running again for a minute, after 1 min 31 s down. "
                               "New loads work again."]


def test_a_downtime_neither_systemd_nor_the_activity_record_gives_isnt_known():
    clock, emitted = Clock(T), Emitted()
    watch = BackUpWatch(emitted, clock)
    watch.gate_restarted(GateState(clean_shutdown=False, saved_at=T - 12), T)
    watch.observe("gate", up(0, T + 1, None))
    clock.now = T + 60
    watch.tick()
    assert emitted.calls == [("back_up", f"back:gate:{T:.3f}", {"unit": "gate", "down_unknown": True})]
    assert emitted.texts() == ["The gate on brightroar has been running again for a minute; how long it was down "
                               "isn't known. New loads work again."]


def test_after_a_damaged_state_the_gates_downtime_is_still_systemds(tmp_path):
    # load_state sets a damaged file aside and saves the fail-safe state at once, its clean_shutdown false. systemd's
    # times don't depend on it; only with neither systemd's nor the activity record's does the damage word the
    # unknown (the controller's rulings, at Task 13's re-review and Task 14's review).
    (tmp_path / "state.json").write_text("{")
    state, problem = load_state(tmp_path, now=T, set_aside=True)
    assert problem and state.fresh_after_damage and not state.clean_shutdown and state.saved_at == T
    clock, emitted = Clock(T), Emitted()
    watch = BackUpWatch(emitted, clock)
    watch.gate_restarted(state, T)
    watch.observe("gate", up(1, T + 1, T - 4))
    clock.now = T + 60
    watch.tick()
    assert emitted.calls == [("back_up", f"back:gate:{T - 4:.3f}", {"unit": "gate", "down_s": 5})]

    clock, emitted = Clock(T), Emitted()
    watch = BackUpWatch(emitted, clock)
    watch.gate_restarted(state, T)
    watch.observe("gate", up(0, T + 1, None))
    clock.now = T + 60
    watch.tick()
    assert emitted.calls == [("back_up", f"back:gate:{T:.3f}", {"unit": "gate", "state_damaged": True})]
    assert emitted.texts() == ["The gate on brightroar has been running again for a minute; how long it was down "
                               "isn't known: its saved state was damaged. New loads work again."]


def test_llama_swap_down_once_per_outage_when_it_hangs():
    clock, emitted = Clock(T), Emitted()
    watch = LlamaSwapWatch(emitted, clock)
    unit = up(0, T - 3600, None)
    watch.observe(True, unit, applying=None)
    for second in range(1, LLAMA_SWAP_HUNG_S + 1):  # unanswered from T + 1
        clock.now = T + second
        watch.observe(False, unit, applying=None)
    assert emitted.calls == []
    clock.now = T + 1 + LLAMA_SWAP_HUNG_S
    watch.observe(False, unit, applying=None)
    assert emitted.calls == [("llama_swap_down", f"llama-swap:{T + 1:.3f}", {"at": T + 1, "result_words": None})]
    assert emitted.texts() == ["The model service on brightroar isn't answering (since 09:14). No model answers "
                               "until it's back; requests wait, then are refused. On the Spark, `make doctor` shows "
                               "what's wrong."]
    clock.now = T + 60
    watch.observe(False, unit, applying=None)
    assert len(emitted.calls) == 1  # still the same outage
    clock.now = T + 61
    watch.observe(True, unit, applying=None)  # it answers: re-armed
    for second in range(62, 62 + LLAMA_SWAP_HUNG_S + 1):
        clock.now = T + second
        watch.observe(False, unit, applying=None)
    assert [key for _, key, _ in emitted.calls] == [f"llama-swap:{T + 1:.3f}", f"llama-swap:{T + 62:.3f}"]

    # A crash is the failure notifier's: while its unit is down, and as it comes back with NRestarts risen, nothing
    # goes. Back up and still not answering for LLAMA_SWAP_HUNG_S is a hang of its own (the controller's ruling at
    # Task 14's review).
    crashed = Emitted()
    watch = LlamaSwapWatch(crashed, clock := Clock(T + 200))
    readings = ([(True, unit)] + [(False, unit)] * 2  # T + 200 to 202
                + [(False, restarting(1, T - 3600, T + 203))] * 2  # 203, 204: down, the notifier's
                + [(False, up(1, T + 205, T + 203))] * (LLAMA_SWAP_HUNG_S - 1)  # back at 205, 9 s unanswered
                + [(True, up(1, T + 205, T + 203))])  # then it answers
    for second, (answering, st) in enumerate(readings, start=200):
        clock.now = T + second
        watch.observe(answering, st, applying=None)
    assert crashed.calls == []
    readings = ([(False, restarting(2, T + 205, T + 260))] * 2  # T + 260, 261: crashed again
                + [(False, up(2, T + 262, T + 260))] * (LLAMA_SWAP_HUNG_S + 1))  # back at 262, never answering
    for second, (answering, st) in enumerate(readings, start=260):
        clock.now = T + second
        watch.observe(answering, st, applying=None)
    assert crashed.calls == [("llama_swap_down", f"llama-swap:{T + 262:.3f}", {"at": T + 262, "result_words": None})]
    # Restarted between two readings, never seen down: its NRestarts rose, so a hang counts from that reading.
    quick = Emitted()
    watch = LlamaSwapWatch(quick, clock := Clock(T + 400))
    readings = [(False, up(2, T + 262, T + 260))] * 5 + [(False, up(3, T + 404.5, T + 404))] * (LLAMA_SWAP_HUNG_S + 1)
    for second, (answering, st) in enumerate(readings, start=400):
        clock.now = T + second
        watch.observe(answering, st, applying=None)
        if second == 410:
            assert quick.calls == []  # 10 s since T + 400, but only 5 since its restart
    assert [key for _, key, _ in quick.calls] == [f"llama-swap:{T + 405:.3f}"]

    # Never while apply's hold stands: it expects the restart.
    holding = Emitted()
    watch = LlamaSwapWatch(holding, clock := Clock(T + 300))
    hold = ApplyHold(since=T + 290, by_uid=1000, renewed_at=T + 300, begun_at=T + 295, restarting=["llama-swap"],
                     ended=False)
    for second in range(300, 360):
        clock.now = T + second
        watch.observe(False, unit, applying=hold)
    assert holding.calls == []
    # Once it has ended, an outage counts afresh from then.
    ended = ApplyHold(since=T + 290, by_uid=1000, renewed_at=T + 300, begun_at=T + 295, restarting=["llama-swap"],
                      ended=True)
    for second in range(360, 360 + LLAMA_SWAP_HUNG_S + 1):
        clock.now = T + second
        watch.observe(False, unit, applying=ended)
    assert [key for _, key, _ in holding.calls] == [f"llama-swap:{T + 360:.3f}"]
