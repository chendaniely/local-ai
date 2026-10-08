import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from spark import budget
from spark.budget import (ALL, Candidate, Loaded, StaticCheck, check_set, free_for_a_load, hold_after_dans_load,
                          make_room_plan, owed_gib)
from spark.registry import Budget, load_registry

FIX = Path(__file__).parent / "fixtures"
BASE = load_registry(FIX / "models.yaml")


def model(name: str, gib: float, *, resident: bool, needs_room: bool = False):
    return replace(BASE.models["coder"], name=name, label=name, footprint_gib=float(gib), resident=resident,
                   needs_room=needs_room)


def registry(*models, ceiling=102, reserve=24, idle=117, warn=28):
    return replace(BASE, budget=Budget(ceiling, reserve, idle), brake=replace(BASE.brake, warn_gib=warn),
                   models={m.name: m for m in models})


RESIDENTS = (model("gemma", 32, resident=True), model("embed", 8, resident=True), model("whisper", 3, resident=True))
CODER = model("coder", 41, resident=False)


# Rule 9's formula: footprint ≤ min(MemAvailable − reserve − owed, ceiling − committed) − starting − held.

def test_free_for_a_load_is_18_at_the_refusal_examples_moment():
    # plan.md, *What you see in Phase 2a*: the residents loaded and Dan's python job running, 48 GiB available, the
    # residents still owed 6.
    assert free_for_a_load(available=48, reserve=24, owed=6, ceiling=102, committed=43, starting=0,
                           held=0) == Decimal("18")


def test_owed_counts_rss_growth_once():
    # Gemma's load took 27 of its 32, the embeddings' 7 of 8, whisper's all 3: 6 still owed. Gemma then grows 4 GiB in
    # its RssAnon, which MemAvailable has already lost, so what it holds now is 31 and only 1 is still owed by it.
    loaded = [Loaded("gemma", 32, 27), Loaded("embed", 8, 7), Loaded("whisper", 3, 3)]
    assert owed_gib(loaded) == Decimal(6)
    loaded[0] = Loaded("gemma", 32, 31)
    assert owed_gib(loaded) == Decimal(2)


def test_owed_is_never_negative():
    # A model holding more than its footprint is owed nothing: it never gives the others room it took.
    assert owed_gib([Loaded("embed", 8, 9)]) == Decimal(0)


def test_the_ceiling_term_binds_only_below_idle_less_the_reserve():
    at_idle = dict(available=117, reserve=24, owed=0, committed=0, starting=0, held=0)
    assert free_for_a_load(ceiling=102, **at_idle) == Decimal(93)
    assert free_for_a_load(ceiling=90, **at_idle) == Decimal(90)


def test_starting_and_held_are_subtracted():
    assert free_for_a_load(available=117, reserve=24, owed=0, ceiling=102, committed=43, starting=41,
                           held=10) == Decimal(8)


def test_dans_load_draws_the_room_outside_the_hold_first():
    # plan.md: make-room holds 41 for Dan with 9 more free for a load; his coder, 41, takes the 9 first and 32 of the
    # hold, which shrinks to the 9 the coder didn't need from it.
    assert hold_after_dans_load(free_outside_hold=9, hold=41, footprint=41) == Decimal(9)
    assert hold_after_dans_load(free_outside_hold=9, hold=41, footprint=5) == Decimal(41)
    assert hold_after_dans_load(free_outside_hold=9, hold=41, footprint=50) == Decimal(0)


def test_dans_load_takes_from_the_hold_only_what_the_room_outside_it_cant_cover():
    # plan.md's no_fit example: 36 GiB available, less the 24 GiB reserve and a 70 GiB hold, leaves −58 outside it,
    # since Dan's job has taken that room. His 5 GiB load shrinks the hold by the 5 nothing outside it covers, to 65:
    # the hold still counts what his job allocated, until he ends it (rule 4), and is never re-based to what's left.
    assert hold_after_dans_load(free_outside_hold=-58, hold=70, footprint=5) == Decimal(65)


# make-room's plan: plan.md's list, with the residents and the coder loaded and 9 GiB free for a load.

def candidate(name: str, gib: float, resident: bool) -> Candidate:
    return Candidate(model=name, label=name, gib=gib, resident=resident, pinned=False, session=None, inflight=0,
                     oldest_s=None, idle_min=None if resident else 12.0)


LOADED = [candidate("gemma", 32, True), candidate("embed", 8, True), candidate("whisper", 3, True),
          candidate("coder", 41, False)]


def test_make_room_40_unloads_the_coder_and_leaves_50():
    plan = make_room_plan(Decimal(40), Decimal(9), LOADED)
    assert [c.model for c in plan.candidates] == ["coder", "gemma", "embed", "whisper"]  # the largest first
    assert plan.unload == ["coder"]
    assert plan.free_after_gib == Decimal(50) and plan.enough
    assert plan.most_gib == Decimal(93)


