# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""The CUDA-allocatable ceiling, measured carefully (Phase 2a, Task 41). It asks CUDA for memory a step at a time, every
page touched and each step confirmed in MemAvailable, until MemAvailable is within a step of the reserve; then it frees
everything and says how much CUDA gave: the ceiling is at least that. On the Spark, from the repo's root, with the box
cleared and someone watching (Task 41, Step 3):

    uv run --no-project stack/measure/cuda_ceiling.py

- CUDA through ctypes on --lib (default: llama.cpp's own libcudart.so.13): cudaMemGetInfo, cudaMalloc, cudaMemset,
  cudaDeviceSynchronize and cudaFree, with cudaGetErrorString for an error's words.
- First it prints cudaMemGetInfo's free and total, with nothing allocated. That call starts CUDA, whose context takes
  memory of its own, so MemAvailable's start is read after it.
- Before each step it reads MemAvailable, and stops when that, less a step, would pass under --reserve (24 GiB by
  default, the reserve every load keeps free). It never allocates "until failure".
- A step is cudaMalloc'd, cudaMemset over all of it, so every page is touched, and synchronized, so the touch is done
  and an error from it is seen at that step. It prints what CUDA holds, MemAvailable, and cudaMemGetInfo's free.
- After each step it re-reads MemAvailable every 250 ms, for up to 5 s, until it has fallen since the start by at least
  90% of all CUDA holds. If it hasn't, the reading isn't following what CUDA takes: it frees everything and stops
  (exit 3).
- While CUDA holds anything for it, it reads MemAvailable at least every 250 ms: after a step, through each pause, which
  it sleeps in 250 ms slices. A reading under the reserve frees everything at once (exit 3).
- It stops at the first CUDA error, and frees everything (exit 4).
- The reserve can only be raised, and the pace only slowed: a --reserve under 24, a --step-gib over 0.25 or a --pause-s
  under 2.5 is refused, with nothing allocated (exit 2).
- Every pointer is freed on every way out: the stop, a CUDA error, any other error (exit 1), Ctrl-C (130), SIGHUP (129),
  SIGTERM (143), and Ctrl-Z (148), which ends it rather than pausing it, since a paused script would hold CUDA's memory
  with nothing watching the reserve. Under uv run, Ctrl-Z also stops uv itself, which fg lets exit (or kill %1 ends);
  the memory is freed either way. A second signal while it frees is ignored: uv forwards SIGINT, SIGHUP and SIGTERM, so
  one sent to the process group reaches the script twice. A signal that comes during a CUDA call is taken once the
  script's record of that call is straight, so no pointer is ever lost between the two.
- CUDA's allocations don't show in a process's RSS (below), and earlyoom picks by the kernel's oom_score, which counts
  RSS, so earlyoom can't pick this script: its own reading, confirmed at every step, is its guard against a MemAvailable
  that reports late.
- --help lists the exit codes.

Standard library only (PEP 723, no dependencies). spark/tests/test_cuda_ceiling.py loads it from this path."""

import argparse
import contextlib
import ctypes
import math
import signal
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

MEMINFO = Path("/proc/meminfo")
KIB_PER_GIB = 1024 * 1024
GIB = 1024**3
LIB = "/opt/local-ai/bin/llama.cpp/b11146/libcudart.so.13"

# The reserve every admitted load keeps free (plan.md, rule 9), 4 above the brake's line, 20: the default, and the
# lowest it may be set, as the hog's floor (stack/measure/hog.py; the controller's ruling, 2026-10-16).
RESERVE_GIB = 24.0
# The pace: the hog's, 0.25 GiB every 2.5 s, 0.1 GiB/s. The brake's rate-of-fall watch (spark/src/spark/brake.py, Task
# 23) acts only on a fall of MIN_FALL_GIB, 1.5 GiB, within its FALL_WINDOW_S, 2 s. At this pace a 2 s window holds one
# step at most, since the pause comes after a step has shown, and one step falls 0.25. So the measurement never trips
# the watch: it passes the warn line, 28, once (memory_warning), and brings no brake_fired (Task 41).
# test_its_pace_stays_under_the_rate_watch checks it with the brake's own constants, so Task 47's new constants re-check
# it. The pace can only be slowed: a bigger step or a shorter pause is refused.
STEP_GIB = 0.25
PAUSE_S = 2.5
POLL_S = 0.25  # how often it reads MemAvailable while CUDA holds anything: after a step and through each pause
REFLECT_S = 5.0  # how long, after a step, it waits for MemAvailable to show what CUDA holds
REFLECTED_SHARE = 0.9  # what share of all CUDA holds MemAvailable must have fallen by since the start
FILL = 0xA5  # what cudaMemset writes over every step

EXIT_MEASURED, EXIT_ERROR, EXIT_REFUSED, EXIT_STOPPED, EXIT_CUDA_ERROR = 0, 1, 2, 3, 4
EXIT_CTRL_C = 130
# Each of these ends the script as Ctrl-C does, and it exits 128 + the signal's number. Ctrl-Z (SIGTSTP) among them, as
# with the hog: a script paused by it would hold CUDA's memory with no reading watching the reserve. Under uv run,
# Ctrl-Z reaches uv too, in the same process group, and uv stops (measured with the hog at Task 40's re-review,
# 2026-10-16): fg lets it exit, 148, or kill %1 ends it. The memory is freed either way, since the script has exited.
STOP_SIGNALS = (signal.SIGHUP, signal.SIGTERM, signal.SIGTSTP)
HELD_SIGNALS = (signal.SIGINT, *STOP_SIGNALS)

# Read on brightroar on 2026-10-16, in Task 41's one real check: a separate script, through this binding, allocating
# 256 MiB once, with the models loaded and about 49 GiB available. cudaRuntimeGetVersion 13040, cudaDriverGetVersion
# 13000. cudaMemGetInfo, with nothing allocated: free 10,989,010,944 bytes (10.23 GiB), total 130,594,156,544 (121.63
# GiB, MemTotal). Its free equalled /proc/meminfo's MemFree at every reading, never MemAvailable: it leaves out the
# page cache, 43 GiB of it then. Starting CUDA took about 0.24 GiB of MemAvailable, the context's. The 256 MiB
# cudaMalloc took 254 to 259 MiB of MemAvailable at once, before cudaMemset touched it, which added nothing; cudaFree
# gave it back at once. The process's RSS stayed at 137 MiB, the context's, through all of it.
BINDING: dict[str, tuple[list[Any], Any]] = {
    "cudaMemGetInfo": ([ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)], ctypes.c_int),
    "cudaMalloc": ([ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t], ctypes.c_int),
    "cudaMemset": ([ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t], ctypes.c_int),
    "cudaDeviceSynchronize": ([], ctypes.c_int),
    "cudaFree": ([ctypes.c_void_p], ctypes.c_int),
    "cudaGetErrorString": ([ctypes.c_int], ctypes.c_char_p),
}


def _ignore_stops() -> None:
    """On its way out the script ignores Ctrl-C and the stop signals, so a second one can't cut its freeing short. Under
    uv run it's routine: uv forwards SIGINT, SIGHUP and SIGTERM, so one sent to the process group reaches the script
    twice (Task 40's re-review, 2026-10-16). main puts the handlers back."""
    for signum in HELD_SIGNALS:
        signal.signal(signum, signal.SIG_IGN)


@contextlib.contextmanager
def _signals_deferred() -> Iterator[None]:
    """Runs a CUDA call and the script's record of it as one: a signal that comes meanwhile is noted, then raised again
    once both are done, for whatever handler is in place by then. Without it, a signal taken between cudaMalloc's
    return and the pointer's record, or between a pointer leaving the record and its cudaFree, would leave that pointer
    unfreed. A Python handler runs in the main thread whichever thread the signal reached, CUDA's own among them, so
    this holds where blocking the signal in the main thread wouldn't."""
    noted: list[int] = []
    previous: dict[int, Any] = {}
    try:
        for signum in HELD_SIGNALS:
            previous[signum] = signal.signal(signum, lambda number, frame: noted.append(number))
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        if noted:
            signal.raise_signal(noted[0])


class CudaError(Exception):
    """A CUDA call that returned an error, in cudaGetErrorString's words."""

    def __init__(self, call: str, code: int, words: str) -> None:
        super().__init__(f"{call} failed: {words} (CUDA error {code})")
        self.call, self.code = call, code


class Cuda:
    """libcudart through ctypes, and the pointers CUDA holds for this script, each freed by free_all."""

    def __init__(self, lib: Any) -> None:
        for name, (argtypes, restype) in BINDING.items():
            try:
                function = getattr(lib, name)
            except AttributeError:
                raise OSError(f"it has no {name}, so it isn't CUDA's runtime") from None
            function.argtypes, function.restype = argtypes, restype
        self._lib = lib
        self._held: list[tuple[int, int]] = []  # (pointer, bytes)
        self.free_failures: list[str] = []

    @property
    def held_gib(self) -> float:
        return sum(nbytes for _, nbytes in self._held) / GIB

    def words(self, code: int) -> str:
        text = self._lib.cudaGetErrorString(code)
        return text.decode(errors="replace") if text else "no words for it"

    def _check(self, call: str, code: int) -> None:
        if code:
            raise CudaError(call, code, self.words(code))

    def mem_get_info(self) -> tuple[int, int]:
        """cudaMemGetInfo's free and total, in bytes."""
        free, total = ctypes.c_size_t(), ctypes.c_size_t()
        self._check("cudaMemGetInfo", self._lib.cudaMemGetInfo(ctypes.byref(free), ctypes.byref(total)))
        return free.value, total.value

    def take(self, nbytes: int) -> None:
        """One step: cudaMalloc'd and recorded as one, then cudaMemset over all of it and synchronized."""
        pointer = ctypes.c_void_p()
        with _signals_deferred():
            code = self._lib.cudaMalloc(ctypes.byref(pointer), nbytes)
            if not code:
                self._held.append((pointer.value, nbytes))
        self._check("cudaMalloc", code)
        self._check("cudaMemset", self._lib.cudaMemset(pointer.value, FILL, nbytes))
        self._check("cudaDeviceSynchronize", self._lib.cudaDeviceSynchronize())

    def free_all(self) -> None:
        """cudaFree for every pointer held, each once. A pointer cudaFree refuses is noted in free_failures, and the
        rest are still freed."""
        with _signals_deferred():
            while self._held:
                pointer, _ = self._held.pop()
                code = self._lib.cudaFree(pointer)
                if code:
                    self.free_failures.append(f"cudaFree({pointer:#x}) failed: {self.words(code)} (CUDA error {code})")


def load_lib(path: str) -> Any:
    """libcudart, loaded from `path`."""
    return ctypes.CDLL(path)


@dataclass(frozen=True)
class Result:
    allocated_gib: float  # what CUDA gave, every page touched, before it stopped; freed by the time it returns
    # "under_reserve": a reading under the reserve while CUDA held anything, which frees everything at once
    stopped: Literal["reserve", "under_reserve", "cuda_error", "not_reflected"]
    detail: str  # why it stopped, in words for the person watching


class Stopped(BaseException):
    """SIGHUP, SIGTERM or Ctrl-Z's SIGTSTP, raised wherever the script is, so it frees everything as on Ctrl-C."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum
        self.name = signal.Signals(signum).name


def _stop(signum: int, frame: object) -> None:
    raise Stopped(signum)


def read_available_gib(meminfo: Path = MEMINFO) -> float:
    """MemAvailable from /proc/meminfo, in GiB."""
    for line in meminfo.read_text().splitlines():
        name, _, value = line.partition(":")
        if name == "MemAvailable":
            return int(value.split()[0]) / KIB_PER_GIB
    raise OSError(f"{meminfo} has no MemAvailable line")


def _the_reserve(reserve_gib: float) -> str:
    """The reserve in words: the reserve when it is the default, and the one given when it was raised."""
    if reserve_gib == RESERVE_GIB:
        return f"the reserve, {reserve_gib:g} GiB"
    return f"the reserve you set, --reserve {reserve_gib:g} GiB"


def _watch(read_available: Callable[[], float], sleep: Callable[[float], None], *, seconds: float,
           reserve_gib: float) -> float:
    """Waits `seconds` in POLL_S slices, reading MemAvailable after each, and returns the first reading under
    `reserve_gib` at once, or else the last. With no time to wait, it reads once."""
    waited = 0.0
    while True:
        pause = min(POLL_S, seconds - waited)
        sleep(pause)
        waited += pause
        available = read_available()
        if available < reserve_gib or waited >= seconds:
            return available


def _info_words(cuda: Cuda, held_gib: float) -> str:
    """cudaMemGetInfo while CUDA holds what it holds, or why it didn't answer."""
    try:
        free, total = cuda.mem_get_info()
    except CudaError as err:
        return f"cudaMemGetInfo, with {held_gib:.2f} GiB held, didn't answer: {err}."
    return f"cudaMemGetInfo, with {held_gib:.2f} GiB held: free {free / GIB:.2f} GiB, total {total / GIB:.2f} GiB."


def measure(cuda: Cuda, *, read_available: Callable[[], float], reserve_gib: float, step_gib: float, pause_s: float,
            sleep: Callable[[float], None]) -> Result:
    """Asks CUDA for `step_gib` at a time, `pause_s` apart, each step touched and confirmed in MemAvailable
    (REFLECTED_SHARE of all CUDA holds, within REFLECT_S), until MemAvailable less a step would pass under
    `reserve_gib`. While CUDA holds anything it reads MemAvailable at least every POLL_S, through each pause, and a
    reading under `reserve_gib` frees everything at once. It stops at the first CUDA error. Every way it returns, CUDA
    holds nothing for it."""
    try:
        free, total = cuda.mem_get_info()
    except CudaError as err:
        return Result(0.0, "cuda_error",
                      f"{err}, before anything was allocated. Nothing allocated, nothing measured. Check that the GPU "
                      "answers (llama-server --list-devices, from the folder --lib is in), then run it again.")
    print(f"cuda_ceiling: cudaMemGetInfo, with nothing allocated: free {free / GIB:.2f} GiB, total "
          f"{total / GIB:.2f} GiB.", flush=True)
    start = read_available()  # after cudaMemGetInfo has started CUDA, so the context's memory isn't taken for a step
    step_bytes = math.ceil(step_gib * GIB)
    available, steps = start, 0

    def cuda_error(err: CudaError, allocated_gib: float, when: str) -> Result:
        info = _info_words(cuda, cuda.held_gib)
        cuda.free_all()
        return Result(allocated_gib, "cuda_error",
                      f"{when}, {err}, with MemAvailable at {available:.2f} GiB. {info} It stopped at that first CUDA "
                      f"error and freed everything. CUDA gave {allocated_gib:.2f} GiB, every page touched, before it: "
                      "Task 41's Step 4 records a run that stops this way as it is.")

    while True:
        held = cuda.held_gib
        if held and available < reserve_gib:
            cuda.free_all()
            fall = start - available
            if fall - held > step_gib:
                why = (f"{fall - held:.2f} GiB more than CUDA held: something else took memory meanwhile. Check what "
                       "(spark status for the models, top -o %MEM for the rest), and run it again once memory is "
                       "steady.")
            else:
                why = ("within a step of what CUDA held: its own last step took it under. Run it again for a run that "
                       "stops at the reserve.")
            return Result(held, "under_reserve",
                          f"MemAvailable read {available:.2f} GiB, under {_the_reserve(reserve_gib)}, while CUDA held "
                          f"{held:.2f} GiB, so it freed everything at once and stopped. MemAvailable had fallen "
                          f"{fall:.2f} GiB since the start, {why} CUDA gave {held:.2f} GiB, every page touched, before "
                          "it stopped.")
        if available - step_gib < reserve_gib:
            if not held:
                where = "already under" if available < reserve_gib else "within a step of"
                then = ("Something holds that memory: check what (spark status for the models, top -o %MEM for the "
                        "rest), and clear the box first (spark make-room --all, Task 41's Step 3)."
                        if available < reserve_gib else
                        "Clear the box first (spark make-room --all, Task 41's Step 3), then run it again.")
                return Result(0.0, "reserve", f"MemAvailable is {available:.2f} GiB, {where} "
                                              f"{_the_reserve(reserve_gib)}; nothing allocated, nothing measured. "
                                              f"{then}")
            info = _info_words(cuda, held)
            cuda.free_all()
            return Result(held, "reserve",
                          f"MemAvailable is {available:.2f} GiB, within a step of {_the_reserve(reserve_gib)}, so it "
                          f"stopped there: CUDA gave {held:.2f} GiB in {steps} step{'' if steps == 1 else 's'}, every "
                          f"page touched, and MemAvailable fell {start - available:.2f} GiB since the start. {info} It "
                          f"freed everything. The CUDA-allocatable ceiling is at least {held:.2f} GiB: Task 41's "
                          "Step 4 records it as that lower bound. If make-room holds the box for this run, spark "
                          "make-room --done ends the hold.")
        if not steps:
            print(f"cuda_ceiling: MemAvailable is {start:.2f} GiB. Allocating {step_gib:g} GiB every {pause_s:g} s, "
                  f"every page touched, until MemAvailable is within a step of {_the_reserve(reserve_gib)}; a reading "
                  "under it frees everything at once. Ctrl-C stops it and frees everything.", flush=True)
        try:
            cuda.take(step_bytes)
        except CudaError as err:
            return cuda_error(err, held, f"At step {steps + 1}")
        steps += 1
        held = cuda.held_gib
        available = read_available()
        waited = 0.0
        while start - available < REFLECTED_SHARE * held and available >= reserve_gib and waited < REFLECT_S:
            sleep(POLL_S)
            waited += POLL_S
            available = read_available()
        try:
            cuda_free, _ = cuda.mem_get_info()
        except CudaError as err:
            return cuda_error(err, held, f"After step {steps}")
        print(f"cuda_ceiling: CUDA holds {held:.2f} GiB; MemAvailable {available:.2f} GiB; cudaMemGetInfo's free "
              f"{cuda_free / GIB:.2f} GiB.", flush=True)
        if available < reserve_gib:
            continue  # the reserve's check, at the top, frees everything at once
        if start - available < REFLECTED_SHARE * held:
            cuda.free_all()
            return Result(held, "not_reflected",
                          f"MemAvailable fell {start - available:.2f} GiB since the start, under "
                          f"{REFLECTED_SHARE:.0%} of the {held:.2f} GiB CUDA held, {REFLECT_S:g} s after its last "
                          "step: the reading isn't following what CUDA takes, perhaps because something else freed "
                          "memory meanwhile. It freed everything and stopped: this run measures nothing. Run it again "
                          "once memory is steady; if it stops this way again, MemAvailable isn't showing CUDA's "
                          "allocations, and this method can't measure the ceiling (Task 41, Step 4).")
        available = _watch(read_available, sleep, seconds=pause_s, reserve_gib=reserve_gib)


def _number(text: str, *, zero_ok: bool) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0 or (value == 0 and not zero_ok):
        raise argparse.ArgumentTypeError(f"{text!r} isn't a number {'of 0 or more' if zero_ok else 'above 0'}")
    return value


def _above_zero(text: str) -> float:
    return _number(text, zero_ok=False)


def _zero_or_more(text: str) -> float:
    return _number(text, zero_ok=True)


EXIT_CODES = f"""exit status:
  0    measured: CUDA gave every step until MemAvailable was within a step of the reserve; the
       ceiling is at least the figure printed; everything freed
  1    an error outside CUDA (--lib unusable, /proc/meminfo unreadable); everything freed
  2    refused, nothing allocated: a --reserve under {RESERVE_GIB:g}, or a faster pace
  3    stopped, everything freed: MemAvailable not following what CUDA took, or a reading under
       the reserve while CUDA held anything; or nothing allocated, since MemAvailable was already
       within a step of the reserve
  4    a CUDA error stopped it; everything freed, and what CUDA gave before it printed
  {128 + signal.SIGHUP}  SIGHUP; everything freed
  {EXIT_CTRL_C}  Ctrl-C; everything freed
  {128 + signal.SIGTERM}  SIGTERM; everything freed
  {128 + signal.SIGTSTP}  Ctrl-Z, which ends it rather than pausing it; everything freed. Under uv run,
       uv itself stays stopped: fg lets it exit {128 + signal.SIGTSTP}, or kill %1 ends it"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cuda_ceiling.py", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EXIT_CODES,
        description="Measures the CUDA-allocatable ceiling: asks CUDA for memory a step at a time, each step\n"
                    "touched and confirmed in MemAvailable, until MemAvailable is within a step of the reserve;\n"
                    f"then frees everything. --lib's default, llama.cpp's own libcudart:\n  {LIB}")
    parser.add_argument("--lib", default=LIB, metavar="PATH",
                        help="CUDA's runtime, libcudart.so.13 (default: llama.cpp's own, above)")
    parser.add_argument("--reserve", type=_above_zero, default=RESERVE_GIB, metavar="GIB",
                        help="it stops before MemAvailable passes under this, and a reading under it while CUDA holds "
                             f"anything frees everything at once. It can only be raised (default {RESERVE_GIB:g}, the "
                             "reserve every load keeps free)")
    parser.add_argument("--step-gib", type=_above_zero, default=STEP_GIB, metavar="GIB",
                        help=f"how much each step asks for; it can only be made smaller (default {STEP_GIB:g})")
    parser.add_argument("--pause-s", type=_zero_or_more, default=PAUSE_S, metavar="S",
                        help=f"the pause after each step has shown; it can only be made longer (default {PAUSE_S:g})")
    return parser


def _refusal(args: argparse.Namespace) -> str | None:
    """Why the script won't start, in words, or None. The reserve can only be raised, and the pace only slowed, as the
    hog's floor and pace (the controller's rulings, 2026-10-16)."""
    if args.reserve < RESERVE_GIB:
        return (f"--reserve {args.reserve:g} is under {RESERVE_GIB:g} GiB, the reserve every load keeps free and the "
                f"least this script may ever leave (the brake's line is 20); nothing allocated. Leave --reserve out, "
                f"or give {RESERVE_GIB:g} or more.")
    faster = []
    if args.step_gib > STEP_GIB:
        faster.append(f"--step-gib {args.step_gib:g} is a bigger step than {STEP_GIB:g} GiB")
    if args.pause_s < PAUSE_S:
        faster.append(f"--pause-s {args.pause_s:g} {'a' if faster else 'is a'} shorter pause than {PAUSE_S:g} s")
    if faster:
        return (f"{', and '.join(faster)}. The pace test checks {STEP_GIB:g} GiB steps {PAUSE_S:g} s apart against "
                f"the brake's rate watch (Task 23), and a bigger step or a shorter pause could read to it as a fall; "
                f"nothing allocated. Leave {'them' if len(faster) > 1 else 'it'} out, or slow the pace: steps of "
                f"{STEP_GIB:g} GiB or less, pauses of {PAUSE_S:g} s or more.")
    return None


def main(argv: list[str] | None = None, *, lib: Any = None, read_available: Callable[[], float] | None = None,
         sleep: Callable[[float], None] | None = None) -> int:
    """The command line. `lib`, `read_available` and `sleep` are the box's own unless a test gives stand-ins."""
    args = _parser().parse_args(argv)
    refusal = _refusal(args)
    if refusal is not None:
        print(f"cuda_ceiling: {refusal}", file=sys.stderr)
        return EXIT_REFUSED
    read_available = read_available_gib if read_available is None else read_available
    sleep = time.sleep if sleep is None else sleep
    # Every handler the script changes, SIGINT's too (_ignore_stops sets it on the way out), for finally to put back.
    handlers = {signum: signal.getsignal(signum) for signum in HELD_SIGNALS}
    cuda: Cuda | None = None
    try:
        for signum in STOP_SIGNALS:
            signal.signal(signum, _stop)
        try:
            cuda = Cuda(load_lib(args.lib) if lib is None else lib)
        except OSError as err:
            print(f"cuda_ceiling: --lib {args.lib} isn't usable: {err}. Nothing allocated. Give --lib the path to "
                  "CUDA's runtime, libcudart.so.13; llama.cpp's build folder holds one, the default.", file=sys.stderr)
            return EXIT_ERROR
        result = measure(cuda, read_available=read_available, reserve_gib=args.reserve, step_gib=args.step_gib,
                         pause_s=args.pause_s, sleep=sleep)
        if result.stopped == "reserve" and result.allocated_gib:
            print(f"cuda_ceiling: {result.detail}", flush=True)
            return EXIT_MEASURED
        print(f"cuda_ceiling: {result.detail}", file=sys.stderr)
        return EXIT_CUDA_ERROR if result.stopped == "cuda_error" else EXIT_STOPPED
    except Stopped as stop:
        _ignore_stops()
        if cuda is not None:
            cuda.free_all()
        if stop.signum == signal.SIGTSTP:
            print("cuda_ceiling: Ctrl-Z ends this script rather than pausing it, since a paused script would hold "
                  "CUDA's memory with nothing watching the reserve. It freed everything CUDA held and stopped. "
                  f"Under uv run, uv itself is left stopped: fg lets it exit ({128 + stop.signum}), or kill %1 ends "
                  "it; the memory is freed either way. Run it again to measure.", file=sys.stderr)
        else:
            print(f"cuda_ceiling: stopped by {stop.name}, and freed everything CUDA held. Run it again to measure.",
                  file=sys.stderr)
        return 128 + stop.signum
    except KeyboardInterrupt:
        _ignore_stops()
        if cuda is not None:
            cuda.free_all()
        print("cuda_ceiling: stopped by Ctrl-C, and freed everything CUDA held. Run it again to measure.",
              file=sys.stderr)
        return EXIT_CTRL_C
    except OSError as err:
        _ignore_stops()
        if cuda is not None:
            cuda.free_all()
        print(f"cuda_ceiling: stopped on an error, and freed everything CUDA held: {err}. Check that /proc/meminfo "
              "reads (grep MemAvailable /proc/meminfo), then run it again.", file=sys.stderr)
        return EXIT_ERROR
    finally:
        _ignore_stops()
        if cuda is not None:
            cuda.free_all()
            if cuda.free_failures:
                print(f"cuda_ceiling: cudaFree failed for {len(cuda.free_failures)} of the pointers CUDA held: "
                      f"{cuda.free_failures[0]}. The driver frees a process's CUDA memory when the process exits, as "
                      "this one now does: check that MemAvailable came back (grep MemAvailable /proc/meminfo).",
                      file=sys.stderr)
        for signum, handler in handlers.items():
            if handler is not None:  # None: a handler not set from Python, which can't be put back from here
                signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main())
