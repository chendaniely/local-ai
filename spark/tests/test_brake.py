import argparse
import http.server
import os
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark import brake, cli, messages, tickets
from spark.brake import Action, plan_brake, run_brake
from spark.brakeevents import EVENTS_FILE, BrakeEvent, append_event, read_events
from spark.gate.state import GateState, save_state
from spark.gateclient import GateForbidden, GateUnavailable
from spark.hold import Hold, read_hold, release_hold, write_hold
from spark.gate.notify import ingest_brake_events
from spark.llamaswap import LlamaSwapAnswered, LlamaSwapError, LlamaSwapUnreachable, Running
from spark.memory import MemInfo
from spark.registry import BrakeThresholds, RegistryError, load_registry
from test_procs import process

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def plan(available, running):
    return plan_brake(MemInfo(121.7, available), REG.brake, REG, running)


def phase_1(hold):
    """The three fields Phase 1's brake wrote and its tests compare; Phase 2a's four (the boot, the episode, the model
    loading and the reading) are Task 23's tests'."""
    return None if hold is None else Hold(hold.since, hold.reason, hold.unloaded)


def test_above_warn_does_nothing():
    assert plan(40, ["coder"]) == []


def test_between_warn_and_brake_warns():
    assert plan(25, ["coder"]) == [Action("warn")]


def test_below_brake_holds_and_unloads_on_demand_first():
    assert plan(18, ["vision-chat", "coder", "embed"]) == [Action("hold"), Action("unload", "coder")]


def test_then_residents_largest_first():
    assert plan(18, ["embed", "stt", "vision-chat"]) == [Action("hold"), Action("unload", "vision-chat")]


def test_nothing_running_still_holds():
    assert plan(18, []) == [Action("hold")]


class FakeClient:
    def __init__(self, running=(), fail=False):
        self._running, self.fail, self.unloaded = list(running), fail, []

    def running(self):
        if self.fail:
            raise LlamaSwapError("llama-swap unreachable at http://127.0.0.1:9100")
        return self._running

    def unload(self, model):
        self.unloaded.append(model)


def test_loop_unloads_and_records_the_hold(tmp_path):
    client, logs = FakeClient([Running("coder", "ready")]), []
    run_brake(REG, client, tmp_path, read_mem=lambda: MemInfo(121.7, 18), sleep=lambda s: None,
              log=logs.append, now=lambda: "2026-09-23T10:00:00", once=True)
    assert client.unloaded == ["coder"]
    assert phase_1(read_hold(tmp_path)) == Hold("2026-09-23T10:00:00", "18.0 GiB available", ("coder",))


def test_the_hold_names_the_command_that_lifts_it(tmp_path):
    # `spark` isn't on Dan's PATH: the Makefile runs it through uv, from the clone.
    logs = []
    # In 2a the gate lifts the pause too, by rule 5 (the review's I3, the controller's ruling at Task 23), and *paused*
    # is the brake's word.
    run_brake(REG, FakeClient([Running("coder", "ready")]), tmp_path, read_mem=lambda: MemInfo(121.7, 18),
              sleep=lambda s: None, log=logs.append, now=lambda: "t", once=True)
    # It names no figure: a drill's copy of the registry raises its own lines, never the gate's (the re-review's
    # Minor 5).
    assert ("brake: new loads paused; the gate lifts the pause once memory has stayed above the gate's warn line for 5 "
            "minutes, at most once an hour, or `make brake-release` lifts it now") in logs


def test_loop_survives_llama_swap_being_down(tmp_path):
    logs = []
    run_brake(REG, FakeClient(fail=True), tmp_path, read_mem=lambda: MemInfo(121.7, 18),
              sleep=lambda s: None, log=logs.append, now=lambda: "t", once=True)
    assert any("unreachable" in line for line in logs)
    assert read_hold(tmp_path) is not None


def test_release(tmp_path, capsys):
    write_hold(tmp_path, Hold("t", "r", ()))
    assert brake.release(tmp_path) == 0
    assert read_hold(tmp_path) is None and "released" in capsys.readouterr().out


def test_the_brake_waits_for_llama_swap_2_s_at_most(monkeypatch):
    # Each tick acts on memory it has just read; a hung llama-swap must not hold it for the default 10 s.
    seen = {}
    monkeypatch.setattr(brake, "load_registry", lambda path: REG)
    monkeypatch.setattr(brake, "run_brake", lambda registry, client, state, **kw: seen.update(client=client))
    assert brake.run(argparse.Namespace(release=False, once=True, key_env="SPARK_API_KEY")) == 0
    assert seen["client"].timeout == 2


# Fix round 1: the brake keeps unloading whatever else fails, and unloads no more than the shortfall needs.

# The on-demand model smaller than a resident one, so that "on-demand first" and "largest first" disagree: in the
# fixture the on-demand coder is also the largest.
SMALL_CODER = replace(REG, models={**REG.models, "coder": replace(REG.models["coder"], footprint_gib=10.0)})
ALL = ["coder", "vision-chat", "stt", "embed"]
TICK = REG.brake.poll_ms / 1000


class Enough(Exception):
    """Ends a run of the brake after its last poll."""


class Box:
    """MemAvailable plus llama-swap as v257's source has it: an engine being unloaded reads `stopping` (one that was
    `starting` goes on reading `starting`: v257 aborts a start without showing `stopping`), then leaves /running,
    and its footprint shows in MemAvailable. `delay` is how many polls that takes (0: before the unload is
    answered, as v257 answers once the engine has exited). `stuck` engines stay `ready` after an accepted unload,
    `hung` ones stay `stopping`; `fail` unloads raise; `late` ones get no answer in 2 s and the engine stops
    anyway, as v257 carries on with an unload whose caller gave up. `errors` maps a poll to the error /running
    answers with then, `reload` a poll to a model that is loaded again then, and `set_avail` a poll to the
    memory available from then on. Each sleep is a poll: memory falls by `fall` GiB, and after `polls` of them the
    run ends. MemAvailable reads `noise` GiB high on even polls and low on odd ones."""

    def __init__(self, avail, running, *, registry=REG, hold_dir=None, delay=1, stuck=(), hung=(), fail=(),
                 late=(), fall=0.0, noise=0.0, polls=12, errors=None, reload=None, set_avail=None, on_unload=None,
                 fail_status=500):
        self.avail, self.registry, self.hold_dir, self.fail_status = avail, registry, hold_dir, fail_status
        self.states = dict(running) if isinstance(running, dict) else {m: "ready" for m in running}
        self.delay, self.fall, self.polls, self.errors, self.on_unload = delay, fall, polls, errors or {}, on_unload
        self.noise, self.reload, self.set_avail = noise, reload or {}, set_avail or {}
        self.stuck, self.hung, self.fail, self.late = set(stuck), set(hung), set(fail), set(late)
        self.exits = {}  # a stopping engine: polls until it has exited
        self.requests, self.sleeps, self.running_calls, self.held_at_unload = [], [], 0, []

    def read_mem(self):
        return MemInfo(121.7, self.avail + (self.noise if len(self.sleeps) % 2 == 0 else -self.noise))

    def running(self):
        self.running_calls += 1
        if len(self.sleeps) in self.errors:
            raise LlamaSwapError(self.errors[len(self.sleeps)])
        return [Running(m, s) for m, s in self.states.items()]

    def unload(self, model):
        self.requests.append((len(self.sleeps), model))
        if self.hold_dir is not None:
            self.held_at_unload.append(read_hold(self.hold_dir) is not None)
        if self.on_unload:
            self.on_unload(model)
        if model in self.fail:  # as llama-swap answers a refused call: with its status (Task 23)
            raise LlamaSwapAnswered(f"llama-swap POST /api/models/unload/{model}: HTTP {self.fail_status}",
                                    status=self.fail_status)
        if model not in self.stuck and self.states.get(model) in ("starting", "ready"):
            if self.states[model] == "ready":
                self.states[model] = "stopping"
            if model not in self.hung:
                self.exits[model] = self.delay
                if self.delay == 0:
                    self._exit(model)
        if model in self.late:
            raise LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: timed out")

    def _exit(self, model):
        del self.exits[model], self.states[model]
        self.avail += self.registry.models[model].footprint_gib

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.avail -= self.fall
        for model in list(self.exits):
            self.exits[model] -= 1
            if self.exits[model] <= 0:
                self._exit(model)
        if len(self.sleeps) in self.reload:
            model = self.reload[len(self.sleeps)]
            self.states[model] = "ready"
            self.avail -= self.registry.models[model].footprint_gib
        if len(self.sleeps) in self.set_avail:
            self.avail = self.set_avail[len(self.sleeps)]
        if len(self.sleeps) >= self.polls:
            raise Enough


def brake_loop(box, state, *, read_mem=None, **kw):
    logs = []
    try:
        run_brake(box.registry, box, state, read_mem=read_mem or box.read_mem, sleep=box.sleep, log=logs.append,
                  now=lambda: "T", **kw)
    except Enough:
        pass
    return logs


class Script:
    """Poll i reads avail[i] and /running lists[i]; every unload gets no answer in 2 s, as a v257 stop over 2 s."""

    registry = REG

    def __init__(self, avail, lists):
        self.avail, self.lists, self.i, self.requests = avail, lists, 0, []

    def read_mem(self):
        return MemInfo(121.7, self.avail[self.i])

    def running(self):
        return [Running(m, s) for m, s in self.lists[self.i].items()]

    def unload(self, model):
        self.requests.append((self.i, model))
        raise LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: timed out")

    def sleep(self, seconds):
        self.i += 1
        if self.i >= len(self.avail):
            raise Enough


@pytest.mark.parametrize("folder", ["missing", "read-only"])
def test_a_state_folder_it_cant_write_still_lets_the_unload_out(tmp_path, folder):
    # The unload is what saves the box: a hold that can't be written is said loudly, and never stops it (C1).
    if folder == "read-only" and os.geteuid() == 0:
        pytest.skip("root writes through a read-only mode")
    state = tmp_path / "missing" if folder == "missing" else tmp_path
    box = Box(18, ALL)
    if folder == "read-only":
        tmp_path.chmod(0o555)
    try:
        logs = brake_loop(box, state, once=True)
    finally:
        tmp_path.chmod(0o755)
    assert box.requests == [(0, "coder")]
    assert "ALERT" in logs[0] and str(state) in logs[0]  # at startup, before any write
    assert any("can't write the hold" in line for line in logs[1:])


def test_the_hold_is_on_disk_before_the_unload_goes_out(tmp_path):
    # Review Focus 2: once memory crosses the brake line, new loads are held, whatever the unload then does.
    box = Box(18, ALL, hold_dir=tmp_path)
    brake_loop(box, tmp_path, once=True)
    assert box.held_at_unload == [True]


def test_a_failed_unload_still_holds_and_says_why(tmp_path):
    logs = brake_loop(Box(18, ["coder"], fail={"coder"}), tmp_path, once=True)
    assert phase_1(read_hold(tmp_path)) == Hold("T", "18.0 GiB available", ())
    assert any("coder" in line and "HTTP 500" in line for line in logs)


def test_a_release_during_an_unload_holds_again_with_the_unload_in_it(tmp_path):
    # `spark brake --release` lands while the unload is on its way, and memory is still below the line (I1).
    brake_loop(Box(18, ALL, on_unload=lambda model: release_hold(tmp_path)), tmp_path, once=True)
    assert phase_1(read_hold(tmp_path)) == Hold("T", "18.0 GiB available", ("coder",))


@pytest.mark.parametrize("damage", ["{garbage", "[" * 100_000], ids=["not JSON", "nested too deep"])
def test_an_unload_over_a_damaged_hold_writes_a_true_one(tmp_path, damage):
    # read_hold's stand-in for a damaged file ("an unknown time", "can't be read") is never written back (M1). Nested
    # too deep, the file would raise RecursionError on every poll below the line, if read_hold let it through.
    (tmp_path / "hold.json").write_text(damage)
    brake_loop(Box(18, ALL), tmp_path, once=True)
    hold = read_hold(tmp_path)
    assert (hold.since, hold.unloaded) == ("T", ("coder",))
    assert hold.reason.startswith("18.0 GiB available; the hold file before this one was damaged")


def test_memory_on_its_way_back_counts_against_the_shortfall():
    mem = MemInfo(121.7, 18)
    assert plan_brake(mem, REG.brake, REG, ["vision-chat"], 1.5) == [Action("hold"), Action("unload", "vision-chat")]
    assert plan_brake(mem, REG.brake, REG, ["vision-chat"], 2.0) == [Action("hold")]


@pytest.mark.parametrize("delay", [0, 1, 2, 3, 4, 8])
def test_one_unload_whenever_its_memory_shows(tmp_path, delay):
    # 18 GiB with all four loaded; the coder alone gives back 28, at once or `delay` polls on (I2, F8).
    box = Box(18, ALL, delay=delay)
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder")] and box.avail == 46
    assert box.sleeps == [TICK] * 12  # a poll every 250 ms
    assert phase_1(read_hold(tmp_path)) == Hold("T", "18.0 GiB available", ("coder",))  # still held, memory back


def test_it_stops_waiting_when_memory_keeps_falling(tmp_path):
    # The coder's memory takes 20 polls, and memory falls 1.5 GiB a poll meanwhile, more than the noise the brake
    # allows for (FLOOR_TOLERANCE_GIB, 1.0): it must not wait, and the next model goes at the next poll (I2).
    box = Box(18, ALL, delay=20, fall=1.5, polls=6)
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder"), (1, "vision-chat"), (2, "stt"), (3, "embed")]


def test_a_model_already_stopping_counts_as_memory_on_its_way(tmp_path):
    # As when the brake restarts mid-unload: the coder is stopping, and its 28 GiB is on its way.
    box = Box(18, {"coder": "stopping", "vision-chat": "ready", "stt": "ready", "embed": "ready"}, polls=8)
    box.exits["coder"] = 4
    brake_loop(box, tmp_path)
    assert box.requests == [] and box.avail == 46 and read_hold(tmp_path) is not None


def test_an_unload_that_times_out_but_goes_on_is_waited_for(tmp_path):
    # v257 answers an unload once the engine has exited, and carries on when its caller gives up; the brake gives
    # up after 2 s. A slower stop gets no answer: its memory counts as on its way, and no second model goes.
    box = Box(18, ALL, late={"coder"}, delay=8)
    logs = brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder")] and box.avail == 46
    assert any("no answer in 2 s; counting coder as on its way" in line and "timed out" in line for line in logs)
    assert read_hold(tmp_path).unloaded == ("coder",)


def test_a_stopping_model_is_never_chosen(tmp_path):
    # 5 GiB plus the coder's 10 on its way falls short, so something must go: not the model already going.
    box = Box(5, {"coder": "stopping", "vision-chat": "ready"}, registry=SMALL_CODER)
    box.exits["coder"] = 99
    brake_loop(box, tmp_path, once=True)
    assert box.requests == [(0, "vision-chat")]


def test_a_model_whose_unload_fails_is_passed_over(tmp_path):
    box = Box(18, ALL, fail={"coder"})
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder"), (1, "vision-chat")]
    assert read_hold(tmp_path).unloaded == ("vision-chat",)


def test_a_model_loaded_again_can_be_unloaded_again(tmp_path):
    # Memory stays in the warn band, so the episode goes on; the coder comes back and memory falls below the line
    # again. It is a new engine, so it goes again.
    box = Box(15, ALL, registry=SMALL_CODER, polls=6, reload={3: "coder"})
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder"), (3, "coder")]
    assert read_hold(tmp_path).unloaded == ("coder",)  # each model once


