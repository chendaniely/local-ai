"""`spark apply` — render, validate, say render's warnings, show the diff of what changes, deploy under
/opt/local-ai, and restart only what changed (Task 31). plan_restarts decides what: the brake and the gate for any
change to the app or the registry; llama-swap for its config or unit, and the front for its own modules
(spark.front.FRONT_MODULES, with the app's environment), the registry's key groups, or `--restart
local-ai-front.service` (`make restart-front`), only once nothing is in flight: llama-swap's restart stops every model,
and the front's cuts off every request it holds. Restarts follow restart_order: the brake, the gate, the front,
llama-swap last, then the compose unit on its own.

**The quiet wait** (Task 32; plan.md, *Deploy workflow*). When llama-swap or the front must restart, apply asks the gate
what is in flight, by the front's counts (GET /v1/quiet, every second), and waits until nothing has been for QUIET_S,
the front's call to the gate open, saying what it waits on once per change. Requests keep being served meanwhile, and
Ctrl-C leaves everything as it was. After APPLY_DEADLINE_S (`--deadline-s`, for a drill) it offers "drain now": holding
new requests while those in flight finish. `--now` (`make apply-now`) asks before it cuts off the requests the gate
lists in flight, naming whose they are, and waits for nothing. Either way, from POST /v1/drain-all on, the gate's
persisted hold stands (ApplyGate): the gate tells the front to hold every new request, admission answers `restarting`,
nothing unloads, and apply renews the hold every APPLY_RENEW_S from a thread of its own. apply then waits until nothing
is in flight under the hold, counted only from a snapshot the front made while holding, its call open, since the gate
took drain-all (`drained`, Task 32's review: one made before may lack a request the front let through just before it
held; and the gate counts `holding` only from a snapshot on the front's call open now), asking at the deadline, once the
front has had ASK_AFTER_S to report, whether to keep holding (keep_holding_question). Only then does it sync the app and
write the files, tell the gate the units it restarts (apply/begin), and restart them: the brake, the gate (then its
control socket answering within GATE_READY_S, or apply stops there), the front, llama-swap (then the gate seeing it
answer again within READY_SECONDS), then apply/end, which releases the held requests and reloads the residents; the
compose unit after it. Just before each restart of the gate, the front and llama-swap, a renewal of apply's own confirms
that the hold still stands and wasn't lost and re-created (ApplyGate.confirm); and, but with --now, just before the
front's and llama-swap's, the front's counts under its hold are read again. A hold that doesn't stand stops apply there.
Any failure after drain-all, or Ctrl-C, SIGHUP or SIGTERM (exit 130, 129, 143), ends the hold with undrain-all first,
then says so, and writes nothing more; apply's lines survive a terminal that has hung up (_say), as on a dropped SSH
session.

A change that restarts neither applies at once, with no wait and no hold. While the gate doesn't answer, apply refuses a
restart of a running llama-swap, or of the front, unless --now, since it can't see what is in flight; with llama-swap
stopped it deploys without the gate (the cutover's case). apply never asks llama-swap itself: from the cutover it
answers only the internal keys. After the brake's restart it checks that the brake is still running.

The units and the Compose project that root runs are root's own copies, which only
`make install-units` (sudo) installs: apply stages them in /opt/local-ai/etc and never writes
root's copies. While the staged ones differ from root's, apply stages them and stops, and deploys
and restarts nothing else until root has them, before it asks the gate anything.

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
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from spark import messages, paths
from spark.front import FRONT_MODULES
from spark.gateclient import GateClient, GateError, GateRefused, GateUnavailable
from spark.gateproto import APPLY_DEADLINE_S, APPLY_END_TIMEOUT_S, APPLY_RENEW_S, QUIET_S, REQUEST_TIMEOUT_S
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
# request it holds.
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
READY_SECONDS = 30.0  # how long a restarted llama-swap has for the gate to see it answer again
GATE_READY_S = 30.0  # how long a restarted gate has to answer on its control socket; and the wait's gate, to be back
POLL_S = 1.0  # the quiet wait's readings, and the waits after a restart, this far apart
# A drain wait asks its deadline's question only once it has given the front this long to report that it holds: it
# posts at least every second while its call is open, and after the gate's restart its call opens again within seconds
# (Task 32's re-review, N2).
ASK_AFTER_S = 10.0
# apply/end and undrain-all: the gate bounds each at APPLY_END_TIMEOUT_S, past its /running read, so apply waits longer
# for them (the controller's ruling at Task 19's review); every other call of apply's waits REQUEST_TIMEOUT_S.
END_TIMEOUT_S = APPLY_END_TIMEOUT_S + 5.0
# The requests a restart that waits for the quiet moment would cut off, in apply's words.
_COSTS = {LLAMA_SWAP_UNIT: "restarting llama-swap stops every model",
          FRONT_UNIT: "restarting the front cuts off every request it holds"}
_STEP = "On the Spark, `make doctor` shows what's wrong."  # gateclient's step, in Dan's words
# apply's clock and sleep for its waits, and its question; the tests give their own.
_clock = time.monotonic
_sleep = time.sleep
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
    front's cuts off every request it holds; when it isn't empty, `now`'s restarts are made with it, after the wait and
    under apply's hold, in restart_order, since the app's sync comes after the wait. `not_running`: the units a change
    touches that aren't running, which apply never starts: each starts with its new files."""

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


