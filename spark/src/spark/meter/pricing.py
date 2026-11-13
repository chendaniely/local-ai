"""Pricing one row at its hosted proxy's rates (Claude, OpenAI or Voyage; the design's §3), when it is read: nothing
stored holds a dollar. A row that can't be priced says why and has no `usd`; it is never 0. At module level this
imports only the standard library and `spark.meter` modules."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from spark.meter.prices import PriceBook, line_at
from spark.meter.store import Row

WHYS = ("not_done", "no_counts", "unmapped", "none", "no_line", "cache_unknown", "no_audio_length")
_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Priced:
    usd: Decimal | None = None
    tier: str | None = None
    line: str | None = None  # the PriceLine's label
    band: str | None = None  # "base" or "above"
    why: str | None = None  # one of WHYS when unpriced
    by_audio: bool = False  # priced by the audio minute, on a per-minute line


def price(book: PriceBook, row: Row) -> Priced:
    """The row's price at the line in effect at its t0, or why it has none. WHYS names the reasons; they are checked
    in its order but for `none`, which comes before the counts: a model unpriced on purpose (mapped to `none`) is `none`, never `no_counts`, which the summary counts as
    unpriced (the final review's I1, 2026-11-12).
    An unmapped model with no counts stays `no_counts`, so it raises no ⚠map: a mapping line wouldn't price it."""
    if row.end != "done":
        return Priced(why="not_done")
    mapping = book.mapping.get(row.model) if row.model is not None else None
    if mapping is not None and mapping.tier == "none":
        return Priced(tier="none", why="none")
    line = line_at(book, mapping.tier, row.t0) if mapping is not None else None
    if line is not None and line.per_minute is not None:
        if row.audio_s is None:  # priced by the audio minute, and this row has no length
            return Priced(tier=mapping.tier, line=line.label, why="no_audio_length")
        usd = Decimal(str(row.audio_s)) / 60 * line.per_minute * mapping.factor
        return Priced(usd, mapping.tier, line.label, "base", by_audio=True)
    if row.p is None or row.o is None or (row.c is not None and row.c > row.p):  # a cache read can't exceed P
        return Priced(why="no_counts")
    if mapping is None:
        return Priced(why="unmapped")
    if line is None:
        return Priced(tier=mapping.tier, why="no_line")
    assert line.base is not None
    if row.c is None and line.base.cache_read == line.base.input:  # no cache count matters where the rates match
        row = dataclasses.replace(row, c=0)
    if row.c is None:
        return Priced(tier=mapping.tier, line=line.label, why="cache_unknown")
    f = mapping.factor
    above = line.above is not None and line.above_over is not None and row.p * f > line.above_over
    rates = line.above if above else line.base
    uncached = (row.p - row.c) * f
    uncached_rate = rates.write_5m if book.uncached_as_writes.get(mapping.tier, False) else rates.input
    usd = (uncached * uncached_rate + row.c * f * rates.cache_read + row.o * f * rates.output) / _MILLION
    return Priced(usd, mapping.tier, line.label, "above" if above else "base")


def row_json(row: Row, priced: Priced) -> dict[str, Any]:
    """The row's fields (`c_est` for c_estimated, `a_est` for audio_est, left out unless true; `audio_s` and
    `bytes_in` when it has them) and the four priced fields, `usd` a float rounded to 6 places; None values are left
    out."""
    out: dict[str, Any] = {}
    for f in dataclasses.fields(row):
        out[{"c_estimated": "c_est", "audio_est": "a_est"}.get(f.name, f.name)] = getattr(row, f.name)
    out.update(usd=None if priced.usd is None else round(float(priced.usd), 6), tier=priced.tier, line=priced.line,
               band=priced.band, why=priced.why)
    return {k: v for k, v in out.items() if v is not None and not (k == "a_est" and v is False)}
