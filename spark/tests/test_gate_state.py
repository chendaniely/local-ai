"""Task 13: the gate's state, kept across restarts, and the brake's hold with its boot, episode and loading model.
Every time, boot id, pid and uid here is a stand-in; none was read from the box."""

import errno
import json
import os
import stat
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from spark import hold
from spark.gate import state as gate_state
from spark.gate.state import (STATE_FILE, ApplyHold, BrakeMark, GateState, ModelRecord, Pin, RefusalRecord, RoomHold,
                              Session, Ticketed, load_state, notified_key, restore, save_state)
from spark.gateproto import AUTO_RELEASE_EVERY_S, NOTIFIED_KEEP_S, REFUSAL_HISTORY
from spark.hold import Hold, read_hold, release_waits_for_dan, write_hold
from spark.llamaswap import Running
from spark.registry import load_registry

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
REGISTRY = load_registry(STACK_REGISTRY)
T = 1_800_000_000.0
BOOT, NEXT_BOOT = "b1", "b2"
DAN, AGENT = 1000, 1002  # stand-in uids


def by_role(role: str) -> str:
    """The model that has `role` in the stack's registry, so these tests follow the coder's swap (Task 42)."""
    return next(name for name, model in REGISTRY.models.items() if role in model.roles)


CODER, GEMMA = by_role("coder"), by_role("vision")


def record(name: str, *, footprint: float = 32.0, state: str = "ready", last_use: float = T - 3600,
           **more) -> ModelRecord:
    return ModelRecord(name=name, footprint_gib=footprint, loaded_at=T - 7200, load_fall_gib=25.0, fall_flagged=False,
                       rss_anon_at_load_gib=1.5, last_use=last_use, state=state, **more)


def refusal(n: int) -> RefusalRecord:
    return RefusalRecord(at=T + n, model=CODER, key="agent", uid=AGENT, code="no_fit", message=f"refusal {n}")


def full_state() -> GateState:
    """Every part of the state that survives a restart, filled."""
    state = GateState(boot_id=BOOT)
    state.models = {GEMMA: record(GEMMA), CODER: record(CODER, footprint=33.0, unload_requested_at=T - 5)}
    state.pins = {CODER: Pin(model=CODER, until=T, by_uid=DAN)}
    state.sessions = {"s1": Session(id="s1", uid=AGENT, pid=4242, model=CODER, label="agent's pi", started_at=T - 600,
                                    renewed_at=T - 60)}
    state.room_hold = RoomHold(size_gib=40.0, until=None, boot_id=BOOT, created_at=T - 300, unloaded=[CODER])
    state.brake_marks = {CODER: BrakeMark(model=CODER, at=T - 3 * 3600, seen_gib=26.0)}
    state.last_auto_release_at = T - 1800
    state.brake_events_after = (BOOT, 7)
    state.notified = {notified_key("loaded", "load:abc"): T - 10}
    state.notify_failing_since = T - 20
    state.refusals.extend(refusal(n) for n in range(3))
    state.applying = ApplyHold(since=T - 30, by_uid=DAN, renewed_at=T - 15, begun_at=T - 10,
                               restarting=["local-ai-gate.service", "local-ai-llama-swap.service"], ended=False)
    state.ticketed = {GEMMA: Ticketed(model=GEMMA, pid=4300, ticket_id="t-gemma", at=T - 7000, start_time=123456)}
    state.issued = {CODER: "t-coder"}
    state.clean_shutdown = False
    return state


def test_pins_sessions_holds_and_marks_survive_a_restart(tmp_path):
    state = full_state()
    save_state(tmp_path, state, now=T)
    loaded, problem = load_state(tmp_path)
    assert problem is None
    assert loaded == state
    assert loaded.saved_at == T and loaded.brake_events_after == (BOOT, 7)
    assert loaded.pins[CODER].until == T and loaded.room_hold.until is None and loaded.room_hold.size_gib == 40
    assert loaded.brake_marks[CODER] == BrakeMark(CODER, T - 3 * 3600, 26.0)
    assert list(loaded.refusals) == [refusal(0), refusal(1), refusal(2)]


def test_a_missing_state_file_is_a_fresh_state_and_no_problem(tmp_path):
    assert load_state(tmp_path) == (GateState(), None)
    assert load_state(tmp_path / "nowhere") == (GateState(), None)