@pytest.mark.parametrize("wont_go", ["stuck", "hung"])
def test_a_model_that_wont_go_is_passed_over_once_the_grace_is_over(tmp_path, wont_go):
    # Accepted but still `ready`, or `stopping` for good: one request, then the next model (I3).
    grace = int(brake.GRACE_S / TICK)
    box = Box(18, ALL, polls=grace + 4, **{wont_go: {"coder"}})
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder"), (grace, "vision-chat")]
    assert read_hold(tmp_path).unloaded == ("coder", "vision-chat")  # each model once


def test_on_demand_goes_before_a_larger_resident():
    assert plan_brake(MemInfo(121.7, 18), REG.brake, SMALL_CODER, ["vision-chat", "coder"]) == [
        Action("hold"), Action("unload", "coder")]


def test_the_key_comes_from_the_variable_key_env_names(monkeypatch):
    seen = {}
    monkeypatch.setenv("SPARK_API_KEY", "not-this-one")
    monkeypatch.setenv("SPARK_TEST_BRAKE_KEY", "this-one")
    monkeypatch.setattr(brake, "load_registry", lambda path: REG)
    monkeypatch.setattr(brake, "run_brake", lambda registry, client, state, **kw: seen.update(client=client))
    assert brake.run(argparse.Namespace(release=False, once=True, key_env="SPARK_TEST_BRAKE_KEY")) == 0
    assert seen["client"].api_key == "this-one"


def test_llama_swap_is_left_alone_while_memory_is_fine(tmp_path):
    # Each call sends the key to 127.0.0.1:9100; above the warn line there is nothing to ask (M2).
    box = Box(60, ALL, polls=8)
    assert brake_loop(box, tmp_path) == [] and box.running_calls == 0


def test_a_run_of_the_same_failure_is_logged_once(tmp_path):
    down = "llama-swap unreachable at http://127.0.0.1:9100: [Errno 111] Connection refused"
    key = "llama-swap GET /running: HTTP 401"
    box = Box(25, ALL, polls=8, errors={0: down, 1: down, 2: down, 3: down, 6: key, 7: key})
    logs = brake_loop(box, tmp_path)
    assert box.running_calls == 8
    assert logs == [f"brake: {down}", "brake: warning — 25.0 GiB available (warns below 28)",
                    "brake: llama-swap answers again", f"brake: {key}"]


def test_a_memory_read_that_fails_is_logged_once_and_tried_again(tmp_path):
    box = Box(18, ALL, polls=5)

    def read_mem():
        if len(box.sleeps) < 3:
            raise OSError("/proc/meminfo: Input/output error")
        return box.read_mem()

    logs = brake_loop(box, tmp_path, read_mem=read_mem)
    assert sum("can't read memory" in line for line in logs) == 1 and "memory reads again" in logs[1]
    assert box.requests == [(3, "coder")]


@pytest.mark.parametrize("error", [FileNotFoundError(2, "No such file or directory"),
                                   RegistryError("brake: warn_gib must be above brake_gib")])
def test_a_registry_that_wont_load_still_leaves_a_brake(tmp_path, monkeypatch, capsys, error):
    # No registry must not mean no brake: the plan's thresholds, and /running's order with no model table (M4).
    real_run_brake, seen = brake.run_brake, {}

    def wont_load(path):
        raise error

    monkeypatch.setattr(brake, "load_registry", wont_load)
    monkeypatch.setattr(brake, "run_brake", lambda registry, client, state, **kw: seen.update(registry=registry))
    assert brake.run(argparse.Namespace(release=False, once=True, key_env="SPARK_API_KEY")) == 0
    out = capsys.readouterr().out
    assert "ALERT" in out and "didn't load" in out and str(brake.paths.REGISTRY) in out
    registry = seen["registry"]
    assert registry.brake == BrakeThresholds(warn_gib=28, brake_gib=20, poll_ms=250) and registry.models == {}
    box = Box(18, ["stt", "coder"], registry=registry)
    real_run_brake(registry, box, tmp_path, read_mem=box.read_mem, sleep=box.sleep, log=lambda line: None,
                   now=lambda: "T", once=True)
    assert box.requests == [(0, "stt")]


# Fix round 2: MemAvailable noise and unanswered unloads (I2), a startup check that can't raise (N1), stale credits
# (N2), and the rules no test pinned (N3).

@pytest.mark.parametrize("answer", ["accepted", "no answer in 2 s"])
def test_noise_in_memavailable_doesnt_end_the_wait(tmp_path, answer):
    # MemAvailable moves by megabytes from poll to poll (5 MiB either way here) while the coder's slow stop, 6 s,
    # goes on: noise is not a fall, and only the coder goes (I2).
    box = Box(18, ALL, delay=24, noise=5 / 1024, polls=30, late={"coder"} if answer != "accepted" else ())
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder")] and box.avail == 46


def test_a_dip_of_exactly_the_tolerance_is_still_noise(tmp_path):
    # The credit ends only when memory falls more than FLOOR_TOLERANCE_GIB below where it was (I2).
    box = Box(18, ALL, delay=8, set_avail={1: 18 - brake.FLOOR_TOLERANCE_GIB}, polls=4)
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder")]


def test_one_gemma_checkpoint_during_a_slow_stop_doesnt_unload_a_second_model(tmp_path):
    # Phase 1's council (reliability m2): a Gemma context checkpoint, ~0.59 GiB, allocated while the coder stops, is a
    # real allocation but not a sign that the unload failed. It must not end the wait, or a resident goes too.
    box = Box(18, ALL, delay=8, set_avail={1: 18 - 0.59}, polls=4)
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder")]


def test_an_unanswered_unload_of_a_starting_model_counts_as_on_its_way(tmp_path):
    # v257 aborts a start without ever showing `stopping`: an unload with no answer in 2 s is still on its way (I2).
    box = Box(18, {"coder": "starting", "vision-chat": "ready", "stt": "ready", "embed": "ready"}, late={"coder"},
              delay=24, polls=30)
    logs = brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder")] and box.avail == 46
    assert read_hold(tmp_path).unloaded == ("coder",)
    assert any("no answer in 2 s; counting coder as on its way" in line for line in logs)


def test_a_state_folder_behind_a_parent_it_cant_search_still_lets_the_unload_out(tmp_path):
    # Python 3.12's Path.is_dir raises for EACCES on the way to the folder: the startup check must not (N1).
    if os.geteuid() == 0:
        pytest.skip("root searches any folder")
    parent = tmp_path / "parent"
    state = parent / "brake"
    state.mkdir(parents=True)
    parent.chmod(0o600)
    box = Box(18, ALL)
    try:
        logs = brake_loop(box, state, once=True)
    finally:
        parent.chmod(0o700)
    assert box.requests == [(0, "coder")]
    assert "ALERT" in logs[0] and str(state) in logs[0]


def test_a_model_back_from_stopping_is_a_new_engine(tmp_path):
    # The re-review's episode-2 case: the coder's unload gets no answer and it stops; memory is fine; it loads again.
    # When memory falls below the line again, the new coder goes too, and its 28 GiB counts while it stops (N2).
    ready = {"coder": "ready", "vision-chat": "ready", "stt": "ready", "embed": "ready"}
    stopping = {**ready, "coder": "stopping"}
    box = Script([18, 18, 17.9, 17.9, 60, 60, 60, 18, 18, 18, 18],
                 [ready, stopping, stopping, stopping, stopping, stopping, ready, ready, stopping, stopping, stopping])
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder"), (7, "coder")]


def test_a_credit_no_longer_counted_is_dropped_when_the_episode_ends(tmp_path):
    # The coder's stop hangs, and memory falls past it, so its credit stops counting and vision-chat goes. Memory
    # recovers, then falls below the line again with the coder still stopping: it counts again, so stt stays (N2).
    box = Box(18, ALL, hung={"coder"}, set_avail={1: 16.5, 3: 60, 5: 18}, polls=8)  # 1.5 below: past the tolerance
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder"), (1, "vision-chat")]


def test_a_model_whose_unload_failed_is_asked_again_in_the_next_episode(tmp_path):
    # Passed over for the rest of its episode, not for good (N3: J).
    box = Box(18, ALL, fail={"coder"}, set_avail={3: 60, 5: 18}, polls=7)
    brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder"), (1, "vision-chat"), (5, "coder"), (6, "stt")]


def test_llama_swap_is_asked_while_memory_is_on_its_way_even_above_the_warn_line(tmp_path):
    # Until the coder has left /running its credit needs /running, whatever memory reads (N3: H).
    box = Box(18, ALL, delay=4, set_avail={1: 60}, polls=8)
    brake_loop(box, tmp_path)
    assert box.running_calls == 5  # polls 0 to 4: the coder is gone at poll 4


def test_each_episode_logs_its_first_llama_swap_failure_and_its_warning(tmp_path):
    # While memory is fine the brake doesn't ask, so the next episode's first failure is news (N3: A).
    down = "llama-swap unreachable at http://127.0.0.1:9100: [Errno 111] Connection refused"
    box = Box(25, ALL, polls=6, errors={0: down, 1: down, 4: down}, set_avail={2: 60, 4: 25})
    logs = brake_loop(box, tmp_path)
    assert sum(down in line for line in logs) == 2
    assert sum("warning" in line for line in logs) == 2


def test_a_state_folder_it_cant_search_is_reported(tmp_path):
    # Writable but not searchable (mode 0600): no file can be made in it, so the check wants X_OK too (N3: B).
    if os.geteuid() == 0:
        pytest.skip("root searches any folder")
    state = tmp_path / "brake"
    state.mkdir()
    state.chmod(0o600)
    try:
        logs = brake_loop(Box(60, ALL), state, once=True)
    finally:
        state.chmod(0o700)
    assert logs and "ALERT" in logs[0] and str(state) in logs[0]


def test_a_hold_written_after_a_failure_says_so(tmp_path, monkeypatch):
    # The first write finds the disk full; the next goes through, and the log says the hold is back (N3: M). Since the
    # controller's ruling on Task 23's second re-review, the next is a --once run's last try (brake.hold_last), not the
    # unload's write in the same poll.
    real, calls = brake.write_hold, []

    def full_disk_once(state_dir, hold):
        calls.append(hold)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        real(state_dir, hold)

    monkeypatch.setattr(brake, "write_hold", full_disk_once)
    logs = brake_loop(Box(18, ALL), tmp_path, once=True)
    assert "brake: the hold is written again" in logs
    assert phase_1(read_hold(tmp_path)) == Hold("T", "18.0 GiB available", ("coder",))


# Task 5's fix round 1: a release this account can't make says who can, not a traceback.

@pytest.mark.parametrize("mode", [0o550, 0o000], ids=["read-only", "closed"])
def test_a_release_this_account_cant_make_says_who_can(tmp_path, monkeypatch, capsys, mode):
    # agent isn't in spark-admin, and the folder is 2770 spark:spark-admin; nor is a shell of Dan's started before
    # bootstrap added him to the group.
    if os.geteuid() == 0:
        pytest.skip("root writes any folder")
    state = tmp_path / "brake"
    state.mkdir()
    write_hold(state, Hold("t", "r", ()))
    monkeypatch.setattr(brake.paths, "STATE", state)
    # Never the live gate's socket: a missing one here, so the release falls back to the hold file (Task 23).
    monkeypatch.setattr(brake.paths, "GATE_CONTROL_SOCKET", tmp_path / "no-gate.sock")
    state.chmod(mode)
    try:
        code = cli.main(["brake", "--release"])
    finally:
        state.chmod(0o700)
    assert code == 1
    assert capsys.readouterr().err == (f"brake: can't release the hold: this account can't write {state}; "
                                       "spark-admin can\n")
    assert read_hold(state) == Hold("t", "r", ())  # still holds


def test_a_hold_release_cant_remove_says_why(tmp_path, capsys):
    # A folder named hold.json holds (read_hold's stand-in), and unlink can't remove a folder.
    (tmp_path / "hold.json").mkdir()
    assert brake.release(tmp_path) == 1
    err = capsys.readouterr().err
    assert err.startswith("brake: can't release the hold: ") and str(tmp_path / "hold.json") in err


# Phase 1's council (reliability I1): the brake asks llama-swap nothing above the warn line, so at start it checks once
# that llama-swap takes its key, logs the answer, and records it where `spark status` and `spark doctor` read it.

KEY_ENV = "LLAMASWAP_KEY_SPARK"


class Answers:
    """llama-swap as the start check meets it: each GET /running takes the next of `answers`, an error to raise or
    anything else to answer with, the last one again once they run out. `api_key` is the key it was made with."""

    def __init__(self, *answers, api_key="not-a-real-key"):
        self.answers, self.api_key, self.calls = list(answers), api_key, 0

    def running(self):
        self.calls += 1
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def unload(self, model):
        raise AssertionError("the start check never unloads")


class Clock:
    """A monotonic clock that moves only when told to."""

    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def start(client, state, logs, clock=None, sleep=None):
    """A start check on `clock`, whose pause between asks moves that clock unless `sleep` says otherwise."""
    clock = clock or Clock()

    def advance(seconds):
        clock.t += seconds

    return brake.StartCheck(client, state, KEY_ENV, logs.append, now=lambda: "T", clock=clock, sleep=sleep or advance)


def finish(check):
    """Waits for the check's own thread to end, when it has one."""
    thread = getattr(check, "thread", None)
    if thread is not None:
        thread.join(timeout=10)
        assert not thread.is_alive()


def run_with_check(client, state, *, polls=3, available=60.0, clock=None, sleep=None):
    """The brake, with its start check, for `polls` polls at `available` GiB; then the check, to its end."""
    logs, done = [], []
    check = start(client, state, logs, clock, sleep)

    def poll(seconds):
        done.append(seconds)
        if len(done) >= polls:
            raise Enough

    try:
        run_brake(REG, client, state, read_mem=lambda: MemInfo(121.7, available), sleep=poll, log=logs.append,
                  now=lambda: "T", start_check=check)
    except Enough:
        pass
    finish(check)
    return logs


def test_at_start_the_brake_records_that_llama_swap_takes_its_key(tmp_path):
    client = Answers([Running("coder", "ready")])
    logs = run_with_check(client, tmp_path)
    assert client.calls == 1  # once, though memory is fine, and never again
    assert logs == ["brake: llama-swap answers GET /running with the key in $LLAMASWAP_KEY_SPARK"]
    assert brake.read_key_check(tmp_path) == (
        brake.KeyCheck("T", True, "llama-swap answers GET /running with the key in $LLAMASWAP_KEY_SPARK", KEY_ENV),
        None)


@pytest.mark.parametrize("error, key, why", [
    pytest.param(LlamaSwapError("llama-swap GET /running: HTTP 401"), "not-a-real-key",
                 "llama-swap GET /running: HTTP 401", id="refused"),
    pytest.param(LlamaSwapError("llama-swap GET /running: HTTP 401"), None,
                 "llama-swap GET /running: HTTP 401 (no key in $LLAMASWAP_KEY_SPARK)", id="no-key"),
    pytest.param(LlamaSwapError("llama-swap GET /running: an answer it can't read (not v257's shape)"),
                 "not-a-real-key", "llama-swap GET /running: an answer it can't read (not v257's shape)",
                 id="unreadable"),
])
def test_a_start_check_that_fails_is_an_alert_and_is_recorded(tmp_path, error, key, why):
    client = Answers(error, api_key=key)
    logs = run_with_check(client, tmp_path)
    assert client.calls == 1  # an answer, even an error, ends it
    assert logs == [f"brake: ALERT — its start check failed: {why}. Until llama-swap answers it with its key, the "
                    "brake can hold new loads but can't unload a model"]
    assert brake.read_key_check(tmp_path) == (brake.KeyCheck("T", False, why, KEY_ENV), None)
    text = (tmp_path / brake.KEY_CHECK_FILE).read_text() + "\n".join(logs)
    assert "not-a-real-key" not in text  # the key is never written, nor logged


