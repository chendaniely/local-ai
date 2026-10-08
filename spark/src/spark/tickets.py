"""Admission tickets (website/design/plan.md, *The front and the gate*): the gate issues one for each load it admits,
and `spark launch` uses it up, so no engine starts around the gate. Launch then keeps the ticket under `started/`
while the load runs, which the brake reads as a load in progress while the gate's record is stale, and the gate for
its bypass check.

Everything lives in launch's folder (paths.LAUNCH, 0750 spark:spark): `tickets/<model>.json`, one per model, and
`started/<model>.json`. The gate, launch and the brake all run as spark, so a ticket or record that spark doesn't own
isn't one. A ticket is used once: a claim renames it to a name of its own before reading it, so of two claims at once
only one finds it, and it leaves `tickets/` whatever the claim finds. Every file is written whole (a reader finds the
old one or the new one, never part of one), mode 0600, and read without following a link or waiting on a FIFO: launch
never waits.
"""

from __future__ import annotations

import json
import math
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark.registry import NAME

TICKETS, STARTED = "tickets", "started"  # launch's folder's two, beside launch.REFUSALS
# A started record older than this is no load in progress: twice llama-swap's 180 s healthCheckTimeout, which Task 12's
# test ties to render.HEALTH_CHECK_TIMEOUT_S. So a record the gate never cleared can't weaken the brake's watch.
STARTED_EXPIRES_S = 360
RECORD_MAX_BYTES = 65536  # a ticket is a few hundred bytes: a larger file isn't one
NO_TICKET, TICKET_EXPIRED = "no_ticket", "ticket_expired"
NO_TICKET_WHY = "no admission ticket from the gate"
_NUMBERS = ("issued_at", "deadline", "footprint_gib", "available_gib")
_TEXTS = ("model", "id", "nonce", "boot_id")


@dataclass(frozen=True)
class Claim:
    ok: bool
    code: str | None  # NO_TICKET or TICKET_EXPIRED when not ok
    why: str  # for launch's refusal: one line
    ticket: dict | None  # the ticket, when ok


def issue(folder: Path, model: str, deadline: float, *, now: float, footprint_gib: float, available_gib: float,
          boot_id: str) -> str:
    """The gate's ticket for one load of `model`, at `<folder>/tickets/<model>.json`: the footprint rule 9 admitted
    (never the cold load), MemAvailable at admission, and this boot's id. It expires at `deadline`, the load's. Returns
    its id."""
    ticket = {"model": model, "id": secrets.token_hex(8), "issued_at": now, "deadline": deadline,
              "nonce": secrets.token_hex(16), "boot_id": boot_id, "footprint_gib": footprint_gib,
              "available_gib": available_gib}
    problem = _ticket_problem(ticket)
    if problem:
        raise ValueError(f"not a ticket: {problem}")
    write_whole(record_path(folder, TICKETS, model), ticket)
    return ticket["id"]


