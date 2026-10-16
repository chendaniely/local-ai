"""Task 40: the drills' memory hog, stack/measure/hog.py, loaded from its path. Every reading, step and pause here is a
stand-in's, but for test_its_memory_is_resident_and_freed, which takes 4 MiB of real memory through the script's own
allocator, under make test's 4 GiB cap. The fixture swaps the script's own reader and allocator for ones that fail the
test if anything reaches them. A stand-in box allows a bounded number of steps, readings and seconds, so a broken loop
fails within those bounds instead of running on toward the cap."""

import importlib.util
import os
import re
import signal
import sys
from collections import deque
from pathlib import Path

import pytest

from spark import brake
from spark.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
HOG = ROOT / "stack" / "measure" / "hog.py"
STACK_REGISTRY = ROOT / "stack" / "models.yaml"
MAX_STEPS = 1000  # 250 GiB at the default step: more than the box has, so a stand-in refuses more
MAX_READS = 5000  # the longest run here reads about 2,200 times
MAX_CLOCK_S = 10_000.0  # and a stand-in's clock stops a run that sleeps past this
SIGNALS = {"sighup": signal.SIGHUP, "sigterm": signal.SIGTERM, "ctrl-z": signal.SIGTSTP}


def load_hog(monkeypatch):
    spec = importlib.util.spec_from_file_location("hog", HOG)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "hog", module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def hog(monkeypatch):
    module = load_hog(monkeypatch)

    def the_box(*args, **kwargs):
        raise AssertionError("a test reached the hog's own reader or allocator, which read and take the box's memory")

    monkeypatch.setattr(module, "read_available_gib", the_box)
    monkeypatch.setattr(module, "Memory", the_box)
    return module


class Box:
    """A stand-in box: its MemAvailable, a clock that moves only when the hog sleeps, and the hog's steps, each a
    record, never memory. A step shows in MemAvailable `lag_s` after it is taken, and only `shows(n)` of step n's size
    (all of it by default). From `drop_at` on, MemAvailable reads `drop_to`, as when something else takes memory.
    `on_take(n)` runs once step n is taken."""

    def __init__(self, start, *, lag_s=0.0, shows=None, drop_at=None, drop_to=None, on_take=None):
        self.start, self.lag_s = start, lag_s
        self.shows = shows or (lambda n: 1.0)
        self.drop_at, self.drop_to = drop_at, drop_to
        self.on_take = on_take
        self.now = 0.0
        self.steps = []  # {"n", "gib", "at", "freed_at"}
        self.reads = []  # (time, GiB)

    @property
    def live(self):
        return [step for step in self.steps if step["freed_at"] is None]

    def available_at(self, at):
        if self.drop_at is not None and at >= self.drop_at:
            return self.drop_to
        shown = sum(step["gib"] * self.shows(step["n"]) for step in self.steps
                    if step["at"] + self.lag_s <= at and (step["freed_at"] is None or step["freed_at"] > at))
        return self.start - shown

    def read(self):
        if len(self.reads) >= MAX_READS:
            raise AssertionError(f"the hog read MemAvailable more than {MAX_READS} times")
        value = self.available_at(self.now)
        self.reads.append((self.now, value))
        return value

    def take(self, gib):
        if len(self.steps) >= MAX_STEPS:
            raise AssertionError(f"the hog took more than {MAX_STEPS} steps")
        self.steps.append({"n": len(self.steps) + 1, "gib": gib, "at": self.now, "freed_at": None})
        if self.on_take:
            self.on_take(len(self.steps))

    def free_all(self):
        for step in self.live:
            step["freed_at"] = self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds
        if self.now > MAX_CLOCK_S:
            raise AssertionError(f"the hog ran past {MAX_CLOCK_S:g} s")


def run(hog, box, *argv):
    return hog.main(list(argv), read_available=box.read, memory=box, sleep=box.sleep)


