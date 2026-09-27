import os
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark import apply as spark_apply
from spark import cli
from spark import render as spark_render
from spark.apply import (RUNNING_UNITS, UNKNOWN_START, app_diff, apply_files, diff_tree, models_loaded, not_installed,
                         outdated_units, read_copies, start_times, started_at, units_to_restart, validation_env)
from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, Running
from spark.registry import load_registry
from spark.render import KEY_ENVS, installed_path, render
from spark.versions import load_versions

LLAMA, BRAKE, COMPOSE = "local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service"
FILES = {"llama-swap.yaml": "a", "models.yaml": "m", "compose/compose.yaml": "c",
         "systemd/local-ai-llama-swap.service": "u"}
# Root's copies of the units and the Compose project, as `make install-units` installed them.
ROOTS = {"compose/compose.yaml": "c", "systemd/local-ai-llama-swap.service": "u"}


def seeded(etc):
    for rel, content in FILES.items():
        (etc / rel).parent.mkdir(parents=True, exist_ok=True)
        (etc / rel).write_text(content)
    return etc


def run_apply(etc, files, *, installed=ROOTS, unreadable=(), outdated=(), active=(LLAMA, BRAKE, COMPOSE),
              app_changes=(), running=(), now_ok=False, dry_run=False, fails=()):
    ran, logs, synced = [], [], []

    def run_cmd(cmd):  # systemctl, as a stand-in: a unit in `fails` doesn't restart
        if cmd[-1] in fails:
            raise subprocess.CalledProcessError(1, cmd)
        ran.append(cmd)

    code = apply_files(files, etc, installed=dict(installed), unreadable=set(unreadable), outdated=list(outdated),
                       active=lambda unit: unit in active, app_changes=list(app_changes),
                       running=None if running is None else list(running), now_ok=now_ok, dry_run=dry_run,
                       sync_app=lambda: synced.append(True), run_cmd=run_cmd, log=logs.append)
    return code, ran, logs, synced


def test_nothing_changed_does_nothing(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES)
    assert (code, ran, logs) == (0, [], ["apply: nothing to change"])


def test_a_llama_swap_config_change_restarts_it_when_no_model_is_loaded(tmp_path):
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"})
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-llama-swap.service"]]
    assert (tmp_path / "llama-swap.yaml").read_text() == "b"


def test_llama_swap_change_is_refused_while_models_are_loaded(tmp_path):
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"},
                                        app_changes=["src/spark/brake.py"], running=["qwen3.6-35b-a3b"])
    assert code == 1 and ran == [] and synced == []
    assert (tmp_path / "llama-swap.yaml").read_text() == "a"
    assert "--now" in logs[-1] and "qwen3.6-35b-a3b" in logs[-1]


def test_not_knowing_what_is_loaded_counts_as_loaded(tmp_path):
    code, _, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, running=None)
    assert code == 1


def test_now_restarts_llama_swap_anyway(tmp_path):
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"},
                                running=["qwen3.6-35b-a3b"], now_ok=True)
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-llama-swap.service"]]


def test_an_app_change_is_synced_and_restarts_the_brake(tmp_path):
    code, ran, _, synced = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/brake.py"])
    assert code == 0 and synced == [True]
    assert ran == [["systemctl", "restart", "local-ai-brake.service"]]


def test_a_changed_unit_is_staged_for_root_and_nothing_else_moves(tmp_path):
    # Root runs only root's copy. apply stages the new unit where `make install-units` reads it, and
    # deploys and restarts nothing until root has it: not llama-swap for its new config, which the
    # new unit may need, not the brake for the app, not the web services for their older copy.
    new = FILES | {"systemd/local-ai-llama-swap.service": "u2", "llama-swap.yaml": "b"}
    code, ran, logs, synced = run_apply(seeded(tmp_path), new, app_changes=["src/spark/brake.py"],
                                        outdated=[COMPOSE])
    assert code == 0 and ran == [] and synced == []
    assert (tmp_path / "systemd/local-ai-llama-swap.service").read_text() == "u2"
    assert (tmp_path / "llama-swap.yaml").read_text() == "a"
    assert "systemd/local-ai-llama-swap.service differs from root's copy" in "\n".join(logs)
    assert "make install-units" in logs[-1]


def test_the_first_deploy_stages_roots_files_before_anything_else(tmp_path):
    code, ran, logs, synced = run_apply(tmp_path, FILES, installed={}, active=(), app_changes=["pyproject.toml"])
    assert code == 0 and ran == [] and synced == []
    staged = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert staged == sorted(ROOTS)
    assert "root has no copy of compose/compose.yaml yet" in "\n".join(logs)
    assert "make install-units" in logs[-1]


def test_a_running_unit_is_restarted_once_its_new_definition_is_installed(tmp_path):
    # `make install-units` installed a new Compose project; the web services still run the old one.
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES, outdated=[COMPOSE])
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-compose.service"]]


