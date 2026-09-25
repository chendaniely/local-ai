"""Guarantees in the GitHub workflows that a later edit could quietly drop."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def steps(workflow: str) -> list[dict]:
    jobs = yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())["jobs"]
    return [step for job in jobs.values() for step in job["steps"]]


def test_ci_leaves_no_job_token_in_its_checkouts():
    # CI runs the repo's own code and third-party actions; none of it needs the token that
    # actions/checkout otherwise leaves in .git/config. (publish-website.yml's publish needs it.)
    checkouts = [step for step in steps("ci.yml") if step.get("uses", "").startswith("actions/checkout@")]
    assert checkouts
    for step in checkouts:
        assert step.get("with", {}).get("persist-credentials") is False, step


def test_every_action_is_pinned_by_a_full_commit_sha():
    # A tag can be moved under us; a SHA can't. Dependabot moves a SHA pin and its version comment
    # together.
    for workflow in ("ci.yml", "publish-website.yml"):
        for step in steps(workflow):
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]


def test_dependabot_proposes_actions_and_uv_updates_for_upgrade_day():
    # Upgrade day is Saturday; Friday's proposals are ready for it. A few at a time.
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    updates = {update["package-ecosystem"]: update for update in config["updates"]}
    assert updates.keys() == {"github-actions", "uv"}
    assert updates["github-actions"]["directory"] == "/"
    assert updates["uv"]["directory"] == "/spark"
    assert (ROOT / "spark/uv.lock").is_file()
    for update in updates.values():
        assert update["schedule"] == {"interval": "weekly", "day": "friday"}
        assert update["open-pull-requests-limit"] <= 3
