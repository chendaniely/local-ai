"""`spark usage harvest`, run on the Mac: OpenCode's database, opened read-only, as one row of numbers per message to
the by-hand engines, for `spark usage import` on the Spark (the design's part 3).

Only the fields in QUERY are extracted, never a message's content, text or error message, nor a title or directory.
The session id is read only to hash it. At module level this imports only the standard library and `spark.meter`'s
own modules, which import only the standard library, `yaml` and `spark.paths`."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

from spark.meter import importer
from spark.meter.prices import Provider

TESTED_WITH = "2.2.7"


def default_db() -> Path:
    """OpenCode's database, resolved when called so the environment can redirect it."""
    return Path(os.environ.get("SPARK_OPENCODE_DB") or Path.home() / ".local/share/opencode/opencode.db")


# Kept for the interface's name; a module-level value is read at import, `default_db()` when called.
OPENCODE_DB = default_db()


class HarvestError(ValueError):
    """A database that can't be read as OpenCode's: its text says what and which version the reader knows."""


_SQL = """SELECT id, session_id, type,
       json_extract(data, '$.model.providerID'), json_extract(data, '$.model.id'),
       json_extract(data, '$.time.created'), json_extract(data, '$.time.completed'),
       json_extract(data, '$.tokens.input'), json_extract(data, '$.tokens.output'),
       json_extract(data, '$.tokens.reasoning'),
       json_extract(data, '$.tokens.cache.read'), json_extract(data, '$.tokens.cache.write'),
       json_extract(data, '$.error.type')
FROM session_message
WHERE type IN ('assistant', 'compaction')
  AND json_extract(data, '$.model.providerID') IN ({marks})
  AND time_updated >= ?
ORDER BY time_created"""


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _row(raw: tuple, windows: dict[str, Provider]) -> dict | None:
    mid, sid, kind, prov, model, created, completed, *counts, error = raw
    if not (_number(created) and isinstance(prov, str) and prov in windows):
        return None
    t0 = created / 1000
    if kind == "assistant":
        if not _number(completed):
            return None  # still running
        t1 = completed / 1000
    else:
        t1 = t0  # a compaction has no completed time
    if all(c is None for c in counts):
        return None
    window = windows[prov]
    if (window.start is not None and t0 < window.start) or (window.end is not None and t0 >= window.end):
        return None
    inp, out, reas, cr, cw = (0 if c is None else c for c in counts)
    return {"v": 1, "src": "opencode", "mid": mid, "sid": hashlib.sha256(str(sid).encode()).hexdigest()[:16],
            "prov": prov, "model": model, "t0": t0, "t1": t1, "in": inp, "out": out, "reas": reas, "cr": cr,
            "cw": cw, "end": "done" if error is None else "cut" if error == "aborted" else "error"}


def harvest(db: Path, providers: list[Provider], since: float | None, stats: dict | None = None) -> Iterator[dict]:
    """One row per message, in importer.FIELDS. A message that can't be a row (still running, no counts, outside
    its provider's window, or refused by the importer's own check) is left out; `stats["skipped"]`, when given,
    counts the ones the check refused."""
    words = f"{db} isn't an OpenCode {TESTED_WITH} database ({{}}); this reader was written against {TESTED_WITH}"
    if not db.is_file():  # mode=ro would not create it, but say so in words
        raise HarvestError(words.format("no such file"))
    if not providers:
        return
    windows = {p.provider: p for p in providers}
    sql = _SQL.format(marks=", ".join("?" for _ in windows))
    now = time.time()
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as err:
        raise HarvestError(words.format(f"can't be opened: {err}")) from err
    try:
        try:
            found = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'session_message'")
            if found.fetchone() is None:
                raise HarvestError(words.format("no session_message table"))
            cursor = conn.execute(sql, (*windows, 0 if since is None else since * 1000))
            for raw in cursor:
                row = _row(raw, windows)
                if row is None:
                    continue
                if importer.check(row, now) is not None:
                    if stats is not None:
                        stats["skipped"] = stats.get("skipped", 0) + 1
                    continue
                yield row
        except sqlite3.Error as err:
            raise HarvestError(words.format(f"can't be read: {err}")) from err
    finally:
        conn.close()