def test_llama_swap_waits_for_idle_models_to_run_its_new_definition(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES, outdated=[LLAMA], running=["qwen3.6-35b-a3b"])
    assert code == 1 and ran == [] and "--now" in logs[-1]
    code, ran, _, _ = run_apply(seeded(tmp_path), FILES, outdated=[LLAMA], running=["qwen3.6-35b-a3b"], now_ok=True)
    assert code == 0 and ran == [["systemctl", "restart", "local-ai-llama-swap.service"]]


def test_a_unit_that_isnt_running_is_never_started(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, active=())
    assert code == 0 and ran == [] and (tmp_path / "llama-swap.yaml").read_text() == "b"
    assert logs[-1] == "apply: local-ai-llama-swap.service isn't running; it starts with the new config"


def test_a_dry_run_lists_only_the_restarts_the_real_run_makes(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   active=(BRAKE,), dry_run=True)
    assert code == 0 and ran == []
    assert "apply: dry run — would restart local-ai-brake.service" in logs
    assert "apply: dry run — local-ai-llama-swap.service isn't running; it starts with the new config" in logs


def test_a_restart_that_fails_says_the_files_are_deployed_and_how_to_finish(tmp_path):
    # A unit that couldn't start again is stopped, and apply restarts only units that run, so the next apply finds
    # nothing to change: this is the one time to say which unit still needs it. (One that still runs, because systemd
    # refused the restart, started before its files were written, and the next apply restarts it: Task 7's fix round 1.)
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                   fails=(BRAKE,))
    assert code == 1
    assert ran == [["systemctl", "restart", LLAMA]]  # the other restarts still happen
    assert (tmp_path / "models.yaml").read_text() == "m2"
    assert logs[-1] == ("apply: restarting local-ai-brake.service failed, and its new files are deployed: see "
                        "`make logs s=brake`, and once it's fixed, `systemctl restart local-ai-brake.service`")


def test_a_copy_apply_cant_read_is_not_called_missing(tmp_path):
    code, _, logs, _ = run_apply(seeded(tmp_path), FILES, installed={"systemd/local-ai-llama-swap.service": "u"},
                                 unreadable={"compose/compose.yaml"})
    text = "\n".join(logs)
    assert code == 0 and "can't read root's copy of compose/compose.yaml" in text
    assert "root has no copy of compose/compose.yaml" not in text


def test_dry_run_changes_nothing(tmp_path):
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"models.yaml": "m2"},
                                        app_changes=["pyproject.toml"], outdated=[COMPOSE], dry_run=True)
    assert code == 0 and ran == [] and synced == []
    assert (tmp_path / "models.yaml").read_text() == "m"
    assert logs[-1] == "apply: dry run — would restart local-ai-brake.service, local-ai-compose.service"


def test_a_dry_run_shows_what_it_would_stage_and_changes_nothing(tmp_path):
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"compose/compose.yaml": "c2"}, dry_run=True)
    assert code == 0 and ran == [] and synced == []
    assert (tmp_path / "compose/compose.yaml").read_text() == "c"
    assert "compose/compose.yaml differs from root's copy" in "\n".join(logs)
    assert "make install-units" in logs[-1]


def test_restart_mapping():
    assert units_to_restart(["models.yaml"]) == ["local-ai-brake.service"]
    assert units_to_restart(["llama-swap.yaml"]) == ["local-ai-llama-swap.service"]
    assert units_to_restart([], app_changed=True) == ["local-ai-brake.service"]
    # Root's files restart nothing by themselves: a unit restarts once root's copy is installed.
    assert units_to_restart(["compose/searxng/settings.yml", "systemd/local-ai-brake.service"]) == []


def test_roots_files_wait_for_make_install_units_until_roots_copies_match():
    installed = {"compose/compose.yaml": "c", "systemd/local-ai-llama-swap.service": "old"}
    files = FILES | {"systemd/local-ai-brake.service": "b"}
    assert not_installed(files, installed) == ["systemd/local-ai-brake.service", "systemd/local-ai-llama-swap.service"]


def test_a_running_unit_that_started_before_its_copy_was_installed_is_outdated():
    started = {LLAMA: 100.0, BRAKE: 100.0, COMPOSE: 100.0}
    changed_at = {
        "systemd/local-ai-llama-swap.service": 100.25,  # installed a quarter second after llama-swap started
        "systemd/local-ai-brake.service": 100.0,        # installed as the brake started: not after
        "compose/searxng/settings.yml": 150.0,         # the Compose project is the web services' definition
        "systemd/local-ai-pull.service": 200.0,         # the pull unit runs only when asked
    }
    assert outdated_units(started, changed_at) == ["local-ai-compose.service", "local-ai-llama-swap.service"]
    # A unit that isn't running starts with its new definition.
    assert outdated_units({LLAMA: None}, {"systemd/local-ai-llama-swap.service": 200.0}) == []


