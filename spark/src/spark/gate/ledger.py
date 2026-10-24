"""The gate's ledger (the usage meter's counter, Task 44e; website/design/usage-meter.md, §2): a row for each event of
the gate's, into the month's `ledger-YYYY-MM.jsonl` in paths.GATE_LEDGER_DIR, by the row writer the front's usage rows
use (spark.rowfile.RowFile), from a thread of its own, never in the gate's way. Load history can't be recovered later,
so the gate writes it as it happens.

**A row**, by the plan's *The row and the ledger row*, a field whose value is None left out: `v`, the row's version;
`t`, when the event happened, in UTC epoch seconds, by the gate's clock or by the time the event carries (a brake
event's own, the front's refusal's, the front's start, counting off), so the file isn't strictly in `t`'s order; `kind`;
and what each kind takes:
- `load`: `model`; `id`, the request that started it, none for a command's, a reload's or a load a restarted gate
  found; `wait_s`, that request's wait in the gate's queue until the load's ticket was issued; `seconds`, from the
  ticket (or launch's start, for a load found starting) to its end; `outcome`: `ready`, `failed`, or `requeued`, when
  no start of the model's own ended and its requests went back to the queue (an unload's abort, the brake's among
  them, or llama-swap refusing or not answering).
- `unload`: `model`; `why`, a drain's (gateproto.DRAIN_WHY), or `brake`, `apply` (llama-swap's restart under apply's
  hold), or `gone` (it left /running with no unload of the gate's: a crash, or a kill around the gate).
- `refusal`: `code`, `model` and `id`.
- `brake_fired` and `brake_released`.
- `start` and `stop`: `who`, `front` or `gate`.
- `counting_off` and `counting_on`: `who`, whose writer; off at the time it was reported off, and on again with
  `seconds`, how long it was off, so a span shows even when the ledger's own writer couldn't write the row that began
  it.

**What a row may hold.** No key's name: a row joins the front's usage row by the request's id, which holds the key, so
the gate's user, which is the engines' too, keeps no timeline per key. `model` only when it is a name the registry has
(`models()`, asked at each row, so a registry read again at apply's end counts); `id` only as the front mints one, 16
lowercase hex; `why`, `code`, `outcome` and `who` only from the sets here, each kept as the ledger's own copy; numbers
only when finite. Anything else is left out of the row, never written; an event that isn't one (a kind or a who it
doesn't have) is no row at all.

**Never in the gate's way.** No method raises: what breaks is logged by its class alone, never its text, once until a
row goes in again. With no row file (`rows` None, as for the parts the tests build without one), nothing is written.
`counting` writes only on a change."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from typing import Any

from spark import messages, tickets
from spark.gateproto import DRAIN_WHY, FRONT_REFUSAL_CODES

VERSION = 1
PREFIX = "ledger"  # the row file's name: ledger-YYYY-MM.jsonl
WHO = ("front", "gate")
UNLOAD_WHY = (*DRAIN_WHY, "brake", "apply", "gone")
# Every refusal code the gate or the front gives (messages'), and launch's backstop's refusals of a start around the
# gate (tickets').
REFUSAL_CODES = tuple(dict.fromkeys((*messages.CODES_409, *messages.CODES_503, *messages.FRONT_CODES,
                                     *FRONT_REFUSAL_CODES, tickets.NO_TICKET, tickets.TICKET_EXPIRED)))
LOAD_OUTCOMES = ("ready", "failed", "requeued")
_BRAKE = {"fired": "brake_fired", "released": "brake_released"}
_LIFECYCLE = {"start": "start", "stop": "stop"}
_ID = re.compile(r"[0-9a-f]{16}")  # secrets.token_hex(8), as the front mints a request's id


def _own(values: tuple[str, ...]) -> dict[str, str]:
    return {value: value for value in values}  # the ledger's own copy of each, never the caller's string


_WHO, _WHY, _CODES, _OUTCOMES = _own(WHO), _own(UNLOAD_WHY), _own(REFUSAL_CODES), _own(LOAD_OUTCOMES)
_log = logging.getLogger(__name__)


def _finite(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _number(value: Any) -> float | None:
    return round(float(value), 3) if _finite(value) else None


def _one_of(table: dict[str, str], value: Any) -> str | None:
    return table.get(value) if isinstance(value, str) else None


class Ledger:
    """The gate's ledger. `rows` is its row file (rowfile.RowFile, or a stand-in with `put(row, at)` and `off_since()`;
    None writes nothing); `clock` gives Unix seconds; `models()` the names the registry has now."""

    def __init__(self, rows: Any, clock: Callable[[], float], models: Callable[[], frozenset[str]]):
        self._rows = rows
        self._clock = clock
        self._models = models
        self._counting: dict[str, float | None] = {}  # each writer's off_since, as last written: None is on
        self._failing: str | None = None  # the class of the last failure logged, until a row goes in again

    # The events.

    def load(self, model: str, request_id: str | None, wait_s: float | None, seconds: float | None,
             outcome: str) -> None:
        self._put("load", model=model, id=request_id, wait_s=wait_s, seconds=seconds, outcome=outcome)

    def unload(self, model: str, why: str, *, at: float | None = None) -> None:
        self._put("unload", at=at, model=model, why=why)

    def refusal(self, code: str, model: str | None, request_id: str | None, *, at: float | None = None) -> None:
        self._put("refusal", at=at, model=model, id=request_id, code=code)

    def brake(self, kind: str, *, at: float | None = None) -> None:
        """`fired` or `released`."""
        event = _one_of(_BRAKE, kind)
        if event is not None:
            self._put(event, at=at)

    def lifecycle(self, kind: str, who: str, *, at: float | None = None) -> None:
        """`start` or `stop`, of the `front` or the `gate`."""
        event = _one_of(_LIFECYCLE, kind)
        if event is not None and _one_of(_WHO, who) is not None:
            self._put(event, at=at, who=who)

    def counting(self, off_since: float | None, who: str) -> None:
        """`who`'s writer, off since `off_since` (anything but a time reads as on): a row only when that changed since
        the last call for `who`. The first call's None is no change: a writer counts as on until it says otherwise."""
        try:
            if _one_of(_WHO, who) is None:
                return
            since = float(off_since) if _finite(off_since) else None
            last = self._counting.get(who)
            if since == last:
                return
            self._counting[who] = since
            if since is not None:
                self._put("counting_off", at=since, who=who)
            else:
                now = self._clock()
                self._put("counting_on", at=now, who=who, seconds=max(0.0, now - last))
        except Exception as err:
            self._broke("counting's change", err)

    def off_since(self) -> float | None:
        """When the ledger's own row file stopped writing, or None while it writes (or there is none)."""
        try:
            value = self._rows.off_since() if self._rows is not None else None
            return float(value) if _finite(value) else None
        except Exception as err:
            self._broke("row file's state", err)
            return None

    # The row.

    def _put(self, kind: str, *, at: float | None = None, **fields: Any) -> None:
        try:
            if self._rows is None:
                return
            when = float(at) if _finite(at) else self._clock()
            row: dict[str, Any] = {"v": VERSION, "t": round(when, 3), "kind": kind}
            for name, value in fields.items():
                kept = self._keep(name, value)
                if kept is not None:
                    row[name] = kept
            self._rows.put(row, when)
            self._failing = None
        except Exception as err:
            self._broke(f"{kind} row", err)

    def _keep(self, name: str, value: Any) -> Any:
        if value is None:
            return None
        if name == "model":
            return value if isinstance(value, str) and value in self._models() else None
        if name == "id":
            return value if isinstance(value, str) and _ID.fullmatch(value) else None
        if name in ("wait_s", "seconds"):
            return _number(value)
        table = {"why": _WHY, "code": _CODES, "outcome": _OUTCOMES, "who": _WHO}[name]
        return _one_of(table, value)

    def _broke(self, what: str, err: Exception) -> None:
        """One journal line by the error's class, never its text, and only when it differs from the last."""
        try:
            why = type(err).__name__
            if why != self._failing:
                self._failing = why
                _log.error("the gate's ledger couldn't take its %s (%s); the gate goes on as ever", what, why)
        except Exception:
            pass
