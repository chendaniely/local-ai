"""Task 41: the CUDA-allocatable ceiling, stack/measure/cuda_ceiling.py, loaded from its path. Every reading, pause and
CUDA call here is a stand-in's: a stand-in libcudart whose functions take their arguments as ctypes converts them,
over a stand-in box whose MemAvailable falls as each step is touched. No test loads a library, reads the box or ends
the process: the script is executed with a stand-in ctypes, the fixture swaps its own loader, reader and watchdog for
ones that fail the test if reached and its os for one whose _exit does, and a check after every test finds no
libcudart mapped into the test's process. The stand-in allows a bounded number of allocations, readings, calls and
seconds, so a broken loop fails within those bounds instead of running on. The tests of the reader itself read a
stand-in meminfo file, and the watchdog's run with a stand-in clock and exit."""

import ctypes
import importlib.util
import os
import re
import signal
import sys
import threading
import time
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark import brake
from spark.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "stack" / "measure" / "cuda_ceiling.py"
STACK_REGISTRY = ROOT / "stack" / "models.yaml"
GIB = 1024**3
STEP_BYTES = GIB // 4  # the default step, 0.25 GiB
MAX_ALLOCS = 1000  # 250 GiB at the default step: more than the box has, so the stand-in refuses more
MAX_READS = 10_000  # the longest run here reads about 5,600 times
MAX_CALLS = 10_000  # and calls CUDA or reads about 7,500 times: a loop that calls CUDA without reading fails here
MAX_CLOCK_S = 10_000.0  # and the stand-in's clock stops a run that sleeps past this
CUDA_TOTAL = 130_594_156_544  # cudaMemGetInfo's total on the box (2026-10-16): 121.63 GiB, MemTotal
OUT_OF_MEMORY = 2  # cudaErrorMemoryAllocation
INVALID_VALUE = 1  # cudaErrorInvalidValue
SIGNALS = {"sighup": signal.SIGHUP, "sigterm": signal.SIGTERM, "ctrl-z": signal.SIGTSTP}
DANS_CALL = "This needs Dan's decision before any rerun (Task 41, Step 4)."
SECOND_STOP = ("run it again once memory is steady; if it stops this way again, this method can't measure the ceiling "
               "here, and that's Dan's call (Task 41, Step 4).")
# /proc/meminfo's own format, from the box (2026-10-16): MemFree comes before MemAvailable, every value in kB.
MEMINFO = """MemTotal:       127533356 kB
MemFree:        11041096 kB
MemAvailable:   51839984 kB
Buffers:          123456 kB
Cached:         44569984 kB
"""


class NoLibraries:
    """ctypes, but every way it has to load a library fails the test before one is loaded."""

    LOADERS = {"CDLL", "PyDLL", "cdll", "pydll", "LibraryLoader", "util"}

    def __getattr__(self, name):
        if name in self.LOADERS:
            raise AssertionError(f"a test reached ctypes.{name}, which loads a library: the real libcudart, perhaps")
        return getattr(ctypes, name)


class NoExit:
    """os, but ending the process, or signalling one, fails the test instead."""

    def __getattr__(self, name):
        if name in ("_exit", "abort", "kill", "killpg"):
            raise AssertionError(f"a test reached os.{name}, which would end or signal a process")
        return getattr(os, name)


def load_script(monkeypatch):
    """The script, executed with a stand-in in sys.modules["ctypes"], so even its import-time code (the binding's
    types) can't reach a library loader; the stand-in hands back ctypes' own types, so the binding is ctypes'."""
    spec = importlib.util.spec_from_file_location("cuda_ceiling", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "cuda_ceiling", module)
    real = sys.modules["ctypes"]
    sys.modules["ctypes"] = NoLibraries()
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules["ctypes"] = real
    assert isinstance(module.ctypes, NoLibraries)
    return module


@pytest.fixture
def cc(monkeypatch):
    module = load_script(monkeypatch)

    def the_box(*args, **kwargs):
        raise AssertionError("a test reached the script's own loader, reader or watchdog, which load the real "
                             "libcudart, read the box's memory and can end the process")

    monkeypatch.setattr(module, "load_lib", the_box)
    monkeypatch.setattr(module, "read_available_gib", the_box)
    monkeypatch.setattr(module, "start_watchdog", the_box)
    monkeypatch.setattr(module, "os", NoExit())
    return module


@pytest.fixture(autouse=True)
def no_libcudart_in_this_process():
    yield
    maps = Path("/proc/self/maps")
    if maps.exists():
        assert "libcudart" not in maps.read_text(), "libcudart is mapped into the tests' process"


def _target(arg):
    """What a pointer argument points at, given as ctypes.byref(x) or ctypes.pointer(x)."""
    return arg._obj if hasattr(arg, "_obj") else arg.contents


def _address(arg):
    return arg.value if isinstance(arg, ctypes.c_void_p) else arg


class CFunction:
    """One function of the stand-in libcudart, as ctypes holds one: argtypes and restype for the script to set, and each
    call's arguments converted by them as ctypes converts them, so a call ctypes would refuse fails here too. A char*
    result comes back as bytes only when restype says so, as with ctypes, and otherwise as an address."""

    def __init__(self, name, body):
        self.name, self.body = name, body
        self.argtypes = None
        self.restype = ctypes.c_int  # ctypes' default

    def __call__(self, *args):
        assert self.argtypes is not None, f"{self.name} was called before its argtypes were set"
        assert len(args) == len(self.argtypes), f"{self.name} takes {len(self.argtypes)} arguments, given {len(args)}"
        for arg, argtype in zip(args, self.argtypes):
            argtype.from_param(arg)
        result = self.body(*args)
        if isinstance(result, bytes) and self.restype is not ctypes.c_char_p:
            return 0xFFFF_0000_1000
        return result


class StandInWatchdog:
    """What main starts in place of the watchdog's thread: it notes how it was started, and each stop."""

    def __init__(self):
        self.started, self.stops = [], 0

    def __call__(self, read_available, *, reserve_gib, holding):
        self.started.append((reserve_gib, holding))
        return self

    def stop(self):
        self.stops += 1