def test_started_at_reads_when_the_last_start_began():
    # InactiveExitTimestamp is when the start began, ActiveEnterTimestamp when it ended: for the compose
    # unit, whose start runs `docker compose up` for up to 15 minutes, a copy installed meanwhile is
    # newer than the start's beginning, and would look older than its end.
    show = ("ActiveState=active\n"
            "InactiveExitTimestamp=Fri 2026-09-25 23:19:46.826238 UTC\n"
            "ActiveEnterTimestamp=Fri 2026-09-25 23:34:46.000000 UTC\n")
    assert started_at(show) == datetime(2026, 9, 25, 23, 19, 46, 826238, tzinfo=timezone.utc).timestamp()
    # A stopped unit keeps the time it last started: only its state says it isn't running.
    assert started_at(show.replace("ActiveState=active", "ActiveState=inactive")) is None
    assert started_at("") is None
    for stamp in ("", "@1790371186"):  # a running unit's time that isn't the us+utc form: see start_times
        with pytest.raises(ValueError, match="InactiveExitTimestamp="):
            started_at(f"ActiveState=active\nInactiveExitTimestamp={stamp}\n")


def test_a_running_unit_whose_start_cant_be_read_is_named_not_skipped():
    shows = {LLAMA: "ActiveState=active\nInactiveExitTimestamp=Fri 2026-09-25 23:19:46.826238 UTC\n",
             BRAKE: "ActiveState=active\nInactiveExitTimestamp=@1790371186\n",
             COMPOSE: "ActiveState=inactive\nInactiveExitTimestamp=\n"}
    logs = []
    started = start_times([LLAMA, BRAKE, COMPOSE], shows.get, log=logs.append)
    assert started[COMPOSE] is None and started[BRAKE] == UNKNOWN_START and started[LLAMA] < UNKNOWN_START
    assert logs == [f"apply: can't read when {BRAKE} started (InactiveExitTimestamp='@1790371186'), so it can't "
                    f"tell whether {BRAKE} runs root's latest copy: if `make install-units` changed its files, "
                    f"run `systemctl restart {BRAKE}`"]
    # Still running, so a changed config restarts it; but no copy, however new, counts as newer.
    assert outdated_units(started, {"systemd/local-ai-brake.service": 9e9}) == []
    # Restarting llama-swap by hand stops every loaded model, so its line says when to.
    logs.clear()
    start_times([LLAMA], {LLAMA: shows[BRAKE]}.get, log=logs.append)
    assert logs == [f"apply: can't read when {LLAMA} started (InactiveExitTimestamp='@1790371186'), so it can't "
                    f"tell whether {LLAMA} runs root's latest copy: if `make install-units` changed its files, "
                    f"run `systemctl restart {LLAMA}` once no model is loaded (restarting it stops them all)"]


class Client:
    """Stands in for LlamaSwap: `running()` returns or raises what it was given."""

    def __init__(self, answer):
        self.answer = answer

    def running(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_what_llama_swap_has_loaded_is_unknown_while_its_unit_runs_and_it_doesnt_answer():
    logs = []
    assert models_loaded(Client([Running("coder", "ready")]), unit_active=True, log=logs.append) == ["coder"]
    # Its unit isn't running: nothing is loaded, and restarting it stops nothing.
    assert models_loaded(Client(LlamaSwapUnreachable("down")), unit_active=False, log=logs.append) == []
    # Its unit runs and nothing answered in time: models may be loaded. Can't tell.
    assert models_loaded(Client(LlamaSwapUnreachable("hung")), unit_active=True, log=logs.append) is None
    # It answered but wouldn't say, a wrong key say.
    assert models_loaded(Client(LlamaSwapError("HTTP 401")), unit_active=True, log=logs.append) is None
    assert len(logs) == 2


def test_a_llama_swap_that_takes_the_call_and_never_answers_counts_as_cant_tell():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)  # the kernel completes the connection; nothing ever reads the request
    try:
        client = LlamaSwap(f"http://127.0.0.1:{listener.getsockname()[1]}", "key", timeout=0.5)
        with pytest.raises(LlamaSwapUnreachable):
            client.running()
        assert models_loaded(client, unit_active=True, log=lambda line: None) is None
    finally:
        listener.close()


def test_validate_gives_each_key_its_own_placeholder():
    env = validation_env({"PATH": "/usr/bin", "LLAMASWAP_KEY_AGENT": "real"})
    assert env["PATH"] == "/usr/bin"
    values = [env[name] for name in KEY_ENVS]
    assert len(set(values)) == len(KEY_ENVS) and "real" not in values


@pytest.mark.skipif(__import__("os").geteuid() == 0, reason="root reads any file")
def test_read_copies_tells_a_missing_copy_from_one_it_cant_read(tmp_path):
    (tmp_path / "a").write_text("x")
    (tmp_path / "b").write_text("y")
    (tmp_path / "b").chmod(0)
    where = {"systemd/a": tmp_path / "a", "systemd/b": tmp_path / "b", "systemd/c": tmp_path / "c"}
    texts, times, unreadable = read_copies({**dict.fromkeys(where, ""), "models.yaml": ""}, where.get)
    assert texts == {"systemd/a": "x", "systemd/b": None, "systemd/c": None} and unreadable == {"systemd/b"}
    assert set(times) == {"systemd/a"} and isinstance(times["systemd/a"], float)


