"""`spark status` — what's loaded, how much memory is left before the brake, the brake's hold, and whether llama-swap
took the brake's own key when the brake started.

It reports problems, it doesn't fail on them: what it can't read, it says so, shows the rest, and exits 0. What it says
must be true for whoever runs it. The brake's state folder is 2770 spark:spark-admin, so an account outside spark-admin
(agent) is told the hold is unknown to it, never that there is one or that there's none. Launch's folder, which holds
the refusals since Phase 2a, is 0750 spark:spark, so an account outside the spark group, Dan's included, is told the
last refusal is unknown to it, until Task 29 asks the gate instead.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import stat
from collections.abc import Mapping
from dataclasses import asdict
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

import yaml

from spark import paths
from spark.brake import FALLBACK, KeyCheck, read_key_check
from spark.hold import UNREADABLE_SINCE, Hold, read_hold
from spark.launch import REFUSALS, newest_refusal
from spark.llamaswap import LlamaSwap, LlamaSwapError, Running, key_from_env
from spark.memory import MemInfo, read_meminfo
from spark.registry import Registry, load_registry

# Nearer the brake line than this, memory shows in tenths of a GiB: a whole number there could hide which side of the
# line the box is on.
NEAR_GIB = 10
TENTH = Decimal("0.1")
# What can go wrong, in the order of the lines it bears on: problems are listed in this order.
PARTS = ("registry", "memory", "llama-swap", "loaded", "state", "key check", "refusal")


def _down(value: Decimal) -> float:
    """Rounded down to a tenth, as admission rounds what's available: what's available never shows as more than there
    is, and a box past the brake line never reads as on it, or as -0.0 (round(-0.04, 1) is -0.0; this gives -0.1)."""
    return float(value.quantize(TENTH, ROUND_FLOOR))


def gather(mem: MemInfo | None, registry: Registry | None, running: list[Running] | None, hold: Hold | None,
           refusal: dict | None = None, *, problems: Mapping[str, str] | None = None,
           can_release: bool = True, key_check: KeyCheck | None = None) -> dict:
    """What `spark status` shows, as data. A `mem`, `registry` or `running` of None couldn't be read, and `problems`
    says why, by part (PARTS): with no registry, headroom is measured to the plan's brake line, and `running` None
    with no "llama-swap" problem reads as llama-swap unreachable. A `hold`, `refusal` (the newest of launch's
    refusal records) or `key_check` (the brake's start check) of None is none. A "state" problem means the brake's
    state folder is closed to this account: the hold and the start check are then unknown, not absent. A "refusal"
    problem means the last refusal is unknown: its folder is closed to this account, or its record isn't one."""
    problems = dict(problems or {})
    if mem is not None and not (math.isfinite(mem.total_gib) and math.isfinite(mem.available_gib)):
        problems.setdefault("memory", f"memory reads that aren't numbers: {mem}")
        mem = None
    if mem is None:
        problems.setdefault("memory", "can't read memory")
    if running is None:
        problems.setdefault("llama-swap", "llama-swap unreachable")
    brake_gib = FALLBACK.brake_gib if registry is None else registry.brake.brake_gib
    memory = None
    if mem is not None:
        available = Decimal(repr(mem.available_gib))
        memory = {
            "total_gib": round(mem.total_gib, 1),
            "available_gib": _down(available),
            "headroom_before_brake_gib": _down(available - Decimal(repr(brake_gib))),
            "brake_gib": brake_gib,
        }
    loaded = None
    if running is not None:
        loaded, unlisted = [], []
        for r in running:
            model = None if registry is None else registry.models.get(r.model)
            if registry is not None and model is None:
                unlisted.append(f"{r.model} is loaded but isn't in the registry")
            loaded.append({
                "model": r.model,
                "state": r.state,
                "resident": model.resident if model else None,
                "footprint_gib": model.footprint_gib if model else None,
            })
        if unlisted:
            problems.setdefault("loaded", "; ".join(unlisted))
    if "state" in problems:
        brake = {"state": "unknown", "since": None, "reason": None, "unloaded": None, "can_release": False}
    elif hold is None:
        brake = {"state": "no hold", "since": None, "reason": None, "unloaded": None, "can_release": False}
    else:
        stand_in = hold.since == UNREADABLE_SINCE  # read_hold's stand-in for a file it couldn't read: it still holds
        brake = {"state": "holding", "since": None if stand_in else hold.since, "reason": hold.reason,
                 "unloaded": None if stand_in else list(hold.unloaded), "can_release": can_release}
    brake["key_check"] = None if key_check is None or "state" in problems else asdict(key_check)
    return {
        "memory": memory,
        "registry_loaded": registry is not None,
        "loaded": loaded,
        "brake": brake,
        "last_refusal": refusal,
        "problems": [problems[part] for part in PARTS if part in problems]
                    + [text for part, text in problems.items() if part not in PARTS],
    }


def _memory(m: dict | None, registry_loaded: bool) -> str:
    if m is None:
        return "unknown"
    line = f"{m['brake_gib']:g} GiB" if registry_loaded else f"{m['brake_gib']:g} GiB, the plan's default"
    available, headroom = m["available_gib"], m["headroom_before_brake_gib"]
    if headroom >= NEAR_GIB:
        return (f"{math.floor(available)} GiB available of {m['total_gib']:.0f} GiB · "
                f"{math.floor(headroom)} GiB before the brake ({line})")
    where = (f"{headroom:.1f} GiB before the brake" if headroom >= 0
             else f"past the brake line by {-headroom:.1f} GiB")
    return f"{available:.1f} GiB available of {m['total_gib']:.0f} GiB · {where} ({line})"


def _loaded(entries: list[dict] | None, registry_loaded: bool) -> str:
    if entries is None:
        return "unknown"
    if not entries:
        return "nothing"
    parts = []
    for x in entries:
        if x["footprint_gib"] is not None:
            kind = "resident" if x["resident"] else "on demand"
            parts.append(f"{x['model']} ({kind}, ~{x['footprint_gib']:g} GiB, {x['state']})")
        elif registry_loaded:
            parts.append(f"{x['model']} (not in the registry, {x['state']})")
        else:  # the registry that would say didn't load
            parts.append(f"{x['model']} ({x['state']})")
    return " · ".join(parts)


def _key_check(check: dict | None) -> str:
    """What the brake line says of the brake's start check: whether llama-swap took its key when it started."""
    if check is None:
        return "no start check recorded (`make logs s=brake`)"
    if check["ok"] is None:
        return f"its start check is waiting for llama-swap (since {check['at']})"
    if check["ok"]:
        return f"llama-swap took its key at {check['at']}"
    return f"its start check FAILED at {check['at']}: {check['detail']} — `make logs s=brake`"


def _brake(b: dict) -> str:
    if b["state"] == "unknown":
        return "unknown"  # the hold and the start check alike
    if b["state"] != "holding":
        return f"no hold · {_key_check(b['key_check'])}"
    since = UNREADABLE_SINCE if b["since"] is None else b["since"]
    unloaded = "unknown" if b["unloaded"] is None else (", ".join(b["unloaded"]) or "nothing yet")
    release = "`make brake-release` to clear" if b["can_release"] else "spark-admin can release it"
    return f"HOLDING since {since} ({b['reason']}); unloaded: {unloaded} — {release} · {_key_check(b['key_check'])}"


def _shown(line: str) -> str:
    """The line with every character a terminal would act on written out instead (ESC as \\x1b, a newline as \\n):
    reasons, names and states come from files, and from whatever answers on llama-swap's port."""
    return "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in line)


def format_text(status: dict) -> str:
    lines = [
        f"memory   {_memory(status['memory'], status['registry_loaded'])}",
        f"loaded   {_loaded(status['loaded'], status['registry_loaded'])}",
        f"brake    {_brake(status['brake'])}",
    ]
    r = status["last_refusal"]
    if r:
        lines.append(f"refused  {r['model']} at {r['at']}: {r['reason']}")
    lines += [f"problem  {text}" for text in status["problems"]]
    return "\n".join(_shown(line) for line in lines)


def register(subparsers) -> None:
    p = subparsers.add_parser("status", help="what's loaded, memory headroom, the brake")
    p.add_argument("--json", action="store_true")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def _closed(folder: Path) -> bool:
    """Whether the folder (the brake's state folder, or launch's) is there but closed to this account. Then what it
    holds is unknown to it, not absent: `spark launch` and the brake run as spark, which reads them."""
    try:
        mode = os.stat(folder).st_mode
    except FileNotFoundError:
        return False  # no folder, so no file in it: launch finds the same
    except PermissionError:
        return True  # a folder on the way to it can't be searched
    except OSError:
        return False  # the reader says what's wrong, as launch would find it
    return stat.S_ISDIR(mode) and not os.access(folder, os.X_OK)  # search is what opening a file in it takes


def _one_line(err: BaseException) -> str:
    return " ".join(str(err).split())  # a YAML error spans lines, with a caret under the fault


def run(args: argparse.Namespace) -> int:
    problems: dict[str, str] = {}
    try:
        registry = load_registry(paths.REGISTRY)
    except (OSError, ValueError, yaml.YAMLError, RecursionError) as err:  # RecursionError: YAML nested too deep
        registry = None
        problems["registry"] = f"the registry {paths.REGISTRY} won't load: {_one_line(err)}"
    key = key_from_env(args.key_env)
    try:
        running = LlamaSwap(paths.LLAMASWAP_URL, key, timeout=3).running()
    except LlamaSwapError as err:  # its text never holds the key, and says "unreachable" only when nothing answered
        running = None
        problems["llama-swap"] = str(err) if key else f"{err} (no key in ${args.key_env})"
    try:
        mem = read_meminfo()
    except (OSError, ValueError) as err:
        mem = None
        problems["memory"] = f"can't read memory: {err}"
    hold = refusal = key_check = None
    if _closed(paths.STATE):
        problems["state"] = (f"this account can't read {paths.STATE}, so the brake's hold and its start check are "
                             "unknown to it; spark-admin can read them")
    else:
        hold = read_hold(paths.STATE)
        key_check, damaged = read_key_check(paths.STATE)
        if damaged:
            problems["key check"] = damaged
    closed = [folder for folder in (paths.LAUNCH, paths.LAUNCH / REFUSALS) if _closed(folder)]
    if closed:
        problems["refusal"] = f"this account can't read {closed[0]}, so the last refusal is unknown to it"
    else:
        refusal, damaged = newest_refusal(paths.LAUNCH)
        if damaged:
            problems["refusal"] = damaged
    status = gather(mem, registry, running, hold, refusal, problems=problems,
                    can_release=os.access(paths.STATE, os.W_OK | os.X_OK), key_check=key_check)
    print(json.dumps(status, indent=2, allow_nan=False) if args.json else format_text(status))
    return 0
