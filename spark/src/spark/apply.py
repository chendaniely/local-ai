"""`spark apply` — render, validate, say render's warnings, show the diff of what changes, deploy under
/opt/local-ai, and restart only what changed (Task 31). plan_restarts decides what: the brake and the gate for any
change to the app or the registry, at once; llama-swap for its config or unit, and the front for its own modules
(spark.front.FRONT_MODULES, with the app's environment), the registry's key groups, or `--restart
local-ai-front.service` (`make restart-front`), only once nothing is in flight, which is Task 32's wait. Until that
wait is built, those two are restarted only while llama-swap has no model loaded, or with --now: llama-swap's restart
stops every model, and the front's cuts off every request in flight; while it can't tell what is loaded, it counts
them as loaded. Restarts follow restart_order: the brake, the gate, the front, llama-swap last, then the compose unit
on its own. It asks llama-swap what is loaded before it changes anything and again just before each restart that
waits, and after llama-swap's restart waits for it to answer. After the brake's restart it checks that the brake is
still running.

The units and the Compose project that root runs are root's own copies, which only
`make install-units` (sudo) installs: apply stages them in /opt/local-ai/etc and never writes
root's copies. While the staged ones differ from root's, apply stages them and stops, and deploys
and restarts nothing else until root has them.

A run cut off part-way leaves the next run to finish it: each file is replaced whole, the app counts
as changed until a sync of it finishes, and a running unit that started before its files were
written or synced is restarted, as one that started before root's copy was installed is; the front
by when its own files were last written. A restart that no file's time records, the front's for the
registry's key groups and the gate's and the front's for `--restart`, is recorded by a stamp apply
touches just before it writes (KEY_GROUPS_STAMP, RESTART_FRONT_STAMP), so the next run owes it
too. The one exception is a unit whose start time apply can't read: it can't tell whether that
unit is behind, so it names the unit and the command to restart it by hand."""

from __future__ import annotations

import argparse
import difflib
import math
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from spark import paths
from spark.front import FRONT_MODULES
from spark.llamaswap import LlamaSwap, LlamaSwapAnswered, LlamaSwapError, LlamaSwapUnreachable, key_from_env
from spark.registry import Registry, load_registry
from spark.render import (DEPLOY, INTERNAL_KEY_ENVS, _load, check_budget, installed_path, render, write_atomic,
                          write_tree)
from spark.versions import load_versions, unpinned

LLAMA_SWAP_UNIT = "local-ai-llama-swap.service"
BRAKE_UNIT = "local-ai-brake.service"
COMPOSE_UNIT = "local-ai-compose.service"
FRONT_UNIT = "local-ai-front.service"
GATE_UNIT = "local-ai-gate.service"
# The long-running units: the pull unit, the S05 drill's and the failure notifier run only when started, and apply
# restarts no socket (stopping the front's would free 9100 for any account to bind).
RUNNING_UNITS = (LLAMA_SWAP_UNIT, BRAKE_UNIT, COMPOSE_UNIT, FRONT_UNIT, GATE_UNIT)
# The order apply restarts them in (Task 31): the brake, then the gate, then the front, then llama-swap last, whose
# restart stops every model; the compose unit after them, on its own, as in Phase 1, since its start can take minutes.
RESTART_ORDER = (BRAKE_UNIT, GATE_UNIT, FRONT_UNIT, LLAMA_SWAP_UNIT, COMPOSE_UNIT)
# The restarts that wait for the quiet moment (Task 32): llama-swap's stops every model, and the front's cuts off every
# request it holds. Until that wait is built, apply makes them only while llama-swap has no model loaded, or with --now.
AFTER_QUIET = (LLAMA_SWAP_UNIT, FRONT_UNIT)
UNKNOWN_START = math.inf  # a running unit whose start time can't be read: later than any file's
NOT_APP = {".venv", "__pycache__", ".pytest_cache"}
# In the deployed app's venv, written once `uv sync` succeeded and removed before a sync starts: until it is back,
# the app counts as changed, so a sync that failed or was cut off is tried again, and so is one whose venv is gone.
SYNC_STAMP = ".venv/.spark-apply-synced"
APP = "app"  # the app, among the files apply deploys itself: when its last sync finished (deployed_times)
FRONT_APP = "app: the front's files"  # the front's files of the app: when the last of them was written (deployed_times)
# The app's environment: the front runs what these install (uvicorn, httpx and the rest), so a change to one changes
# the front's code as a change to one of its modules does, and `uv sync` replaces them under it.
ENVIRONMENT = ("pyproject.toml", "uv.lock", ".python-version")
# apply's stamps in its etc folder, each touched just before apply writes, for a restart no file's time records
# (Task 31's review): the front's for the registry's key groups, since the registry's own time would restart it for
# every change to the registry, which reaches it through the gate's events; and the gate's and the front's for
# `--restart`, after a change to the key digests or the key list, which apply doesn't deploy. A run cut off before
# that restart leaves the stamp newer than the unit's start, so the next run owes it (outdated_units).
KEY_GROUPS_STAMP = ".spark-apply-key-groups"
RESTART_FRONT_STAMP = ".spark-apply-restart-front"
# The files apply deploys itself, and the long-running units that run on each: llama-swap on its config; the brake
# and the gate on the registry and the app; the front on its own files (front_file). And apply's stamps.
DEPLOYED = {"llama-swap.yaml": (LLAMA_SWAP_UNIT,), "models.yaml": (BRAKE_UNIT, GATE_UNIT),
            APP: (BRAKE_UNIT, GATE_UNIT), FRONT_APP: (FRONT_UNIT,), KEY_GROUPS_STAMP: (FRONT_UNIT,),
            RESTART_FRONT_STAMP: (GATE_UNIT, FRONT_UNIT)}
