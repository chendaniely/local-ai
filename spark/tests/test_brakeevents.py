"""Task 13: the brake's steps, recorded for the gate in `brake/events.jsonl`, keyed by boot id and sequence number.
Every time and boot id here is a stand-in."""

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from spark import brakeevents
from spark.brakeevents import BrakeEvent, append_event, episode_for, next_seq, read_events
from spark.hold import Hold

T = 1_800_000_000.0
B1, B2 = "b1", "b2"


def event(kind: str = "unload", *, boot: str = B1, episode: int = 1, model: str | None = "coder",
          state: str | None = "idle", at: float = T, sent_by_brake: bool = False) -> BrakeEvent:
    """An event as the brake builds it; append_event gives it its seq."""
    return BrakeEvent(seq=0, at=at, boot_id=boot, episode=episode, kind=kind, model=model, state=state,
                      available_gib=19.6, line_gib=20.0, sent_by_brake=sent_by_brake)


@pytest.fixture
def path(tmp_path) -> Path:
    return tmp_path / brakeevents.EVENTS_FILE


def keys(events: list[BrakeEvent]) -> list[tuple[str, int]]:
    return [(e.boot_id, e.seq) for e in events]


def test_events_append_whole_lines_and_continue_their_seq(path):
    assert next_seq(path) == 1  # no file yet
    fired = append_event(path, event("fired", model=None, state=None))
    assert fired.seq == 1 and fired.kind == "fired"
    assert [append_event(path, event()).seq for _ in range(2)] == [2, 3]
    assert next_seq(path) == 4
    text = path.read_text()
    assert text.endswith("\n") and len(text.splitlines()) == 3
    assert [json.loads(line)["seq"] for line in text.splitlines()] == [1, 2, 3]
    assert read_events(path, None) == [fired, *read_events(path, (B1, 1))]
    assert keys(read_events(path, None)) == [(B1, 1), (B1, 2), (B1, 3)]


def test_read_events_after_a_point(path):
    for n in range(3):
        append_event(path, event(at=T + n))
    assert keys(read_events(path, (B1, 2))) == [(B1, 3)]
    assert read_events(path, (B1, 3)) == []
    assert read_events(path.with_name("nothing.jsonl"), None) == []


def test_a_file_started_again_still_reads(path):
    for _ in range(3):
        append_event(path, event())
    path.unlink()
    again = append_event(path, event(boot=B2))
    assert again.seq == 1
    assert read_events(path, (B1, 3)) == [again]


def test_a_file_started_again_within_a_boot_still_reads(path):
    # The gate keeps each event's `at` with its key (the controller's ruling at Task 13's review): a file removed by
    # hand within a boot starts its seq from 1 again, and an event of the new file that comes to the same seq isn't
    # taken for the gate's place, so none of the new file's events is hidden.
    for n in range(3):
        append_event(path, event(at=T + n))
    place = read_events(path, None)[-1]
    assert read_events(path, (B1, 3, place.at)) == []
    path.unlink()
    again = [append_event(path, event(at=T + 100 + n)) for n in range(3)]
    assert [e.seq for e in again] == [1, 2, 3]
    assert read_events(path, (B1, 3, place.at)) == again
    assert keys(read_events(path, (B1, 2, again[1].at))) == [(B1, 3)]


def test_seq_continues_across_boots_in_one_file(path):
    append_event(path, event(boot=B1))
    later = append_event(path, event(boot=B2))
    assert later.seq == 2
    assert read_events(path, (B1, 1)) == [later] and read_events(path, (B2, 2)) == []


