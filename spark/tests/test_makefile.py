"""The Makefile's deploy targets and make test, run against stand-ins for the tools they call: what each runs, and what
make says and exits with when one fails. (make install-units' tests are in test_bootstrap.py, with bootstrap's.)"""

import os
import re
import subprocess
from pathlib import Path

import pytest

from memory_cap import CAP_GIB

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
    assert ("usage: make logs s=llama-swap|brake|pull|compose|open-webui|searxng|front|gate|notify"
            in result.stderr)


@pytest.mark.parametrize(("service", "asked"), [
    ("llama-swap", "journalctl -u local-ai-llama-swap.service -n 100 --no-pager"),
    ("open-webui", "journalctl CONTAINER_NAME=local-ai-open-webui-1 -n 100 --no-pager"),
])
def test_make_logs_reads_a_units_journal_or_a_containers(tmp_path, service, asked):
    env = fakes(tmp_path, journalctl="")
    assert make("logs", f"s={service}", env=env).returncode == 0 and calls(tmp_path) == [asked]


@pytest.mark.parametrize(("service", "asked"), [
    ("front", "journalctl -u local-ai-front.service -n 100 --no-pager"),
    ("gate", "journalctl -u local-ai-gate.service -n 100 --no-pager"),
    # The failure notifier is a template unit, one instance per unit that failed: every instance's journal, the
    # pattern passed to journalctl as it is, never expanded by the shell.
    ("notify", "journalctl -u local-ai-notify@* -n 100 --no-pager"),
])
def test_make_logs_takes_front_gate_and_notify(tmp_path, service, asked):
    env = fakes(tmp_path, journalctl="")
    result = make("logs", f"s={service}", env=env)
    assert result.returncode == 0, result.stderr
    assert calls(tmp_path) == [asked]


def test_help_says_what_tunnel_and_brake_release_reach():
    # From Phase 2a the tunnel carries the model API, 127.0.0.1:9100, which is the front's from the cutover, and
    # make brake-release goes through the gate, Phase 1's direct release its fallback (Task 23).
    lines = {line.split()[0]: line for line in make("help").stdout.splitlines()}
    assert "forward the Spark's model API, 127.0.0.1:9100" in lines["tunnel"]
    assert "llama-swap" not in lines["tunnel"]
    assert "through the gate" in lines["brake-release"] and "directly" in lines["brake-release"]


# make test's memory cap (Dan's decision, 2026-10-15): where /opt/local-ai exists, the Spark, pytest runs in a user
# scope capped at memory_cap.CAP_GIB with no swap, after a check that the user manager answers; elsewhere (the Mac, CI's
# runners) as before. SPARK_OPT on make's command line stands in for that folder, there or not.
PYTEST = "uv run --frozen --project spark pytest"
SCOPE = f"systemd-run --user --scope --quiet -p MemoryMax={CAP_GIB}G -p MemorySwapMax=0 --"
ASK_MANAGER = "systemctl --user show --property=Version --value"


def recipe(*args: str) -> list[str]:
    # make -n prints nothing for the line that holds only the Spark's check, off the Spark (GNU make 4.3); blank lines
    # are dropped, so the test reads the same wherever make prints one.
    return [line for line in make("-n", "test", *args).stdout.splitlines() if line.strip()]


def test_make_test_on_the_spark_checks_the_user_manager_then_runs_pytest_capped(tmp_path):
    check, run = recipe(f"SPARK_OPT={tmp_path}")
    assert check.startswith(f"{ASK_MANAGER} >/dev/null 2>&1 || ")
    assert run == f"{SCOPE} {PYTEST} spark/tests"


def test_make_test_off_the_spark_runs_as_before(tmp_path):
    assert recipe(f"SPARK_OPT={tmp_path / 'absent'}") == [f"{PYTEST} spark/tests"]


def test_make_test_follows_this_machine():
    # The Spark's own form here, the Mac's and CI's there: what make test does depends only on /opt/local-ai.
    run = f"{SCOPE} {PYTEST} spark/tests" if Path("/opt/local-ai").exists() else f"{PYTEST} spark/tests"
    assert recipe()[-1] == run


@pytest.mark.parametrize("spark", [True, False], ids=["the Spark", "elsewhere"])
def test_make_test_passes_args_to_pytest_in_place_of_the_whole_suite(tmp_path, spark):
    opt = tmp_path if spark else tmp_path / "absent"
    run = recipe(f"SPARK_OPT={opt}", 'ARGS=spark/tests/test_x.py -k "a or b"')[-1]
    assert run == (f"{SCOPE} " if spark else "") + f'{PYTEST} spark/tests/test_x.py -k "a or b"'