# Shown escaped in a diff: every control character but tab, as `make install-units` refuses one, so no line can hide
# another on the terminal.
_CONTROL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")
READY_SECONDS = 30.0  # how long a restarted llama-swap has to answer GET /running
# How long after its restart the brake must still be running, with no restart of systemd's own in between: longer than
# the unit's RestartSec, 2 s, so a brake that stops once it runs has been restarted by then.
STAY_UP_S = 3.0


class SyncError(RuntimeError):
    """The app couldn't be synced; the message says how far the sync got."""


def diff_tree(files: dict[str, str], etc: Path) -> list[str]:
    """Rendered files that are missing from `etc` or differ from what is there."""
    return sorted(rel for rel, content in files.items()
                  if not (Path(etc) / rel).exists() or (Path(etc) / rel).read_text() != content)


def _app_files(top: Path) -> set[str]:
    """The app's files under `top`, by their path relative to it: never the venv's, nor caches."""
    found = set()
    for folder, dirs, names in os.walk(top):
        dirs[:] = [name for name in dirs if name not in NOT_APP]
        found.update((Path(folder) / name).relative_to(top).as_posix() for name in names)
    return found


def _differs(src: Path, app: Path, rel: str) -> bool:
    return not (app / rel).is_file() or (app / rel).read_bytes() != (src / rel).read_bytes()


def app_diff(src: Path, app: Path) -> list[str]:
    """What syncing the app would change: files of the repo's spark/ project that are missing from, or differ in,
    the deployed copy, and deployed files the project no longer has. Until a sync has finished (SYNC_STAMP), every
    file of the project counts."""
    src, app = Path(src), Path(app)
    ours = _app_files(src)
    synced = (app / SYNC_STAMP).is_file()
    return sorted({rel for rel in ours if not synced or _differs(src, app, rel)} | (_app_files(app) - ours))


def not_installed(files: dict[str, str], installed: dict[str, str | None]) -> list[str]:
    """Root's files, the units and the Compose project, whose installed copy is missing or differs."""
    return sorted(rel for rel, content in files.items()
                  if installed_path(rel) is not None and installed.get(rel) != content)


def _diff_lines(text: str | None) -> list[str]:
    """`text`'s lines as a diff shows them, each control character escaped (`\\x1b`); none for no file."""
    if text is None:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [_CONTROL.sub(lambda char: f"\\x{ord(char.group()):02x}", line) for line in lines]


def diff_text(files: dict[str, str], etc: Path, installed: dict[str, str | None]) -> str:
    """A unified diff of each file that changes, in name order: a file apply deploys itself, its deployed copy in `etc`
    against the rendered one; one of root's, root's installed copy against the copy apply stages in `etc`, as `make
    install-units` shows it. A file with no copy yet (or one apply can't read: read_copies) is diffed against nothing,
    /dev/null. Control characters are shown escaped (_diff_lines). The app's changes aren't here: apply names them by
    file (app_diff)."""
    out: list[str] = []
    for rel in sorted(files):
        where = installed_path(rel)
        if where is None:
            deployed = Path(etc) / rel
            old = deployed.read_text() if deployed.is_file() else None
            before, after = f"deployed: {deployed}", f"rendered: {rel}"
        else:
            old = installed.get(rel)
            before, after = f"installed: {where}", f"staged: {Path(etc) / rel}"
        if old == files[rel]:
            continue
        out.extend(difflib.unified_diff(_diff_lines(old), _diff_lines(files[rel]),
                                        "/dev/null" if old is None else before, after, lineterm=""))
    return "".join(f"{line}\n" for line in out)