def test_a_missing_file_counts_as_changed(tmp_path):
    assert diff_tree({"new.yaml": "x"}, tmp_path) == ["new.yaml"]


def test_app_diff_skips_the_venv_and_caches(tmp_path):
    src = tmp_path / "src"
    for rel in ("pyproject.toml", "src/spark/cli.py", ".venv/bin/python", "src/spark/__pycache__/cli.pyc"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text("x")
    assert app_diff(src, tmp_path / "app") == ["pyproject.toml", "src/spark/cli.py"]


# Task 7's fix round 1. apply_files' own rules first; then `spark apply` whole, through cli.main, with everything it
# touches faked (Box): the review found run() untested, and a one-line slip there passed every test above.


def put(path, text, at):
    """`path` holding `text`, last modified at `at` (seconds since the epoch)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (at, at))


class Answers:
    """Stands in for LlamaSwap: each `running()` gives the next answer, the last one repeated, and raises an error."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def running(self):
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_a_dry_run_refuses_what_the_real_run_refuses(tmp_path):
    # Minor 5: it says what the real run would do, and exits as the real run would.
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"},
                                        running=["qwen3.6-35b-a3b"], dry_run=True)
    assert code == 1 and ran == [] and synced == [] and (tmp_path / "llama-swap.yaml").read_text() == "a"
    assert logs[-1] == ("apply: dry run — would refuse: models are loaded (qwen3.6-35b-a3b), and restarting llama-swap "
                        f"stops every model; with --now it would restart {LLAMA}, {BRAKE}")


def test_the_staging_stop_says_nothing_but_the_staging(tmp_path):
    # Minor 5: at that stop nothing else is deployed, so nothing else is listed as changing.
    new = FILES | {"systemd/local-ai-llama-swap.service": "u2", "llama-swap.yaml": "b"}
    for dry_run in (False, True):
        _, _, logs, _ = run_apply(seeded(tmp_path / str(dry_run)), new, app_changes=["src/spark/brake.py"],
                                  outdated=[COMPOSE], dry_run=dry_run)
        assert logs[:-1] == ["apply: systemd/local-ai-llama-swap.service differs from root's copy "
                             "(/etc/systemd/system/local-ai-llama-swap.service)"]
        assert "make install-units" in logs[-1]


def test_a_llama_swap_whose_unit_is_stopped_is_never_refused_for(tmp_path):
    # Minor 6: however /running answered, a stopped unit has nothing loaded, and apply restarts nothing that's stopped.
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, active=(BRAKE, COMPOSE),
                                   running=None)
    assert code == 0 and ran == [] and (tmp_path / "llama-swap.yaml").read_text() == "b"
    assert logs[-1] == f"apply: {LLAMA} isn't running; it starts with the new config"


def test_each_file_apply_deploys_belongs_to_the_unit_that_runs_on_it():
    rels = ("llama-swap.yaml", "models.yaml", spark_apply.APP, "compose/searxng/settings.yml",
            "systemd/local-ai-brake.service", "systemd/local-ai-pull.service")
    assert [spark_apply.unit_of(rel) for rel in rels] == [LLAMA, BRAKE, BRAKE, COMPOSE, BRAKE, None]


def test_a_running_unit_that_started_before_apply_wrote_its_files_is_outdated():
    # I2: a run cut off after it wrote a file, before the restart it owed. The next run owes that restart still.
    started = {LLAMA: 100.0, BRAKE: 100.0, COMPOSE: 100.0}
    assert outdated_units(started, {"llama-swap.yaml": 100.25, "models.yaml": 99.0}) == [LLAMA]
    assert outdated_units(started, {"models.yaml": 100.25}) == [BRAKE]
    assert outdated_units(started, {spark_apply.APP: 100.25}) == [BRAKE]  # the app's last sync finished after it
    assert outdated_units({LLAMA: None, BRAKE: None}, {"llama-swap.yaml": 200.0, spark_apply.APP: 200.0}) == []


def test_deployed_times_are_when_apply_wrote_its_files_and_last_synced_the_app(tmp_path):
    etc, app = tmp_path / "etc", tmp_path / "app"
    put(etc / "llama-swap.yaml", "x", 100.0)
    put(app / spark_apply.SYNC_STAMP, "", 300.0)
    put(etc / "compose/compose.yaml", "x", 400.0)  # staged for root: root's copy says when it was installed
    assert spark_apply.deployed_times(etc, app) == {"llama-swap.yaml": 100.0, spark_apply.APP: 300.0}


