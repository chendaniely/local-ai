"""The meter's rows (the design's part 3): the front's usage rows, the rows imported from a harness's own session
store, and the gate ledger's spans when counting was off. It reads; nothing here writes, and nothing stored holds a
dollar. At module level this imports only the standard library, `spark.paths` and `spark.meter.prices`.

A missing folder reads as empty (nothing counted yet); any other OSError is the caller's to report, since a summary
that silently dropped a month it couldn't read would be wrong. A line that isn't a row is skipped and counted, never
raised, so one torn line (a crash mid-write) doesn't hide the rest."""

from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spark import paths
from spark.meter.prices import PriceBook, by_hand, cache_reported

_HEX16 = re.compile(r"[0-9a-f]{16}")
_FRONT_FILE = re.compile(r"usage-\d{4}-\d{2}\.jsonl")
_LEDGER_FILE = re.compile(r"ledger-\d{4}-\d{2}\.jsonl")


@dataclass(frozen=True)
class Row:
    """One request or one model call. `c` is None when its engine reported no cache; `c_estimated` marks one the
    meter estimated (with_estimates). A front row has `key`; an imported row has `provider` and `session`."""
    source: str  # "front" or "opencode"
    id: str
    t0: float
    t1: float | None
    model: str | None
    p: int | None
    c: int | None
    o: int | None
    end: str
    key: str | None = None
    provider: str | None = None
    session: str | None = None
    c_estimated: bool = False
    dropped: int = 0


@dataclass
class Store:
    rows: list[Row] = field(default_factory=list)
    counting_off: list[tuple[float, float | None]] = field(default_factory=list)
    bad_lines: int = 0
    last_import: float | None = None
    imported: int = 0


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ok(value: Any) -> bool:
    """A count the meter will read: a whole number, never a bool, with 0 <= n < 2**53."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**53


def _count(value: Any) -> int | None:
    return value if _ok(value) else None


_EARLIEST = 1_577_836_800.0  # 2020-01-01T00:00Z
_LATEST = 4_102_444_800.0  # 2100-01-01T00:00Z


def _time(value: Any) -> bool:
    """A time the meter will read: epoch seconds from 2020 up to 2100. JSON's NaN and Infinity, which json.loads
    accepts, and a millisecond stamp are refused here, before a summary places them on a calendar."""
    return _number(value) and _EARLIEST <= value < _LATEST  # NaN fails both comparisons


def _times(data: dict) -> bool:
    """A row's t0 is a time, and its t1, when it has one, is too."""
    return _time(data.get("t0")) and (data.get("t1") is None or _time(data["t1"]))


FRONT_END = ("done", "cut", "gone", "error", "refused")
IMPORTED_END = ("done", "error", "cut")


def _lines(folder: Path, pattern: re.Pattern[str]) -> list[tuple[Path, str]]:
    """Every line of the folder's files that match `pattern`, in name order. A missing folder is empty."""
    try:
        names = sorted(n for n in os.listdir(folder) if pattern.fullmatch(n))
    except FileNotFoundError:
        return []
    out = []
    for name in names:
        with open(folder / name, encoding="utf-8", errors="replace") as handle:
            out.extend((folder / name, line) for line in handle if line.strip())
    return out


def _object(line: str) -> dict | None:
    try:
        data = json.loads(line)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _front_rows(folder: Path) -> tuple[list[Row], int]:
    rows, bad = [], 0
    for _, line in _lines(folder, _FRONT_FILE):
        data = _object(line)
        if (data is None or data.get("v") != 1 or isinstance(data.get("v"), bool)
                or not isinstance(data.get("id"), str) or not _HEX16.fullmatch(data["id"])
                or not _times(data) or data.get("end") not in FRONT_END
                or any(k in data and not _ok(data[k]) for k in ("p", "c", "o"))):
            bad += 1
            continue
        t1, model, key = data.get("t1"), data.get("model"), data.get("key")
        rows.append(Row("front", data["id"], float(data["t0"]), float(t1) if _number(t1) else None,
                        model if isinstance(model, str) else None, _count(data.get("p")), _count(data.get("c")),
                        _count(data.get("o")), data["end"], key if isinstance(key, str) else None,
                        dropped=_count(data.get("dropped")) or 0))
    return rows, bad


def front_rows(folder: Path | None = None) -> list[Row]:
    """Every usage-YYYY-MM.jsonl row of `folder` (paths.USAGE_DIR, read when called)."""
    return _front_rows(paths.USAGE_DIR if folder is None else folder)[0]