class Box:
    """A stand-in box and its libcudart (`lib`): MemAvailable, a clock that moves only when the script sleeps, and
    CUDA's allocations, each a record, never memory. An allocation shows in MemAvailable once cudaMemset has touched all
    of it, `lag_s` later, and only `shows(n)` of step n's size (all of it by default). The first CUDA call starts CUDA,
    whose context takes `context_gib` of MemAvailable. `others(at)` is the memory other processes have freed since the
    start, in GiB (none by default; taken, below 0). From `drop_at` on, MemAvailable reads `drop_to`, as when something
    else takes memory. `fail(name, n)` gives the error code a call returns for step n (0: none); `before(name, n)` runs
    as a call begins and `after(name, n)` once its work is done."""

    def __init__(self, start, *, lag_s=0.0, shows=None, others=None, drop_at=None, drop_to=None, context_gib=0.0,
                 cuda_free=80 * GIB, fail=None, before=None, after=None):
        self.start, self.lag_s = start, lag_s
        self.others = others or (lambda at: 0.0)
        self.shows = shows or (lambda n: 1.0)
        self.drop_at, self.drop_to = drop_at, drop_to
        self.context_gib, self.context_at = context_gib, None
        self.cuda_free = cuda_free
        self.fail = fail or (lambda name, n: 0)
        self.before = before or (lambda name, n: None)
        self.after = after or (lambda name, n: None)
        self.now = 0.0
        self.allocs = []  # {"n", "pointer", "bytes", "at", "touched_at", "freed_at"}
        self.calls = []  # (name, n, time), the readings among them as "read"
        self.reads = []  # (time, GiB)
        self.memsets = []  # (pointer, value, count)
        self.read_error = None  # an OSError each reading raises, once set, as an unreadable /proc/meminfo would
        self.watchdog = StandInWatchdog()
        names = ("cudaMemGetInfo", "cudaMalloc", "cudaMemset", "cudaDeviceSynchronize", "cudaFree",
                 "cudaGetErrorString")
        self.lib = SimpleNamespace(**{name: CFunction(name, getattr(self, "_" + name)) for name in names})

    @property
    def live(self):
        return [alloc for alloc in self.allocs if alloc["freed_at"] is None]

    def available_at(self, at):
        if self.drop_at is not None and at >= self.drop_at:
            return self.drop_to
        context = self.context_gib if self.context_at is not None and self.context_at <= at else 0.0
        shown = sum(alloc["bytes"] / GIB * self.shows(alloc["n"]) for alloc in self.allocs
                    if alloc["touched_at"] is not None and alloc["touched_at"] + self.lag_s <= at
                    and (alloc["freed_at"] is None or alloc["freed_at"] > at))
        return self.start - context - shown + self.others(at)

    def read(self):
        if len(self.reads) >= MAX_READS:
            raise AssertionError(f"the script read MemAvailable more than {MAX_READS} times")
        if self.read_error is not None:
            raise self.read_error
        value = self.available_at(self.now)
        self.reads.append((self.now, value))
        self.calls.append(("read", len(self.allocs), self.now))
        return value

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds
        if self.now > MAX_CLOCK_S:
            raise AssertionError(f"the script ran past {MAX_CLOCK_S:g} s")

    def _begin(self, name, n):
        if len(self.calls) >= MAX_CALLS:
            raise AssertionError(f"the script called CUDA or read MemAvailable more than {MAX_CALLS} times")
        self.calls.append((name, n, self.now))
        if self.context_at is None:
            self.context_at = self.now
        self.before(name, n)

    def _alloc(self, name, address):
        alloc = next((alloc for alloc in self.allocs if alloc["pointer"] == address), None)
        assert alloc is not None, f"{name} was given {address!r}, which no cudaMalloc gave"
        assert alloc["freed_at"] is None, f"{name} was given {address:#x}, already freed"
        return alloc

    def _cudaMemGetInfo(self, free_p, total_p):
        n = len(self.allocs)
        self._begin("cudaMemGetInfo", n)
        code = self.fail("cudaMemGetInfo", n)
        if code:
            return code
        _target(free_p).value = max(self.cuda_free - sum(alloc["bytes"] for alloc in self.live), 0)
        _target(total_p).value = CUDA_TOTAL
        self.after("cudaMemGetInfo", n)
        return 0

    def _cudaMalloc(self, pointer_p, size):
        n = len(self.allocs) + 1
        if n > MAX_ALLOCS:
            raise AssertionError(f"the script asked for more than {MAX_ALLOCS} allocations")
        self._begin("cudaMalloc", n)
        code = self.fail("cudaMalloc", n)
        if code:
            return code
        pointer = 0x3_0000_0000 + n * 0x2000_0000
        self.allocs.append({"n": n, "pointer": pointer, "bytes": size, "at": self.now, "touched_at": None,
                            "freed_at": None})
        _target(pointer_p).value = pointer
        self.after("cudaMalloc", n)
        return 0

    def _cudaMemset(self, pointer, value, count):
        alloc = self._alloc("cudaMemset", _address(pointer))
        self._begin("cudaMemset", alloc["n"])
        code = self.fail("cudaMemset", alloc["n"])
        if code:
            return code
        assert count <= alloc["bytes"], f"cudaMemset of {count} bytes on an allocation of {alloc['bytes']}"
        self.memsets.append((alloc["pointer"], value, count))
        if count == alloc["bytes"] and alloc["touched_at"] is None:
            alloc["touched_at"] = self.now
        self.after("cudaMemset", alloc["n"])
        return 0

    def _cudaDeviceSynchronize(self):
        n = len(self.allocs)
        self._begin("cudaDeviceSynchronize", n)
        code = self.fail("cudaDeviceSynchronize", n)
        if code:
            return code
        self.after("cudaDeviceSynchronize", n)
        return 0

    def _cudaFree(self, pointer):
        alloc = self._alloc("cudaFree", _address(pointer))
        self._begin("cudaFree", alloc["n"])
        code = self.fail("cudaFree", alloc["n"])
        if code:
            return code
        alloc["freed_at"] = self.now
        self.after("cudaFree", alloc["n"])
        return 0

    def _cudaGetErrorString(self, code):
        return {OUT_OF_MEMORY: b"out of memory"}.get(code, b"a stand-in's error")


class Output:
    """A stand-in stdout or stderr: each line written, with how many pointers CUDA still held for the script as it was
    written. `fail_with`, once set, is raised by the next write, as a closed pipe would."""

    def __init__(self, box):
        self.box, self.lines, self.fail_with = box, [], None

    def write(self, text):
        if self.fail_with is not None:
            err, self.fail_with = self.fail_with, None
            raise err
        if text.strip():
            self.lines.append((text, len(self.box.live)))
        return len(text)

    def flush(self):
        pass

    @property
    def words(self):
        return "".join(text for text, _ in self.lines)


def run(cc, box, *argv):
    return cc.main(list(argv), lib=box.lib, read_available=box.read, sleep=box.sleep, watchdog=box.watchdog)


def measure(cc, box, *, pause_s=2.5):
    return cc.measure(cc.Cuda(box.lib), read_available=box.read, reserve_gib=24.0, step_gib=0.25, pause_s=pause_s,
                      sleep=box.sleep)


def cuda_calls(box, *names):
    return [name for name, _, _ in box.calls if name in names]


