"""`spark apply` — render, validate, list what changes, deploy under /opt/local-ai, and restart only
what changed. It never restarts llama-swap, which stops every model, while models are loaded, or
while it can't tell — unless told to with --now. It asks llama-swap what is loaded before it changes
anything and again just before the restart, restarts llama-swap first, and after the restart waits
for llama-swap to answer. After the brake's restart it checks that the brake is still running.

The units and the Compose project that root runs are root's own copies, which only
`make install-units` (sudo) installs: apply stages them in /opt/local-ai/etc and never writes
root's copies. While the staged ones differ from root's, apply stages them and stops, and deploys
and restarts nothing else until root has them.

A run cut off part-way leaves the next run to finish it: each file is replaced whole, the app counts
as changed until a sync of it finishes, and a running unit that started before its files were
written or synced is restarted, as one that started before root's copy was installed is. The one
exception is a unit whose start time apply can't read: it can't tell whether that unit is behind,
so it names the unit and the command to restart it by hand."""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import tempfile
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from spark import paths
from spark.llamaswap import LlamaSwap, LlamaSwapAnswered, LlamaSwapError, LlamaSwapUnreachable, key_from_env
from spark.registry import load_registry
from spark.render import DEPLOY, KEY_ENVS, _load, installed_path, render, write_atomic, write_tree
from spark.versions import load_versions, unpinned

LLAMA_SWAP_UNIT = "local-ai-llama-swap.service"
BRAKE_UNIT = "local-ai-brake.service"
COMPOSE_UNIT = "local-ai-compose.service"
RUNNING_UNITS = (LLAMA_SWAP_UNIT, BRAKE_UNIT, COMPOSE_UNIT)  # the pull unit runs only when asked
UNKNOWN_START = math.inf  # a running unit whose start time can't be read: later than any file's
NOT_APP = {".venv", "__pycache__", ".pytest_cache"}
# In the deployed app's venv, written once `uv sync` succeeded and removed before a sync starts: until it is back,
# the app counts as changed, so a sync that failed or was cut off is tried again, and so is one whose venv is gone.
SYNC_STAMP = ".venv/.spark-apply-synced"
APP = "app"  # the app, among the files apply deploys itself: when its last sync finished (deployed_times)
# The files apply deploys itself, and the long-running unit that runs on each.
DEPLOYED = {"llama-swap.yaml": LLAMA_SWAP_UNIT, "models.yaml": BRAKE_UNIT, APP: BRAKE_UNIT}
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


def unit_of(rel: str) -> str | None:
    """The long-running unit that runs on a file: a unit of root's is its own, the Compose project is the compose
    unit's, and of what apply deploys itself, llama-swap runs on its config and the brake on the registry and the
    app (APP)."""
    if rel.startswith("compose/"):
        return COMPOSE_UNIT
    if rel in DEPLOYED:
        return DEPLOYED[rel]
    unit = rel.removeprefix("systemd/")
    return unit if unit in RUNNING_UNITS else None


def outdated_units(started: dict[str, float | None], changed_at: dict[str, float]) -> list[str]:
    """Running units whose start began before a file of theirs was installed or written, so they still
    run an older one. `started`: when each unit's start began (None when it isn't running);
    `changed_at`: when each of root's copies was installed, and each file apply deploys itself was
    written (their modification times; deployed_times)."""
    units = set()
    for rel, when in changed_at.items():
        unit = unit_of(rel)
        if unit is not None and started.get(unit) is not None and when > started[unit]:
            units.add(unit)
    return sorted(units)


def units_to_restart(changed: list[str], app_changed: bool = False) -> list[str]:
    """The units that read a file apply deploys itself. Root's files restart nothing here: a unit
    picks up a new definition once `make install-units` installed it (outdated_units)."""
    units = {BRAKE_UNIT} if app_changed else set()  # the one long-running process that imports the app
    for rel in changed:
        if rel == "llama-swap.yaml":
            units.add(LLAMA_SWAP_UNIT)
        if rel == "models.yaml":
            units.add(BRAKE_UNIT)
    return sorted(units)


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
    """When apply last wrote each file it deploys itself, and when the app's last sync finished (APP): the
    modification times of those there are, keyed as outdated_units takes them."""
    where = {rel: Path(etc) / rel for rel in DEPLOYED if rel != APP} | {APP: Path(app) / SYNC_STAMP}
    times = {}
    for rel, path in where.items():
        try:
            times[rel] = path.stat().st_mtime
        except FileNotFoundError:
            continue
    return times


