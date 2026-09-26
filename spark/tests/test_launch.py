from dataclasses import replace
from pathlib import Path

import pytest

from spark import launch
from spark.admission import admit
from spark.hold import Hold, read_hold, release_hold, write_hold
from spark.memory import MemInfo, parse_meminfo
from spark.registry import load_registry

FIXTURE = Path(__file__).parent / "fixtures" / "models.yaml"
MEMINFO = "MemTotal:       127622144 kB\nMemFree:  1024 kB\nMemAvailable:   73400320 kB\n"


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
    assert decision.reason == "needs ~28 GiB, 40 GiB available (24 GiB reserve kept)"


def test_admit_counts_no_more_memory_than_the_gpu_can_allocate():
    # MemAvailable can be above what CUDA can allocate (reported near 102 GiB), and overcommitting can
    # hard-freeze a GB10: the fit uses the lower of the two.
    reg = load_registry(FIXTURE)
    big = replace(reg.models["coder"], footprint_gib=90)
    decision = admit(big, MemInfo(121.7, 118.0), reg.budget, None)
    assert not decision.ok  # 118 − 24 = 94 would fit; 102 − 24 = 78 doesn't
    assert decision.reason == ("needs ~90 GiB, 102 GiB available, capped at what the GPU can allocate "
                               "(24 GiB reserve kept)")


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
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: pytest.fail("must not exec"))
    code = launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    assert code == 3
    assert "spark: not starting coder: needs ~28 GiB" in capsys.readouterr().err


def test_launch_unknown_model_is_a_usage_error(tmp_path):
    assert launch.main_launch(["nope", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path) == 2


def test_a_refusal_is_kept_for_spark_status_until_the_next_start(tmp_path, monkeypatch):
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: None)
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: None)
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 30.0))
    launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    refusal = launch.read_refusal(tmp_path)
    assert refusal["model"] == "coder" and refusal["reason"].startswith("needs ~28 GiB")
    monkeypatch.setattr(launch, "read_meminfo", lambda: MemInfo(121.7, 90.0))
    launch.main_launch(["coder", "--", "/bin/engine"], registry=FIXTURE, state=tmp_path)
    assert launch.read_refusal(tmp_path) is None
