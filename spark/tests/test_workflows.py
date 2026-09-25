"""Guarantees in the GitHub workflows that a later edit could quietly drop."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def jobs(workflow: str) -> dict[str, dict]:
    return yaml.safe_load((ROOT / ".github/workflows" / workflow).read_text())["jobs"]


def steps(workflow: str) -> list[dict]:
    return [step for job in jobs(workflow).values() for step in job["steps"]]


def only_step(job_steps: list[dict], fragment: str) -> int:
    """The index of the one step whose `run` contains `fragment`."""
    found = [i for i, step in enumerate(job_steps) if fragment in step.get("run", "")]
    assert len(found) == 1, (fragment, found)
    return found[0]


def test_every_ci_job_has_a_time_limit():
    # Without one, a hung step holds a runner for GitHub's default of six hours.
    for name, job in jobs("ci.yml").items():
        limit = job.get("timeout-minutes")
        assert isinstance(limit, int) and 0 < limit <= 30, (name, limit)


def test_gitleaks_is_fetched_checked_and_run_in_separate_steps():
    # So a red step names its cause: a failed download is the network, a failed checksum a tampered
    # or corrupt file, and only the scan reports leaks.
    leaks = jobs("ci.yml")["leaks"]["steps"]
    download = only_step(leaks, "https://github.com/gitleaks/gitleaks/releases/download/")
    check = only_step(leaks, "sha256sum -c")
    scan = only_step(leaks, "./gitleaks git")
    assert download < check < scan
    # A network blip gets retried, and a stalled download can't hang the job.
    fetch = leaks[download]["run"]
    assert "--retry 3" in fetch and "--max-time 120" in fetch, fetch


def test_ci_reads_every_commits_patches_and_messages_with_the_repo_patterns():
    # gitleaks' rules don't cover addresses, so the repo's own patterns also read the whole history,
    # patches and messages, not only today's tree. That needs every commit fetched, and CI's mode.
    leaks = jobs("ci.yml")["leaks"]["steps"]
    checkout = next(step for step in leaks if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    step = leaks[only_step(leaks, "git log -p")]
    assert step.get("env", {}).get("CI") == "true"
    log, scan = step["run"].strip().splitlines()
    history = re.fullmatch(r"git log -p (?:--\S+ )*--format='%H%n%B' > (\S+)", log.strip())
    assert history, log  # every commit (no range), each one's patch and whole message
    assert re.search(rf"spark leakcheck --message {re.escape(history.group(1))} --ci$", scan), scan


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