class Signalled(BaseException):
    """SIGHUP (a dropped SSH session) or SIGTERM, raised in apply's own thread so that apply ends as Ctrl-C ends it, with
    the same cleanup; `code` is the exit, 128 + the signal's number. A BaseException, as KeyboardInterrupt is, so no
    `except Exception` takes it for an error."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.code = 128 + signum
        self.name = signal.Signals(signum).name


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_quiet(gate) -> dict:
    """GET /v1/quiet (gateproto.QuietAnswer), its shape checked: an answer apply can't read is a GateError."""
    answer = gate.get("/v1/quiet")
    inflight, last, made = answer.get("inflight"), answer.get("last_label"), answer.get("snapshot_at")
    if not (_number(answer.get("quiet_for_s")) and (last is None or isinstance(last, str))
            and isinstance(inflight, list)
            and all(isinstance(row, dict) and isinstance(row.get("model_label"), str)
                    and isinstance(row.get("key_label"), str) for row in inflight)
            and isinstance(answer.get("front_connected"), bool) and isinstance(answer.get("holding"), bool)
            and (made is None or _number(made))):
        raise GateError(f"The gate's answer to GET /v1/quiet couldn't be read: it isn't in the shape apply knows. {_STEP}")
    return answer


def _askers(inflight: list[dict]) -> str:
    """Whose the requests in flight are, by their keys' labels, each once with its count past one, as Task 7's
    apply_now_confirm names them."""
    labels = [row["key_label"] for row in inflight]
    counts = {label: labels.count(label) for label in labels}
    return ", ".join(label if n == 1 else f"{label} ×{n}" for label, n in counts.items())


def _reading(answer: dict) -> str:
    """What a /v1/quiet reading says: `front` (the front's call to the gate is down, so the gate's counts are its last
    snapshot, however old, and the gate's hold doesn't reach it), `busy` (a request in flight), `unknown` (nothing
    listed in flight, and not quiet: the gate has no count from the front yet, as after its own restart) or `idle`."""
    if not answer["front_connected"]:
        return "front"
    if answer["inflight"]:
        return "busy"
    return "idle" if answer["quiet_for_s"] > 0 else "unknown"


def _since(answer: dict, after: float | None) -> bool:
    """Whether the reading's snapshot was made at or after `after`, when the gate took apply's drain-all."""
    made = answer["snapshot_at"]
    return after is not None and made is not None and made >= after


def drained(answer: dict, after: float | None) -> bool:
    """Whether a reading shows nothing in flight under apply's hold (Task 32's review, C1): idle, from a snapshot the
    front made while holding new requests, its call to the gate open, since the gate took apply's drain-all at
    `after`. The front starts holding in one step, and counts each request in the step that clears it, so a snapshot
    made while it holds has every request it let through before; one made before the hold, or by a front whose call is
    down (the gate's hold_all never reached it), may lack one, which a restart would cut off."""
    return _reading(answer) == "idle" and answer["holding"] is True and _since(answer, after)


def waiting_words(answer: dict, quiet_s: float = QUIET_S, *, now_step: bool = True) -> str:
    """What the quiet wait says it waits on (Task 7's apply_waiting, for a model in flight or one that answered). A
    model in flight is the oldest request's, never the last to answer: that one may be another. With the front's call
    down (its last snapshot stands at the gate, and what it lists may long be gone), without the front's counts, or
    with no request since the front started, it says so. `now_step`: whether `make apply-now` restarts now, which it
    doesn't for `make restart-front` (Task 31's review: no make target passes --now with --restart)."""
    kind, quiet = _reading(answer), answer["quiet_for_s"]
    if kind == "front":
        text = ("Waiting for a quiet moment: the front isn't reporting its counts (`make logs s=front` shows why), so "
                f"apply can't tell what's in flight, and it needs {round(quiet_s)} s with nothing in flight. Ctrl-C "
                "leaves everything as it was; `make apply-now` restarts now.")
    elif kind == "busy":
        text = messages.apply_waiting(answer["inflight"][0]["model_label"], 0, quiet_s)
    elif answer["last_label"] is not None and kind == "idle":
        # A quiet moment under a second old would read "is answering now": it answered a moment ago.
        text = messages.apply_waiting(answer["last_label"], max(1.0, quiet), quiet_s)
    else:
        what = ("the gate doesn't have the front's counts yet, so it can't tell what's in flight" if kind == "unknown"
                else f"nothing has been in flight since the front started {max(1, round(quiet))} s ago")
        text = (f"Waiting for a quiet moment: {what}, and it needs {round(quiet_s)} s with nothing in flight. Ctrl-C "
                "leaves everything as it was; `make apply-now` restarts now.")
    step = "; `make apply-now` restarts now."
    return text if now_step or not text.endswith(step) else text.removesuffix(step) + "."


def draining_words(answer: dict, after: float | None) -> str:
    """What the drain wait says, under apply's hold: whose requests it waits on, once the front confirms that it holds
    new requests, with counts made since drain-all; that the front isn't connected, so it holds nothing (never
    *Holding new requests* then: the review's M6); or, until the front confirms its hold, that apply waits for that."""
    kind, inflight = _reading(answer), answer["inflight"]
    if kind == "front":
        what = ("The front isn't connected to the gate (`make logs s=front` shows why), so it holds no new requests, "
                "and apply can't tell what's in flight; it waits until the front is back and holding.")
    elif kind == "busy" and answer["holding"] and _since(answer, after):
        if len(inflight) == 1:
            what = f"Holding new requests while the 1 request in flight finishes ({_askers(inflight)})."
        else:
            what = f"Holding new requests while the {len(inflight)} requests in flight finish ({_askers(inflight)})."
    else:
        what = "Waiting for the front to confirm that it holds new requests, with its counts."
    return f"{what} Ctrl-C ends the hold and leaves everything as it was."


