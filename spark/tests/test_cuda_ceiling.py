"""Task 41: the CUDA-allocatable ceiling, stack/measure/cuda_ceiling.py, loaded from its path. Every reading, pause and
CUDA call here is a stand-in's: a stand-in libcudart whose functions take their arguments as ctypes converts them,
over a stand-in box whose MemAvailable falls as each step is touched. No test loads a library or reads the box: the
fixture swaps the script's own loader and reader, and its ctypes' library loaders, for ones that fail the test if
reached, and a check after every test finds no libcudart mapped into the test's process. The stand-in allows a bounded
number of allocations, readings and seconds, so a broken loop fails within those bounds instead of running on."""

import ctypes
import importlib.util
import os
import re
import signal
import sys
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
MAX_CALLS = 10_000  # and calls CUDA about 1,500 times: a loop that calls CUDA without reading fails here
MAX_CLOCK_S = 10_000.0  # and the stand-in's clock stops a run that sleeps past this
CUDA_TOTAL = 130_594_156_544  # cudaMemGetInfo's total on the box (2026-10-16): 121.63 GiB, MemTotal
OUT_OF_MEMORY = 2  # cudaErrorMemoryAllocation
SIGNALS = {"sighup": signal.SIGHUP, "sigterm": signal.SIGTERM, "ctrl-z": signal.SIGTSTP}


def load_script(monkeypatch):
    spec = importlib.util.spec_from_file_location("cuda_ceiling", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "cuda_ceiling", module)
    spec.loader.exec_module(module)
    return module


class NoLibraries:
    """The script's ctypes, but every way it has to load a library fails the test before one is loaded."""

    LOADERS = {"CDLL", "PyDLL", "cdll", "pydll", "LibraryLoader", "util"}

    def __getattr__(self, name):
        if name in self.LOADERS:
            raise AssertionError(f"a test reached ctypes.{name}, which loads a library: the real libcudart, perhaps")
        return getattr(ctypes, name)


@pytest.fixture
def cc(monkeypatch):
    module = load_script(monkeypatch)

    def the_box(*args, **kwargs):
        raise AssertionError("a test reached the script's own loader or reader, which load the real libcudart and "
                             "read the box's memory")

    monkeypatch.setattr(module, "load_lib", the_box)
    monkeypatch.setattr(module, "read_available_gib", the_box)
    monkeypatch.setattr(module, "ctypes", NoLibraries())
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


class Box:
    """A stand-in box and its libcudart (`lib`): MemAvailable, a clock that moves only when the script sleeps, and
    CUDA's allocations, each a record, never memory. An allocation shows in MemAvailable once cudaMemset has touched all
    of it, `lag_s` later, and only `shows(n)` of step n's size (all of it by default). The first CUDA call starts CUDA,
    whose context takes `context_gib` of MemAvailable. From `drop_at` on, MemAvailable reads `drop_to`, as when
    something else takes memory. `fail(name, n)` gives the error code a call returns for step n (0: none);
    `before(name, n)` runs as a call begins and `after(name, n)` once its work is done."""

    def __init__(self, start, *, lag_s=0.0, shows=None, drop_at=None, drop_to=None, context_gib=0.0,
                 cuda_free=80 * GIB, fail=None, before=None, after=None):
        self.start, self.lag_s = start, lag_s
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
        return self.start - context - shown

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


def run(cc, box, *argv):
    return cc.main(list(argv), lib=box.lib, read_available=box.read, sleep=box.sleep)


def measure(cc, box, *, pause_s=2.5):
    return cc.measure(cc.Cuda(box.lib), read_available=box.read, reserve_gib=24.0, step_gib=0.25, pause_s=pause_s,
                      sleep=box.sleep)


def cuda_calls(box, *names):
    return [name for name, _, _ in box.calls if name in names]


@pytest.mark.parametrize("lag_s", [0.0, 1.0], ids=["at-once", "a-second-late"])
def test_it_stops_when_memavailable_would_pass_the_reserve(cc, capsys, lag_s):
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
    # Through main: exit 0, every line its own, and the last says what it measured.
    capsys.readouterr()
    box = Box(117.0, lag_s=lag_s)
    assert run(cc, box) == 0
    assert len(box.allocs) == 372 and box.live == []
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert out.err == "" and all(line.startswith("cuda_ceiling: ") for line in lines)
    assert "The CUDA-allocatable ceiling is at least 93.00 GiB" in lines[-1]
    assert "cudaMemGetInfo, with 93.00 GiB held: free" in lines[-1] and "It freed everything" in lines[-1]