@pytest.mark.parametrize("lag_s", [0.0, 1.0], ids=["at-once", "a-second-late"])
def test_it_stops_when_memavailable_would_pass_the_reserve(cc, monkeypatch, lag_s):
    # A step that shows a second late is waited for (re-read every 250 ms), not taken for one that never showed.
    box = Box(117.0, lag_s=lag_s)
    result = measure(cc, box)
    assert (result.allocated_gib, result.stopped) == (93.0, "reserve")
    assert len(box.allocs) == 372 and all(alloc["bytes"] == STEP_BYTES for alloc in box.allocs)
    assert box.reads[-1][1] == 24.0  # it stopped once MemAvailable, less a step, would pass under 24
    assert box.live == [] and box.available_at(box.now) == 117.0  # and freed every step
    # Paced: each step at least --pause-s after the last.
    times = [alloc["at"] for alloc in box.allocs]
    assert min(b - a for a, b in zip(times, times[1:])) >= 2.5
    # Through main: exit 0, every line its own, and the last says what it measured. It is written while CUDA still
    # holds every step, so a signal during the free that follows can't lose it.
    box = Box(117.0, lag_s=lag_s)
    holding = []
    box.after = lambda name, n: holding.append(box.watchdog.started[0][1]()) if (name, n) == ("cudaMemset", 5) else 0
    out, err = Output(box), Output(box)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    assert run(cc, box) == 0
    assert len(box.allocs) == 372 and box.live == []
    lines = [text for text, _ in out.lines]
    assert err.lines == [] and all(line.startswith("cuda_ceiling: ") for line in lines)
    assert lines[1].endswith(f"Ctrl-C stops it and frees everything; a CUDA call that hangs ignores Ctrl-C, and "
                             f"kill -KILL {os.getpid()} from another pane ends it, and the driver frees CUDA's memory "
                             "as the process exits.")
    assert ("Each line's shortfall is what CUDA holds less MemAvailable's fall since the start: it may be 10% of what "
            "CUDA holds, at least 0.1 GiB and at most 1 GiB, and over that, 5 s after a step, it stops.") in lines[1]
    assert lines[2] == ("cuda_ceiling: CUDA holds 0.25 GiB; MemAvailable 116.75 GiB, a shortfall of 0.00 GiB; "
                        "cudaMemGetInfo's free 79.75 GiB.")
    last, held_then = out.lines[-1]
    assert "The CUDA-allocatable ceiling is at least 93.00 GiB" in last and held_then == 372
    assert "cudaMemGetInfo, with 93.00 GiB held: free 0.00 GiB" in last and "It frees everything now" in last
    # The watchdog ran beside it: started with the reserve, holding while CUDA held, and stopped as the run ended.
    assert [reserve for reserve, _ in box.watchdog.started] == [24.0] and box.watchdog.stops == 1
    assert holding == [True] and box.watchdog.started[0][1]() is False


@pytest.mark.parametrize("shows, steps", [
    (lambda n: 0.0 if n == 3 else 1.0, 3),
    (lambda n: 0.2 if n == 3 else 1.0, 3),
    (lambda n: 0.55 if n == 1 else 1.0, 1),
    (lambda n: 0.0 if n == 8 else 1.0, 8),
], ids=["flat", "a-fifth", "the-first-just-over-the-floor", "at-2-gib"])
def test_a_step_that_doesnt_show_stops_it(cc, capsys, shows, steps):
    # After the third step MemAvailable doesn't fall (flat), or falls a fifth of a step: 0.20 GiB short with 0.75 held,
    # over the 0.1 GiB allowed; the first step shows 55% of itself, 0.11 GiB short, just over that; or, with 2 GiB held,
    # 0.25 GiB short, over the 10% allowed there. Each time it re-reads for 5 s, then frees every pointer and stops.
    box = Box(117.0, shows=shows)
    result = measure(cc, box)
    assert result.stopped == "not_reflected" and result.allocated_gib == steps * 0.25
    assert len(box.allocs) == steps and box.live == []
    last_at, freed_at = box.allocs[-1]["at"], {alloc["freed_at"] for alloc in box.allocs}
    assert len(freed_at) == 1 and freed_at.pop() - last_at == pytest.approx(5.0)  # it waited the 5 s, no more
    box = Box(117.0, shows=shows)
    assert run(cc, box) == 3
    assert len(box.allocs) == steps and box.live == []
    words = capsys.readouterr().err
    assert f"short of the {steps * 0.25:.2f} GiB CUDA held, 5 s after its last step" in words
    assert "it allows 10% of what CUDA holds, at least 0.1 GiB and at most 1 GiB" in words
    assert "isn't following what CUDA takes" in words and "This run measures nothing: " + SECOND_STOP in words


def test_a_small_shortfall_at_the_start_is_only_noise(cc):
    # 10% of a first step is 0.025 GiB, tighter than MemAvailable's own noise (a 0.029 GiB rise over 5 s, at the
    # script's re-review), so the allowed shortfall never goes under 0.1 GiB (the controller's ruling, 2026-11-09). A
    # first step that shows 89% of itself, 0.0275 GiB short, is noise, and the run goes on to the reserve.
    box = Box(30.0, shows=lambda n: 0.89 if n == 1 else 1.0)
    result = measure(cc, box)
    assert (result.stopped, len(box.allocs)) == ("reserve", 24) and box.live == []
    assert box.now == pytest.approx(23 * 2.5 + 2.5)  # and it never waited for a step to show


def test_a_shortfall_over_1_gib_stops_it(cc, capsys):
    # The 90% alone grows loose as CUDA holds more: 10% of 20 GiB is 2 GiB. So the shortfall, what CUDA holds less
    # MemAvailable's fall since the start, is capped at 1 GiB too (the controller's ruling, 2026-10-16). From step 81
    # no step shows: the shortfall is 1.00 after step 84, still allowed, and 1.25 after step 85, which stops it. The
    # 90% alone would go on to step 89.
    box = Box(117.0, shows=lambda n: 0.0 if n > 80 else 1.0)
    result = measure(cc, box)
    assert result.stopped == "not_reflected" and result.allocated_gib == 21.25
    assert len(box.allocs) == 85 and box.live == []
    out, err = capsys.readouterr()
    assert "CUDA holds 21.00 GiB; MemAvailable 97.00 GiB, a shortfall of 1.00 GiB;" in out
    assert "MemAvailable fell 20.00 GiB since the start, 1.25 GiB short of the 21.25 GiB CUDA held" in err


def test_cudas_own_context_isnt_taken_for_a_step(cc):
    # The first CUDA call starts CUDA, whose context takes memory of its own (about 0.24 GiB on the box, 2026-10-16), so
    # MemAvailable's start is read after cudaMemGetInfo. A first step that doesn't show is then caught, not covered by
    # the context's fall: 0.24 would pass for 90% of a 0.25 GiB step.
    box = Box(117.0, context_gib=0.24, shows=lambda n: 0.0 if n == 1 else 1.0)
    result = measure(cc, box)
    assert result.stopped == "not_reflected" and len(box.allocs) == 1 and box.live == []
    assert [name for name, _, _ in box.calls[:2]] == ["cudaMemGetInfo", "read"]


def test_its_pace_stays_under_the_rate_watch(cc):
    registry = load_registry(STACK_REGISTRY)
    poll_s = registry.brake.poll_ms / 1000

    def watched(pause_s=None):
        """From 117 down to the reserve, 24, through main at its default step and pause, or with `pause_s`, through
        measure (main refuses a faster pace): each of the brake's readings every poll, through Task 23's predict_breach,
        keeping the readings the brake keeps (twice its window). Returns whether each poll would have fired, and the
        readings."""
        box = Box(117.0)
        if pause_s is None:
            assert run(cc, box) == 0
        else:
            assert measure(cc, box, pause_s=pause_s).stopped == "reserve"
        assert box.reads[0] == (0.0, 117.0) and len(box.allocs) == 372  # from 117 all the way down to 24
        readings, fired, seen = deque(), [], []
        end = max(alloc["at"] for alloc in box.allocs) + 2 * brake.FALL_WINDOW_S
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
    # The same watch fires for a script fifty times as fast, 5 GiB/s: these readings are ones the brake would act on.
    fired, _ = watched(pause_s=0.05)
    assert any(fired)


def test_it_touches_every_step(cc):
    # cudaMemset once per allocation, over all of it, then a synchronize, before the next step is asked for.
    box = Box(30.0)
    result = measure(cc, box)
    assert result.stopped == "reserve" and len(box.allocs) == 24
    assert [(pointer, count) for pointer, _, count in box.memsets] == [(alloc["pointer"], alloc["bytes"])
                                                                       for alloc in box.allocs]
    assert cuda_calls(box, "cudaMalloc", "cudaMemset", "cudaDeviceSynchronize") == [
        "cudaMalloc", "cudaMemset", "cudaDeviceSynchronize"] * 24