def keep_holding_question(answer: dict, after: float | None, deadline_s: float, *, written: bool) -> str:
    """The drain wait's question at the deadline (Task 32's re-review, N2): a quiet minute was found, or Dan said drain
    now, and new requests are held; but requests are still in flight, or the front hasn't confirmed that it holds. What
    a no does is said: nothing is changed, or, with the files already deployed (`written`), nothing more is
    restarted."""
    no = "nothing more is restarted" if written else "nothing is changed"
    took = messages.duration(deadline_s)
    inflight = answer["inflight"]
    if _reading(answer) == "busy" and answer["holding"] and _since(answer, after):
        what = (f"the 1 request in flight ({_askers(inflight)})" if len(inflight) == 1
                else f"the {len(inflight)} requests in flight ({_askers(inflight)})")
        return (f"Requests are still in flight after {took}: {what}. Keep holding new requests until they finish? No "
                f"ends the hold, and {no}. [y/N]")
    return (f"The front hasn't confirmed in {took} that it holds new requests, so apply can't tell what's in flight. "
            f"Keep waiting? No ends the hold, and {no}. [y/N]")


class Deadline:
    """The quiet wait's deadline, kept for the drain waits after it (the review's M5): `deadline_s` from `start`, by the
    waits' clock. `agreed` once Dan has said yes, to drain now or to keep holding, after which nothing more is asked."""

    def __init__(self, start: float, deadline_s: float) -> None:
        self.start, self.deadline_s, self.agreed = start, deadline_s, False

    def due(self, now: float) -> bool:
        return not self.agreed and now - self.start >= self.deadline_s

    def ask(self, question: str, ask) -> bool:
        """`question` asked: True, and agreed, for yes."""
        self.agreed = bool(ask(question))
        return self.agreed


class _Reader:
    """The waits' readings of /v1/quiet, a second apart. A gate that stops answering (as across its own restart) is
    said once and asked again each second; one gone GATE_READY_S raises its GateUnavailable."""

    def __init__(self, gate, clock, sleep, say) -> None:
        self.gate, self.clock, self.sleep, self.say = gate, clock, sleep, say
        self.gone_since: float | None = None

    def read(self) -> dict | None:
        try:
            answer = read_quiet(self.gate)
        except GateUnavailable as err:
            now = self.clock()
            if self.gone_since is None:
                self.gone_since = now
                self.say(f"apply: {err} It asks again each second, for up to {GATE_READY_S:g} s.")
            elif now - self.gone_since >= GATE_READY_S:
                raise
            return None
        if self.gone_since is not None:
            self.gone_since = None
            self.say("apply: the gate answers again.")
        return answer


def wait_for_quiet(gate, *, quiet_s: float = QUIET_S, deadline_s: float = APPLY_DEADLINE_S, clock=time.monotonic,
                   say=print, ask, sleep=time.sleep, drain_all=None, first: dict | None = None,
                   now_step: bool = True, deadline: Deadline | None = None) -> str:
    """The quiet wait (Task 32): GET /v1/quiet every second until nothing has been in flight for `quiet_s`, by the
    front's counts, its call to the gate open: "quiet". While it waits it says what it waits on (waiting_words), once
    per change: what is in flight, which model answered last, a new quiet moment begun (a request came and went between
    two readings), or a front that isn't reporting. After `deadline_s` (`deadline`, which the drain waits after it keep)
    it asks Task 7's apply_no_quiet, naming that deadline: yes posts drain-all (`drain_all()`, which starts the hold's
    renewals and gives when the gate took it) and waits for nothing in flight under the hold (wait_for_drained):
    "drained". No, or no answer: "declined". `first` is a reading already taken. Ctrl-C, SIGHUP and SIGTERM go to the
    caller, which has changed nothing."""
    reader = _Reader(gate, clock, sleep, say)
    deadline = deadline or Deadline(clock(), deadline_s)
    said, last_quiet, period = None, None, 0
    answer = first
    while True:
        if answer is None:
            answer = reader.read()
        if answer is not None:
            kind, quiet = _reading(answer), answer["quiet_for_s"]
            if kind == "idle" and quiet >= quiet_s:
                return "quiet"
            if kind == "idle" and last_quiet is not None and quiet < last_quiet:
                period += 1  # nothing in flight at either reading, and still a new quiet moment: one came and went
            last_quiet = quiet if kind == "idle" else None
            key = ((kind, answer["inflight"][0]["model_label"]) if kind == "busy"
                   else (kind, answer["last_label"], period) if kind == "idle" else (kind,))
            if key != said:
                say(waiting_words(answer, quiet_s, now_step=now_step))
                said = key
            if deadline.due(clock()):
                # The requests in flight are counted only from a front whose call is open: a disconnected front's last
                # snapshot may long be stale (M6).
                count = len(answer["inflight"]) if answer["front_connected"] else 0
                if not deadline.ask(messages.apply_no_quiet(count, deadline_s=deadline.deadline_s), ask):
                    return "declined"
                after = (drain_all or (lambda: gate.post("/v1/drain-all", {}).get("at")))()
                outcome = wait_for_drained(gate, after=after, deadline=deadline, ask=ask, clock=clock, sleep=sleep,
                                           say=say)
                return "declined" if outcome == "declined" else "drained"
        answer = None
        sleep(POLL_S)


def wait_for_drained(gate, *, after: float | None, deadline: Deadline | None = None, ask=None, clock=time.monotonic,
                     sleep=time.sleep, say=print, written: bool = False, ask_after_s: float = ASK_AFTER_S) -> str:
    """Under apply's hold: GET /v1/quiet every second until a reading shows nothing in flight, made under the front's
    own hold since the gate took drain-all at `after` (drained). It says what it waits on, once per change
    (draining_words). However long a request takes, nothing cuts it off; but at `deadline`, unless Dan has already
    said yes, and once the wait has given the front `ask_after_s` to report (N2), it asks keep_holding_question (the
    review's M5), and a no, or no answer, ends the wait. `written`: the files are deployed, as before a restart.
    "drained" at the first reading, "waited" after one or more, or "declined"."""
    reader, said, waited, started = _Reader(gate, clock, sleep, say), None, False, clock()
    while True:
        answer = reader.read()
        if answer is not None:
            if drained(answer, after):
                return "waited" if waited else "drained"
            kind = _reading(answer)
            key = (("front",) if kind == "front"
                   else ("busy", _askers(answer["inflight"])) if kind == "busy" and answer["holding"]
                   and _since(answer, after) else ("pending",))
            if key != said:
                say(draining_words(answer, after))
                said = key
            now = clock()
            if deadline is not None and deadline.due(now) and now - started >= ask_after_s and not deadline.ask(
                    keep_holding_question(answer, after, deadline.deadline_s, written=written), ask or (lambda q: None)):
                return "declined"
        waited = True
        sleep(POLL_S)


