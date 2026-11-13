"""`spark usage harvest`, run on the Mac: OpenCode's database, opened read-only, as one row of numbers per message to
the by-hand engines, for `spark usage import` on the Spark (the design's part 3).

Only the fields in _SQL are extracted, never a message's content, text or error message, nor a title or directory;
the table check and the canary's queries return only 1.
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


# The canary (the final review's U6 triage, 2026-11-12): OpenCode updates itself, and a message format that no longer
# holds one of these paths would read as "0 rows", or as every message still running or without counts. So when there
# are assistant messages at all, at least one must hold each path, or the harvest refuses, naming the path. Each query
# stops at its first match and returns only 1, never a value from the database.
CANARY_PATHS = ("$.model.providerID", "$.time.completed", "$.tokens.input")
_ANY_ASSISTANT = "SELECT 1 FROM session_message WHERE type = 'assistant' LIMIT 1"
_HOLDS = "SELECT 1 FROM session_message WHERE type = 'assistant' AND json_extract(data, ?) IS NOT NULL LIMIT 1"


def _canary(conn: sqlite3.Connection, db: Path) -> None:
    if conn.execute(_ANY_ASSISTANT).fetchone() is None:
        return  # a new install, or one that only holds user messages: nothing to read, and nothing wrong
    for path in CANARY_PATHS:
        if conn.execute(_HOLDS, (path,)).fetchone() is None:
            raise HarvestError(f"{db}: no assistant message holds {path}, so OpenCode's message format may have "
                               f"changed; this reader was tested with OpenCode {TESTED_WITH}")


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_REFUSED: dict = {}


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
    if any(c is None for c in counts):
        return _REFUSED  # a missing count is absent, never 0
    inp, out, reas, cr, cw = counts
    return {"v": 1, "src": "opencode", "mid": mid, "sid": hashlib.sha256(str(sid).encode()).hexdigest()[:16],
            "prov": prov, "model": model, "t0": t0, "t1": t1, "in": inp, "out": out, "reas": reas, "cr": cr,
            "cw": cw, "end": "done" if error is None else "cut" if error == "aborted" else "error"}


def harvest(db: Path, providers: list[Provider], since: float | None, stats: dict | None = None) -> Iterator[dict]:
    """One row per message, in importer.FIELDS. A message that can't be a row is left out: still running, no counts
    at all, outside its provider's window (all uncounted), or with a count missing or refused by the importer's own
    check (`stats["skipped"]`, when given, counts these). Every result is fetched and the connection closed before
    the first row is yielded, so a stalled reader never holds OpenCode's read snapshot open."""
    if not db.is_file():  # mode=ro would not create it, but say so in words
        raise HarvestError(f"{db} doesn't exist; this reader was tested with OpenCode {TESTED_WITH}")
    if not providers:
        return
    windows = {p.provider: p for p in providers}
    sql = _SQL.format(marks=", ".join("?" for _ in windows))
    params = (*windows, 0 if since is None else since * 1000)
    changed = f"{db} isn't an OpenCode database (tested with {TESTED_WITH})"
    try:
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)  # as_uri escapes #, ? and %
        try:
            found = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'session_message'")
            if found.fetchone() is None:
                raise HarvestError(f"{changed}: no session_message table")
            _canary(conn, db)
            fetched = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError as err:
        if "locked" in str(err) or "busy" in str(err):
            raise HarvestError(f"{db} couldn't be read just now (it is busy); try again") from err
        raise HarvestError(f"{changed}: {err}") from err
    except sqlite3.DatabaseError as err:  # not a SQLite file
        raise HarvestError(f"{changed}: {err}") from err
    now = time.time()
    for raw in fetched:
        row = _row(raw, windows)
        if row is None:
            continue
        if row is _REFUSED or importer.check(row, now) is not None:
            if stats is not None:
                stats["skipped"] = stats.get("skipped", 0) + 1
            continue
        yield row
