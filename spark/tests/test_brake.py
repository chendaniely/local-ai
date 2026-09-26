import argparse
import os
from dataclasses import replace
from pathlib import Path

import pytest

from spark import brake
from spark.brake import Action, plan_brake, run_brake
from spark.hold import Hold, read_hold, release_hold, write_hold
from spark.llamaswap import LlamaSwapError, LlamaSwapUnreachable, Running
from spark.memory import MemInfo
from spark.registry import BrakeThresholds, RegistryError, load_registry

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def plan(available, running):
    return plan_brake(MemInfo(121.7, available), REG.brake, REG, running)


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
    assert read_hold(tmp_path) == Hold("2026-09-23T10:00:00", "18.0 GiB available", ("coder",))


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
    """MemAvailable plus llama-swap as v257's source has it: an engine being unloaded reads `stopping`, then leaves
    /running, and its footprint shows in MemAvailable. `delay` is how many polls that takes (0: before the unload
    is answered, as v257 answers once the engine has exited). `stuck` engines stay `ready` after an accepted
    unload, `hung` ones stay `stopping`; `fail` unloads raise; `late` ones time out and the engine stops anyway,
    as v257 carries on with an unload whose caller gave up. `errors` maps a poll to the error /running answers
    with then, and `reload` a poll to a model that is loaded again then. Each sleep is a poll: memory falls by
    `fall` GiB, and after `polls` of them the run ends."""

    def __init__(self, avail, running, *, registry=REG, hold_dir=None, delay=1, stuck=(), hung=(), fail=(),
                 late=(), fall=0.0, polls=12, errors=None, reload=None, on_unload=None):
        self.avail, self.registry, self.hold_dir = avail, registry, hold_dir
        self.states = dict(running) if isinstance(running, dict) else {m: "ready" for m in running}
        self.delay, self.fall, self.polls, self.errors, self.on_unload = delay, fall, polls, errors or {}, on_unload
        self.reload = reload or {}
        self.stuck, self.hung, self.fail, self.late = set(stuck), set(hung), set(fail), set(late)
        self.exits = {}  # a stopping engine: polls until it has exited
        self.requests, self.sleeps, self.running_calls, self.held_at_unload = [], [], 0, []

    def read_mem(self):
        return MemInfo(121.7, self.avail)

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
        if model in self.fail:
            raise LlamaSwapError(f"llama-swap POST /api/models/unload/{model}: HTTP 500")
        if model not in self.stuck and self.states.get(model) == "ready":
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
    assert read_hold(tmp_path) == Hold("T", "18.0 GiB available", ())
    assert any("coder" in line and "HTTP 500" in line for line in logs)


def test_a_release_during_an_unload_holds_again_with_the_unload_in_it(tmp_path):
    # `spark brake --release` lands while the unload is on its way, and memory is still below the line (I1).
    brake_loop(Box(18, ALL, on_unload=lambda model: release_hold(tmp_path)), tmp_path, once=True)
    assert read_hold(tmp_path) == Hold("T", "18.0 GiB available", ("coder",))


def test_an_unload_over_a_damaged_hold_writes_a_true_one(tmp_path):
    # read_hold's stand-in for a damaged file ("an unknown time", "can't be read") is never written back (M1).
    (tmp_path / "hold.json").write_text("{garbage")
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
    assert read_hold(tmp_path) == Hold("T", "18.0 GiB available", ("coder",))  # still held once memory is back


def test_it_stops_waiting_when_memory_keeps_falling(tmp_path):
    # The coder's memory takes 20 polls, and memory falls 0.5 GiB a poll meanwhile: the brake must not wait (I2).
    box = Box(18, ALL, delay=20, fall=0.5, polls=6)
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
    # up after 2 s. A slower stop reads as a failure, then as `stopping`: no second model goes.
    box = Box(18, ALL, late={"coder"}, delay=8)
    logs = brake_loop(box, tmp_path)
    assert box.requests == [(0, "coder")] and box.avail == 46
    assert any("timed out" in line for line in logs)


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
