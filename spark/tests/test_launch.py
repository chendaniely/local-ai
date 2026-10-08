import json
import os
import re
import stat
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from spark import launch, tickets
from spark.admission import Decision, admit
from spark.hold import Hold, read_hold, release_hold, write_hold
from spark.memory import MemInfo, boot_id, parse_meminfo
from spark.registry import load_registry

FIXTURE = Path(__file__).parent / "fixtures" / "models.yaml"
MEMINFO = "MemTotal:       127622144 kB\nMemFree:  1024 kB\nMemAvailable:   73400320 kB\n"
KIB_PER_GIB = 1024 * 1024
SHOWN = re.compile(r"needs (\S+) GiB, (\S+) GiB available(?:, capped at what the GPU can allocate)?, "
                   r"(\S+) GiB reserve kept: (\S+) GiB short")


def test_parse_meminfo_in_gib():
    mem = parse_meminfo(MEMINFO)
    assert round(mem.total_gib, 1) == 121.7
    assert mem.available_gib == 70.0


def test_parse_meminfo_needs_both_fields():
    with pytest.raises(ValueError, match="MemAvailable"):
        parse_meminfo("MemTotal: 1 kB\n")
    with pytest.raises(ValueError, match="MemTotal"):
        parse_meminfo("MemAvailable: 1 kB\n")


def test_meminfo_reads_memfree_and_cached():
    # /proc/meminfo's own order and padding; SwapCached, right after Cached, is a different figure.
    text = ("MemTotal:       127622144 kB\nMemFree:         2097152 kB\nMemAvailable:   73400320 kB\n"
            "Buffers:           65536 kB\nCached:         62914560 kB\nSwapCached:            0 kB\n")
    mem = parse_meminfo(text)
    assert (mem.memfree_gib, mem.cached_gib) == (2.0, 60.0)
    assert mem.available_gib == 70.0
    bare = parse_meminfo("MemTotal:       127622144 kB\nMemAvailable:   73400320 kB\n")
    assert (bare.memfree_gib, bare.cached_gib) == (None, None)
    assert round(bare.total_gib, 1) == 121.7
    assert bare.available_gib == 70.0


def test_boot_id_is_read_stripped(tmp_path):
    path = tmp_path / "boot_id"
    path.write_text("abc\n")
    assert boot_id(path) == "abc"


def test_admit_fits():
    reg = load_registry(FIXTURE)
    assert admit(reg.models["coder"], MemInfo(121.7, 70.0), reg.budget, None).ok


def test_admit_refuses_when_it_would_eat_the_reserve():
    reg = load_registry(FIXTURE)
    decision = admit(reg.models["coder"], MemInfo(121.7, 40.0), reg.budget, None)
    assert not decision.ok
    assert decision.reason == "needs 28.0 GiB, 40.0 GiB available, 24 GiB reserve kept: 12.0 GiB short"


def test_admit_counts_no_more_memory_than_the_gpu_can_allocate():
    # MemAvailable can be above what CUDA can allocate (reported near 102 GiB), and overcommitting can
    # hard-freeze a GB10: the fit uses the lower of the two.
    reg = load_registry(FIXTURE)
    big = replace(reg.models["coder"], footprint_gib=90)
    decision = admit(big, MemInfo(121.7, 118.0), reg.budget, None)
    assert not decision.ok  # 118 − 24 = 94 would fit; 102 − 24 = 78 doesn't
    assert decision.reason == ("needs 90.0 GiB, 102.0 GiB available, capped at what the GPU can allocate, "
                               "24 GiB reserve kept: 12.0 GiB short")


@pytest.mark.parametrize("footprint, available", [(28, 52.0), (27.6, 51.6), (2.5, 26.5), (28.3, 52.3)])
def test_admit_takes_a_model_that_fits_exactly(footprint, available):
    # footprint == available − reserve fits. In binary floats 52.3 − 24 is 28.299999999999997, which
    # would refuse the 28.3 GiB model by a rounding error.
    reg = load_registry(FIXTURE)
    model = replace(reg.models["coder"], footprint_gib=footprint)
    assert admit(model, MemInfo(121.7, available), reg.budget, None) == Decision(True, "fits")