def module_of(rel: str) -> str | None:
    """The module a file of the app is, by its path in the spark/ project: `src/spark/front/parse.py` is
    spark.front.parse, and a package's `__init__.py` the package; None for a file that isn't a module."""
    if not (rel.startswith("src/") and rel.endswith(".py")):
        return None
    parts = rel.removeprefix("src/").removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def front_file(rel: str) -> bool:
    """Whether a file of the app (by its path in the spark/ project) is the front's: one of the modules of its start
    (spark.front.FRONT_MODULES, which spark.front reads without importing uvicorn), or the app's environment
    (ENVIRONMENT)."""
    return rel in ENVIRONMENT or module_of(rel) in FRONT_MODULES


def units_of(rel: str) -> tuple[str, ...]:
    """The long-running units that run on a file: a unit of root's is its own, the Compose project is the compose
    unit's, and of what apply deploys itself, as DEPLOYED has it. A socket's unit is none: apply restarts no socket."""
    if rel.startswith("compose/"):
        return (COMPOSE_UNIT,)
    if rel in DEPLOYED:
        return DEPLOYED[rel]
    unit = rel.removeprefix("systemd/")
    return (unit,) if unit in RUNNING_UNITS else ()


def outdated_units(started: dict[str, float | None], changed_at: dict[str, float]) -> list[str]:
    """Running units whose start began before a file of theirs was installed or written, so they still
    run an older one. `started`: when each unit's start began (None when it isn't running);
    `changed_at`: when each of root's copies was installed, and each file apply deploys itself was
    written (their modification times; deployed_times)."""
    units = set()
    for rel, when in changed_at.items():
        for unit in units_of(rel):
            if started.get(unit) is not None and when > started[unit]:
                units.add(unit)
    return sorted(units)


@dataclass(frozen=True)
class RestartPlan:
    """What a change restarts, each list in restart_order. `now`: once the files are in place. `after_quiet`: only once
    no request has been in flight for a minute (Task 32's wait), since llama-swap's restart stops every model and the
    front's cuts off every request it holds; when it isn't empty, `now`'s restarts are made with it, in restart_order
    (from Task 32, after the wait, since the app's sync comes after it). `not_running`: the units a change touches
    that aren't running, which apply never starts: each starts with its new files."""

    now: list[str]
    after_quiet: list[str]
    not_running: list[str] = field(default_factory=list)


def restart_order(units: list[str]) -> list[str]:
    """`units` in RESTART_ORDER, once each: the brake, the gate, the front, llama-swap last, and the compose unit after
    them, on its own. A unit that isn't one of those raises ValueError."""
    unknown = sorted(set(units) - set(RESTART_ORDER))
    if unknown:
        raise ValueError(f"apply restarts only {', '.join(RESTART_ORDER)}; not {', '.join(unknown)}")
    return sorted(set(units), key=RESTART_ORDER.index)


def key_groups_changed(changed: list[str], registry_old: Registry | None, registry_new: Registry) -> bool:
    """Whether the registry apply deploys changes its key groups, which the front reads only at start: its text
    changes (`changed`, diff_tree), and the deployed one won't load (None) or has other key groups."""
    return "models.yaml" in changed and (registry_old is None or registry_old.key_groups != registry_new.key_groups)


