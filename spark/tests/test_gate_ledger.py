"""The gate's ledger (the usage meter's counter, Task 44e; website/design/usage-meter.md, §2): one row per event of the
gate's, a load, an unload, a refusal, the brake firing or released, a start or a stop, counting off or on again, put
on a row file (spark.rowfile) as the plan's *The row and the ledger row* names its fields. It holds no key name, and
nothing a client chose: a model only by the registry's name, an id only as the front mints one (16 hex), a why and a
code only from the gate's own sets. Its methods never raise into the gate. Every name and time here is a stand-in."""

import json
import logging
import math

from spark.gate import ledger as gate_ledger
from spark.gate.ledger import Ledger
from spark.gateproto import DRAIN_WHY

T = 1_790_000_000.0
CODER, GEMMA = "qwen3.8-27b", "gemma-4-26b-a4b"
MODELS = frozenset({CODER, GEMMA})
RID = "0123456789abcdef"  # a request's id, as the front mints it (secrets.token_hex(8))
FIELDS = {"v", "t", "kind", "who", "model", "id", "why", "code", "wait_s", "seconds", "outcome"}
LOG = "spark.gate.ledger"


class Clock:
    def __init__(self, now: float = T):
        self.now = now

    def __call__(self) -> float:
        return self.now


class Rows:
    """The row file's stand-in, as the ledger calls rowfile.RowFile: each row put kept with its time; with `raises`,
    each put raises it instead; `off` is what off_since() gives."""

    def __init__(self, raises: BaseException | None = None):
        self.rows: list[dict] = []
        self.at: list[float] = []
        self.raises = raises
        self.off: float | None = None

    def put(self, row, at):
        if self.raises is not None:
            raise self.raises
        self.rows.append(row)
        self.at.append(at)

    def off_since(self):
        return self.off


def make(rows: Rows | None = None, clock: Clock | None = None, models=lambda: MODELS) -> tuple[Ledger, Rows]:
    rows = Rows() if rows is None else rows
    return Ledger(rows, clock or Clock(), models), rows


def test_each_event_is_one_row_with_no_key_name():
    ledger, rows = make()
    ledger.load(CODER, RID, 2.5, 31.25, "ready")
    ledger.unload(GEMMA, "idle")
    ledger.refusal("no_fit", CODER, RID)
    ledger.brake("fired", at=T - 5)
    ledger.brake("released")
    ledger.lifecycle("start", "gate")
    ledger.lifecycle("stop", "front")
    ledger.counting(T - 10, "front")
    ledger.counting(None, "front")
    assert rows.rows == [
        {"v": 1, "t": T, "kind": "load", "model": CODER, "id": RID, "wait_s": 2.5, "seconds": 31.25,
         "outcome": "ready"},
        {"v": 1, "t": T, "kind": "unload", "model": GEMMA, "why": "idle"},
        {"v": 1, "t": T, "kind": "refusal", "model": CODER, "id": RID, "code": "no_fit"},
        {"v": 1, "t": T - 5, "kind": "brake_fired"},  # when the brake fired, by its own event
        {"v": 1, "t": T, "kind": "brake_released"},
        {"v": 1, "t": T, "kind": "start", "who": "gate"},
        {"v": 1, "t": T, "kind": "stop", "who": "front"},
        {"v": 1, "t": T - 10, "kind": "counting_off", "who": "front"},  # when it went off, as the front reports it
        {"v": 1, "t": T, "kind": "counting_on", "who": "front", "seconds": 10.0},  # and how long it was off
    ]
    assert rows.at == [row["t"] for row in rows.rows]  # each row in the month of its own time
    for row in rows.rows:
        assert set(row) <= FIELDS and "key" not in row
        json.dumps(row, allow_nan=False)  # what the row file writes
    # Every why the gate has: its drains', and the brake's, apply's and a model gone from /running.
    for why in (*DRAIN_WHY, "brake", "apply", "gone"):
        ledger.unload(CODER, why)
        assert rows.rows[-1]["why"] == why


def test_a_clients_string_never_reaches_the_ledger():
    ledger, rows = make()
    # model_not_found's model is the client's own name, which passes the front's text pattern: never kept.
    ledger.refusal("model_not_found", "secretproj-q3", None)
    assert rows.rows == [{"v": 1, "t": T, "kind": "refusal", "code": "model_not_found"}]
    # An id that isn't one the front mints, a why, a code or an outcome the gate doesn't have: left out, the row kept.
    for request_id in ("r1", "load:qwen3.8-27b:3", "0123456789ABCDEF", RID + "0", "secret-id-q3"):
        ledger.refusal("no_fit", CODER, request_id)
        assert "id" not in rows.rows[-1] and rows.rows[-1]["code"] == "no_fit"
    ledger.unload(CODER, "secret-why")
    ledger.refusal("secret-code", CODER, RID)
    ledger.load("secretproj-q3", RID, math.nan, math.inf, "secret-outcome")
    assert rows.rows[-3:] == [{"v": 1, "t": T, "kind": "unload", "model": CODER},
                              {"v": 1, "t": T, "kind": "refusal", "model": CODER, "id": RID},
                              {"v": 1, "t": T, "kind": "load", "id": RID}]
    # An event that isn't one is no row at all.
    before = len(rows.rows)
    ledger.brake("secret")
    ledger.lifecycle("secret", "gate")
    ledger.lifecycle("start", "secret")
    ledger.counting(T, "secret")
    assert len(rows.rows) == before
    assert "secret" not in json.dumps(rows.rows)


def test_counting_writes_only_on_a_change():
    ledger, rows = make()
    for off_since in (None, 5.0, 5.0, 7.0, None):
        ledger.counting(off_since, "front")
    assert [(row["kind"], row["t"]) for row in rows.rows] == [("counting_off", 5.0), ("counting_off", 7.0),
                                                              ("counting_on", T)]
    # Each writer apart: the gate's own isn't the front's. A value that isn't a time is "not reported": on.
    ledger.counting(9.0, "gate")
    ledger.counting("x", "gate")
    ledger.counting(math.nan, "front")
    assert [(row["kind"], row["who"]) for row in rows.rows[3:]] == [("counting_off", "gate"), ("counting_on", "gate")]


def test_a_put_that_raises_never_reaches_the_caller(caplog):
    caplog.set_level(logging.ERROR, logger=LOG)
    ledger, rows = make(Rows(raises=OSError("secret text of the error")))
    ledger.load(CODER, RID, 1.0, 2.0, "failed")
    ledger.unload(CODER, "idle")
    ledger.refusal("no_fit", CODER, RID)
    ledger.brake("fired")
    ledger.lifecycle("start", "gate")
    ledger.counting(T - 1, "front")
    lines = [r.getMessage() for r in caplog.records if r.name == LOG]
    assert lines and all("OSError" in line and "secret" not in line for line in lines)
    # A registry that can't be read, or a clock that breaks, is the same: nothing raises.
    broken = Ledger(Rows(), lambda: 1 / 0, lambda: 1 / 0)
    broken.load(CODER, RID, 1.0, 2.0, "ready")
    broken.counting(T, "front")


def test_off_since_is_the_row_files_and_no_row_file_writes_nothing():
    ledger, rows = make()
    assert ledger.off_since() is None
    rows.off = T - 30
    assert ledger.off_since() == T - 30
    none = Ledger(None, Clock(), lambda: MODELS)
    none.load(CODER, RID, 1.0, 2.0, "ready")
    none.counting(T, "front")
    assert none.off_since() is None
    assert gate_ledger.PREFIX == "ledger"  # ledger-YYYY-MM.jsonl