@pytest.mark.parametrize("footprint, available, reason", [
    (28.1, 52.0, "needs 28.1 GiB, 52.0 GiB available, 24 GiB reserve kept: 0.1 GiB short"),
    (28, 51.6, "needs 28.0 GiB, 51.6 GiB available, 24 GiB reserve kept: 0.4 GiB short"),
    (27.6, 51.5, "needs 27.6 GiB, 51.5 GiB available, 24 GiB reserve kept: 0.1 GiB short"),
    (2.5, 26.4, "needs 2.5 GiB, 26.4 GiB available, 24 GiB reserve kept: 0.1 GiB short"),
    # 1 KiB short: what's available rounds down, so the shortfall shows as 0.1, never as 0.0
    (28, (52 * KIB_PER_GIB - 1) / KIB_PER_GIB,
     "needs 28.0 GiB, 51.9 GiB available, 24 GiB reserve kept: 0.1 GiB short"),
])
def test_admit_refuses_a_model_just_over_and_shows_by_how_much(footprint, available, reason):
    reg = load_registry(FIXTURE)
    model = replace(reg.models["coder"], footprint_gib=footprint)
    assert admit(model, MemInfo(121.7, available), reg.budget, None) == Decision(False, reason)


def test_the_numbers_a_refusal_shows_never_add_up_to_a_fit():
    # Probes each side of the edge in 0.01 GiB steps and in single KiB, and past what the GPU can
    # allocate. Whatever the rounding, needed − (available − reserve) as shown is the shortfall shown.
    reg = load_registry(FIXTURE)
    for footprint in (0.1, 1.5, 2.5, 18, 27.6, 28, 28.04, 28.3, 90):
        model = replace(reg.models["coder"], footprint_gib=footprint)
        edge_kib = round((footprint + 24) * KIB_PER_GIB)
        probes = [footprint + 24 + step / 100 for step in range(-30, 31)]
        probes += [(edge_kib + kib) / KIB_PER_GIB for kib in (-1024, -1, 0, 1, 1024)]
        verdicts = set()
        for available in probes:
            decision = admit(model, MemInfo(121.7, available), reg.budget, None)
            verdicts.add(decision.ok)
            if not decision.ok:
                needed, free, reserve, short = map(Decimal, SHOWN.fullmatch(decision.reason).groups())
                assert short > 0 and needed - (free - reserve) == short, decision.reason
        assert verdicts == ({False} if footprint == 90 else {True, False}), footprint  # 90 is over the cap


def test_admit_refuses_while_the_brake_holds():
    reg = load_registry(FIXTURE)
    hold = Hold(since="2026-09-23T10:00:00", reason="18.0 GiB available", unloaded=("coder",))
    decision = admit(reg.models["embed"], MemInfo(121.7, 90.0), reg.budget, hold)
    assert not decision.ok and "brake" in decision.reason and "make brake-release" in decision.reason


def test_hold_round_trip(tmp_path):
    assert read_hold(tmp_path) is None
    write_hold(tmp_path, Hold("t", "why", ("a",)))
    assert read_hold(tmp_path) == Hold("t", "why", ("a",))
    assert release_hold(tmp_path) is True
    assert read_hold(tmp_path) is None and release_hold(tmp_path) is False


def test_a_hold_is_on_disk_before_it_takes_its_name_and_the_name_after(tmp_path, monkeypatch):
    # The brake writes the hold as memory runs out, when a GB10 can hard-freeze and need a power cycle.
    # A power cut soon after must not leave hold.json missing or empty: loads would resume unreleased.
    events = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        info = os.fstat(fd)
        events.append("fsync the folder" if stat.S_ISDIR(info.st_mode) else f"fsync {info.st_size} bytes")
        real_fsync(fd)

    def replace(src, dst):
        events.append(f"rename to {Path(dst).name}")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    write_hold(tmp_path, Hold("t", "why", ("a",)))
    whole = len(json.dumps({"since": "t", "reason": "why", "unloaded": ["a"]}))
    assert events == [f"fsync {whole} bytes", "rename to hold.json", "fsync the folder"]
    assert read_hold(tmp_path) == Hold("t", "why", ("a",))
    assert [p.name for p in tmp_path.iterdir()] == ["hold.json"]


DAMAGED_HOLDS = {
    "empty": b"",
    "not text": b"\xff\xfe garbage",
    "cut off": b'{"since": "2026-09-23T10:00:00", "rea',
    "not an object": b"[]",
    "no reason": b'{"since": "t"}',
    "since not text": b'{"since": 1, "reason": "r"}',
    "unloaded not a list": b'{"since": "t", "reason": "r", "unloaded": "coder"}',
    "nested too deep": b"[" * 100_000,  # json.loads raises RecursionError
    "a folder": None,  # can't be read, even by root
}


