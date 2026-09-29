import argparse
import os
from dataclasses import replace
from pathlib import Path

import pytest

from spark import brake, cli
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


def test_the_hold_names_the_command_that_lifts_it(tmp_path):
    # `spark` isn't on Dan's PATH: the Makefile runs it through uv, from the clone.
    logs = []
    run_brake(REG, FakeClient([Running("coder", "ready")]), tmp_path, read_mem=lambda: MemInfo(121.7, 18),
              sleep=lambda s: None, log=logs.append, now=lambda: "t", once=True)
    assert "brake: holding new loads until `make brake-release`" in logs


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
                 late=(), fall=0.0, noise=0.0, polls=12, errors=None, reload=None, set_avail=None, on_unload=None):
        self.avail, self.registry, self.hold_dir = avail, registry, hold_dir
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
        if model in self.fail:
            raise LlamaSwapError(f"llama-swap POST /api/models/unload/{model}: HTTP 500")
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
    assert read_hold(tmp_path) == Hold("T", "18.0 GiB available", ())
    assert any("coder" in line and "HTTP 500" in line for line in logs)


def test_a_release_during_an_unload_holds_again_with_the_unload_in_it(tmp_path):
    # `spark brake --release` lands while the unload is on its way, and memory is still below the line (I1).
    brake_loop(Box(18, ALL, on_unload=lambda model: release_hold(tmp_path)), tmp_path, once=True)
    assert read_hold(tmp_path) == Hold("T", "18.0 GiB available", ("coder",))


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
    assert read_hold(tmp_path) == Hold("T", "18.0 GiB available", ("coder",))  # still held once memory is back


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
    # The first write finds the disk full; the next goes through, and the log says the hold is back (N3: M).
    real, calls = brake.write_hold, []

    def full_disk_once(state_dir, hold):
        calls.append(hold)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        real(state_dir, hold)

    monkeypatch.setattr(brake, "write_hold", full_disk_once)
    logs = brake_loop(Box(18, ALL), tmp_path, once=True)
    assert "brake: the hold is written again" in logs
    assert read_hold(tmp_path) == Hold("T", "18.0 GiB available", ("coder",))


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


def start(client, state, logs, clock=None):
    return brake.StartCheck(client, state, KEY_ENV, logs.append, now=lambda: "T", clock=clock or Clock())


def run_with_check(client, state, *, polls=3, available=60.0, clock=None, each_poll=None):
    """The brake, with its start check, for `polls` polls at `available` GiB; `each_poll(i)` runs after poll i."""
    logs, done = [], []
    check = start(client, state, logs, clock)

    def sleep(seconds):
        done.append(seconds)
        if each_poll:
            each_poll(len(done))
        if len(done) >= polls:
            raise Enough

    try:
        run_brake(REG, client, state, read_mem=lambda: MemInfo(121.7, available), sleep=sleep, log=logs.append,
                  now=lambda: "T", start_check=check)
    except Enough:
        pass
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
    seen = []

    def each_poll(i):
        seen.append(brake.read_key_check(tmp_path)[0].ok)
        clock.t += 1

    logs = run_with_check(client, tmp_path, polls=5, clock=clock, each_poll=each_poll)
    assert client.calls == 3 and seen == [None, None, True, True, True]
    assert logs == ["brake: llama-swap answers GET /running with the key in $LLAMASWAP_KEY_SPARK"]


def test_a_llama_swap_that_never_answers_fails_the_start_check_after_its_wait(tmp_path):
    down = LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: timed out")
    client, clock = Answers(down), Clock()

    def each_poll(i):
        clock.t += 10

    logs = run_with_check(client, tmp_path, polls=6, clock=clock, each_poll=each_poll)
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
    # The first poll acts on memory before the check asks anything.
    order = []

    class Recording(Answers):
        def running(self):
            order.append("running")
            return super().running()

    run_brake(REG, Recording([]), tmp_path, read_mem=lambda: order.append("memory") or MemInfo(121.7, 60),
              sleep=lambda s: None, log=lambda line: None, now=lambda: "T", once=True,
              start_check=start(Recording([]), tmp_path, []))
    assert order == ["memory", "running"]


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
    monkeypatch.setattr(brake, "run_brake", lambda registry, client, state, **kw: seen.update(kw))
    assert brake.run(argparse.Namespace(release=False, once=once, key_env=KEY_ENV)) == 0
    check = seen["start_check"]
    assert (check is None) if once else (check.key_env == KEY_ENV and check.client.timeout == 2)
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
