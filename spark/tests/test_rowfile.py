"""The row file writer: JSONL rows from a thread of its own, never in a request's way. Its `write`, `statvfs` and
clocks are injected; no test patches `os`, and none leaves a thread running past its teardown."""

from __future__ import annotations

import json
import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark.rowfile import RefusedLimit, RowFile

OCT_1_0300Z = 1790823600.0  # 2026-10-01T03:00:00Z, still 2026-09-30 in Vancouver
RETRY_S = 30.0


class Mono:
    """A monotonic clock the test moves."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.005)


def settle(rf: RowFile) -> None:
    wait_for(rf._drained)


def raw_lines(path: Path) -> list[str]:
    return [line for line in path.read_text().split("\n") if line]


def free(gib: int):
    """A statvfs returning `gib` GiB free."""
    return lambda path: SimpleNamespace(f_bavail=gib, f_frsize=1 << 30)


@pytest.fixture
def made(tmp_path):
    """Builds RowFiles over tmp_path/"rows"; closes and joins every one at teardown."""
    made_files: list[RowFile] = []
    release: list[threading.Event] = []

    def make(folder: Path | None = None, **kwargs) -> RowFile:
        kwargs.setdefault("clock", lambda: OCT_1_0300Z)
        kwargs.setdefault("statvfs", free(100))
        rf = RowFile(folder if folder is not None else tmp_path, "usage", **kwargs)
        made_files.append(rf)
        return rf

    make.release = release  # events to set before closing, so a stuck write lets go
    yield make
    for event in release:
        event.set()
    for rf in made_files:
        rf.close(5.0)
        rf._thread.join(5.0)
        assert not rf._thread.is_alive()


def test_rows_append_one_line_each_to_the_months_file_by_utc(made, tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "America/Vancouver")
    time.tzset()
    try:
        rf = made()
        rf.put({"v": 1, "p": 1}, OCT_1_0300Z)
        rf.put({"v": 1, "p": 2}, OCT_1_0300Z + 60)
        rf.put({"v": 1, "p": 3}, OCT_1_0300Z - 4 * 86400)  # 2026-09-27, the month before
        settle(rf)
    finally:
        monkeypatch.undo()
        time.tzset()
    assert raw_lines(tmp_path / "usage-2026-10.jsonl") == ['{"v":1,"p":1}', '{"v":1,"p":2}']
    assert raw_lines(tmp_path / "usage-2026-09.jsonl") == ['{"v":1,"p":3}']


@pytest.mark.parametrize("umask", [0o000, 0o022, 0o077])
def test_the_files_mode_is_0640_under_any_umask(made, tmp_path, umask):
    old = os.umask(umask)
    try:
        folder = tmp_path / f"m{umask}"
        folder.mkdir()
        rf = made(folder)
        rf.put({"v": 1}, OCT_1_0300Z)
        settle(rf)
    finally:
        os.umask(old)
    assert stat.S_IMODE((folder / "usage-2026-10.jsonl").stat().st_mode) == 0o640


def test_a_missing_folder_is_counting_off_at_start_and_never_raises(made, tmp_path):
    folder = tmp_path / "gone"
    mono = Mono()
    offs: list[tuple[float, str]] = []
    ons: list[int] = []
    rf = made(
        folder,
        monotonic=mono,
        retry_s=RETRY_S,
        on_off=lambda since, why: offs.append((since, why)),
        on_on=lambda: ons.append(1),
    )
    wait_for(lambda: rf.off_since() is not None)
    assert rf.off_since() == OCT_1_0300Z
    assert [why for _, why in offs] == ["FileNotFoundError"]

    rf.put({"v": 1, "p": 1}, OCT_1_0300Z)
    settle(rf)
    folder.mkdir()
    mono.now += RETRY_S - 1  # not yet: no reopen before retry_s
    rf.put({"v": 1, "p": 2}, OCT_1_0300Z)
    settle(rf)
    assert not (folder / "usage-2026-10.jsonl").exists()
    assert rf.off_since() is not None

    mono.now += 2  # now past retry_s
    rf.put({"v": 1, "p": 3}, OCT_1_0300Z)
    settle(rf)
    assert raw_lines(folder / "usage-2026-10.jsonl") == ['{"v":1,"p":3,"dropped":2}']
    assert rf.off_since() is None
    assert ons == [1]
    assert [why for _, why in offs] == ["FileNotFoundError"]  # one call for the whole spell


def test_a_short_write_leaves_one_torn_line_and_the_next_row_whole(made, tmp_path):
    mono = Mono()
    calls: list[int] = []
    offs: list[str] = []

    def write(fd: int, data: bytes) -> int:
        calls.append(1)
        if len(calls) == 1:
            return os.write(fd, data[:7])  # short
        return os.write(fd, data)

    rf = made(monotonic=mono, retry_s=RETRY_S, write=write, on_off=lambda s, why: offs.append(why))
    rf.put({"v": 1, "p": 1}, OCT_1_0300Z)
    settle(rf)
    assert rf.off_since() is not None
    assert offs == ["ShortWrite"]
    mono.now += RETRY_S + 1
    rf.put({"v": 1, "p": 2}, OCT_1_0300Z)
    settle(rf)
    assert raw_lines(tmp_path / "usage-2026-10.jsonl") == ['{"v":1,', '{"v":1,"p":2,"dropped":1}']
    assert rf.off_since() is None


def test_an_error_names_only_its_class_never_its_text(made, tmp_path):
    offs: list[str] = []

    def write(fd: int, data: bytes) -> int:
        raise OSError(28, "No space left on device: SECRET-PATH")

    rf = made(write=write, on_off=lambda s, why: offs.append(why))
    rf.put({"v": 1}, OCT_1_0300Z)
    settle(rf)
    assert offs == ["OSError"]


def test_a_row_that_wont_serialize_is_dropped_and_the_writer_lives(made, tmp_path):
    rf = made()
    rf.put({"v": 1, "p": b"bytes"}, OCT_1_0300Z)
    rf.put({"v": 1, "p": float("nan")}, OCT_1_0300Z)
    rf.put({"v": 1, "p": 3}, OCT_1_0300Z)
    settle(rf)
    lines = raw_lines(tmp_path / "usage-2026-10.jsonl")
    assert [json.loads(line) for line in lines] == [{"v": 1, "p": 3, "dropped": 2}]
    assert rf._thread.is_alive()


def test_a_full_queue_drops_without_blocking(made, tmp_path):
    entered = threading.Event()
    gate = threading.Event()
    made.release.append(gate)

    def write(fd: int, data: bytes) -> int:
        entered.set()
        gate.wait(10)
        return os.write(fd, data)

    rf = made(write=write, queue_bytes=30)
    rf.put({"v": 1, "p": 1}, OCT_1_0300Z)
    assert entered.wait(5)  # the thread holds row 1 inside write; the queue is empty

    def burst() -> None:
        for p in range(2, 6):
            rf.put({"v": 1, "p": p}, OCT_1_0300Z)

    t = threading.Thread(target=burst)
    t.start()
    t.join(1.0)
    assert not t.is_alive(), "put blocked on a full queue"

    gate.set()
    path = tmp_path / "usage-2026-10.jsonl"
    wait_for(lambda: path.exists() and len(raw_lines(path)) == 3)  # row 1 and the two that fit
    settle(rf)
    rf.put({"v": 1, "p": 99}, OCT_1_0300Z)
    settle(rf)
    rows = [json.loads(line) for line in raw_lines(path)]
    assert rows[-1]["p"] == 99
    assert len(rows) - 1 + sum(row.get("dropped", 0) for row in rows) == 5


def test_below_the_free_floor_counting_turns_off_with_why_disk(made, tmp_path):
    mono = Mono()
    level = {"gib": 5}
    offs: list[str] = []
    ons: list[int] = []
    rf = made(
        monotonic=mono,
        retry_s=RETRY_S,
        statvfs=lambda path: SimpleNamespace(f_bavail=level["gib"], f_frsize=1 << 30),
        free_floor=20 << 30,
        on_off=lambda s, why: offs.append(why),
        on_on=lambda: ons.append(1),
    )
    wait_for(lambda: rf.off_since() is not None)
    assert offs == ["disk"]
    rf.put({"v": 1, "p": 1}, OCT_1_0300Z)
    settle(rf)
    assert not (tmp_path / "usage-2026-10.jsonl").exists()
    level["gib"] = 50
    mono.now += RETRY_S + 1
    rf.put({"v": 1, "p": 2}, OCT_1_0300Z)
    settle(rf)
    assert raw_lines(tmp_path / "usage-2026-10.jsonl") == ['{"v":1,"p":2,"dropped":1}']
    assert ons == [1]
    assert offs == ["disk"]


def test_every_thousand_rows_the_free_floor_is_checked_again(made, tmp_path):
    level = {"gib": 50}
    offs: list[str] = []
    rf = made(
        statvfs=lambda path: SimpleNamespace(f_bavail=level["gib"], f_frsize=1 << 30),
        free_floor=20 << 30,
        queue_bytes=1 << 20,
        on_off=lambda s, why: offs.append(why),
    )
    rf.put({"v": 1}, OCT_1_0300Z)
    settle(rf)
    assert rf.off_since() is None
    level["gib"] = 1
    for _ in range(1000):
        rf.put({"v": 1}, OCT_1_0300Z)
    settle(rf)
    assert offs == ["disk"]
    assert rf.off_since() is not None


def test_close_returns_within_its_bound_with_a_stuck_write(made, tmp_path):
    entered = threading.Event()
    gate = threading.Event()
    made.release.append(gate)

    def write(fd: int, data: bytes) -> int:
        entered.set()
        gate.wait(10)
        return os.write(fd, data)

    rf = made(write=write)
    rf.put({"v": 1}, OCT_1_0300Z)
    assert entered.wait(5)
    started = time.monotonic()
    rf.close(0.2)
    assert time.monotonic() - started < 1.0
    assert rf._thread.is_alive()  # still stuck; the fixture lets go and joins it


def test_close_drains_what_is_queued(made, tmp_path):
    rf = made()
    for p in range(3):
        rf.put({"v": 1, "p": p}, OCT_1_0300Z)
    rf.close(5.0)
    assert len(raw_lines(tmp_path / "usage-2026-10.jsonl")) == 3


def test_put_after_close_never_raises(made, tmp_path):
    rf = made()
    rf.close(5.0)
    rf.put({"v": 1}, OCT_1_0300Z)
    rf.put({"v": b"x"}, OCT_1_0300Z)
    rf.put("not a dict", OCT_1_0300Z)  # type: ignore[arg-type]
    rf.close(0.1)  # twice is fine
    assert not (tmp_path / "usage-2026-10.jsonl").exists() or raw_lines(tmp_path / "usage-2026-10.jsonl") == []


def test_refused_limit_folds_past_its_rate():
    limit = RefusedLimit(per_minute=3)
    assert [limit.allow("k", 100.0 + i) for i in range(5)] == [True, True, True, False, False]
    assert limit.allow("other", 101.0) is True  # keys are independent
    assert limit.folded("k") == 2
    assert limit.folded("k") == 0  # reset once read
    assert limit.allow("k", 105.0) is False
    assert limit.allow("k", 161.0) is True  # a new minute
    assert limit.folded("k") == 1
    assert limit.folded("never-seen") == 0