def test_after_a_restart_every_loaded_models_last_use_is_now():
    state = GateState(boot_id=BOOT, models={GEMMA: record(GEMMA, last_use=T - 3600)})
    restored, ended = restore(state, [Running(GEMMA, "ready")], now=T, boot_id=BOOT, registry=REGISTRY)
    assert restored.models[GEMMA].last_use == T and restored.models[GEMMA].state == "ready"
    assert ended is None
    assert state.models[GEMMA].last_use == T - 3600  # the state it was given is left as it was


def test_a_model_left_starting_counts_as_starting_after_a_restart():
    restored, _ = restore(GateState(boot_id=BOOT), [Running(CODER, "starting")], now=T, boot_id=BOOT,
                          registry=REGISTRY)
    coder = restored.models[CODER]
    assert coder.state == "starting" and coder.footprint_gib == REGISTRY.models[CODER].footprint_gib
    assert coder.last_use == T and coder.load_fall_gib is None and coder.rss_anon_at_load_gib is None


def test_a_load_that_finished_while_the_gate_was_away_is_still_starting():
    # Its ticket issued and not yet settled: Task 15's ready-or-gone path settles it, so its started/ record moves
    # into ticketed, as for a model /running still shows starting.
    state = GateState(boot_id=BOOT, issued={CODER: "t-coder"})
    restored, _ = restore(state, [Running(CODER, "ready")], now=T, boot_id=BOOT, registry=REGISTRY)
    assert restored.models[CODER].state == "starting" and restored.issued == {CODER: "t-coder"}
    # A load already settled (its ticket in ticketed) is ready.
    state.ticketed = {CODER: Ticketed(CODER, 4301, "t-coder", T - 50, 99)}
    restored, _ = restore(state, [Running(CODER, "ready")], now=T, boot_id=BOOT, registry=REGISTRY)
    assert restored.models[CODER].state == "ready"


def test_a_model_running_that_the_state_doesnt_know_is_counted():
    # A state lost, or a load around the gate: its memory is counted at the registry's footprint, with no fall and no
    # RssAnon, so owed errs safe; a model the registry doesn't know counts at its largest footprint.
    restored, _ = restore(GateState(boot_id=BOOT), [Running(GEMMA, "ready"), Running("mystery-model", "ready")],
                          now=T, boot_id=BOOT, registry=REGISTRY)
    gemma = restored.models[GEMMA]
    assert (gemma.state, gemma.footprint_gib, gemma.load_fall_gib) == ("ready", 32, None)
    largest = max(model.footprint_gib for model in REGISTRY.models.values())
    assert restored.models["mystery-model"].footprint_gib == largest


def test_a_model_gone_from_running_is_dropped():
    state = GateState(boot_id=BOOT, models={CODER: record(CODER)},
                      ticketed={CODER: Ticketed(CODER, 4301, "t-coder", T - 50, 99)})
    restored, _ = restore(state, [], now=T, boot_id=BOOT, registry=REGISTRY)
    assert restored.models == {} and restored.ticketed == {}


def test_a_draining_model_whose_unload_wasnt_requested_is_ready_again():
    # A drain doesn't outlive the gate that ran it: before its unload call, the model is still loaded and serving.
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, state="draining")})
    restored, _ = restore(state, [Running(CODER, "ready")], now=T, boot_id=BOOT, registry=REGISTRY)
    assert restored.models[CODER].state == "ready"


def test_a_model_found_stopping_stays_stopping_and_its_leaving_is_the_gates_own():
    # Its drain's unload under way when the gate restarted: its memory not freed until /running shows it gone, and
    # its leaving never read as a crash (the controller's ruling at Task 12's re-review).
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, footprint=33.0, state="stopping",
                                                           unload_requested_at=T - 40)})
    restored, _ = restore(state, [Running(CODER, "stopping")], now=T, boot_id=BOOT, registry=REGISTRY)
    coder = restored.models[CODER]
    assert (coder.state, coder.footprint_gib, coder.unload_requested_at) == ("stopping", 33.0, T - 40)
    # Found stopping with no record of it: counted at the registry's footprint, its leaving expected.
    restored, _ = restore(GateState(boot_id=BOOT), [Running(GEMMA, "stopping")], now=T, boot_id=BOOT,
                          registry=REGISTRY)
    gemma = restored.models[GEMMA]
    assert (gemma.state, gemma.footprint_gib, gemma.unload_requested_at) == ("stopping", 32, T)


