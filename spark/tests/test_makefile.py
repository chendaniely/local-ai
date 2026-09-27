"""The Makefile's deploy targets, run against stand-ins for the tools they call: what each runs, and what make says and
exits with when one fails. (make install-units' tests are in test_bootstrap.py, with bootstrap's.)"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def fakes(tmp_path: Path, **tools: str) -> dict[str, str]:
    """A stand-in for each tool, named by its keyword, that logs its call in tmp_path/calls and then runs its body. The
    environment is built, not copied: PATH with the stand-ins first, a HOME of its own, and CALLS."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in tools.items():
        (bindir / name).write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "$CALLS"\n{body}\n')
        (bindir / name).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(home), "CALLS": str(tmp_path / "calls")}


def make(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # -s: GNU make 4 prints "Entering directory" lines under -C otherwise.
    return subprocess.run(["make", "-s", "-C", str(ROOT), *args], capture_output=True, text=True, env=env)


def calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text().splitlines() if path.exists() else []


# The pull's journal, one "YYYY-MM-DD HH:MM:SS text" line an entry, in $JOURNAL. The stand-in systemctl logs this run's
# FAILED line there, stamped with the time it runs, as the pull logs its lines; the stand-in journalctl prints the
# entries from --since on, as journalctl does, or all of them without it.
PULL = 'printf "%s %s\\n" "$(date "+%Y-%m-%d %H:%M:%S")" "pull: coder: coder.gguf: FAILED — this run" >> "$JOURNAL"\n' \
       'exit "$PULL_EXIT"'
JOURNAL = ('since=""\n'
           'while [[ $# -gt 0 ]]; do case "$1" in --since) since="$2"; shift 2 ;; *) shift ;; esac; done\n'
           'while IFS= read -r entry; do\n'
           '  if [[ -z "$since" || ! "${entry:0:19}" < "$since" ]]; then echo "${entry:20}"; fi\n'
           'done < "$JOURNAL"')
EARLIER = "2020-01-01 00:00:00 pull: coder: coder.gguf: FAILED — an earlier run\n"


@pytest.mark.parametrize("pull", [0, 1], ids=["pulled", "failed"])
def test_make_pull_shows_this_runs_journal_whether_or_not_it_failed(tmp_path, pull):
    # A file that fails makes the pull exit 1, and the FAILED line that says why is in its journal: make shows the
    # journal then too, and still fails. Only this run's lines (Task 9's fix round 1, M3): an earlier run's FAILED
    # lines, shown above them, would read as this run's.
    env = fakes(tmp_path, systemctl=PULL, journalctl=JOURNAL)
    (tmp_path / "journal").write_text(EARLIER)
    result = make("pull", env={**env, "JOURNAL": str(tmp_path / "journal"), "PULL_EXIT": str(pull)})
    start, read = calls(tmp_path)
    assert start == "systemctl start local-ai-pull.service"
    assert re.fullmatch(r"journalctl -u local-ai-pull\.service --since \d{4}-\d\d-\d\d \d\d:\d\d:\d\d --no-pager",
                        read), read
    assert "FAILED — this run" in result.stdout and "an earlier run" not in result.stdout
    assert (result.returncode == 0) == (pull == 0), result.stderr


def test_make_pull_is_one_recipe_line_that_keeps_the_pulls_status():
    # On two lines, make stopped at the failed start, before the journal. The time is taken before the start: an
    # InvocationID wouldn't do, since a oneshot that ended cleanly can be unloaded, and its ID with it.
    assert make("-n", "pull").stdout.splitlines() == [
        "t=$(date '+%Y-%m-%d %H:%M:%S'); systemctl start local-ai-pull.service; s=$?; "
        'journalctl -u local-ai-pull.service --since "$t" --no-pager; exit $s']


def test_help_starts_every_description_in_one_column():
    # install-units-dry-run is 21 characters: a 20-character column pushed its description one place right.
    lines = make("help").stdout.splitlines()
    assert len({re.match(r"  \S+ +", line).end() for line in lines}) == 1, lines


def test_make_logs_without_a_service_says_how_to_ask(tmp_path):
    # Without s= it ran `journalctl -u local-ai-.service`, which names no unit: it showed nothing, and said nothing.
    env = fakes(tmp_path, journalctl="")
    result = make("logs", env=env)
    assert result.returncode == 2 and calls(tmp_path) == []
    assert "usage: make logs s=llama-swap|brake|pull|compose|open-webui|searxng" in result.stderr


@pytest.mark.parametrize(("service", "asked"), [
    ("llama-swap", "journalctl -u local-ai-llama-swap.service -n 100 --no-pager"),
    ("open-webui", "journalctl CONTAINER_NAME=local-ai-open-webui-1 -n 100 --no-pager"),
])
def test_make_logs_reads_a_units_journal_or_a_containers(tmp_path, service, asked):
    env = fakes(tmp_path, journalctl="")
    assert make("logs", f"s={service}", env=env).returncode == 0 and calls(tmp_path) == [asked]