WRITER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from spark.brakeevents import BrakeEvent, append_event
    path, me, go = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    Path(sys.argv[3] + "." + me).touch()
    while not go.exists():
        time.sleep(0.001)
    for n in range(50):
        append_event(path, BrakeEvent(0, n, me, 1, "unload", "coder", "idle", 19.6, 20.0, False))
    """
)


def test_two_writers_never_share_a_seq(path, tmp_path):
    # The brake and the S05 drill's --once (Task 25) can append at once: each seq comes from the file under its lock.
    go = tmp_path / "go"
    writers = [subprocess.Popen([sys.executable, "-c", WRITER, str(path), name, str(go)]) for name in ("w1", "w2")]
    try:
        deadline = time.monotonic() + 30
        while not all(Path(f"{go}.{name}").exists() for name in ("w1", "w2")):
            assert time.monotonic() < deadline, "the writers didn't start"
            time.sleep(0.01)
        go.touch()
        assert [w.wait(timeout=60) for w in writers] == [0, 0]
    finally:
        for w in writers:
            w.kill()
    lines = path.read_text().splitlines()
    assert len(lines) == 100
    assert sorted(json.loads(line)["seq"] for line in lines) == list(range(1, 101))
    assert [json.loads(line)["seq"] for line in lines] == list(range(1, 101))  # in the file's order, too


def test_the_episode_is_the_holds_or_the_next(path):
    assert episode_for(path, None, boot_id=B1) == 1  # no file, no hold
    append_event(path, event(boot="b0", episode=5))  # another boot's episodes don't count
    assert episode_for(path, None, boot_id=B1) == 1
    append_event(path, event("fired", episode=1, model=None, state=None))
    append_event(path, event(episode=2))
    standing = Hold("2027-01-15T08:00:00", "19.6 GiB available", (), boot_id=B1, episode=2)
    assert episode_for(path, standing, boot_id=B1) == 2
    assert episode_for(path, None, boot_id=B1) == 3
    assert episode_for(path, Hold("t", "r", (), boot_id="b0", episode=7), boot_id=B1) == 3
    assert episode_for(path, Hold("t", "r", ()), boot_id=B1) == 3  # Phase 1's hold: another boot's


def test_a_half_written_last_line_is_left_for_next_time(path):
    append_event(path, event())
    append_event(path, event())
    third = json.dumps({**json.loads(path.read_text().splitlines()[-1]), "seq": 3})
    with open(path, "a") as f:
        f.write(third[:20])  # a write in progress
    assert keys(read_events(path, None)) == [(B1, 1), (B1, 2)]
    with open(path, "a") as f:
        f.write(third[20:])  # all of it but its newline: it reads as JSON, and is still not a whole line
    assert keys(read_events(path, None)) == [(B1, 1), (B1, 2)]
    assert read_events(path, (B1, 2)) == []
    with open(path, "a") as f:
        f.write("\n")
    assert keys(read_events(path, (B1, 2))) == [(B1, 3)]


def test_an_append_after_a_torn_line_starts_a_line_of_its_own(path):
    # A crash part-way through a write leaves a line with no newline: the next append ends it first, so its own line
    # stays whole, and the torn one is passed over.
    append_event(path, event())
    with open(path, "a") as f:
        f.write('{"seq": 2, "at": 18')
    written = append_event(path, event())
    assert written.seq == 2
    assert keys(read_events(path, None)) == [(B1, 1), (B1, 2)]
    assert path.read_text().endswith("\n")


def test_a_damaged_line_is_passed_over(path):
    append_event(path, event())
    with open(path, "a") as f:
        f.write("not json\n")
        f.write(json.dumps({"seq": 9, "kind": "unload"}) + "\n")  # not a whole event
        f.write(json.dumps({**json.loads(path.read_text().splitlines()[0]), "seq": 10, "state": "busy"}) + "\n")
        f.write(json.dumps({**json.loads(path.read_text().splitlines()[0]), "seq": 11, "at": "noon"}) + "\n")
    good = append_event(path, event())
    assert good.seq == 2
    assert keys(read_events(path, None)) == [(B1, 1), (B1, 2)]


def test_an_event_the_reader_would_pass_over_is_never_written(path):
    with pytest.raises(ValueError, match="state"):
        append_event(path, event(state="busy"))
    with pytest.raises(ValueError, match="kind"):
        append_event(path, event("stopped"))
    with pytest.raises(ValueError, match="available_gib"):
        append_event(path, BrakeEvent(0, T, B1, 1, "unload", "coder", "idle", float("nan"), 20.0, False))
    assert not path.exists()


def test_root_never_appends(path, monkeypatch):
    monkeypatch.setattr(brakeevents.os, "geteuid", lambda: 0)
    with pytest.raises(PermissionError, match="root"):
        append_event(path, event())
    assert not path.exists()


def test_the_events_file_is_never_followed_through_a_link(path, tmp_path):
    target = tmp_path / "elsewhere.jsonl"
    path.symlink_to(target)
    with pytest.raises(OSError):
        append_event(path, event())
    assert not target.exists()
    with pytest.raises(OSError):
        read_events(path, None)
    os.unlink(path)
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular file"):
        read_events(path, None)  # never waits for a writer