class ApplyGate:
    """apply's calls to the gate on its control socket (Task 32): what is in flight (`quiet`), apply's hold (`drain_all`,
    its renewals from a thread of its own, `begin`, `end`, `undrain`), and whether the gate (`gate_answers`) and
    llama-swap (`llama_swap_back`) answer after their restarts. `client` waits REQUEST_TIMEOUT_S for an answer;
    `end_client`, for apply/end and undrain-all, END_TIMEOUT_S, past the gate's own bound on them. `clock` and `sleep`
    time the waits; `pause(due, stopped)` the renewals' thread: True once `stopped` is set, False once `due` comes."""

    def __init__(self, client, end_client, *, clock=time.monotonic, sleep=time.sleep, pause=None,
                 renew_s: float = APPLY_RENEW_S, log=print) -> None:
        self.client, self.end_client = client, end_client
        self.clock, self.sleep, self.log = clock, sleep, log
        self.renew_s = renew_s
        self._pause = pause or self._wait_until
        self.since: float | None = None  # the hold's, once drain-all has answered
        self.drained_at: float | None = None  # when the gate took drain-all, by its clock (Task 32's review, C1)
        self.ended = threading.Event()  # a renewal answered {ended: true}: the gate's hold ended without apply
        # A renewal answered {recreated: true}: the gate had lost the hold (a start fresh from a damaged state file),
        # and its hello released the front's held requests meanwhile (the review's M7).
        self.lost = False
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def quiet(self) -> dict:
        return read_quiet(self.client)

    def drain_all(self) -> float:
        """POST /v1/drain-all: apply's hold, persisted at the gate, from now; then its renewals, every renew_s.
        Returns when the gate took it (`drained_at`), by its clock: only a snapshot the front made since counts."""
        answer = self.client.post("/v1/drain-all", {})
        since, taken = answer.get("since"), answer.get("at")
        if not (_number(since) and _number(taken)):
            raise GateError(f"The gate's answer to POST /v1/drain-all couldn't be read: it names no hold. {_STEP}")
        self.since, self.drained_at = float(since), float(taken)
        first = self.clock() + self.renew_s
        self._thread = threading.Thread(target=self._renew, args=(first,), name="apply-renew", daemon=True)
        self._thread.start()
        return self.drained_at

    def _wait_until(self, due: float, stopped: threading.Event) -> bool:
        while True:
            left = due - self.clock()
            if left <= 0:
                return stopped.is_set()
            if stopped.wait(left):
                return True

    def _renew(self, due: float) -> None:
        """The renewals' thread: apply/renew every renew_s, so neither a long wait nor a long sync lets the hold lapse
        at the gate (APPLY_LAPSE_S); a renewal that doesn't reach the gate is said, and the next tried on time. It
        stops once stopped, or once the gate says the hold has ended."""
        while not self._pause(due, self._stopped):
            try:
                answer = self.client.post("/v1/apply/renew", {"since": self.since})
            except Exception as err:  # a thread of its own: anything said, never its end
                self.log(f"apply: renewing the hold on new requests didn't take: {err} It tries again in "
                         f"{self.renew_s:g} s.")
            else:
                if answer.get("ended") is True:
                    self.ended.set()
                    return
                if answer.get("recreated") is True:
                    self.lost = True
            due += self.renew_s

    def stop(self) -> None:
        """No more renewals: the thread is stopped and waited for, so none lands after the hold's end."""
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(END_TIMEOUT_S)

    def confirm(self) -> str | None:
        """Just before a restart under the hold (concern 3's ruling at Task 32): apply/renew, bounded by the client's
        own REQUEST_TIMEOUT_S, so that a hold the gate has ended since the last renewal is never followed by a restart.
        None while the hold stands; otherwise why not, as apply says it: the hold ended (`ended` is set), or the gate
        didn't confirm it."""
        if not (self.ended.is_set() or self.lost):
            try:
                answer = self.client.post("/v1/apply/renew", {"since": self.since})
            except GateError as err:
                return f"the gate didn't confirm that its hold on new requests still stands ({err})"
            if answer.get("ended") is True:
                self.ended.set()
            elif answer.get("recreated") is True:
                self.lost = True
            elif answer.get("ok") is True:
                return None
            else:
                return ("the gate didn't confirm that its hold on new requests still stands (its answer to apply/renew "
                        "couldn't be read)")
        if self.lost and not self.ended.is_set():
            return ("the gate had lost its hold on new requests and re-created it (it started fresh from a damaged "
                    "state file), so it may have let requests through meanwhile")
        return ("the gate's hold on new requests has ended (it lapsed, or something else ended it), and requests flow "
                "again, which a restart now would cut off")

    def begin(self, units: list[str]) -> bool:
        """POST /v1/apply/begin, before the first restart: the units it restarts, saved with the hold. False when the
        gate says no hold stands, so apply restarts nothing it doesn't hold for."""
        return self.client.post("/v1/apply/begin", {"restarting": list(units)}).get("ended") is not True

    def gate_answers(self) -> str | None:
        """After the gate's restart: None once its control socket answers (any answer, a refusal too); otherwise, after
        GATE_READY_S, what went wrong, as apply says it."""
        start, said = self.clock(), False
        while True:
            try:
                self.client.get("/v1/status")
                return None
            except GateUnavailable:
                if self.clock() - start >= GATE_READY_S:
                    return (f"the gate didn't answer on its control socket within {GATE_READY_S:g} s: see `make logs "
                            "s=gate`")
                if not said:
                    self.log(f"apply: waiting up to {GATE_READY_S:g} s for the gate to answer after its restart")
                    said = True
            except GateError:  # it answered: a refusal, or an answer the client can't take, is the gate up
                return None
            self.sleep(POLL_S)

    def llama_swap_back(self) -> str | None:
        """After llama-swap's restart, under the hold: None once the gate's status shows llama-swap `ok` and the hold
        ended by the gate itself, which it does only once a /running read sent after llama-swap's new start has
        answered (gate/core.py's _check_apply_end), so an `ok` read before the restart never counts. Otherwise, after
        READY_SECONDS, what went wrong, as apply says it."""
        start, said, seen = self.clock(), False, None  # seen: the last health the gate gave
        while True:
            try:
                status = self.client.get("/v1/status")
            except GateError:
                status = None
            health = status.get("health") if status is not None else None
            if isinstance(health, dict):
                seen = health
                if health.get("llama_swap") == "ok" and status.get("applying") is None:
                    return None
            if self.clock() - start >= READY_SECONDS:
                if seen is None:
                    return (f"the gate didn't answer within {READY_SECONDS:g} s of llama-swap's restart, so apply can't "
                            "confirm llama-swap answers again: `make doctor` shows what's wrong")
                if seen.get("llama_swap") != "ok":
                    return (f"llama-swap didn't answer the gate within {READY_SECONDS:g} s of its restart: see `make "
                            "logs s=llama-swap`")
                # Concern 2's ruling: a safe false failure, said as one (the review's M4: one "but", with the prefix).
                return (f"the gate, which sees llama-swap answer, didn't confirm its new start within "
                        f"{READY_SECONDS:g} s. apply counts llama-swap as back only once the gate has ended the hold "
                        "itself, after a reading taken since the restart, so llama-swap may well be serving: `make "
                        "status` shows it, and a gate that can't read llama-swap's unit says so on its health line")
            if not said:
                self.log(f"apply: waiting up to {READY_SECONDS:g} s for the gate to see llama-swap answer again")
                said = True
            self.sleep(POLL_S)

    def end(self, *, reloads: bool) -> bool:
        """POST /v1/apply/end, once the restarts are done: the hold released, the held requests through admission,
        the registry read again, the residents reloaded (`reloads`: llama-swap restarted). A 503 says the gate is still
        ending it, past its bound (gate.app's STILL_FINISHING): said, and never a failure. False when it went wrong."""
        self.stop()
        try:
            self.end_client.post("/v1/apply/end", {})
        except GateRefused as err:
            if err.status == 503:
                self.log(f"apply: the restarts are done, and the gate is ending its hold on new requests. {err}")
                return True
            self.log(f"apply: the restarts are done, but the gate refused apply/end: {err}")
            return False
        except GateError as err:
            self.log(f"apply: the restarts are done, but the gate didn't answer apply/end ({err}), so the hold on new "
                     "requests ends a minute after its last renewal: the gate lets it lapse, or the front drops it once "
                     "the gate has been gone that long")
            return False
        self.log("apply: done: the gate has released the requests it held"
                 + (", and reloads the always-loaded models one at a time" if reloads else ""))
        return True

    def undrain(self) -> None:
        """POST /v1/undrain-all, apply's cleanup on any failure after drain-all: the hold's same one end. A 503 says
        the gate is still ending it; one that doesn't answer leaves the hold to lapse."""
        self.stop()
        try:
            self.end_client.post("/v1/undrain-all", {})
        except GateRefused as err:
            if err.status == 503:
                self.log(f"apply: the gate is ending its hold on new requests. {err}")
            elif self.since is None:  # drain-all's answer never came: a hold it made, if any, has had no renewal
                self.log(f"apply: the gate refused undrain-all ({err}), so if drain-all made a hold on new requests, "
                         "that hold ends by itself within a minute, when the gate lets it lapse")
            else:
                self.log(f"apply: the gate refused undrain-all ({err}), so the hold on new requests ends a minute "
                         "after its last renewal, when the gate lets it lapse")
        except GateError:
            if self.since is None:  # the review's M3: words for a drain-all whose answer was lost
                self.log("apply: the gate didn't answer undrain-all either, so if drain-all made a hold on new "
                         "requests, that hold ends by itself within a minute: the gate lets it lapse, or the front drops "
                         "it once the gate has been gone that long")
            else:
                self.log("apply: the gate didn't answer undrain-all, so the hold on new requests ends a minute after "
                         "its last renewal: the gate lets it lapse, or the front drops it once the gate has been gone "
                         "that long")
        else:
            self.log("apply: the hold on new requests is ended")


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