@pytest.mark.parametrize("third", [0.0, 0.2], ids=["flat", "a-fifth"])
def test_a_step_that_doesnt_show_stops_it(cc, capsys, third):
    # After the third step MemAvailable doesn't fall (flat), or falls a fifth of a step: 0.55 of 0.75 GiB held, under
    # 90%. Either way it re-reads for 5 s, then frees every pointer and stops.
    def shows(n):
        return third if n == 3 else 1.0

    box = Box(117.0, shows=shows)
    result = measure(cc, box)
    assert result.stopped == "not_reflected" and result.allocated_gib == 0.75
    assert len(box.allocs) == 3 and box.live == []
    third_at, freed_at = box.allocs[2]["at"], {alloc["freed_at"] for alloc in box.allocs}
    assert len(freed_at) == 1 and freed_at.pop() - third_at == pytest.approx(5.0)  # it waited the 5 s, no more
    box = Box(117.0, shows=shows)
    assert run(cc, box) == 3
    assert len(box.allocs) == 3 and box.live == []
    assert "isn't following what CUDA takes" in capsys.readouterr().err


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


class Stderr:
    """The script's stderr, noting with each line how many pointers CUDA still held for it as the line was written."""

    def __init__(self, box):
        self.box, self.lines = box, []

    def write(self, text):
        if text.strip():
            self.lines.append((text, len(self.box.live)))
        return len(text)

    def flush(self):
        pass

    @property
    def words(self):
        return "".join(text for text, _ in self.lines)


@pytest.mark.parametrize("way", ["exception", "meminfo-unreadable", "ctrl-c", "sighup", "sigterm", "ctrl-z"])
def test_it_frees_everything_on_any_way_out(cc, monkeypatch, way):
    def at_step_5(name, n):
        if (name, n) != ("cudaMemset", 5):
            return
        if way == "exception":
            raise RuntimeError("a stand-in's failure")
        if way == "meminfo-unreadable":
            box.read_error = OSError("/proc/meminfo: a stand-in's unreadable file")
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
    stderr = Stderr(box)
    monkeypatch.setattr(sys, "stderr", stderr)
    before = {signum: signal.signal(signum, note) for signum in SIGNALS.values()}
    try:
        if way == "exception":
            with pytest.raises(RuntimeError):
                run(cc, box)
        else:
            # 1 for an error outside CUDA; 129, 143, and 148 for Ctrl-Z on Linux
            code = {"meminfo-unreadable": 1, "ctrl-c": 130}.get(way) or 128 + SIGNALS[way]
            assert run(cc, box) == code
        after = {signum: signal.getsignal(signum) for signum in SIGNALS.values()}
    finally:
        for signum, handler in before.items():
            signal.signal(signum, handler)
    assert len(box.allocs) == 5 and box.live == []  # cudaFree for each pointer taken, once (the stand-in checks)
    # The script took the signal itself, then put the test's handlers back.
    assert reached_the_test == [] and all(handler is note for handler in after.values())
    # Its words say it freed everything, and by the time they are written it has.
    assert (way == "exception") == (stderr.lines == []) and all(held == 0 for _, held in stderr.lines)
    words = stderr.words
    if way == "meminfo-unreadable":
        assert "stopped on an error, and freed everything CUDA held: /proc/meminfo: a stand-in's" in words
        assert "grep MemAvailable /proc/meminfo" in words
    if way in ("ctrl-c", "sighup", "sigterm"):
        assert f"stopped by {'Ctrl-C' if way == 'ctrl-c' else way.upper()}, and freed everything CUDA held" in words
    if way == "ctrl-z":  # it ends the script, never pauses it holding memory with nothing watching the reserve
        assert "Ctrl-Z ends this script rather than pausing it" in words and "freed everything CUDA held" in words
        stopped = 128 + signal.SIGTSTP  # 148 on Linux
        assert f"Under uv run, uv itself is left stopped: fg lets it exit ({stopped}), or kill %1 ends it" in words


@pytest.mark.parametrize("call, n, allocated, taken", [
    ("cudaMalloc", 10, 2.25, 9),
    ("cudaMemset", 10, 2.25, 10),
    ("cudaDeviceSynchronize", 10, 2.25, 10),
    ("cudaMemGetInfo", 10, 2.5, 10),
    ("cudaMemGetInfo", 0, 0.0, 0),
], ids=["malloc", "memset", "synchronize", "memgetinfo-after-a-step", "memgetinfo-first"])
def test_it_stops_at_the_first_cuda_error(cc, capsys, call, n, allocated, taken):
    # cudaMalloc refusing step 10: the 9 taken freed, and CUDA gave 2.25 GiB. A failed cudaMemset or synchronize leaves
    # step 10 untouched, and its pointer is freed too; a cudaMemGetInfo that fails after step 10, which was touched,
    # counts it. One failing first, before anything is allocated, measures nothing.
    def fail(name, at):
        return OUT_OF_MEMORY if (name, at) == (call, n) else 0

    box = Box(117.0, fail=fail)
    result = measure(cc, box)
    assert result.stopped == "cuda_error" and result.allocated_gib == allocated
    assert len(box.allocs) == taken and box.live == []
    failed_at = next(i for i, (name, at, _) in enumerate(box.calls) if (name, at) == (call, n))
    after_it = {name for name, _, _ in box.calls[failed_at + 1:]}
    assert not after_it & {"cudaMalloc", "cudaMemset", "cudaDeviceSynchronize"}  # nothing asked for or touched after
    assert f"{call} failed: out of memory (CUDA error {OUT_OF_MEMORY})" in result.detail
    assert ("freed everything" in result.detail) == bool(taken)
    box = Box(117.0, fail=fail)
    assert run(cc, box) == 4
    assert len(box.allocs) == taken and box.live == []
    words = capsys.readouterr().err
    if taken:
        assert f"CUDA gave {allocated:.2f} GiB, every page touched, before it" in words
    else:
        assert "Nothing allocated, nothing measured" in words


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
    (["--reserve", "20"], "--reserve 20 is under 24 GiB, the reserve every load keeps free"),
    (["--reserve", "23.9"], "--reserve 23.9 is under 24 GiB, the reserve every load keeps free"),
], ids=["20", "23.9"])
def test_a_reserve_under_24_is_refused(cc, capsys, argv, named):
    # The reserve can only be raised, as the hog's floor (the controller's ruling, 2026-10-16): nothing loaded, read or
    # allocated, and the words say what to do instead.
    box = Box(117.0)
    assert run(cc, box, *argv) == 2
    assert box.calls == [] and box.reads == [] and box.allocs == []
    words = capsys.readouterr().err
    assert named in words and "nothing allocated" in words and "Leave --reserve out, or give 24 or more" in words
    # A raised reserve runs, stops there, and is named as the one given.
    box = Box(32.0)
    assert run(cc, box, "--reserve", "30") == 0
    assert len(box.allocs) == 8 and box.live == []
    out = capsys.readouterr().out
    assert "the reserve you set, --reserve 30 GiB" in out and "the reserve every load keeps free" not in out


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