@pytest.mark.parametrize("damage", DAMAGED_HOLDS.values(), ids=DAMAGED_HOLDS.keys())
def test_a_hold_file_that_cant_be_read_still_holds(tmp_path, damage):
    # Fail closed: a damaged hold must never read as no hold. The reason names the file.
    hold_file = tmp_path / "hold.json"
    if damage is None:
        hold_file.mkdir()
    else:
        hold_file.write_bytes(damage)
    hold = read_hold(tmp_path)
    assert hold is not None and f"hold file {hold_file} can't be read: " in hold.reason


def test_a_hold_that_goes_between_a_check_and_the_act_is_no_hold(tmp_path, monkeypatch):
    # Two releases can race: a check followed by the unlink would crash the loser. So the file is read
    # or unlinked directly, and only its absence means no hold.
    monkeypatch.setattr(Path, "exists", lambda self: True)  # as if hold.json went just after a check
    assert read_hold(tmp_path) is None
    assert release_hold(tmp_path) is False


# Phase 2a, Task 11: `spark launch` starts a model only with the gate's admission ticket, which it uses up. Behind the
# ticket it keeps the brake's hold and Phase 1's zero-wait fit as backstops, and refuses a model that isn't downloaded.
# Each test runs on a box of its own: the fixture registry, a state folder, a launch folder, a Hugging Face cache holding
# every model's files, 70 GiB available, and an engine whose exec is only recorded.

NOW = 1_800_000_000.0
BOOT = "boot-a"
REG = load_registry(FIXTURE)
NO_TICKET = "spark: not starting coder: no admission ticket from the gate\n"


def model_file(hf_home: Path, name: str, file: str) -> Path:
    """Where `make pull` leaves a model's file: the Hugging Face cache's own layout, written out here rather than taken
    from the code under test."""
    source = REG.models[name].source
    org, repo = source.repo.split("/")
    return hf_home / "hub" / f"models--{org}--{repo}" / "snapshots" / source.revision / file


class Box:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.state, self.launch, self.hf = tmp_path / "state", tmp_path / "launch", tmp_path / "hf"
        for folder in (self.state, self.launch, self.hf):
            folder.mkdir()
        for name, model in REG.models.items():
            for file in filter(None, (model.source.file, model.source.mmproj)):
                path = model_file(self.hf, name, file)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"weights")
        self.oom = tmp_path / "oom_score_adj"  # the real one would raise pytest's own
        self.oom.write_text("0\n")
        self.mem = MemInfo(121.7, 70.0)
        self.execs: list[dict] = []
        self.exec_error: OSError | None = None
        monkeypatch.setattr(launch, "OOM_SCORE_ADJ", self.oom)
        monkeypatch.setattr(launch, "read_meminfo", lambda: self.mem)
        monkeypatch.setattr(launch, "boot_id", lambda: BOOT)
        monkeypatch.setattr(launch.os, "execvpe", self._exec)

    def _exec(self, file, argv, env):
        started = self.launch / "started" / f"{self.model}.json"
        self.execs.append({"file": file, "argv": argv, "env": env, "oom": self.oom.read_text(),
                           "started": json.loads(started.read_text()) if started.exists() else None})
        if self.exec_error is not None:
            raise self.exec_error

    def issue(self, model="coder", deadline=NOW + 60, *, boot_id=BOOT):
        return tickets.issue(self.launch, model, deadline, now=NOW - 1, footprint_gib=REG.models[model].footprint_gib,
                             available_gib=70.0, boot_id=boot_id)

    def run(self, model="coder", cmd=("/bin/engine", "--port", "801"), *, registry=FIXTURE, clock=lambda: NOW):
        self.model = model
        return launch.main_launch([model, "--", *cmd], registry=registry, state=self.state, launch=self.launch,
                                  hf_home=str(self.hf), clock=clock)

    def tickets(self) -> list[str]:
        folder = self.launch / "tickets"
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def started(self) -> list[str]:
        folder = self.launch / "started"
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []

    def refusal(self, model="coder") -> dict | None:
        return launch.read_refusal(self.launch, model)


@pytest.fixture()
def box(tmp_path, monkeypatch):
    return Box(tmp_path, monkeypatch)