@pytest.mark.parametrize("way", ["exception", "meminfo-unreadable", "broken-pipe", "ctrl-c", "sighup", "sigterm",
                                 "ctrl-z"])
def test_it_frees_everything_on_any_way_out(cc, monkeypatch, way):
    def at_step_5(name, n):
        if (name, n) != ("cudaMemset", 5):
            return
        if way == "exception":
            raise RuntimeError("a stand-in's failure")
        if way == "meminfo-unreadable":
            box.read_error = OSError("/proc/meminfo: a stand-in's unreadable file")
            return
        if way == "broken-pipe":
            out.fail_with = BrokenPipeError(32, "Broken pipe")  # the step's line, to a pipe that has closed
            return
        if way == "ctrl-c":
            raise KeyboardInterrupt
        os.kill(os.getpid(), SIGNALS[way])

    # The test's own handlers only note a signal that reaches them: a script that doesn't take a signal for itself runs
    # on to the reserve and exits 0, and the test fails, never pytest (which SIGTSTP would otherwise stop).
    reached_the_test = []

    def note(signum, frame):
        reached_the_test.append(signum)

    box = Box(117.0, after=at_step_5)
    out, err = Output(box), Output(box)
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    before = {signum: signal.signal(signum, note) for signum in SIGNALS.values()}
    try:
        if way == "exception":
            with pytest.raises(RuntimeError):
                run(cc, box)
        else:
            # 1 for an error outside CUDA; 129, 143, and 148 for Ctrl-Z on Linux
            code = {"meminfo-unreadable": 1, "broken-pipe": 1, "ctrl-c": 130}.get(way) or 128 + SIGNALS[way]
            assert run(cc, box) == code
        after = {signum: signal.getsignal(signum) for signum in SIGNALS.values()}
    finally:
        for signum, handler in before.items():
            signal.signal(signum, handler)
    assert len(box.allocs) == 5 and box.live == []  # cudaFree for each pointer taken, once (the stand-in checks)
    # The script took the signal itself, then put the test's handlers back, and stopped the watchdog.
    assert reached_the_test == [] and all(handler is note for handler in after.values())
    assert box.watchdog.stops == 1
    # Its words say it freed everything, and by the time they are written it has.
    assert (way == "exception") == (err.lines == []) and all(held == 0 for _, held in err.lines)
    words = err.words
    if way == "meminfo-unreadable":
        assert ("couldn't read MemAvailable, its guard, so it freed everything CUDA held and stopped: /proc/meminfo: "
                "a stand-in's unreadable file. Check that /proc/meminfo reads (grep MemAvailable /proc/meminfo), "
                "then run it again.") in words
    if way == "broken-pipe":  # an error that isn't the reading's names itself, and doesn't blame /proc/meminfo
        assert ("stopped on an error, and freed everything CUDA held: BrokenPipeError: [Errno 32] Broken pipe. Run "
                "it again once that's put right.") in words
        assert "meminfo" not in words
    if way in ("ctrl-c", "sighup", "sigterm"):
        name = "Ctrl-C" if way == "ctrl-c" else way.upper()
        assert (f"stopped by {name}, and freed everything CUDA held. Unless a line above gives the ceiling, run it "
                "again to measure.") in words
    if way == "ctrl-z":  # it ends the script, never pauses it holding memory with nothing watching the reserve
        assert "Ctrl-Z ends this script rather than pausing it" in words and "freed everything CUDA held" in words
        stopped = 128 + signal.SIGTSTP  # 148 on Linux
        assert f"Under uv run, uv itself is left stopped: fg lets it exit ({stopped}), or kill %1 ends it" in words


def test_a_signal_as_the_watchdog_stops_changes_nothing(cc):
    # main's finally ignores the signals first thing, before it stops the watchdog, whose join can take a moment. After
    # an error the finally's free is the only free, and a signal then would cut it short, every pointer unfreed.
    def at_step_5(name, n):
        if (name, n) == ("cudaMemset", 5):
            raise RuntimeError("a stand-in's failure")

    class SignalledAsItStops(StandInWatchdog):
        def stop(self):
            super().stop()
            os.kill(os.getpid(), signal.SIGTERM)

    reached_the_test = []

    def note(signum, frame):
        reached_the_test.append(signum)

    box = Box(117.0, after=at_step_5)
    box.watchdog = SignalledAsItStops()
    before = {signum: signal.signal(signum, note) for signum in SIGNALS.values()}
    try:
        try:
            run(cc, box)
        except RuntimeError:
            pass
        except BaseException as escaped:
            pytest.fail(f"{type(escaped).__name__} escaped the script, as the watchdog stopped")
        else:
            pytest.fail("the stand-in's failure didn't reach the test")
    finally:
        for signum, handler in before.items():
            signal.signal(signum, handler)
    assert box.watchdog.stops == 1 and reached_the_test == []
    assert len(box.allocs) == 5 and box.live == []


def second_run(given, free):
    return (f"CUDA gave {given} GiB and refused more with {free} GiB free by its own count, so this isn't the page "
            f"cache's signature: Task 41's Step 4 asks for a second run before it records {given} GiB.")


NOTHING_GIVEN = f"CUDA refused the first step, with nothing allocated, so this run measures nothing. {DANS_CALL}"


def _fail_at(call, n, code, *, memgetinfo_after=False):
    """A stand-in's `fail`: `call` at step `n` returns `code`; with `memgetinfo_after`, so does every cudaMemGetInfo
    after it."""
    failed = []

    def fail(name, at):
        if (name, at) == (call, n):
            failed.append(name)
            return code
        if memgetinfo_after and failed and name == "cudaMemGetInfo":
            return code
        return 0

    return fail


