import shutil
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from spark import apply as spark_apply
from spark import paths
from spark.apply import RestartPlan, plan_restarts
from spark.front import FRONT_MODULES
from spark.meter import prices
from spark.meter.prices import PriceError, by_hand, cache_reported, line_at, load, table
from spark.registry import load_registry

SHIPPED = Path(prices.__file__).parent
REPO = Path(__file__).resolve().parents[2]


def epoch(day: str, hms: str = "00:00:00") -> float:
    return datetime.fromisoformat(f"{day}T{hms}+00:00").timestamp()


def test_the_shipped_files_load():
    book = load()
    assert len(book.lines["sonnet"]) == 2
    second = book.lines["sonnet"][1]
    assert second.start == epoch("2026-10-07")
    assert second.base.cache_read == Decimal("0.10")
    assert second.label == "sonnet@2026-10-07"
    assert book.mapping["qwen3.8-27b"].tier == "sonnet" and book.mapping["qwen3.8-27b"].factor == 1
    assert book.mapping["whisper-large-v3"].tier == "none"
    assert book.lines["haiku"][1].above_over == 100000
    assert book.lines["haiku"][1].above.input == Decimal("0.50")
    assert book.lines["opus"][0].above is None and book.lines["opus"][0].above_over is None
    assert book.uncached_as_writes == {t: False for t in prices.TIERS}