def test_app_diff_counts_every_file_until_a_sync_finishes_and_files_removed_from_spark(tmp_path):
    # C1 and Minor 8: a sync that failed is tried again, and a file removed from spark/ leaves the app too.
    src, app = tmp_path / "src", tmp_path / "app"
    for rel in ("pyproject.toml", "src/spark/cli.py"):
        put(src / rel, "x", 0)
        put(app / rel, "x", 0)
    put(app / "src/spark/retired.py", "x", 0)
    put(app / ".venv/bin/spark", "x", 0)  # the venv is uv's, not the app's
    assert app_diff(src, app) == ["pyproject.toml", "src/spark/cli.py", "src/spark/retired.py"]  # no sync finished
    put(app / spark_apply.SYNC_STAMP, "", 0)
    assert app_diff(src, app) == ["src/spark/retired.py"]
    put(src / "src/spark/cli.py", "y", 0)
    assert app_diff(src, app) == ["src/spark/cli.py", "src/spark/retired.py"]


def test_after_a_restart_apply_waits_a_bounded_time_for_llama_swap_to_answer():
    now, slept = [0.0], []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    down = LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: connection refused")
    assert spark_apply.wait_for_running(Answers(down, down, []), 30, sleep=sleep, clock=lambda: now[0]) is None
    assert slept == [1.0, 1.0]
    now[0], slept[:] = 0.0, []
    odd = LlamaSwapError("llama-swap GET /running: an answer it can't read (not v257's shape)")
    assert spark_apply.wait_for_running(Answers(odd), 2.5, sleep=sleep, clock=lambda: now[0]) == str(odd)
    assert slept == [1.0, 1.0, 0.5]  # never past its time


def test_validate_gets_path_and_a_placeholder_for_each_key_and_nothing_else():
    # Minor 9: Dan's environment can hold secrets of his own, and -validate needs none of it.
    placeholders = {name: f"validate-only-{i}" for i, name in enumerate(KEY_ENVS)}
    assert validation_env({"PATH": "/usr/bin", "HOME": "/home/dan", "SPARK_API_KEY": "k"}) == {"PATH": "/usr/bin",
                                                                                              **placeholders}
    assert validation_env({}) == {"PATH": os.defpath, **placeholders}


ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures"
OLD = time.time() - 3600  # when a finished apply wrote its files, and `make install-units` installed root's copies
T0 = OLD + 600  # when the running units started


def systemctl_show(start):
    """What `systemctl show --timestamp=us+utc` says of a unit whose start began at `start`; None: not running."""
    if start is None:
        return "ActiveState=inactive\nInactiveExitTimestamp=\n"
    stamp = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%a %Y-%m-%d %H:%M:%S.%f UTC")
    return f"ActiveState=active\nInactiveExitTimestamp={stamp}\n"