@pytest.mark.parametrize("exit_code", [0, 1], ids=["passed", "failed"])
def test_make_test_on_the_spark_runs_the_capped_line_and_keeps_its_status(tmp_path, exit_code):
    env = fakes(tmp_path, systemctl="exit 0", **{"systemd-run": f"exit {exit_code}"}, uv="exit 0")
    result = make("test", f"SPARK_OPT={tmp_path}", "ARGS=spark/tests/test_x.py -k name", env=env)
    assert calls(tmp_path) == [ASK_MANAGER, f"{SCOPE} {PYTEST} spark/tests/test_x.py -k name"]
    assert (result.returncode == 0) == (exit_code == 0), result.stderr


def test_make_test_on_the_spark_without_a_user_manager_says_so_and_never_runs_uncapped(tmp_path):
    # agent's clone too: a session with no user manager gets this line, never an uncapped run.
    env = fakes(tmp_path, systemctl="echo 'Failed to connect to bus' >&2; exit 1", **{"systemd-run": "exit 0"},
                uv="exit 0")
    result = make("test", f"SPARK_OPT={tmp_path}", env=env)
    assert calls(tmp_path) == [ASK_MANAGER]
    assert result.returncode != 0
    assert "make test: " in result.stderr and "Log in again" in result.stderr
    assert "Failed to connect" not in result.stderr


def test_make_test_off_the_spark_runs_pytest_alone(tmp_path):
    env = fakes(tmp_path, systemctl="exit 0", **{"systemd-run": "exit 0"}, uv="exit 0")
    assert make("test", f"SPARK_OPT={tmp_path / 'absent'}", env=env).returncode == 0
    assert calls(tmp_path) == [f"{PYTEST} spark/tests"]


# TEMPORARY: make fast-coder and make flash-next, removed once Phase 4a puts the fast coder and Flash-Next in the
# registry (test_by_hand.py's TEMPORARY lists every piece). Their scripts' own tests are in test_by_hand.py; these are
# the Makefile's part: what each target runs, its help line, and its refusal off the Spark.
@pytest.mark.parametrize(("args", "line"), [
    (["fast-coder"], "BY_HAND_OPT='/opt/local-ai' bash stack/host/by-hand-fast-coder ''"),
    (["fast-coder", "DO=wait"], "BY_HAND_OPT='/opt/local-ai' bash stack/host/by-hand-fast-coder 'wait'"),
    (["fast-coder", "DO=stop"], "BY_HAND_OPT='/opt/local-ai' bash stack/host/by-hand-fast-coder 'stop'"),
    (["flash-next"], "BY_HAND_OPT='/opt/local-ai' MTP='' bash stack/host/by-hand-flash-next ''"),
    (["flash-next", "MTP=1"], "BY_HAND_OPT='/opt/local-ai' MTP='1' bash stack/host/by-hand-flash-next ''"),
    (["flash-next", "DO=wait"], "BY_HAND_OPT='/opt/local-ai' MTP='' bash stack/host/by-hand-flash-next 'wait'"),
    (["flash-next", "DO=stop"], "BY_HAND_OPT='/opt/local-ai' MTP='' bash stack/host/by-hand-flash-next 'stop'"),
], ids=["fast-coder", "fast-coder wait", "fast-coder stop", "flash-next", "flash-next MTP", "flash-next wait",
        "flash-next stop"])
def test_the_by_hand_targets_run_their_scripts_with_do_and_mtp(args, line):
    assert make("-n", *args).stdout.splitlines() == [line]


@pytest.mark.parametrize("target", ["fast-coder", "flash-next"])
def test_the_by_hand_targets_help_says_temporary_and_on_the_spark(target):
    lines = {line.split()[0]: line for line in make("help").stdout.splitlines()}
    assert "TEMPORARY" in lines[target] and "on the Spark" in lines[target]


@pytest.mark.parametrize("args", [["fast-coder"], ["fast-coder", "DO=stop"], ["flash-next"], ["flash-next", "DO=stop"]],
                         ids=["fast-coder", "fast-coder stop", "flash-next", "flash-next stop"])
def test_the_by_hand_targets_refuse_off_the_spark_and_run_nothing(tmp_path, args):
    # SPARK_OPT stands in for /opt/local-ai, as for make test: here, a folder that isn't there, as on the Mac or CI.
    env = fakes(tmp_path, **{name: "exit 0" for name in
                             ("sudo", "docker", "systemctl", "pgrep", "pkill", "curl", "free", "choom", "sync")})
    result = make(*args, f"SPARK_OPT={tmp_path / 'absent'}", env=env)
    assert result.returncode != 0 and calls(tmp_path) == []
    assert "runs on the Spark" in result.stderr
