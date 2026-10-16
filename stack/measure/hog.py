# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""The drills' one memory hog (Phase 2a, Task 40; Tasks 47, 48 and 50 use it too). It brings MemAvailable down to
--leave a step at a time, sized from MemAvailable as it reads it, never a fixed size; holds it there for --hold-s; then
frees everything and exits 0. On the Spark, from the repo's root:

    uv run --no-project stack/measure/hog.py --leave 48

- Before each step it reads MemAvailable, and stops growing when that, less a step, would pass under --leave.
- A step is private anonymous memory with every page written, so all of it is resident at once (Memory, below).
- After each step it re-reads MemAvailable every 250 ms, for up to 5 s, until it has fallen since the hog's start by
  at least 90% of all the hog holds. If it hasn't, the reading isn't following what the hog takes, so the hog frees
  everything and stops (exit 3).
- While it holds anything, a reading under --floor (24 GiB by default, the reserve every load keeps free, 4 above the
  brake's line) frees everything at once and stops (exit 3). A --leave under the floor is refused, with nothing taken
  (exit 2).
- It frees everything on every way out: the hold's end, a stop, an error (exit 1), Ctrl-C (exit 130), SIGTERM (143)
  and SIGHUP (129). Killed any other way (SIGKILL, earlyoom), the kernel frees its memory with it.
- It prints each step's total and MemAvailable, in GiB.

Standard library only (PEP 723, no dependencies). spark/tests/test_hog.py loads it from this path."""

import argparse
import math
import mmap
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MEMINFO = Path("/proc/meminfo")
KIB_PER_GIB = 1024 * 1024
GIB = 1024**3

FLOOR_GIB = 24.0  # the reserve every admitted load keeps free (plan.md, rule 9), 4 above the brake's line, 20
# The pace: 0.25 GiB every 2.5 s, 0.1 GiB/s. The brake's rate-of-fall watch (spark/src/spark/brake.py, Task 23) acts
# only on a fall of MIN_FALL_GIB, 1.5 GiB, within its FALL_WINDOW_S, 2 s. At this pace a 2 s window holds one step at
# most, since the pause comes after a step has shown, and one step falls 0.25. So the hog never trips the watch, and a
# drill trips the brake only where it raises the brake's thresholds on purpose (Task 48).
# test_its_pace_stays_under_the_rate_watch checks it with the brake's own constants, so Task 47's re-check it.
STEP_GIB = 0.25
PAUSE_S = 2.5
# How long it holds at --leave unless told otherwise: an hour, so a hog left running ends by itself. The drills that
# end it with Ctrl-C (S03, S05) give no --hold-s.
HOLD_S = 3600.0
POLL_S = 0.25  # how often it re-reads MemAvailable after a step, and while it holds
REFLECT_S = 5.0  # how long, after a step, it waits for MemAvailable to show what it holds
REFLECTED_SHARE = 0.9  # what share of all it holds MemAvailable must have fallen by since its start

EXIT_HELD, EXIT_ERROR, EXIT_REFUSED, EXIT_STOPPED = 0, 1, 2, 3
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


@dataclass(frozen=True)
class Result:
    held_gib: float  # what it held when it stopped; for "not_reflected" and "floor", already freed
    stopped: Literal["leave", "not_reflected", "floor"]
    detail: str  # why it stopped, in a sentence for the person watching


def read_available_gib(meminfo: Path = MEMINFO) -> float:
    """MemAvailable from /proc/meminfo, in GiB."""
    for line in meminfo.read_text().splitlines():
        name, _, value = line.partition(":")
        if name == "MemAvailable":
            return int(value.split()[0]) / KIB_PER_GIB
    raise OSError(f"{meminfo} has no MemAvailable line")


# Which process earlyoom would pick (Task 40, Step 4). Run as agent, the hog carries agent's oom_score_adj, 1000, which
# root sets at each login (stack/host/local-ai-agent-oom, Task 28). earlyoom v1.7, the box's, ranks processes by the
# kernel's /proc/<pid>/oom_score, less 300 for a name its --avoid matches (python's isn't one), and
# stack/host/earlyoom.default has no --prefer since Task 28. The kernel's score (fs/proc/base.c's proc_oom_score, from
# mm/oom_kill.c's oom_badness) counts in pages and divides in whole numbers:
#   total   = RAM + swap: MemTotal 127,533,356 kB and SwapTotal 16,777,212 kB in /proc/meminfo, in 4 KiB pages
#             (getconf PAGESIZE), 31,883,339 + 4,194,303 = 36,077,642 (121.6 GiB + 16.0 GiB; read on the box,
#             2026-10-16)
#   badness = RSS + swapped-out pages + page tables + adj × (total // 1000)
#   score   = (1000 + badness × 1000 // total) × 2 // 3
# which is near enough Task 28's (1000 + 1000 × RSS / (RAM + swap) + adj) × 2/3. So:
#   - The hog holding 12 GiB, at 1000: 3,145,728 pages + 1000 × 36,077 = 39,222,728; × 1000 // total = 1087;
#     (1000 + 1087) × 2 // 3 = 1391. Holding a tenth of RAM, 12.16 GiB, as Task 28 reckoned it: 1392.
#   - The coder's engine, on demand at 1000 (spark launch's OOM_ADJ_ON_DEMAND), holding 2% of RAM, 2.43 GiB:
#     637,666 + 36,077,000 = 36,714,666; × 1000 // total = 1017; (1000 + 1017) × 2 // 3 = 1344. An engine's GPU memory
#     isn't in its RSS: Task 28 read 2,643,628 kB, 2.1% of RAM, for a llama-server at 1000, and its score, 1345, as
#     this gives.
# The hog scores about 47 above that engine, so earlyoom, like the kernel, would take it first. At the same adj the
# larger RSS wins: the hog passes an on-demand engine once it holds more than that engine does, about 2.5 GiB, and a
# resident (900; 1278 at 2% of RAM) from the start. Step 4's dry run checks it on the box.
class Memory:
    """The hog's memory: one private anonymous mapping per step, every page written, so each step is resident at once,
    in the hog's RssAnon (which earlyoom's and the kernel's score count) and in MemAvailable's fall.

    An mmap, not a bytearray: a mapping comes straight from the kernel, and close() unmaps it at once, whatever still
    refers to it (a traceback's frame, say), so free_all() frees exactly what it says. A bytearray's memory goes back
    only when its last reference goes, through CPython's and glibc's allocators. Either way a page counts only once
    written: a page only read maps the kernel's shared zero page."""

    def __init__(self) -> None:
        self._blocks: list[mmap.mmap] = []

    def take(self, gib: float) -> None:
        pages = math.ceil(gib * GIB / mmap.PAGESIZE)
        block = mmap.mmap(-1, pages * mmap.PAGESIZE, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)
        self._blocks.append(block)  # before a page is written, so a failure partway still frees it
        block[:: mmap.PAGESIZE] = b"\xa5" * pages  # one byte in every page

    def free_all(self) -> None:
        while self._blocks:
            self._blocks.pop().close()


class Stopped(BaseException):
    """SIGTERM or SIGHUP, raised wherever the hog is, so it frees everything as on Ctrl-C."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum
        self.name = signal.Signals(signum).name


def _stop(signum: int, frame: object) -> None:
    raise Stopped(signum)


def _under_the_floor(available: float, floor_gib: float, held_gib: float) -> str:
    return (f"MemAvailable read {available:.2f} GiB, under the floor, {floor_gib:.2f} GiB, while the hog held "
            f"{held_gib:.2f} GiB: something else is taking memory too. It freed everything at once and stopped.")


def grow(read_available: Callable[[], float], take: Callable[[float], None], free_all: Callable[[], None], *,
         leave_gib: float, floor_gib: float, step_gib: float, pause_s: float,
         sleep: Callable[[float], None]) -> Result:
    """Takes `step_gib` at a time, `pause_s` apart, until MemAvailable less a step would pass under `leave_gib`, each step
    confirmed in MemAvailable (REFLECTED_SHARE of all it holds, within REFLECT_S) and never under `floor_gib` while it
    holds anything. At "leave" it still holds what it took; at "not_reflected" and "floor" it has freed everything."""
    start = read_available()
    print(f"hog: MemAvailable is {start:.2f} GiB. Taking {step_gib:g} GiB every {pause_s:g} s down to "
          f"{leave_gib:.2f} GiB; under {floor_gib:.2f} GiB it frees everything at once.", flush=True)
    held, available = 0.0, start
    while True:
        if held and available < floor_gib:
            free_all()
            return Result(held, "floor", _under_the_floor(available, floor_gib, held))
        if available - step_gib < leave_gib:
            if not held:
                return Result(held, "leave", f"MemAvailable is {available:.2f} GiB, already within a step of --leave "
                                             f"{leave_gib:.2f} GiB: nothing taken.")
            return Result(held, "leave", f"MemAvailable is {available:.2f} GiB, within a step of --leave "
                                         f"{leave_gib:.2f} GiB, with {held:.2f} GiB held.")
        take(step_gib)
        held += step_gib
        available = read_available()
        waited = 0.0
        while start - available < REFLECTED_SHARE * held and available >= floor_gib and waited < REFLECT_S:
            sleep(POLL_S)
            waited += POLL_S
            available = read_available()
        print(f"hog: holding {held:.2f} GiB, MemAvailable {available:.2f} GiB", flush=True)
        if available < floor_gib:
            continue  # the floor's check, at the top, frees everything at once
        if start - available < REFLECTED_SHARE * held:
            free_all()
            return Result(held, "not_reflected",
                          f"MemAvailable fell {start - available:.2f} GiB since the start, under "
                          f"{REFLECTED_SHARE:.0%} of the {held:.2f} GiB the hog held, {REFLECT_S:g} s after its last "
                          "step: the reading isn't following what it takes, perhaps because something else freed "
                          "memory meanwhile. It freed everything and stopped; run it again once memory is steady.")
        sleep(pause_s)
        available = read_available()


def hold(read_available: Callable[[], float], free_all: Callable[[], None], *, held_gib: float, floor_gib: float,
         hold_s: float, sleep: Callable[[float], None]) -> Result:
    """Holds what it took for `hold_s`, reading MemAvailable every POLL_S; a reading under `floor_gib` frees everything
    at once ("floor"). Once the time is up it frees everything ("leave")."""
    waited = 0.0
    while waited < hold_s:
        pause = min(POLL_S, hold_s - waited)
        sleep(pause)
        waited += pause
        available = read_available()
        if available < floor_gib:
            free_all()
            return Result(held_gib, "floor", _under_the_floor(available, floor_gib, held_gib))
    free_all()
    return Result(held_gib, "leave", f"Held {held_gib:.2f} GiB for {hold_s:g} s, and freed it.")


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hog.py", description="The drills' memory hog: takes memory until MemAvailable is down to --leave, "
                                   "holds it, then frees everything.")
    parser.add_argument("--leave", type=_above_zero, required=True, metavar="GIB",
                        help="the MemAvailable it brings the box down to, never below")
    parser.add_argument("--floor", type=_above_zero, default=FLOOR_GIB, metavar="GIB",
                        help=f"while it holds anything, a reading under this frees everything at once; a --leave "
                             f"under it is refused (default {FLOOR_GIB:g}, the reserve)")
    parser.add_argument("--step-gib", type=_above_zero, default=STEP_GIB, metavar="GIB",
                        help=f"how much each step takes (default {STEP_GIB:g})")
    parser.add_argument("--pause-s", type=_zero_or_more, default=PAUSE_S, metavar="S",
                        help=f"the pause after each step has shown (default {PAUSE_S:g})")
    parser.add_argument("--hold-s", type=_zero_or_more, default=HOLD_S, metavar="S",
                        help=f"how long it holds at --leave before it frees everything and exits 0 (default "
                             f"{HOLD_S:g}, an hour); Ctrl-C frees it sooner")
    return parser


def main(argv: list[str] | None = None, *, read_available: Callable[[], float] | None = None,
         memory: Memory | None = None, sleep: Callable[[float], None] | None = None) -> int:
    """The command line. `read_available`, `memory` and `sleep` are the box's own unless a test gives stand-ins."""
    args = _parser().parse_args(argv)
    if args.leave < args.floor:
        print(f"hog: --leave {args.leave:g} is under the floor, {args.floor:g} GiB, the reserve every load keeps free; "
              f"nothing taken. Give a --leave of {args.floor:g} or more.", file=sys.stderr)
        return EXIT_REFUSED
    read_available = read_available_gib if read_available is None else read_available
    memory = Memory() if memory is None else memory
    sleep = time.sleep if sleep is None else sleep
    handlers = {}
    try:
        for signum in STOP_SIGNALS:
            handlers[signum] = signal.signal(signum, _stop)
        result = grow(read_available, memory.take, memory.free_all, leave_gib=args.leave, floor_gib=args.floor,
                      step_gib=args.step_gib, pause_s=args.pause_s, sleep=sleep)
        if result.stopped == "leave" and result.held_gib:
            print(f"hog: {result.detail} Holding it for {args.hold_s:g} s; Ctrl-C frees it sooner.", flush=True)
            result = hold(read_available, memory.free_all, held_gib=result.held_gib, floor_gib=args.floor,
                          hold_s=args.hold_s, sleep=sleep)
        if result.stopped == "leave":
            print(f"hog: {result.detail}", flush=True)
            return EXIT_HELD
        print(f"hog: {result.detail}", file=sys.stderr)
        return EXIT_STOPPED
    except Stopped as stop:
        memory.free_all()
        print(f"hog: stopped by {stop.name}, and freed everything it held.", file=sys.stderr)
        return 128 + stop.signum
    except KeyboardInterrupt:
        memory.free_all()
        print("hog: stopped by Ctrl-C, and freed everything it held.", file=sys.stderr)
        return 130
    except OSError as err:
        memory.free_all()
        print(f"hog: stopped on an error, and freed everything it held: {err}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        memory.free_all()
        for signum, handler in handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main())
