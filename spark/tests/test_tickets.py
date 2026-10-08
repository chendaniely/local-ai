import json
import math
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from spark import tickets

NOW = 1_800_000_000.0
BOOT = "boot-a"
TICKET = {"footprint_gib": 41.0, "available_gib": 74.0}


def issue(folder, model="coder", deadline=NOW + 60, *, now=NOW, boot_id=BOOT, **fields):
    return tickets.issue(folder, model, deadline, now=now, boot_id=boot_id, **(TICKET | fields))


def start(folder, model, *, at, boot_id=BOOT):
    """A load as the gate and launch make it: the ticket issued, claimed and marked started, all at `at`."""
    issue(folder, model, at + 60, now=at, boot_id=boot_id)
    claim = tickets.claim(folder, model, at, boot_id=boot_id)
    assert claim.ok, claim
    tickets.mark_started(folder, claim.ticket, at)
    return claim.ticket


def test_a_ticket_holds_what_the_gate_admitted(tmp_path):
    ticket_id = issue(tmp_path)
    path = tmp_path / "tickets" / "coder.json"
    ticket = json.loads(path.read_text())
    assert set(ticket) == {"model", "id", "issued_at", "deadline", "nonce", "boot_id", "footprint_gib", "available_gib"}
    assert ticket["id"] == ticket_id and ticket["model"] == "coder" and ticket["boot_id"] == BOOT
    assert (ticket["issued_at"], ticket["deadline"]) == (NOW, NOW + 60)
    assert (ticket["footprint_gib"], ticket["available_gib"]) == (41.0, 74.0)
    assert ticket["nonce"] and ticket["nonce"] != ticket_id
    assert issue(tmp_path) != ticket_id  # each ticket its own


def test_a_ticket_is_written_whole_and_only_its_owner_reads_it(tmp_path, monkeypatch):
    # The gate writes it while launch may be claiming: launch finds the whole ticket or none, never part of one.
    renamed = []
    real_replace = os.replace

    def replace(src, dst):
        renamed.append((Path(src).parent.name, Path(dst).name))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    issue(tmp_path)
    assert renamed == [("tickets", "coder.json")]
    assert [p.name for p in (tmp_path / "tickets").iterdir()] == ["coder.json"]  # no temporary file left
    assert stat.S_IMODE((tmp_path / "tickets" / "coder.json").stat().st_mode) == 0o600


@pytest.mark.parametrize("model, deadline, bad", [
    ("coder", NOW + 60, {"footprint_gib": math.nan}),
    ("coder", NOW + 60, {"available_gib": math.inf}),
    ("coder", NOW + 60, {"footprint_gib": True}),
    ("coder", NOW + 60, {"boot_id": ""}),
    ("coder", math.nan, {}),
    ("../coder", NOW + 60, {}),  # a name that isn't a model's would name a file outside tickets/
], ids=["footprint NaN", "available infinite", "footprint a bool", "no boot id", "deadline NaN", "not a model's name"])
def test_issue_refuses_a_ticket_that_isnt_one(tmp_path, model, deadline, bad):
    with pytest.raises(ValueError):
        issue(tmp_path, model, deadline, **bad)
    assert not (tmp_path / "tickets").exists() or list((tmp_path / "tickets").iterdir()) == []


def test_a_claim_uses_the_ticket_up(tmp_path):
    ticket_id = issue(tmp_path)
    claim = tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT)
    assert claim.ok and claim.code is None and claim.ticket["id"] == ticket_id
    assert claim.ticket["footprint_gib"] == 41.0 and claim.ticket["available_gib"] == 74.0
    assert list((tmp_path / "tickets").iterdir()) == []  # neither the ticket nor the claimed copy stays
    again = tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT)
    assert (again.ok, again.code, again.why, again.ticket) == (False, "no_ticket", "no admission ticket from the gate",
                                                               None)