def test_the_start_check_waits_for_llama_swap_to_come_up(tmp_path):
    # At boot the brake starts as soon as llama-swap's binary runs, before it listens.
    down = LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: [Errno 111] Connection refused")
    client, clock = Answers(down, down, [], api_key="k"), Clock()
    seen, pauses = [], []

    def sleep(seconds):  # between asks: what the record says meanwhile
        seen.append(brake.read_key_check(tmp_path)[0].ok)
        pauses.append(seconds)
        clock.t += seconds

    logs = run_with_check(client, tmp_path, clock=clock, sleep=sleep)
    assert client.calls == 3 and seen == [None, None] and pauses == [brake.KEY_CHECK_RETRY_S] * 2
    assert brake.read_key_check(tmp_path)[0].ok is True
    assert logs == ["brake: llama-swap answers GET /running with the key in $LLAMASWAP_KEY_SPARK"]


def test_a_llama_swap_that_never_answers_fails_the_start_check_after_its_wait(tmp_path):
    down = LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: timed out")
    client, clock = Answers(down), Clock()

    def sleep(seconds):  # each ask and its pause take 10 s
        clock.t += 10

    logs = run_with_check(client, tmp_path, clock=clock, sleep=sleep)
    assert client.calls == 4  # at 0, 10, 20 and 30 s after the start
    why = f"llama-swap didn't answer in 30 s ({down})"
    assert logs == [f"brake: ALERT — its start check failed: {why}. Until llama-swap answers it with its key, the "
                    "brake can hold new loads but can't unload a model"]
    assert brake.read_key_check(tmp_path)[0] == brake.KeyCheck("T", False, why, KEY_ENV)


def test_the_last_starts_record_is_replaced_at_once(tmp_path):
    # A brake that dies before its check finishes must not leave its last start's pass standing.
    brake.write_key_check(tmp_path, brake.KeyCheck("earlier", True, "passed last time", KEY_ENV))
    start(Answers([]), tmp_path, [])
    check, problem = brake.read_key_check(tmp_path)
    assert problem is None and (check.at, check.ok) == ("T", None) and "waiting for llama-swap" in check.detail


def test_braking_never_waits_for_the_start_check(tmp_path):
    # The re-review of Phase 1's council fixes (N4): a llama-swap that takes the connection and never answers held each
    # ask for the client's 2 s timeout, and each poll with it, for 30 s. The check runs off the poll path, so the polls
    # go on at their own pace while it waits.
    answered, polls = threading.Event(), []

    class Hung(Answers):
        def running(self):
            self.calls += 1
            answered.wait(timeout=2)  # as long as the brake's client waits for an answer
            self.polls_meanwhile = len(polls)
            return []

    def poll(seconds):
        polls.append(seconds)
        if len(polls) >= 5:
            answered.set()
            raise Enough

    client = Hung()
    check = start(client, tmp_path, [])
    try:
        run_brake(REG, FakeClient(), tmp_path, read_mem=lambda: MemInfo(121.7, 60), sleep=poll,
                  log=lambda line: None, now=lambda: "T", start_check=check)
    except Enough:
        pass
    finish(check)
    assert client.calls == 1 and client.polls_meanwhile == 5  # every poll ran while the ask hung