def grow(hog, box, *, leave_gib, floor_gib=24.0):
    return hog.grow(box.read, box.take, box.free_all, leave_gib=leave_gib, floor_gib=floor_gib, step_gib=0.25,
                    pause_s=2.5, sleep=box.sleep)


@pytest.mark.parametrize("lag_s", [0.0, 1.0], ids=["at-once", "a-second-late"])
def test_it_stops_at_leave(hog, lag_s):
    # A step that shows a second late is waited for (re-read every 250 ms), not taken for one that never showed.
    box = Box(74.0, lag_s=lag_s)
    result = grow(hog, box, leave_gib=48.0)
    assert (result.held_gib, result.stopped) == (26.0, "leave")
    assert len(box.steps) == 104 and box.live == box.steps and box.available_at(box.now) == 48.0
    # Paced: each step at least --pause-s after the last.
    times = [step["at"] for step in box.steps]
    assert min(b - a for a, b in zip(times, times[1:])) >= 2.5
    # And through main: it holds at 48 for --hold-s, then frees everything and exits 0.
    box = Box(74.0, lag_s=lag_s)
    assert run(hog, box, "--leave", "48", "--hold-s", "60") == 0
    assert len(box.steps) == 104 and box.live == []
    # It found 48 one pause after its last step (that step showed at once, within 90% of all it holds), then held
    # for the 60 s.
    held_until = max(step["freed_at"] for step in box.steps)
    assert held_until - box.steps[-1]["at"] == pytest.approx(2.5 + 60)


@pytest.mark.parametrize("argv, named, reserve", [
    (["--leave", "20"], "the floor, 24 GiB", True),
    (["--floor", "20", "--leave", "22"], "under 24 GiB", True),
    (["--floor", "30", "--leave", "28"], "--floor 30 GiB", False),
], ids=["leave-under-the-floor", "floor-under-the-reserve", "leave-under-a-raised-floor"])
def test_a_leave_under_the_floor_is_refused(hog, capsys, argv, named, reserve):
    # The floor can only be raised: one under the reserve, 24, is refused as a --leave under the floor is (the
    # controller's ruling, 2026-10-16). A floor raised above the reserve isn't called the reserve.
    box = Box(74.0)
    assert run(hog, box, *argv) == 2
    assert box.steps == [] and box.reads == []
    words = capsys.readouterr().err
    assert named in words and ("the reserve every load keeps free" in words) == reserve


@pytest.mark.parametrize("argv", [["--step-gib", "0.5"], ["--pause-s", "2"], ["--step-gib", "0.3", "--pause-s", "1"]],
                         ids=["bigger-steps", "shorter-pauses", "both"])
def test_a_faster_pace_is_refused(hog, capsys, argv):
    # The pace can only be slowed, as the floor can only be raised (the controller's ruling, 2026-10-16).
    box = Box(74.0)
    assert run(hog, box, "--leave", "48", "--hold-s", "0", *argv) == 2
    assert box.steps == [] and box.reads == []
    words = capsys.readouterr().err
    assert "rate watch" in words and all(option in words for option in argv[::2])
    # A slower one runs.
    box = Box(30.0)
    assert run(hog, box, "--leave", "28", "--step-gib", "0.125", "--pause-s", "5", "--hold-s", "0") == 0
    assert len(box.steps) == 16 and box.live == []


@pytest.mark.parametrize("start, code, words", [
    (47.75, 3, "already under --leave 48.00 GiB; nothing taken"),
    (48.0, 0, "already within a step of --leave 48.00 GiB; nothing taken"),
    (48.2, 0, "already within a step of --leave 48.00 GiB; nothing taken"),
], ids=["under", "at", "within-a-step"])
def test_nothing_is_taken_at_or_under_leave(hog, capsys, start, code, words):
    # Under --leave already, something else holds memory the drill didn't expect: a stop (the controller's ruling,
    # 2026-10-16). At it, or within a step above it, the drill's moment is already there.
    box = Box(start)
    assert run(hog, box, "--leave", "48") == code
    assert box.steps == []
    out = capsys.readouterr()
    assert words in (out.err if code else out.out)