def test_a_model_still_ready_with_its_unload_requested_counts_as_stopping():
    # The gate saved "unload requested" before it called unload, and restarted while v257 had the unload queued: the
    # model still ready, its leaving the gate's own (the controller's ruling at Task 12's second re-review).
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, state="draining", unload_requested_at=T - 3)})
    restored, _ = restore(state, [Running(CODER, "ready")], now=T, boot_id=BOOT, registry=REGISTRY)
    assert restored.models[CODER].state == "stopping" and restored.models[CODER].unload_requested_at == T - 3
    restored, _ = restore(state, [], now=T, boot_id=BOOT, registry=REGISTRY)  # gone: the record goes with it
    assert restored.models == {}


def test_a_room_hold_ends_at_the_next_boot():
    held = RoomHold(size_gib=40.0, until=None, boot_id=BOOT, created_at=T - 600, unloaded=[CODER])
    restored, ended = restore(GateState(boot_id=BOOT, room_hold=held), [], now=T, boot_id=NEXT_BOOT,
                              registry=REGISTRY)
    assert restored.room_hold is None and ended == held
    restored, ended = restore(GateState(boot_id=BOOT, room_hold=held), [], now=T, boot_id=BOOT, registry=REGISTRY)
    assert restored.room_hold == held and ended is None


def test_pins_sessions_marks_and_refusals_stay_across_a_reboot():
    state = full_state()
    restored, _ = restore(state, [], now=T, boot_id=NEXT_BOOT, registry=REGISTRY)
    assert (restored.pins, restored.sessions, restored.brake_marks) == (state.pins, state.sessions, state.brake_marks)
    assert list(restored.refusals) == list(state.refusals) and restored.boot_id == NEXT_BOOT
    assert restored.last_auto_release_at == state.last_auto_release_at


@pytest.mark.parametrize("damage", ["{", "[]", '{"models": 5}', '{"saved_at": NaN}', '{"saved_at": 1e999}',
                                    '{"last_auto_release_at": true}', '{"brake_events_after": ["b1"]}',
                                    '{"pins": {"coder": {"model": "gemma", "until": null, "by_uid": 1000}}}',
                                    '{"models": {"coder": {"name": "coder"}}}', "[" * 100_000, "\udcff"])
def test_a_damaged_state_file_starts_fresh_and_says_so(tmp_path, damage):
    (tmp_path / STATE_FILE).write_text(damage, errors="surrogateescape")
    fresh, problem = load_state(tmp_path)
    assert fresh == GateState()
    assert "state.json" in problem and problem.startswith("the gate's state ")


def test_a_state_file_that_isnt_a_regular_file_starts_fresh_and_says_so(tmp_path):
    os.mkfifo(tmp_path / STATE_FILE)  # opened without waiting for a writer, then refused
    fresh, problem = load_state(tmp_path)
    assert fresh == GateState() and "state.json" in problem


def test_each_loaded_model_keeps_the_footprint_it_was_loaded_with():
    newer = replace(REGISTRY, models={**REGISTRY.models,
                                      CODER: replace(REGISTRY.models[CODER], footprint_gib=41.0)})
    state = GateState(boot_id=BOOT, models={CODER: record(CODER, footprint=33.0)})
    restored, _ = restore(state, [Running(CODER, "ready")], now=T, boot_id=BOOT, registry=newer)
    assert restored.models[CODER].footprint_gib == 33.0
    state.models[CODER].state = "starting"
    restored, _ = restore(state, [Running(CODER, "starting")], now=T, boot_id=BOOT, registry=newer)
    assert restored.models[CODER].footprint_gib == 33.0


def test_state_is_written_whole_and_on_disk_before_it_returns(tmp_path, monkeypatch):
    calls: list[tuple[str, int, bool]] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        info = os.fstat(fd)
        calls.append(("fsync", info.st_ino, stat.S_ISDIR(info.st_mode)))
        real_fsync(fd)

    def rename(src, dst):
        calls.append(("replace", 0, False))
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", rename)
    save_state(tmp_path, full_state(), now=T)
    path = tmp_path / STATE_FILE
    assert calls == [("fsync", path.stat().st_ino, False), ("replace", 0, False),
                     ("fsync", tmp_path.stat().st_ino, True)]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sorted(p.name for p in tmp_path.iterdir()) == [STATE_FILE]