def test_launch_refuses_without_a_ticket_and_records_why(box, capsys):
    assert box.run() == 3
    assert capsys.readouterr().err == NO_TICKET
    refusal = box.refusal()
    assert (refusal["model"], refusal["code"], refusal["reason"]) == ("coder", "no_ticket",
                                                                      "no admission ticket from the gate")
    assert box.execs == [] and box.oom.read_text() == "0\n" and box.started() == []


def test_a_ticket_is_used_up_by_one_start(box, capsys):
    ticket_id = box.issue()
    assert box.run() == 0
    [call] = box.execs
    assert (call["file"], call["argv"]) == ("/bin/engine", ["/bin/engine", "--port", "801"])
    assert box.tickets() == []
    # On disk before the exec, so the brake sees the load from its first byte: its footprint, MemAvailable at
    # admission, its start, and launch's pid, which the engine keeps across the exec.
    started = call["started"]
    assert started["id"] == ticket_id and started["model"] == "coder" and started["boot_id"] == BOOT
    assert (started["footprint_gib"], started["available_gib"]) == (28, 70.0)
    assert (started["started_at"], started["pid"]) == (NOW, os.getpid())
    assert box.started() == ["coder.json"]  # kept, for the gate to clear once the load is ready or failed
    assert box.run() == 3
    assert capsys.readouterr().err == NO_TICKET
    assert len(box.execs) == 1 and box.refusal()["code"] == "no_ticket"


def test_a_refused_start_leaves_nothing_started(box):
    box.issue()
    write_hold(box.state, Hold("2026-09-23T10:00:00", "18.0 GiB available", ("coder",)))
    assert box.run() == 3
    assert box.refusal()["code"] == "held_by_brake"
    assert box.started() == [] and box.execs == []


def test_a_failed_exec_clears_its_started_record(box, capsys):
    box.issue()
    box.exec_error = FileNotFoundError(2, "No such file or directory", "/bin/engine")
    assert box.run() == 3
    [call] = box.execs
    assert call["started"]["model"] == "coder"  # it was there for the exec
    assert box.started() == [] and box.tickets() == []
    refusal = box.refusal()
    assert refusal["code"] == "exec_failed" and "/bin/engine" in refusal["reason"]
    err = capsys.readouterr().err
    assert err.startswith("spark: not starting coder: ") and "No such file or directory" in err


def test_a_start_that_cant_be_recorded_starts_nothing(box):
    # The brake reads the record as a load in progress, and the gate for its bypass check: a start neither could see
    # doesn't happen.
    box.issue()
    (box.launch / "started").write_text("not a folder")
    assert box.run() == 3
    refusal = box.refusal()
    assert refusal["code"] == "start_unrecorded" and str(box.launch / "started") in refusal["reason"]
    assert box.execs == [] and box.oom.read_text() == "0\n" and box.tickets() == []


@pytest.mark.parametrize("ticket, code, why", [
    ({"deadline": NOW - 1}, "ticket_expired", "expired"),
    ({"boot_id": "boot-z"}, "ticket_expired", "another boot"),
    ({"model": "embed"}, "no_ticket", "no admission ticket from the gate"),
], ids=["expired", "another boot's", "another model's"])
def test_a_ticket_that_isnt_for_this_start_starts_nothing(box, capsys, ticket, code, why):
    box.issue(**ticket)
    assert box.run() == 3
    assert box.refusal()["code"] == code and why in box.refusal()["reason"]
    assert capsys.readouterr().err.startswith("spark: not starting coder: ")
    assert box.execs == [] and box.started() == []
    assert box.tickets() == (["embed.json"] if ticket.get("model") == "embed" else [])


def test_a_ticket_with_a_number_too_large_is_a_refusal_never_a_traceback(box, capsys):
    # Task 11's review, I-1: an int too large for a float in a ticket made claim raise OverflowError, which the CLI
    # doesn't catch: a traceback and exit 1, with nothing recorded.
    (box.launch / "tickets").mkdir()
    (box.launch / "tickets" / "coder.json").write_text(json.dumps({
        "model": "coder", "id": "t1", "issued_at": NOW, "deadline": 10**400, "nonce": "n1", "boot_id": BOOT,
        "footprint_gib": 28, "available_gib": 70.0}))
    assert box.run() == 3
    err = capsys.readouterr().err
    assert err.startswith("spark: not starting coder: no admission ticket from the gate: ") and len(err.splitlines()) == 1
    assert box.refusal()["code"] == "no_ticket" and box.execs == [] and box.tickets() == []


