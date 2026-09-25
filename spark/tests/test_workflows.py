"""Guarantees in the GitHub workflows that a later edit could quietly drop."""

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
