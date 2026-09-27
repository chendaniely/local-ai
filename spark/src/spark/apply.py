"""`spark apply` — render, validate, list what changes, deploy under /opt/local-ai, and restart only
what changed. It never restarts llama-swap, which stops every model, while models are loaded, or
while it can't tell — unless told to with --now.

The units and the Compose project that root runs are root's own copies, which only
`make install-units` (sudo) installs: apply stages them in /opt/local-ai/etc and never writes
root's copies. While the staged ones differ from root's, apply stages them and stops, and deploys
and restarts nothing else until root has them."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from spark import paths
from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, key_from_env
from spark.registry import load_registry
from spark.render import DEPLOY, KEY_ENVS, installed_path, render, write_tree
from spark.versions import load_versions, unpinned

LLAMA_SWAP_UNIT = "local-ai-llama-swap.service"
BRAKE_UNIT = "local-ai-brake.service"
COMPOSE_UNIT = "local-ai-compose.service"
RUNNING_UNITS = (LLAMA_SWAP_UNIT, BRAKE_UNIT, COMPOSE_UNIT)  # the pull unit runs only when asked
UNKNOWN_START = math.inf  # a running unit whose start time can't be read: later than any copy
NOT_APP = {".venv", "__pycache__", ".pytest_cache"}


def diff_tree(files: dict[str, str], etc: Path) -> list[str]:
    """Rendered files that are missing from `etc` or differ from what is there."""
    return sorted(rel for rel, content in files.items()
                  if not (Path(etc) / rel).exists() or (Path(etc) / rel).read_text() != content)


def app_diff(src: Path, app: Path) -> list[str]:
    """Files of the repo's spark/ project that are missing from, or differ in, the deployed copy."""
    changed = []
    for f in sorted(Path(src).rglob("*")):
        rel = f.relative_to(src)
        if f.is_dir() or NOT_APP & set(rel.parts):
            continue
        dest = Path(app) / rel
        if not dest.exists() or dest.read_bytes() != f.read_bytes():
            changed.append(str(rel))
    return changed


def not_installed(files: dict[str, str], installed: dict[str, str | None]) -> list[str]:
    """Root's files, the units and the Compose project, whose installed copy is missing or differs."""
    return sorted(rel for rel, content in files.items()
                  if installed_path(rel) is not None and installed.get(rel) != content)


def unit_of(rel: str) -> str | None:
    """The long-running unit a file of root's defines; the Compose project is the compose unit's."""
    if rel.startswith("compose/"):
        return COMPOSE_UNIT
    unit = rel.removeprefix("systemd/")
    return unit if unit in RUNNING_UNITS else None


def outdated_units(started: dict[str, float | None], changed_at: dict[str, float]) -> list[str]:
    """Running units whose start began before a file of theirs was installed, so they still run the
    older definition. `started`: when each unit's start began (None when it isn't running);
    `changed_at`: when each of root's copies was installed (its modification time)."""
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