def test_its_help_lists_every_exit_code(hog, capsys):
    with pytest.raises(SystemExit) as stop:
        hog.main(["--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    for code in (0, 1, 2, 3, 128 + signal.SIGHUP, 130, 128 + signal.SIGTERM, 128 + signal.SIGTSTP):
        assert re.search(rf"^\s+{code}\s", out, re.MULTILINE), code


@pytest.mark.parametrize("third", [0.0, 0.2], ids=["flat", "a-fifth"])
def test_a_step_that_doesnt_show_stops_it(hog, third):
    # After the third step MemAvailable doesn't fall (flat), or falls a fifth of a step: 0.55 of 0.75 GiB held, under
    # 90%. Either way the hog re-reads for 5 s, then frees every step and stops.
    def shows(n):
        return third if n == 3 else 1.0

    box = Box(74.0, shows=shows)
    result = grow(hog, box, leave_gib=48.0)
    assert result.stopped == "not_reflected" and result.held_gib == 0.75
    assert len(box.steps) == 3 and box.live == []
    third_at, freed_at = box.steps[2]["at"], box.steps[2]["freed_at"]
    assert freed_at - third_at == pytest.approx(5.0)  # it waited the 5 s, no more
    box = Box(74.0, shows=shows)
    assert run(hog, box, "--leave", "48") == 3
    assert len(box.steps) == 3 and box.live == []


@pytest.mark.parametrize("case", ["after-a-step", "in-a-pause", "at-leave"])
def test_a_fall_under_the_floor_while_it_holds_frees_everything(hog, case):
    # From 30 down to --leave 28 takes 8 steps, 2.5 s apart, the last at 17.5 s, and it holds from 20 s. Something else
    # takes memory, and MemAvailable reads 23, under the floor, 24: as the third step is taken, at 5 s, so the reading
    # right after that step is the first to see it; at 6 s, in the pause after it; or at 30 s, while it holds.
    def a_box():
        box = Box(30.0, drop_at={"in-a-pause": 6.0, "at-leave": 30.0}.get(case), drop_to=23.0)
        if case == "after-a-step":
            def drop_now(n):
                if n == 3:
                    box.drop_at = box.now
            box.on_take = drop_now
        return box

    box = a_box()
    assert run(hog, box, "--leave", "28", "--hold-s", "60") == 3
    assert box.steps and box.live == []
    first_under = next(at for at, gib in box.reads if gib < 24)
    assert first_under - box.drop_at <= hog.POLL_S  # read within a poll of the fall, through every pause
    assert {step["freed_at"] for step in box.steps} == {first_under}  # and freed at once: no pause, no reading first
    assert box.now == first_under and box.reads[-1][1] == 23.0
    if case != "at-leave":
        result = grow(hog, a_box(), leave_gib=28.0)
        assert result.stopped == "floor" and result.held_gib == 0.75


@pytest.mark.parametrize("way", ["exception", "ctrl-c", "sighup", "sigterm", "ctrl-z"])
def test_it_frees_everything_on_any_way_out(hog, capsys, way):
    def at_step_5(n):
        if n != 5:
            return
        if way == "exception":
            raise RuntimeError("a stand-in's failure")
        if way == "ctrl-c":
            raise KeyboardInterrupt
        os.kill(os.getpid(), SIGNALS[way])

    # The test's own handlers only note a signal that reaches them: a hog that doesn't take a signal for itself runs on
    # to --leave and exits 0, and the test fails, never pytest (which SIGTSTP would otherwise stop).
    reached_the_test = []

    def note(signum, frame):
        reached_the_test.append(signum)

    before = {signum: signal.signal(signum, note) for signum in SIGNALS.values()}
    try:
        box = Box(74.0, on_take=at_step_5)
        if way == "exception":
            with pytest.raises(RuntimeError):
                run(hog, box, "--leave", "48", "--hold-s", "0")
        else:
            code = 130 if way == "ctrl-c" else 128 + SIGNALS[way]  # 129, 143, and 148 for Ctrl-Z on Linux
            assert run(hog, box, "--leave", "48", "--hold-s", "0") == code
        after = {signum: signal.getsignal(signum) for signum in SIGNALS.values()}
    finally:
        for signum, handler in before.items():
            signal.signal(signum, handler)
    assert len(box.steps) == 5 and box.live == []
    # The hog took the signal itself, then put the test's handlers back.
    assert reached_the_test == [] and all(handler is note for handler in after.values())
    if way == "ctrl-z":  # it ends the hog, never pauses it holding memory with nothing watching the floor
        words = capsys.readouterr().err
        assert "Ctrl-Z ends the hog rather than pausing it" in words and "freed everything" in words


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="RssAnon is in Linux's /proc/self/status")
def test_its_memory_is_resident_and_freed(monkeypatch):
    # The one test that takes real memory, through the script's own allocator: 4 MiB, under make test's 4 GiB cap.
    # Every page is written, so all of it shows in RssAnon at once; free_all gives all of it back.
    def rss_anon_kib():
        line = next(line for line in Path("/proc/self/status").read_text().splitlines() if line.startswith("RssAnon:"))
        return int(line.split()[1])

    memory = load_hog(monkeypatch).Memory()
    try:
        before = rss_anon_kib()
        memory.take(4 / 1024)
        taken = rss_anon_kib()
    finally:
        memory.free_all()
    freed = rss_anon_kib()
    assert 4096 <= taken - before < 4096 + 1024
    assert taken - freed >= 4096 - 256


def test_its_pace_stays_under_the_rate_watch(hog):
    registry = load_registry(STACK_REGISTRY)
    poll_s = registry.brake.poll_ms / 1000

    def watched(pause_s=None):
        """From 74 down to the floor, 24, through main at its default step and pause, or with `pause_s`, through grow
        (main refuses a faster pace): each of the brake's readings every poll, through Task 23's predict_breach,
        keeping the readings the brake keeps (twice its window). Returns whether each poll would have fired, and the
        readings."""
        box = Box(74.0)
        if pause_s is None:
            assert run(hog, box, "--leave", "24", "--hold-s", "0") == 0
        else:
            result = hog.grow(box.read, box.take, box.free_all, leave_gib=24.0, floor_gib=24.0, step_gib=0.25,
                              pause_s=pause_s, sleep=box.sleep)
            assert result.stopped == "leave"
        assert box.reads[0] == (0.0, 74.0) and len(box.steps) == 200  # from 74 all the way down to 24
        readings, fired, seen = deque(), [], []
        end = max(step["at"] for step in box.steps) + 2 * brake.FALL_WINDOW_S
        for i in range(int(end / poll_s) + 1):
            at = i * poll_s
            readings.append((at, box.available_at(at)))
            seen.append(readings[-1][1])
            while readings and at - readings[0][0] > 2 * brake.FALL_WINDOW_S:
                readings.popleft()
            # MIN_FALL_GIB through predict_breach's default, so Task 47's constants re-check the pace.
            fired.append(brake.predict_breach(readings, brake_gib=registry.brake.brake_gib,
                                              lead_s=brake.UNLOAD_LEAD_S, window_s=brake.FALL_WINDOW_S,
                                              floor_gib=None))
        return fired, seen

    fired, seen = watched()
    assert min(seen) == 24.0
    assert not any(fired)
    # The same watch fires for a hog fifty times as fast, 5 GiB/s: these readings are ones the brake would act on.
    fired, _ = watched(pause_s=0.05)
    assert any(fired)