def claim(folder: Path, model: str, now: float, *, boot_id: str) -> Claim:
    """Use up the gate's ticket for `model`: renamed first to a name of this claim's own, so only one claim of it can
    win, then read, and gone from `tickets/` whatever it holds. Refused NO_TICKET when there is none, or what is there
    is damaged, another account's, or another model's; TICKET_EXPIRED when its deadline has passed (`deadline < now`)
    or it is another boot's, since a boot can set the wall clock back."""
    path = record_path(folder, TICKETS, model)
    claimed = path.with_name(f".{path.name}.claim-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        os.rename(path, claimed)
    except FileNotFoundError:
        return Claim(False, NO_TICKET, NO_TICKET_WHY, None)
    except OSError as err:
        return Claim(False, NO_TICKET, f"{NO_TICKET_WHY}: {path} can't be claimed ({err})", None)
    try:
        ticket = read_record(claimed, own=True)
    except (OSError, ValueError) as err:
        return Claim(False, NO_TICKET, f"{NO_TICKET_WHY}: {path} isn't one ({err})", None)
    finally:
        _remove(claimed)
    problem = _ticket_problem(ticket)
    if problem:
        return Claim(False, NO_TICKET, f"{NO_TICKET_WHY}: {path} isn't one ({problem})", None)
    if ticket["model"] != model:
        return Claim(False, NO_TICKET, f"{NO_TICKET_WHY}: {path} is {ticket['model']}'s", None)
    if ticket["boot_id"] != boot_id:
        return Claim(False, TICKET_EXPIRED, f"the gate's admission ticket in {path} is from another boot", None)
    if ticket["deadline"] < now:
        return Claim(False, TICKET_EXPIRED,
                     f"the gate's admission ticket in {path} expired {now - ticket['deadline']:.1f} s before this start",
                     None)
    return Claim(True, None, f"ticket {ticket['id']}", ticket)


def mark_started(folder: Path, ticket: dict, now: float) -> None:
    """Launch, just before its exec: the claimed ticket with `started_at` and launch's pid, which the engine keeps
    across the exec, at `<folder>/started/<model>.json`."""
    write_whole(record_path(folder, STARTED, ticket["model"]), ticket | {"started_at": now, "pid": os.getpid()})


def started(folder: Path, *, now: float, boot_id: str) -> list[dict]:
    """The loads in progress, oldest first: each started record of this boot, started at most STARTED_EXPIRES_S before
    `now`. One dated after `now` (the clock went back), or that isn't a whole record of spark's under its model's name,
    is passed over, so none can weaken the brake's watch for longer. A folder that can't be read raises OSError."""
    try:
        entries = list(os.scandir(Path(folder) / STARTED))
    except FileNotFoundError:
        return []
    records = []
    for entry in entries:
        model = entry.name.removesuffix(".json")
        if entry.name.startswith(".") or model == entry.name:
            continue  # a write not yet swapped in, or not a record
        try:
            record = read_record(Path(entry.path), own=True)
        except (OSError, ValueError):
            continue
        if _ticket_problem(record) or record["model"] != model or not _is_number(record.get("started_at")):
            continue
        pid = record.get("pid")
        if not (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0):
            continue
        if record["boot_id"] == boot_id and 0 <= now - record["started_at"] <= STARTED_EXPIRES_S:
            records.append(record)
    return sorted(records, key=lambda r: r["started_at"])


def clear_started(folder: Path, model: str) -> None:
    """The load is ready, has failed or is gone, or launch's exec failed: its record goes. One already gone is no
    error."""
    record_path(folder, STARTED, model).unlink(missing_ok=True)


def withdraw(folder: Path, model: str) -> bool:
    """The gate takes back a ticket its load didn't use: True if it was there, False if it wasn't (claimed, or never
    issued)."""
    try:
        record_path(folder, TICKETS, model).unlink()
    except FileNotFoundError:
        return False
    return True


def write_whole(path: Path, data: dict) -> None:
    """`data` as JSON at `path`, mode 0600, its folder made if missing: written to a new file of this write's own, then
    swapped in, so a reader finds the old file or the new one, never part of one, and no file someone left at the
    temporary name is written through."""
    path.parent.mkdir(mode=0o750, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, allow_nan=False))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_record(path: Path, *, own: bool) -> Any:
    """The JSON in `path`, read without following a link and without waiting (opening a FIFO waits for a writer), and
    only from a regular file of at most RECORD_MAX_BYTES; with `own`, only from one this account owns. Raises OSError,
    or ValueError for a file that is none of these, isn't JSON, or is nested too deep."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("not a regular file")
        if own and info.st_uid != os.geteuid():
            raise ValueError(f"owned by uid {info.st_uid}, not by this account")
        f = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with f:
        data = f.read(RECORD_MAX_BYTES + 1)
    if len(data) > RECORD_MAX_BYTES:
        raise ValueError(f"larger than {RECORD_MAX_BYTES} bytes")
    try:
        return json.loads(data)
    except RecursionError:
        raise ValueError("JSON nested too deep") from None


def record_path(folder: Path, kind: str, model: str) -> Path:
    """`<folder>/<kind>/<model>.json`. A name that isn't a model's (registry.NAME) is refused: it could name a file
    outside the folder."""
    if not (isinstance(model, str) and NAME.fullmatch(model)):
        raise ValueError(f"{model!r} isn't a model's name")
    return Path(folder) / kind / f"{model}.json"


def _remove(claimed: Path) -> None:
    """A claimed ticket goes, whatever it held: a file, or an empty folder someone put in a ticket's place."""
    try:
        claimed.unlink(missing_ok=True)
    except OSError:
        try:
            claimed.rmdir()
        except OSError:
            pass  # under a name no claim looks for, it can never be used again


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _ticket_problem(ticket: Any) -> str | None:
    """What makes `ticket` not one of the gate's, or None: an object whose texts are non-empty text and whose numbers
    are finite numbers (a NaN deadline would never read as passed)."""
    if not isinstance(ticket, dict):
        return "not a JSON object"
    for field in _TEXTS:
        if not (isinstance(ticket.get(field), str) and ticket[field]):
            return f"{field} must be non-empty text"
    for field in _NUMBERS:
        if not _is_number(ticket.get(field)):
            return f"{field} must be a finite number"
    return None