@pytest.mark.parametrize("case", ["after-a-step", "in-a-pause", "while-a-step-shows"])
def test_a_fall_under_the_reserve_while_it_holds_frees_everything(cc, capsys, case):
    # From 30 the reserve, 24, is 24 steps away, 2.5 s apart. Something else takes memory, and MemAvailable reads 23:
    # as the third step is touched, at 5 s, so the reading right after that step is the first to see it; at 6.1 s, in
    # the pause after it; or, with each step showing a second late, at 7.6 s, while the script waits for the third step
    # (taken at 7 s) to show. The last two are off any slice's grid, so only readings 250 ms apart see them within
    # 250 ms.
    def a_box():
        box = Box(30.0, lag_s=1.0 if case == "while-a-step-shows" else 0.0,
                  drop_at={"in-a-pause": 6.1, "while-a-step-shows": 7.6}.get(case), drop_to=23.0)
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
    assert {alloc["freed_at"] for alloc in box.allocs} == {first_under}  # and freed at once: no pause, no read first
    assert box.now == first_under and box.reads[-1][1] == 23.0
    words = capsys.readouterr().err
    assert "MemAvailable read 23.00 GiB, under the reserve, 24 GiB, while CUDA held 0.75 GiB" in words
    assert "freed everything at once" in words and "7.00 GiB since the start" in words
    box = a_box()
    result = measure(cc, box)
    assert result.stopped == "under_reserve" and result.allocated_gib == 0.75
    assert box.live == [] and {alloc["freed_at"] for alloc in box.allocs} == {box.now}  # freed by measure itself


@pytest.mark.parametrize("start, words", [
    (23.5, "MemAvailable is 23.50 GiB, already under the reserve, 24 GiB; nothing allocated, nothing measured"),
    (24.0, "MemAvailable is 24.00 GiB, within a step of the reserve, 24 GiB; nothing allocated, nothing measured"),
    (24.2, "MemAvailable is 24.20 GiB, within a step of the reserve, 24 GiB; nothing allocated, nothing measured"),
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
    # cudaFree as the run ends at the reserve, before it frees. Every pointer must still be freed, once.
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
    assert "freed everything CUDA held" in capsys.readouterr().err


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
    assert box.calls == [] and box.reads == []
    words = capsys.readouterr().err
    assert "--lib /somewhere/libother.so isn't usable: it has no cudaMalloc, so it isn't CUDA's runtime" in words
    assert "Nothing allocated" in words and "libcudart.so.13" in words

    # One that doesn't load.
    def no_such_file(path):
        raise OSError(f"{path}: cannot open shared object file: No such file or directory")

    monkeypatch.setattr(cc, "load_lib", no_such_file)
    box = Box(117.0)
    assert cc.main(["--lib", "/nowhere/libcudart.so.13"], read_available=box.read, sleep=box.sleep) == 1
    assert box.reads == []
    assert "--lib /nowhere/libcudart.so.13 isn't usable: /nowhere/libcudart.so.13: cannot open" in (
        capsys.readouterr().err)


def test_a_cudafree_that_fails_is_reported(cc, capsys):
    # A pointer cudaFree refuses is said once, with what to do; every other pointer is still freed.
    box = Box(30.0, fail=lambda name, n: 1 if (name, n) == ("cudaFree", 3) else 0)
    assert run(cc, box) == 0
    assert [alloc["n"] for alloc in box.live] == [3]
    words = capsys.readouterr().err
    assert words.count("cudaFree failed for 1 of the pointers CUDA held") == 1
    assert "a stand-in's error (CUDA error 1)" in words and "grep MemAvailable /proc/meminfo" in words
