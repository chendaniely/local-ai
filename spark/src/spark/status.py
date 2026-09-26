"""`spark status` — what's loaded, how much memory is left before the brake, and the brake's state."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from spark import paths
from spark.hold import Hold, read_hold
from spark.launch import read_refusal
from spark.llamaswap import LlamaSwap, LlamaSwapError, Running, key_from_env
from spark.memory import MemInfo, read_meminfo
from spark.registry import Registry, load_registry


def gather(mem: MemInfo, registry: Registry, running: list[Running] | None, hold: Hold | None,
           refusal: dict | None = None) -> dict:
    loaded = None
    if running is not None:
        loaded = []
        for r in running:
            model = registry.models.get(r.model)
            loaded.append({
                "model": r.model,
                "state": r.state,
                "resident": model.resident if model else None,
                "footprint_gib": model.footprint_gib if model else None,
            })
    return {
        "memory": {
            "total_gib": round(mem.total_gib, 1),
            "available_gib": round(mem.available_gib, 1),
            "headroom_before_brake_gib": round(mem.available_gib - registry.brake.brake_gib, 1),
            "brake_gib": registry.brake.brake_gib,
        },
        "loaded": loaded,
        "brake": None if hold is None else asdict(hold) | {"unloaded": list(hold.unloaded)},
        "last_refusal": refusal,
    }


def format_text(status: dict) -> str:
    m = status["memory"]
    lines = [
        f"memory   {m['available_gib']:.0f} GiB available of {m['total_gib']:.0f} GiB · "
        f"{m['headroom_before_brake_gib']:.0f} GiB before the brake ({m['brake_gib']:g} GiB)"
    ]
    if status["loaded"] is None:
        lines.append("loaded   ? (llama-swap unreachable)")
    elif not status["loaded"]:
        lines.append("loaded   nothing")
    else:
        parts = []
        for x in status["loaded"]:
            kind = "resident" if x["resident"] else "on demand"
            parts.append(f"{x['model']} ({kind}, ~{x['footprint_gib']:.0f} GiB, {x['state']})")
        lines.append("loaded   " + " · ".join(parts))
    b = status["brake"]
    if b is None:
        lines.append("brake    off")
    else:
        unloaded = ", ".join(b["unloaded"]) or "nothing yet"
        lines.append(f"brake    HOLDING since {b['since']} ({b['reason']}); unloaded: {unloaded} — "
                     "`spark brake --release` to clear")
    r = status["last_refusal"]
    if r:
        lines.append(f"refused  {r['model']} at {r['at']}: {r['reason']}")
    return "\n".join(lines)


def register(subparsers) -> None:
    p = subparsers.add_parser("status", help="what's loaded, memory headroom, the brake")
    p.add_argument("--json", action="store_true")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    registry = load_registry(paths.REGISTRY)
    try:
        running = LlamaSwap(paths.LLAMASWAP_URL, key_from_env(args.key_env), timeout=3).running()
    except LlamaSwapError:
        running = None
    status = gather(read_meminfo(), registry, running, read_hold(paths.STATE), read_refusal(paths.STATE))
    print(json.dumps(status, indent=2) if args.json else format_text(status))
    return 0
