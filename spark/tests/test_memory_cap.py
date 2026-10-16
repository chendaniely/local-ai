"""The memory cap on a test run on the Spark (memory_cap.py, conftest.py's refusal): the cgroup check over fake cgroup
trees, the refusal's words, and conftest stopping a run it refuses before any test runs."""

from pathlib import Path

import pytest

import conftest
import memory_cap
from memory_cap import CAP_BYTES, CAP_GIB, capped, refusal

GIB = 1024**3
LEAF = "/user.slice/user-1000.slice/user@1000.service/app.slice/run-r1.scope"


def tree(base: Path, **levels: tuple[str | None, str | None]) -> str:
    """A fake cgroup tree under `base`, on LEAF's path: each keyword names a level by its last part (`root` for the
    root itself), with its memory.max and memory.swap.max, None for a file that isn't there. Every other level has
    neither file, as the real root has neither. Returns /proc/self/cgroup's text for a process in the leaf."""
    parts = LEAF.strip("/").split("/")
    paths = {"root": base} | {part: base.joinpath(*parts[:i + 1]) for i, part in enumerate(parts)}
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    for name, (memory, swap) in levels.items():
        if memory is not None:
            (paths[name] / "memory.max").write_text(memory + "\n")
        if swap is not None:
            (paths[name] / "memory.swap.max").write_text(swap + "\n")
    return f"0::{LEAF}\n"


def test_the_cap_is_4_gib():
    # Dan's size, 2026-10-15. The Makefile reads CAP_GIB's line (test_makefile.py keeps the two in step).
    assert (CAP_GIB, CAP_BYTES) == (4, 4 * GIB)


def test_a_run_capped_at_its_leaf_with_no_swap_is_capped(tmp_path):
    # What `make test`'s systemd-run line makes: the scope itself holds both limits.
    proc = tree(tmp_path, **{"run-r1.scope": (str(4 * GIB), "0")})
    assert capped(proc, tmp_path)


def test_a_run_capped_at_an_ancestor_with_no_swap_there_is_capped(tmp_path):
    proc = tree(tmp_path, **{"run-r1.scope": ("max", "max"), "app.slice": (str(2 * GIB), "0")})
    assert capped(proc, tmp_path)


def test_swap_turned_off_above_the_caps_level_counts(tmp_path):
    # "memory.swap.max of 0 at that level or an ancestor": the ancestor's 0 holds the whole subtree.
    proc = tree(tmp_path, **{"run-r1.scope": (str(4 * GIB), "max"), "user@1000.service": ("max", "0")})
    assert capped(proc, tmp_path)


def test_swap_turned_off_only_below_the_caps_level_doesnt_count(tmp_path):
    # Dan's rule as approved: the swap limit at the cap's level or above it. Stricter than the kernel needs, so it can
    # only refuse a run that was capped, never pass one that wasn't.
    proc = tree(tmp_path, **{"run-r1.scope": ("max", "0"), "app.slice": (str(4 * GIB), "max")})
    assert not capped(proc, tmp_path)


def test_max_everywhere_is_uncapped(tmp_path):
    # A plain shell's run: tmux's scope and every level above it say max (seen on the Spark, 2026-10-15).
    proc = tree(tmp_path, **{name: ("max", "max") for name in
                             ("run-r1.scope", "app.slice", "user@1000.service", "user-1000.slice", "user.slice")})
    assert not capped(proc, tmp_path)


def test_a_cap_with_swap_left_on_is_uncapped(tmp_path):
    # Swap would let a runaway grow past the cap, and thrash the disk the models load from.
    proc = tree(tmp_path, **{"run-r1.scope": (str(4 * GIB), "max")})
    assert not capped(proc, tmp_path)


@pytest.mark.parametrize("missing", ["memory.max", "memory.swap.max"])
def test_a_missing_file_is_uncapped(tmp_path, missing):
    # No memory controller delegated to the scope: neither file exists there.
    proc = tree(tmp_path, **{"run-r1.scope": (None if missing == "memory.max" else str(4 * GIB),
                                              None if missing == "memory.swap.max" else "0")})
    assert not capped(proc, tmp_path)


def test_an_unreadable_file_is_uncapped(tmp_path):
    proc = tree(tmp_path, **{"run-r1.scope": (str(4 * GIB), "0")})
    limit = tmp_path / LEAF.strip("/") / "memory.max"
    limit.chmod(0)
    try:
        assert not capped(proc, tmp_path)
    finally:
        limit.chmod(0o644)