def plan_restarts(changed: list[str], app_changes: list[str], registry_old: Registry | None, registry_new: Registry,
                  outdated: list[str], *, restart_front: bool, active: Callable[[str], bool]) -> RestartPlan:
    """What a change restarts (Task 31; plan.md, *Deploy workflow*). `changed`: the files that change of those apply
    deploys itself (diff_tree); `app_changes`: the app's (app_diff); `registry_old`: the registry apply deployed last,
    None when it won't load; `registry_new`: the one it deploys; `outdated`: running units that run an older file
    (outdated_units); `restart_front`: `--restart local-ai-front.service`; `active(unit)`: whether a unit runs.

    - llama-swap, for its config or its unit: after the quiet moment.
    - The front, for a change to one of its own files (front_file), to the registry's key groups, which it reads only
      at start, or `restart_front`: after the quiet moment. Never for a change to the registry's model names or roles,
      which reach it through the gate's events (Dan's decision, 2026-10-07, after the forward-and-back council).
    - The brake and the gate, for any change to the app or the registry: now. `restart_front` restarts the gate too,
      since it reads the key list at start, as the front does (spark.gate.main).
    - Any other unit that runs an older definition (outdated): now; the compose unit as in Phase 1.

    A unit that isn't running is never restarted (`not_running`)."""
    touched = set(outdated)
    if "llama-swap.yaml" in changed:
        touched.add(LLAMA_SWAP_UNIT)
    if app_changes or "models.yaml" in changed:
        touched |= {BRAKE_UNIT, GATE_UNIT}
    if (restart_front or key_groups_changed(changed, registry_old, registry_new)
            or any(front_file(rel) for rel in app_changes)):
        touched.add(FRONT_UNIT)
    if restart_front:
        touched.add(GATE_UNIT)
    running = [unit for unit in touched if active(unit)]
    return RestartPlan(now=restart_order([unit for unit in running if unit not in AFTER_QUIET]),
                       after_quiet=restart_order([unit for unit in running if unit in AFTER_QUIET]),
                       not_running=restart_order([unit for unit in touched if not active(unit)]))


def models_loaded(client, unit_active: bool, log=print) -> list[str] | None:
    """What llama-swap has loaded, for the decision to restart it: [] when its unit isn't running, so
    a restart stops nothing; None when it runs and doesn't say — no answer in time (llama-swap v257
    answers /running during a load, so it hangs, or the box is struggling), or an error such as a
    wrong key. None counts as loaded."""
    try:
        return [r.model for r in client.running()]
    except LlamaSwapUnreachable as err:
        if not unit_active:
            return []
        log(f"apply: {err}, and its unit runs, so models may be loaded")
        return None
    except LlamaSwapError as err:
        log(f"apply: {err}")
        return None


def wait_for_running(client, timeout: float, sleep=time.sleep, clock=time.monotonic) -> str | None:
    """After llama-swap's restart: None once it answers GET /running in v257's shape. Otherwise what went wrong, as
    apply says it: at once for an answer that is an error (it's up, and no wait changes a wrong key, say) or a request
    that was never sent; for no answer at all, once `timeout` seconds are up. -validate isn't enough on its own:
    llama-swap ignores config keys it doesn't know, so every config change is followed by a start and GET /running."""
    deadline = clock() + timeout
    while True:
        try:
            client.running()
            return None
        except LlamaSwapUnreachable as err:  # not up yet
            unanswered = err
        except LlamaSwapAnswered as err:
            return (f"llama-swap answered GET /running with an error ({err}): it's up, but apply can't confirm it "
                    "serves the new config")
        except LlamaSwapError as err:  # never sent: a bad URL, or a key a header can't carry
            return (f"apply couldn't ask llama-swap GET /running ({err}), so it can't confirm llama-swap serves the "
                    "new config")
        left = deadline - clock()
        if left <= 0:
            return (f"llama-swap didn't answer GET /running within {timeout:g} s ({unanswered}): see "
                    "`make logs s=llama-swap`")
        sleep(min(1.0, left))


def _props(show: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in show.splitlines() if "=" in line)


def check_stays_up(unit: str, show, sleep=time.sleep, wait: float | None = None) -> str | None:
    """After the brake's restart: None once it still runs `wait` seconds (STAY_UP_S) later, and systemd hasn't restarted
    it meanwhile. Otherwise what went wrong, as apply says it. `show(unit)` is `systemctl show`'s ActiveState, SubState
    and NRestarts. Type=exec counts the brake as started once its Python runs, before the app is imported, so a brake
    that stops once it runs passes `systemctl restart` and then restarts every 2 s (Restart=always)."""
    wait = STAY_UP_S if wait is None else wait
    before = _props(show(unit)).get("NRestarts")
    sleep(wait)
    after = _props(show(unit))
    name = unit.removeprefix("local-ai-").removesuffix(".service")
    if after.get("ActiveState") != "active":
        state = f"{after.get('ActiveState') or 'unknown'}, {after.get('SubState') or 'unknown'}"
        return f"it isn't running {wait:g} s later ({state}): see `make logs s={name}`"
    if before is None or after.get("NRestarts") is None:
        return f"apply can't tell whether it stayed up, since systemd gave no restart count: `systemctl status {unit}`"
    if after["NRestarts"] != before:
        return f"systemd restarted it again within {wait:g} s, so it stops once it runs: see `make logs s={name}`"
    return None