def test_launch_keeps_the_hold_check_as_a_backstop(box, capsys):
    box.issue("embed")
    write_hold(box.state, Hold("2026-09-23T10:00:00", "18.0 GiB available", ("coder",)))
    box.mem = MemInfo(121.7, 90.0)  # plenty: only the hold refuses
    assert box.run("embed") == 3
    assert capsys.readouterr().err == (
        "spark: not starting embed: the memory brake has held new loads since 2026-09-23T10:00:00 "
        "(18.0 GiB available); run `make brake-release` once memory is back\n"
    )
    assert box.refusal("embed")["code"] == "held_by_brake"
    assert box.tickets() == []  # the ticket is used up: a start after the hold needs a new one from the gate
    assert box.execs == [] and box.oom.read_text() == "0\n"


@pytest.mark.parametrize("damage", [b"", b"\x00\xff{garbage"], ids=["empty", "garbage"])
def test_launch_refuses_while_a_damaged_hold_file_stands(box, capsys, damage):
    launch.record_refusal(box.launch, "vision-chat", "no_fit", "an older, unrelated reason")
    (box.state / "hold.json").write_bytes(damage)
    box.mem = MemInfo(121.7, 90.0)
    box.issue("embed")
    assert box.run("embed") == 3
    err = capsys.readouterr().err
    assert err.startswith("spark: not starting embed: ") and "make brake-release" in err
    assert str(box.state / "hold.json") in err
    refusal = box.refusal("embed")
    assert refusal["code"] == "held_by_brake" and str(box.state / "hold.json") in refusal["reason"]
    assert box.refusal("vision-chat")["reason"] == "an older, unrelated reason"  # each model's own
    assert box.execs == []


def test_launch_keeps_a_zero_wait_fit_check(box, capsys):
    box.issue()
    box.mem = MemInfo(121.7, 30.0)
    assert box.run() == 3
    reason = "needs 28.0 GiB, 30.0 GiB available, 24 GiB reserve kept: 22.0 GiB short"
    assert capsys.readouterr().err == f"spark: not starting coder: {reason}\n"  # Phase 1's words
    assert box.refusal()["code"] == "no_fit" and box.refusal()["reason"] == reason
    assert box.execs == [] and box.oom.read_text() == "0\n" and box.tickets() == []


@pytest.mark.parametrize("model, file, how", [
    ("coder", "coder.gguf", "missing"),
    ("coder", "coder.gguf", "a link to a blob that isn't there"),
    ("vision-chat", "vision-mmproj.gguf", "missing"),
    ("stt", "whisper.bin", "a folder"),
], ids=["the coder's file", "a dangling link", "the vision model's projector", "a folder in a file's place"])
def test_launch_refuses_not_downloaded_naming_make_pull(box, capsys, model, file, how):
    box.issue(model)
    path = model_file(box.hf, model, file)
    path.unlink()
    if how == "a link to a blob that isn't there":
        path.symlink_to(box.hf / "hub" / "blobs" / "0123")  # an interrupted download leaves the snapshot's link
    elif how == "a folder":
        path.mkdir()
    assert box.run(model) == 3
    refusal = box.refusal(model)
    assert refusal["code"] == "not_downloaded" and "`make pull`" in refusal["reason"] and str(path) in refusal["reason"]
    assert capsys.readouterr().err == f"spark: not starting {model}: {refusal['reason']}\n"
    assert box.execs == [] and box.started() == [] and box.tickets() == []


def test_a_model_whose_every_file_is_there_starts(box):
    # The other side of the check above: the weights and the projector both in the cache at hf_home.
    box.issue("vision-chat")
    assert box.run("vision-chat") == 0 and len(box.execs) == 1


def test_launch_execs_the_engine_when_it_fits(box):
    box.issue()
    assert box.run("coder", ("/bin/engine", "--port", "5800")) == 0
    [call] = box.execs
    assert (call["file"], call["argv"], call["oom"]) == ("/bin/engine", ["/bin/engine", "--port", "5800"], "1000")


# Phase 1's council (reliability m5): earlyoom chooses among the engines by oom_score, and a model's GPU memory isn't in
# its engine's RSS, so at one adjustment for all their scores sat within 9 of each other, and earlyoom's dry run picked
# Gemma, a resident, before the on-demand coder. A resident engine starts at 900 and an on-demand one at 1000, so
# earlyoom takes the on-demand coder first, as the brake does, and every engine still goes before a process at 0.