@pytest.mark.parametrize("start, call, n, code, cuda_free_gib, memgetinfo_after, allocated, taken, verdict", [
    (117.0, "cudaMalloc", 10, OUT_OF_MEMORY, 80.0, False, 2.25, 9, second_run("2.25", "77.75")),
    (117.0, "cudaMalloc", 10, OUT_OF_MEMORY, 3.25, False, 2.25, 9, second_run("2.25", "1.00")),
    (117.0, "cudaMalloc", 1, OUT_OF_MEMORY, 80.0, False, 0.0, 0, NOTHING_GIVEN),
    (117.0, "cudaMalloc", 1, OUT_OF_MEMORY, 0.5, False, 0.0, 0, NOTHING_GIVEN),
    (26.5, "cudaMalloc", 10, OUT_OF_MEMORY, 3.0, False, 2.25, 9,
     "MemAvailable was still 0.25 GiB above the drills' floor, 24 GiB, but cudaMemGetInfo's free, which leaves out the "
     "page cache, was 0.75 GiB: CUDA didn't take the memory the page cache held, so this isn't the ceiling. "
     + DANS_CALL),
    (117.0, "cudaMalloc", 10, OUT_OF_MEMORY, 3.0, False, 2.25, 9,
     "MemAvailable was still 90.75 GiB above the drills' floor, 24 GiB, but cudaMemGetInfo's free, which leaves out the "
     "page cache, was 0.75 GiB: CUDA didn't take the memory the page cache held, so this isn't the ceiling. "
     + DANS_CALL),
    (117.0, "cudaMalloc", 10, OUT_OF_MEMORY, 80.0, True, 2.25, 9,
     "cudaMemGetInfo didn't answer after it, so it can't tell CUDA's ceiling from the page cache. " + DANS_CALL),
    (117.0, "cudaMalloc", 10, INVALID_VALUE, 80.0, False, 2.25, 9,
     "Only cudaMalloc's out of memory can be a ceiling, so this run measures nothing. " + DANS_CALL),
    (117.0, "cudaMemset", 10, OUT_OF_MEMORY, 80.0, False, 2.25, 10,
     "Only cudaMalloc's out of memory can be a ceiling, so this run measures nothing. " + DANS_CALL),
    (117.0, "cudaDeviceSynchronize", 10, OUT_OF_MEMORY, 80.0, False, 2.25, 10,
     "Only cudaMalloc's out of memory can be a ceiling, so this run measures nothing. " + DANS_CALL),
    (117.0, "cudaMemGetInfo", 10, OUT_OF_MEMORY, 80.0, False, 2.5, 10,
     "Only cudaMalloc's out of memory can be a ceiling, so this run measures nothing. " + DANS_CALL),
    (117.0, "cudaMemGetInfo", 0, 100, 80.0, False, 0.0, 0,
     "Nothing allocated, nothing measured. Check that the GPU answers (llama-server --list-devices, from the folder "
     "--lib is in); this needs Dan's decision before any rerun (Task 41, Step 4)."),
], ids=["malloc-a-second-run", "malloc-a-second-run-at-1-gib-free", "malloc-nothing-allocated",
        "malloc-nothing-allocated-under-1-gib-free", "malloc-the-page-cache-a-step-above-the-reserve",
        "malloc-the-page-cache", "malloc-memgetinfo-silent", "malloc-another-error", "memset", "synchronize",
        "memgetinfo-after-a-step", "memgetinfo-first"])
def test_it_stops_at_the_first_cuda_error(cc, capsys, start, call, n, code, cuda_free_gib, memgetinfo_after,
                                          allocated, taken, verdict):
    # It stops at the first CUDA error, frees everything and exits 4. Only cudaMalloc's out of memory can be a ceiling,
    # and the script can't know that it is one (the controller's rulings, 2026-11-09): with nothing allocated it
    # measures nothing; with cudaMemGetInfo's free, which leaves out the page cache, under 1 GiB it is the page cache's
    # signature; otherwise that signature is absent, and Task 41's Step 4 wants a second run. cudaMalloc runs only with
    # MemAvailable at least a step above the reserve, so a run that gets there exactly, 26.5 less 2.25, is the page
    # cache's too. A failed cudaMemset or synchronize leaves step 10 untouched, and its pointer is freed too; a
    # cudaMemGetInfo that fails after step 10, which was touched, counts it. One failing first measures nothing.
    def a_box():
        return Box(start, cuda_free=int(cuda_free_gib * GIB), fail=_fail_at(call, n, code,
                                                                          memgetinfo_after=memgetinfo_after))

    box = a_box()
    result = measure(cc, box)
    assert result.stopped == "cuda_error" and result.allocated_gib == allocated
    assert len(box.allocs) == taken and box.live == []
    failed_at = next(i for i, (name, at, _) in enumerate(box.calls) if (name, at) == (call, n))
    after_it = {name for name, _, _ in box.calls[failed_at + 1:]}
    assert not after_it & {"cudaMalloc", "cudaMemset", "cudaDeviceSynchronize"}  # nothing asked for or touched after
    words = {OUT_OF_MEMORY: "out of memory"}.get(code, "a stand-in's error")
    assert f"{call} failed: {words} (CUDA error {code})" in result.detail and result.detail.endswith(verdict)
    assert ("freed everything" in result.detail) == bool(taken)
    capsys.readouterr()
    box = a_box()
    assert run(cc, box) == 4
    assert len(box.allocs) == taken and box.live == []
    assert verdict in capsys.readouterr().err


def test_it_reads_memgetinfo_before_allocating(cc, capsys):
    # The first line, printed before any cudaMalloc, holds cudaMemGetInfo's free and total with nothing allocated.
    printed_by_the_first_malloc = []

    def before(name, n):
        if name == "cudaMalloc" and not printed_by_the_first_malloc:
            printed_by_the_first_malloc.append(capsys.readouterr().out)

    box = Box(30.0, cuda_free=10_989_010_944, before=before)
    assert run(cc, box) == 0
    first = printed_by_the_first_malloc[0].splitlines()[0]
    assert first == "cuda_ceiling: cudaMemGetInfo, with nothing allocated: free 10.23 GiB, total 121.63 GiB."
    assert box.calls[0][:2] == ("cudaMemGetInfo", 0)


@pytest.mark.parametrize("argv, named", [
    (["--reserve", "20"], "--reserve 20 is under 24 GiB, the drills' own floor"),
    (["--reserve", "23.9"], "--reserve 23.9 is under 24 GiB, the drills' own floor"),
], ids=["20", "23.9"])
def test_a_reserve_under_24_is_refused(cc, capsys, argv, named):
    # The reserve can only be raised, as the hog's floor (the controller's ruling, 2026-10-16): nothing loaded, read or
    # allocated, and the words say what to do instead.
    box = Box(117.0)
    assert run(cc, box, *argv) == 2
    assert box.calls == [] and box.reads == [] and box.allocs == [] and box.watchdog.started == []
    words = capsys.readouterr().err
    assert named in words and "nothing allocated" in words and "Leave --reserve out, or give 24 or more" in words
    # A raised reserve runs, stops there, and is named as the one given, to the watchdog too.
    box = Box(32.0)
    assert run(cc, box, "--reserve", "30") == 0
    assert len(box.allocs) == 8 and box.live == [] and [r for r, _ in box.watchdog.started] == [30.0]
    out = capsys.readouterr().out
    assert "the reserve you set, --reserve 30 GiB" in out and "the drills' own floor" not in out


