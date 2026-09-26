"""The Phase 1 launch check: the brake's hold, then a static fit against MemAvailable − reserve, with
MemAvailable capped at what the GPU can allocate."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from spark.hold import Hold
from spark.memory import MemInfo
from spark.registry import Budget, Model

TENTH = Decimal("0.1")


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str


def _gib(value: float) -> Decimal:
    """The number as the registry or /proc/meminfo gave it. In binary floats 52.3 − 24 is
    28.299999999999997, which would refuse a 28.3 GiB model that fits exactly."""
    return Decimal(repr(value))


def admit(model: Model, mem: MemInfo, budget: Budget, hold: Hold | None) -> Decision:
    if hold is not None:
        return Decision(
            False,
            f"the memory brake has held new loads since {hold.since} ({hold.reason}); "
            "run `spark brake --release` once memory is back",
        )
    available = min(mem.available_gib, budget.allocatable_gib)  # overcommitting can hard-freeze a GB10
    needed, free, reserve = _gib(model.footprint_gib), _gib(available), _gib(budget.reserve_gib)
    if needed <= free - reserve:
        return Decision(True, "fits")
    # One decimal, each rounded against the load: the need up, what's available down. So the numbers
    # shown always add up to the refusal they explain, and the shortfall never shows as 0.0.
    needed, free = needed.quantize(TENTH, ROUND_CEILING), free.quantize(TENTH, ROUND_FLOOR)
    capped = ", capped at what the GPU can allocate" if available < mem.available_gib else ""
    return Decision(
        False,
        f"needs {needed:f} GiB, {free:f} GiB available{capped}, {reserve:f} GiB reserve kept: "
        f"{needed - (free - reserve):f} GiB short",
    )