@pytest.mark.parametrize("model, adj", [("coder", "1000"), ("vision-chat", "900"), ("embed", "900"), ("stt", "900")])
def test_an_engine_is_marked_for_the_oom_killers_in_the_brakes_order(box, model, adj):
    box.issue(model)
    assert box.run(model) == 0
    assert box.oom.read_text() == adj and box.execs[0]["oom"] == adj  # set before the exec, so the engine keeps it


def test_an_engine_it_cant_mark_still_starts_and_says_so_in_one_line(box, monkeypatch, capsys):
    # Not Linux, or a write the kernel refuses: the engine still starts, left at llama-swap's own 0, which scores below
    # Dan's jobs. The line goes to stderr, which llama-swap keeps, before the exec that would lose a buffered one.
    monkeypatch.setattr(launch, "OOM_SCORE_ADJ", box.oom.parent / "missing" / "oom_score_adj")
    seen = {}
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: seen.update(file=f, err=capsys.readouterr().err))
    box.issue()
    assert box.run() == 0
    assert seen["file"] == "/bin/engine"
    lines = seen["err"].splitlines()
    assert len(lines) == 1 and lines[0].startswith("spark: coder starts without oom_score_adj 1000 (")
    assert "No such file or directory" in lines[0] and "earlyoom" in lines[0]


def test_the_engine_inherits_no_api_key(box, monkeypatch):
    # llama-swap reads its keys from its environment, and every engine it starts inherits that
    # environment. Engines parse third-party model files and need none of the keys.
    monkeypatch.setenv("LLAMASWAP_KEY_AGENT", "x")
    monkeypatch.setenv("LLAMASWAP_KEY_SPARK", "x")
    monkeypatch.setenv("HF_HOME", "/var/lib/local-ai/hf")
    box.issue()
    box.run()
    env = box.execs[0]["env"]
    assert [name for name in env if name.startswith("LLAMASWAP_KEY_")] == []
    assert env["HF_HOME"] == "/var/lib/local-ai/hf"


def test_engines_lose_the_internal_keys_too(box, monkeypatch):
    # From 2a llama-swap holds only the internal keys, the front's, the gate's and the brake's: none reaches an engine.
    for name in ("LLAMASWAP_KEY_FRONT", "LLAMASWAP_KEY_GATE", "LLAMASWAP_KEY_BRAKE"):
        monkeypatch.setenv(name, "stand-in")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    box.issue()
    assert box.run() == 0
    env = box.execs[0]["env"]
    assert env["PATH"] == "/usr/bin:/bin"
    assert [name for name in env if name.startswith("LLAMASWAP_KEY_")] == []


def test_launch_never_waits(box, monkeypatch):
    # The gate is where a load waits: launch refuses at once, or starts at once. Every path it can take, run with
    # sleeping made an error.
    def sleep(seconds):
        raise AssertionError(f"launch slept {seconds} s")

    monkeypatch.setattr(time, "sleep", sleep)
    assert box.run() == 3  # no ticket
    box.issue(deadline=NOW - 1)
    assert box.run() == 3  # expired
    box.issue()
    write_hold(box.state, Hold("t0", "why", ()))
    assert box.run() == 3  # held
    release_hold(box.state)
    box.issue()
    box.mem = MemInfo(121.7, 30.0)
    assert box.run() == 3  # no room
    box.mem = MemInfo(121.7, 70.0)
    box.issue()
    model_file(box.hf, "coder", "coder.gguf").unlink()
    assert box.run() == 3  # not downloaded
    model_file(box.hf, "coder", "coder.gguf").write_bytes(b"weights")
    box.issue()
    box.exec_error = PermissionError(13, "Permission denied", "/bin/engine")
    assert box.run() == 3  # the exec failed
    box.exec_error = None
    box.issue()
    assert box.run() == 0  # started
    assert box.run("coder", registry=box.hf / "missing.yaml") == 3  # the registry won't load
    assert box.refusal()["code"] == "registry"


def test_launch_unknown_model_is_a_usage_error(box):
    box.issue()
    assert box.run("nope") == 2
    assert box.tickets() == ["coder.json"] and box.execs == []