@pytest.mark.parametrize("argv, named", [
    (["--step-gib", "0.5"], ["--step-gib 0.5 is a bigger step than 0.25 GiB"]),
    (["--pause-s", "2"], ["--pause-s 2 is a shorter pause than 2.5 s"]),
    (["--step-gib", "0.3", "--pause-s", "1"], ["--step-gib 0.3 is a bigger step", "--pause-s 1 a shorter pause"]),
    (["--step-gib", "0.5", "--pause-s", "10"], ["--step-gib 0.5 is a bigger step than 0.25 GiB"]),
], ids=["bigger-steps", "shorter-pauses", "both", "bigger-steps-longer-pauses"])
def test_a_faster_pace_is_refused(cc, capsys, argv, named):
    # The pace can only be slowed, as the hog's (the controller's ruling, 2026-10-16): a bigger step or a shorter pause
    # than the one the pace test checks, even one slower on average (0.5 GiB every 10 s), which the words mustn't call
    # faster.
    box = Box(117.0)
    assert run(cc, box, *argv) == 2
    assert box.calls == [] and box.reads == [] and box.allocs == []
    words = capsys.readouterr().err
    assert "rate watch" in words and "nothing allocated" in words and "faster than" not in words
    assert all(part in words for part in named)
    # A slower one runs.
    box = Box(26.0)
    assert run(cc, box, "--step-gib", "0.125", "--pause-s", "5") == 0
    assert len(box.allocs) == 16 and box.live == [] and all(alloc["bytes"] == GIB // 8 for alloc in box.allocs)


@pytest.mark.parametrize("drop_to", [23.0, 23.99])
@pytest.mark.parametrize("case", ["after-a-step", "in-a-pause", "while-a-step-shows"])
def test_a_fall_under_the_reserve_while_it_holds_frees_everything(cc, capsys, case, drop_to):
    # From 30 the reserve, 24, is 24 steps away, 2.5 s apart. Something else takes memory, and MemAvailable reads 23,
    # or 23.99, a hundredth under the reserve: as the third step is touched, at 5 s, so the reading right after that
    # step sees it at once; at 6.1 s, in the pause after it; or, with each step showing a second late, at 7.6 s, while
    # the script waits for the third step (taken at 7 s) to show. The last two are off any slice's grid, so only
    # readings 250 ms apart see them within 250 ms.
    def a_box():
        box = Box(30.0, lag_s=1.0 if case == "while-a-step-shows" else 0.0,
                  drop_at={"in-a-pause": 6.1, "while-a-step-shows": 7.6}.get(case), drop_to=drop_to)
        if case == "after-a-step":
            def drop_now(name, n):
                if (name, n) == ("cudaMemset", 3):
                    box.drop_at = box.now
            box.after = drop_now
        return box

    box = a_box()
    assert run(cc, box) == 3
    assert len(box.allocs) == 3 and box.live == []
    first_under = next(at for at, gib in box.reads if gib < 24)
    assert first_under - box.drop_at <= 0.25  # read within 250 ms of the fall, through every pause and wait
    if case == "after-a-step":
        assert first_under == box.drop_at  # read right after the step, before any pause
    assert {alloc["freed_at"] for alloc in box.allocs} == {first_under}  # and freed at once: no pause, no read first
    assert box.now == first_under and box.reads[-1][1] == drop_to
    # Freed before anything else: the call right after that reading is a cudaFree, not a cudaMemGetInfo, and no step
    # line is printed for it.
    k = next(i for i, (_, gib) in enumerate(box.reads) if gib < 24)  # the k-th reading is the k-th "read" call
    under_at = [i for i, (name, _, _) in enumerate(box.calls) if name == "read"][k]
    assert box.calls[under_at + 1][0] == "cudaFree"
    out, err = capsys.readouterr()
    assert f"MemAvailable {drop_to:.2f} GiB, a shortfall" not in out
    last_fall = 29.5 - drop_to  # since the reading before the third step, 29.5
    assert (f"MemAvailable read {drop_to:.2f} GiB, under the drills' floor, 24 GiB, while CUDA held 0.75 GiB, so it freed "
            f"everything at once and stopped. Since just before its last step MemAvailable fell {last_fall:.2f} GiB, "
            f"{last_fall - 0.25:.2f} GiB more than the step's 0.25 GiB: something else took memory meanwhile.") in err
    assert "This run measures nothing: " + SECOND_STOP in err
    box = a_box()
    result = measure(cc, box)
    assert result.stopped == "under_reserve" and result.allocated_gib == 0.75
    assert box.live == [] and {alloc["freed_at"] for alloc in box.allocs} == {box.now}  # freed by measure itself


def test_a_fall_under_the_reserve_while_a_step_hasnt_shown_frees_at_once(cc):
    # A reading under the reserve ends the wait for a step to show at once, even when the step still hasn't shown. From
    # 41: steps 1 to 40 show, 41 to 44 don't (a shortfall of 1.00 GiB, the most allowed), and the rest show, so step 72,
    # at 177.5 s, leaves 18 GiB held and MemAvailable at 24, within a step of the reserve. But something else frees
    # 0.5 GiB at 179 s, in the pause, so step 73 is taken at 180 s: a shortfall of 1.50 GiB, which it waits to see
    # shrink. At 180.6 s something takes memory: MemAvailable reads 23.9, under the reserve, with a shortfall of 1.15
    # GiB, still not shown. It frees everything at that reading, not 5 s later at the wait's end.
    box = Box(41.0, shows=lambda n: 0.0 if 41 <= n <= 44 else 1.0, others=lambda at: 0.5 if at >= 179.0 else 0.0,
              drop_at=180.6, drop_to=23.9)
    result = measure(cc, box)
    assert result.stopped == "under_reserve" and len(box.allocs) == 73 and box.allocs[-1]["at"] == 180.0
    first_under = next(at for at, gib in box.reads if gib < 24)
    assert first_under == 180.75 and 18.25 - (41.0 - 23.9) > 1.0  # under the reserve, and the step not yet shown
    assert box.live == [] and {alloc["freed_at"] for alloc in box.allocs} == {first_under} and box.now == first_under


@pytest.mark.parametrize("last_shows, own", [(1.08, True), (1.2, False)], ids=["its-own-step", "something-else"])
def test_under_the_reserve_it_says_whose_fall_it_was(cc, capsys, last_shows, own):
    # Each step here falls 0.27 GiB, a little more than its 0.25, as steps can on the box (254 to 259 MiB for 256 MiB,
    # 2026-10-16). From 29.66, after 20 steps MemAvailable is 24.26, a step and a hundredth above the reserve, so step
    # 21 is taken. If it falls 0.27 too, it takes MemAvailable under the reserve by itself: within a tenth of a step,
    # its own. If it falls 0.30, more than that, something else took memory. Over the whole run MemAvailable fell more
    # than a step beyond what CUDA held either way, so only the last step's own fall tells them apart.
    box = Box(29.66, shows=lambda n: 1.08 if n < 21 else last_shows)
    result = measure(cc, box)
    assert result.stopped == "under_reserve" and len(box.allocs) == 21 and box.live == []
    assert (29.66 - box.reads[-1][1]) - result.allocated_gib > 0.25
    words = capsys.readouterr().err
    if own:
        assert ("Since just before its last step MemAvailable fell 0.27 GiB, within a tenth of the step's 0.25 GiB: "
                "its own last step took it under.") in words
        assert "something else" not in words
    else:
        assert ("Since just before its last step MemAvailable fell 0.30 GiB, 0.05 GiB more than the step's 0.25 GiB: "
                "something else took memory meanwhile.") in words
        assert "its own last step" not in words


@pytest.mark.parametrize("start, words", [
    (23.5, "MemAvailable is 23.50 GiB, already under the drills' floor, 24 GiB; nothing allocated, nothing measured"),
    (24.0, "MemAvailable is 24.00 GiB, within a step of the drills' floor, 24 GiB; nothing allocated, nothing measured"),
    (24.2, "MemAvailable is 24.20 GiB, within a step of the drills' floor, 24 GiB; nothing allocated, nothing measured"),
], ids=["under", "at", "within-a-step"])
def test_nothing_is_allocated_within_a_step_of_the_reserve(cc, capsys, start, words):
    # A box that isn't clear measures nothing: it stops (exit 3) and says to clear it first.
    box = Box(start)
    assert run(cc, box) == 3
    assert box.allocs == []
    err = capsys.readouterr().err
    assert words in err and "spark make-room --all" in err


def test_its_help_lists_every_exit_code(cc, capsys):
    with pytest.raises(SystemExit) as stop:
        cc.main(["--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    for code in (0, 1, 2, 3, 4, 128 + signal.SIGHUP, 130, 128 + signal.SIGTERM, 128 + signal.SIGTSTP):
        assert re.search(rf"^\s+{code}\s", out, re.MULTILINE), code
    words = " ".join(out.split())  # argparse wraps the options' help
    assert "uv itself stays stopped: fg lets it exit" in words and "kill %1" in words
    assert "It can only be raised" in words and "it can only be made smaller" in words
    assert "it can only be made longer" in words
    assert "a --reserve under 24, or a bigger step or a shorter pause" in words and "faster" not in words
    assert "or a cudaFree that failed" in words and "the watchdog's too" in words
    assert "Only an out-of-memory cudaMalloc can be a ceiling, and only once a second run agrees" in words


@pytest.mark.parametrize("second", ["sigint", "sighup", "sigterm"])
@pytest.mark.parametrize("first", ["ctrl-c", "sighup", "sigterm"])
def test_a_second_signal_while_it_frees_changes_nothing(cc, capsys, first, second):
    # Under uv run, a signal to the process group reaches the script twice: uv forwards SIGINT, SIGHUP and SIGTERM to
    # it. The second lands while it frees, and must change nothing: the first signal's exit code and words, no exception
    # out of main, every pointer freed.
    signals = {"sigint": signal.SIGINT, "sighup": signal.SIGHUP, "sigterm": signal.SIGTERM}
    sent = []

    def at_step_5(name, n):
        if (name, n) != ("cudaMemset", 5):
            return
        if first == "ctrl-c":
            raise KeyboardInterrupt  # what Python's own SIGINT handler raises
        os.kill(os.getpid(), signals[first])

    def at_the_first_free(name, n):
        if name == "cudaFree" and not sent:
            sent.append(second)
            os.kill(os.getpid(), signals[second])

    # The test's own handlers only note a signal that reaches them, so one the script doesn't ignore fails the test,
    # never pytest (a SIGINT would otherwise interrupt the whole run).
    reached_the_test = []

    def note(signum, frame):
        reached_the_test.append(signum)

    watched = (signal.SIGINT, *SIGNALS.values())
    before = {signum: signal.signal(signum, note) for signum in watched}
    try:
        box = Box(117.0, after=at_step_5, before=at_the_first_free)
        try:
            code = run(cc, box)
        except BaseException as escaped:  # a traceback's case
            pytest.fail(f"{type(escaped).__name__} escaped the script after a second signal")
        after = {signum: signal.getsignal(signum) for signum in watched}
    finally:
        for signum, handler in before.items():
            signal.signal(signum, handler)
    assert sent == [second] and reached_the_test == []
    assert code == {"ctrl-c": 130, "sighup": 128 + signal.SIGHUP, "sigterm": 128 + signal.SIGTERM}[first]
    words = capsys.readouterr().err
    assert f"stopped by {'Ctrl-C' if first == 'ctrl-c' else first.upper()}, and freed everything CUDA held" in words
    assert len(box.allocs) == 5 and box.live == []
    assert all(handler is note for handler in after.values())  # and it put every handler back, SIGINT's too


@pytest.mark.parametrize("where", ["in-cudaMalloc", "in-cudaFree"])
@pytest.mark.parametrize("way", ["ctrl-c", "sighup", "sigterm", "ctrl-z"])
def test_a_signal_inside_a_cuda_call_still_frees_its_pointer(cc, capsys, where, way):
    # A signal can be taken between a CUDA call and the script's record of it: after cudaMalloc has given its pointer
    # but before the script has noted it, or after a pointer has left the record but before cudaFree has freed it. The
    # stand-in sends it inside the call, at those moments: step 5's cudaMalloc, once its pointer is given, or the first
    # cudaFree as the run ends at the reserve, before it frees. Every pointer must still be freed, once; and a result
    # already reached is already on the screen.
    signum = signal.SIGINT if way == "ctrl-c" else SIGNALS[way]
    sent = []

    def send(name, n):
        if not sent:
            sent.append((name, n))
            os.kill(os.getpid(), signum)

    def in_malloc(name, n):
        if (name, n) == ("cudaMalloc", 5):
            send(name, n)

    def in_free(name, n):
        if name == "cudaFree":
            send(name, n)

    # SIGINT keeps Python's own handler, which raises KeyboardInterrupt; the others note a signal that reaches the
    # test.
    reached_the_test = []

    def note(signum, frame):
        reached_the_test.append(signum)

    sigint_before = signal.getsignal(signal.SIGINT)
    before = {signum: signal.signal(signum, note) for signum in SIGNALS.values()}
    try:
        box = Box(30.0, after=in_malloc) if where == "in-cudaMalloc" else Box(30.0, before=in_free)
        try:
            code = run(cc, box)
        except BaseException as escaped:
            pytest.fail(f"{type(escaped).__name__} escaped the script")
        after = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, *SIGNALS.values())}
    finally:
        for signum_, handler in before.items():
            signal.signal(signum_, handler)
    assert sent and reached_the_test == []
    assert code == 128 + signum  # 130 for Ctrl-C's SIGINT
    assert len(box.allocs) == (5 if where == "in-cudaMalloc" else 24) and box.live == []
    assert after[signal.SIGINT] is sigint_before
    assert all(after[signum_] is note for signum_ in SIGNALS.values())
    out, err = capsys.readouterr()
    assert "freed everything CUDA held" in err
    assert ("The CUDA-allocatable ceiling is at least 6.00 GiB" in out) == (where == "in-cudaFree")


def test_its_binding_is_the_one_checked_on_the_box(cc):
    # The argument and result types, as Task 41's one real check used them on the box (2026-10-16): sizes as size_t,
    # pointers as void*, cudaMalloc's out-pointer as void**, cudaGetErrorString's words as char*. Without them ctypes
    # passes a Python int as a C int, which would cut a 64-bit pointer short.
    box = Box(117.0)
    cc.Cuda(box.lib)
    size_p, void_pp = ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_void_p)
    expected = {
        "cudaMemGetInfo": ([size_p, size_p], ctypes.c_int),
        "cudaMalloc": ([void_pp, ctypes.c_size_t], ctypes.c_int),
        "cudaMemset": ([ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t], ctypes.c_int),
        "cudaDeviceSynchronize": ([], ctypes.c_int),
        "cudaFree": ([ctypes.c_void_p], ctypes.c_int),
        "cudaGetErrorString": ([ctypes.c_int], ctypes.c_char_p),
    }
    bound = {name: (list(getattr(box.lib, name).argtypes), getattr(box.lib, name).restype) for name in expected}
    assert bound == expected
    assert box.calls == []  # binding calls nothing


