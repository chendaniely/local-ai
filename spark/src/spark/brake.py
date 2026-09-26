"""The minimal memory brake: warn, then hold new loads and unload models before the freeze band."""

from __future__ import annotations

import argparse
import datetime
import time
from dataclasses import dataclass, replace
from pathlib import Path

from spark import paths
from spark.hold import Hold, read_hold, release_hold, write_hold
from spark.llamaswap import LlamaSwap, LlamaSwapError, key_from_env
from spark.memory import MemInfo, read_meminfo
from spark.registry import BrakeThresholds, Registry, load_registry


@dataclass(frozen=True)
class Action:
    kind: str
    model: str | None = None


def plan_brake(mem: MemInfo, thresholds: BrakeThresholds, registry: Registry, running: list[str]) -> list[Action]:
    if mem.available_gib >= thresholds.warn_gib:
        return []
    if mem.available_gib >= thresholds.brake_gib:
        return [Action("warn")]

    def order(name: str) -> tuple[bool, float]:
        model = registry.models.get(name)
        return (model.resident if model else False, -(model.footprint_gib if model else 0.0))

    victims = sorted(running, key=order)
    return [Action("hold")] + ([Action("unload", victims[0])] if victims else [])


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def run_brake(registry, client, state_dir: Path, *, read_mem=read_meminfo, sleep=time.sleep,
              log=print, now=_now, once: bool = False) -> None:
    warned = False
    while True:
        mem = read_mem()
        try:
            running = [r.model for r in client.running() if r.state in ("starting", "ready")]
        except LlamaSwapError as err:
            log(f"brake: {err}")
            running = []
        actions = plan_brake(mem, registry.brake, registry, running)
        if not actions:
            warned = False
        for action in actions:
            if action.kind == "warn" and not warned:
                log(f"brake: warning — {mem.available_gib:.1f} GiB available (warns below {registry.brake.warn_gib:g})")
                warned = True
            elif action.kind == "hold" and read_hold(state_dir) is None:
                write_hold(state_dir, Hold(now(), f"{mem.available_gib:.1f} GiB available", ()))
                log("brake: holding new loads until `spark brake --release`")
            elif action.kind == "unload":
                try:
                    client.unload(action.model)
                except LlamaSwapError as err:
                    log(f"brake: {err}")
                    continue
                hold = read_hold(state_dir)
                write_hold(state_dir, replace(hold, unloaded=hold.unloaded + (action.model,)))
                log(f"brake: unloaded {action.model} at {mem.available_gib:.1f} GiB available")
        if once:
            return
        sleep(registry.brake.poll_ms / 1000)


def release(state_dir: Path) -> int:
    print("brake: hold released" if release_hold(state_dir) else "brake: no hold to release")
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser("brake", help="watch memory; unload models before the box freezes")
    p.add_argument("--once", action="store_true", help="one check, then exit")
    p.add_argument("--release", action="store_true", help="clear the hold so loads can start again")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    if args.release:
        return release(paths.STATE)
    registry = load_registry(paths.REGISTRY)
    # 2 s, not the default 10: a hung llama-swap must not hold a tick that acts on memory it just read.
    client = LlamaSwap(paths.LLAMASWAP_URL, key_from_env(args.key_env), timeout=2)
    run_brake(registry, client, paths.STATE, log=lambda m: print(m, flush=True), once=args.once)
    return 0