def test_the_default_folder_is_read_when_called(tmp_path, monkeypatch):
    shutil.copytree(SHIPPED, tmp_path / "m", ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    monkeypatch.setattr(paths, "METER_DIR", tmp_path / "m")
    (tmp_path / "m" / "prices.yaml").write_text("v: 2\ntiers: {}\n")
    with pytest.raises(PriceError, match="prices.yaml"):
        load()


# Dan's decision, 2026-11-12 (website/design/usage-meter-qa.md): the chat models priced by size.
BY_SIZE = {"qwen3.8-27b": "sonnet", "qwen3.8-27b-sglang": "sonnet", "qwen3.8-flash-next": "sonnet",
           "qwen3.8-flash-next-mtp": "sonnet", "qwen3.6-35b-a3b": "haiku", "gemma-4-26b-a4b": "haiku"}
DOCS = REPO / "website" / "reference" / "model-prices.md"
TABLE_START, TABLE_END = "<!-- the table: spark.meter.prices.table -->", "<!-- end of the table -->"


def test_chat_models_priced_by_size():
    book = load()
    assert {name: book.mapping[name].tier for name in BY_SIZE} == BY_SIZE


def test_the_sites_table_is_the_price_files():
    text = DOCS.read_text()
    assert text.count(TABLE_START) == 1 and text.count(TABLE_END) == 1
    block = text.split(TABLE_START, 1)[1].split(TABLE_END, 1)[0].strip("\n")
    assert block == table(load())


def test_the_table_has_every_model_once_with_its_newest_line():
    book = load()
    rows = table(book).splitlines()
    assert rows[0] == "| Model | Priced as | Since | Fresh input | Cached input | Output |"
    assert [r.split("|")[1].strip().strip("`") for r in rows[2:]] == list(book.mapping)
    by_model = {r.split("|")[1].strip().strip("`"): r for r in rows[2:]}
    assert by_model["qwen3.8-27b"] == "| `qwen3.8-27b` | Sonnet 5.5 | 2026-10-07 | $2.00 | $0.10 | $10.00 |"
    assert by_model["qwen3.6-35b-a3b"] == ("| `qwen3.6-35b-a3b` | Haiku 5.5 | 2026-10-07 | $0.10; $0.50 above 100,000 "
                                           "| $0.01; $0.05 above 100,000 | $0.50; $2.50 above 100,000 |")
    assert by_model["whisper-large-v3"] == "| `whisper-large-v3` | not priced yet | — | — | — | — |"


def test_every_registry_model_has_a_line():
    registry = load_registry(REPO / "stack" / "models.yaml")
    book = load()
    missing = [name for name in registry.models if name not in book.mapping]
    assert missing == []


def test_line_at_picks_the_line_in_effect():
    book = load()
    change = epoch("2026-10-07")
    assert line_at(book, "sonnet", change - 1).base.cache_read == Decimal("0.20")
    assert line_at(book, "sonnet", change).base.cache_read == Decimal("0.10")
    assert line_at(book, "opus", epoch("2026-09-21", "23:59:59")) is None
    assert line_at(book, "opus", epoch("2026-09-22")).name == "Opus 5.5"


def test_cache_reported():
    book = load()
    for t in (0.0, epoch("2026-10-15"), epoch("2027-01-01")):
        assert cache_reported(book, "spark-sglang", t) is False
    assert cache_reported(book, "spark-flash-next", epoch("2026-10-14", "23:59:59")) is False
    assert cache_reported(book, "spark-flash-next", epoch("2026-10-15")) is True
    assert cache_reported(book, "spark-flash-next", epoch("2026-12-01")) is True
    assert cache_reported(book, "spark", epoch("2026-12-01")) is False
    assert [p.provider for p in by_hand(book, "opencode")] == ["spark-sglang", "spark-flash-next"]
    assert by_hand(book, "pi") == []
    assert by_hand(book, "opencode")[0].start == epoch("2026-10-15")


def edit(name, fn):
    def apply(folder):
        path = folder / name
        data = yaml.safe_load(path.read_text())
        fn(data)
        path.write_text(yaml.safe_dump(data))
    return apply


def opus(data):
    return data["tiers"]["opus"]


FAULTS = {
    "unknown tier in prices": ("prices.yaml", lambda d: d["tiers"].update(gold=d["tiers"]["opus"])),
    "unknown tier in mapping": ("mapping.yaml", lambda d: d["models"].update(x={"tier": "gold"})),
    "negative rate": ("prices.yaml", lambda d: opus(d)[0].update(input=-1)),
    "non-numeric rate": ("prices.yaml", lambda d: opus(d)[0].update(output="lots")),
    "boolean rate": ("prices.yaml", lambda d: opus(d)[0].update(output=True)),
    "missing rate": ("prices.yaml", lambda d: opus(d)[0].pop("write_1h")),
    "factor zero": ("mapping.yaml", lambda d: d["models"]["qwen3.8-27b"].update(factor=0)),
    "factor negative": ("mapping.yaml", lambda d: d["models"]["qwen3.8-27b"].update(factor=-2)),
    "two lines one day": ("prices.yaml", lambda d: d["tiers"]["sonnet"][1].update(
        {"from": d["tiers"]["sonnet"][0]["from"]})),
    "lines out of order": ("prices.yaml", lambda d: d["tiers"]["sonnet"].reverse()),
    "above without over": ("prices.yaml", lambda d: d["tiers"]["haiku"][1]["above"].pop("over")),
    "prices version": ("prices.yaml", lambda d: d.update(v=2)),
    "mapping version": ("mapping.yaml", lambda d: d.update(v=2)),
    "providers version": ("providers.yaml", lambda d: d.update(v=2)),
    "by-hand provider without from": ("providers.yaml", lambda d: d["providers"][4].pop("from")),
}


@pytest.mark.parametrize("name", list(FAULTS))
def test_refusals_name_the_file(name, tmp_path):
    filename, fault = FAULTS[name]
    shutil.copytree(SHIPPED, tmp_path / "m", ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    edit(filename, fault)(tmp_path / "m")
    with pytest.raises(PriceError) as raised:
        load(tmp_path / "m")
    assert filename in str(raised.value)


def test_a_missing_file_is_a_price_error_naming_it(tmp_path):
    with pytest.raises(PriceError, match="mapping.yaml"):
        load(tmp_path)


def test_the_front_never_imports_the_meter():
    assert not [m for m in FRONT_MODULES if m.startswith("spark.meter")]
    assert spark_apply.front_file("src/spark/meter/prices.yaml") is False
    assert spark_apply.front_file("src/spark/meter/prices.py") is False


def test_a_price_edit_restarts_only_the_gate_and_the_brake():
    registry = load_registry(Path(__file__).parent / "fixtures/models.yaml")
    plan = plan_restarts([], ["src/spark/meter/prices.yaml"], registry, registry, [], restart_front=False,
                         active=lambda unit: True)
    assert plan == RestartPlan(now=["local-ai-brake.service", "local-ai-gate.service"], after_quiet=[])
