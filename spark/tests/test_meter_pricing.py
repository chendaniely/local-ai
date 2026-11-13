"""spark.meter.pricing and `spark usage rows`: a row priced at Claude's rates when read. Every row is invented."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from spark import cli, paths
from spark.meter import pricing, prices
from spark.meter.prices import Mapping
from spark.meter.store import Row

BASE = prices.load()


def at(text: str) -> float:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def book(**models):
    mapping = dict(BASE.mapping)
    mapping.update({name: Mapping(tier, Decimal(str(f))) for name, (tier, f) in models.items()})
    return dataclasses.replace(BASE, mapping=mapping)


# The default row's model pinned at Opus, so Dan's remaps of the shipped mapping never move these sums.
BOOK = book(**{"qwen3.8-27b": ("opus", 1)}, son=("sonnet", 1), hai=("haiku", 1), hai2=("haiku", 2), half=("opus", 0.5),
            nil=("none", 1))


def row(model="qwen3.8-27b", t="2026-11-01T00:00:00Z", p=1_000_000, c=900_000, o=10_000, end="done") -> Row:
    return Row("front", "0" * 16, at(t), None, model, p, c, o, end)


def test_opus_row():
    priced = pricing.price(BOOK, row())
    assert priced.usd == Decimal("0.78") and priced.line == "opus@2026-09-22"
    assert (priced.tier, priced.band, priced.why) == ("opus", "base", None)


def test_sonnet_cache_read_changes_on_its_day():
    before = pricing.price(BOOK, row("son", "2026-10-06T23:59:59Z")).usd
    after = pricing.price(BOOK, row("son", "2026-10-07T00:00:00Z")).usd
    assert before - after == Decimal("0.09")


def test_haiku_band_on_the_whole_prompt():
    low = pricing.price(BOOK, row("hai", p=100_000, c=0, o=0))
    high = pricing.price(BOOK, row("hai", p=100_001, c=0, o=0))
    assert low.band == "base" and high.band == "above"
    assert low.usd == Decimal("0.01") and high.usd == Decimal("100001") * Decimal("0.5") / 1_000_000
    assert pricing.price(BOOK, row("hai2", p=60_000, c=0, o=0)).band == "above"


def test_haiku_4_5_before_10_07():
    priced = pricing.price(BOOK, row("hai", "2026-10-01T00:00:00Z", p=1_000_000, c=0, o=0))
    assert priced.usd == Decimal("1") and priced.line == "haiku@2026-09-28"


def test_factor_scales_every_kind():
    assert pricing.price(BOOK, row("half")).usd == Decimal("0.39")


def test_uncached_as_writes():
    on = dataclasses.replace(BOOK, uncached_as_writes={**BOOK.uncached_as_writes, "opus": True})
    assert pricing.price(on, row()).usd == Decimal("0.1") * 5 + Decimal("0.18") + Decimal("0.2")


def test_why_each():
    cases = [(row(end="error"), "not_done"), (row(end="cut"), "not_done"), (row(end="refused"), "not_done"),
             (row(o=None), "no_counts"), (row(p=None), "no_counts"), (row("nope"), "unmapped"),
             (row("nil"), "none"), (row(t="2026-09-01T00:00:00Z"), "no_line"), (row(c=None), "cache_unknown")]
    for r, why in cases:
        priced = pricing.price(BOOK, r)
        assert priced.why == why and priced.usd is None and why in pricing.WHYS


def test_a_none_row_with_no_counts_is_none():
    # A transcription row has no token counts; `none` means unpriced on purpose, so it must never read as no_counts,
    # which the summary counts as unpriced (the final review's I1).
    for r in (row("nil", p=None, c=None, o=None), row("nil", o=None), row("nil", p=100, c=101, o=1),
              row("nil", p=None, c=None, o=None)):
        priced = pricing.price(BOOK, r)
        assert (priced.why, priced.tier, priced.usd) == ("none", "none", None)
    assert pricing.price(BOOK, row("nil", end="error")).why == "not_done"  # not_done still comes first
    assert pricing.price(BOOK, row("nope", p=None)).why == "no_counts"  # an unmapped row with no counts: no ⚠map


def test_row_json_leaves_out_none_and_rounds():
    r = dataclasses.replace(row(), key="demo", c_estimated=True)
    out = pricing.row_json(r, pricing.price(BOOK, r))
    assert out["usd"] == 0.78 and isinstance(out["usd"], float) and out["c_est"] is True
    assert "provider" not in out and "why" not in out and out["line"] == "opus@2026-09-22"
    bad = pricing.row_json(row(end="error"), pricing.price(BOOK, row(end="error")))
    assert "usd" not in bad and bad["why"] == "not_done"


def test_rows_verb_prints_json_lines(tmp_path, monkeypatch, capsys):
    usage, sessions, ledger = tmp_path / "u", tmp_path / "s", tmp_path / "l"
    usage.mkdir()
    lines = [{"v": 1, "id": f"{i:016x}", "t0": at(t), "t1": at(t) + 5, "key": "demo", "model": "qwen3.8-27b",
              "end": "done", "p": 1000, "c": 500, "o": 10}
             for i, t in enumerate(["2026-11-03T00:00:00Z", "2026-10-30T00:00:00Z", "2026-11-02T00:00:00Z"])]
    (usage / "usage-2026-11.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    monkeypatch.setattr(paths, "USAGE_DIR", usage)
    monkeypatch.setattr(paths, "USAGE_SESSIONS_DIR", sessions)
    monkeypatch.setattr(paths, "GATE_LEDGER_DIR", ledger)
    assert cli.main(["usage", "rows"]) == 0
    out = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [o["t0"] for o in out] == sorted(o["t0"] for o in out) and len(out) == 3
    allowed = {f.name for f in dataclasses.fields(Row)} - {"c_estimated"} | {"c_est", "usd", "tier", "line", "band",
                                                                                "why"}
    assert all(set(o) <= allowed for o in out) and all("usd" in o for o in out)
    assert cli.main(["usage", "rows", "--since", "2026-11-01"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 2
    assert cli.main(["usage", "rows", "--since", str(int(at("2026-11-03T00:00:00Z")))]) == 0
    assert len(capsys.readouterr().out.splitlines()) == 1
    with pytest.raises(SystemExit):
        cli.main(["usage", "rows", "--since", "yesterday"])


def test_cache_over_prompt_is_no_counts():
    priced = pricing.price(BOOK, row(p=100, c=101, o=1))
    assert priced.usd is None and priced.why == "no_counts"
    assert pricing.price(BOOK, row(p=100, c=100, o=1)).usd is not None


def test_since_parsing():
    from spark.meter import _since
    import argparse
    assert _since("20261101") == _since("2026-11-01") == at("2026-11-01T00:00:00Z")
    assert _since("1793000000") == 1793000000.0
    for bad in ("nan", "inf", "-inf", "yesterday"):
        with pytest.raises(argparse.ArgumentTypeError):
            _since(bad)


def test_embeddings_and_asr_rows_price_without_a_cache_count():
    emb = pricing.price(BASE, row("qwen3-embedding-0.6b", p=1_000_000, c=None, o=0))
    assert emb.usd == Decimal("0.02") and emb.why is None and emb.tier == "voyage-4-lite"
    asr = pricing.price(BASE, row("qwen3-asr-1.7b-32k", p=1_000_000, c=0, o=1_000_000))
    assert asr.usd == Decimal("6.25")
    asr_no_cache = pricing.price(BASE, row("qwen3-asr-1.7b-32k", p=1_000_000, c=None, o=1_000_000))
    assert asr_no_cache.usd == Decimal("6.25")


def test_a_per_minute_row_waits_for_its_length():
    for r in (row("whisper-large-v3", p=None, c=None, o=None), row("whisper-large-v3-turbo")):
        priced = pricing.price(BASE, r)
        assert (priced.usd, priced.tier, priced.line, priced.why) == (None, "whisper-1", "whisper-1@2026-09-01",
                                                                      "no_audio_length")
    assert "no_audio_length" in pricing.WHYS and pricing.WHYS[-1] == "no_audio_length"
    assert pricing.price(BASE, row("whisper-large-v3", end="error")).why == "not_done"
