"""`spark docs`: the scenario pages' check, and the notifications page generated from the registry."""

import re
from dataclasses import replace
from pathlib import Path

from spark import cli, docs
from spark.docs import check_scenarios
from spark.registry import NOTIFICATION_TYPES, load_registry

REPO = Path(__file__).resolve().parents[2]
STACK_REGISTRY = REPO / "stack" / "models.yaml"
DOWN_TYPES = ("gate_down", "front_down", "llama_swap_down", "brake_down")

GOOD = """---
title: "S01 · Morning start"
scenario-id: S01
phase: 2
status: planned
---

**Situation.** x
"""


def test_clean_page_passes(tmp_path):
    (tmp_path / "s01-morning-start.md").write_text(GOOD)
    assert check_scenarios(tmp_path) == []


def test_id_must_match_the_filename(tmp_path):
    (tmp_path / "s02-big-job.md").write_text(GOOD)
    assert any("s02-big-job.md" in p and "S02" in p for p in check_scenarios(tmp_path))


def test_status_must_be_known(tmp_path):
    (tmp_path / "s01-morning-start.md").write_text(GOOD.replace("planned", "done-ish"))
    assert any("status" in p for p in check_scenarios(tmp_path))


def test_verified_needs_a_date(tmp_path):
    (tmp_path / "s01-morning-start.md").write_text(GOOD.replace("planned", "verified"))
    assert any("verified" in p for p in check_scenarios(tmp_path))


def test_broken_front_matter_is_reported_by_name(tmp_path):
    (tmp_path / "s03-doesnt-fit.md").write_text("no front matter here\n")
    assert any("s03-doesnt-fit.md" in p for p in check_scenarios(tmp_path))


def test_invalid_yaml_front_matter_is_reported_by_name(tmp_path):
    (tmp_path / "s01-morning-start.md").write_text(GOOD.replace('Morning start"', "Morning start"))
    problems = check_scenarios(tmp_path)
    assert any("s01-morning-start.md" in p and "not valid YAML" in p for p in problems)


def test_front_matter_that_is_not_a_mapping_is_reported_by_name(tmp_path):
    (tmp_path / "s01-morning-start.md").write_text("---\n- S01\n- planned\n---\n\n**Situation.** x\n")
    problems = check_scenarios(tmp_path)
    assert any("s01-morning-start.md" in p and "needs YAML front matter" in p for p in problems)


def _rows(page: str) -> list[list[str]]:
    """The notifications table's rows, each its cells, the header and the rule left out."""
    lines = [line for line in page.splitlines() if line.startswith("| `")]
    return [[cell.strip() for cell in line.strip("|").split(" | ")] for line in lines]


def test_the_notifications_page_is_generated_from_the_registry():
    registry = load_registry(STACK_REGISTRY)
    page = docs.render_notifications_page(registry)
    assert page.startswith('---\ntitle: "Notifications"\n')
    assert "generated from `stack/models.yaml` by `spark docs notifications --write`" in page
    # Where each part comes from, and where the commands run (the review's minor 9).
    prose = " ".join(page.split("| Type |")[0].split())
    assert "the words from `spark/src/spark/messages.py`" in prose
    assert "then `make apply` on the Spark" in prose
    assert "| Type | Priority | When | Example |" in page
    rows = _rows(page)
    assert len(rows) == 20
    assert [row[0] for row in rows] == [f"`{kind}`" for kind in NOTIFICATION_TYPES]
    assert {row[0].strip("`"): row[1] for row in rows} == registry.notifications
    # Each priority is the registry's: change one, and the page changes with it.
    quiet = replace(registry, notifications={**registry.notifications, "loaded": "off", "gate_down": "default"})
    changed = {row[0].strip("`"): row[1] for row in _rows(docs.render_notifications_page(quiet))}
    assert (changed["loaded"], changed["gate_down"]) == ("off", "default")
    # Under the table: the four the failure notifier's unit carries need make install-units after make apply.
    after = page.split("| `memory_warning`")[1]
    line = " ".join(after.split())
    assert all(f"`{kind}`" in line for kind in DOWN_TYPES)
    assert re.search(r"needs `make install-units` after `make apply`, both on the Spark", line)


def test_spark_docs_notifications_check_finds_a_stale_page(tmp_path, monkeypatch, capsys):
    page = tmp_path / "notifications.md"
    monkeypatch.setattr(docs, "NOTIFICATIONS_PAGE", page)
    monkeypatch.setattr(docs, "REGISTRY_FILE", STACK_REGISTRY)
    assert cli.main(["docs", "notifications", "--check"]) == 1  # no page yet
    assert cli.main(["docs", "notifications", "--write"]) == 0
    assert page.read_text() == docs.render_notifications_page(load_registry(STACK_REGISTRY))
    assert cli.main(["docs", "notifications", "--check"]) == 0
    page.write_text(page.read_text().replace("| low |", "| high |", 1))
    capsys.readouterr()
    assert cli.main(["docs", "notifications", "--check"]) == 1
    assert "notifications.md is stale" in capsys.readouterr().err


def test_the_committed_notifications_page_is_current():
    # Kept current: a change to the registry's notifications, or to their words, fails here until the page is written
    # again (`make docs`, or `spark docs notifications --write`), since CI doesn't run `--check` yet.
    committed = (REPO / "website" / "reference" / "notifications.md").read_text()
    assert committed == docs.render_notifications_page(load_registry(STACK_REGISTRY))