def deployed_times(etc: Path, app: Path) -> dict[str, float]:
    """When apply last wrote each file it deploys itself, when the app's last sync finished (APP), and when the last of
    the front's files in the app was written (FRONT_APP: _sync_app writes only a file that differs, so that is when
    one of them last changed): the modification times of those there are, keyed as outdated_units takes them."""
    where = {rel: Path(etc) / rel for rel in DEPLOYED if rel not in (APP, FRONT_APP)} | {APP: Path(app) / SYNC_STAMP}
    times = {}
    for rel, path in where.items():
        try:
            times[rel] = path.stat().st_mtime
        except FileNotFoundError:
            continue
    front = []
    for rel in sorted(_app_files(Path(app))):
        if front_file(rel):
            try:
                front.append((Path(app) / rel).stat().st_mtime)
            except FileNotFoundError:
                continue
    if front:
        times[FRONT_APP] = max(front)
    return times


def deployed_registry(path: Path) -> Registry | None:
    """The registry apply deployed last, the one the running front read its key groups from at start; None when there
    is none or it won't load (an older shape, say), and plan_restarts then counts its key groups as changed."""
    try:
        return load_registry(path)
    except (OSError, ValueError, yaml.YAMLError, RecursionError):  # RecursionError: YAML nested too deep
        return None


# What a restart that waits for the quiet moment costs, in apply's words, until Task 32's wait is built.
_COSTS = {FRONT_UNIT: "restarting the front cuts off every request in flight",
          LLAMA_SWAP_UNIT: "restarting llama-swap stops every model"}
# A restart that waits for the quiet moment, held back because a model loaded meanwhile: what it costs, and how it gets
# made. llama-swap's config or unit is deployed, so it's outdated, and the next apply restarts it. The front's is
# owed to the next apply too, by its files or apply's stamps, unless the run wrote none of them: after a sync that
# failed, every file of the app counts, the front's among them, though none of them may have changed. `make
# restart-front` makes it whatever it was for.
_HELD_BACK = {FRONT_UNIT: "restarting it cuts off every request in flight; once no model is loaded, `make "
                          "restart-front` restarts it",
              LLAMA_SWAP_UNIT: "restarting it stops every model; its new files are deployed, so re-run when idle, or "
                               "with --now"}


