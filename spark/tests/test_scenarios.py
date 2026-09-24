from spark.docs import check_scenarios

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
