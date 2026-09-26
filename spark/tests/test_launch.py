import json
import os
import re
import stat
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from spark import launch
from spark.admission import Decision, admit
from spark.hold import Hold, read_hold, release_hold, write_hold
from spark.memory import MemInfo, parse_meminfo
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
    assert not decision.ok and "brake" in decision.reason and "spark brake --release" in decision.reason


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


def test_launch_execs_the_engine_when_it_fits(tmp_path, monkeypatch):
    calls = {}
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 70.0))
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: calls.setdefault("oom", True))
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: calls.update(file=f, argv=a))
    code = launch.main_launch(["coder", "--", "/bin/engine", "--port", "5800"], registry=FIXTURE, state=tmp_path)
    assert code == 0 and calls == {"oom": True, "file": "/bin/engine", "argv": ["/bin/engine", "--port", "5800"]}


def test_the_engine_inherits_no_api_key(tmp_path, monkeypatch):
    # llama-swap reads its keys from its environment, and every engine it starts inherits that
    # environment. Engines parse third-party model files and need none of the keys.
    calls = {}
    monkeypatch.setenv("LLAMASWAP_KEY_AGENT", "x")
    monkeypatch.setenv("LLAMASWAP_KEY_SPARK", "x")
    monkeypatch.setenv("HF_HOME", "/var/lib/local-ai/hf")
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 70.0))
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: None)
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: calls.update(env=env))
    launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    assert [name for name in calls["env"] if name.startswith("LLAMASWAP_KEY_")] == []
    assert calls["env"]["HF_HOME"] == "/var/lib/local-ai/hf"


def test_launch_refuses_with_exit_3(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 30.0))
    # the real one would raise pytest's own oom_score_adj
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: pytest.fail("must not mark the engine"))
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: pytest.fail("must not exec"))
    code = launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    assert code == 3
    assert ("spark: not starting coder: needs 28.0 GiB, 30.0 GiB available, 24 GiB reserve kept: "
            "22.0 GiB short\n") in capsys.readouterr().err


def test_launch_unknown_model_is_a_usage_error(tmp_path):
    assert launch.main_launch(["nope", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path) == 2


def test_a_refusal_is_kept_for_spark_status_until_the_next_start(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: None)
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: None)
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 30.0))
    launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    refusal = launch.read_refusal(tmp_path)
    assert refusal["model"] == "coder" and refusal["reason"].startswith("needs 28.0 GiB")
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 90.0))
    launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    assert launch.read_refusal(tmp_path) is None
