"""`spark launch <model> -- <engine cmd…>` — llama-swap runs every engine through this.

From Phase 2a it starts a model only with the gate's admission ticket, and uses the ticket up (tickets.py): nothing
starts around the gate, not a crashed engine llama-swap would restart, nor a model a request reaches llama-swap for
without the gate's admission. Behind the ticket it keeps two backstops, Phase 1's: the brake's hold, and a static fit
that never waits (admission.py). It refuses a model whose files aren't downloaded. Just before the exec it records the
start under `started/`, which the brake reads as a load in progress, in a union with the gate's record, bounded as
phase-2a.md's Task 23 says, and the gate for its bypass check. It marks the engine among the first processes the kernel or earlyoom should kill, in the brake's
order: an on-demand engine first, then a resident one. GB10's GPU memory doesn't count toward oom_score, so without
this a user's job could be chosen instead. The engine gets llama-swap's environment without its API keys: it parses
third-party model files and needs none of them.

A refusal exits 3, says why on stderr, which llama-swap keeps, and leaves the reason in `refusals/<model>.json` under
launch's folder, for the gate and `spark status`: every refusal but root's, which writes nothing. Launch never sleeps or retries: the gate is where a load waits. It
runs as spark, never as root: its folder is spark's, and root never writes through a path spark controls.
"""

from __future__ import annotations

import argparse
import datetime
import os
import stat
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

import yaml

from spark import paths, tickets
from spark.admission import admit
from spark.hold import read_hold
from spark.memory import boot_id, read_meminfo
from spark.registry import Model, load_registry
from spark.render import HF_HOME, model_path

REFUSALS = "refusals"  # launch's folder's third, beside tickets.TICKETS and tickets.STARTED
RECORD = ("at", "model", "code", "reason")  # a refusal's fields, each text
KEY_PREFIX = "LLAMASWAP_KEY_"
# An engine's own oom_score_adj, set before the exec so the engine keeps it. A model's GPU memory isn't in its engine's
# RSS (Phase 1, Task 13: RSS 0.4-2.1 GiB against 2-25 GiB on the GPU), so at one value for all the engines their scores
# sat within 9 of each other, and earlyoom's dry run picked Gemma, a resident, before the on-demand coder. A resident
# engine gets 900 and an on-demand one 1000, so earlyoom takes the on-demand coder first, as the brake does, unless a
# resident's RSS exceeds the coder's by a tenth of RAM plus swap (what 100 points of adj are worth), and every engine
# still goes before a process at 0 (Phase 1's council, 2026-09-28). Seen on the box after that day's deploy: earlyoom's
# dry run picked the coder's engine (adj 1000) over the two residents (900).
OOM_SCORE_ADJ = Path("/proc/self/oom_score_adj")
OOM_ADJ_RESIDENT, OOM_ADJ_ON_DEMAND = 900, 1000


def engine_env(env: Mapping[str, str]) -> dict[str, str]:
    """llama-swap's environment without its API keys, which every engine would otherwise inherit."""
    return {name: value for name, value in env.items() if not name.startswith(KEY_PREFIX)}


def _mark_first_to_kill(name: str, resident: bool) -> None:
    adj = OOM_ADJ_RESIDENT if resident else OOM_ADJ_ON_DEMAND
    try:
        OOM_SCORE_ADJ.write_text(str(adj))
    except OSError as err:  # not Linux, or not permitted: the engine still starts, and the brake still stands
        # One line on stderr, which llama-swap keeps, flushed now: the exec that follows would lose a buffered one.
        print(f"spark: {name} starts without oom_score_adj {adj} ({err}), so the kernel and earlyoom may kill another "
              "process before it", file=sys.stderr, flush=True)


def _refusal_path(launch: Path, model: str) -> Path:
    """`<launch>/refusals/<model>.json`; a name that isn't a model's raises ValueError (tickets.record_path)."""
    return tickets.record_path(launch, REFUSALS, model)


def record_refusal(launch: Path, model: str, code: str, reason: str) -> None:
    """Leave the reason where the gate and `spark status` find it; the client only sees a failed start. One record per
    model, swapped in whole, so a reader never sees part of one."""
    at = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        tickets.write_whole(_refusal_path(launch, model), {"at": at, "model": model, "code": code, "reason": reason})
    except (OSError, ValueError):
        pass  # the reason still reaches llama-swap through stderr, and its in-memory buffer


def _check(path: Path, model: str) -> tuple[dict | None, str | None]:
    try:
        record = tickets.read_record(path, own=False)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as err:
        return None, f"the refusal record {path} can't be read: {err}"
    if not (isinstance(record, dict) and all(isinstance(record.get(field), str) for field in RECORD)
            and record["model"] == model):
        return None, (f"the refusal record {path} isn't one: at, model, code and reason must all be text, and the "
                      f"model {model}")
    return {field: record[field] for field in RECORD}, None


def check_refusal(launch: Path, model: str) -> tuple[dict | None, str | None]:
    """The model's last refusal, and what's wrong with the record when it isn't one: `spark status` says so rather
    than show nothing. (None, None) when there is no record."""
    try:
        path = _refusal_path(launch, model)
    except ValueError:
        return None, None  # no model of that name, so no record
    return _check(path, model)