@pytest.mark.parametrize("limit", [str(4 * GIB + 4096), str(8 * GIB), "a lot", ""])
def test_a_limit_above_4_gib_or_unreadable_as_a_number_is_uncapped(tmp_path, limit):
    proc = tree(tmp_path, **{"run-r1.scope": (limit, "0")})
    assert not capped(proc, tmp_path)


@pytest.mark.parametrize("limit", [4 * GIB, GIB, 512 * 1024**2])
def test_a_limit_at_or_below_4_gib_is_a_cap(tmp_path, limit):
    proc = tree(tmp_path, **{"run-r1.scope": (str(limit), "0")})
    assert capped(proc, tmp_path)


@pytest.mark.parametrize("proc", ["", "12:memory:/user.slice\n", "0::/../../etc\n", "0::/a\n0::/b\n"],
                         ids=["empty", "cgroup v1 only", "outside the root", "two v2 lines"])
def test_a_cgroup_line_it_cant_place_is_uncapped(tmp_path, proc):
    tree(tmp_path, **{"run-r1.scope": (str(4 * GIB), "0")}, root=(str(GIB), "0"))
    assert not capped(proc, tmp_path)


def test_the_refusal_is_one_plain_message_naming_the_command_and_the_cap():
    words = memory_cap.REFUSAL
    assert "4 GiB" in words and "make test" in words and 'make test ARGS="spark/tests/' in words
    assert "\n" not in words


def test_on_the_spark_an_uncapped_run_is_refused(tmp_path):
    spark = tmp_path / "opt-local-ai"
    spark.mkdir()
    (tmp_path / "self-cgroup").write_text(tree(tmp_path / "fs", **{"run-r1.scope": ("max", "max")}))
    assert refusal(spark, tmp_path / "self-cgroup", tmp_path / "fs") == memory_cap.REFUSAL


def test_on_the_spark_a_capped_run_goes_ahead(tmp_path):
    spark = tmp_path / "opt-local-ai"
    spark.mkdir()
    (tmp_path / "self-cgroup").write_text(tree(tmp_path / "fs", **{"run-r1.scope": (str(4 * GIB), "0")}))
    assert refusal(spark, tmp_path / "self-cgroup", tmp_path / "fs") is None


def test_on_the_spark_a_run_that_cant_read_its_own_cgroup_is_refused(tmp_path):
    spark = tmp_path / "opt-local-ai"
    spark.mkdir()
    tree(tmp_path / "fs", **{"run-r1.scope": (str(4 * GIB), "0")})
    assert refusal(spark, tmp_path / "no-such-file", tmp_path / "fs") == memory_cap.REFUSAL


def test_off_the_spark_nothing_is_refused(tmp_path):
    # The Mac and CI's runners have no /opt/local-ai: their runs go ahead as before, capped or not.
    (tmp_path / "self-cgroup").write_text(tree(tmp_path / "fs", **{"run-r1.scope": ("max", "max")}))
    assert refusal(tmp_path / "absent", tmp_path / "self-cgroup", tmp_path / "fs") is None


def test_the_real_check_reads_the_real_paths():
    # No variable or flag moves them: conftest calls refusal() as it is.
    assert refusal.__defaults__ == (Path("/opt/local-ai"), Path("/proc/self/cgroup"), Path("/sys/fs/cgroup"))


# conftest checks at session start, before collection, and again once collection is done, before any test runs: a run
# from the repo's root that names no path finds conftest.py only while it collects, after session start, and so ran all
# 3106 tests uncapped (seen 2026-10-15, before the second check).
HOOKS = ["pytest_sessionstart", "pytest_collection_finish"]


@pytest.mark.parametrize("hook", HOOKS)
def test_conftest_stops_a_refused_run_before_any_test_with_a_failing_code(monkeypatch, hook):
    monkeypatch.setattr(memory_cap, "refusal", lambda: "the refusal's words")
    with pytest.raises(pytest.exit.Exception) as stopped:
        getattr(conftest, hook)(None)
    assert stopped.value.msg == "the refusal's words"
    assert stopped.value.returncode == pytest.ExitCode.USAGE_ERROR


@pytest.mark.parametrize("hook", HOOKS)
def test_conftest_lets_a_run_it_doesnt_refuse_go_ahead(monkeypatch, hook):
    monkeypatch.setattr(memory_cap, "refusal", lambda: None)
    assert getattr(conftest, hook)(None) is None