class _Progress:
    """How far a run has got, for what an interrupted run says it left, and whether apply's hold stands at the gate."""

    def __init__(self) -> None:
        self.held = False  # from drain-all, or just before it, to the hold's end
        self.stage = "nothing"  # "syncing" once the app's sync has begun, "written" once the files are


def apply_files(files: dict[str, str], etc: Path, *, installed: dict[str, str | None], unreadable: set[str],
                outdated: list[str], active, app_changes: list[str], now_ok: bool, dry_run: bool, sync_app, run_cmd,
                log, registry_old: Registry | None, registry_new: Registry, restart_front: bool = False,
                gate: ApplyGate | None = None, ask=None, quiet_s: float = QUIET_S,
                deadline_s: float = APPLY_DEADLINE_S, stays_up=None) -> int:
    """Stage root's files, or deploy the rest and restart what runs on it, as plan_restarts plans, in restart_order;
    either way, first the diff of what changes (diff_text), and the app's changed files by name. Root's files come
    first: while any is pending, apply stages them and stops before it asks the gate anything. A restart that waits for
    the quiet moment (RestartPlan.after_quiet) is made under apply's hold at the gate, after the quiet wait or `now_ok`
    (--now) and its question (_deploy); the rest at once. `ask(question)` is True for yes; `stays_up(unit)`, after the
    brake's restart, what went wrong, None once it stayed up (check_stays_up); `sync_app()` raises SyncError when it
    fails. 0: done, nothing to do, or root's files staged for `make install-units`. 1: refused (by a dry run too),
    declined, the app's sync failed, or a restart failed, wasn't confirmed or was put off. Ctrl-C, SIGHUP or SIGTERM:
    the hold ended, said, and 130, 129 or 143."""
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
        # Root's files first: until root has them, deploy and restart nothing else, list nothing else, and ask the
        # gate nothing (the cutover's Step 2, when no gate runs yet).
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
    progress = _Progress()
    stop = None
    try:
        code = _deploy(files=files, etc=etc, changed=changed, app_changes=app_changes, plan=plan, restarts=restarts,
                       active=active, now_ok=now_ok, dry_run=dry_run, sync_app=sync_app, run_cmd=run_cmd, log=log,
                       registry_old=registry_old, registry_new=registry_new, restart_front=restart_front, gate=gate,
                       ask=ask or (lambda question: None), quiet_s=quiet_s, deadline_s=deadline_s, stays_up=stays_up,
                       again=again, progress=progress)
    except (KeyboardInterrupt, Signalled) as err:
        stop, code = err, (130 if isinstance(err, KeyboardInterrupt) else err.code)
    finally:
        # The hold ends first, whatever ended the run (a failure that returned, an interruption, a GateError cli.main
        # says), and whatever then becomes of the terminal: a dropped SSH session hangs it up before SIGHUP comes, and
        # every write to it fails (Task 32's review, I1).
        if progress.held:
            progress.held = False
            gate.undrain()
    if stop is not None:
        why = "Ctrl-C" if isinstance(stop, KeyboardInterrupt) else stop.name
        if progress.stage == "nothing":
            log(f"apply: stopped by {why}; nothing was changed")
        elif progress.stage == "syncing":
            log(f"apply: stopped by {why} while syncing the app, which may be part-way synced; nothing else was "
                f"deployed or restarted — `{again}` syncs it again")
        else:
            log(f"apply: stopped by {why} after deploying the files; the next `{again}` restarts each unit still "
                "running older ones")
    return code


