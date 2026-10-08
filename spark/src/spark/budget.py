"""Rule 9, the budget (website/design/plan.md, *Admission and memory rules*): the gate's formula for a load, the growth
the loaded models are still owed, what a load of Dan's takes from make-room's hold, make-room's plan, and render's checks
of the registry, which evaluate the gate's formula at idle, so that render and the gate hold one formula between them.

Every number is an exact Decimal: a float or an int as its repr reads, as admission._gib does, and each function here
sets its own precision, DIGITS digits, on a copy of its caller's decimal context, whatever the caller's precision. In
binary floats 52.3 − 24 is 28.299999999999997, which would refuse a 28.3 GiB model that fits exactly."""

from __future__ import annotations

import functools
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from enum import Enum
from typing import Literal

from spark.registry import Registry

Number = float | Decimal  # an int is welcome too
TENTH = Decimal("0.1")
ZERO = Decimal(0)
# Decimal's default 28 digits can't hold an absurd registry number, 1e30 say, to a tenth, and a refusal would crash
# instead of saying why. Each function here runs with 400, which hold the largest float's (about 1.8e308) to a tenth.
DIGITS = 400


def _exact(func):
    """`func`, run with DIGITS digits of precision, whatever its caller's (28 by default): localcontext copies the
    caller's decimal context and sets only the precision on the copy, which it drops on the way out."""

    @functools.wraps(func)
    def exact(*args, **kwargs):
        with localcontext(prec=DIGITS):
            return func(*args, **kwargs)

    return exact


def _gib(value: Number) -> Decimal:
    """`value` exactly: a Decimal as it is, a float or an int as its repr reads."""
    return value if isinstance(value, Decimal) else Decimal(repr(value))


def _up(value: Decimal) -> str:
    """A need, to one decimal, rounded up: with the room rounded down, the two never read as a fit."""
    return f"{value.quantize(TENTH, ROUND_CEILING):f}"


def _down(value: Decimal) -> str:
    """A room, to one decimal, rounded down."""
    return f"{value.quantize(TENTH, ROUND_FLOOR):f}"


def _free(room: Decimal) -> str:
    """What is free for a load, in plain words, which never show a negative number: a room that rounds down to 0 (below
    0.1 GiB) reads *nothing is free for a load*, not "0.0 GiB is free"."""
    shown = room.quantize(TENTH, ROUND_FLOOR)
    return f"{shown:f} GiB is free for a load" if shown > 0 else "nothing is free for a load"


@dataclass(frozen=True)
class Loaded:
    """A loaded model, for *owed*. `held_now_gib` is what its load took, the fall in MemAvailable across it, plus its
    engine's RssAnon growth since, when the registry's gate.owed_reads_rss is on for its engine kind: the gate adds
    that, not this module."""

    name: str
    footprint_gib: float  # from the registry that loaded it, never a newer one's
    held_now_gib: float


@_exact
def owed_gib(loaded: Iterable[Loaded]) -> Decimal:
    """The growth the loaded models are still owed: each one's footprint less what it holds now, never below 0."""
    return sum((max(ZERO, _gib(m.footprint_gib) - _gib(m.held_now_gib)) for m in loaded), ZERO)


@_exact
def free_for_a_load(*, available: Number, reserve: Number, owed: Number, ceiling: Number, committed: Number,
                    starting: Number, held: Number) -> Decimal:
    """Free for a load: min(available − reserve − owed, ceiling − committed) − starting − held. A model loads when its
    footprint is at most this. `available` is MemAvailable; `ceiling`, the CUDA-allocatable ceiling; `committed`, the
    loaded models' footprints; `starting`, the footprint of any model still starting; `held`, make-room's hold, which a
    key group that uses it doesn't count."""
    room = min(_gib(available) - _gib(reserve) - _gib(owed), _gib(ceiling) - _gib(committed))
    return room - _gib(starting) - _gib(held)


@_exact
def hold_after_dans_load(*, free_outside_hold: Number, hold: Number, footprint: Number) -> Decimal:
    """make-room's hold after a load into it (a key group with uses_hold): the load takes what is free for a load
    outside the hold first, and the hold shrinks only by the part of the load that room couldn't cover (rule 4). Once
    Dan's job has taken the room, what's free outside the hold is below 0, and counts as none: the hold still counts what
    his job allocated, until he ends it."""
    return max(ZERO, _gib(hold) - max(ZERO, _gib(footprint) - max(ZERO, _gib(free_outside_hold))))


class _All(Enum):
    ALL = "all"


ALL = _All.ALL  # make-room's --all: every candidate, whatever is free