def apply_files(files: dict[str, str], etc: Path, *, installed: dict[str, str | None], unreadable: set[str],
                outdated: list[str], active, app_changes: list[str], running: list[str] | None, now_ok: bool,
                dry_run: bool, sync_app, run_cmd, log, recheck=None, came_up=None, stays_up=None) -> int:
    """Stage root's files, or deploy the rest and restart what runs on it. 0: done, nothing to do, or root's files
    staged for `make install-units`. 1: refused (by a dry run too), the app's sync failed, or after the files were
    deployed a restart failed, was put off, llama-swap didn't answer after it as v257 does, or the brake didn't stay
    up. `recheck()` says what llama-swap has loaded just before its restart, as models_loaded does; `came_up()`, after
    it, what went wrong, None once llama-swap answered (wait_for_running); `stays_up(unit)`, after the brake's restart,
    what went wrong, None once it stayed up (check_stays_up). Any left out isn't asked. `sync_app()` raises SyncError
    when it fails."""
    changed = diff_tree(files, etc)
    pending = not_installed(files, installed)
    # llama-swap first, so nothing comes between the second look at what it has loaded and its restart.
    restart = sorted(set(units_to_restart(changed, bool(app_changes))) | set(outdated),
                     key=lambda unit: (unit != LLAMA_SWAP_UNIT, unit))
    if not (changed or app_changes or pending or restart):
        log("apply: nothing to change")
        return 0
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
        if dry_run:
            log(f"apply: dry run — would stage them in {etc} and stop until `make install-units` installs them")
            return 0
        write_tree({rel: files[rel] for rel in changed if installed_path(rel) is not None}, etc)
        log(f"apply: staged in {etc}; nothing else is deployed or restarted until `make install-units` (sudo) "
            "installs root's copies — then run `make apply` again")
        return 0
    for rel in changed:
        log(f"apply: changes etc/{rel}")
    if app_changes:
        log(f"apply: changes the app ({len(app_changes)} files)")
    for unit in outdated:
        log(f"apply: {unit} started before its latest files were in place, so it still runs older ones")
    restarts = [unit for unit in restart if active(unit)]  # apply starts nothing
    refusal = None
    # A stopped llama-swap has nothing loaded, whatever /running said, and isn't restarted.
    if LLAMA_SWAP_UNIT in restarts and not now_ok and running != []:
        loaded = "can't tell which models are loaded" if running is None else f"models are loaded ({', '.join(running)})"
        refusal = f"{loaded}, and restarting llama-swap stops every model"
    if dry_run:
        for unit in restart:
            if not active(unit):
                log(f"apply: dry run — {unit} isn't running; it starts with the new config")
        if refusal:
            log(f"apply: dry run — would refuse: {refusal}; with --now it would restart {', '.join(restarts)}")
            return 1
        log("apply: dry run — would restart " + (", ".join(restarts) or "nothing"))
        return 0
    if refusal:
        log(f"apply: {refusal}; re-run when idle, or with --now (nothing was changed)")
        return 1
    if app_changes:
        try:
            sync_app()
        except SyncError as err:
            log(f"apply: {err}; nothing else was deployed or restarted — once it's fixed, `make apply` syncs it again")
            return 1
    write_tree({rel: files[rel] for rel in changed}, etc)
    failed = False
    for unit in restart:
        if not active(unit):
            log(f"apply: {unit} isn't running; it starts with the new config")
            continue
        if unit == LLAMA_SWAP_UNIT and not now_ok and recheck is not None:
            loaded = recheck()  # a model may have started loading while apply synced and wrote
            if loaded != []:
                # Its files are deployed, so it's outdated now: the next apply restarts it, once no model is loaded.
                failed = True
                what = ("can't tell which models are loaded" if loaded is None
                        else f"models are now loaded ({', '.join(loaded)})")
                log(f"apply: didn't restart {unit}: {what}, and restarting it stops every model; its new files are "
                    "deployed, so re-run when idle, or with --now")
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
    """llama-swap's environment for -validate: `env`'s PATH, and a placeholder for each key, each its own,
    since the real ones are in a file Dan can't read. Nothing else: Dan's environment can hold secrets
    of his own, and -validate needs none of it."""
    return {"PATH": env.get("PATH", os.defpath)} | {name: f"validate-only-{i}" for i, name in enumerate(KEY_ENVS)}


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
    p.add_argument("--now", action="store_true", help="restart llama-swap even though models are loaded")
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
    files = render(_load("registry", registry_path, load_registry), versions,
                   _load("registry", registry_path, Path.read_text))
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
                       log=print, recheck=lambda: models_loaded(client, unit_active=True),
                       came_up=lambda: wait_for_running(client, READY_SECONDS),
                       stays_up=lambda unit: check_stays_up(unit, _show_restarts))