def test_a_start_check_it_cant_record_is_said_and_the_brake_runs_on(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root writes through a read-only mode")
    tmp_path.chmod(0o555)
    try:
        logs = run_with_check(Answers([]), tmp_path)
    finally:
        tmp_path.chmod(0o755)
    assert sum(f"can't record its start check in {tmp_path}" in line for line in logs) == 2  # the wait, then the pass
    assert "brake: llama-swap answers GET /running with the key in $LLAMASWAP_KEY_SPARK" in logs


@pytest.mark.parametrize("once", [False, True])
def test_only_the_brake_that_keeps_running_checks_its_key(tmp_path, monkeypatch, once):
    # A --once run, as a drill's with Dan's key, would replace the unit's record with its own.
    seen = {}
    monkeypatch.setattr(brake, "load_registry", lambda path: REG)
    monkeypatch.setattr(brake.paths, "STATE", tmp_path)
    monkeypatch.setattr(brake, "run_brake", lambda registry, client, state, **kw: seen.update(kw, client=client))
    assert brake.run(argparse.Namespace(release=False, once=once, key_env=KEY_ENV)) == 0
    check = seen["start_check"]
    # The check asks from a thread of its own, with a client of its own, sharing nothing with the polls.
    assert (check is None) if once else (check.key_env == KEY_ENV and check.client.timeout == 2
                                         and check.client is not seen["client"])
    assert (tmp_path / brake.KEY_CHECK_FILE).exists() is not once


@pytest.mark.parametrize("damage", ["{garbage", '{"at": "T", "ok": "yes", "detail": "d", "key_env": "K"}',
                                    '{"at": "T", "ok": 1, "detail": "d", "key_env": "K"}', "[1]"])
def test_a_start_check_record_that_cant_be_read_says_why(tmp_path, damage):
    (tmp_path / brake.KEY_CHECK_FILE).write_text(damage)
    check, problem = brake.read_key_check(tmp_path)
    record = tmp_path / brake.KEY_CHECK_FILE
    assert check is None and problem.startswith(f"the brake's start check {record} can't be read")


def test_a_missing_state_folder_is_said_and_never_made_by_the_start_check(tmp_path):
    # The folder is bootstrap's, 2770 spark:spark-admin: one the brake made would have neither that group nor that mode.
    state = tmp_path / "missing"
    logs = run_with_check(Answers([]), state)
    assert not state.exists()
    assert sum(f"can't record its start check in {state}" in line for line in logs) == 2


# Task 23: the brake for 2a. The gate's activity record orders the unloads; the rate-of-fall watch acts early on a fall
# the gate didn't admit; each step is recorded for the gate; while the gate is down, the brake sends its own alert; its
# key comes from its credential, and `make brake-release` goes through the gate. Every time, boot id, pid, address,
# topic, token and key here is a stand-in.

BOOT = "boot-1"
T0 = 1_800_000_000.0  # the wall clock at the first poll
U0 = 5000.0  # /proc/uptime at the first poll
CLK_TCK = os.sysconf("SC_CLK_TCK")
CODER, GEMMA, EMBED, WHISPER = "coder", "vision-chat", "embed", "stt"
# The stack's sizes, the coder at 2a's ~41 GiB: Phase 1's order (on-demand first, then by size) reads the coder, Gemma,
# the embeddings, whisper, as the plan's test has it.
REG2A = replace(REG, models={**REG.models,
                             CODER: replace(REG.models[CODER], footprint_gib=41.0),
                             GEMMA: replace(REG.models[GEMMA], footprint_gib=32.0),
                             EMBED: replace(REG.models[EMBED], footprint_gib=8.0),
                             WHISPER: replace(REG.models[WHISPER], footprint_gib=3.0)})
LABELS = {name: model.label for name, model in REG2A.models.items()}
ENGINE_PID = 4242
FIRED_AT = datetime.fromtimestamp(T0).astimezone().isoformat(timespec="seconds")  # the hold's `since` at T0
# -q first, so curl reads no .curlrc: the brake runs as spark, the engines' account, which could plant one (the review's
# M10).
CURL_ARGS = ["/usr/bin/curl", "-q", "--fail", "--silent", "--max-time", "10", "--config", "-"]
NTFY = {"NTFY_URL": "http://ntfy.stand-in.invalid:8080", "NTFY_TOPIC_BRAKE": "stand-in-topic-b7"}
TOKEN_LINE = "Authorization: Bearer stand-in-token-z9"


def fall(start: float, end: float, per_s: float, *, then: int = 8) -> list[float]:
    """MemAvailable at each 250 ms poll, falling `per_s` GiB a second from `start` while the next reading stays at or
    above `end`, then holding there for `then` polls. Each is worked out from `start`, so no rounding builds up."""
    readings = [start]
    while start - per_s * TICK * len(readings) >= end:
        readings.append(start - per_s * TICK * len(readings))
    return readings + [readings[-1]] * then


def ready(inflight: int = 0, last_use: float = T0 - 60) -> dict:
    return {"state": "ready", "inflight": inflight, "last_use": last_use}


def starting(admitted: float = 41.0, started_at: float = T0, available_at_start: float | None = 74.0) -> dict:
    return {"state": "starting", "inflight": 0, "last_use": started_at, "admitted_gib": admitted,
            "started_at": started_at, "available_at_start": available_at_start}


def write_activity(folder: Path, written_at: float, models: dict, *, boot: str = BOOT) -> None:
    """The gate's activity record, as its core writes it (tickets.write_whole)."""
    tickets.write_whole(folder / brake.ACTIVITY_FILE, {"written_at": written_at, "boot_id": boot, "models": models})


def started_record(folder: Path, model: str = CODER, *, footprint: float = 41.0, available: float = 74.0,
                   started_at: float = T0, pid: int = ENGINE_PID, boot: str = BOOT,
                   start_time: int | None = None) -> None:
    """launch's started/ record of a claimed ticket, as tickets.mark_started writes it: with launch's own start time
    (`start_time`, its pid's ticks since boot, which the engine keeps across the exec), or without it, as a record
    written before the controller's ruling at Task 23."""
    ticket = {"model": model, "id": "stand-in-ticket", "issued_at": started_at - 1, "deadline": started_at + 200,
              "nonce": "stand-in-nonce", "boot_id": boot, "footprint_gib": footprint, "available_gib": available}
    extra = {} if start_time is None else {"start_time": start_time}
    tickets.write_whole(tickets.record_path(folder, tickets.STARTED, model),
                        ticket | {"started_at": started_at, "pid": pid} | extra)


def ticks(forked_at: float) -> int:
    """The start time /proc/<pid>/stat gives a process llama-swap forked at the wall time `forked_at`."""
    return round((U0 - (T0 - forked_at)) * CLK_TCK)


def an_engine(proc: Path, model: str = CODER, *, pid: int = ENGINE_PID, forked_at: float = T0 - 0.5,
              port: int | None = None, comm: str = "llama-server", uid: int | None = None) -> None:
    """The model's engine in a /proc stand-in: spark's (this test's uid), on its port, forked by llama-swap at the wall
    time `forked_at`, which /proc/<pid>/stat's start time holds in clock ticks since boot."""
    port = brake.engine_ports(REG2A)[model] if port is None else port
    process(proc, pid, comm, uid=os.geteuid() if uid is None else uid,
            argv=(comm, "--host", "127.0.0.1", "--port", str(port)), start=ticks(forked_at))


@pytest.fixture
def box_folders(tmp_path):
    """The brake's folder, the gate's, launch's, and a /proc stand-in whose uptime is U0."""
    folders = SimpleNamespace(state=tmp_path / "brake", gate=tmp_path / "gate", launch=tmp_path / "launch",
                              proc=tmp_path / "proc")
    for folder in vars(folders).values():
        folder.mkdir()
    (folders.proc / "uptime").write_text(f"{U0:.2f} 1000.00\n")
    return folders


class Timeline(Box):
    """Box on 2a's registry, with the brake's two clocks, each poll 250 ms on both: MemAvailable is `readings[i]` at
    poll i (the last one held), plus what its unloads gave back, less any `fall`; /proc/uptime moves with the clocks.
    While `gate_up`, the gate's activity record is written at each poll, as the gate writes it every second, with
    `models` in it; otherwise it stays as it was."""

    def __init__(self, readings, running, *, folders, models=None, gate_up=True, registry=REG2A, **kw):
        kw.setdefault("polls", len(readings))
        super().__init__(readings[0], running, registry=registry, **kw)
        self.readings, self.folders, self.models, self.gate_up = list(readings), folders, models or {}, gate_up
        self.start_avail = self.avail
        self.tick()

    def now_s(self) -> float:
        return len(self.sleeps) * TICK

    def wall(self) -> float:
        return T0 + self.now_s()

    def boot_s(self) -> float:
        """CLOCK_BOOTTIME, as /proc/uptime gives it."""
        return U0 + self.now_s()

    def read_mem(self):
        moved = self.avail - self.start_avail
        return MemInfo(121.7, self.readings[min(len(self.sleeps), len(self.readings) - 1)] + moved)

    def tick(self):
        (self.folders.proc / "uptime").write_text(f"{U0 + self.now_s():.2f} 1000.00\n")
        if self.gate_up:
            write_activity(self.folders.gate, self.wall(), self.models)

    def sleep(self, seconds):
        try:
            super().sleep(seconds)
        finally:
            self.tick()


def run_2a(box: Timeline, *, once: bool = False, alerter=None, now=lambda: FIRED_AT) -> list[str]:
    """The brake as its unit runs it, on the box's folders and clocks; its journal lines."""
    logs, f = [], box.folders
    try:
        run_brake(box.registry, box, f.state, read_mem=box.read_mem, sleep=box.sleep, log=logs.append, now=now,
                  once=once, monotonic=box.now_s, wall=box.wall, boottime=box.boot_s, boot_id=BOOT, gate_state=f.gate,
                  launch=f.launch, proc=f.proc, spark_uid=os.geteuid(), alerter=alerter)
    except Enough:
        pass
    return logs


def events(folders) -> list[BrakeEvent]:
    return read_events(folders.state / EVENTS_FILE, None)


class Curl:
    """subprocess.Popen as the brake starts curl: each start recorded, with its arguments and what was written to its
    stdin. `finishes_after` seconds after it starts it has exited with `code`; None: never."""

    def __init__(self, finishes_after: float | None = None, code: int = 0):
        self.finishes_after, self.code, self.started, self.polls = finishes_after, code, [], 0

    def __call__(self, args, **kwargs):
        child = _Child(self, args, kwargs)
        self.started.append(child)
        return child


class _Pipe:
    def __init__(self):
        self.data, self.closed = b"", False

    def write(self, data: bytes) -> int:
        assert not self.closed
        self.data += data
        return len(data)

    def close(self) -> None:
        self.closed = True


class _Child:
    def __init__(self, curl: Curl, args, kwargs):
        self.curl, self.args, self.kwargs, self.at = curl, args, kwargs, time.monotonic()
        self.stdin, self.returncode = _Pipe(), None

    def poll(self):
        self.curl.polls += 1
        after = self.curl.finishes_after
        if after is not None and time.monotonic() - self.at >= after:
            self.returncode = self.curl.code
        return self.returncode

    def wait(self, timeout=None):
        after = self.curl.finishes_after
        if after is None:  # one that never finishes: the wait runs out (a second at most, here)
            time.sleep(min(timeout or 1, 1))
            raise subprocess.TimeoutExpired(self.args, timeout)
        time.sleep(max(0.0, self.at + after - time.monotonic()))
        self.returncode = self.curl.code
        return self.returncode


def ntfy_env(tmp_path: Path, *, token: str = TOKEN_LINE) -> dict:
    """The brake's environment with ntfy set up: the values file's two, and its credential folder holding the token's
    header line (Task 25's placeholder, `not-set-up`, when `token` says so)."""
    creds = tmp_path / "credentials"
    creds.mkdir(exist_ok=True)
    (creds / "ntfy-token").write_text(token + "\n")
    return {**NTFY, "CREDENTIALS_DIRECTORY": str(creds)}


def alerter(curl: Curl, env: dict):
    return brake.Alerter(env=env, popen=curl, curl_ok=lambda: True)


def curl_config(data: bytes) -> dict[str, list[str]]:
    """curl's --config text, each `name = "value"` line read back as curl reads it: a backslash takes the next
    character as itself (\\n, \\t, \\r and \\v as their controls), and an unescaped quote can't come before the last."""
    found: dict[str, list[str]] = {}
    for line in data.decode().splitlines():
        name, sep, quoted = line.partition(" = ")
        assert sep and len(quoted) >= 2 and quoted[0] == quoted[-1] == '"', line
        chars, value, i = quoted[1:-1], [], 0
        while i < len(chars):
            if chars[i] == "\\":
                i += 1
                value.append({"n": "\n", "t": "\t", "r": "\r", "v": "\v"}.get(chars[i], chars[i]))
            else:
                assert chars[i] != '"', line
                value.append(chars[i])
            i += 1
        found.setdefault(name, []).append("".join(value))
    return found


def first_alert(**fields) -> str:
    """Task 7's brake_fired, as the brake words its own: at T0, from 18 GiB, under the 20 GiB line, the gate down."""
    given = {"at": T0, "available_gib": 18, "line_gib": 20, "by_brake": True, "release_waits_for_dan": False,
             "release_after_s": 300} | fields
    return messages.notification("brake_fired", REG2A, **given).message


def test_a_loading_engine_goes_first_then_idle_models_of_any_class_then_lru(tmp_path):
    write_activity(tmp_path, T0, {GEMMA: ready(0, T0 - 10), EMBED: ready(0, T0 - 100), CODER: starting(),
                                  WHISPER: ready(2, T0 - 1)})
    record = brake.read_activity(tmp_path / brake.ACTIVITY_FILE, T0 + 1, boot_id=BOOT)
    running = [(GEMMA, "ready"), (EMBED, "ready"), (CODER, "starting"), (WHISPER, "ready")]
    assert brake.brake_order(running, record, REG2A) == [CODER, EMBED, GEMMA, WHISPER]


@pytest.mark.parametrize("record", ["stale", "missing", "damaged", "another boot's"])
def test_a_stale_or_missing_activity_record_falls_back_to_phase_1s_order(tmp_path, record):
    models = {GEMMA: ready(0, T0 - 10), EMBED: ready(0, T0 - 100), CODER: starting(), WHISPER: ready(2, T0 - 1)}
    path = tmp_path / brake.ACTIVITY_FILE
    if record == "stale":
        write_activity(tmp_path, T0 - 4, models)
        assert brake.read_activity(path, T0 - 1, boot_id=BOOT) is not None  # 3 s old is still fresh
    elif record == "damaged":
        path.write_text('{"written_at": ')
    elif record == "another boot's":
        write_activity(tmp_path, T0, models, boot="boot-0")
    found = brake.read_activity(path, T0, boot_id=BOOT)
    assert found is None
    running = [(WHISPER, "ready"), (EMBED, "ready"), (GEMMA, "ready"), (CODER, "starting")]
    assert brake.brake_order(running, found, REG2A) == [CODER, GEMMA, EMBED, WHISPER]


def test_the_brake_reads_the_gates_record_and_ports_where_the_gate_writes_them():
    from spark.gate import core  # the gate's own: its record's name, and the ports llama-swap v257 hands out
    assert brake.ACTIVITY_FILE == core.ACTIVITY_FILE
    assert brake.ENGINE_START_PORT == core.ENGINE_START_PORT
    assert brake.engine_ports(REG2A) == core.engine_ports(REG2A)


def test_an_admitted_coder_load_never_trips_the_rate_watch(box_folders):
    box = Timeline(fall(74, 40, 2.5), {GEMMA: "ready", CODER: "starting"}, folders=box_folders,
                   models={GEMMA: ready(), CODER: starting(admitted=41, available_at_start=74)})
    run_2a(box)
    assert box.requests == [] and read_hold(box_folders.state) is None
    assert box.running_calls == 0  # above the warn line, with nothing to act on, llama-swap isn't asked


def test_an_unexplained_fall_at_the_same_rate_trips_it(box_folders):
    readings = fall(74, 40, 2.5)
    box = Timeline(readings, {GEMMA: "ready", EMBED: "ready"}, folders=box_folders,
                   models={GEMMA: ready(0, T0 - 100), EMBED: ready(0, T0 - 10)})
    run_2a(box)
    first = next(i for i, gib in enumerate(readings) if gib < 45)  # 45 − 25 = 20
    assert readings[first] == 44.625
    assert box.requests == [(first, GEMMA)]  # once, the idle model unused longest, well above 28
    hold = read_hold(box_folders.state)
    assert (hold.available_gib, hold.unloaded) == (44.625, (GEMMA,))
    assert hold.reason == "44.6 GiB available, falling fast toward the 20 GiB line"
    assert [(e.kind, e.falling) for e in events(box_folders)] == [("fired", True), ("unload", True)]


def test_a_load_that_runs_past_its_admission_trips_it(box_folders):
    readings = fall(74, 25, 2.5)
    box = Timeline(readings, {GEMMA: "ready", CODER: "starting"}, folders=box_folders,
                   models={GEMMA: ready(), CODER: starting(admitted=41, available_at_start=74)})
    run_2a(box)
    first = next(i for i, gib in enumerate(readings) if gib < 31)  # its floor: 74 − 41 − 2
    assert readings[first] == 30.875
    assert box.requests == [(first, CODER)]  # the loading engine first
    assert read_hold(box_folders.state).loading == CODER


def test_a_started_record_is_bounded_by_what_the_brake_reads(box_folders):
    f = box_folders
    now = T0 + 5
    (f.proc / "uptime").write_text(f"{U0 + 5:.2f} 1000.00\n")
    an_engine(f.proc)
    own = deque([(T0 - 1, 61.0), (T0, 60.0), (T0 + 1, 58.0)])  # the brake's own readings: 60 at the record's start

    def loads(activity=None):
        return brake.loads_in_progress(activity, f.launch, now=now, boot_id=BOOT, registry=REG2A, readings=own,
                                       proc=f.proc, spark_uid=os.geteuid())

    started_record(f.launch, available=74)  # it claims 74 available at its start
    assert loads() == [brake.LoadInProgress(CODER, 41.0, 60.0, T0)]
    assert brake.expected_floor(loads()) == 60 - 41 - brake.LOAD_FLOOR_MARGIN_GIB
    started_record(f.launch, footprint=90)  # more than the coder's footprint
    assert [load.admitted_gib for load in loads()] == [41.0]
    # The gate's record fresh, without the coder starting: the record still counts.
    gate = brake.Activity(now, BOOT, {GEMMA: brake.ModelActivity("ready", 0, T0 - 60)})
    assert [load.model for load in loads(gate)] == [CODER]


@pytest.mark.parametrize("case", ["its pid gone", "another process at its pid", "the engine of another port",
                                  "another account's engine", "an engine forked long before"])
def test_a_started_record_that_isnt_its_models_engine_is_no_load_in_progress(box_folders, case):
    f = box_folders
    (f.proc / "uptime").write_text(f"{U0 + 5:.2f} 1000.00\n")
    started_record(f.launch)
    if case == "another process at its pid":
        process(f.proc, ENGINE_PID, "python3", uid=os.geteuid(),
                argv=("python3", "--port", str(brake.engine_ports(REG2A)[CODER])))
    elif case == "the engine of another port":
        an_engine(f.proc, port=brake.engine_ports(REG2A)[GEMMA])
    elif case == "another account's engine":
        an_engine(f.proc, uid=os.geteuid() + 1)
    elif case == "an engine forked long before":  # an engine that has run an hour: no record of a load just begun
        an_engine(f.proc, forked_at=T0 - 3600)
    assert brake.loads_in_progress(None, f.launch, now=T0 + 5, boot_id=BOOT, registry=REG2A,
                                   readings=deque([(T0, 74.0)]), proc=f.proc, spark_uid=os.geteuid()) == []


@pytest.mark.parametrize("written", ["with launch's start time", "from before it"])
def test_with_the_gate_down_the_claimed_ticket_gives_the_load(box_folders, written):
    f = box_folders
    write_activity(f.gate, T0 - 4, {})  # stale: the gate is down
    started_record(f.launch, start_time=ticks(T0 - 0.5) if written.startswith("with") else None)
    an_engine(f.proc)
    box = Timeline(fall(74, 40, 2.5), {GEMMA: "ready", CODER: "starting"}, folders=f, gate_up=False)
    run_2a(box)
    assert box.requests == [] and read_hold(f.state) is None


@pytest.mark.parametrize("record", ["361 s old", "another boot's"])
def test_a_stale_started_record_gives_no_floor(box_folders, record):
    f = box_folders
    write_activity(f.gate, T0 - 4, {})
    if record == "361 s old":
        started_record(f.launch, started_at=T0 - 361)
        an_engine(f.proc, forked_at=T0 - 361.5)
    else:
        started_record(f.launch, boot="boot-0")
        an_engine(f.proc)
    readings = fall(74, 40, 2.5)
    box = Timeline(readings, {GEMMA: "ready", CODER: "starting"}, folders=f, gate_up=False)
    run_2a(box)
    first = next(i for i, gib in enumerate(readings) if gib < 45)
    assert box.requests == [(first, CODER)]  # as with nothing loading; Phase 1's order, the gate's record stale


def test_a_started_folder_it_cant_read_is_no_load_in_progress(box_folders):
    if os.geteuid() == 0:
        pytest.skip("root reads any folder")
    f = box_folders
    write_activity(f.gate, T0 - 4, {})
    started_record(f.launch)
    an_engine(f.proc)
    (f.launch / "started").chmod(0o000)
    readings = fall(74, 40, 2.5)
    box = Timeline(readings, {GEMMA: "ready", CODER: "starting"}, folders=f, gate_up=False)
    try:
        logs = run_2a(box)
    finally:
        (f.launch / "started").chmod(0o750)
    first = next(i for i, gib in enumerate(readings) if gib < 45)
    assert box.requests == [(first, CODER)]  # no record read is no load in progress, the stricter way
    assert sum("started records" in line and "can't be read" in line for line in logs) == 1


def test_a_load_marked_starting_or_started_counts_once(box_folders):
    f = box_folders
    (f.proc / "uptime").write_text(f"{U0 + 5:.2f} 1000.00\n")
    started_record(f.launch)
    an_engine(f.proc)
    gate = brake.Activity(T0 + 5, BOOT, {CODER: brake.ModelActivity("starting", 0, T0, 41.0, T0, 74.0)})
    loads = brake.loads_in_progress(gate, f.launch, now=T0 + 5, boot_id=BOOT, registry=REG2A,
                                    readings=deque([(T0, 74.0)]), proc=f.proc, spark_uid=os.geteuid())
    assert loads == [brake.LoadInProgress(CODER, 41.0, 74.0, T0)]
    assert brake.expected_floor(loads) == 74 - 41 - 2
    # Two loads: from the earliest start's reading, less both footprints; none, no floor.
    both = loads + [brake.LoadInProgress(GEMMA, 32.0, 60.0, T0 + 10)]
    assert brake.expected_floor(both) == 74 - 41 - 32 - 2 and brake.expected_floor([]) is None


def test_two_once_runs_with_a_release_between_are_two_episodes(box_folders):
    f = box_folders
    # Dan's raised drill copy: its line above what the unloads give back, so each run finds memory under it. Each engine
    # has gone by the time v257 answers its unload (delay 0), as between two starts of the drill's unit.
    drill = replace(REG2A, brake=BrakeThresholds(warn_gib=110, brake_gib=100, poll_ms=250))
    box = Timeline([52], {GEMMA: "ready", EMBED: "ready", WHISPER: "ready"}, folders=f, gate_up=False, registry=drill,
                   delay=0)
    run_2a(box, once=True)
    release_hold(f.state)
    run_2a(box, once=True)
    run_2a(box, once=True)  # while the second hold stands
    assert [(e.kind, e.episode, e.model, e.line_gib) for e in events(f)] == [
        ("fired", 1, None, 100), ("unload", 1, GEMMA, 100),
        ("fired", 2, None, 100), ("unload", 2, EMBED, 100),
        ("unload", 2, WHISPER, 100)]
    assert read_hold(f.state).episode == 2


def test_the_floor_uses_the_admitted_footprint(box_folders):
    # The coder's cold load measured at 33 (Task 45), admitted at its footprint, 41; 3 GiB of other memory moves during
    # its load, so memory falls from 74 to 37: past a floor from the cold load (39), never past the admitted one (31).
    registry = replace(REG2A, models={**REG2A.models, CODER: replace(REG2A.models[CODER], cold_load_gib=33.0)})
    box = Timeline(fall(74, 37, 2.5), {GEMMA: "ready", CODER: "starting"}, folders=box_folders, registry=registry,
                   models={GEMMA: ready(), CODER: starting(admitted=41, available_at_start=74)})
    run_2a(box)
    assert box.requests == [] and read_hold(box_folders.state) is None


def test_a_slow_fall_doesnt_trip_the_rate_watch(box_folders):
    # At 0.2 GiB/s the window holds a fall of 0.4 GiB, under MIN_FALL_GIB (the review's I2, the controller's ruling at
    # Task 23), so the watch never acts: the brake acts at the line, as before. (It acted just under 22 until then,
    # which the controller's first ruling had accepted.)
    readings = fall(40.02, 18, 0.2)
    box = Timeline(readings, {GEMMA: "ready", EMBED: "ready"}, folders=box_folders,
                   models={GEMMA: ready(0, T0 - 100), EMBED: ready()})
    run_2a(box)
    first, model = box.requests[0]
    assert readings[first] < 20 <= readings[first - 1] and model == GEMMA
    assert read_hold(box_folders.state).available_gib == readings[first]


@pytest.mark.parametrize("period", [2, 8, 16], ids=["every poll", "a 2 s square wave", "a 4 s square wave"])
def test_noise_doesnt_trip_the_rate_watch(box_folders, period):
    readings = [30 + (0.5 if (i // (period // 2)) % 2 == 0 else -0.5) for i in range(240)]  # a minute, 4 a second
    box = Timeline(readings, {GEMMA: "ready"}, folders=box_folders, models={GEMMA: ready()})
    run_2a(box)
    assert box.requests == [] and read_hold(box_folders.state) is None and box.running_calls == 0


def test_predict_breach_reads_the_fall_over_its_window():
    def line(start: float, per_s: float, polls: int = 9) -> deque:
        return deque((i * TICK, start + per_s * i * TICK) for i in range(polls))

    def breach(readings, floor=None, window_s=brake.FALL_WINDOW_S):
        return brake.predict_breach(readings, brake_gib=20, lead_s=brake.UNLOAD_LEAD_S, window_s=window_s,
                                    floor_gib=floor)

    assert breach(line(46, -2.5)) and not breach(line(51, -2.5))  # ends at 41 (16 ahead) and 46 (21 ahead)
    assert not breach(line(30, 0)) and not breach(line(21, 1))  # flat, and rising
    assert not breach(line(46, -2.5), floor=31) and breach(line(46, -2.5), floor=19)  # its floor, above or below 20
    assert breach(line(30, -2.5), floor=31)  # past its floor: an unexplained fall
    assert not breach(line(46, -2.5, polls=1)) and not breach(line(46, -2.5, polls=3))  # too short a span to judge
    # Only the window counts: a fall long over, then a flat line, is no fall.
    over = deque(list(line(60, -5, polls=8)) + [(2.0 + i * TICK, 25.0) for i in range(1, 10)])
    assert not breach(over)
    # The watch acts only on a fall of MIN_FALL_GIB within its window, the highest reading less the latest (the review's
    # I2): 1.4 GiB over 2 s, near the line, projects under it, and isn't one; 1.5 is.
    assert brake.MIN_FALL_GIB == 1.5
    assert not breach(line(21.4, -0.7)) and breach(line(21.5, -0.75))
    assert brake.predict_breach(line(21.4, -0.7), brake_gib=20, lead_s=brake.UNLOAD_LEAD_S,
                                window_s=brake.FALL_WINDOW_S, floor_gib=None, min_fall_gib=0)


def test_the_hold_records_the_boot_the_episode_and_the_loading_model(box_folders):
    box = Timeline([18], {GEMMA: "ready", CODER: "starting"}, folders=box_folders,
                   models={GEMMA: ready(), CODER: starting()})
    run_2a(box, once=True)
    hold = read_hold(box_folders.state)
    assert (hold.boot_id, hold.episode, hold.loading, hold.available_gib) == (BOOT, 1, CODER, 18)
    assert (hold.since, hold.reason, hold.unloaded) == (FIRED_AT, "18.0 GiB available", (CODER,))


def test_the_holds_since_carries_its_utc_offset(box_folders):
    # The controller's ruling at Task 17's review: so the gate reads when it fired unambiguously across a clock going
    # back. Phase 1's reader takes it as text.
    box = Timeline([18], {GEMMA: "ready"}, folders=box_folders, gate_up=False)
    run_2a(box, once=True, now=brake._now)
    since = datetime.fromisoformat(read_hold(box_folders.state).since)
    assert since.utcoffset() is not None and abs(since.timestamp() - time.time()) < 60


def test_each_step_is_recorded_for_the_gate_with_a_rising_seq(box_folders):
    f = box_folders
    for n in range(2):  # an earlier boot's
        append_event(f.state / EVENTS_FILE, BrakeEvent(0, T0 - 3600 + n, "boot-0", 1, "unload", GEMMA, None, 19.0, 20,
                                                        False))
    n = 3
    box = Timeline([18], {CODER: "ready", GEMMA: "ready", EMBED: "ready"}, folders=f, gate_up=False, delay=99,
                   fall=1.5, polls=2)
    run_2a(box)
    mine = [e for e in events(f) if e.boot_id == BOOT]
    assert [(e.seq, e.kind, e.model) for e in mine] == [(n, "fired", None), (n + 1, "unload", CODER),
                                                        (n + 2, "unload", GEMMA)]
    assert [(e.episode, e.line_gib, e.available_gib) for e in mine] == [(1, 20, 18), (1, 20, 18), (1, 20, 16.5)]
    again = Timeline([18], {EMBED: "ready"}, folders=f, gate_up=False)  # a brake started again, on the same file
    run_2a(again, once=True)
    assert [(e.seq, e.episode, e.model) for e in events(f)][-1] == (n + 3, 1, EMBED)


def test_each_unload_names_its_models_state_as_the_gate_saw_it(box_folders):
    f = box_folders
    up = Timeline([18], {CODER: "starting", GEMMA: "ready", EMBED: "ready"}, folders=f, delay=99, fall=1.5, polls=3,
                  models={CODER: starting(), GEMMA: ready(0), EMBED: ready(2)})
    run_2a(up)
    assert [(e.model, e.state) for e in events(f) if e.kind == "unload"] == [
        (CODER, "starting"), (GEMMA, "idle"), (EMBED, "answering")]
    # With the gate down there are no counts: only a model llama-swap shows starting has a state.
    release_hold(f.state)
    write_activity(f.gate, T0 - 4, {})
    down = Timeline([18], {CODER: "starting", GEMMA: "ready"}, folders=f, gate_up=False, delay=99, fall=1.5, polls=2)
    run_2a(down)
    assert [(e.model, e.state) for e in events(f) if e.kind == "unload"][3:] == [(CODER, "starting"), (GEMMA, None)]


def test_the_started_record_of_a_load_it_stopped_goes(box_folders):
    f = box_folders
    started_record(f.launch)
    an_engine(f.proc)
    box = Timeline([18], {CODER: "starting"}, folders=f, gate_up=False)
    run_2a(box, once=True)
    assert box.requests == [(0, CODER)] and not tickets.record_path(f.launch, tickets.STARTED, CODER).exists()


def test_a_started_record_it_cant_clear_stops_nothing(box_folders):
    if os.geteuid() == 0:
        pytest.skip("root writes through a read-only mode")
    f = box_folders
    started_record(f.launch)
    (f.launch / "started").chmod(0o550)
    box = Timeline([18], {CODER: "starting", GEMMA: "ready"}, folders=f, gate_up=False, delay=99, fall=1.5, polls=2)
    try:
        logs = run_2a(box)
    finally:
        (f.launch / "started").chmod(0o750)
    assert box.requests == [(0, CODER), (1, GEMMA)]
    assert sum("can't clear" in line for line in logs) == 1


def test_a_failed_unload_leaves_an_event_with_its_reason(box_folders, tmp_path):
    # The controller's ruling at Task 14: when an unload fails or llama-swap refuses it, the brake writes an event with
    # the reason, so the gate can word the cause.
    curl = Curl()
    box = Timeline([18], {GEMMA: "ready", EMBED: "ready"}, folders=box_folders, gate_up=False, fail={GEMMA}, polls=2,
                   delay=99)
    run_2a(box, alerter=alerter(curl, ntfy_env(tmp_path)))
    assert [(e.kind, e.model, e.reason) for e in events(box_folders)] == [
        ("fired", None, None), ("unload_failed", GEMMA, "HTTP 500"), ("unload", EMBED, None)]
    # Its own alert, the gate being down, says llama-swap couldn't do it (a 500 is no refusal, and is on pi's list: no
    # number); the next unload follows up.
    first, then = (curl_config(child.stdin.data)["data-raw"][0] for child in curl.started)
    assert first == first_alert(unloaded=[], unload_unanswered=True, not_unloaded=[(LABELS[GEMMA], "HTTP 500")])
    assert "It hasn't unloaded anything yet: llama-swap couldn't do its unload of the vision model." in first
    assert then == messages.notification("brake_fired", REG2A, at=T0 + TICK, unloaded=[(LABELS[EMBED], None)],
                                         follow_up=True).message


def test_with_the_gate_down_the_brake_sends_brake_fired_itself_without_waiting(box_folders, tmp_path):
    f = box_folders
    write_activity(f.gate, T0 - 4, {GEMMA: ready()})  # stale
    curl = Curl()  # never finishes
    box = Timeline([18], {GEMMA: "ready"}, folders=f, gate_up=False, polls=2, delay=99)
    begun = time.monotonic()
    run_2a(box, alerter=alerter(curl, ntfy_env(tmp_path)))
    assert time.monotonic() - begun < 1  # nothing waited on curl
    (child,) = curl.started
    assert child.args == CURL_ARGS
    assert child.kwargs == {"stdin": subprocess.PIPE, "start_new_session": True, "stdout": subprocess.DEVNULL,
                            "stderr": subprocess.DEVNULL, "env": {}}  # nothing of the brake's environment (M10)
    words = first_alert(unloaded=[(LABELS[GEMMA], None)])
    assert words.endswith("They resume once the gate is back and memory has stayed above 28 GiB available for 5 min.")
    assert curl_config(child.stdin.data) == {
        "url": [f"{NTFY['NTFY_URL']}/{NTFY['NTFY_TOPIC_BRAKE']}"],
        "header": [f"@{tmp_path / 'credentials' / 'ntfy-token'}", "Priority: high"],
        "data-raw": [words]}
    assert child.stdin.closed
    argv = "\0".join(child.args)
    for private in (NTFY["NTFY_URL"], NTFY["NTFY_TOPIC_BRAKE"], "stand-in-token-z9", "ntfy-token", words[:30]):
        assert private not in argv
    assert [(e.kind, e.sent_by_brake) for e in events(f)] == [("fired", True), ("unload", True)]


def test_with_the_gate_up_the_brake_sends_nothing_itself(box_folders, tmp_path):
    curl = Curl()
    box = Timeline([18], {GEMMA: "ready"}, folders=box_folders, models={GEMMA: ready()})
    run_2a(box, once=True, alerter=alerter(curl, ntfy_env(tmp_path)))
    assert curl.started == []
    assert [(e.kind, e.state, e.sent_by_brake) for e in events(box_folders)] == [
        ("fired", None, False), ("unload", "idle", False)]


@pytest.mark.parametrize("code, why", [(0, None), (22, "ntfy answered with an error"),
                                       (28, "ntfy didn't answer within 10 s"), (6, "it couldn't reach ntfy"),
                                       (7, "it couldn't reach ntfy"), (35, "curl exited 35")])
def test_finished_alerts_are_reaped(box_folders, tmp_path, code, why):
    # A failed one is said in words, with what follows from it: the gate won't send it either (the review's M6).
    curl = Curl(finishes_after=0, code=code)
    alert = alerter(curl, ntfy_env(tmp_path))
    box = Timeline([18], {GEMMA: "ready"}, folders=box_folders, gate_up=False, polls=2, delay=99)
    logs = run_2a(box, alerter=alert)
    assert len(curl.started) == 1 and curl.polls >= 1 and alert.children == []
    failed = [line for line in logs if "didn't go through" in line]
    assert failed == ([] if why is None else [
        f"brake: its own alert didn't go through: {why}; the gate won't send it either, since the brake's event "
        "records it as sent by the brake"])


def test_a_quote_in_the_text_is_escaped_for_curls_config(tmp_path):
    text = 'Brake on "brightroar": a back\\slash, and a "quote"'
    data = brake.curl_config("http://ntfy.stand-in.invalid/topic", tmp_path / "ntfy-token", "high", text)
    (line,) = [each for each in data.decode().splitlines() if each.startswith("data-raw")]
    assert line == 'data-raw = "Brake on \\"brightroar\\": a back\\\\slash, and a \\"quote\\""'
    assert curl_config(data)["data-raw"] == [text]


def test_once_waits_for_its_alert_before_exiting(box_folders, tmp_path):
    curl = Curl(finishes_after=0.2)
    box = Timeline([18], {GEMMA: "ready"}, folders=box_folders, gate_up=False)
    begun = time.monotonic()
    run_2a(box, once=True, alerter=alerter(curl, ntfy_env(tmp_path)))
    (child,) = curl.started
    assert time.monotonic() - begun >= 0.2 and child.returncode == 0


def test_with_nothing_loaded_the_brakes_own_alert_says_so(box_folders, tmp_path):
    # The controller's ruling at Task 7's review, worded at Task 13: brake_fired takes unloaded=[].
    curl = Curl(finishes_after=0)
    run_2a(Timeline([18], {}, folders=box_folders, gate_up=False), once=True, alerter=alerter(curl, ntfy_env(tmp_path)))
    (child,) = curl.started
    words = curl_config(child.stdin.data)["data-raw"][0]
    assert words == first_alert(unloaded=[]) and "Nothing of the stack's was loaded" in words


@pytest.mark.parametrize("saved", ["released within the hour", "damaged"])
def test_the_brakes_own_alert_says_when_the_pause_waits_for_dan(box_folders, tmp_path, saved):
    f = box_folders
    if saved == "damaged":
        (f.gate / "state.json").write_text("{not json")
    else:
        save_state(f.gate, GateState(last_auto_release_at=T0 - 600), now=T0 - 600)
    curl = Curl(finishes_after=0)
    run_2a(Timeline([18], {GEMMA: "ready"}, folders=f, gate_up=False), once=True,
           alerter=alerter(curl, ntfy_env(tmp_path)))
    words = curl_config(curl.started[0].stdin.data)["data-raw"][0]
    if saved == "damaged":
        assert words.endswith("New loads stay paused until you release them: the gate's saved state was damaged, so it "
                              "can't tell when the last automatic release was. On the Spark, `make brake-release` "
                              "resumes them.")
    else:
        assert words.endswith(f"It fired within an hour of the automatic release at "
                              f"{datetime.fromtimestamp(T0 - 600):%H:%M}, so they stay paused until you release them: "
                              "on the Spark, `make brake-release`.")


def test_the_brake_never_waits_on_the_gate_a_socket_or_ntfy(box_folders, tmp_path):
    curl, stamps = Curl(), []  # a curl that never finishes; the gate's record missing
    box = Timeline([18], {CODER: "ready", GEMMA: "ready", EMBED: "ready", WHISPER: "ready"}, folders=box_folders,
                   gate_up=False, delay=99, fall=1.5, polls=6)
    poll = box.sleep

    def sleep(seconds):
        stamps.append(time.monotonic())
        poll(seconds)

    box.sleep = sleep
    # The disk's fsyncs aren't what this measures (the review's M8): the brake's own work, never waiting on curl.
    real_fsync = os.fsync
    os.fsync = lambda fd: None
    try:
        begun = time.monotonic()
        run_2a(box, alerter=alerter(curl, ntfy_env(tmp_path)))
    finally:
        os.fsync = real_fsync
    ticks = [later - earlier for earlier, later in zip([begun, *stamps], stamps)]
    assert len(ticks) == 6 and max(ticks) < 0.05
    sent = [curl_config(child.stdin.data)["data-raw"][0] for child in curl.started]
    assert len(sent) == 4 and sent[0].startswith("Brake on") and all(s.startswith("Brake, ") for s in sent[1:])


def test_the_brake_reads_its_key_from_its_credential(tmp_path, monkeypatch):
    creds = tmp_path / "credentials"
    creds.mkdir()
    (creds / "llamaswap-key").write_text("stand-in-brake-key-q4\n")
    stand_in = (creds / "llamaswap-key").read_text().strip()  # compared by name, so no literal key sits in an assert
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(creds))
    monkeypatch.setenv("SPARK_API_KEY", "not-this-one")
    monkeypatch.setattr(brake.paths, "STATE", tmp_path)
    monkeypatch.setattr(brake, "load_registry", lambda path: REG)
    seen = {}
    monkeypatch.setattr(brake, "run_brake", lambda registry, client, state, **kw: seen.update(kw, client=client))
    args = cli.build_parser().parse_args(["brake", "--key-credential", "llamaswap-key"])
    assert brake.run(args) == 0
    assert seen["client"].api_key == stand_in == seen["start_check"].client.api_key
    check, _ = brake.read_key_check(tmp_path)
    assert check.key_env == "credential llamaswap-key"
    assert stand_in not in (tmp_path / brake.KEY_CHECK_FILE).read_text()
    # Its answer names the credential, never the key.
    answered = brake.StartCheck(Answers([]), tmp_path, "credential llamaswap-key", lambda line: None, now=lambda: "T")
    answered.ask()
    assert brake.read_key_check(tmp_path)[0].detail == ("llama-swap answers GET /running with the key in credential "
                                                        "llamaswap-key")


def test_a_key_credential_that_cant_be_read_still_leaves_a_brake(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(brake.paths, "STATE", tmp_path)
    monkeypatch.setattr(brake, "load_registry", lambda path: REG)
    seen = {}
    monkeypatch.setattr(brake, "run_brake", lambda registry, client, state, **kw: seen.update(kw, client=client))
    assert brake.run(argparse.Namespace(release=False, once=True, key_env="SPARK_API_KEY",
                                        key_credential="llamaswap-key")) == 0
    out = capsys.readouterr().out
    assert seen["client"].api_key is None and "ALERT" in out and "llamaswap-key" in out


class Gate:
    """GateClient as `make brake-release` meets it: `answer` is what POST /v1/release answers, or the error it
    raises."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, path, timeout_s):
        self.calls.append((path, timeout_s))
        return self

    def post(self, route, body):
        self.calls.append((route, body))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_release_goes_through_the_gate_and_falls_back_when_it_doesnt_answer(tmp_path, capsys):
    sock = tmp_path / "gate-control.sock"
    write_hold(tmp_path, Hold("t", "r", ()))
    gate = Gate({"room": None, "brake": True, "reloading": ["Gemma", "the embeddings"]})
    assert brake.release_through_gate(sock, tmp_path, client=gate) == 0
    assert capsys.readouterr().out == "New loads resume. Reloading Gemma, then the embeddings…\n"
    assert gate.calls == [(sock, 5), ("/v1/release", {"brake": True})]
    assert read_hold(tmp_path) is not None  # the gate's to lift, which this stand-in doesn't
    forbidden = "Stand-in words: only spark-admin's members can use that socket."
    assert brake.release_through_gate(sock, tmp_path, client=Gate(GateForbidden(forbidden))) == 1
    out = capsys.readouterr()
    assert (out.out, out.err) == ("", forbidden + "\n") and read_hold(tmp_path) is not None
    assert brake.release_through_gate(sock, tmp_path, client=Gate(GateUnavailable("stand-in: no socket"))) == 0
    assert capsys.readouterr().out == messages.BRAKE_RELEASED_WITHOUT_GATE + "\n"
    assert read_hold(tmp_path) is None


def test_a_gate_that_answers_is_never_bypassed(tmp_path, capsys):
    # An answer the client can't take is a plain GateError: the gate is up, so the hold file stays its to lift (Task
    # 10's re-review). A gate that lifted nothing says why, and only a hold that still stands is a failure.
    sock = tmp_path / "gate-control.sock"
    write_hold(tmp_path, Hold("t", "r", ()))
    assert brake.release_through_gate(sock, tmp_path, client=Gate(brake.GateError("stand-in: too large"))) == 1
    assert capsys.readouterr().err == "stand-in: too large\n" and read_hold(tmp_path) is not None
    lifted_nothing = {"room": None, "brake": False, "reloading": []}
    assert brake.release_through_gate(sock, tmp_path, client=Gate(lifted_nothing)) == 1
    assert "still stands" in capsys.readouterr().err
    release_hold(tmp_path)
    assert brake.release_through_gate(sock, tmp_path, client=Gate(lifted_nothing)) == 0
    assert capsys.readouterr().out == "brake: no hold to release\n"


def test_make_brake_release_falls_back_when_no_gate_listens(tmp_path, monkeypatch, capsys):
    # The CLI's own path, on a socket that isn't there: never the live gate's.
    write_hold(tmp_path, Hold("t", "r", ()))
    monkeypatch.setattr(brake.paths, "STATE", tmp_path)
    monkeypatch.setattr(brake.paths, "GATE_CONTROL_SOCKET", tmp_path / "no-gate.sock")
    assert cli.main(["brake", "--release"]) == 0
    assert capsys.readouterr().out == messages.BRAKE_RELEASED_WITHOUT_GATE + "\n" and read_hold(tmp_path) is None


@pytest.mark.parametrize("missing", ["NTFY_URL", "NTFY_TOPIC_BRAKE", "the credential ntfy-token"])
def test_without_ntfy_set_up_the_brake_sends_nothing_and_says_so(box_folders, tmp_path, monkeypatch, missing):
    f = box_folders
    env = ntfy_env(tmp_path, token="not-set-up" if missing.startswith("the credential") else TOKEN_LINE)
    env.pop(missing, None)
    curl = Curl()
    box = Timeline([18], {CODER: "ready", GEMMA: "ready", EMBED: "ready"}, folders=f, gate_up=False, delay=99, fall=1.5,
                   polls=2)
    logs = run_2a(box, alerter=alerter(curl, env))
    assert curl.started == [] and len(box.requests) == 2  # two unloads in the episode
    said = [line for line in logs if "ntfy isn't set up" in line]
    assert len(said) == 1 and missing in said[0] and "(Task 2, Phase 5)" in said[0]
    assert "stand-in" not in said[0]  # what is missing, by its name, never a value
    assert {e.sent_by_brake for e in events(f)} == {False}
    # A --once run, as the drill's unit runs it, exits 0 all the same, and starts no curl.
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(brake.paths, "STATE", f.state)
    monkeypatch.setattr(brake.paths, "GATE_STATE", f.gate)
    monkeypatch.setattr(brake.paths, "LAUNCH", f.launch)
    monkeypatch.setattr(brake, "load_registry", lambda path: REG2A)
    monkeypatch.setattr(brake, "read_meminfo", lambda: MemInfo(121.7, 18))
    monkeypatch.setattr(brake, "LlamaSwap", lambda url, key, timeout: Box(18, {WHISPER: "ready"}, registry=REG2A))
    monkeypatch.setattr(brake.subprocess, "Popen", curl)
    release_hold(f.state)
    assert brake.run(argparse.Namespace(release=False, once=True, key_env="SPARK_API_KEY", key_credential=None)) == 0
    assert curl.started == [] and read_hold(f.state).unloaded == (WHISPER,)


def test_a_failed_unload_while_the_gate_is_down_follows_up_with_why(box_folders, tmp_path):
    # The controller's ruling, at Task 23: the follow-up says why llama-swap didn't unload.
    curl = Curl()
    box = Timeline([18], {GEMMA: "ready", EMBED: "ready"}, folders=box_folders, gate_up=False, fail={EMBED},
                   fail_status=401, delay=99, fall=1.5, polls=2)
    run_2a(box, alerter=alerter(curl, ntfy_env(tmp_path)))
    first, then = (curl_config(child.stdin.data)["data-raw"][0] for child in curl.started)
    assert first == first_alert(unloaded=[(LABELS[GEMMA], None)])
    assert then == messages.notification("brake_fired", REG2A, at=T0 + TICK, unloaded=[],
                                         not_unloaded=[(LABELS[EMBED], "HTTP 401")], follow_up=True).message
    assert then.endswith("llama-swap refused its unload of the embeddings (HTTP 401: the brake's key). On the Spark, "
                         "`make logs s=brake` shows why.")
    assert [(e.kind, e.reason, e.sent_by_brake) for e in events(box_folders)] == [
        ("fired", None, True), ("unload", None, True), ("unload_failed", "HTTP 401", True)]


def test_a_model_marked_starting_without_the_gates_record_was_seen_using_nothing(box_folders):
    # Task 14's Q2 ruling, checked at Task 23: the brake names a model llama-swap shows starting `starting` even with
    # the gate's record missing, so the gate marks it; with no reading of where its load began, what it was seen using
    # is None, never a guess.
    box = Timeline([18], {CODER: "starting"}, folders=box_folders, gate_up=False)
    run_2a(box, once=True)
    gate = GateState()
    ingest_brake_events(box_folders.state / EVENTS_FILE, gate, REG2A, boot_id=BOOT, now=T0 + 6, starts={})
    mark = gate.brake_marks[CODER]
    assert (mark.model, mark.at, mark.seen_gib) == (CODER, T0, None)


def test_a_started_record_with_its_start_time_is_matched_exactly(box_folders):
    # The controller's ruling, at Task 23: launch writes its own start time, so the brake matches the engine's exactly,
    # and judges the load's age by the boot's clock, never the wall clock: a clock stepped since the record (here the
    # record says T0, the engine forked 100 s before by /proc) no longer rejects it.
    f = box_folders
    (f.proc / "uptime").write_text(f"{U0 + 5:.2f} 1000.00\n")
    an_engine(f.proc, forked_at=T0 - 100)

    def loads():
        return brake.loads_in_progress(None, f.launch, now=T0 + 5, boot_id=BOOT, registry=REG2A,
                                       readings=deque([(T0, 74.0)]), proc=f.proc, spark_uid=os.geteuid())

    started_record(f.launch, start_time=ticks(T0 - 100))
    assert loads() == [brake.LoadInProgress(CODER, 41.0, 74.0, T0)]
    started_record(f.launch, start_time=ticks(T0 - 100) + 1)  # a tick off: another process's, or a pid reused
    assert loads() == []
    started_record(f.launch)  # the same record without the field, from before the ruling: its age disagrees by 100 s
    assert loads() == []


def test_a_started_record_naming_a_long_running_engine_is_no_load_even_with_its_start_time(box_folders):
    # An engine runs as spark and can write started/, copying its own start time: it has still run an hour by the
    # boot's clock, longer than any load in progress.
    f = box_folders
    (f.proc / "uptime").write_text(f"{U0 + 5:.2f} 1000.00\n")
    an_engine(f.proc, forked_at=T0 - 3600)
    started_record(f.launch, start_time=ticks(T0 - 3600))
    assert brake.loads_in_progress(None, f.launch, now=T0 + 5, boot_id=BOOT, registry=REG2A,
                                   readings=deque([(T0, 74.0)]), proc=f.proc, spark_uid=os.geteuid()) == []



# Task 23's review, the controller's rulings: an admitted load's floor stands for the window after it ends (C1); no lock
# another process holds stops the hold or the unload (I1); the watch acts only on a fall of MIN_FALL_GIB within its
# window (I2); the journal says how the pause ends in 2a (I3); a started record's start reading by the boot's clock
# (M4); the release's words when the gate may be up (M7); and curl as the brake runs it (M9, M10).

class ReadyAt(Timeline):
    """Timeline whose loading `model` becomes ready `ready_poll` polls in, as the gate makes it on its load call's
    success: its record says ready, /running shows it ready, and launch's started/ record is cleared."""

    def __init__(self, *args, model, ready_poll, **kw):
        self.model, self.ready_poll = model, ready_poll
        super().__init__(*args, **kw)

    def tick(self):
        if len(self.sleeps) >= self.ready_poll and self.states.get(self.model) == "starting":
            self.models = {**self.models, self.model: ready(0, self.wall())}
            self.states[self.model] = "ready"
            tickets.clear_started(self.folders.launch, self.model)
        super().tick()


@pytest.mark.parametrize("model, start, admitted", [(CODER, 74, 41), (EMBED, 32.5, 8), (WHISPER, 27.5, 3)],
                         ids=["the coder, 74 to 33", "the embeddings, 32.5 to 24.5", "whisper, 27.5 to 24.5"])
@pytest.mark.parametrize("after_polls", [0, 2, 4, 6, 8], ids=["at its end", "0.5 s after", "1 s after", "1.5 s after",
                                                             "2 s after"])
def test_an_admitted_load_never_trips_the_watch_as_it_ends(box_folders, model, start, admitted, after_polls):
    # The review's C1: the gate marks the load ready, and clears its started/ record, as its fall ends or a moment
    # after, while the 2 s window still holds the fall. Its floor stands for FALL_WINDOW_S and a poll after it leaves
    # the loads in progress. The embeddings' and whisper's loads end near the 24 GiB reserve.
    f = box_folders
    readings = fall(start, start - admitted, 2.5, then=16)
    end = next(i for i in range(1, len(readings)) if readings[i] == readings[i - 1])
    started_record(f.launch, model, footprint=admitted, available=start, start_time=ticks(T0 - 0.5))
    an_engine(f.proc, model)
    box = ReadyAt(readings, {GEMMA: "ready", model: "starting"}, folders=f, model=model, ready_poll=end + after_polls,
                  models={GEMMA: ready(), model: starting(admitted=admitted, available_at_start=start)})
    run_2a(box)
    assert box.states[model] == "ready" and not tickets.record_path(f.launch, tickets.STARTED, model).exists()
    assert box.requests == [] and read_hold(f.state) is None


def hold_the_events_lock(path: Path) -> subprocess.Popen:
    """A second process that takes the events file's lock through a read-only fd, as any reader can, and holds it until
    it is killed."""
    path.touch()
    holder = subprocess.Popen(
        [sys.executable, "-c", "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDONLY); "
         "fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); time.sleep(30)", str(path)],
        stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    return holder


def release_the_lock(holder: subprocess.Popen) -> None:
    holder.kill()
    holder.wait()


def test_a_lock_another_process_holds_never_stops_the_hold_or_the_unload(box_folders):
    # The review's I1: flock needs no write access, so any process that can read the brake's events can hold their
    # lock. The hold is written first, its episode from an unlocked read, and the unload goes out before any step is
    # written (the re-review's Minor 8); a try for the lock waits LOCK_WAIT_S at most, then the brake goes on, and says
    # so once. A --once run makes one last bounded try, then says what went unrecorded (the controller's ruling on fix
    # round 1's concern 2). The disk's fsyncs are stubbed: this measures the lock's waits (the re-review's Minor 7).
    holder = hold_the_events_lock(box_folders.state / EVENTS_FILE)
    sent, real_fsync = [], os.fsync
    os.fsync = lambda fd: None
    try:
        box = Timeline([18], {GEMMA: "ready", EMBED: "ready"}, folders=box_folders, gate_up=False,
                       on_unload=lambda model: sent.append(time.monotonic()))
        begun = time.monotonic()
        logs = run_2a(box, once=True)
        took = time.monotonic() - begun
    finally:
        os.fsync = real_fsync
        release_the_lock(holder)
    hold = read_hold(box_folders.state)
    assert hold is not None and (hold.episode, hold.unloaded) == (1, (GEMMA,)) and box.requests == [(0, GEMMA)]
    assert sent[0] - begun < brake.LOCK_WAIT_S / 2, sent[0] - begun  # no lock waited for before the unload
    # One try at the poll's end and one last try: each LOCK_WAIT_S at most, never one per step (measured 0.41 s).
    assert 2 * brake.LOCK_WAIT_S <= took < 3 * brake.LOCK_WAIT_S, took
    assert sum("another process holds" in line for line in logs) == 1
    assert [line for line in logs if "went unrecorded" in line] == [
        "brake: ALERT — 2 of its steps for the gate went unrecorded (fired, unload vision-chat): another process held "
        f"the lock on {box_folders.state / EVENTS_FILE}"]
    assert events(box_folders) == []


def test_steps_the_lock_held_up_are_written_in_order_on_the_next_poll_that_gets_it(box_folders):
    # The controller's ruling on fix round 1's concern 2: no step is lost for the gate. Held across one poll, the lock
    # then released, the next poll appends the fired event and the unload's, in order, their seq from the file then.
    path = box_folders.state / EVENTS_FILE
    append_event(path, BrakeEvent(0, T0 - 3600, "boot-0", 1, "unload", GEMMA, None, 19.0, 20, False))  # earlier boot
    holder = hold_the_events_lock(path)
    box = Timeline([18], {GEMMA: "ready", EMBED: "ready"}, folders=box_folders, gate_up=False, delay=99, polls=2)
    poll = box.sleep

    def sleep(seconds):
        if len(box.sleeps) == 0:
            assert len(events(box_folders)) == 1  # nothing of this boot's written while the lock was held
            release_the_lock(holder)
        poll(seconds)

    box.sleep = sleep
    try:
        logs = run_2a(box)
    finally:
        release_the_lock(holder)
    mine = [e for e in events(box_folders) if e.boot_id == BOOT]
    assert [(e.seq, e.kind, e.model, e.episode) for e in mine] == [(2, "fired", None, 1), (3, "unload", GEMMA, 1)]
    assert mine[0].at == T0 and mine[1].at == T0  # written a poll later, as they were at poll 0
    assert "brake: its steps are recorded for the gate again" in logs


def test_steps_waiting_for_the_lock_are_bounded_and_the_oldest_dropped(box_folders):
    # At most PENDING_MAX steps wait: past it the oldest go, the journal says so once, and how many, once the rest are
    # written. 66 models the registry doesn't know (so no memory comes back), one unloaded a poll while the lock is
    # held: 67 steps, so 3 dropped.
    assert brake.PENDING_MAX == 64
    path = box_folders.state / EVENTS_FILE
    holder = hold_the_events_lock(path)
    names = [f"stand-in-{n}" for n in range(1, 67)]
    box = Timeline([18], dict.fromkeys(names, "ready"), folders=box_folders, gate_up=False, delay=999, polls=68)
    poll = box.sleep

    def sleep(seconds):
        if len(box.sleeps) == 66:  # every model asked: free the lock for the next poll
            release_the_lock(holder)
        poll(seconds)

    box.sleep = sleep
    real_fsync, os.fsync = os.fsync, lambda fd: None  # this measures the lock's waits, not the disk (re-review 2)
    begun = time.monotonic()
    try:
        logs = run_2a(box)
    finally:
        os.fsync = real_fsync
        release_the_lock(holder)
    # Once a try has found the lock held, the polls after it don't wait: one wait, not 66 (the re-review's Minor 2).
    assert time.monotonic() - begun < 10 * brake.LOCK_WAIT_S
    assert len(box.requests) == 66
    written = events(box_folders)
    assert [e.seq for e in written] == list(range(1, 65))
    assert [e.model for e in written] == names[2:]  # the newest 64: fired and the first two unloads were dropped
    assert sum("more than 64 of its steps" in line for line in logs) == 1
    assert ("brake: ALERT — 3 of its steps for the gate were dropped, the oldest first, while it couldn't record them; "
            "the newest 64 are recorded") in logs


@pytest.mark.parametrize("level", [24, 25])
@pytest.mark.parametrize("shape", ["±0.3, a 2 s square wave", "±0.5, a 2 s square wave", "±0.5, a 4 s square wave",
                                   "±0.5 every poll", "a 1 GiB step over 2 s"])
def test_near_the_reserve_noise_and_small_steps_dont_trip_the_watch(box_folders, level, shape):
    # The review's I2: memory sits near the 24 GiB reserve after an admitted load, where noise of ±0.5 GiB, or a 1 GiB
    # step, projected 10 s ahead, would pass the line. The watch acts only on a fall of MIN_FALL_GIB within its window.
    if shape.startswith("a 1 GiB"):
        readings = [level] * 12 + [level - min(1.0, (k + 1) * TICK / 2.0) for k in range(40)]
    else:
        amp = 0.3 if shape.startswith("±0.3") else 0.5
        half = 1 if "every poll" in shape else 4 if "2 s" in shape else 8
        readings = [level + (amp if (i // half) % 2 == 0 else -amp) for i in range(240)]
    box = Timeline(readings, {GEMMA: "ready"}, folders=box_folders, models={GEMMA: ready()})
    run_2a(box)
    assert box.requests == [] and read_hold(box_folders.state) is None


def test_an_unexplained_fast_fall_near_the_reserve_still_trips_before_the_line(box_folders):
    readings = fall(30, 15, 2.5)
    box = Timeline(readings, {GEMMA: "ready"}, folders=box_folders, models={GEMMA: ready()})
    run_2a(box)
    first, model = box.requests[0]
    assert model == GEMMA and readings[first] > 20 + brake.MIN_FALL_GIB and first <= 5


@pytest.mark.parametrize("saved, why", [
    ("released within the hour", "it fired within an hour of the gate's last automatic release"),
    ("damaged", "the gate's saved state was damaged, so it can't tell when its last automatic release was")])
def test_the_journal_says_a_pause_that_waits_for_dan_waits_for_him(box_folders, saved, why):
    # The review's I3: when release_waits_for_dan holds, the pause waits for `make brake-release`, and the line says so.
    if saved == "damaged":
        (box_folders.gate / "state.json").write_text("{not json")
    else:
        save_state(box_folders.gate, GateState(last_auto_release_at=T0 - 600), now=T0 - 600)
    logs = run_2a(Timeline([18], {GEMMA: "ready"}, folders=box_folders, gate_up=False), once=True)
    assert f"brake: new loads paused until `make brake-release`: {why}" in logs
    assert not any("the gate lifts the pause" in line for line in logs)


def test_a_started_records_start_reading_is_the_brakes_own_at_the_engines_start_by_the_boot_clock(box_folders):
    # The review's M4: with launch's start time, the start reading is the brake's own nearest the engine's start on the
    # boot's clock, whatever the wall clock did since (here it stepped 50 s ahead, so started_at reads T0 + 50); a
    # record from before it keeps the wall clock's nearest.
    f = box_folders
    (f.proc / "uptime").write_text(f"{U0 + 55:.2f} 1000.00\n")
    an_engine(f.proc, forked_at=T0 - 0.5)
    started_record(f.launch, started_at=T0 + 50, start_time=ticks(T0 - 0.5))
    wall, boot = deque([(T0, 74.0), (T0 + 50, 60.0)]), deque([(U0, 74.0), (U0 + 50, 60.0)])

    def loads(now):
        return brake.loads_in_progress(None, f.launch, now=now, boot_id=BOOT, registry=REG2A, readings=wall,
                                       boot_readings=boot, proc=f.proc, spark_uid=os.geteuid())

    assert [(load.available_at_start, load.started_at) for load in loads(T0 + 55)] == [(74.0, T0 + 50)]
    (f.proc / "uptime").write_text(f"{U0 + 5:.2f} 1000.00\n")
    started_record(f.launch)  # from before the ruling: started_at T0, no start time
    wall, boot = deque([(T0, 74.0), (T0 + 3, 60.0)]), deque([(U0 + 3, 60.0)])  # the boot clock's would give 60
    assert [load.available_at_start for load in loads(T0 + 5)] == [74.0]


def unavailable(text: str, *, cause: BaseException, sent: bool) -> GateUnavailable:
    err = GateUnavailable(text, sent=sent)
    err.__cause__ = cause
    return err


@pytest.mark.parametrize("cause, sent, wait_s", [
    pytest.param(FileNotFoundError, False, ..., id="no gate there"),
    pytest.param(TimeoutError, True, 5, id="no answer within 5 s"),
    pytest.param(TimeoutError, False, 5, id="no connection within 5 s"),
    pytest.param(ConnectionResetError, True, None, id="its answer broke off")])
def test_the_releases_fallback_says_whether_the_gate_may_still_be_up(tmp_path, capsys, cause, sent, wait_s):
    # The review's M7: the fallback stays the brief's, on any GateUnavailable; its words are true in every case. A gate
    # that may be up (it took the connection, or the request) reloads as usual once it finds the hold gone.
    error = unavailable("stand-in: the gate's answer", cause=cause(), sent=sent)
    words = messages.BRAKE_RELEASED_WITHOUT_GATE if wait_s is ... else messages.brake_released_without_answer(wait_s)
    write_hold(tmp_path, Hold("t", "r", ()))
    assert brake.release_through_gate(tmp_path / "gate-control.sock", tmp_path, client=Gate(error)) == 0
    assert capsys.readouterr().out == words + "\n" and read_hold(tmp_path) is None


def test_curl_sends_the_alert_as_it_is_and_reads_no_curlrc(tmp_path):
    # The review's M9 and M10, with Ubuntu's curl against a stand-in HTTP server on a free port (never ntfy's, nor a
    # live one): data-raw sends a leading @, quotes, backslashes and line breaks as they are, -q keeps a planted
    # .curlrc from being read, and curl needs nothing of the brake's environment.
    if not os.access(brake.CURL, os.X_OK):
        pytest.skip(f"no {brake.CURL} here")
    seen = []

    class Answer(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen.append({"path": self.path, "body": body, "priority": self.headers.get("Priority"),
                         "auth": self.headers.get("Authorization"), "planted": self.headers.get("X-Planted")})
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Answer)
    port = server.server_address[1]
    assert port not in (9100, 900, 3000, 8888) and not 800 <= port < 1024 and not 5800 <= port < 6000
    header = tmp_path / "ntfy-token"
    header.write_text(TOKEN_LINE + "\n")
    home = tmp_path / "home"
    home.mkdir()
    (home / ".curlrc").write_text('header = "X-Planted: yes"\n')  # what an engine, running as spark, could plant
    text = '@not-a-file "quoted" a back\\slash\nand a second line'
    config = brake.curl_config(f"http://127.0.0.1:{port}/stand-in-topic", header, "high", text)
    try:
        for args in (list(brake.CURL_ARGS), [arg for arg in brake.CURL_ARGS if arg != "-q"]):
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            run = subprocess.run(args, input=config, env={"HOME": str(home)}, capture_output=True, timeout=30)
            thread.join(10)
            assert run.returncode == 0, run.returncode
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        run = subprocess.run(list(brake.CURL_ARGS), input=config, env={}, capture_output=True, timeout=30)
        thread.join(10)
        assert run.returncode == 0, run.returncode
    finally:
        server.server_close()
    with_q, without_q, bare = seen
    assert with_q["body"].decode() == text and with_q["path"] == "/stand-in-topic"
    assert (with_q["priority"], with_q["auth"]) == ("high", "Bearer stand-in-token-z9")
    assert with_q["planted"] is None and without_q["planted"] == "yes"  # -q is what keeps the .curlrc out
    assert bare == with_q  # an empty environment is all it needs



# Task 23's re-review, the controller's rulings (fix round 2).

@pytest.mark.parametrize("gap", [0, 1, 2], ids=["ready with the step", "a poll after", "two polls after"])
def test_a_slow_load_then_a_step_with_ready_at_once_never_trips_the_watch(box_folders, gap):
    # The re-review's Minor 1, its probe: the coder's weights fall at 1 GiB/s, 74 to 60, which never projects under the
    # line; then its KV cache and compute buffers come as a 20 GiB step in one poll, with the gate marking it ready on
    # that poll. Its loads are read whenever the window shows a fall of MIN_FALL_GIB, so its floor (31) is kept.
    f = box_folders
    readings = [74 - 1.0 * TICK * i for i in range(57)] + [40.0] * 20
    started_record(f.launch, CODER, footprint=41, available=74, start_time=ticks(T0 - 0.5))
    an_engine(f.proc, CODER)
    box = ReadyAt(readings, {GEMMA: "ready", CODER: "starting"}, folders=f, model=CODER, ready_poll=57 + gap,
                  models={GEMMA: ready(), CODER: starting(admitted=41, available_at_start=74)})
    run_2a(box)
    assert box.requests == [] and read_hold(f.state) is None


@pytest.mark.parametrize("before", [1, 2])
def test_ready_a_poll_or_two_before_the_fall_ends_never_trips_the_watch(box_folders, before):
    f = box_folders
    readings = fall(74, 33, 2.5, then=16)
    end = next(i for i in range(1, len(readings)) if readings[i] == readings[i - 1])
    started_record(f.launch, CODER, footprint=41, available=74, start_time=ticks(T0 - 0.5))
    an_engine(f.proc, CODER)
    box = ReadyAt(readings, {GEMMA: "ready", CODER: "starting"}, folders=f, model=CODER, ready_poll=end - before,
                  models={GEMMA: ready(), CODER: starting(admitted=41, available_at_start=74)})
    run_2a(box)
    assert box.requests == [] and read_hold(f.state) is None


def test_an_events_file_that_isnt_a_regular_file_is_said_once_an_episode(box_folders):
    # The re-review's Minor 3: the file is at fault, not the step; said once an episode, not once a step, and the steps
    # wait, as for a lock. Re-review 2's Minor 3: keyed on the newest step waiting, the episode being fired or recorded,
    # not the oldest, so a second episode says it again; and once the file is put right, the journal says the steps are
    # recorded again.
    path = box_folders.state / EVENTS_FILE
    os.mkfifo(path)
    names = ["stand-in-1", "stand-in-2", "stand-in-3"]
    box = Timeline([18, 18, 60, 18, 18, 18], dict.fromkeys(names, "ready"), folders=box_folders, gate_up=False,
                   delay=999, polls=6)
    poll = box.sleep

    def sleep(seconds):
        if len(box.sleeps) == 1:
            release_hold(box_folders.state)  # the pause lifted: the next fall is a second episode
        if len(box.sleeps) == 4:
            path.unlink()  # put right: the next poll writes the steps
        poll(seconds)

    box.sleep = sleep
    logs = run_2a(box)
    said = [line for line in logs if "regular file" in line]
    line = (f"brake: ALERT — the brake's events file {path} isn't a regular file, so its steps can't be recorded "
            "for the gate")
    assert said == [line, line]  # episodes 1 and 2
    assert sum("recorded for the gate again" in each for each in logs) == 1
    assert [(e.kind, e.episode) for e in events(box_folders)] == [
        ("fired", 1), ("unload", 1), ("unload", 1), ("fired", 2), ("unload", 2)]


def test_a_new_episode_counts_past_the_steps_still_waiting(box_folders):
    # The re-review's Minor 4: the lock held past the hold's release, the brake fires again; its episode is past both
    # the file's and the waiting steps', so the gate never takes two pauses for one.
    path = box_folders.state / EVENTS_FILE
    holder = hold_the_events_lock(path)
    names = ["stand-in-1", "stand-in-2", "stand-in-3"]
    box = Timeline([18, 60, 60, 18, 18], dict.fromkeys(names, "ready"), folders=box_folders, gate_up=False,
                   delay=999, polls=5)
    poll = box.sleep

    def sleep(seconds):
        if len(box.sleeps) == 0:
            release_hold(box_folders.state)  # the gate's release, or Dan's, while the lock stays held
        if len(box.sleeps) == 3:
            release_the_lock(holder)
        poll(seconds)

    box.sleep = sleep
    try:
        run_2a(box)
    finally:
        release_the_lock(holder)
    assert [(e.kind, e.episode, e.model) for e in events(box_folders)] == [
        ("fired", 1, None), ("unload", 1, "stand-in-1"), ("fired", 2, None), ("unload", 2, "stand-in-2"),
        ("unload", 2, "stand-in-3")]
    assert read_hold(box_folders.state).episode == 2


def test_a_firing_poll_writes_the_hold_then_unloads_then_records(box_folders, monkeypatch):
    # The re-review's Minors 8 and 9: the hold first, then the unload, then the journal's line and the gate's steps,
    # even with steps still waiting from before; nothing that could wait or decode comes before the unload.
    order = []
    real_write, real_append, real_load = brake.write_hold, brake.append_event, brake.load_state

    def write_hold(folder, hold):
        order.append("hold")
        return real_write(folder, hold)

    def append_event(path, event, **kw):
        order.append(f"step {event.kind}")
        return real_append(path, event, **kw)

    def load_state(*args, **kw):
        order.append("gate's state")
        return real_load(*args, **kw)

    monkeypatch.setattr(brake, "write_hold", write_hold)
    monkeypatch.setattr(brake, "append_event", append_event)
    monkeypatch.setattr(brake, "load_state", load_state)
    box = Timeline([18], {GEMMA: "ready", EMBED: "ready"}, folders=box_folders, gate_up=False,
                   on_unload=lambda model: order.append(f"unload {model}"))
    logs = []
    run_brake(box.registry, box, box_folders.state, read_mem=box.read_mem, sleep=box.sleep,
              log=lambda line: (order.append("journal") if "new loads paused" in line else None, logs.append(line)),
              now=lambda: FIRED_AT, once=True, monotonic=box.now_s, wall=box.wall, boottime=box.boot_s, boot_id=BOOT,
              gate_state=box_folders.gate, launch=box_folders.launch, proc=box_folders.proc, spark_uid=os.geteuid())
    assert order[:order.index(f"unload {GEMMA}") + 1] == ["hold", f"unload {GEMMA}"]
    assert order[order.index(f"unload {GEMMA}"):] == [f"unload {GEMMA}", "hold", "gate's state", "journal",
                                                      "step fired", "step unload"]


def test_steps_waiting_from_an_earlier_poll_still_come_after_the_hold_and_the_unload(box_folders, monkeypatch):
    # Re-review 2's Minor 5: the case its Minor 8 named. Steps queued while another process held the lock, the lock then
    # freed and the pause lifted, the next poll fires again: its hold and unload come before any step is written, the
    # earlier ones included. A poll-start flush (the reviewer's mutant) writes them first, and fails this.
    order = []
    real_write, real_append = brake.write_hold, brake.append_event

    def write_hold(folder, hold):
        order.append("hold")
        return real_write(folder, hold)

    def append_event(path, event, **kw):
        order.append(f"step {event.kind} {event.episode}")
        return real_append(path, event, **kw)

    monkeypatch.setattr(brake, "write_hold", write_hold)
    monkeypatch.setattr(brake, "append_event", append_event)
    holder = hold_the_events_lock(box_folders.state / EVENTS_FILE)
    box = Timeline([18, 18], dict.fromkeys(["stand-in-1", "stand-in-2"], "ready"), folders=box_folders,
                   gate_up=False, delay=999, polls=2, on_unload=lambda model: order.append(f"unload {model}"))
    poll = box.sleep

    def sleep(seconds):
        if len(box.sleeps) == 0:
            release_the_lock(holder)
            release_hold(box_folders.state)
            order.append("next poll")
        poll(seconds)

    box.sleep = sleep
    try:
        run_2a(box)
    finally:
        release_the_lock(holder)
    second = order[order.index("next poll") + 1:]
    assert second[:2] == ["hold", "unload stand-in-2"]
    assert [each for each in second if each.startswith("step")] == [
        "step fired 1", "step unload 1", "step fired 2", "step unload 2"]
    assert second.index("step fired 1") > second.index("unload stand-in-2")


def test_a_hold_it_couldnt_write_is_recorded_and_its_alert_says_loads_werent_paused(box_folders, tmp_path,
                                                                                     monkeypatch):
    # The controller's ruling on Task 23's fix round 2: the fired event records whether the hold reached the disk, and
    # the brake's own alert, the gate being down, says new loads weren't paused when it didn't.
    def full_disk(state_dir, hold):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(brake, "write_hold", full_disk)
    curl = Curl(finishes_after=0)
    run_2a(Timeline([18], {GEMMA: "ready"}, folders=box_folders, gate_up=False), once=True,
           alerter=alerter(curl, ntfy_env(tmp_path)))
    assert [(e.kind, e.hold_written) for e in events(box_folders)] == [("fired", False), ("unload", True)]
    (child,) = curl.started
    words = curl_config(child.stdin.data)["data-raw"][0]
    assert words == first_alert(unloaded=[(LABELS[GEMMA], None)], hold_written=False)
    assert "couldn't write its pause, so new loads weren't paused" in words and "resume" not in words


# Task 23's second re-review, the controller's rulings (fix round 3).

@pytest.mark.parametrize("rate", [0.0, 0.5, 0.7], ids=["no fall", "0.5 GiB/s", "0.7 GiB/s"])
@pytest.mark.parametrize("gap", [0, 1, 2], ids=["ready with the step", "a poll after", "two polls after"])
def test_a_load_too_slow_for_the_window_then_a_step_with_ready_never_trips_the_watch(box_folders, rate, gap):
    # Re-review 2's Minor 1, its probe: the weights fall slower than MIN_FALL_GIB over the window (or not at all), then
    # the KV cache and compute buffers come as a 20 GiB step, the gate marking the load ready on that poll. The loads
    # are read on every poll the watch runs, so the floor is known before the step.
    f = box_folders
    readings = [74 - rate * TICK * i for i in range(57)] + [40.0] * 20
    started_record(f.launch, CODER, footprint=41, available=74, start_time=ticks(T0 - 0.5))
    an_engine(f.proc, CODER)
    box = ReadyAt(readings, {GEMMA: "ready", CODER: "starting"}, folders=f, model=CODER, ready_poll=57 + gap,
                  models={GEMMA: ready(), CODER: starting(admitted=41, available_at_start=74)})
    run_2a(box)
    assert box.requests == [] and read_hold(f.state) is None


def test_a_small_load_landing_as_one_step_with_ready_never_trips_the_watch(box_folders):
    # Re-review 2's Minor 1: whisper's 3 GiB in one poll at 27.5, ready on that poll, would project to 16.5.
    f = box_folders
    readings = [27.5] * 57 + [24.5] * 20
    started_record(f.launch, WHISPER, footprint=3, available=27.5, start_time=ticks(T0 - 0.5))
    an_engine(f.proc, WHISPER, comm="whisper-server")
    box = ReadyAt(readings, {GEMMA: "ready", WHISPER: "starting"}, folders=f, model=WHISPER, ready_poll=57,
                  models={GEMMA: ready(), WHISPER: starting(admitted=3, available_at_start=27.5)})
    run_2a(box)
    assert box.requests == [] and read_hold(f.state) is None


def test_a_hold_that_stands_though_its_folders_fsync_failed_counts_as_written(box_folders, monkeypatch):
    # Re-review 2's Minor 2: write_hold renamed the hold into place, then the folder's fsync failed. The hold stands, so
    # new loads are paused: hold_written is true, and the journal says only that the fsync failed.
    real = brake.write_hold

    def fsync_fails(state_dir, hold):
        real(state_dir, hold)
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(brake, "write_hold", fsync_fails)
    logs = run_2a(Timeline([18], {GEMMA: "ready"}, folders=box_folders, gate_up=False), once=True)
    hold = read_hold(box_folders.state)
    assert hold is not None and hold.episode == 1
    assert [(e.kind, e.hold_written) for e in events(box_folders)] == [("fired", True), ("unload", True)]
    assert sum("fsync failed" in line and "stands" in line for line in logs) == 1
    assert not any("can't write the hold" in line for line in logs)
    assert any("new loads paused" in line for line in logs)


def test_a_hold_update_it_cant_write_says_the_standing_hold_still_pauses_loads(box_folders, monkeypatch):
    # The same ruling's other half: the unload's write of the hold, listing the model, fails before its rename, so the
    # fired hold still stands and pauses new loads; the journal says so, never that new loads aren't held.
    real, calls = brake.write_hold, []

    def full_after_the_first(state_dir, hold):
        calls.append(hold)
        if len(calls) > 1:
            raise OSError(28, "No space left on device")
        real(state_dir, hold)

    monkeypatch.setattr(brake, "write_hold", full_after_the_first)
    logs = run_2a(Timeline([18], {GEMMA: "ready"}, folders=box_folders, gate_up=False), once=True)
    hold = read_hold(box_folders.state)
    assert (hold.episode, hold.unloaded) == (1, ())
    assert [line for line in logs if "ALERT" in line] == [
        f"brake: ALERT — can't write the hold in {box_folders.state}: [Errno 28] No space left on device. The hold "
        "that stands still pauses new loads, but doesn't list every model unloaded"]


def test_a_hold_that_can_never_be_written_is_one_episode_with_one_first_alert(box_folders, tmp_path, monkeypatch):
    # Re-review 2's out-of-scope storm, the controller's ruling: no hold stands and none can be written. The episode
    # stands in memory: no new episode, no new first alert, the unloads go on as with a hold, and the write is tried
    # again at most once a second.
    tries = []

    def full_disk(state_dir, hold):
        tries.append(len(box.sleeps))
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(brake, "write_hold", full_disk)
    curl = Curl(finishes_after=0)
    names = ["stand-in-1", "stand-in-2", "stand-in-3", "stand-in-4"]
    box = Timeline([18] * 12, dict.fromkeys(names, "ready"), folders=box_folders, gate_up=False, delay=999, polls=12)
    run_2a(box, alerter=alerter(curl, ntfy_env(tmp_path)))
    steps = [(e.kind, e.episode, e.hold_written) for e in events(box_folders)]
    assert steps == [("fired", 1, False)] + [("unload", 1, True)] * 4
    sent = [curl_config(child.stdin.data)["data-raw"][0] for child in curl.started]
    assert len([words for words in sent if words.startswith("Brake on")]) == 1  # one first alert; the rest follow up
    assert all(words.startswith("Brake, ") for words in sent[1:])
    assert tries == [0, 4, 8]  # at the fire, then once a second (every 4th 250 ms poll), never more


def test_a_hold_written_later_ends_the_unwritten_episode_as_a_standing_hold(box_folders, monkeypatch):
    real, tries = brake.write_hold, []

    def disk_full_then_freed(state_dir, hold):
        tries.append(len(box.sleeps))
        if len(tries) < 3:
            raise OSError(28, "No space left on device")
        real(state_dir, hold)

    monkeypatch.setattr(brake, "write_hold", disk_full_then_freed)
    box = Timeline([18] * 10, dict.fromkeys(["stand-in-1", "stand-in-2"], "ready"), folders=box_folders,
                   gate_up=False, delay=999, polls=10)
    logs = run_2a(box)
    hold = read_hold(box_folders.state)
    assert hold is not None and (hold.episode, hold.unloaded) == (1, ("stand-in-1", "stand-in-2"))
    assert "brake: the hold is written again" in logs
    assert [e.kind for e in events(box_folders)].count("fired") == 1


LATE_BY_BRAKE = ("Brake, 01:00: its pause now stands: new loads are paused until the gate is back and memory has "
                 "stayed above 28 GiB available for 5 min.")


@pytest.mark.parametrize("gate_up", [False, True], ids=["the gate down", "the gate up"])
def test_a_hold_written_late_is_recorded_and_said_to_stand_now(box_folders, tmp_path, monkeypatch, gate_up):
    # The controller's ruling on fix round 3's concern 2: the first alert said new loads weren't paused. Once a retry
    # writes the hold, the brake records hold_written for the gate, through the same queue, and, the gate being down,
    # sends the follow-up itself: the pause now stands, and how it ends.
    real, tries = brake.write_hold, []

    def disk_full_then_freed(state_dir, hold):
        tries.append(len(box.sleeps))
        if len(tries) < 2:
            raise OSError(28, "No space left on device")
        real(state_dir, hold)

    monkeypatch.setattr(brake, "write_hold", disk_full_then_freed)
    curl = Curl(finishes_after=0)
    box = Timeline([18] * 6, dict.fromkeys(["stand-in-1", "stand-in-2"], "ready"), folders=box_folders,
                   gate_up=gate_up, delay=999, polls=6)
    logs = run_2a(box, alerter=alerter(curl, ntfy_env(tmp_path)))
    assert tries == [0, 4]
    assert [(e.kind, e.episode, e.hold_written, e.sent_by_brake) for e in events(box_folders)] == [
        ("fired", 1, False, not gate_up), ("unload", 1, True, not gate_up), ("unload", 1, True, not gate_up),
        ("hold_written", 1, True, not gate_up)]
    sent = [curl_config(child.stdin.data)["data-raw"][0] for child in curl.started]
    if gate_up:
        assert sent == []
    else:
        assert len(sent) == 3 and "new loads weren't paused" in sent[0] and sent[2] == LATE_BY_BRAKE
    assert sum("new loads paused" in line for line in logs) == 1


def test_a_once_runs_last_try_records_the_hold_written_late(box_folders, tmp_path, monkeypatch):
    real, tries = brake.write_hold, []

    def full_once(state_dir, hold):
        tries.append(hold)
        if len(tries) == 1:
            raise OSError(28, "No space left on device")
        real(state_dir, hold)

    monkeypatch.setattr(brake, "write_hold", full_once)
    curl = Curl(finishes_after=0)
    run_2a(Timeline([18], {GEMMA: "ready"}, folders=box_folders, gate_up=False), once=True,
           alerter=alerter(curl, ntfy_env(tmp_path)))
    assert [(e.kind, e.hold_written) for e in events(box_folders)] == [
        ("fired", False), ("unload", True), ("hold_written", True)]
    assert read_hold(box_folders.state).unloaded == (GEMMA,)
    sent = [curl_config(child.stdin.data)["data-raw"][0] for child in curl.started]
    assert len(sent) == 2 and sent[1] == LATE_BY_BRAKE


def test_an_unwritten_pause_ends_once_memory_stays_above_the_warn_line_for_5_minutes(box_folders, monkeypatch):
    # With no hold ever written, the episode ends after the gate's five minutes above the warn line, saying no pause
    # stood; the next fall is a new episode.
    def full_disk(state_dir, hold):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(brake, "write_hold", full_disk)
    above = int(brake.RELEASE_AFTER_S / TICK)
    readings = [18] + [60] * (above + 2) + [18]
    box = Timeline(readings, dict.fromkeys(["stand-in-1", "stand-in-2"], "ready"), folders=box_folders,
                   gate_up=False, delay=99999, polls=len(readings))
    logs = run_2a(box)
    assert [line for line in logs if "stood" in line] == [
        "brake: memory has stayed above the warn line for 5 minutes, so episode 1 is over; its hold was never written, "
        "so no pause ever stood"]
    assert [(e.kind, e.episode) for e in events(box_folders) if e.kind == "fired"] == [("fired", 1), ("fired", 2)]


def test_an_unwritten_pause_needs_5_minutes_above_the_warn_line_in_one_stretch(box_folders, monkeypatch):
    # Four minutes above it, a dip under it, four more: the episode stands on, as the gate's pause would.
    def full_disk(state_dir, hold):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(brake, "write_hold", full_disk)
    four_minutes = int(240 / TICK)
    readings = [18] + [60] * four_minutes + [25] + [60] * four_minutes + [18]
    box = Timeline(readings, dict.fromkeys(["stand-in-1", "stand-in-2"], "ready"), folders=box_folders,
                   gate_up=False, delay=99999, polls=len(readings))
    logs = run_2a(box)
    assert not any("stood" in line for line in logs)
    assert [(e.kind, e.episode) for e in events(box_folders)] == [("fired", 1), ("unload", 1), ("unload", 1)]


def test_a_flapping_lock_costs_a_poll_its_budget_at_most(box_folders, monkeypatch):
    # Re-review 2's out-of-scope stall, the controller's ruling: a reader that takes the lock back after each of the
    # brake's appends would make one flush wait up to LOCK_WAIT_S a step. A poll's flush waits LOCK_WAIT_S in all,
    # counting the waits that succeed; what is left waits for the next poll. Here 21 steps wait behind a FIFO (no lock
    # found held), then the file is put right, with a reader that takes the real lock again after each step the brake
    # writes and holds it 0.15 s: 21 steps would cost a poll about 3 s.
    path = box_folders.state / EVENTS_FILE
    os.mkfifo(path)
    names = [f"stand-in-{n}" for n in range(1, 21)]
    box = Timeline([18] * 24, dict.fromkeys(names, "ready"), folders=box_folders, gate_up=False, delay=999, polls=24)
    stamps, flapper = [], []
    real_append = brake.append_event

    def take_the_lock():
        flapper[0].stdin.write("take\n")
        flapper[0].stdin.flush()
        assert flapper[0].stdout.readline().strip() == "held"

    def append_event(events_path, event, **kw):
        real_append(events_path, event, **kw)
        if flapper and flapper[0].poll() is None:
            take_the_lock()  # the reader takes it back at once, before the brake's next try

    monkeypatch.setattr(brake, "append_event", append_event)
    poll = box.sleep

    def sleep(seconds):
        if len(box.sleeps) == 20:  # every model asked, 21 steps waiting
            path.unlink()
            path.touch()
            flapper.append(subprocess.Popen(
                [sys.executable, "-c", "import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDONLY)\n"
                 "for _ in sys.stdin:\n    fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); time.sleep(0.15);"
                 " fcntl.flock(fd, fcntl.LOCK_UN)", str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True))
            take_the_lock()
        if len(box.sleeps) == 22:
            release_the_lock(flapper[0])
        stamps.append(time.monotonic())
        poll(seconds)

    box.sleep = sleep
    real_fsync, os.fsync = os.fsync, lambda fd: None  # this measures the lock's waits, not the disk
    try:
        run_2a(box)
    finally:
        os.fsync = real_fsync
        for each in flapper:
            release_the_lock(each)
    flapping = stamps[21] - stamps[20]  # poll 21: 21 steps to write, under the flapping lock
    assert flapping < 2 * brake.LOCK_WAIT_S, flapping
    written = events(box_folders)
    assert [e.seq for e in written] == list(range(1, 22))  # every step, in order, by the polls after it
    assert [e.model for e in written] == [None] + names