def test_make_room_70_unloads_the_coder_and_gemma():
    plan = make_room_plan(Decimal(70), Decimal(9), LOADED)
    assert plan.unload == ["coder", "gemma"]
    assert plan.free_after_gib == Decimal(82) and plan.enough


def test_make_room_asked_for_70_with_the_python_job_reports_61():
    # plan.md: "Unloading everything leaves 61 GiB free for a load, not 70."
    plan = make_room_plan(Decimal(70), Decimal(18), LOADED[:3])
    assert plan.unload == ["gemma", "embed", "whisper"]
    assert not plan.enough
    assert plan.most_gib == Decimal(61) and plan.free_after_gib == Decimal(61)


def test_make_room_all_unloads_everything():
    plan = make_room_plan(ALL, Decimal(9), LOADED)
    assert plan.unload == ["coder", "gemma", "embed", "whisper"]
    assert plan.enough
    assert plan.free_after_gib == plan.most_gib == Decimal(93)


def test_make_room_lists_the_largest_first_and_a_tie_by_name():
    plan = make_room_plan(Decimal(8), Decimal(0), [candidate("b", 8, True), candidate("c", 3, True),
                                                   candidate("a", 8, True)])
    assert [c.model for c in plan.candidates] == ["a", "b", "c"]
    assert plan.unload == ["a"] and plan.free_after_gib == Decimal(8) and plan.enough


# render's checks of the registry: errors that refuse it, and warnings it prints.

def test_the_2a_set_passes_with_no_warning():
    assert check_set(registry(*RESIDENTS, CODER)) == StaticCheck(errors=[], warnings=[])


def test_a_set_over_the_ceiling_is_a_warning():
    # Dan's decision (2026-10-07, after the forward-and-back council): the gate admits each load against live memory,
    # so the whole registry needn't fit the ceiling at once, and later phases' registries won't.
    check = check_set(registry(*RESIDENTS, CODER, model("extra", 20, resident=False)))
    assert check.errors == []
    assert [w for w in check.warnings if "104.0" in w and "102.0" in w]


def test_residents_and_the_reserve_must_fit_idle_memavailable():
    # The residents load together at boot: 103 against min(117 − 24, 102) = 93 free for a load with nothing loaded.
    check = check_set(registry(*RESIDENTS, model("big", 60, resident=True)))
    assert "103.0" in check.errors[0] and "93.0" in check.errors[0]


@pytest.mark.parametrize("on_demand", [(), (model("big", 30, resident=False, needs_room=True),), (CODER,)],
                         ids=["none", "one that needs room", "a plain one"])
def test_the_residents_must_fit_under_the_ceiling_too(on_demand):
    # The controller's ruling at Task 5's review (2026-10-07): the residents load together at boot, so they fit under
    # both terms, idle less the reserve and the ceiling: 43 against min(117 − 24, 40) = 40, whatever else is listed.
    check = check_set(registry(*RESIDENTS, *on_demand, ceiling=40))
    assert check.errors[0].startswith("the always-loaded models ")
    assert "43.0" in check.errors[0] and "40.0" in check.errors[0]
    assert not [line for line in check.errors + check.warnings if re.search(r"-\d", line)]  # never a negative number


@pytest.mark.parametrize("budget_terms", [dict(ceiling=43.3), dict(idle=67.3)], ids=["ceiling", "idle less the reserve"])
def test_residents_exactly_at_the_room_pass(budget_terms):
    # The gate admits a footprint at most what's free for a load, so residents of exactly the room fit, under either
    # term. In binary floats 32 + 8.1 + 3.2 is 43.300000000000004, which would refuse them.
    exact = (model("gemma", 32, resident=True), model("embed", 8.1, resident=True), model("whisper", 3.2, resident=True))
    assert check_set(registry(*exact, **budget_terms)).errors == []


def test_a_room_under_a_tenth_reads_nothing_is_free():
    # The controller's ruling at Task 5's re-review (2026-10-07): a room that rounds down to 0.0 reads as nothing, not
    # "0.0 GiB is free". Beside the residents' 43 with idle 67.05: min(67.05 − 43 − 24, 102 − 43) leaves 0.05.
    check = check_set(registry(*RESIDENTS, model("small", 1, resident=False), idle=67.05))
    assert len(check.errors) == 1
    assert check.errors[0].startswith("small: needs 1.0 GiB, but nothing is free for a load beside ")


def test_an_on_demand_model_that_cant_load_beside_the_residents_is_refused():
    # The gate's formula at idle with the residents loaded: min(117 − 43 − 24, 102 − 43) = 50, against 57.
    check = check_set(registry(*RESIDENTS, model("big", 57, resident=False)))
    assert len(check.errors) == 1
    assert check.errors[0].startswith("big: ") and "57.0" in check.errors[0] and "50.0" in check.errors[0]