@pytest.mark.parametrize("argv", [
    [], ["coder"], ["coder", "/bin/engine"], ["coder", "--"], ["--", "/bin/engine"],
    ["coder", "x", "--", "/bin/engine"],
], ids=["nothing", "no --", "no -- before the command", "no command", "no model", "-- not second"])
def test_launch_usage_errors(box, capsys, argv):
    box.issue()
    assert launch.main_launch(argv, registry=FIXTURE, state=box.state, launch=box.launch, hf_home=str(box.hf)) == 2
    assert capsys.readouterr().err == "usage: spark launch <model> -- <engine command…>\n"
    assert not (box.launch / "refusals").exists() and box.tickets() == ["coder.json"] and box.execs == []


def test_launch_as_root_starts_nothing_and_writes_nothing(box, monkeypatch, capsys):
    # The launch folder is spark's, and root never writes through a path spark controls: as root, launch neither claims
    # a ticket nor records why it refused.
    box.issue()
    monkeypatch.setattr(launch.os, "geteuid", lambda: 0)
    assert box.run() == 3
    err = capsys.readouterr().err
    assert err.startswith("spark: not starting coder: ") and "root" in err and len(err.splitlines()) == 1
    assert box.tickets() == ["coder.json"] and not (box.launch / "refusals").exists() and box.execs == []


@pytest.mark.parametrize("setup, error", [
    (None, "No such file or directory"),
    ("a folder", "Is a directory"),
    ("budget: [102, 24\n", "while parsing a flow sequence"),
    ("budget: {allocatable_gib: 102}\n", "budget: reserve_gib is required"),
], ids=["missing", "a folder", "not YAML", "invalid"])
def test_launch_refuses_when_the_registry_wont_load(box, tmp_path, capsys, setup, error):
    registry = tmp_path / "models.yaml"
    if setup == "a folder":
        registry.mkdir()
    elif setup is not None:
        registry.write_text(setup)
    box.issue()
    assert box.run(registry=registry) == 3
    err = capsys.readouterr().err
    assert err.startswith(f"spark: not starting coder: the registry {registry} won't load: ") and error in err
    refusal = box.refusal()
    assert refusal["model"] == "coder" and refusal["code"] == "registry"
    assert str(registry) in refusal["reason"] and error in refusal["reason"]
    assert box.tickets() == ["coder.json"] and box.execs == []  # refused before its ticket is claimed


def test_a_refusal_is_kept_for_spark_status_until_the_next_start(box):
    box.mem = MemInfo(121.7, 30.0)
    box.issue()
    box.run()
    refusal = box.refusal()
    assert refusal["model"] == "coder" and refusal["reason"].startswith("needs 28.0 GiB")
    launch.record_refusal(box.launch, "embed", "no_fit", "embed's own")
    box.mem = MemInfo(121.7, 90.0)
    box.issue()
    assert box.run() == 0
    assert box.refusal() is None and box.refusal("embed")["reason"] == "embed's own"


def test_refusal_records_are_one_per_model(tmp_path):
    launch.record_refusal(tmp_path, "coder", "no_fit", "needs 28.0 GiB")
    launch.record_refusal(tmp_path, "embed", "no_ticket", "no admission ticket from the gate")
    assert sorted(p.name for p in (tmp_path / "refusals").iterdir()) == ["coder.json", "embed.json"]
    refusal = launch.read_refusal(tmp_path, "coder")
    assert set(refusal) == {"at", "model", "code", "reason"}
    assert (refusal["model"], refusal["code"], refusal["reason"]) == ("coder", "no_fit", "needs 28.0 GiB")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", refusal["at"])  # local time, to the second
    launch.clear_refusal(tmp_path, "coder")
    assert launch.read_refusal(tmp_path, "coder") is None and launch.read_refusal(tmp_path, "embed") is not None
    launch.clear_refusal(tmp_path, "coder")  # one already gone is no error


def test_a_refusal_record_is_swapped_in_whole(tmp_path, monkeypatch):
    # The gate and `spark status` can read it at any moment: they must find the old record or the new one, never part
    # of one, which they would report as no refusal at all.
    renamed = []
    real_replace = os.replace

    def replace(src, dst):
        renamed.append(Path(dst).name)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    launch.record_refusal(tmp_path, "coder", "no_fit", "why")
    assert renamed == ["coder.json"]
    assert launch.read_refusal(tmp_path, "coder")["reason"] == "why"
    assert [p.name for p in (tmp_path / "refusals").iterdir()] == ["coder.json"]  # no temporary file left


