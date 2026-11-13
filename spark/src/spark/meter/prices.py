"""The meter's price files (the design's part 1): which proxy each model is priced as (mapping.yaml), the dated
price lines of each proxy (prices.yaml) and the providers that reached brightroar (providers.yaml).

Nothing stored holds a dollar: prices are applied when a summary is read, so a remap or a corrected line reprices the
past. Every refusal is a PriceError naming the file and the reason, which `cli.main` prints. At module level this
imports only the standard library, `yaml` and `spark.paths`, and the front never imports it."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from spark import paths

TIERS = ("opus", "sonnet", "haiku", "fable")
PROXY_NAME = re.compile(r"[a-z0-9][a-z0-9.-]*")
RATE_FIELDS = ("input", "output", "cache_read", "write_5m", "write_1h")


class PriceError(ValueError):
    """A price file that can't be used: its text is the file and the reason."""


@dataclass(frozen=True)
class Rates:
    """US dollars per million tokens."""
    input: Decimal
    output: Decimal
    cache_read: Decimal
    write_5m: Decimal
    write_1h: Decimal


@dataclass(frozen=True)
class PriceLine:
    tier: str
    start: float  # UTC epoch of 00:00 on its `from` day
    name: str
    base: Rates | None  # None for a per-minute line
    above: Rates | None
    above_over: int | None
    day: str = ""  # its `from` day, ISO
    per_minute: Decimal | None = None  # US dollars per audio minute; set only on a per-minute line

    @property
    def label(self) -> str:
        return f"{self.tier}@{self.day}"


@dataclass(frozen=True)
class Mapping:
    tier: str  # a key of prices.yaml's tiers, or "none"
    factor: Decimal


@dataclass(frozen=True)
class Provider:
    harness: str
    provider: str
    front: bool
    start: float | None
    end: float | None
    cache_from: float | None  # None: its engine doesn't report a cache yet


@dataclass(frozen=True)
class PriceBook:
    mapping: dict[str, Mapping]
    lines: dict[str, list[PriceLine]]  # each tier's, sorted by start
    uncached_as_writes: dict[str, bool]
    providers: list[Provider]


def _read(folder: Path, name: str) -> dict[str, Any]:
    path = folder / name
    try:
        data = yaml.safe_load(path.read_text())
    except OSError as err:
        raise PriceError(f"{path}: can't be read ({err.strerror or err})") from err
    except yaml.YAMLError as err:
        raise PriceError(f"{path}: isn't valid YAML ({err})") from err
    if not isinstance(data, dict):
        raise PriceError(f"{name}: should hold a mapping")
    if data.get("v") != 1 or isinstance(data.get("v"), bool):
        raise PriceError(f"{name}: v should be 1, not {data.get('v')!r}")
    return data