class Box:
    """Everything `spark apply` touches, faked under tmp_path, to drive it whole through cli.main:
    - the repo: the fixture registry, its versions pinned, stack's own templates, a small spark/ project;
    - /opt/local-ai (DEPLOY), with a llama-swap binary so -validate runs, and root's two folders;
    - systemd: `units`, when each unit's start began (None: not running); a restart starts it anew;
    - llama-swap's GET /running: `answers`, one per call, the last repeated;
    - every command apply runs, each faked here; any other fails the test. Nothing real runs.
    `events` lists, in order, what apply asked of each."""

    def __init__(self, tmp_path, monkeypatch, capsys):
        self.capsys = capsys
        self.repo, self.opt = tmp_path / "repo", tmp_path / "opt"
        self.etc, self.app = self.opt / "etc", self.opt / "app"
        shutil.copytree(ROOT / "stack/templates", self.repo / "stack/templates")
        shutil.copy(FIX / "models.yaml", self.repo / "stack/models.yaml")
        pinned = (FIX / "versions.yaml").read_text().replace("pin: null", "pin: sha256:" + "0" * 64)
        (self.repo / "stack/versions.yaml").write_text(pinned)
        for rel, text in {"pyproject.toml": "p", "uv.lock": "l", "src/spark/cli.py": "v1"}.items():
            put(self.repo / "spark" / rel, text, OLD)
        put(self.opt / "bin/llama-swap/v257/llama-swap", "", OLD)
        self.units = dict.fromkeys(RUNNING_UNITS)
        self.answers = [LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: connection refused")]
        self.interrupted, self.failing, self.uv_fails, self.validate_code, self.validate_env = set(), set(), False, 0, None
        self.uv_env = None
        self.events = []
        monkeypatch.setattr(spark_apply, "DEPLOY", str(self.opt))
        monkeypatch.setattr(spark_render, "UNIT_DIR", str(tmp_path / "root/systemd"))
        monkeypatch.setattr(spark_render, "COMPOSE_DIR", str(tmp_path / "root/compose"))
        monkeypatch.setattr(spark_apply, "_show", lambda unit: systemctl_show(self.units[unit]))
        monkeypatch.setattr(spark_apply, "LlamaSwap", self.client)
        monkeypatch.setattr(spark_apply, "subprocess",
                            SimpleNamespace(run=self.run, CalledProcessError=subprocess.CalledProcessError))
        monkeypatch.setattr(spark_apply, "write_tree", self.write_tree)
        monkeypatch.setattr(spark_apply, "READY_SECONDS", 0.0)  # one try: no test waits
        monkeypatch.chdir(self.repo)

    def client(self, url, key, timeout):
        box = self

        class Client:
            def running(self):
                box.events.append("GET /running")
                answer = box.answers.pop(0) if len(box.answers) > 1 else box.answers[0]
                if isinstance(answer, Exception):
                    raise answer
                return answer

        return Client()

    def run(self, cmd, **kwargs):
        cmd = [str(word) for word in cmd]
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if len(cmd) == 4 and cmd[1] == "-config" and cmd[3] == "-validate":
            self.events.append("-validate")
            self.validate_env = kwargs.get("env")
            return subprocess.CompletedProcess(cmd, self.validate_code)
        if cmd[:2] == ["uv", "sync"]:
            self.events.append("uv sync")
            self.uv_env = kwargs.get("env")
            if self.uv_fails:
                raise subprocess.CalledProcessError(2, cmd)
            put(self.app / ".venv/bin/spark", "", time.time())
            return subprocess.CompletedProcess(cmd, 0)
        if cmd[:2] == ["systemctl", "restart"] and len(cmd) == 3:
            self.events.append(f"restart {cmd[2]}")
            if cmd[2] in self.interrupted:
                raise KeyboardInterrupt  # Ctrl-C, before systemd took the job
            if cmd[2] in self.failing:
                raise subprocess.CalledProcessError(1, cmd)
            self.units[cmd[2]] = time.time()
            return subprocess.CompletedProcess(cmd, 0)
        raise AssertionError(f"apply ran {cmd}, which no test fakes")

    def write_tree(self, files, out):
        self.events.extend(f"write {rel}" for rel in files)
        spark_render.write_tree(files, out)

    def rendered(self):
        registry = self.repo / "stack/models.yaml"
        return render(load_registry(registry), load_versions(self.repo / "stack/versions.yaml"), registry.read_text())

    def installed(self, at=OLD):
        """`make install-units` ran at `at`: root's copies are what render renders, and so are the staged ones."""
        for rel, text in self.rendered().items():
            if installed_path(rel) is not None:
                put(Path(installed_path(rel)), text, at)
                put(self.etc / rel, text, at)

    def deployed(self, old=None, running=True):
        """A finished apply left the stack at OLD: root's copies installed, every file deployed (each in `old` with
        that text instead), the app synced. Its units run, started at T0, unless `running` is False."""
        self.installed()
        for rel, text in self.rendered().items():
            if installed_path(rel) is None:
                put(self.etc / rel, (old or {}).get(rel, text), OLD)
        for path in (self.repo / "spark").rglob("*"):
            if path.is_file():
                put(self.app / path.relative_to(self.repo / "spark"), path.read_text(), OLD)
        put(self.app / ".venv/bin/spark", "", OLD)
        put(self.app / spark_apply.SYNC_STAMP, "", OLD)
        self.units = dict.fromkeys(RUNNING_UNITS, T0 if running else None)

    def apply(self, *flags):
        """`spark apply --key-env PROBE_KEY <flags>`: its exit code, what it printed, and what it asked of the fakes."""
        self.events = []
        try:
            code = cli.main(["apply", "--key-env", "PROBE_KEY", *flags])
        finally:
            printed = self.capsys.readouterr()
        return code, (printed.out + printed.err).splitlines(), self.events


@pytest.fixture
def box(tmp_path, monkeypatch, capsys):
    return Box(tmp_path, monkeypatch, capsys)


def test_run_stages_roots_files_first_and_says_and_changes_nothing_else(box):
    # The first deploy: root has no copies yet. apply stages them for `make install-units`, and stops there.
    roots = sorted(rel for rel in box.rendered() if installed_path(rel) is not None)
    code, lines, events = box.apply()
    assert code == 0 and events == ["-validate", "GET /running", *(f"write {rel}" for rel in roots)]
    assert sorted(p.relative_to(box.etc).as_posix() for p in box.etc.rglob("*") if p.is_file()) == roots
    assert not box.app.exists()
    assert [line.split(" yet (")[0] for line in lines[:-1]] == [f"apply: root has no copy of {rel}" for rel in roots]
    assert lines[-1].startswith(f"apply: staged in {box.etc}; nothing else is deployed or restarted")


def test_run_refuses_to_restart_a_llama_swap_that_runs_and_doesnt_answer_unless_now(box):
    # I4's first slip, `unit_active=… is None`, read a hung llama-swap as idle and restarted it over its models.
    box.deployed(old={"llama-swap.yaml": "old config"})
    box.answers = [LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: timed out")]
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running"])  # nothing written, nothing restarted
    assert (box.etc / "llama-swap.yaml").read_text() == "old config"
    assert lines[-1] == ("apply: can't tell which models are loaded, and restarting llama-swap stops every model; "
                         "re-run when idle, or with --now (nothing was changed)")
    box.answers = [LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: timed out"), []]
    code, _, events = box.apply("--now")  # restarted, the new llama-swap answers
    assert (code, events) == (0, ["-validate", "GET /running", "write llama-swap.yaml", f"restart {LLAMA}",
                                  "GET /running"])


def test_run_restarts_llama_swap_over_loaded_models_only_with_now(box):
    box.deployed(old={"llama-swap.yaml": "old config"})
    box.answers = [[Running("coder", "ready")]]
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running"]) and "(coder)" in lines[-1] and "--now" in lines[-1]
    box.answers = [[Running("coder", "ready")], []]
    code, _, events = box.apply("--now")  # no second look before the restart: --now said to go ahead
    assert (code, events) == (0, ["-validate", "GET /running", "write llama-swap.yaml", f"restart {LLAMA}",
                                  "GET /running"])


def test_run_starts_no_unit_that_isnt_running(box):
    # I4's second slip, `active=lambda unit: True`, started units that weren't running.
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": "old registry"}, running=False)
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "uv sync", "write llama-swap.yaml", "write models.yaml"])
    assert lines[-2:] == [f"apply: {LLAMA} isn't running; it starts with the new config",
                          f"apply: {BRAKE} isn't running; it starts with the new config"]


def test_a_failed_sync_is_said_and_tried_again_until_it_succeeds(box):
    # C1: the first deploy after `make install-units`. uv sync fails: apply says so, deploys nothing else, exits 1.
    # The app counts as changed until a sync finishes, so the next run syncs again, then deploys the rest.
    box.installed()
    box.uv_fails = True
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running", "uv sync"])
    assert lines[-1] == (f"apply: the app's files are copied into {box.app}, but `uv sync` failed (exit 2), so its "
                         "environment isn't synced; nothing else was deployed or restarted — once it's fixed, "
                         "`make apply` syncs it again")
    assert not (box.etc / "llama-swap.yaml").exists() and not (box.app / spark_apply.SYNC_STAMP).exists()
    box.uv_fails = False
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "uv sync", "write llama-swap.yaml", "write models.yaml"])
    assert (box.app / ".venv/bin/spark").exists() and (box.app / spark_apply.SYNC_STAMP).exists()
    assert box.apply()[1] == ["apply: nothing to change"]