def test_a_refusal_for_a_name_that_isnt_a_models_writes_nothing(tmp_path, monkeypatch):
    # The name comes from llama-swap's command line, and names the record's file: one that isn't a model's could name a
    # file outside refusals/. The reason still reaches llama-swap's log through stderr.
    (tmp_path / "refusals").mkdir()
    launch.record_refusal(tmp_path, "../coder", "registry", "why")
    assert list(tmp_path.iterdir()) == [tmp_path / "refusals"] and list((tmp_path / "refusals").iterdir()) == []
    monkeypatch.setattr(launch.os, "geteuid", lambda: 0)  # and root records nothing, even for a model's name
    launch.record_refusal(tmp_path, "coder", "registry", "why")
    assert list((tmp_path / "refusals").iterdir()) == []
    assert launch.read_refusal(tmp_path, "../coder") is None and launch.check_refusal(tmp_path, "../x") == (None, None)


# Task 5's fix round 1: `spark status` shows the record, so the reader returns only a whole one, and says what's wrong
# with a record that isn't (I5).

DAMAGED_REFUSALS = {
    "a list": b"[1]",
    "a string": b'"s"',
    "a number": b"5",
    "none of its fields": b'{"x": 1}',
    "at not text": b'{"at": 1, "model": "coder", "code": "no_fit", "reason": "r"}',
    "no reason": b'{"at": "t", "model": "coder", "code": "no_fit"}',
    "no code": b'{"at": "t", "model": "coder", "reason": "r"}',
    "another model's": b'{"at": "t", "model": "embed", "code": "no_fit", "reason": "r"}',
    "not JSON": b"{not json",
    "empty": b"",
    "not text": b"\xff\xfe garbage",
    "nested too deep": b"[" * 100_000,
    "a folder": None,
}


@pytest.mark.parametrize("damage", DAMAGED_REFUSALS.values(), ids=DAMAGED_REFUSALS.keys())
def test_a_refusal_record_that_isnt_one_reads_as_none_and_says_why(tmp_path, damage):
    record = tmp_path / "refusals" / "coder.json"
    record.parent.mkdir()
    if damage is None:
        record.mkdir()
    else:
        record.write_bytes(damage)
    assert launch.read_refusal(tmp_path, "coder") is None
    refusal, problem = launch.check_refusal(tmp_path, "coder")
    assert refusal is None and problem.startswith(f"the refusal record {record} ")
    assert launch.newest_refusal(tmp_path) == (None, problem)


def test_a_whole_refusal_record_is_read_as_its_four_fields(tmp_path):
    (tmp_path / "refusals").mkdir()
    (tmp_path / "refusals" / "coder.json").write_text(
        json.dumps({"at": "t1", "model": "coder", "code": "no_fit", "reason": "why", "x": 1}))
    whole = {"at": "t1", "model": "coder", "code": "no_fit", "reason": "why"}
    assert launch.check_refusal(tmp_path, "coder") == (whole, None)
    assert launch.read_refusal(tmp_path, "coder") == whole


def test_no_refusal_record_is_no_refusal_and_no_problem(tmp_path):
    assert launch.check_refusal(tmp_path, "coder") == (None, None)
    assert launch.newest_refusal(tmp_path) == (None, None)
    (tmp_path / "refusals").mkdir()
    assert launch.newest_refusal(tmp_path) == (None, None)


def test_the_newest_refusal_is_the_one_written_last(tmp_path):
    # `spark status`'s `refused` line, until Task 29 reads the refusals through the gate.
    launch.record_refusal(tmp_path, "embed", "no_fit", "embed's")
    launch.record_refusal(tmp_path, "coder", "no_ticket", "the coder's")
    os.utime(tmp_path / "refusals" / "embed.json", ns=(2_000_000_000_000_000_000, 2_000_000_000_000_000_000))
    os.utime(tmp_path / "refusals" / "coder.json", ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    (tmp_path / "refusals" / ".coder.json.123.abcd").write_text("{")  # a write not yet swapped in
    refusal, problem = launch.newest_refusal(tmp_path)
    assert problem is None and (refusal["model"], refusal["reason"]) == ("embed", "embed's")
    os.utime(tmp_path / "refusals" / "coder.json", ns=(3_000_000_000_000_000_000, 3_000_000_000_000_000_000))
    assert launch.newest_refusal(tmp_path)[0]["model"] == "coder"