def apply_files(files: dict[str, str], etc: Path, *, installed: dict[str, str | None], unreadable: set[str],
                outdated: list[str], active, app_changes: list[str], running: list[str] | None, now_ok: bool,
                dry_run: bool, sync_app, run_cmd, log) -> int:
    changed = diff_tree(files, etc)
    pending = not_installed(files, installed)
    restart = sorted(set(units_to_restart(changed, bool(app_changes))) | set(outdated))
    if not (changed or app_changes or pending or restart):
        log("apply: nothing to change")
        return 0
    for rel in changed:
        log(f"apply: changes etc/{rel}")
    if app_changes:
        log(f"apply: changes the app ({len(app_changes)} files)")
    for unit in outdated:
        log(f"apply: {unit} still runs an older definition than root's copy")
    if pending:
        # Root's files first: until root has them, deploy and restart nothing else.
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
    refusal = None
    if LLAMA_SWAP_UNIT in restart and not now_ok and running != []:
        loaded = "can't tell which models are loaded" if running is None else f"models are loaded ({', '.join(running)})"
        refusal = f"apply: {loaded}, and restarting llama-swap stops every model; re-run when idle, or with --now"
    if dry_run:
        log("apply: dry run — would restart " + (", ".join(unit for unit in restart if active(unit)) or "nothing"))
        for unit in restart:
            if not active(unit):
                log(f"apply: dry run — {unit} isn't running; it starts with the new config")
        if refusal:
            log(refusal)
        return 0
    if refusal:
        log(refusal + " (nothing was changed)")
        return 1
    if app_changes:
        sync_app()
    write_tree({rel: files[rel] for rel in changed}, etc)
    failed = False
    for unit in restart:
        if not active(unit):
            log(f"apply: {unit} isn't running; it starts with the new config")
            continue
        try:
            run_cmd(["systemctl", "restart", unit])
        except (subprocess.CalledProcessError, OSError):
            # The files are deployed, so the next apply sees nothing to change: say it now.
            failed = True
            name = unit.removeprefix("local-ai-").removesuffix(".service")
            log(f"apply: restarting {unit} failed, and its new files are deployed: see `make logs s={name}`, "
                f"and once it's fixed, `systemctl restart {unit}`")
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
    output. A running unit whose time can't be read counts as started after every copy, so apply
    never restarts it for one: it says so, and what to run by hand."""
    started: dict[str, float | None] = {}
    for unit in units:
        try:
            started[unit] = started_at(show(unit))
        except ValueError as err:
            started[unit] = UNKNOWN_START
            when = " once no model is loaded (restarting it stops them all)" if unit == LLAMA_SWAP_UNIT else ""
            log(f"apply: can't read when {unit} started ({err}), so it can't tell whether {unit} runs root's "
                f"latest copy: if `make install-units` changed its files, run `systemctl restart {unit}`{when}")
    return started


def _show(unit: str) -> str:
    return subprocess.run(["systemctl", "show", "--property=ActiveState", "--property=InactiveExitTimestamp",
                           "--timestamp=us+utc", unit], capture_output=True, text=True).stdout


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
    """llama-swap's environment for -validate: a placeholder for each key, each its own, since the real
    ones are in a file Dan can't read."""
    return dict(env) | {name: f"validate-only-{i}" for i, name in enumerate(KEY_ENVS)}


def _validate_llama_swap(config: str, binary: Path) -> bool:
    """llama-swap's own check, with placeholder keys."""
    if not binary.exists():
        print(f"apply: {binary} isn't installed yet — skipping llama-swap -validate")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "llama-swap.yaml"
        cfg.write_text(config)
        run = subprocess.run([str(binary), "-config", str(cfg), "-validate"], env=validation_env(os.environ))
        return run.returncode == 0


def _sync_app(src: Path, app: Path) -> None:
    for rel in app_diff(src, app):
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, app / rel)
    # UV_PYTHON_INSTALL_DIR: a Python that uv downloads lands where the spark user can read it.
    # UV_LINK_MODE=copy: the deployed venv shares no files with Dan's uv cache.
    # VIRTUAL_ENV is dropped: `make apply` runs inside `uv run`, which points it at the repo's venv.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    env |= {"UV_PYTHON_INSTALL_DIR": f"{DEPLOY}/python", "UV_LINK_MODE": "copy"}
    subprocess.run(["uv", "sync", "--frozen", "--no-dev", "--project", str(app)], check=True, env=env)


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
    versions = load_versions(repo / "stack/versions.yaml")
    missing = unpinned(versions)
    if missing:
        print(f"apply: no pin yet for {', '.join(missing)} — record it in stack/versions.yaml first")
        return 1
    files = render(load_registry(registry_path), versions, registry_path.read_text())
    binary = Path(f"{DEPLOY}/bin/llama-swap/{versions['llama-swap'].version}/llama-swap")
    if not _validate_llama_swap(files["llama-swap.yaml"], binary):
        print("apply: llama-swap rejected the rendered config; nothing was changed")
        return 1
    started = start_times(RUNNING_UNITS, _show)
    client = LlamaSwap(paths.LLAMASWAP_URL, key_from_env(args.key_env), timeout=3)
    running = models_loaded(client, unit_active=started[LLAMA_SWAP_UNIT] is not None)
    installed, changed_at, unreadable = read_copies(files)
    src, app = repo / "spark", Path(f"{DEPLOY}/app")
    return apply_files(files, Path(f"{DEPLOY}/etc"), installed=installed, unreadable=unreadable,
                       outdated=outdated_units(started, changed_at), active=lambda unit: started.get(unit) is not None,
                       app_changes=app_diff(src, app), running=running, now_ok=args.now, dry_run=args.dry_run,
                       sync_app=lambda: _sync_app(src, app), run_cmd=lambda cmd: subprocess.run(cmd, check=True),
                       log=print)
