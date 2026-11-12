"""spark.meter.store: the meter reads the front's rows, the imported rows and counting's gaps. Every row is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spark import paths
from spark.meter import prices, store

BOOK = prices.load()
T = 1_793_000_000.0  # 2026-10-26, after both by-hand providers began


def write(path: Path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in lines))


def front(id_: str, t0: float = T, **more) -> dict:
    return {"v": 1, "id": id_, "t0": t0, "t1": t0 + 5, "key": "demo", "model": "spark-fast", "end": "done",
            "p": 10, "o": 4, **more}


def imp(mid: str, prov: str = "spark-flash-next", t0: float = T, sid: str = "a" * 16, **more) -> dict:
    row = {"v": 1, "src": "opencode", "mid": mid, "sid": sid, "prov": prov, "model": "m", "t0": t0, "t1": t0 + 3,
           "in": 10, "out": 5, "reas": 3, "cr": 90, "cw": 0, "end": "done"}
    row.update(more)
    return row


def test_front_rows_read_every_month_and_skip_bad_lines(tmp_path):
    write(tmp_path / "usage-2026-10.jsonl", [front("0" * 16, c=7, dropped=2), '{"v": 1, "id": "11', front("1" * 16, v=2)])
    write(tmp_path / "usage-2026-11.jsonl", [front("2" * 16), "not json", front("3" * 16, id="xyz")])
    rows, bad = store._front_rows(tmp_path)
    assert [r.id for r in rows] == ["0" * 16, "2" * 16] and bad == 4
    first, second = rows
    assert (first.source, first.key, first.c, first.dropped) == ("front", "demo", 7, 2)
    assert second.c is None and second.dropped == 0 and second.provider is None and second.session is None
    assert len(store.front_rows(tmp_path)) == 2


def test_imported_rows_last_line_wins(tmp_path):
    write(tmp_path / "opencode.jsonl", [imp("m1", out=5), imp("m1", out=50)])
    rows = store.imported_rows(BOOK, tmp_path)
    assert len(rows) == 1 and rows[0].o == 53


def test_imported_normalization(tmp_path):
    write(tmp_path / "opencode.jsonl", [imp("m1"), imp("m2", prov="spark-sglang")])
    flash, sglang = store.imported_rows(BOOK, tmp_path)
    assert (flash.p, flash.c, flash.o) == (100, 90, 8)
    assert (sglang.p, sglang.c, sglang.o) == (100, None, 8)
    assert flash.source == "opencode" and flash.key is None and flash.provider == "spark-flash-next"
    assert flash.session == "a" * 16 and flash.end == "done"


def row(session, t0, p, c=None):
    return store.Row("opencode", f"{t0:016x}", t0, t0 + 1, "m", p, c, 1, "done", None, "spark-sglang", session)


def test_estimate_follows_the_session():
    rows = [row("s1", 1, 1000), row("s1", 2, 1500), row("s1", 3, 400), row("s2", 1, 700)]
    out = store.with_estimates(rows)
    assert [r.c for r in out] == [0, 1000, 0, 0] and all(r.c_estimated for r in out)
    reported = [row("s1", 1, 1000, c=600), row("s1", 2, 1500)]
    first, second = store.with_estimates(reported)
    assert first.c == 600 and not first.c_estimated
    assert second.c == 1000 and second.c_estimated
    front_row = store.Row("front", "f" * 16, 1, 2, "m", 5, None, 1, "done", "demo", None, None)
    assert store.with_estimates([front_row]) == [front_row]


def test_counting_off_spans(tmp_path):
    write(tmp_path / "ledger-2026-11.jsonl", [
        {"v": 1, "kind": "counting_on", "t": 200.0, "who": "front", "seconds": 50.0},
        {"v": 1, "kind": "counting_off", "t": 100.0, "who": "gate"},
        {"v": 1, "kind": "counting_on", "t": 130.0, "who": "gate", "seconds": 30.0},
        {"v": 1, "kind": "counting_off", "t": 300.0, "who": "gate"},
        {"v": 1, "kind": "load", "t": 5.0},
    ])
    assert store.counting_off(tmp_path) == [(100.0, 130.0), (150.0, 200.0), (300.0, None)]


def test_a_provider_outside_its_window_is_left_out(tmp_path):
    early = 1_760_000_000.0  # 2025-10-09
    write(tmp_path / "opencode.jsonl", [imp("m1", prov="spark-sglang", t0=early), imp("m2", prov="spark-sglang")])
    rows = store.imported_rows(BOOK, tmp_path)
    assert [r.t0 for r in rows] == [T]


def test_missing_folders_read_as_empty(tmp_path):
    gone = tmp_path / "gone"
    assert store.front_rows(gone) == [] and store.imported_rows(BOOK, gone) == [] and store.counting_off(gone) == []


def test_an_unreadable_folder_raises(tmp_path):
    (tmp_path / "usage-2026-10.jsonl").mkdir()  # a directory where a file belongs
    with pytest.raises(OSError):
        store.front_rows(tmp_path)


def test_read_joins_all_three(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "USAGE_DIR", tmp_path / "u")
    monkeypatch.setattr(paths, "USAGE_SESSIONS_DIR", tmp_path / "s")
    monkeypatch.setattr(paths, "GATE_LEDGER_DIR", tmp_path / "g")
    write(tmp_path / "u" / "usage-2026-11.jsonl", [front("0" * 16), "torn"])
    write(tmp_path / "s" / "opencode.jsonl", [imp("m1", prov="spark-sglang"), imp("m1", prov="spark-sglang"),
                                              imp("m2", prov="spark-sglang", t0=T + 10)])
    (tmp_path / "s" / "opencode.imported").write_text("1793000100\n")
    write(tmp_path / "g" / "ledger-2026-11.jsonl", [{"v": 1, "kind": "counting_off", "t": 9.0, "who": "gate"}])
    result = store.read(BOOK)
    assert result.bad_lines == 1 and result.imported == 2 and result.last_import == 1793000100.0
    assert result.counting_off == [(9.0, None)] and len(result.rows) == 3
    assert [r.c for r in result.rows if r.source == "opencode"] == [0, 100]


def test_a_provider_that_is_not_by_hand_is_dropped_and_counted(tmp_path):
    write(tmp_path / "opencode.jsonl", [imp("m1", prov="spark"), imp("m2", prov="made-up-provider"),
                                        imp("m3", prov="spark-sglang")])
    rows, bad = store._imported_rows(BOOK, tmp_path)
    assert [r.id for r in rows] == ["m3"] and bad == 2