def test_a_library_it_cant_use_is_an_error(cc, capsys, monkeypatch):
    # One that isn't CUDA's runtime: exit 1, nothing read or allocated, and the words say what --lib wants.
    box = Box(117.0)
    del box.lib.cudaMalloc
    assert run(cc, box, "--lib", "/somewhere/libother.so") == 1
    assert box.calls == [] and box.reads == [] and box.watchdog.started == []
    words = capsys.readouterr().err
    assert "--lib /somewhere/libother.so isn't usable: it has no cudaMalloc, so it isn't CUDA's runtime" in words
    assert "Nothing allocated" in words and "libcudart.so.13" in words

    # One that doesn't load.
    def no_such_file(path):
        raise OSError(f"{path}: cannot open shared object file: No such file or directory")

    monkeypatch.setattr(cc, "load_lib", no_such_file)
    box = Box(117.0)
    assert cc.main(["--lib", "/nowhere/libcudart.so.13"], read_available=box.read, sleep=box.sleep,
                   watchdog=box.watchdog) == 1
    assert box.reads == []
    assert "--lib /nowhere/libcudart.so.13 isn't usable: /nowhere/libcudart.so.13: cannot open" in (
        capsys.readouterr().err)


def test_a_cudafree_that_fails_is_reported(cc, capsys):
    # A pointer cudaFree refuses is said once, with what to do, and the run exits 1, an error; every other pointer is
    # still freed, by measure's own free, not only main's.
    def fail(name, n):
        return INVALID_VALUE if (name, n) == ("cudaFree", 3) else 0

    box = Box(30.0, fail=fail)
    result = measure(cc, box)
    assert result.stopped == "reserve" and [alloc["n"] for alloc in box.live] == [3]
    capsys.readouterr()
    box = Box(30.0, fail=fail)
    assert run(cc, box) == 1
    assert [alloc["n"] for alloc in box.live] == [3]
    out, err = capsys.readouterr()
    assert "The CUDA-allocatable ceiling is at least 6.00 GiB" in out
    assert err.count("cudaFree failed for 1 of the pointers CUDA held") == 1
    assert "a stand-in's error (CUDA error 1)" in err and "grep MemAvailable /proc/meminfo" in err