def test_a_write_that_fails_half_way_leaves_the_previous_file_whole(tmp_path, monkeypatch):
    before = full_state()
    save_state(tmp_path, before, now=T)
    real_open, real_write, temporary = os.open, os.write, set()

    def opened(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if Path(path).name.startswith(f".{STATE_FILE}"):
            temporary.add(fd)
        return fd

    def half(fd, data):
        if fd in temporary:  # the disk fills half-way through the new file
            real_write(fd, bytes(data)[:len(data) // 2])
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_write(fd, data)

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "write", half)
    after = full_state()
    after.pins = {}
    with pytest.raises(OSError, match="No space left"):
        save_state(tmp_path, after, now=T + 5)
    monkeypatch.undo()
    assert temporary  # the write did reach the new file
    assert load_state(tmp_path) == (before, None)
    assert sorted(p.name for p in tmp_path.iterdir()) == [STATE_FILE]  # nothing left behind


def test_root_never_writes_the_state(tmp_path, monkeypatch):
    # The gate's folder is spark's: root never writes through a path spark controls.
    monkeypatch.setattr(gate_state.os, "geteuid", lambda: 0)
    with pytest.raises(PermissionError, match="root"):
        save_state(tmp_path, full_state(), now=T)
    assert list(tmp_path.iterdir()) == []


def test_the_refusal_history_keeps_the_last_50(tmp_path):
    assert REFUSAL_HISTORY == 50
    state = GateState()
    state.refusals.extend(refusal(n) for n in range(60))
    assert list(state.refusals) == [refusal(n) for n in range(10, 60)]  # the newest 50, oldest first
    save_state(tmp_path, state, now=T)
    assert list(load_state(tmp_path)[0].refusals) == [refusal(n) for n in range(10, 60)]
    # A file that holds more (written by hand, or by a later version) loads as its newest 50, still bounded.
    data = json.loads((tmp_path / STATE_FILE).read_text())
    data["refusals"] = [data["refusals"][0]] * 5 + data["refusals"]
    (tmp_path / STATE_FILE).write_text(json.dumps(data))
    loaded = load_state(tmp_path)[0]
    assert list(loaded.refusals) == [refusal(n) for n in range(10, 60)]
    loaded.refusals.append(refusal(60))
    assert len(loaded.refusals) == 50 and loaded.refusals[0] == refusal(11)


def test_old_notified_keys_are_pruned(tmp_path):
    assert NOTIFIED_KEEP_S == 86400
    state = GateState()
    state.notified[notified_key("loaded", "load:a")] = T - 2 * 86400
    state.notified[notified_key("loaded", "load:b")] = T - 3600
    kept = state.notified
    save_state(tmp_path, state, now=T)
    assert state.notified == {notified_key("loaded", "load:b"): T - 3600}
    assert state.notified is kept  # pruned in place, so whoever holds the dict sees it too
    assert load_state(tmp_path)[0].notified == {notified_key("loaded", "load:b"): T - 3600}


def test_notified_is_keyed_by_type_and_event_key_together(tmp_path):
    # load_started and loaded both send load:<ticket id> for one load: keyed by the event key alone, loaded would be
    # dropped as a repeat (the controller's ruling after Task 7's fix round; Task 14).
    assert notified_key("load_started", "load:abc") != notified_key("loaded", "load:abc")
    assert notified_key("loaded", "load:abc") == "loaded load:abc"
    state = GateState(notified={notified_key("load_started", "load:abc"): T - 30,
                                notified_key("loaded", "load:abc"): T - 5})
    save_state(tmp_path, state, now=T)
    assert len(load_state(tmp_path)[0].notified) == 2


def test_an_apply_hold_survives_a_restart_but_not_a_reboot(tmp_path):
    state = full_state()
    save_state(tmp_path, state, now=T)
    loaded, _ = load_state(tmp_path)
    running = [Running(GEMMA, "ready")]
    restored, _ = restore(loaded, running, now=T + 5, boot_id=BOOT, registry=REGISTRY)
    assert restored.applying == state.applying
    assert restored.applying.restarting == ["local-ai-gate.service", "local-ai-llama-swap.service"]
    assert restored.ticketed == state.ticketed  # Gemma still runs, its engine still the one its ticket started
    restored, _ = restore(loaded, running, now=T + 5, boot_id=NEXT_BOOT, registry=REGISTRY)
    assert restored.applying is None and restored.ticketed == {}


def test_issued_tickets_survive_a_restart_but_not_a_reboot():
    # The ticket ids the gate issued (the controller's ruling at Task 11's review): Task 18's bypass check counts a
    # started/ record as ticketed only when its id is one of them.
    state = GateState(boot_id=BOOT, issued={CODER: "t-coder"})
    assert restore(state, [], now=T, boot_id=BOOT, registry=REGISTRY)[0].issued == {CODER: "t-coder"}
    assert restore(state, [], now=T, boot_id=NEXT_BOOT, registry=REGISTRY)[0].issued == {}


def test_a_ticketed_engine_keeps_its_start_time(tmp_path):
    # So a pid the kernel hands to another process of spark's is never read as the engine (the controller's ruling
    # at Task 8's review): procs.engine_pid takes a recorded pid only with its start time.
    state = GateState(ticketed={GEMMA: Ticketed(GEMMA, 4300, "t-gemma", T, 987654)})
    save_state(tmp_path, state, now=T)
    assert load_state(tmp_path)[0].ticketed[GEMMA].start_time == 987654


def local(at: float) -> str:
    """A hold's `since`, as the brake writes it: local time, to the second, with no zone."""
    return datetime.fromtimestamp(at).isoformat(timespec="seconds")


def test_a_hold_carries_its_boot_episode_and_loading_model(tmp_path):
    written = Hold(local(T), "19.6 GiB available", (CODER,), boot_id=BOOT, episode=2, loading=CODER,
                   available_gib=19.6)
    write_hold(tmp_path, written)
    assert read_hold(tmp_path) == written
    assert json.loads((tmp_path / hold.FILE).read_text())["unloaded"] == [CODER]
    # Nothing was starting when it fired: no loading in the file, and None read back.
    write_hold(tmp_path, replace(written, loading=None))
    assert "loading" not in json.loads((tmp_path / hold.FILE).read_text())
    assert read_hold(tmp_path) == replace(written, loading=None)
    # A hold without the four is written as Phase 1 wrote it, so Phase 1's brake reads it after a rollback.
    write_hold(tmp_path, Hold(local(T), "18.0 GiB available", ()))
    assert set(json.loads((tmp_path / hold.FILE).read_text())) == {"since", "reason", "unloaded"}


def test_a_phase_1_hold_reads_with_none_and_counts_as_another_boots(tmp_path):
    (tmp_path / hold.FILE).write_text(json.dumps({"since": local(T), "reason": "18.0 GiB available",
                                                  "unloaded": [CODER]}))
    phase_1 = read_hold(tmp_path)
    assert phase_1 == Hold(local(T), "18.0 GiB available", (CODER,))
    assert (phase_1.boot_id, phase_1.episode, phase_1.loading, phase_1.available_gib) == (None, None, None, None)
    assert release_waits_for_dan(phase_1, boot_id=BOOT, last_auto_release_at=None, now=T)


@pytest.mark.parametrize("field, value", [("boot_id", 7), ("boot_id", ""), ("episode", "two"), ("episode", True),
                                          ("episode", 0), ("loading", ["coder"]), ("available_gib", "19.6"),
                                          ("available_gib", float("nan"))])
def test_a_hold_with_a_damaged_new_field_still_holds(tmp_path, field, value):
    raw = {"since": local(T), "reason": "18.0 GiB available", "unloaded": [], field: value}
    (tmp_path / hold.FILE).write_text(json.dumps(raw))
    damaged = read_hold(tmp_path)
    assert damaged.since == hold.UNREADABLE_SINCE and "can't be read" in damaged.reason  # fail closed: still holds
    assert release_waits_for_dan(damaged, boot_id=BOOT, last_auto_release_at=None, now=T)


def test_release_waits_for_dan_after_a_reboot_or_a_second_brake_within_the_hour():
    assert AUTO_RELEASE_EVERY_S == 3600
    released = T

    def fired(after_s: float, boot: str = BOOT) -> Hold:
        return Hold(local(released + after_s), "19.6 GiB available", (), boot_id=boot, episode=2)

    assert release_waits_for_dan(fired(3 * 3600, "b0"), boot_id=BOOT, last_auto_release_at=None, now=T + 4 * 3600)
    assert release_waits_for_dan(fired(20 * 60), boot_id=BOOT, last_auto_release_at=released, now=T + 25 * 60)
    assert not release_waits_for_dan(fired(61 * 60), boot_id=BOOT, last_auto_release_at=released, now=T + 62 * 60)
    assert not release_waits_for_dan(fired(20 * 60), boot_id=BOOT, last_auto_release_at=None, now=T + 25 * 60)
    # A since that can't be read, or that lies ahead of now (a clock set back), errs towards Dan.
    assert release_waits_for_dan(replace(fired(61 * 60), since="garbled"), boot_id=BOOT,
                                 last_auto_release_at=released, now=T + 62 * 60)
    assert release_waits_for_dan(fired(3 * 3600), boot_id=BOOT, last_auto_release_at=released, now=T + 30 * 60)
