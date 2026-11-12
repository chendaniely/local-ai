"""spark.meter.importer and `spark usage import`: the Mac's rows for the by-hand engines, checked and merged by
message. Every row here is invented."""

from __future__ import annotations

import io
import json
import os
import stat

import pytest

from spark import cli, paths
from spark.meter import importer, prices, store

NOW = 1_795_000_000.0  # 2026-11-18
T0 = 1_792_000_000.0  # 2026-10-14


def make(mid: str = "msg_a1", **more) -> dict:
    row = {"v": 1, "src": "opencode", "mid": mid, "sid": "0123456789abcdef", "prov": "spark-sglang",
           "model": "qwen3.8-27b", "t0": T0 + 1_000_000, "t1": T0 + 1_000_005, "in": 100, "out": 20, "reas": 5,
           "cr": 0, "cw": 0, "end": "done"}
    row.update(more)
    return row


def lines(*rows) -> list[str]:
    return [json.dumps(r) for r in rows]


def stored(folder) -> list[dict]:
    path = folder / "opencode.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_new_then_unchanged(tmp_path):
    batch = lines(make("m1"), make("m2"))
    assert importer.import_rows(batch, tmp_path, NOW).line() == "import: 2 new, 0 changed, 0 unchanged, 0 refused"
    assert importer.import_rows(batch, tmp_path, NOW).line() == "import: 0 new, 0 changed, 2 unchanged, 0 refused"
    assert len(stored(tmp_path)) == 2
    assert float((tmp_path / "opencode.imported").read_text()) == NOW


def test_changed_row_appends(tmp_path):
    importer.import_rows(lines(make("m1")), tmp_path, NOW)
    verdict = importer.import_rows(lines(make("m1", out=99)), tmp_path, NOW)
    assert (verdict.new, verdict.changed, verdict.unchanged) == (0, 1, 0)
    rows = store.imported_rows(prices.load(), tmp_path)
    assert [r.o for r in rows] == [99 + 5]


BAD = [
    ("field", lambda r: {**r, "extra": 1}),
    ("field", lambda r: {k: v for k, v in r.items() if k != "reas"}),
    ("text", lambda r: {**r, "mid": "a b"}),
    ("text", lambda r: {**r, "model": "x/y"}),
    ("text", lambda r: {**r, "sid": "0123456789abcde"}),
    ("count", lambda r: {**r, "in": -1}),
    ("count", lambda r: {**r, "out": True}),
    ("time", lambda r: {**r, "t1": r["t0"] - 1}),
    ("time", lambda r: {**r, "t0": 1_750_000_000.0, "t1": 1_750_000_001.0}),
    ("end", lambda r: {**r, "end": "stop"}),
    ("version", lambda r: {**r, "v": 2}),
    ("source", lambda r: {**r, "src": "pi"}),
]


@pytest.mark.parametrize("reason,mutate", BAD)
def test_each_refusal(tmp_path, reason, mutate):
    verdict = importer.import_rows(lines(mutate(make("m1"))), tmp_path, NOW)
    assert verdict.line() == f"import: 0 new, 0 changed, 0 unchanged, 1 refused ({reason} 1)"
    assert stored(tmp_path) == []


def test_torn_last_line(tmp_path):
    batch = lines(make("m1"), make("m2")) + [json.dumps(make("m3"))[:40]]
    verdict = importer.import_rows(batch, tmp_path, NOW)
    assert (verdict.new, verdict.refused) == (2, 1)
    assert len(stored(tmp_path)) == 2


def test_refusal_words_hold_no_value(tmp_path):
    verdict = importer.import_rows(lines(make("m1", model="secretproj/q3")), tmp_path, NOW)
    assert verdict.refused == 1 and "secretproj" not in verdict.line()


def test_oversize_line_refused(tmp_path):
    verdict = importer.import_rows(["x" * 5000], tmp_path, NOW)
    assert verdict.refused == 1


def test_file_mode(tmp_path):
    old = os.umask(0o022)
    try:
        importer.import_rows(lines(make("m1")), tmp_path, NOW)
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "opencode.jsonl").stat().st_mode) == 0o640


def test_import_verb(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "USAGE_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines(make("m1"))) + "\n"))
    assert cli.main(["usage", "import", "--source", "opencode"]) == 0
    assert "import: 1 new, 0 changed, 0 unchanged, 0 refused" in capsys.readouterr().out
