"""The Phase 1 launch check: the brake's hold, then a static fit against MemAvailable − reserve, with
MemAvailable capped at what the GPU can allocate."""

from __future__ import annotations

from dataclasses import dataclass

from spark.hold import Hold
from spark.memory import MemInfo
from spark.registry import Budget, Model


@dataclass(frozen=True)
class Decision:
    ok: bool
    reason: str


def admit(model: Model, mem: MemInfo, budget: Budget, hold: Hold | None) -> Decision:
    if hold is not None:
        return Decision(
            False,
            f"the memory brake has held new loads since {hold.since} ({hold.reason}); "
            "run `spark brake --release` once memory is back",
        )
    available = min(mem.available_gib, budget.allocatable_gib)  # overcommitting can hard-freeze a GB10
    if model.footprint_gib > available - budget.reserve_gib:
        capped = ", capped at what the GPU can allocate" if available < mem.available_gib else ""
        return Decision(
            False,
            f"needs ~{model.footprint_gib:.0f} GiB, {available:.0f} GiB available{capped} "
            f"({budget.reserve_gib:.0f} GiB reserve kept)",
        )
    return Decision(True, "fits")