@dataclass(frozen=True)
class Candidate:
    """A loaded model make-room could unload, with what its list shows of it."""

    model: str
    label: str
    # Its footprint. Unloading it raises free for a load by at least that much: MemAvailable gains what it holds,
    # *owed* loses the rest, and *committed* the whole.
    gib: float
    resident: bool
    pinned: bool
    session: str | None  # the session keeping it loaded, by its label
    inflight: int  # its requests in flight
    oldest_s: float | None  # the oldest one's age, in seconds
    idle_min: float | None  # minutes since its last request


@dataclass(frozen=True)
class RoomPlan:
    candidates: list[Candidate]  # the largest first, a tie by name
    unload: list[str]  # the models to unload, in that order
    free_after_gib: Decimal  # free for a load once they are gone
    enough: bool  # whether that reaches the target
    most_gib: Decimal  # free for a load with every candidate gone


@_exact
def make_room_plan(target: Decimal | Literal[_All.ALL], free_now_gib: Number,
                   candidates: Iterable[Candidate]) -> RoomPlan:
    """What make-room unloads to make `target` GiB free for a load: the fewest of the largest, in order. When even all
    of them fall short, all of them, and not `enough`; with ALL, every one."""
    ordered = sorted(candidates, key=lambda c: (-_gib(c.gib), c.model))
    free_now = _gib(free_now_gib)
    most = free_now + sum((_gib(c.gib) for c in ordered), ZERO)
    if target is ALL:
        return RoomPlan(ordered, [c.model for c in ordered], most, True, most)
    goal, free, unload = _gib(target), free_now, []
    for c in ordered:
        if free >= goal:
            break
        unload.append(c.model)
        free += _gib(c.gib)
    return RoomPlan(ordered, unload, free, free >= goal, most)


@dataclass(frozen=True)
class StaticCheck:
    errors: list[str]  # each refuses the registry
    warnings: list[str]  # each is said, and the registry still renders


@_exact
def check_set(registry: Registry) -> StaticCheck:
    """render's checks of the registry. Each error is the gate's own formula, free_for_a_load, at idle with nothing else
    running. Errors: the always-loaded models, which load together at boot, don't fit with nothing loaded (idle
    MemAvailable less the reserve, within the ceiling); an on-demand model doesn't fit beside them, so the gate would
    refuse it with nothing else running; a model marked needs_room, which loads only once make-room has freed room for
    it, the always-loaded models' included, doesn't fit with nothing loaded. Warnings (Dan's decision, 2026-10-07: the
    gate admits each load against live memory, so the whole registry needn't fit at once): every model loaded at once
    would pass the ceiling, or leave memory available under the brake's warn line."""
    errors: list[str] = []
    warnings: list[str] = []
    b = registry.budget
    idle, reserve, ceiling = _gib(b.idle_available_gib), _gib(b.reserve_gib), _gib(b.allocatable_gib)
    models = list(registry.models.values())
    residents = sum((_gib(m.footprint_gib) for m in models if m.resident), ZERO)
    everything = sum((_gib(m.footprint_gib) for m in models), ZERO)

    def free(*, beside_the_residents: bool) -> Decimal:
        loaded = residents if beside_the_residents else ZERO
        return free_for_a_load(available=idle - loaded, reserve=reserve, owed=ZERO, ceiling=ceiling, committed=loaded,
                               starting=ZERO, held=ZERO)

    room = free(beside_the_residents=False)
    if residents > room:
        errors.append(f"the always-loaded models need {_up(residents)} GiB together, but {_free(room)} with nothing "
                      "loaded at all, so they can't all load")
    for m in models:
        if m.resident:
            continue
        need, room = _gib(m.footprint_gib), free(beside_the_residents=not m.needs_room)
        if need <= room:
            continue
        if m.needs_room:
            errors.append(f"{m.name}: needs {_up(need)} GiB, but {_free(room)} with nothing loaded at all, so even "
                          "make-room can't free enough for it")
        else:
            errors.append(f"{m.name}: needs {_up(need)} GiB, but {_free(room)} beside the always-loaded models with "
                          "nothing else running, so the gate would refuse it even then (needs_room: true marks a model "
                          "that loads only once make-room has freed room for it)")
    if everything > ceiling:
        warnings.append(f"every model loaded at once would take {_up(everything)} GiB, above the CUDA-allocatable "
                        f"ceiling, {_down(ceiling)} GiB: they can't all be loaded together, and the gate admits each "
                        "load only as memory allows")
    left, warn = idle - everything, _gib(registry.brake.warn_gib)
    if left < 0:
        warnings.append(f"every model loaded at once would need {_up(-left)} GiB more than the {_down(idle)} GiB "
                        "available with no model loaded")
    elif left < warn:
        warnings.append(f"every model loaded at once would leave {_down(left)} GiB available, under the brake's warn "
                        f"line, {_up(warn)} GiB")
    return StaticCheck(errors, warnings)
