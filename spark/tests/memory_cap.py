"""The memory cap on a test run on the Spark (Dan's decision, 2026-10-15).

A test run on the Spark shares the one unified memory pool with the models, and a runaway one can freeze the GB10. On
2026-10-15 a build session's pytest run grew to 107,882 MiB of RSS, likely a mutation that removed a read guard so a
test's link to /dev/zero was read whole. It took the box from 49 GiB available to 12; the brake unloaded three models,
which couldn't stop it, and earlyoom ended it. So on the Spark, `make test` runs pytest in a user scope capped at
CAP_GIB with no swap, and the kernel kills only that run if it passes the cap; and conftest.py refuses a run without
such a cap, for every account, before any test runs. No variable or flag turns the check off.

The Spark is where /opt/local-ai exists. The Mac and CI's runners have none, and their runs go ahead as before."""

from pathlib import Path, PurePosixPath

# The cap, in GiB, on one test run on the Spark. A full suite run peaked at 346 MiB (363,065,344 bytes, page cache
# included, in a user scope, measured on the Spark on 2026-10-15), so 4 GiB is more than ten times a normal run: room
# for the suite to grow, and far below what the models need. The Makefile reads this line with sed, for `make test`'s
# MemoryMax=, so it stays `CAP_GIB = ` and digits, with nothing after them (test_makefile.py checks the two agree).
CAP_GIB = 4
CAP_BYTES = CAP_GIB * 1024**3

REFUSAL = (f"On the Spark a test run must be capped at {CAP_GIB} GiB of memory, and this one isn't: run the tests with "
           'make test from the repo\'s root, or make test ARGS="spark/tests/test_x.py -k name" for some of them.')


def _limit(path: Path) -> int | None:
    """A cgroup limit file's value in bytes; None for `max`, or a file that is missing, unreadable or not a number."""
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def capped(proc_cgroup: str, cgroup_root: Path, cap_bytes: int = CAP_BYTES) -> bool:
    """Whether a process whose /proc/self/cgroup reads `proc_cgroup` runs capped: some cgroup on the path from its own
    up to the root has memory.max at or below `cap_bytes`, and memory.swap.max of 0 at that level or above it. A
    cgroup v2 line it can't place (none, two, one that climbs out with `..`) is uncapped."""
    paths = [line.removeprefix("0::") for line in proc_cgroup.splitlines() if line.startswith("0::")]
    if len(paths) != 1 or not paths[0].startswith("/"):
        return False
    parts = PurePosixPath(paths[0]).parts[1:]
    if ".." in parts:
        return False
    levels = [cgroup_root.joinpath(*parts[:depth]) for depth in range(len(parts), -1, -1)]  # its own first
    for at, level in enumerate(levels):
        memory = _limit(level / "memory.max")
        if memory is not None and memory <= cap_bytes and any(
                _limit(above / "memory.swap.max") == 0 for above in levels[at:]):
            return True
    return False


def refusal(spark_dir: Path = Path("/opt/local-ai"), proc_cgroup: Path = Path("/proc/self/cgroup"),
            cgroup_root: Path = Path("/sys/fs/cgroup")) -> str | None:
    """REFUSAL on the Spark when this process isn't capped; None when it is, and off the Spark. conftest.py calls it
    with these defaults; the tests give it fakes."""
    if not spark_dir.exists():
        return None
    try:
        text = proc_cgroup.read_text()
    except OSError:
        return REFUSAL
    return None if capped(text, cgroup_root) else REFUSAL