def test_a_model_that_needs_room_is_checked_against_the_box_less_the_reserve():
    # It loads only after make-room has freed room for it, residents included: min(117 − 24, 102) = 93.
    assert check_set(registry(*RESIDENTS, model("big", 57, resident=False, needs_room=True))).errors == []
    check = check_set(registry(*RESIDENTS, model("big", 95, resident=False, needs_room=True)))
    assert len(check.errors) == 1
    assert check.errors[0].startswith("big: ") and "95.0" in check.errors[0] and "93.0" in check.errors[0]


def test_a_model_that_needs_room_fits_under_the_ceiling_too():
    # A ceiling measured below idle less the reserve binds: min(117 − 24, 85) = 85, against 90.
    check = check_set(registry(*RESIDENTS, model("big", 90, resident=False, needs_room=True), ceiling=85))
    assert len(check.errors) == 1
    assert check.errors[0].startswith("big: ") and "90.0" in check.errors[0] and "85.0" in check.errors[0]


def test_render_warns_when_everything_loaded_sits_under_the_warn_line():
    # 43 + 41 + 10 = 94, within the ceiling; 117 − 94 leaves 23 available, under the warn line, 28.
    check = check_set(registry(*RESIDENTS, CODER, model("small", 10, resident=False)))
    assert check.errors == []
    assert len(check.warnings) == 1
    assert "23.0" in check.warnings[0] and "28.0" in check.warnings[0]


def test_a_set_past_idle_memory_says_by_how_much_never_a_negative():
    # Later phases' registries (two coders, a fallback, a larger model) may pass idle memory together: 43 + 41 + 45 is
    # 129, 12 GiB more than the 117 available with no model loaded. Plain words never show a negative number.
    check = check_set(registry(*RESIDENTS, CODER, model("second-coder", 45, resident=False)))
    assert check.errors == []
    assert [w for w in check.warnings if "12.0" in w and "117.0" in w]
    assert not [w for w in check.warnings if re.search(r"-\d", w)]


def test_render_and_the_gate_share_one_formula(monkeypatch):
    calls = []

    def spy(**given):
        calls.append(given)
        return Decimal("40.5")

    monkeypatch.setattr(budget, "free_for_a_load", spy)
    check = check_set(registry(*RESIDENTS, CODER, model("small", 10, resident=False),
                               model("big", 60, resident=False, needs_room=True)))
    nothing_loaded = dict(available=Decimal(117), reserve=Decimal(24), owed=Decimal(0), ceiling=Decimal(102),
                          committed=Decimal(0), starting=Decimal(0), held=Decimal(0))
    beside_the_residents = dict(available=Decimal(74), reserve=Decimal(24), owed=Decimal(0), ceiling=Decimal(102),
                                committed=Decimal(43), starting=Decimal(0), held=Decimal(0))
    # Once for the residents, then once per on-demand model, in the registry's order: the coder and the small one
    # beside the residents, the one that needs room with nothing loaded.
    assert calls == [nothing_loaded, beside_the_residents, beside_the_residents, nothing_loaded]
    # Each check's room is the formula's answer, the spy's 40.5: against the residents' 43, the coder's 41 and the
    # big one's 60; the small one's 10 fits.
    assert [error.split(" ")[0] for error in check.errors] == ["the", "coder:", "big:"]
    assert all("40.5" in error for error in check.errors)


def test_the_numbers_add_up_exactly():
    # In binary floats 52.3 − 24 is 28.299999999999997, which would refuse a 28.3 GiB model that fits exactly.
    free = free_for_a_load(available=52.3, reserve=24, owed=0, ceiling=102, committed=0, starting=0, held=0)
    assert isinstance(free, Decimal) and free == Decimal("28.3")


def test_each_formula_is_exact_on_its_own():
    # Each runs in a decimal context of its own, never the caller's 28 digits, which would round 1e30 − 0.1 to 1e30.
    assert free_for_a_load(available=1e30, reserve=0.1, owed=0, ceiling=1e30, committed=0, starting=0,
                           held=0) == Decimal("999999999999999999999999999999.9")
    assert owed_gib([Loaded("big", 1e30, 0.1)]) == Decimal("999999999999999999999999999999.9")
    assert hold_after_dans_load(free_outside_hold=0.1, hold=1e30, footprint=0.2) == Decimal(
        "999999999999999999999999999999.9")
    plan = make_room_plan(Decimal("1E+30"), 0.1, [candidate("big", 1e30, True)])
    assert plan.most_gib == plan.free_after_gib == Decimal("1000000000000000000000000000000.1") and plan.enough