def apply_files(files: dict[str, str], etc: Path, *, installed: dict[str, str | None], unreadable: set[str],
                outdated: list[str], active, app_changes: list[str], running: list[str] | None, now_ok: bool,
                dry_run: bool, sync_app, run_cmd, log, registry_old: Registry | None, registry_new: Registry,
                restart_front: bool = False, recheck=None, came_up=None, stays_up=None) -> int:
    """Stage root's files, or deploy the rest and restart what runs on it, as plan_restarts plans, in restart_order;
    either way, first the diff of what changes (diff_text), and the app's changed files by name. 0: done, nothing to
    do, or root's files staged for `make install-units`. 1: refused (by a dry run too), the app's sync failed, or after
    the files were deployed a restart failed, was put off, llama-swap didn't answer after it as v257 does, or the brake
    didn't stay up. Until Task 32's quiet wait, the restarts that wait for it (RestartPlan.after_quiet) are made only
    while llama-swap has no model loaded, or with `now_ok` (--now). `recheck()` says what llama-swap has loaded just
    before each of them, as models_loaded does; `came_up()`, after llama-swap's restart, what went wrong, None once it
    answered (wait_for_running); `stays_up(unit)`, after the brake's restart, what went wrong, None once it stayed up
    (check_stays_up). Any left out isn't asked. `sync_app()` raises SyncError when it fails."""
    changed = diff_tree(files, etc)
    pending = not_installed(files, installed)
    plan = plan_restarts(changed, app_changes, registry_old, registry_new, outdated, restart_front=restart_front,
                         active=active)
    restarts = restart_order(plan.now + plan.after_quiet)  # apply starts nothing
    if not (changed or app_changes or pending or restarts or plan.not_running):
        log("apply: nothing to change")
        return 0
    again = "make restart-front" if restart_front else "make apply"  # what to run again: the same restarts
    if pending:
        # Root's files first: until root has them, deploy and restart nothing else, and list nothing else.
        for rel in pending:
            if rel in unreadable:
                log(f"apply: can't read root's copy of {rel} ({installed_path(rel)}) from your account: "
                    "`id -nG` should list spark-admin")
            elif installed.get(rel) is None:
                log(f"apply: root has no copy of {rel} yet ({installed_path(rel)})")
            else:
                log(f"apply: {rel} differs from root's copy ({installed_path(rel)})")
        for line in diff_text({rel: files[rel] for rel in pending}, etc, installed).splitlines():
            log(line)
        if dry_run:
            log(f"apply: dry run — would stage them in {etc} and stop until `make install-units` installs them")
            return 0
        write_tree({rel: files[rel] for rel in changed if installed_path(rel) is not None}, etc)
        log(f"apply: staged in {etc}; nothing else is deployed or restarted until `make install-units` (sudo) "
            f"installs root's copies — then run `{again}` again")
        return 0
    for rel in changed:
        log(f"apply: changes etc/{rel}")
    for rel in app_changes:
        log(f"apply: changes app/{rel}")
    for line in diff_text(files, etc, installed).splitlines():
        log(line)
    for unit in outdated:
        log(f"apply: {unit} started before its latest files were in place, so it still runs older ones")
    loaded = running if active(LLAMA_SWAP_UNIT) else []  # a stopped llama-swap has nothing loaded, whatever /running said
    refusal = None
    if plan.after_quiet and not now_ok and loaded != []:
        what = "can't tell which models are loaded" if loaded is None else f"models are loaded ({', '.join(loaded)})"
        refusal = f"{what}, and {', and '.join(_COSTS[unit] for unit in plan.after_quiet)}"
    if dry_run:
        for unit in plan.not_running:
            log(f"apply: dry run — {unit} isn't running; it starts with the new config")
        if refusal:
            log(f"apply: dry run — would refuse: {refusal}; with --now it would restart {', '.join(restarts)}")
            return 1
        log("apply: dry run — would restart " + (", ".join(restarts) or "nothing"))
        return 0
    if refusal:
        if restart_front:  # no make target passes --now with --restart; until Task 32's wait, this is the step
            log(f"apply: {refusal}; unload what's loaded (`spark unload <model>`), then run `make restart-front` "
                "again (nothing was changed)")
        else:
            log(f"apply: {refusal}; re-run when idle, or with --now (nothing was changed)")
        return 1
    if app_changes:
        try:
            sync_app()
        except SyncError as err:
            log(f"apply: {err}; nothing else was deployed or restarted — once it's fixed, `{again}` syncs it again")
            return 1
    # Just before the files, a stamp for each restart no file's time records, so a run cut off before it leaves it owed
    # to the next (KEY_GROUPS_STAMP, RESTART_FRONT_STAMP). A dry run, a refusal, the staging stop and a failed sync
    # have returned, so they touch none.
    stamps = [stamp for stamp, owed in ((KEY_GROUPS_STAMP, key_groups_changed(changed, registry_old, registry_new)),
                                        (RESTART_FRONT_STAMP, restart_front)) if owed]
    for stamp in stamps:
        write_atomic(Path(etc) / stamp, "spark apply: a restart is owed to each unit that started before this file\n")
    write_tree({rel: files[rel] for rel in changed}, etc)
    failed = False
    held_back = None  # what a look found loaded: it holds back every restart that waits for the quiet moment
    for unit in restart_order(restarts + plan.not_running):
        if unit in plan.not_running:
            log(f"apply: {unit} isn't running; it starts with the new config")
            continue
        if unit in plan.after_quiet and not now_ok and recheck is not None:
            if held_back is None and active(LLAMA_SWAP_UNIT):
                looked = recheck()  # a model may have started loading while apply synced, wrote and restarted
                if looked != []:
                    held_back = ("can't tell which models are loaded" if looked is None
                                 else f"models are now loaded ({', '.join(looked)})")
            if held_back is not None:
                failed = True
                log(f"apply: didn't restart {unit}: {held_back}, and {_HELD_BACK[unit]}")
                continue
        try:
            run_cmd(["systemctl", "restart", unit])
        except (subprocess.CalledProcessError, OSError):
            # The files are deployed. A unit that couldn't start again is stopped now, and apply restarts only units
            # that run, so the next apply won't: say how to finish while it's known.
            failed = True
            name = unit.removeprefix("local-ai-").removesuffix(".service")
            log(f"apply: restarting {unit} failed, and its new files are deployed: see `make logs s={name}`, "
                f"and once it's fixed, `systemctl restart {unit}`")
            continue
        if unit == LLAMA_SWAP_UNIT and came_up is not None:
            said = came_up()
            if said is not None:
                failed = True
                log(f"apply: restarted {unit}, but {said}")
        if unit == BRAKE_UNIT and stays_up is not None:
            said = stays_up(unit)
            if said is not None:
                failed = True
                log(f"apply: restarted {unit}, but {said}")
    return 1 if failed else 0


