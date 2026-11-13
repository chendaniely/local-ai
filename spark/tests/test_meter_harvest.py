"""spark.meter.harvest and `spark usage harvest`: OpenCode's database, read on the Mac, as rows of numbers. The
database here is invented and built in the test."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3

import pytest

from spark import cli
from spark.meter import harvest, importer, prices

NOW = 1_795_000_000.0
T0_MS = 1_793_000_000_000  # 2026-10-26, inside both providers' windows
SESSION = "ses_invented_secret_id"
CONTENT_MARK = "contentmarkerword"
ERROR_MARK = "errormarkerword"
TITLE_MARK = "titlemarkerword"

DDL = ("CREATE TABLE session_message (id text PRIMARY KEY, session_id text NOT NULL, type text NOT NULL, "
       "seq integer NOT NULL, time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL)")


def msg(provider="spark-sglang", model="qwen3.8-27b", created=T0_MS, completed=T0_MS + 5000,
        tokens=(100, 20, 5, 0, 0), error=None, **more) -> dict:
    data: dict = {"model": {"providerID": provider, "id": model}, "time": {"created": created},
                  "content": CONTENT_MARK}
    if completed is not None:
        data["time"]["completed"] = completed
    if tokens is not None:
        i, o, r, cr, cw = tokens
        data["tokens"] = {"input": i, "output": o, "reasoning": r, "cache": {"read": cr, "write": cw}}
    if error:
        data["error"] = {"type": error, "message": ERROR_MARK}
    data.update(more)
    return data


def build(path, rows) -> None:
    """rows: (id, type, data dict, time_updated)."""
    db = sqlite3.connect(path)
    db.execute(DDL)
    db.execute("CREATE TABLE session_v2 (id text PRIMARY KEY, title text)")
    db.execute("INSERT INTO session_v2 VALUES (?, ?)", (SESSION, TITLE_MARK))
    for seq, (mid, kind, data, updated) in enumerate(rows):
        db.execute("INSERT INTO session_message VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (mid, SESSION, kind, seq, data["time"]["created"] if "time" in data else T0_MS, updated,
                    json.dumps(data)))
    db.commit()
    db.close()


def standard(path) -> None:
    build(path, [
        ("m1", "assistant", msg(tokens=(100, 20, 5, 7, 3)), T0_MS + 6000),
        ("m2", "compaction", {"model": {"providerID": "spark-flash-next", "id": "flash"},
                              "time": {"created": T0_MS + 10_000}, "content": CONTENT_MARK,
                              "tokens": {"input": 50, "output": 10, "reasoning": 0, "cache": {"read": 40, "write": 0}}},
         T0_MS + 11_000),
        ("m3", "assistant", msg(created=T0_MS + 20_000, completed=T0_MS + 21_000, tokens=(9, 8, 0, 0, 0),
                                error="aborted"), T0_MS + 22_000),
        ("m4", "assistant", msg(provider="spark"), T0_MS + 6000),
        ("m5", "user", {"time": {"created": T0_MS}, "content": CONTENT_MARK}, T0_MS),
    ])


def providers():
    return prices.by_hand(prices.load(), "opencode")


def test_rows_from_an_invented_database(tmp_path):
    db = tmp_path / "o.db"
    standard(db)
    rows = list(harvest.harvest(db, providers(), None))
    assert [r["mid"] for r in rows] == ["m1", "m2", "m3"]
    a, b, c = rows
    assert (a["in"], a["out"], a["reas"], a["cr"], a["cw"]) == (100, 20, 5, 7, 3)
    assert (b["in"], b["out"], b["reas"], b["cr"], b["cw"]) == (50, 10, 0, 40, 0)
    assert (c["in"], c["out"], c["reas"], c["cr"], c["cw"]) == (9, 8, 0, 0, 0)
    assert (a["t0"], a["t1"]) == (T0_MS / 1000, (T0_MS + 5000) / 1000)
    assert (a["prov"], a["model"], a["v"], a["src"]) == ("spark-sglang", "qwen3.8-27b", 1, "opencode")
    assert [r["end"] for r in rows] == ["done", "done", "cut"]
    assert b["t1"] == b["t0"]
    assert set(a) == set(importer.FIELDS)


def test_other_error_is_error(tmp_path):
    db = tmp_path / "o.db"
    build(db, [("m1", "assistant", msg(error="provider.internal"), T0_MS)])
    assert [r["end"] for r in harvest.harvest(db, providers(), None)] == ["error"]


def test_nothing_but_numbers_and_ids_leaves(tmp_path, capsys, monkeypatch):
    db = tmp_path / "o.db"
    build(db, [("m1", "assistant", msg(error="provider.internal"), T0_MS)])
    rows = list(harvest.harvest(db, providers(), None))
    monkeypatch.setenv("SPARK_OPENCODE_DB", str(db))
    assert cli.main(["usage", "harvest", "--source", "opencode"]) == 0
    out, err = capsys.readouterr()
    text = json.dumps(rows) + out + err
    for mark in (CONTENT_MARK, ERROR_MARK, TITLE_MARK, SESSION):
        assert mark not in text
    assert rows[0]["sid"] == hashlib.sha256(SESSION.encode()).hexdigest()[:16]
    assert rows[0]["sid"] != SESSION


def test_running_and_tokenless_and_outside_window_skipped(tmp_path):
    db = tmp_path / "o.db"
    build(db, [
        ("run", "assistant", msg(completed=None), T0_MS),
        ("notok", "assistant", msg(tokens=None), T0_MS),
        ("early", "assistant", msg(created=1_770_000_000_000, completed=1_770_000_001_000), T0_MS),
        ("ok", "assistant", msg(), T0_MS),
    ])
    stats: dict = {}
    assert [r["mid"] for r in harvest.harvest(db, providers(), None, stats)] == ["ok"]
    assert "skipped" not in stats  # left out by the window, not refused by the check


def test_provider_end_is_a_window(tmp_path):
    db = tmp_path / "o.db"
    build(db, [("in", "assistant", msg(), T0_MS), ("late", "assistant", msg(created=T0_MS + 10_000_000,
                                                                              completed=T0_MS + 10_001_000), T0_MS)])
    ended = [prices.Provider("opencode", "spark-sglang", False, None, T0_MS / 1000 + 1000, None)]
    stats: dict = {}
    assert [r["mid"] for r in harvest.harvest(db, ended, None, stats)] == ["in"]
    assert "skipped" not in stats


def test_missing_count_is_skipped_and_counted(tmp_path):
    db = tmp_path / "o.db"
    gap = msg()
    del gap["tokens"]["cache"]["read"]
    build(db, [("gap", "assistant", gap, T0_MS), ("ok", "assistant", msg(), T0_MS)])
    stats: dict = {}
    assert [r["mid"] for r in harvest.harvest(db, providers(), None, stats)] == ["ok"]
    assert stats["skipped"] == 1


def test_odd_characters_in_the_path_stay_read_only(tmp_path):
    folder = tmp_path / "d"
    folder.mkdir()
    db = folder / "a#b?c%d.db"
    standard(db)
    before = sorted(p.name for p in folder.iterdir())
    assert len(list(harvest.harvest(db, providers(), None))) == 3
    assert sorted(p.name for p in folder.iterdir()) == before


def test_connection_is_read_only_and_closed_before_rows(tmp_path, monkeypatch):
    db = tmp_path / "o.db"
    standard(db)
    seen = []
    real = sqlite3.connect

    def spy(target, *args, **kwargs):
        seen.append((target, kwargs))
        conn = real(target, *args, **kwargs)
        seen.append(conn)
        return conn

    monkeypatch.setattr(harvest.sqlite3, "connect", spy)
    rows = harvest.harvest(db, providers(), None)
    next(rows)
    target, kwargs = seen[0]
    assert target.endswith("?mode=ro") and kwargs.get("uri") is True
    with pytest.raises(sqlite3.ProgrammingError):  # closed already, with rows still to come
        seen[1].execute("SELECT 1")


def test_a_busy_database_is_not_called_a_schema_change(tmp_path, monkeypatch):
    db = tmp_path / "o.db"
    standard(db)

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(harvest.sqlite3, "connect", busy)
    with pytest.raises(harvest.HarvestError, match="busy") as err:
        list(harvest.harvest(db, providers(), None))
    assert "isn't an OpenCode" not in str(err.value)


def test_not_a_sqlite_file(tmp_path):
    db = tmp_path / "junk.db"
    db.write_bytes(b"this is not a database at all" * 20)
    with pytest.raises(harvest.HarvestError, match=r"isn't an OpenCode database \(tested with 2\.2\.7\)"):
        list(harvest.harvest(db, providers(), None))


def test_since(tmp_path):
    db = tmp_path / "o.db"
    build(db, [("old", "assistant", msg(), T0_MS), ("new", "assistant", msg(), T0_MS + 100_000)])
    rows = list(harvest.harvest(db, providers(), (T0_MS + 50_000) / 1000))
    assert [r["mid"] for r in rows] == ["new"]


def test_refused_row_is_skipped_and_counted(tmp_path, capsys, monkeypatch):
    db = tmp_path / "o.db"
    build(db, [("good", "assistant", msg(), T0_MS), ("bad id", "assistant", msg(), T0_MS)])
    monkeypatch.setenv("SPARK_OPENCODE_DB", str(db))
    assert cli.main(["usage", "harvest", "--source", "opencode"]) == 0
    out, err = capsys.readouterr()
    assert [json.loads(x)["mid"] for x in out.splitlines()] == ["good"]
    assert err.strip() == "harvest: 1 rows, skipped 1"


def test_read_only(tmp_path):
    db = tmp_path / "o.db"
    standard(db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.commit()
    conn.close()
    before = (db.read_bytes(), os.stat(db).st_mtime_ns)
    assert len(list(harvest.harvest(db, providers(), None))) == 3
    assert (db.read_bytes(), os.stat(db).st_mtime_ns) == before
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("UPDATE session_message SET seq = seq + 1")
    try:
        assert len(list(harvest.harvest(db, providers(), None))) == 3
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_missing_and_changed_database(tmp_path, capsys, monkeypatch):
    missing = tmp_path / "none.db"
    with pytest.raises(harvest.HarvestError, match=r"2\.2\.7"):
        list(harvest.harvest(missing, providers(), None))
    assert not missing.exists()
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE message (id text)")
    conn.commit()
    conn.close()
    with pytest.raises(harvest.HarvestError, match=r"2\.2\.7"):
        list(harvest.harvest(other, providers(), None))
    for path in (missing, other):
        assert cli.main(["usage", "harvest", "--source", "opencode", "--db", str(path)]) == 1
        assert capsys.readouterr().err.count("2.2.7") == 1


def test_default_db_resolves_when_called(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARK_OPENCODE_DB", str(tmp_path / "x.db"))
    assert harvest.default_db() == tmp_path / "x.db"


def test_rows_pass_the_import_check(tmp_path):
    db = tmp_path / "o.db"
    standard(db)
    rows = list(harvest.harvest(db, providers(), None))
    assert rows
    assert all(importer.check(r, NOW) is None for r in rows)


def _moved(data: dict, path: str) -> dict:
    """The message with one JSON path renamed, as an OpenCode update that moved a field would leave it."""
    if path == "$.model.providerID":
        data["model"]["provider"] = data["model"].pop("providerID")
    elif path == "$.time.completed":
        data["time"]["done"] = data["time"].pop("completed")
    else:
        data["tokens"]["in"] = data["tokens"].pop("input")
    return data


@pytest.mark.parametrize("path", ["$.model.providerID", "$.time.completed", "$.tokens.input"])
def test_a_moved_field_is_refused_not_read_as_no_rows(tmp_path, capsys, path):
    # OpenCode updates itself; if its assistant messages stop holding a path the harvest reads, zero rows must not hide
    # it (the final review's U6 canary). The words name the path and hold nothing from the database.
    db = tmp_path / "o.db"
    build(db, [("m1", "assistant", _moved(msg(), path), T0_MS),
               ("m2", "assistant", _moved(msg(provider="spark", model="secretmodel"), path), T0_MS),
               ("m3", "user", {"time": {"created": T0_MS}, "content": CONTENT_MARK}, T0_MS)])
    with pytest.raises(harvest.HarvestError, match=re.escape(path)):
        list(harvest.harvest(db, providers(), None))
    assert cli.main(["usage", "harvest", "--source", "opencode", "--db", str(db)]) == 1
    out, err = capsys.readouterr()
    assert out == "" and path in err and "2.2.7" in err
    for mark in (CONTENT_MARK, SESSION, "secretmodel", "spark-sglang", "m1"):
        assert mark not in err.replace(str(db), "")


def test_one_message_holding_each_field_is_enough(tmp_path):
    # Older messages from before a field existed don't trip the canary, so long as some assistant message holds it.
    db = tmp_path / "o.db"
    build(db, [("old", "assistant", _moved(_moved(msg(), "$.time.completed"), "$.tokens.input"), T0_MS),
               ("ok", "assistant", msg(), T0_MS)])
    assert [r["mid"] for r in harvest.harvest(db, providers(), None)] == ["ok"]


def test_no_assistant_messages_is_no_rows(tmp_path, capsys, monkeypatch):
    db = tmp_path / "o.db"
    build(db, [("u1", "user", {"time": {"created": T0_MS}, "content": CONTENT_MARK}, T0_MS)])
    monkeypatch.setenv("SPARK_OPENCODE_DB", str(db))
    assert cli.main(["usage", "harvest", "--source", "opencode"]) == 0
    out, err = capsys.readouterr()
    assert out == "" and err.strip() == "harvest: 0 rows"
    empty = tmp_path / "e.db"
    build(empty, [])
    assert list(harvest.harvest(empty, providers(), None)) == []


def test_no_by_hand_providers_prints_nothing(tmp_path, capsys, monkeypatch):
    db = tmp_path / "o.db"
    standard(db)
    monkeypatch.setenv("SPARK_OPENCODE_DB", str(db))
    monkeypatch.setattr(prices, "by_hand", lambda book, harness: [])
    assert cli.main(["usage", "harvest", "--source", "opencode"]) == 0
    out, err = capsys.readouterr()
    assert out == "" and err.strip() == "harvest: 0 rows"