def test_its_reader_reads_memavailable(monkeypatch, tmp_path):
    # The script's own reader, the guard every stop rests on, unpatched, against a meminfo in the box's own format.
    module = load_script(monkeypatch)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(MEMINFO)
    assert module.read_available_gib(meminfo) == 51839984 / 1024**2  # MemAvailable, kB to GiB, not MemFree's
    assert module.MEMINFO == Path("/proc/meminfo")
    meminfo.write_text(MEMINFO.replace("MemAvailable:   51839984 kB\n", ""))
    with pytest.raises(OSError, match="has no MemAvailable line"):
        module.read_available_gib(meminfo)


class Clock:
    """The watchdog's stand-in clock: it moves only as the watchdog sleeps, and stops a watchdog that sleeps past
    `stop_at`, or fails one that runs past MAX_CLOCK_S."""

    def __init__(self, *, stop_at=None):
        self.now, self.stop_at, self.watchdog = 0.0, stop_at, None

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds
        if self.stop_at is not None and self.now >= self.stop_at:
            self.watchdog.stop()
        if self.now > MAX_CLOCK_S:
            raise AssertionError(f"the watchdog ran past {MAX_CLOCK_S:g} s")


def a_watchdog(cc, clock, readings, *, holding=True):
    """The script's own watchdog, run in the test's thread, with a stand-in reader (`readings(now)`), clock, exit and
    words. Returns it, with what it read, the exits it asked for and what it said."""
    seen, exits, said = [], [], []

    def read():
        seen.append((clock.now, readings(clock.now)))
        return seen[-1][1]

    watchdog = cc.Watchdog(read, reserve_gib=24.0, holding=lambda: holding, sleep=clock.sleep, exit=exits.append,
                           write=said.append)
    clock.watchdog = watchdog
    return watchdog, seen, exits, said


@pytest.mark.parametrize("drop_at", [0.0, 6.1, 9.99])
def test_the_watchdog_ends_the_process_within_250_ms_of_a_drop(cc, drop_at):
    # A thread of its own reads MemAvailable every 250 ms, whatever the main thread is doing (blocked on a paused
    # terminal, or in a CUDA call that hangs), and on a reading under the reserve while CUDA holds memory, ends the
    # process at once, exit 3, after one line of words (the controller's ruling, 2026-10-16).
    clock = Clock()
    watchdog, seen, exits, said = a_watchdog(cc, clock, lambda now: 23.99 if now >= drop_at else 30.0)
    watchdog.run()
    first_under = next(at for at, gib in seen if gib < 24)
    assert first_under - drop_at <= 0.25 and exits == [3] and clock.now == first_under
    assert said == ["cuda_ceiling: the watchdog read MemAvailable 23.99 GiB, under the drills' floor, 24 GiB, while CUDA "
                    "held memory, so it ended the process at once (exit 3); a process's exit frees its CUDA memory. "
                    "The main thread's words were skipped. If a result line is printed above, it stands; if not, this "
                    "run measures nothing. Check that MemAvailable came back (grep MemAvailable /proc/meminfo), and "
                    "what else is using memory (spark status for the models, top -o %MEM for the rest).\n"]


@pytest.mark.parametrize("holding", [True, False], ids=["holding", "nothing-held"])
def test_the_watchdog_never_fires_above_the_reserve_or_with_nothing_held(cc, holding):
    # At the reserve, or above it, it never fires; and with nothing held, a reading under the reserve is the main
    # thread's to say (there is nothing of CUDA's to free). It runs until stopped.
    clock = Clock(stop_at=100.0)
    readings = (lambda now: 24.0 if int(now * 4) % 2 else 30.0) if holding else (lambda now: 20.0)
    watchdog, seen, exits, said = a_watchdog(cc, clock, readings, holding=holding)
    watchdog.run()
    assert exits == [] and said == [] and clock.now == 100.0 and len(seen) == 400
    assert max(b - a for (a, _), (b, _) in zip(seen, seen[1:])) == 0.25


def test_the_watchdog_ends_the_process_even_if_its_words_fail(cc):
    # Its one line is best effort: a writer thread that can't start mustn't leave the watchdog dead and the process
    # alive, so the exit runs whatever the words do.
    clock, exits = Clock(), []

    def no_words(text):
        raise RuntimeError("can't start new thread")

    watchdog = cc.Watchdog(lambda: 23.99, reserve_gib=24.0, holding=lambda: True, sleep=clock.sleep, exit=exits.append,
                           write=no_words)
    with pytest.raises(RuntimeError):  # a real exit never returns, so a stand-in's lets the failure show
        watchdog.run()
    assert exits == [3]


def test_the_watchdog_outlives_a_reading_that_fails(cc):
    # A reading that raises is skipped, not fatal: the watchdog goes on, and the next reading under the reserve fires.
    def readings(now):
        if now < 0.5:
            raise OSError("/proc/meminfo: a stand-in's unreadable file")
        return 23.99

    clock = Clock()
    watchdog, seen, exits, said = a_watchdog(cc, clock, readings)
    watchdog.run()
    assert exits == [3] and clock.now == 0.5 and seen == [(0.5, 23.99)] and len(said) == 1


def test_the_watchdog_stops_when_the_run_ends(cc):
    # Its thread, started for real: a daemon, so it never keeps the process alive, and it ends when stopped.
    exits = []
    watchdog = cc.Watchdog(lambda: 50.0, reserve_gib=24.0, holding=lambda: True, exit=exits.append,
                           write=exits.append)
    assert watchdog.start() is watchdog
    thread = watchdog._thread
    try:
        assert thread.daemon and thread.is_alive()
    finally:
        watchdog.stop()
    assert not thread.is_alive() and exits == []


def test_the_watchdogs_words_never_wait_on_a_stalled_terminal(cc, monkeypatch):
    # A terminal paused with Ctrl-S, or a full pipe, blocks a write; the watchdog's exit can't wait for one. Its words
    # go from a thread of their own, waited for a moment at most.
    release, written = threading.Event(), []

    class StalledOs(NoExit):
        def write(self, fd, data):
            written.append((fd, data))
            release.wait(5)
            return len(data)

    monkeypatch.setattr(cc, "os", StalledOs())
    started = time.monotonic()
    cc._say_without_waiting("cuda_ceiling: a line\n")
    took = time.monotonic() - started
    release.set()
    assert took < 0.5 and written == [(2, b"cuda_ceiling: a line\n")]
