"""`spark launch <model> -- <engine cmd…>` — llama-swap runs every engine through this.

It refuses a load while the brake holds or when the model doesn't fit, and marks the engine as the
first process the kernel or earlyoom should kill — GB10's GPU memory may not count toward
oom_score, so without this a user's job could be chosen instead. The engine gets llama-swap's
environment without its API keys: it parses third-party model files and needs none of them.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import yaml

from spark import paths
from spark.admission import admit
from spark.hold import read_hold
from spark.memory import read_meminfo
from spark.registry import load_registry

REFUSAL = "last-refusal.json"
RECORD = ("at", "model", "reason")  # a refusal's fields, each text
KEY_PREFIX = "LLAMASWAP_KEY_"


def engine_env(env: Mapping[str, str]) -> dict[str, str]:
    """llama-swap's environment without its API keys, which every engine would otherwise inherit."""
    return {name: value for name, value in env.items() if not name.startswith(KEY_PREFIX)}


def _mark_first_to_kill() -> None:
    try:
        Path("/proc/self/oom_score_adj").write_text("1000")
    except OSError:
        pass  # not Linux, or not permitted — the brake still stands


def record_refusal(state: Path, model: str, reason: str) -> None:
    """Leave the reason where `spark status` shows it; the client only sees a failed start. The record is
    swapped in whole, so a reader never sees part of one."""
    at = datetime.datetime.now().isoformat(timespec="seconds")
    path = Path(state) / REFUSAL
    tmp = path.with_name(f".{REFUSAL}.{os.getpid()}")  # one per process: refusals can come together
    try:
        tmp.write_text(json.dumps({"at": at, "model": model, "reason": reason}))
        os.replace(tmp, path)
    except OSError:
        pass  # the reason still reaches llama-swap through stderr


def check_refusal(state: Path) -> tuple[dict | None, str | None]:
    """The last refusal, and what's wrong with the record when it isn't one: `spark status` says so rather than show
    nothing. (None, None) when there is no record."""
    path = Path(state) / REFUSAL
    try:
        record = json.loads(path.read_text())
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError, RecursionError) as err:  # RecursionError: JSON nested too deep
        return None, f"the refusal record {path} can't be read: {err}"
    if not (isinstance(record, dict) and all(isinstance(record.get(field), str) for field in RECORD)):
        return None, f"the refusal record {path} isn't one: at, model and reason must all be text"
    return {field: record[field] for field in RECORD}, None


def read_refusal(state: Path) -> dict | None:
    """The last refusal, or None: when there is none, and when the record can't be read or isn't one."""
    return check_refusal(state)[0]


def clear_refusal(state: Path) -> None:
    try:
        (Path(state) / REFUSAL).unlink(missing_ok=True)
    except OSError:
        pass  # a stale reason only misleads `spark status`; it must never stop the engine's start


def _refuse(state: Path, name: str, reason: str) -> int:
    print(f"spark: not starting {name}: {reason}", file=sys.stderr)
    record_refusal(state, name, reason)
    return 3


def main_launch(argv: list[str], *, registry: Path = paths.REGISTRY, state: Path = paths.STATE) -> int:
    if "--" not in argv or argv.index("--") != 1 or len(argv) < 3:
        print("usage: spark launch <model> -- <engine command…>", file=sys.stderr)
        return 2
    name, cmd = argv[0], argv[2:]
    try:
        reg = load_registry(registry)
    except (OSError, ValueError, yaml.YAMLError) as err:  # missing or unreadable, not YAML, or invalid
        return _refuse(state, name, f"the registry {registry} won't load: {err}")
    model = reg.models.get(name)
    if model is None:
        print(f"spark: unknown model {name!r}", file=sys.stderr)
        return 2
    decision = admit(model, read_meminfo(), reg.budget, read_hold(state))  # a damaged hold still holds
    if not decision.ok:
        return _refuse(state, name, decision.reason)
    clear_refusal(state)
    _mark_first_to_kill()
    os.execvpe(cmd[0], cmd, engine_env(os.environ))
    return 0  # reached only when execvpe is replaced in tests


def register(subparsers) -> None:
    p = subparsers.add_parser("launch", help="start an engine if it fits (llama-swap's cmd prefix)")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=lambda args: main_launch(args.rest))