def _deploy(*, files: dict[str, str], etc: Path, changed: list[str], app_changes: list[str], plan: RestartPlan,
            restarts: list[str], active, now_ok: bool, dry_run: bool, sync_app, run_cmd, log,
            registry_old: Registry | None, registry_new: Registry, restart_front: bool, gate: ApplyGate | None, ask,
            quiet_s: float, deadline_s: float, stays_up, again: str, progress: _Progress) -> int:
    """apply_files' deploy, once root's files are installed: the gate's reading, the wait or --now's question and the
    hold, then the sync, the files, apply/begin, the restarts and the hold's end (apply_files ends it on a failure)."""
    first, gone = None, None
    if plan.after_quiet:  # what is in flight, by the front's counts: apply's first call to the gate
        try:
            if gate is None:
                raise GateUnavailable(f"apply has no client for the gate. {_STEP}")
            first = gate.quiet()
        except GateUnavailable as err:
            gone = err
        except GateError as err:  # it answered, but not with what is in flight: refused, or this login turned away
            log(f"apply: {'dry run — would refuse: ' if dry_run else 'nothing was changed: '}{err}")
            return 1
        if first is None and active(LLAMA_SWAP_UNIT) and not now_ok:
            why = "apply can't see what's in flight while the gate isn't answering"
            if dry_run:
                log(f"apply: dry run — would refuse: {why}; with --now it would restart {', '.join(restarts)}")
                return 1
            log(f"apply: {gone}")
            step = ("once it answers (`make doctor` shows what's wrong), `make restart-front` waits for a quiet moment"
                    if restart_front else "`make apply-now` restarts anyway")
            log(f"apply: nothing was changed: {why}; {step}")
            return 1
    if dry_run:
        for unit in plan.not_running:
            log(f"apply: dry run — {unit} isn't running; it starts with the new config")
        if first is not None and now_ok:
            count = len(first["inflight"])
            asking = (f", after asking whether to cut off the {count} request{'' if count == 1 else 's'} in flight"
                      if count else "")
            log(f"apply: dry run — would restart {', '.join(restarts)} now{asking}")
        elif first is not None:
            log(f"apply: dry run — would wait until nothing has been in flight for {quiet_s:g} s (for up to "
                f"{messages.duration(deadline_s)}, then offer to drain now), then restart {', '.join(restarts)}")
        else:
            log("apply: dry run — would restart " + (", ".join(restarts) or "nothing"))
        return 0

    def drain() -> float:
        progress.held = True  # before the call: one whose answer is lost may have made the hold, which undrain ends
        try:
            return gate.drain_all()
        except GateUnavailable as err:
            if not err.sent:  # it never reached the gate, so no hold was made (the review's M3)
                progress.held = False
            raise
        except GateRefused as err:
            # Refused, the gate made no hold, or took it back (its save failed): none to end. A 503 says the work goes
            # on past the route's bound, so the hold may stand (the re-review, beside M3).
            if err.status != 503:
                progress.held = False
            raise

    deadline = None  # the quiet wait's, kept for the drain waits after it (the review's M5)

    if first is not None and now_ok:
        if not first["front_connected"]:
            # Its last snapshot may be stale, so it names nobody; and drain-all's hold_all can't reach it (N5).
            question = ("The front isn't reporting its counts, so apply can't tell what's in flight. This restarts the "
                        "model service now and cuts off any request in flight. Continue? [y/N]")
        elif first["inflight"]:
            question = messages.apply_now_confirm([row["key_label"] for row in first["inflight"]])
        else:
            question = None
        if question is not None and not ask(question):
            log("apply: nothing was changed")
            return 1
        try:
            drain()
        except GateError as err:
            log(f"apply: nothing was changed: {err}")
            return 1
        log("apply: new requests are held until the restarts are done" if first["front_connected"] else
            "apply: the front isn't connected to the gate, so it holds no new requests; with --now apply restarts "
            "anyway, and any request in flight is cut off")
    elif first is not None:
        log(f"apply: {', and '.join(_COSTS[unit] for unit in plan.after_quiet)}, so apply restarts only once nothing "
            f"has been in flight for {quiet_s:g} s, by the front's counts")
        deadline = Deadline(gate.clock(), deadline_s)
        try:
            outcome = wait_for_quiet(gate.client, quiet_s=quiet_s, clock=gate.clock, say=log, ask=ask,
                                     sleep=gate.sleep, drain_all=drain, first=first, now_step=not restart_front,
                                     deadline=deadline)
            if outcome == "quiet":
                # Nothing in flight a moment ago, by the front's counts. One let through since, before the front
                # started holding, is waited for: "nothing in flight" counts only from a snapshot the front made under
                # its own hold, since drain-all (Task 32's review, C1); at the deadline, Dan says (M5).
                after = drain()
                if wait_for_drained(gate.client, after=after, deadline=deadline, ask=ask, clock=gate.clock,
                                    sleep=gate.sleep, say=log) == "declined":
                    outcome = "declined"
        except GateError as err:  # the gate gone GATE_READY_S, or an answer it refused or apply can't read
            log(f"apply: nothing was changed: {err}")
            return 1
        if outcome == "declined":
            log("apply: nothing was changed; " + ("`make restart-front` waits for a quiet minute again" if restart_front
                                                  else "`make apply` waits for a quiet minute again, and `make "
                                                       "apply-now` restarts now"))
            return 1
        log("apply: nothing is in flight, and new requests are held until the restarts are done")
    elif plan.after_quiet:  # the gate isn't answering: with --now, or with llama-swap stopped (the cutover's case)
        log(f"apply: {gone}")
        if active(LLAMA_SWAP_UNIT):
            log("apply: the gate isn't answering, so apply can't hold new requests; with --now it restarts anyway, and "
                "any request in flight is cut off")
        else:
            log("apply: llama-swap isn't running, so no model is answering a request: apply deploys without the gate, "
                "and restarts the front all the same")

    # Nothing is written until the drain is done (plan.md, *Deploy workflow*).
    if app_changes:
        progress.stage = "syncing"
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
    progress.stage = "written"
    for stamp in stamps:
        write_atomic(Path(etc) / stamp, "spark apply: a restart is owed to each unit that started before this file\n")
    write_tree({rel: files[rel] for rel in changed}, etc)
    inside = [unit for unit in restarts if unit != COMPOSE_UNIT]  # the compose unit restarts after the hold
    if progress.held and inside:
        try:
            begun = not gate.ended.is_set() and gate.begin(inside)
            why = "the gate's hold on new requests had already ended"
        except GateError as err:
            begun, why = False, f"the gate didn't take apply/begin ({err})"
        if not begun:
            log(f"apply: {why}, so apply restarted nothing; the files are deployed, and the next `{again}` restarts "
                "each unit still running older ones")
            return 1
    failed, llama_restarted = False, False
    after_step = ", and it waits for a quiet moment, then restarts each unit still running older ones"

    def ready(unit: str) -> str | None:
        """None once `unit`'s restart may go ahead under the hold; otherwise what stops it, as apply says it."""
        while True:
            problem = gate.confirm()
            if problem is not None:
                step = (f"run `{again}` again" if gate.ended.is_set() or gate.lost
                        else f"once the gate answers (`make doctor` shows what's wrong), run `{again}` again")
                return (f"{problem}; so apply restarts nothing more, {unit} included. Its files are deployed: "
                        f"{step}{after_step}")
            if now_ok or unit == GATE_UNIT:  # --now is Dan's say; the gate's restart cuts no request
                return None
            try:
                outcome = wait_for_drained(gate.client, after=gate.drained_at, deadline=deadline, ask=ask,
                                           clock=gate.clock, sleep=gate.sleep, say=log, written=True)
            except GateError as err:
                return (f"the gate didn't say what's in flight just before {unit}'s restart ({err}); so apply "
                        f"restarts nothing more, {unit} included. Its files are deployed: once the gate answers (`make "
                        f"doctor` shows what's wrong), run `{again}` again{after_step}")
            if outcome == "drained":
                return None
            if outcome == "declined":
                now = "" if restart_front else "; `make apply-now` restarts now"
                return (f"requests may still be in flight, so apply restarts nothing more, {unit} included. Its files "
                        f"are deployed: run `{again}` again{after_step}{now}")
            # "waited": the hold is confirmed again before the restart

    def end_hold() -> bool:
        """The hold's end, before the compose unit's restart: undrain-all after a failure, else apply/end. True when
        the run failed. `held` stays set until the call is done, so an interrupted end is ended again."""
        if failed:
            gate.undrain()
        ended_badly = failed or not gate.end(reloads=llama_restarted)
        progress.held = False
        return ended_badly

    for unit in restart_order(restarts + plan.not_running):
        if unit in plan.not_running:
            log(f"apply: {unit} isn't running; it starts with the new config")
            continue
        if unit == COMPOSE_UNIT and progress.held:
            failed = end_hold() or failed
        if progress.held and unit in (GATE_UNIT, FRONT_UNIT, LLAMA_SWAP_UNIT):
            # Concern 3's ruling: just before each restart under the hold, the gate confirms the hold still stands. One
            # it ended since the last renewal (a lapse, or something else) lets requests flow, which this restart
            # would cut off; one it lost and re-created (M7) may have let some through; one it can't confirm may have.
            # And just before the front's and llama-swap's, which cut what is in flight, the front's own counts under
            # its hold are read again (C1). Its files are deployed, so the restart is owed to the next run, which
            # waits for a quiet moment again.
            said = ready(unit)
            if said is not None:
                log(f"apply: {said}")
                failed = True
                break
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
        if unit == BRAKE_UNIT and stays_up is not None:
            said = stays_up(unit)
            if said is not None:
                failed = True
                log(f"apply: restarted {unit}, but {said}")
        if unit == GATE_UNIT and gate is not None:
            said = gate.gate_answers()
            if said is not None:  # nothing more: the front and llama-swap need a gate that answers
                log(f"apply: restarted {unit}, but {said}; apply restarts nothing more")
                failed = True
                break
        if unit == LLAMA_SWAP_UNIT:
            llama_restarted = True
            said = (gate.llama_swap_back() if progress.held else
                    "the gate isn't answering, so apply can't confirm llama-swap answers again: see `make logs "
                    "s=llama-swap`, and `make status` once the gate is back")
            if said is not None:
                failed = True
                log(f"apply: restarted {unit}, but {said}")
    if progress.held:
        failed = end_hold() or failed
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