CLAIMER = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from spark import tickets
    print("ready", flush=True)
    for line in sys.stdin:
        claim = tickets.claim(Path(sys.argv[1]), "coder", float(line), boot_id="boot-a")
        print(json.dumps({"ok": claim.ok, "code": claim.code}), flush=True)
    """
)


def test_two_claims_at_once_win_once(tmp_path):
    # llama-swap could start the same model twice: only one start may use the gate's one ticket. Two processes, each
    # waiting on a line, are told to claim together, round after round.
    children = [subprocess.Popen([sys.executable, "-c", CLAIMER, str(tmp_path)], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        assert [child.stdout.readline() for child in children] == ["ready\n", "ready\n"]
        for _ in range(25):
            issue(tmp_path)
            for child in children:
                child.stdin.write(f"{NOW}\n")
            for child in children:
                child.stdin.flush()
            results = [json.loads(child.stdout.readline()) for child in children]
            assert sorted(r["ok"] for r in results) == [False, True], results
            assert [r["code"] for r in results if not r["ok"]] == ["no_ticket"]
            assert list((tmp_path / "tickets").iterdir()) == []
    finally:
        for child in children:
            child.stdin.close()
            child.wait(timeout=30)
            child.stdout.close()


def test_an_expired_ticket_starts_nothing(tmp_path):
    issue(tmp_path, deadline=NOW - 1)
    claim = tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT)
    assert (claim.ok, claim.code, claim.ticket) == (False, "ticket_expired", None)
    assert "expired" in claim.why
    assert list((tmp_path / "tickets").iterdir()) == []
    issue(tmp_path, deadline=NOW)  # at its deadline it still holds: it expires once the deadline has passed
    assert tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT).ok


def test_a_ticket_from_another_boot_starts_nothing(tmp_path):
    # A deadline is wall-clock time, which a boot can set back: the boot id says the ticket is another boot's.
    issue(tmp_path, boot_id="boot-z")
    claim = tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT)
    assert (claim.ok, claim.code) == (False, "ticket_expired") and "another boot" in claim.why
    assert list((tmp_path / "tickets").iterdir()) == []


def test_a_ticket_for_another_model_starts_nothing(tmp_path):
    issue(tmp_path, "embed")
    claim = tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT)
    assert (claim.ok, claim.code, claim.why) == (False, "no_ticket", "no admission ticket from the gate")
    assert [p.name for p in (tmp_path / "tickets").iterdir()] == ["embed.json"]
    # and a ticket under the coder's name that is embed's own
    (tmp_path / "tickets" / "embed.json").rename(tmp_path / "tickets" / "coder.json")
    claim = tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT)
    assert (claim.ok, claim.code) == (False, "no_ticket") and "embed" in claim.why
    assert list((tmp_path / "tickets").iterdir()) == []


def _fields(**change):
    ticket = {"model": "coder", "id": "t1", "issued_at": NOW, "deadline": NOW + 60, "nonce": "n1", "boot_id": BOOT,
              "footprint_gib": 41.0, "available_gib": 74.0}
    return json.dumps({k: v for k, v in (ticket | change).items() if v is not None}).encode()


DAMAGED_TICKETS = {  # each damage, and what the refusal says of it
    "cut off": (b"{", "Expecting property name"),
    "empty": (b"", "Expecting value"),
    "not text": (b"\xff\xfe garbage", "Expecting value"),
    "a list": (b"[]", "not a JSON object"),
    "nested too deep": (b"[" * 10_000, "nested too deep"),
    "too large": (_fields(nonce="n" * 70_000), "larger than 65536 bytes"),
    "no deadline": (_fields(deadline=None), "deadline must be a finite number"),
    "deadline not a number": (_fields(deadline="soon"), "deadline must be a finite number"),
    "deadline NaN": (_fields().replace(str(NOW + 60).encode(), b"NaN"), "deadline must be a finite number"),
    "deadline infinite": (_fields().replace(str(NOW + 60).encode(), b"Infinity"), "deadline must be a finite number"),
    "deadline a bool": (_fields(deadline=True), "deadline must be a finite number"),
    # Task 11's review, I-1: an int too large for a float made math.isfinite raise OverflowError out of claim
    "deadline a huge int": (_fields(deadline=10**400), "deadline must be a finite number"),
    "deadline past int's digit limit": (_fields().replace(str(NOW + 60).encode(), b"9" * 5000), "Exceeds the limit"),
    "no boot id": (_fields(boot_id=None), "boot_id must be non-empty text"),
    "no id": (_fields(id=None), "id must be non-empty text"),
    "footprint not a number": (_fields(footprint_gib="41"), "footprint_gib must be a finite number"),
    "no available": (_fields(available_gib=None), "available_gib must be a finite number"),
}


@pytest.mark.parametrize("damage, said", DAMAGED_TICKETS.values(), ids=DAMAGED_TICKETS.keys())
def test_a_damaged_ticket_starts_nothing(tmp_path, damage, said):
    (tmp_path / "tickets").mkdir()
    (tmp_path / "tickets" / "coder.json").write_bytes(damage)
    claim = tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT)
    assert (claim.ok, claim.code, claim.ticket) == (False, "no_ticket", None)
    assert claim.why.startswith(f"no admission ticket from the gate: {tmp_path / 'tickets' / 'coder.json'} isn't one")
    assert said in claim.why
    assert list((tmp_path / "tickets").iterdir()) == []


def test_a_ticket_another_account_planted_starts_nothing(tmp_path, monkeypatch):
    # The gate and launch both run as spark: a ticket another account owns isn't the gate's. (A test can't chown, so the
    # account claiming is made another one.)
    issue(tmp_path)
    monkeypatch.setattr(tickets.os, "geteuid", lambda: os.getuid() + 1)
    claim = tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT)
    assert (claim.ok, claim.code) == (False, "no_ticket") and f"owned by uid {os.getuid()}" in claim.why
    assert list((tmp_path / "tickets").iterdir()) == []


@pytest.mark.parametrize("kind, said", [("a link to a ticket", "symbolic links"), ("a FIFO", "not a regular file"),
                                        ("a FIFO with a writer", "not a regular file"),
                                        ("a folder", "not a regular file")])
def test_a_ticket_that_isnt_a_plain_file_starts_nothing_and_never_hangs(tmp_path, kind, said):
    # Opening a FIFO waits for a writer, and reading one waits for its data: launch must never wait, so a FIFO is
    # refused, not read. A writer that holds it open with half a ticket in it changes nothing.
    folder = tmp_path / "tickets"
    folder.mkdir()
    path = folder / "coder.json"
    if kind == "a link to a ticket":
        (tmp_path / "elsewhere").mkdir()
        issue(tmp_path / "elsewhere")
        path.symlink_to(tmp_path / "elsewhere" / "tickets" / "coder.json")
    elif kind.startswith("a FIFO"):
        os.mkfifo(path)
    else:
        path.mkdir()
    writer = os.open(path, os.O_RDWR | os.O_NONBLOCK) if kind == "a FIFO with a writer" else None
    if writer is not None:
        os.write(writer, b'{"model": "coder", ')
    claim = subprocess.run([sys.executable, "-c", textwrap.dedent(
        """
        import json, sys
        from pathlib import Path
        from spark import tickets
        claim = tickets.claim(Path(sys.argv[1]), "coder", float(sys.argv[2]), boot_id="boot-a")
        print(json.dumps({"ok": claim.ok, "code": claim.code, "why": claim.why}))
        """), str(tmp_path), str(NOW)], capture_output=True, text=True, timeout=30, check=True)
    if writer is not None:
        os.close(writer)
    result = json.loads(claim.stdout)
    assert (result["ok"], result["code"]) == (False, "no_ticket") and " isn't one " in result["why"]
    assert said in result["why"]
    assert list(folder.iterdir()) == []


def test_withdraw_removes_an_unused_ticket(tmp_path):
    issue(tmp_path)
    assert tickets.withdraw(tmp_path, "coder") is True
    assert list((tmp_path / "tickets").iterdir()) == []
    assert tickets.withdraw(tmp_path, "coder") is False
    assert tickets.withdraw(tmp_path / "nothing-here", "coder") is False


def test_a_withdrawn_ticket_cant_be_claimed_and_a_claimed_one_cant_be_withdrawn(tmp_path):
    # The gate withdraws a ticket its load didn't use, and launch may be claiming it at that moment: one of the two wins.
    issue(tmp_path)
    assert tickets.withdraw(tmp_path, "coder") is True
    assert tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT).code == "no_ticket"
    issue(tmp_path)
    assert tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT).ok
    assert tickets.withdraw(tmp_path, "coder") is False


def test_mark_started_keeps_the_ticket_with_its_start_and_pid(tmp_path):
    ticket = start(tmp_path, "coder", at=NOW)
    record = json.loads((tmp_path / "started" / "coder.json").read_text())
    assert record == ticket | {"started_at": NOW, "pid": os.getpid()}
    assert stat.S_IMODE((tmp_path / "started" / "coder.json").stat().st_mode) == 0o600
    assert [p.name for p in (tmp_path / "started").iterdir()] == ["coder.json"]  # no temporary file left


def test_clear_started_removes_the_record(tmp_path):
    start(tmp_path, "coder", at=NOW)
    assert len(tickets.started(tmp_path, now=NOW, boot_id=BOOT)) == 1
    tickets.clear_started(tmp_path, "coder")
    assert tickets.started(tmp_path, now=NOW, boot_id=BOOT) == []
    tickets.clear_started(tmp_path, "coder")  # a record already gone is no error


def test_started_ignores_old_and_other_boots_records(tmp_path):
    # The brake reads these as loads in progress, so the fall they were admitted for doesn't trip it: a stale record
    # must never keep weakening its watch.
    start(tmp_path, "coder", at=NOW - 361)
    start(tmp_path, "embed", at=NOW - 10, boot_id="boot-z")
    start(tmp_path, "vision-chat", at=NOW + 60)  # dated after now: the clock went back, so its age is unknown
    ticket = start(tmp_path, "stt", at=NOW - 10)
    records = tickets.started(tmp_path, now=NOW, boot_id=BOOT)
    assert [r["model"] for r in records] == ["stt"]
    assert records[0]["pid"] == os.getpid() and records[0]["footprint_gib"] == 41.0
    assert records[0]["available_gib"] == 74.0 and records[0]["started_at"] == NOW - 10
    assert records[0]["id"] == ticket["id"]
    # A second earlier the coder's record is 360 s old, still in progress, and the stt's 9 s: oldest first.
    assert [r["model"] for r in tickets.started(tmp_path, now=NOW - 361 + tickets.STARTED_EXPIRES_S,
                                                boot_id=BOOT)] == ["coder", "stt"]


def test_started_passes_over_a_record_that_isnt_one(tmp_path, monkeypatch):
    start(tmp_path, "stt", at=NOW)
    folder = tmp_path / "started"
    record = json.loads((folder / "stt.json").read_text())
    (folder / "coder.json").write_text("{")
    (folder / "embed.json").write_text(json.dumps(record | {"model": "embed", "pid": "1"}))
    (folder / "vision-chat.json").write_text(json.dumps(record))  # the stt's record under another name
    # Task 11's review, I-1: an int too large for a float, which made started() raise OverflowError, not OSError
    (folder / "big.json").write_text(json.dumps(record | {"model": "big", "started_at": 10**400}))
    (folder / "huge.json").write_text(json.dumps(record | {"model": "huge", "footprint_gib": -10**400}))
    (folder / ".stt.json.123.abcd").write_text(json.dumps(record))  # a write not yet swapped in
    assert [r["model"] for r in tickets.started(tmp_path, now=NOW, boot_id=BOOT)] == ["stt"]
    monkeypatch.setattr(tickets.os, "geteuid", lambda: os.getuid() + 1)  # every record another account's
    assert tickets.started(tmp_path, now=NOW, boot_id=BOOT) == []


def test_started_with_no_folder_is_none(tmp_path):
    assert tickets.started(tmp_path / "nothing-here", now=NOW, boot_id=BOOT) == []


def test_started_expires_at_twice_llama_swaps_health_check_timeout():
    assert tickets.STARTED_EXPIRES_S == 360  # Task 12's test ties it to render.HEALTH_CHECK_TIMEOUT_S


def test_a_claim_sweeps_up_what_launches_no_longer_running_left(tmp_path):
    # Task 11's review: a launch killed between its rename and its removal leaves `.<model>.json.claim-<pid>-<hex>`
    # behind, and a folder in a ticket's place can't be removed. A later claim sweeps those whose launch isn't running;
    # never one whose launch still is, which may be reading it, nor a write of the gate's not yet swapped in.
    folder = tmp_path / "tickets"
    folder.mkdir()
    dead = int(subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True,
                              check=True).stdout)
    for name in (f".coder.json.claim-{dead}-0123abcd", f".embed.json.claim-{dead}-0123abce",
                 ".coder.json.claim-99999999999999999999-0123abcf"):
        (folder / name).write_text("{}")
    (folder / f".stt.json.claim-{dead}-0123abd0").mkdir()
    kept = {f".coder.json.claim-{os.getpid()}-0123abd1", ".embed.json.4242.0123abd2", f".x.claim-{dead}"}
    for name in kept:
        (folder / name).write_text("{}")
    issue(tmp_path)
    assert tickets.claim(tmp_path, "coder", NOW, boot_id=BOOT).ok
    assert {p.name for p in folder.iterdir()} == kept
    assert tickets.claim(tmp_path, "embed", NOW, boot_id=BOOT).code == "no_ticket"  # with no ticket, it sweeps too
    assert {p.name for p in folder.iterdir()} == kept


def test_root_never_writes_a_record(tmp_path, monkeypatch):
    # Task 11's review: the launch folder is spark's, and root never writes through a path spark controls. Launch
    # refuses root first; this covers every other writer of a ticket, a started record or a refusal.
    monkeypatch.setattr(tickets.os, "geteuid", lambda: 0)
    with pytest.raises(PermissionError, match="root"):
        tickets.write_whole(tmp_path / "tickets" / "coder.json", {"model": "coder"})
    with pytest.raises(PermissionError):
        issue(tmp_path)
    with pytest.raises(PermissionError):
        tickets.mark_started(tmp_path, {"model": "coder"}, NOW)
    assert list(tmp_path.iterdir()) == []  # not even a folder