def read_refusal(launch: Path, model: str) -> dict | None:
    """The model's last refusal, or None: when there is none, and when the record can't be read or isn't one."""
    return check_refusal(launch, model)[0]


def newest_refusal(launch: Path) -> tuple[dict | None, str | None]:
    """The newest of the models' refusal records, the one written last, as check_refusal gives it: `spark status`'s
    `refused` line until Task 29 reads the refusals through the gate. (None, None) when there is none; a folder that
    can't be read is said."""
    folder = Path(launch) / REFUSALS
    newest: tuple[int, str] | None = None
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                model = entry.name.removesuffix(".json")
                if entry.name.startswith(".") or model == entry.name:
                    continue  # a write not yet swapped in, or not a record
                try:
                    written = entry.stat(follow_symlinks=False).st_mtime_ns
                except FileNotFoundError:
                    continue  # cleared since the listing
                newest = max(newest, (written, model)) if newest else (written, model)
    except FileNotFoundError:
        return None, None
    except OSError as err:
        return None, f"the refusal records in {folder} can't be read: {err}"
    return (None, None) if newest is None else _check(folder / f"{newest[1]}.json", newest[1])


def clear_refusal(launch: Path, model: str) -> None:
    try:
        _refusal_path(launch, model).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass  # a stale reason only misleads the gate and `spark status`; it must never stop the engine's start


def _refuse(launch: Path, name: str, code: str, reason: str) -> int:
    print(f"spark: not starting {name}: {reason}", file=sys.stderr)
    record_refusal(launch, name, code, reason)
    return 3


def _not_downloaded(model: Model, hf_home: str) -> str | None:
    """Why the model's files aren't all in the Hugging Face cache at `hf_home`, or None. Each file its source names,
    where render points the engine (render.model_path, under `hf_home` in place of render's HF_HOME), must be a regular
    file once its link into the cache's blobs is followed: an interrupted download leaves a link to nothing."""
    missing = []
    for file in filter(None, (model.source.file, model.source.mmproj)):
        path = Path(hf_home) / Path(model_path(model.source, file)).relative_to(HF_HOME)
        try:
            if stat.S_ISREG(os.stat(path).st_mode):
                continue
            missing.append(f"{path} isn't a file")
        except (FileNotFoundError, NotADirectoryError):
            missing.append(f"{path} isn't there")
        except OSError as err:
            missing.append(f"{path} can't be read ({err.strerror})")
    if not missing:
        return None
    return f"its files aren't all downloaded ({'; '.join(missing)}); `make pull` fetches them"


def main_launch(argv: list[str], *, registry: Path = paths.REGISTRY, state: Path = paths.STATE,
                launch: Path = paths.LAUNCH, hf_home: str = HF_HOME, clock: Callable[[], float] = time.time) -> int:
    if "--" not in argv or argv.index("--") != 1 or len(argv) < 3:
        print("usage: spark launch <model> -- <engine command…>", file=sys.stderr)
        return 2
    name, cmd = argv[0], argv[2:]
    if os.geteuid() == 0:  # before anything is read or written in spark's folders
        print(f"spark: not starting {name}: spark launch runs as spark, never as root, since root never writes in "
              "spark's folders", file=sys.stderr)
        return 3
    try:
        reg = load_registry(registry)
    except (OSError, ValueError, yaml.YAMLError) as err:  # missing or unreadable, not YAML, or invalid
        return _refuse(launch, name, "registry", f"the registry {registry} won't load: {err}")
    model = reg.models.get(name)
    if model is None:
        print(f"spark: unknown model {name!r}", file=sys.stderr)
        return 2
    claim = tickets.claim(launch, name, clock(), boot_id=boot_id())
    if not claim.ok:
        return _refuse(launch, name, claim.code, claim.why)
    hold = read_hold(state)  # a damaged hold still holds
    decision = admit(model, read_meminfo(), reg.budget, hold)
    if not decision.ok:
        return _refuse(launch, name, "no_fit" if hold is None else "held_by_brake", decision.reason)
    missing = _not_downloaded(model, hf_home)
    if missing:
        return _refuse(launch, name, "not_downloaded", missing)
    clear_refusal(launch, name)
    try:
        tickets.mark_started(launch, claim.ticket, clock())
    except (OSError, ValueError) as err:
        return _refuse(launch, name, "start_unrecorded",
                       f"its start can't be recorded for the brake and the gate in {Path(launch) / tickets.STARTED} "
                       f"({err})")
    try:
        _mark_first_to_kill(name, model.resident)
        os.execvpe(cmd[0], cmd, engine_env(os.environ))
    except BaseException as err:  # the exec failed, so no load is in progress
        try:
            tickets.clear_started(launch, name)
        except OSError:
            pass  # it stops counting as a load in progress STARTED_EXPIRES_S after its start
        if isinstance(err, (OSError, ValueError)):  # no such engine, not permitted; a NUL in an argument
            return _refuse(launch, name, "exec_failed", f"its engine {cmd[0]} won't start: {err}")
        raise
    return 0  # reached only when execvpe is replaced in tests


def register(subparsers) -> None:
    p = subparsers.add_parser("launch", help="start an engine with the gate's ticket (llama-swap's cmd prefix)")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(func=lambda args: main_launch(args.rest))
