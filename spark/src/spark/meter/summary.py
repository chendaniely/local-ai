"""`spark usage summary` (website/design/usage-meter-now.md, *The summary's JSON*): the spend at Claude's prices today,
this month, last month and all time, by model, with what couldn't be priced and what isn't counted. spark-menu prints
this JSON and adds no two numbers, so every figure it shows is made here.

Dollars are summed as the Decimals `pricing.price` gives and rounded only as the JSON is built, so rounding never
compounds. Days and months are in Dan's zone: the private file's, else the system's. At module level this imports only
the standard library, `yaml`, `spark.paths` and `spark.meter` modules."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from spark import paths
from spark.meter.prices import PriceBook
from spark.meter.pricing import Priced, price
from spark.meter.store import Row, Store

SUMMARY_VERSION = 1
# The whys that leave a row unpriced by a gap someone can close. not_done rows are counted in the flags instead, and
# `none` is unpriced on purpose.
UNPRICED = ("no_counts", "unmapped", "no_line", "cache_unknown")
NOT_COUNTED = ("by-hand engines: counted from heartsbane's OpenCode only",)
NO_MODEL = "(no model)"  # a row with none, in by_model and flags.unmapped alike, where null would print as null
LOCALTIME = Path("/etc/localtime")
_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class Private:
    """The private file (the design's part 2): the Spark's all-in price in US dollars and Dan's zone, each None when
    the file doesn't give it."""
    price_usd: Decimal | None = None
    zone: str | None = None


def _zone_info(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):  # unknown, a path-like name, or a folder of zones
        return None


def _price_usd(path: Path, value: Any) -> Decimal | None:
    if value is None:
        return None
    number = None
    if not isinstance(value, bool) and isinstance(value, (int, float, str)):
        try:
            number = Decimal(str(value))  # str, so a YAML float carries no binary error
        except InvalidOperation:
            pass
    if number is None or not number.is_finite() or number <= 0:
        raise ValueError(f"{path}: price_usd should be a number of US dollars above zero")
    return number


def _zone_name(path: Path, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _zone_info(value) is None:
        raise ValueError(f"{path}: zone should be a time zone name zoneinfo knows, like Europe/Paris")
    return value


def read_private(path: Path | None = None) -> Private:
    """The private file at `path` (paths.METER_PRIVATE, read when called); absent, or a field left out, gives None.

    It holds Dan's real purchase price, so a file that can't be used raises a ValueError naming the file and the field,
    never a value, and suppresses the error it came from: a YAML error's text quotes the line it failed on."""
    path = paths.METER_PRIVATE if path is None else path
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Private()
    except OSError as err:
        raise ValueError(f"{path}: can't be read ({err.strerror or type(err).__name__})") from None
    except UnicodeDecodeError:
        raise ValueError(f"{path}: isn't UTF-8 text") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        mark = getattr(err, "problem_mark", None)
        raise ValueError(f"{path}: isn't valid YAML{'' if mark is None else f' (line {mark.line + 1})'}") from None
    data = {} if data is None else data
    if not isinstance(data, dict):
        raise ValueError(f"{path}: should hold a mapping, with price_usd and zone")
    return Private(_price_usd(path, data.get("price_usd")), _zone_name(path, data.get("zone")))


def zone_of(private: Private, localtime: Path | None = None) -> tuple[dt.tzinfo | None, str, str]:
    """The zone days and months are counted in, its name, and "private_file" or "system". The system's is the name
    after `zoneinfo/` in `localtime`'s link target (LOCALTIME, read when called), as macOS and Ubuntu link it.

    Failing that it is None, named "localtime": the system's own rules, applied instant by instant, as
    `datetime.fromtimestamp(t)` applies them. One offset taken now would shift every row from the other side of a
    DST change by an hour, and a row near midnight onto the wrong day."""
    if private.zone is not None:
        return ZoneInfo(private.zone), private.zone, "private_file"
    try:
        target = os.readlink(LOCALTIME if localtime is None else localtime)
    except OSError:  # not a link, or not there
        target = ""
    _, found, name = target.partition("zoneinfo/")
    zone = _zone_info(name) if found and name else None
    if zone is not None:
        return zone, name, "system"
    return None, "localtime", "system"


def _money(amount: Decimal) -> float:
    return float(amount.quantize(_PLACES))


@dataclass
class _Sum:
    """One total: the priced dollars (with the part resting on an estimated cache), every row, and the tokens of the
    rows that have them."""
    usd: Decimal = Decimal(0)
    usd_estimated: Decimal = Decimal(0)
    requests: int = 0
    p: int = 0
    c: int = 0
    o: int = 0
    unpriced: int = 0

    def add(self, row: Row, priced: Priced) -> None:
        self.requests += 1
        self.p += row.p or 0
        self.c += row.c or 0
        self.o += row.o or 0
        if priced.usd is not None:
            self.usd += priced.usd
            if row.c_estimated:
                self.usd_estimated += priced.usd
        if priced.why in UNPRICED:
            self.unpriced += 1

    def figures(self) -> dict[str, Any]:
        return {"usd": _money(self.usd), "usd_estimated": _money(self.usd_estimated), "requests": self.requests,
                "p": self.p, "c": self.c, "o": self.o}


Item = tuple[Row, Priced]


def _periods(items: list[Item], tz: dt.tzinfo | None, today: dt.date,
             private: Private) -> tuple[dict[str, Any], list[Item]]:
    """The four periods, each row placed by its t0's date in the zone (None: the system's rules at that instant), and
    this month's rows for the flags."""
    last = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    sums = {name: _Sum() for name in ("today", "this_month", "last_month", "all_time")}
    month: list[Item] = []
    for row, priced in items:
        day = dt.datetime.fromtimestamp(row.t0, tz).date()
        sums["all_time"].add(row, priced)
        if day == today:
            sums["today"].add(row, priced)
        if (day.year, day.month) == (today.year, today.month):
            sums["this_month"].add(row, priced)
            month.append((row, priced))
        elif (day.year, day.month) == last:
            sums["last_month"].add(row, priced)
    labels = {"today": today.isoformat(), "this_month": f"{today:%Y-%m}", "last_month": f"{last[0]:04d}-{last[1]:02d}",
              "all_time": "all"}
    out = {name: {"label": labels[name], **s.figures(), "unpriced": s.unpriced} for name, s in sums.items()}
    total = sums["all_time"].usd
    out["all_time"]["share_of_price"] = None if private.price_usd is None else _money(total / private.price_usd)
    return out, month


def _by_model(book: PriceBook, month: list[Item], items: list[Item]) -> list[dict[str, Any]]:
    """Each model's total this month, then all time, each the dearest first; rows with no model as NO_MODEL."""
    out = []
    for period, rows in (("this_month", month), ("all_time", items)):
        sums: dict[str | None, _Sum] = {}
        for row, priced in rows:
            sums.setdefault(row.model, _Sum()).add(row, priced)
        for model, s in sorted(sums.items(), key=lambda kv: (-kv[1].usd, kv[0] or NO_MODEL)):
            mapping = book.mapping.get(model) if model is not None else None
            out.append({"model": NO_MODEL if model is None else model,
                        "tier": None if mapping is None else mapping.tier, "period": period, **s.figures()})
    return out


def _flags(month: list[Item], store: Store) -> dict[str, Any]:
    """What this month's rows need looked at. A model is flagged unmapped only on rows that would otherwise have been
    priced, so a refused request naming a model that doesn't exist raises no flag."""
    unmapped: dict[str, int] = {}
    for row, priced in month:
        if priced.why == "unmapped":
            name = NO_MODEL if row.model is None else row.model
            unmapped[name] = unmapped.get(name, 0) + 1
    return {"unmapped": dict(sorted(unmapped.items())),
            "interrupted": sum(row.end in ("cut", "gone") for row, _ in month),
            "errors": sum(row.end == "error" for row, _ in month),
            "cache_unknown": sum(priced.why == "cache_unknown" for _, priced in month),
            "dropped": sum(row.dropped for row, _ in month if row.source == "front"),
            "bad_lines": store.bad_lines}


def _coverage(store: Store, month_start: float) -> dict[str, Any]:
    """What the totals leave out: counting's gaps that end this month or are still open, and how far the import got."""
    return {"counting_off": [[start, end] for start, end in store.counting_off if end is None or end >= month_start],
            "imported": {"opencode": {"last_import": store.last_import, "rows": store.imported}},
            "not_counted": list(NOT_COUNTED)}


def _hours(items: list[Item]) -> list[dict[str, Any]]:
    """Totals per UTC hour, source, key, provider and model, in that order, for the dashboard later."""
    sums: dict[tuple[int, str, str | None, str | None, str | None], _Sum] = {}
    for row, priced in items:
        group = (int(row.t0 // 3600) * 3600, row.source, row.key, row.provider, row.model)
        sums.setdefault(group, _Sum()).add(row, priced)
    out = []
    for group, s in sorted(sums.items(), key=lambda kv: tuple("" if v is None else v for v in kv[0])):
        hour, source, key, provider, model = group
        out.append({"hour": hour, "source": source, "key": key, "provider": provider, "model": model,
                    "usd": _money(s.usd), "requests": s.requests, "p": s.p, "c": s.c, "o": s.o})
    return out


def summarize(store: Store, book: PriceBook, private: Private, now: float, *, hours: bool = False) -> dict[str, Any]:
    """The summary's JSON, the contract spark-menu reads (version SUMMARY_VERSION), as of `now`."""
    tz, zone, zone_from = zone_of(private)
    today = dt.datetime.fromtimestamp(now, tz).date()
    items = [(row, price(book, row)) for row in store.rows]
    periods, month = _periods(items, tz, today, private)
    month_start = dt.datetime(today.year, today.month, 1, tzinfo=tz).timestamp()  # naive, with None: the system's
    out: dict[str, Any] = {
        "v": SUMMARY_VERSION, "made_at": now, "zone": zone, "zone_from": zone_from,
        "counter_start": min((row.t0 for row in store.rows if row.source == "front"), default=None),
        "last_row_at": max((row.t0 for row in store.rows), default=None),
        "periods": periods, "by_model": _by_model(book, month, items), "flags": _flags(month, store),
        "coverage": _coverage(store, month_start), "electricity": None, "break_even": None}
    if hours:
        out["hours"] = _hours(items)
    return out


def _tokens(n: int) -> str:
    for size, unit in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def words(summary: dict[str, Any]) -> str:
    """The summary in plain words, as `spark usage summary` prints it without --json: a line a period."""
    lines = [f"At Claude's prices; days and months in {summary['zone']}"]
    for name, key in (("This month", "this_month"), ("Today", "today"), ("Last month", "last_month"),
                      ("All time", "all_time")):
        period = summary["periods"][key]
        label = "" if key == "all_time" else period["label"]
        dollars = f"${period['usd']:,.2f}"
        line = f"{name:<10}  {label:<10}  {dollars:>11}  {_tokens(period['p'])} in · {_tokens(period['o'])} out"
        if period["usd_estimated"]:
            line += f" · ~${period['usd_estimated']:,.2f} of it on an estimated cache"
        if period["unpriced"]:
            line += f" · {period['unpriced']} unpriced"
        if key == "all_time":
            share = "—" if period["share_of_price"] is None else f"{period['share_of_price'] * 100:.1f} %"
            line += f" · {share} of the Spark's price (before electricity)"
        lines.append(line)
    return "\n".join(lines)