def test_a_failed_sync_on_a_running_stack_restarts_the_brake_once_it_succeeds(box):
    box.deployed()
    box.answers = [[]]
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    box.uv_fails = True
    code, _, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running", "uv sync"])
    box.uv_fails = False
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "uv sync", f"restart {BRAKE}"])


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_a_copy_that_fails_is_said_and_tried_again(box):
    box.deployed()
    box.answers = [[]]
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    (box.app / "src/spark").chmod(0o555)
    try:
        code, lines, events = box.apply()
    finally:
        (box.app / "src/spark").chmod(0o755)
    assert (code, events) == (1, ["-validate", "GET /running"])
    assert lines[-1].startswith(f"apply: copying the app into {box.app} failed (")
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "uv sync", f"restart {BRAKE}"])


def test_the_restarts_a_cut_off_run_owed_are_made_by_the_next(box):
    # I2: Ctrl-C after apply wrote llama-swap's config and the registry, before either restart.
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": "old registry"})
    box.answers = [[]]
    box.interrupted = {LLAMA}
    with pytest.raises(KeyboardInterrupt):
        box.apply()
    assert box.units == dict.fromkeys(RUNNING_UNITS, T0) and (box.etc / "llama-swap.yaml").read_text() != "old config"
    box.interrupted = set()
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "GET /running", f"restart {LLAMA}", "GET /running",
                                  f"restart {BRAKE}"])
    assert f"apply: {LLAMA} started before its latest files were in place, so it still runs older ones" in lines


def test_a_brake_that_started_before_the_apps_last_sync_is_restarted(box):
    # I2, for the app: the sync finished, and the run was cut off before the brake's restart.
    box.deployed()
    box.answers = [[]]
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    box.interrupted = {BRAKE}
    with pytest.raises(KeyboardInterrupt):
        box.apply()
    box.interrupted = set()
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", f"restart {BRAKE}"])


def test_llama_swap_restarts_first_and_is_asked_again_just_before(box):
    # I3: nothing else between the second look and llama-swap's restart, not compose's restart of up to 15 minutes.
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": "old registry"})
    os.utime(installed_path("compose/compose.yaml"), (T0 + 60, T0 + 60))  # installed after compose started
    box.answers = [[]]
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "write llama-swap.yaml", "write models.yaml",
                                  "GET /running", f"restart {LLAMA}", "GET /running", f"restart {BRAKE}",
                                  f"restart {COMPOSE}"])