def started_at(show: str) -> float | None:
    """When a unit's last start began, from `systemctl show --timestamp=us+utc`'s ActiveState and
    InactiveExitTimestamp (`Fri 2026-09-25 23:19:46.826238 UTC`); None when it isn't running. A running
    unit whose time isn't in that form raises ValueError, naming what systemd said."""
    props = dict(line.split("=", 1) for line in show.splitlines() if "=" in line)
    if props.get("ActiveState") != "active":
        return None
    stamp = props.get("InactiveExitTimestamp", "")
    try:
        when = datetime.strptime(stamp.partition(" ")[2], "%Y-%m-%d %H:%M:%S.%f UTC")  # the weekday goes first
    except ValueError:
        raise ValueError(f"InactiveExitTimestamp={stamp!r}") from None
    return when.replace(tzinfo=timezone.utc).timestamp()


def start_times(units, show, log=print) -> dict[str, float | None]:
    """When each unit's start began (None when it isn't running), from `show(unit)`, `systemctl show`'s
    output. A running unit whose time can't be read counts as started after every file, root's copies
    and apply's own, so apply never restarts it for one: it says so, and what to run by hand."""
    started: dict[str, float | None] = {}
    for unit in units:
        try:
            started[unit] = started_at(show(unit))
        except ValueError as err:
            started[unit] = UNKNOWN_START
            when = " once no model is loaded (restarting it stops them all)" if unit == LLAMA_SWAP_UNIT else ""
            log(f"apply: can't read when {unit} started ({err}), so it can't tell whether {unit} runs its latest "
                "files: if they changed since it started (`make install-units`, or an apply that didn't restart it), "
                f"run `systemctl restart {unit}`{when}")
    return started


def _show(unit: str) -> str:
    return subprocess.run(["systemctl", "show", "--property=ActiveState", "--property=InactiveExitTimestamp",
                           "--timestamp=us+utc", unit], capture_output=True, text=True).stdout


def _show_restarts(unit: str) -> str:
    return subprocess.run(["systemctl", "show", "--property=ActiveState", "--property=SubState",
                           "--property=NRestarts", unit], capture_output=True, text=True).stdout


def read_copies(files: dict[str, str],
                where=installed_path) -> tuple[dict[str, str | None], dict[str, float], set[str]]:
    """Root's copies of the rendered units and Compose project: each one's text (None when there is
    none, or it can't be read), when it was installed (its modification time), and which exist but
    can't be read from this account."""
    texts: dict[str, str | None] = {}
    times: dict[str, float] = {}
    unreadable: set[str] = set()
    for rel in files:
        path = where(rel)
        if path is None:
            continue
        try:
            texts[rel] = Path(path).read_text()
            times[rel] = Path(path).stat().st_mtime
        except FileNotFoundError:
            texts[rel] = None
        except OSError:
            texts[rel] = None
            unreadable.add(rel)
    return texts, times, unreadable


def validation_env(env: Mapping[str, str]) -> dict[str, str]:
    """llama-swap's environment for -validate: `env`'s PATH, and a placeholder for each internal key, each its own,
    since the real ones are in a file Dan can't read. Nothing else: Dan's environment can hold secrets
    of his own, and -validate needs none of it."""
    return {"PATH": env.get("PATH", os.defpath)} | {
        name: f"validate-only-{i}" for i, name in enumerate(INTERNAL_KEY_ENVS)}


def _validate_llama_swap(config: str, binary: Path) -> bool:
    """llama-swap's own check, with placeholder keys and nothing else of Dan's environment."""
    if not binary.exists():
        print(f"apply: {binary} isn't installed yet — skipping llama-swap -validate")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "llama-swap.yaml"
        cfg.write_text(config)
        run = subprocess.run([str(binary), "-config", str(cfg), "-validate"], env=validation_env(os.environ))
        return run.returncode == 0


def _remove_empty_folders(app: Path) -> None:
    """Folders a file's removal left empty, deepest first; never the venv's, nor caches."""
    folders = []
    for folder, dirs, _ in os.walk(app):
        dirs[:] = [name for name in dirs if name not in NOT_APP]
        folders.append(Path(folder))
    for folder in reversed(folders[1:]):  # folders[0] is the app itself
        if not any(folder.iterdir()):
            folder.rmdir()