def _imported_rows(book: PriceBook, folder: Path) -> tuple[list[Row], int]:
    last: dict[str, dict] = {}
    bad = 0
    for _, line in _lines(folder, re.compile(r"opencode\.jsonl")):
        data = _object(line)
        if (data is None or data.get("v") != 1 or not isinstance(data.get("mid"), str)
                or not isinstance(data.get("sid"), str) or not _times(data)
                or not isinstance(data.get("prov"), str) or data.get("end") not in IMPORTED_END
                or not all(_ok(data.get(k)) for k in ("in", "out", "reas", "cr", "cw"))):
            bad += 1
            continue
        last[data["mid"]] = data  # the last line of a message id wins
    rows = []
    for mid, data in last.items():
        t0, prov = float(data["t0"]), data["prov"]
        windows = [p for p in by_hand(book, "opencode") if p.provider == prov]
        if not windows:  # the front already counted a front provider's rows; counting them again would double them
            bad += 1
            continue
        if not any((p.start is None or p.start <= t0) and (p.end is None or t0 < p.end) for p in windows):
            continue
        counts = {k: data[k] for k in ("in", "out", "reas", "cr", "cw")}
        t1, model = data.get("t1"), data.get("model")
        rows.append(Row("opencode", mid, t0, float(t1) if _number(t1) else None,
                        model if isinstance(model, str) else None,
                        counts["in"] + counts["cr"] + counts["cw"], counts["cr"] if cache_reported(book, prov, t0) else None,
                        counts["out"] + counts["reas"], data["end"], None, prov, data["sid"]))
    return rows, bad


def imported_rows(book: PriceBook, folder: Path | None = None) -> list[Row]:
    """The imported rows of `folder` (paths.USAGE_SESSIONS_DIR, read when called): P = in + cr + cw, O = out + reas,
    C = cr only where the provider's engine reported a cache by then. A by-hand provider's rows from outside its
    window are left out, and a provider that isn't a by-hand one of OpenCode's is dropped, counted as a bad line."""
    return _imported_rows(book, paths.USAGE_SESSIONS_DIR if folder is None else folder)[0]


def counting_off(folder: Path | None = None) -> list[tuple[float, float | None]]:
    """The spans when counting was off, from the ledger's counting_off and counting_on rows in time order. An on with
    `seconds` and no off before it still gives (t - seconds, t); an off with no on after it ends None."""
    folder = paths.GATE_LEDGER_DIR if folder is None else folder
    events = []
    for _, line in _lines(folder, _LEDGER_FILE):
        data = _object(line)
        if data and data.get("kind") in ("counting_off", "counting_on") and _number(data.get("t")):
            events.append((float(data["t"]), data["kind"], data.get("seconds")))
    spans: list[tuple[float, float | None]] = []
    start: float | None = None
    for t, kind, seconds in sorted(events, key=lambda e: e[0]):
        if kind == "counting_off":
            start = t if start is None else start
        elif start is not None:
            spans.append((start, t))
            start = None
        elif _number(seconds) and seconds > 0:
            spans.append((t - float(seconds), t))
    if start is not None:
        spans.append((start, None))
    return spans


def with_estimates(rows: list[Row]) -> list[Row]:
    """The design's cache estimate for an imported row whose engine reported none: its cache read is taken to be the
    session's previous call's P, when this call's P is at least that (the prompt grew), else 0."""
    out, previous = [], {}  # session -> the previous row's P, by t0
    for row in sorted(rows, key=lambda r: (r.session or "", r.t0, r.id)):
        if row.source == "front" or row.session is None or row.p is None:
            out.append(row)
            continue
        before = previous.get(row.session)
        previous[row.session] = row.p
        if row.c is not None:
            out.append(row)
            continue
        c = before if before is not None and row.p >= before else 0
        out.append(dataclasses.replace(row, c=c, c_estimated=True))
    return out


def read(book: PriceBook) -> Store:
    """All three folders (read when called), with the estimates applied."""
    fronts, bad = _front_rows(paths.USAGE_DIR)
    imported, bad_imported = _imported_rows(book, paths.USAGE_SESSIONS_DIR)
    last_import = None
    try:
        last_import = float((paths.USAGE_SESSIONS_DIR / "opencode.imported").read_text().strip())
    except FileNotFoundError:
        pass
    except ValueError:
        pass
    return Store(with_estimates(fronts + imported), counting_off(), bad + bad_imported, last_import, len(imported))