def _seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and value > 0):
        raise argparse.ArgumentTypeError(f"{text!r} isn't a number of seconds above 0")
    return value


def register(subparsers) -> None:
    p = subparsers.add_parser("apply", help="render, validate and deploy on the Spark")
    p.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    p.add_argument("--now", action="store_true",
                   help="restart llama-swap and the front now, without waiting for a quiet moment; it asks first when "
                        "requests are in flight, which the restart cuts off (make apply-now)")
    p.add_argument("--restart", choices=[FRONT_UNIT],
                   help="restart the front, and the gate before it, as well: after a change to the key digests or the "
                        "key list, which they read only at start and apply doesn't deploy (make restart-front)")
    # The quiet wait's deadline, shortened for a drill (Task 50's S17), so the drill needs no code change and no
    # restart of the front; `--help` doesn't list it.
    p.add_argument("--deadline-s", type=_seconds, default=APPLY_DEADLINE_S, help=argparse.SUPPRESS)
    p.set_defaults(func=run)


def _silence() -> None:
    """stdout and stderr to /dev/null, at the descriptors, so nothing written later fails on a terminal that has hung
    up: not apply's lines, not cli.main's, not the flush at exit, which would turn exit 129 into 120."""
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, 1)
            os.dup2(null, 2)
        finally:
            os.close(null)
    except OSError:
        pass