def _sync_app(src: Path, app: Path) -> None:
    """Make the app the repo's spark/ project: each changed file replaced whole (`spark launch` imports the app on
    every load), each file the project no longer has deleted, then `uv sync`. The stamp goes first and comes back
    only once uv sync succeeded, so a sync cut off anywhere counts as not done. SyncError says how far it got."""
    src, app = Path(src), Path(app)
    try:
        (app / SYNC_STAMP).unlink(missing_ok=True)
        ours = _app_files(src)
        for rel in sorted(ours):
            if _differs(src, app, rel):
                write_atomic(app / rel, (src / rel).read_bytes())
        for rel in sorted(_app_files(app) - ours):
            (app / rel).unlink()
        _remove_empty_folders(app)
    except OSError as err:
        raise SyncError(f"copying the app into {app} failed ({err})") from None
    # UV_PYTHON_INSTALL_DIR: a Python that uv downloads lands where the spark user can read it.
    # UV_LINK_MODE=copy: the deployed venv shares no files with Dan's uv cache.
    # VIRTUAL_ENV is dropped: `make apply` runs inside `uv run`, which points it at the repo's venv.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env |= {"UV_PYTHON_INSTALL_DIR": f"{DEPLOY}/python", "UV_LINK_MODE": "copy"}
    try:
        subprocess.run(["uv", "sync", "--frozen", "--no-dev", "--project", str(app)], check=True, env=env)
    except (subprocess.CalledProcessError, OSError) as err:
        why = f"exit {err.returncode}" if isinstance(err, subprocess.CalledProcessError) else str(err)
        raise SyncError(f"the app's files are copied into {app}, but `uv sync` failed ({why}), so its environment "
                        "isn't synced") from None
    try:
        write_atomic(app / SYNC_STAMP, "synced by spark apply\n")
    except OSError as err:
        raise SyncError(f"the app is synced, but recording it in {app / SYNC_STAMP} failed ({err})") from None


def register(subparsers) -> None:
    p = subparsers.add_parser("apply", help="render, validate and deploy on the Spark")
    p.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    p.add_argument("--now", action="store_true", help="restart llama-swap and the front even though models are loaded")
    p.add_argument("--restart", choices=[FRONT_UNIT],
                   help="restart the front, and the gate before it, as well: after a change to the key digests or the "
                        "key list, which they read only at start and apply doesn't deploy (make restart-front)")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    repo = Path.cwd()
    registry_path = repo / "stack/models.yaml"
    if not registry_path.exists():
        print("apply: run it from the repo root (make apply)")
        return 2
    if subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip():
        print("apply: note — this clone has uncommitted changes, and they are part of what gets deployed")
    # Loaded as `spark render` loads them: a file that won't load is named, in a refusal, not a traceback.
    versions = _load("versions file", repo / "stack/versions.yaml", load_versions)
    missing = unpinned(versions)
    if missing:
        print(f"apply: no pin yet for {', '.join(missing)} — record it in stack/versions.yaml first")
        return 1
    registry = _load("registry", registry_path, load_registry)
    files = render(registry, versions, _load("registry", registry_path, Path.read_text))
    for warning in check_budget(registry):  # render() refused the registry on any error, so only the warnings are left
        print(f"apply: warning — {warning}")
    binary = Path(f"{DEPLOY}/bin/llama-swap/{versions['llama-swap'].version}/llama-swap")
    if not _validate_llama_swap(files["llama-swap.yaml"], binary):
        print("apply: llama-swap rejected the rendered config; nothing was changed")
        return 1
    started = start_times(RUNNING_UNITS, _show)
    client = LlamaSwap(paths.LLAMASWAP_URL, key_from_env(args.key_env), timeout=3)
    running = models_loaded(client, unit_active=started[LLAMA_SWAP_UNIT] is not None)
    etc, src, app = Path(f"{DEPLOY}/etc"), repo / "spark", Path(f"{DEPLOY}/app")
    installed, changed_at, unreadable = read_copies(files)
    changed_at |= deployed_times(etc, app)
    return apply_files(files, etc, installed=installed, unreadable=unreadable,
                       outdated=outdated_units(started, changed_at), active=lambda unit: started.get(unit) is not None,
                       app_changes=app_diff(src, app), running=running, now_ok=args.now, dry_run=args.dry_run,
                       sync_app=lambda: _sync_app(src, app), run_cmd=lambda cmd: subprocess.run(cmd, check=True),
                       log=print, registry_old=deployed_registry(etc / "models.yaml"), registry_new=registry,
                       restart_front=args.restart == FRONT_UNIT, recheck=lambda: models_loaded(client, unit_active=True),
                       came_up=lambda: wait_for_running(client, READY_SECONDS),
                       stays_up=lambda unit: check_stays_up(unit, _show_restarts))