def _mapping_of(where: str, value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PriceError(f"{where}: {what} should be a mapping")
    return value


def _decimal(where: str, field: str, value: Any, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise PriceError(f"{where}: {field} should be a number, not {value!r}")
    try:
        number = Decimal(str(value))  # str, so a YAML float carries no binary error into a price
    except InvalidOperation:
        raise PriceError(f"{where}: {field} should be a number, not {value!r}") from None
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise PriceError(f"{where}: {field} should be {'above' if positive else 'at least'} zero, not {value!r}")
    return number


def _day(where: str, field: str, value: Any) -> tuple[float, str]:
    """A YAML date or an ISO date string, as the UTC epoch of 00:00 on that day, and the day."""
    if isinstance(value, dt.datetime):
        raise PriceError(f"{where}: {field} should be a day, not a time")
    if isinstance(value, str):
        try:
            value = dt.date.fromisoformat(value)
        except ValueError:
            raise PriceError(f"{where}: {field} should be a day like 2026-10-07, not {value!r}") from None
    if not isinstance(value, dt.date):
        raise PriceError(f"{where}: {field} should be a day like 2026-10-07, not {value!r}")
    start = dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
    return start.timestamp(), value.isoformat()


def _rates(where: str, raw: dict[str, Any]) -> Rates:
    values = {}
    for field in RATE_FIELDS:
        if field not in raw:
            raise PriceError(f"{where}: {field} is missing")
        values[field] = _decimal(where, field, raw[field])
    return Rates(**values)


def _line(file: str, tier: str, index: int, raw: Any) -> PriceLine:
    where = f"{file}: {tier} line {index + 1}"
    raw = _mapping_of(where, raw, "a line")
    if "from" not in raw:
        raise PriceError(f"{where}: from is missing")
    start, day = _day(where, "from", raw["from"])
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise PriceError(f"{where}: name should be a string")
    if "per_minute" in raw:
        extra = sorted(set(raw) - {"from", "name", "per_minute"})
        if extra:
            raise PriceError(f"{where}: a per-minute line holds only from, name and per_minute, not {extra[0]}")
        rate = _decimal(where, "per_minute", raw["per_minute"], positive=True)
        return PriceLine(tier, start, name, None, None, None, day, rate)
    base = _rates(where, raw)
    above = above_over = None
    if "above" in raw:
        extra = _mapping_of(where, raw["above"], "above")
        over = extra.get("over")
        if isinstance(over, bool) or not isinstance(over, int) or over <= 0:
            raise PriceError(f"{where}: above needs over, a whole number of tokens above zero")
        above, above_over = _rates(f"{where}, above", extra), over
    return PriceLine(tier, start, name, base, above, above_over, day)


def _prices(folder: Path) -> dict[str, list[PriceLine]]:
    data = _read(folder, "prices.yaml")
    tiers = _mapping_of("prices.yaml", data.get("tiers"), "tiers")
    lines: dict[str, list[PriceLine]] = {}
    for tier, raw_lines in tiers.items():
        if not isinstance(tier, str) or not PROXY_NAME.fullmatch(tier):
            raise PriceError(f"prices.yaml: {tier!r} isn't a proxy name (lowercase letters, digits, . and -)")
        if not isinstance(raw_lines, list) or not raw_lines:
            raise PriceError(f"prices.yaml: {tier} should be a list of lines")
        parsed = [_line("prices.yaml", tier, i, raw) for i, raw in enumerate(raw_lines)]
        for before, after in zip(parsed, parsed[1:]):
            if after.start == before.start:
                raise PriceError(f"prices.yaml: {tier} has two lines on {after.day}")
            if after.start < before.start:
                raise PriceError(f"prices.yaml: {tier}'s lines should be in date order; {after.day} comes after "
                                 f"{before.day}")
        lines[tier] = parsed
    return lines


def _mappings(folder: Path) -> tuple[dict[str, Mapping], dict[str, bool]]:
    data = _read(folder, "mapping.yaml")
    switches: dict[str, bool] = {}
    for tier, raw in _mapping_of("mapping.yaml", data.get("tiers"), "tiers").items():
        if tier not in TIERS:
            raise PriceError(f"mapping.yaml: {tier!r} isn't a tier (one of {', '.join(TIERS)})")
        value = _mapping_of(f"mapping.yaml: tier {tier}", raw, "its entry").get("uncached_as_writes", False)
        if not isinstance(value, bool):
            raise PriceError(f"mapping.yaml: {tier}'s uncached_as_writes should be true or false")
        switches[tier] = value
    models: dict[str, Mapping] = {}
    for model, raw in _mapping_of("mapping.yaml", data.get("models"), "models").items():
        where = f"mapping.yaml: model {model}"
        raw = _mapping_of(where, raw, "its entry")
        tier = raw.get("tier")
        if tier != "none" and not isinstance(tier, str):
            raise PriceError(f"{where}: tier should be a key of prices.yaml or none, not {tier!r}")
        factor = _decimal(where, "factor", raw.get("factor", 1), positive=True)
        models[str(model)] = Mapping(tier, factor)
    return models, switches


def _providers(folder: Path) -> list[Provider]:
    data = _read(folder, "providers.yaml")
    raw_list = data.get("providers")
    if not isinstance(raw_list, list):
        raise PriceError("providers.yaml: providers should be a list")
    found = []
    for i, raw in enumerate(raw_list):
        where = f"providers.yaml: provider {i + 1}"
        raw = _mapping_of(where, raw, "its entry")
        harness, provider, front = raw.get("harness"), raw.get("provider"), raw.get("front")
        if not isinstance(harness, str) or not isinstance(provider, str) or not isinstance(front, bool):
            raise PriceError(f"{where}: needs a harness, a provider and front: true or false")
        start = end = cache_from = None
        if "from" in raw:
            start = _day(where, "from", raw["from"])[0]
        if "to" in raw:
            end = _day(where, "to", raw["to"])[0]
        if raw.get("cache_from") is not None:
            cache_from = _day(where, "cache_from", raw["cache_from"])[0]
        if not front and start is None:
            raise PriceError(f"{where}: {provider} has front: false and needs a from day")
        found.append(Provider(harness, provider, front, start, end, cache_from))
    return found


def load(folder: Path | None = None) -> PriceBook:
    """The three files of `folder` (paths.METER_DIR, read when called), or a PriceError naming the one at fault."""
    folder = paths.METER_DIR if folder is None else folder
    models, switches = _mappings(folder)
    lines = _prices(folder)
    for model, mapped in models.items():
        if mapped.tier != "none" and mapped.tier not in lines:
            raise PriceError(f"mapping.yaml: model {model}: tier {mapped.tier!r} is neither none nor a key of "
                             f"prices.yaml ({', '.join(lines)})")
    return PriceBook(models, lines, switches, _providers(folder))


def line_at(book: PriceBook, tier: str, t: float) -> PriceLine | None:
    """The last line of the tier with start <= t; None before the first."""
    found = None
    for line in book.lines.get(tier, []):
        if line.start <= t:
            found = line
    return found


def cache_reported(book: PriceBook, provider: str, t: float) -> bool:
    """Whether a by-hand provider's engine reported a cache at t. Any other provider is False: the front's rows
    carry their own cached count."""
    return any(not p.front and p.provider == provider and p.cache_from is not None and p.cache_from <= t
               for p in book.providers)


def by_hand(book: PriceBook, harness: str) -> list[Provider]:
    """The harness's providers that bypassed the front (front: false)."""
    return [p for p in book.providers if p.harness == harness and not p.front]


TABLE_HEAD = ("| Model | Priced as | Since | Fresh input | Cached input | Output |", "|---|---|---|---|---|---|")


def table(book: PriceBook) -> str:
    """Every model of the mapping, in its order, with the proxy it's priced as: its tier's newest line, in US dollars per
    million tokens. A long prompt's rates follow `;`, since a request whose prompt is over that many tokens is priced at
    them throughout. website/reference/model-prices.md holds this table, and a test keeps the two the same."""
    rows = list(TABLE_HEAD)
    for model, mapped in book.mapping.items():
        if mapped.tier == "none":
            rows.append(f"| `{model}` | not priced yet | — | — | — | — |")
            continue
        if not book.lines.get(mapped.tier):
            rows.append(f"| `{model}` | {mapped.tier}, which has no price line | — | — | — | — |")
            continue
        line = book.lines[mapped.tier][-1]
        factor = "" if mapped.factor == 1 else f" × {mapped.factor}"
        if line.per_minute is not None:
            cells = f"${line.per_minute} per audio minute | — | —"
        else:
            cells = " | ".join(_rate(line, field) for field in ("input", "cache_read", "output"))
        rows.append(f"| `{model}` | {line.name}{factor} | {line.day} | {cells} |")
    return "\n".join(rows)


def _rate(line: PriceLine, field: str) -> str:
    assert line.base is not None
    text = f"${getattr(line.base, field):.2f}"
    if line.above is not None and line.above_over is not None:
        text += f"; ${getattr(line.above, field):.2f} above {line.above_over:,}"
    return text