def _say(line: str) -> None:
    """One of apply's lines, printed. A terminal that has hung up (EIO, after a dropped SSH session) never stops apply's
    cleanup: the first line that fails silences stdout and stderr (Task 32's review, I1)."""
    try:
        print(line, flush=True)
    except OSError:
        _silence()


def _ask(question: str) -> bool | None:
    """The question's answer from the terminal: True for y or yes. None, said, when there is no terminal to ask on, or
    it has hung up."""
    stdin = sys.stdin
    if stdin is None or not stdin.isatty():
        _say(f"{question} — there's no terminal to answer on, so that's a no")
        return None
    try:
        print(f"{question} ", end="", flush=True)
        reply = stdin.readline()
        if not reply:  # end of input, Ctrl-D: the next line starts on a line of its own
            print(flush=True)
    except OSError:
        _silence()
        return None
    return reply.strip().lower() in ("y", "yes")


def _catch_signals() -> dict:
    """SIGHUP (a dropped SSH session) and SIGTERM raise Signalled in apply's own thread, and Ctrl-C KeyboardInterrupt,
    as it would: the first of the three only, so a second never cuts the cleanup short. Returns the handlers they
    replace."""
    fired: list[int] = []

    def handler(signum, frame):
        if fired:
            return
        fired.append(signum)
        if signum == signal.SIGINT:
            raise KeyboardInterrupt
        raise Signalled(signum)

    return {signum: signal.signal(signum, handler) for signum in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM)}


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
    etc, src, app = Path(f"{DEPLOY}/etc"), repo / "spark", Path(f"{DEPLOY}/app")
    installed, changed_at, unreadable = read_copies(files)
    changed_at |= deployed_times(etc, app)
    # The gate's control socket, Dan's (spark-admin): no key. apply/end and undrain-all wait past the gate's own bound.
    gate = ApplyGate(GateClient(paths.GATE_CONTROL_SOCKET, REQUEST_TIMEOUT_S),
                     GateClient(paths.GATE_CONTROL_SOCKET, END_TIMEOUT_S), clock=_clock, sleep=_sleep, log=_say)
    before = _catch_signals()
    try:
        return apply_files(files, etc, installed=installed, unreadable=unreadable,
                           outdated=outdated_units(started, changed_at),
                           active=lambda unit: started.get(unit) is not None, app_changes=app_diff(src, app),
                           now_ok=args.now, dry_run=args.dry_run, sync_app=lambda: _sync_app(src, app),
                           run_cmd=lambda cmd: subprocess.run(cmd, check=True), log=_say,
                           registry_old=deployed_registry(etc / "models.yaml"), registry_new=registry,
                           restart_front=args.restart == FRONT_UNIT, gate=gate, ask=_ask, deadline_s=args.deadline_s,
                           stays_up=lambda unit: check_stays_up(unit, _show_restarts))
    finally:
        gate.stop()
        for signum, handler in before.items():
            signal.signal(signum, handler)
