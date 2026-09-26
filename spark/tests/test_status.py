from pathlib import Path

from spark.hold import Hold
from spark.llamaswap import Running
from spark.memory import MemInfo
from spark.registry import load_registry
from spark.status import format_text, gather

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def test_headroom_is_measured_to_the_brake():
    status = gather(MemInfo(121.7, 70.0), REG, [], None)
    assert status["memory"]["headroom_before_brake_gib"] == 50.0


def test_loaded_models_carry_their_class_and_footprint():
    status = gather(MemInfo(121.7, 70.0), REG, [Running("coder", "ready")], None)
    assert status["loaded"] == [{"model": "coder", "state": "ready", "resident": False, "footprint_gib": 28.0}]


def test_text_names_the_models_and_the_brake():
    text = format_text(gather(MemInfo(121.7, 70.0), REG, [Running("coder", "ready")], None))
    assert "70 GiB available of 122 GiB" in text and "50 GiB before the brake" in text
    assert "coder (on demand, ~28 GiB, ready)" in text and "brake    off" in text


def test_unreachable_llama_swap_is_reported_not_raised():
    assert "llama-swap unreachable" in format_text(gather(MemInfo(121.7, 70.0), REG, None, None))


def test_a_hold_is_shown_with_how_to_release():
    text = format_text(gather(MemInfo(121.7, 70.0), REG, [], Hold("t0", "18.0 GiB available", ("coder",))))
    assert "HOLDING since t0" in text and "spark brake --release" in text


def test_the_last_refused_load_is_explained():
    refusal = {"at": "t1", "model": "coder", "reason": "needs ~28 GiB, 40 GiB available (24 GiB reserve kept)"}
    text = format_text(gather(MemInfo(121.7, 40.0), REG, [], None, refusal))
    assert "refused  coder at t1: needs ~28 GiB, 40 GiB available (24 GiB reserve kept)" in text