@pytest.mark.parametrize("second, said", [
    ([Running("coder", "ready")], "models are now loaded (coder)"),
    (LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: timed out"),
     "can't tell which models are loaded"),
], ids=["a model loaded meanwhile", "no answer the second time"])
def test_llama_swap_keeps_running_when_the_second_look_isnt_idle(box, second, said):
    # I3: a model that starts loading while apply syncs and writes is seen, and not stopped. The files are deployed
    # and the other restarts made; llama-swap's restart waits for the next run, which owes it (I2).
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": "old registry"})
    box.answers = [[], second]
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running", "write llama-swap.yaml", "write models.yaml",
                                  "GET /running", f"restart {BRAKE}"])
    assert lines[-1] == (f"apply: didn't restart {LLAMA}: {said}, and restarting it stops every model; its new files "
                         "are deployed, so re-run when idle, or with --now")
    box.answers = [[]]
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "GET /running", f"restart {LLAMA}", "GET /running"])


def test_apply_says_when_a_restarted_llama_swap_doesnt_answer(box):
    # The plan's rule: every config change is followed by a start and GET /running, not just -validate.
    box.deployed(old={"llama-swap.yaml": "old config"})
    odd = LlamaSwapError("llama-swap GET /running: an answer it can't read (not v257's shape)")
    box.answers = [[], [], odd]
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running", "write llama-swap.yaml", "GET /running",
                                  f"restart {LLAMA}", "GET /running"])
    assert lines[-1] == (f"apply: restarted {LLAMA}, but llama-swap didn't answer GET /running in time ({odd}): see "
                         "`make logs s=llama-swap`")


def test_a_key_the_client_wont_send_blocks_nothing_while_llama_swaps_unit_is_stopped(box, monkeypatch):
    # Minor 6, with the real client: it refuses the key before sending, which says nothing of what's loaded. But a
    # stopped unit has nothing loaded, and apply restarts nothing that's stopped.
    monkeypatch.setattr(spark_apply, "LlamaSwap", LlamaSwap)
    monkeypatch.setenv("PROBE_KEY", "dummy\r")  # a stand-in, never a real key
    box.deployed(old={"llama-swap.yaml": "old config"}, running=False)
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", "write llama-swap.yaml"])
    assert "isn't printable ASCII" in lines[0]
    assert lines[-1] == f"apply: {LLAMA} isn't running; it starts with the new config"


@pytest.mark.parametrize("what, text", [
    ("registry", "budget: " + "[" * 100_000 + "]" * 100_000 + "\n"),
    ("registry", "budget: {allocatable_gib: 102}\n"),
    ("versions file", "components: {llama-swap: [\n"),
], ids=["a registry nested too deep", "a registry that isn't valid", "versions that aren't YAML"])
def test_a_file_apply_cant_load_is_named_not_a_traceback(box, what, text):
    # Minor 7: apply loads them as `spark render` does, and cli.main prints the refusal.
    path = box.repo / ("stack/models.yaml" if what == "registry" else "stack/versions.yaml")
    path.write_text(text)
    code, lines, events = box.apply()
    assert (code, events) == (1, [])
    assert lines[0].startswith(f"spark apply: the {what} {path} won't load: ")


def test_uv_sync_is_given_the_tests_own_environment_and_uvs_settings_only(box):
    # Task 7's fix round 2: apply hands uv sync its environment, and the fake it reaches raises when uv fails, so a
    # traceback would print it. It's the test's own (conftest.py's fake_environment). Names only, taken first: pytest
    # prints the arguments of a call in a failing assert.
    box.deployed()
    box.answers = [[]]
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    box.apply()
    names = set(box.uv_env) - {"PYTEST_CURRENT_TEST"}
    assert names <= {"PATH", "HOME", "LANG", "UV_PYTHON_INSTALL_DIR", "UV_LINK_MODE"}


def test_validate_is_run_with_path_and_placeholder_keys_only(box, monkeypatch):
    # Minor 9, as run() runs it, in the test's own environment (conftest.py): PROBE_SECRET stands in for a secret of
    # Dan's, and the expectation is built from that fake, never the shell's.
    monkeypatch.setenv("PROBE_SECRET", "not for llama-swap")
    box.deployed()
    box.answers = [[]]
    box.apply()
    assert box.validate_env == validation_env({"PATH": os.environ["PATH"]})
    box.validate_code = 1
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate"])
    assert lines[-1] == "apply: llama-swap rejected the rendered config; nothing was changed"


def test_the_app_is_replaced_file_by_file_and_loses_what_spark_no_longer_has(box):
    # Minor 8: a file removed from spark/ leaves the app; each changed file is replaced whole; the venv stays.
    box.deployed()
    box.answers = [[]]
    put(box.app / "src/spark/retired/old.py", "gone from spark/", OLD)
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    before = (box.app / "src/spark/cli.py").stat().st_ino
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "uv sync", f"restart {BRAKE}"])
    assert not (box.app / "src/spark/retired").exists()
    assert (box.app / "src/spark/cli.py").read_text() == "v2" and (box.app / "src/spark/cli.py").stat().st_ino != before
    assert (box.app / ".venv/bin/spark").exists() and (box.app / spark_apply.SYNC_STAMP).exists()
