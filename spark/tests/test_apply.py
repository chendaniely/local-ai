import itertools
import os
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark import apply as spark_apply
from spark import cli, llamaswap
from spark import render as spark_render
from spark.apply import (RUNNING_UNITS, UNKNOWN_START, RestartPlan, app_diff, apply_files, diff_text, diff_tree,
                         models_loaded, not_installed, outdated_units, plan_restarts, read_copies, restart_order,
                         start_times, started_at, validation_env)
from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, Running
from spark.registry import load_registry
from spark.render import INTERNAL_KEY_ENVS, installed_path, render
from spark.versions import load_versions

LLAMA, BRAKE, COMPOSE = "local-ai-llama-swap.service", "local-ai-brake.service", "local-ai-compose.service"
FRONT, GATE = "local-ai-front.service", "local-ai-gate.service"  # Phase 2a's (Task 31)
FILES = {"llama-swap.yaml": "a", "models.yaml": "m", "compose/compose.yaml": "c",
         "systemd/local-ai-llama-swap.service": "u"}
# Root's copies of the units and the Compose project, as `make install-units` installed them.
ROOTS = {"compose/compose.yaml": "c", "systemd/local-ai-llama-swap.service": "u"}
# The registry apply deployed last and the one it deploys: the same, unless a test says otherwise (Task 31: the front
# restarts for a change to its key groups, which it reads only at start).
REGISTRY = load_registry(Path(__file__).parent / "fixtures/models.yaml")


def seeded(etc):
    for rel, content in FILES.items():
        (etc / rel).parent.mkdir(parents=True, exist_ok=True)
        (etc / rel).write_text(content)
    return etc


def run_apply(etc, files, *, installed=ROOTS, unreadable=(), outdated=(), active=(LLAMA, BRAKE, COMPOSE, FRONT, GATE),
              app_changes=(), running=(), now_ok=False, dry_run=False, fails=(), stays_up=None,
              registry_old=REGISTRY, registry_new=REGISTRY, restart_front=False, recheck=None):
    ran, logs, synced = [], [], []

    def run_cmd(cmd):  # systemctl, as a stand-in: a unit in `fails` doesn't restart
        if cmd[-1] in fails:
            raise subprocess.CalledProcessError(1, cmd)
        ran.append(cmd)

    code = apply_files(files, etc, installed=dict(installed), unreadable=set(unreadable), outdated=list(outdated),
                       active=lambda unit: unit in active, app_changes=list(app_changes),
                       running=None if running is None else list(running), now_ok=now_ok, dry_run=dry_run,
                       sync_app=lambda: synced.append(True), run_cmd=run_cmd, log=logs.append, stays_up=stays_up,
                       registry_old=registry_old, registry_new=registry_new, restart_front=restart_front,
                       recheck=recheck)
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


def test_an_app_change_is_synced_and_restarts_the_brake_and_the_gate(tmp_path):
    # Task 31: the gate imports the app too, so it restarts with the brake, after it.
    code, ran, _, synced = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/brake.py"])
    assert code == 0 and synced == [True]
    assert ran == [["systemctl", "restart", BRAKE], ["systemctl", "restart", GATE]]


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
    assert ran == [["systemctl", "restart", GATE], ["systemctl", "restart", LLAMA]]  # the other restarts still happen
    assert (tmp_path / "models.yaml").read_text() == "m2"
    assert ("apply: restarting local-ai-brake.service failed, and its new files are deployed: see "
            "`make logs s=brake`, and once it's fixed, `systemctl restart local-ai-brake.service`") in logs


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
    # pyproject.toml is the app's environment, which the front runs too (Task 31).
    assert logs[-1] == f"apply: dry run — would restart {BRAKE}, {GATE}, {FRONT}, {COMPOSE}"


def test_a_dry_run_shows_what_it_would_stage_and_changes_nothing(tmp_path):
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES | {"compose/compose.yaml": "c2"}, dry_run=True)
    assert code == 0 and ran == [] and synced == []
    assert (tmp_path / "compose/compose.yaml").read_text() == "c"
    assert "compose/compose.yaml differs from root's copy" in "\n".join(logs)
    assert "make install-units" in logs[-1]


def test_restart_mapping():
    # Task 31: units_to_restart's cases, as plan_restarts has them, the gate beside the brake.
    def restarts(changed=(), app=()):
        plan = plan_restarts(list(changed), list(app), REGISTRY, REGISTRY, [], restart_front=False,
                             active=lambda unit: True)
        return plan.now + plan.after_quiet

    assert restarts(["models.yaml"]) == [BRAKE, GATE]
    assert restarts(["llama-swap.yaml"]) == [LLAMA]
    assert restarts(app=["src/spark/brake.py"]) == [BRAKE, GATE]
    # Root's files restart nothing by themselves: a unit restarts once root's copy is installed.
    assert restarts(["compose/searxng/settings.yml", "systemd/local-ai-brake.service"]) == []


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
    # Task 7's fix round 2 (M3): the line names both reasons its files can be newer, root's copies and apply's own.
    assert logs == [f"apply: can't read when {BRAKE} started (InactiveExitTimestamp='@1790371186'), so it can't "
                    f"tell whether {BRAKE} runs its latest files: if they changed since it started "
                    f"(`make install-units`, or an apply that didn't restart it), run `systemctl restart {BRAKE}`"]
    # Still running, so a changed config restarts it; but no file, however new, counts as newer.
    assert outdated_units(started, {"systemd/local-ai-brake.service": 9e9, "models.yaml": 9e9, "app": 9e9}) == []
    # Restarting llama-swap by hand stops every loaded model, so its line says when to.
    logs.clear()
    start_times([LLAMA], {LLAMA: shows[BRAKE]}.get, log=logs.append)
    assert logs == [f"apply: can't read when {LLAMA} started (InactiveExitTimestamp='@1790371186'), so it can't "
                    f"tell whether {LLAMA} runs its latest files: if they changed since it started "
                    f"(`make install-units`, or an apply that didn't restart it), run `systemctl restart {LLAMA}` "
                    "once no model is loaded (restarting it stops them all)"]


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
    env = validation_env({"PATH": "/usr/bin", "LLAMASWAP_KEY_GATE": "real"})
    assert env["PATH"] == "/usr/bin"
    values = [env[name] for name in INTERNAL_KEY_ENVS]
    assert len(set(values)) == len(INTERNAL_KEY_ENVS) and "real" not in values


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
                        f"stops every model; with --now it would restart {BRAKE}, {GATE}, {LLAMA}")


def test_the_staging_stop_says_nothing_but_the_staging(tmp_path):
    # Minor 5: at that stop nothing else is deployed, so nothing else is listed as changing. Task 31: the diff it
    # shows is of root's files only, the unit's installed copy against the one it stages.
    new = FILES | {"systemd/local-ai-llama-swap.service": "u2", "llama-swap.yaml": "b"}
    for dry_run in (False, True):
        etc = seeded(tmp_path / str(dry_run))
        _, _, logs, _ = run_apply(etc, new, app_changes=["src/spark/brake.py"], outdated=[COMPOSE], dry_run=dry_run)
        assert logs[:-1] == ["apply: systemd/local-ai-llama-swap.service differs from root's copy "
                             "(/etc/systemd/system/local-ai-llama-swap.service)",
                             "--- installed: /etc/systemd/system/local-ai-llama-swap.service",
                             f"+++ staged: {etc}/systemd/local-ai-llama-swap.service",
                             "@@ -1 +1 @@", "-u", "+u2"]
        assert "make install-units" in logs[-1]


def test_a_llama_swap_whose_unit_is_stopped_is_never_refused_for(tmp_path):
    # Minor 6: however /running answered, a stopped unit has nothing loaded, and apply restarts nothing that's stopped.
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, active=(BRAKE, COMPOSE),
                                   running=None)
    assert code == 0 and ran == [] and (tmp_path / "llama-swap.yaml").read_text() == "b"
    assert logs[-1] == f"apply: {LLAMA} isn't running; it starts with the new config"


def test_each_file_apply_deploys_belongs_to_the_units_that_run_on_it():
    # Task 31: the gate reads the registry and imports the app, as the brake does; the front's own files are its own.
    rels = ("llama-swap.yaml", "models.yaml", spark_apply.APP, spark_apply.FRONT_APP, "compose/searxng/settings.yml",
            "systemd/local-ai-brake.service", "systemd/local-ai-front.service", "systemd/local-ai-gate.service",
            "systemd/local-ai-pull.service", "systemd/local-ai-front.socket", "systemd/local-ai-notify@.service")
    assert [spark_apply.units_of(rel) for rel in rels] == [(LLAMA,), (BRAKE, GATE), (BRAKE, GATE), (FRONT,),
                                                          (COMPOSE,), (BRAKE,), (FRONT,), (GATE,), (), (), ()]


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

    def wait(*answers, timeout=30):
        now[0], slept[:] = 0.0, []
        return spark_apply.wait_for_running(Answers(*answers), timeout, sleep=sleep, clock=lambda: now[0])

    down = LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: connection refused")
    assert wait(down, down, []) is None and slept == [1.0, 1.0]
    assert wait(down, timeout=2.5) == ("llama-swap didn't answer GET /running within 2.5 s "
                                      f"({down}): see `make logs s=llama-swap`")
    assert slept == [1.0, 1.0, 0.5]  # never past its time
    # Task 7's fix round 2 (M2): an answer, even an error, says llama-swap is up, and no wait changes it. Nor does one
    # for a request that was never sent. So neither is waited out.
    refused = llamaswap.LlamaSwapAnswered("llama-swap GET /running: HTTP 401")
    assert wait(down, refused) == (f"llama-swap answered GET /running with an error ({refused}): it's up, but apply "
                                   "can't confirm it serves the new config")
    assert slept == [1.0]
    unsent = LlamaSwapError("llama-swap GET /running: the API key isn't printable ASCII (a stray CR or LF?), so it "
                            "wasn't sent")
    assert wait(unsent) == (f"apply couldn't ask llama-swap GET /running ({unsent}), so it can't confirm llama-swap "
                            "serves the new config")
    assert slept == []


# Phase 1's council (reliability I1): Type=exec counts the brake as started once its Python runs, before the app is
# imported, so a brake that fails once it runs passes `systemctl restart` and then restarts every 2 s.

UP = "ActiveState=active\nSubState=running\nNRestarts=0\n"


@pytest.mark.parametrize("after, said", [
    pytest.param(UP, None, id="stays-up"),
    pytest.param("ActiveState=activating\nSubState=auto-restart\nNRestarts=1\n",
                 "it isn't running 3 s later (activating, auto-restart): see `make logs s=brake`",
                 id="between-restarts"),
    pytest.param("ActiveState=failed\nSubState=failed\nNRestarts=5\n",
                 "it isn't running 3 s later (failed, failed): see `make logs s=brake`", id="gave-up"),
    pytest.param("ActiveState=active\nSubState=running\nNRestarts=1\n",
                 "systemd restarted it again within 3 s, so it stops once it runs: see `make logs s=brake`",
                 id="running-again"),
    pytest.param("ActiveState=active\nSubState=running\n",
                 "apply can't tell whether it stayed up, since systemd gave no restart count: "
                 "`systemctl status local-ai-brake.service`", id="no-count"),
])
def test_after_restarting_the_brake_apply_checks_it_stays_up(after, said):
    shows, slept = iter([UP, after]), []
    assert spark_apply.check_stays_up(BRAKE, show=lambda unit: next(shows), sleep=slept.append) == said
    assert slept == [spark_apply.STAY_UP_S] == [3.0]  # longer than the unit's RestartSec, 2 s


def test_a_brake_that_doesnt_stay_up_after_its_restart_is_said(tmp_path):
    asked = []

    def stays_up(unit):
        asked.append(unit)
        return "it isn't running 3 s later (activating, auto-restart): see `make logs s=brake`"

    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"},
                                   app_changes=["src/spark/brake.py"], stays_up=stays_up)
    assert code == 1 and asked == [BRAKE]  # not llama-swap, which apply asks /running instead, nor the gate (notify)
    assert ran == [["systemctl", "restart", BRAKE], ["systemctl", "restart", GATE], ["systemctl", "restart", LLAMA]]
    assert logs[-1] == (f"apply: restarted {BRAKE}, but it isn't running 3 s later (activating, auto-restart): see "
                        "`make logs s=brake`")


@pytest.mark.parametrize("how", ["restart fails", "dry run", "not running"])
def test_only_a_brake_apply_restarted_is_checked(tmp_path, how):
    asked = []
    run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/brake.py"], stays_up=asked.append,
              fails=(BRAKE,) if how == "restart fails" else (), dry_run=how == "dry run",
              active=(LLAMA, COMPOSE) if how == "not running" else (LLAMA, BRAKE, COMPOSE))
    assert asked == []


def test_validate_gets_path_and_a_placeholder_for_each_key_and_nothing_else():
    # Minor 9: Dan's environment can hold secrets of his own, and -validate needs none of it.
    placeholders = {name: f"validate-only-{i}" for i, name in enumerate(INTERNAL_KEY_ENVS)}
    assert validation_env({"PATH": "/usr/bin", "HOME": "/home/dan", "SPARK_API_KEY": "k"}) == {"PATH": "/usr/bin",
                                                                                              **placeholders}
    assert validation_env({}) == {"PATH": os.defpath, **placeholders}


ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures"
OLD = time.time() - 3600  # when a finished apply wrote its files, and `make install-units` installed root's copies
T0 = OLD + 600  # when the running units started
# The registry a finished apply deployed before this change: one that loads, with the fixture's key groups, so its
# change restarts the brake and the gate and never the front (Task 31). One that won't load is
# test_an_unreadable_deployed_registry_restarts_the_front_too's.
OLDER_REGISTRY = (FIX / "models.yaml").read_text() + "# the registry before this change\n"


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
        shutil.copytree(ROOT / "stack/llama-swap", self.repo / "stack/llama-swap")  # the schema render checks against
        shutil.copy(FIX / "models.yaml", self.repo / "stack/models.yaml")
        pinned = (FIX / "versions.yaml").read_text().replace("pin: null", "pin: sha256:" + "0" * 64)
        (self.repo / "stack/versions.yaml").write_text(pinned)
        for rel, text in {"pyproject.toml": "p", "uv.lock": "l", "src/spark/cli.py": "v1"}.items():
            put(self.repo / "spark" / rel, text, OLD)
        put(self.opt / "bin/llama-swap/v257/llama-swap", "", OLD)
        self.units = dict.fromkeys(RUNNING_UNITS)
        self.answers = [LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: connection refused")]
        self.interrupted, self.failing, self.uv_fails = set(), set(), False
        self.validate_code, self.validate_env, self.uv_env = 0, None, None
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
        # What systemd says of the brake after its restart, one answer per look, the last repeated: it stays up.
        self.brake_shows = ["ActiveState=active\nSubState=running\nNRestarts=0\n"]
        monkeypatch.setattr(spark_apply, "_show_restarts", self.show_restarts)
        monkeypatch.setattr(spark_apply, "STAY_UP_S", 0.0)  # no test waits
        monkeypatch.chdir(self.repo)

    def show_restarts(self, unit):
        assert unit == BRAKE, unit
        return self.brake_shows.pop(0) if len(self.brake_shows) > 1 else self.brake_shows[0]

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
            if cmd[2] in self.failing:  # systemctl exits 1, which only check=True turns into an error, as for real
                if kwargs.get("check"):
                    raise subprocess.CalledProcessError(1, cmd)
                return subprocess.CompletedProcess(cmd, 1)
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
    said = [line for line in lines[:-1] if line.startswith("apply: ")]  # Task 31: the diff follows them
    assert [line.split(" yet (")[0] for line in said] == [f"apply: root has no copy of {rel}" for rel in roots]
    assert [line for line in lines if line.startswith("+++ ")] == [f"+++ staged: {box.etc}/{rel}" for rel in roots]
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
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": OLDER_REGISTRY}, running=False)
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "uv sync", "write llama-swap.yaml", "write models.yaml"])
    assert lines[-3:] == [f"apply: {BRAKE} isn't running; it starts with the new config",
                          f"apply: {GATE} isn't running; it starts with the new config",
                          f"apply: {LLAMA} isn't running; it starts with the new config"]


def test_run_passes_dry_run_on_and_a_dry_run_changes_nothing(box):
    # Task 7's fix round 2 (M1): `make apply-dry-run` goes through run(). It writes, syncs and restarts nothing, and
    # exits as the real run would: 0 when idle, 1 when the real run would refuse.
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": OLDER_REGISTRY})
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    box.answers = [[]]
    code, lines, events = box.apply("--dry-run")
    assert (code, events) == (0, ["-validate", "GET /running"])
    assert lines[-1] == f"apply: dry run — would restart {BRAKE}, {GATE}, {LLAMA}"
    assert (box.etc / "llama-swap.yaml").read_text() == "old config"
    assert (box.app / "src/spark/cli.py").read_text() == "v1" and box.units == dict.fromkeys(RUNNING_UNITS, T0)
    box.answers = [[Running("coder", "ready")]]
    code, lines, events = box.apply("--dry-run")
    assert (code, events) == (1, ["-validate", "GET /running"])
    assert lines[-1] == ("apply: dry run — would refuse: models are loaded (coder), and restarting llama-swap stops "
                         f"every model; with --now it would restart {BRAKE}, {GATE}, {LLAMA}")


@pytest.mark.parametrize("stopped", [False, True], ids=["systemd refused it", "it couldn't start"])
def test_run_says_when_a_restart_fails_and_the_next_run_does_what_it_can(box, stopped):
    # Task 7's fix round 2 (M1): the failed-restart path through run(), whose systemctl must run with check=True: a job
    # that fails exits 1, and only check turns that into an error (Box's fake does as subprocess.run does). Refused
    # (a session without spark-admin), the brake still runs its older files, so the next run restarts it; unable to
    # start (Type=exec: a binary missing, say), it's stopped, so only the line says how to finish.
    box.deployed(old={"models.yaml": OLDER_REGISTRY})
    box.answers = [[]]
    box.failing = {BRAKE}
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running", "write models.yaml", f"restart {BRAKE}",
                                  f"restart {GATE}"])
    assert (f"apply: restarting {BRAKE} failed, and its new files are deployed: see `make logs s=brake`, "
            f"and once it's fixed, `systemctl restart {BRAKE}`") in lines
    box.failing = set()
    if stopped:
        box.units[BRAKE] = None
    code, lines, events = box.apply()
    if stopped:
        assert (code, events, lines) == (0, ["-validate", "GET /running"], ["apply: nothing to change"])
    else:
        assert (code, events) == (0, ["-validate", "GET /running", f"restart {BRAKE}"])


def test_a_start_time_that_cant_be_read_is_named_after_a_run_that_was_cut_off(box, monkeypatch):
    # Task 7's fix round 2 (M3): a cut-off run wrote llama-swap's config and never restarted it, and now its start time
    # comes in another form. apply can't tell it's outdated, so it says what to run by hand, and why: its line names
    # an apply that didn't restart it, not only `make install-units`.
    box.deployed(old={"llama-swap.yaml": "old config"})
    box.answers = [[]]
    box.interrupted = {LLAMA}
    with pytest.raises(KeyboardInterrupt):
        box.apply()
    box.interrupted = set()
    shows = {unit: systemctl_show(box.units[unit]) for unit in RUNNING_UNITS}
    shows[LLAMA] = "ActiveState=active\nInactiveExitTimestamp=@1790371186\n"
    monkeypatch.setattr(spark_apply, "_show", shows.get)
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running"])
    assert lines == [f"apply: can't read when {LLAMA} started (InactiveExitTimestamp='@1790371186'), so it can't tell "
                     f"whether {LLAMA} runs its latest files: if they changed since it started (`make install-units`, "
                     f"or an apply that didn't restart it), run `systemctl restart {LLAMA}` once no model is loaded "
                     "(restarting it stops them all)",
                     "apply: nothing to change"]


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
    # Until a sync finishes every file of the app counts, the front's among them (Task 31): one that failed part-way
    # may have changed the environment under it.
    assert (code, events) == (0, ["-validate", "GET /running", "uv sync", f"restart {BRAKE}", f"restart {GATE}",
                                  "GET /running", f"restart {FRONT}"])


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
    assert (code, events) == (0, ["-validate", "GET /running", "uv sync", f"restart {BRAKE}", f"restart {GATE}",
                                  "GET /running", f"restart {FRONT}"])  # every file counts until a sync finishes


def test_the_restarts_a_cut_off_run_owed_are_made_by_the_next(box):
    # I2: Ctrl-C after apply wrote llama-swap's config and the registry, before any restart (the brake's is the first,
    # since Task 31's order).
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": OLDER_REGISTRY})
    box.answers = [[]]
    box.interrupted = {BRAKE}
    with pytest.raises(KeyboardInterrupt):
        box.apply()
    assert box.units == dict.fromkeys(RUNNING_UNITS, T0) and (box.etc / "llama-swap.yaml").read_text() != "old config"
    box.interrupted = set()
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", f"restart {BRAKE}", f"restart {GATE}", "GET /running",
                                  f"restart {LLAMA}", "GET /running"])
    for unit in (BRAKE, GATE, LLAMA):
        assert f"apply: {unit} started before its latest files were in place, so it still runs older ones" in lines


def test_apply_says_when_the_brake_it_restarted_doesnt_stay_up(box):
    # Phase 1's council (reliability I1): the app fails once it runs, so the restart passes and the unit loops.
    box.deployed()
    box.answers = [[]]
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    box.brake_shows = [UP, "ActiveState=activating\nSubState=auto-restart\nNRestarts=1\n"]
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running", "uv sync", f"restart {BRAKE}", f"restart {GATE}"])
    assert lines[-1] == (f"apply: restarted {BRAKE}, but it isn't running 0 s later (activating, auto-restart): see "
                         "`make logs s=brake`")  # the Box gives it no time (STAY_UP_S)


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
    assert (code, events) == (0, ["-validate", "GET /running", f"restart {BRAKE}", f"restart {GATE}"])


def test_llama_swap_restarts_last_and_is_asked_again_just_before(box):
    # I3: nothing else between the second look and llama-swap's restart, not compose's restart of up to 15 minutes.
    # Phase 1 restarted llama-swap first; since Task 31 it goes last, after the brake and the gate, and the compose
    # unit restarts on its own after it.
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": OLDER_REGISTRY})
    os.utime(installed_path("compose/compose.yaml"), (T0 + 60, T0 + 60))  # installed after compose started
    box.answers = [[]]
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "write llama-swap.yaml", "write models.yaml",
                                  f"restart {BRAKE}", f"restart {GATE}", "GET /running", f"restart {LLAMA}",
                                  "GET /running", f"restart {COMPOSE}"])


@pytest.mark.parametrize("second, said", [
    ([Running("coder", "ready")], "models are now loaded (coder)"),
    (LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: timed out"),
     "can't tell which models are loaded"),
], ids=["a model loaded meanwhile", "no answer the second time"])
def test_llama_swap_keeps_running_when_the_second_look_isnt_idle(box, second, said):
    # I3: a model that starts loading while apply syncs and writes is seen, and not stopped. The files are deployed
    # and the other restarts made; llama-swap's restart waits for the next run, which owes it (I2).
    box.deployed(old={"llama-swap.yaml": "old config", "models.yaml": OLDER_REGISTRY})
    box.answers = [[], second]
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running", "write llama-swap.yaml", "write models.yaml",
                                  f"restart {BRAKE}", f"restart {GATE}", "GET /running"])
    assert lines[-1] == (f"apply: didn't restart {LLAMA}: {said}, and restarting it stops every model; its new files "
                         "are deployed, so re-run when idle, or with --now")
    box.answers = [[]]
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "GET /running", f"restart {LLAMA}", "GET /running"])


def test_apply_says_when_a_restarted_llama_swap_doesnt_answer(box):
    # The plan's rule: every config change is followed by a start and GET /running, not just -validate.
    box.deployed(old={"llama-swap.yaml": "old config"})
    down = LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: connection refused")
    box.answers = [[], [], down]
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running", "write llama-swap.yaml", "GET /running",
                                  f"restart {LLAMA}", "GET /running"])
    assert lines[-1] == (f"apply: restarted {LLAMA}, but llama-swap didn't answer GET /running within 0 s ({down}): "
                         "see `make logs s=llama-swap`")  # the Box gives it no time (READY_SECONDS)


def test_apply_says_when_a_restarted_llama_swap_answers_with_an_error(box):
    # Task 7's fix round 2 (M2): an answer, even a 401 from a wrong or missing key, shows llama-swap is up. apply says
    # that, and exits 1, instead of saying it didn't answer.
    box.deployed(old={"llama-swap.yaml": "old config"})
    refused = llamaswap.LlamaSwapAnswered("llama-swap GET /running: HTTP 401")
    box.answers = [[], [], refused]
    code, lines, events = box.apply()
    assert (code, events) == (1, ["-validate", "GET /running", "write llama-swap.yaml", "GET /running",
                                  f"restart {LLAMA}", "GET /running"])
    assert lines[-1] == (f"apply: restarted {LLAMA}, but llama-swap answered GET /running with an error ({refused}): "
                         "it's up, but apply can't confirm it serves the new config")


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
    # traceback would print it. It's the test's own (conftest.py's fake_environment, which keeps TZ when the run sets
    # one). Names only, taken first: pytest prints the arguments of a call in a failing assert.
    box.deployed()
    box.answers = [[]]
    put(box.repo / "spark/src/spark/cli.py", "v2", time.time())
    box.apply()
    names = set(box.uv_env) - {"PYTEST_CURRENT_TEST"}
    assert names <= {"PATH", "HOME", "LANG", "TZ", "UV_PYTHON_INSTALL_DIR", "UV_LINK_MODE"}


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
    assert (code, events) == (0, ["-validate", "GET /running", "uv sync", f"restart {BRAKE}", f"restart {GATE}"])
    assert not (box.app / "src/spark/retired").exists()
    assert (box.app / "src/spark/cli.py").read_text() == "v2" and (box.app / "src/spark/cli.py").stat().st_ino != before
    assert (box.app / ".venv/bin/spark").exists() and (box.app / spark_apply.SYNC_STAMP).exists()


# Task 31: the diff, and what each change restarts. llama-swap's restart and the front's wait for the quiet moment (Task
# 32's wait; until it is built, apply makes them only while no model is loaded, or with --now); the brake's and the
# gate's are made at once, and the compose unit's on its own, as in Phase 1.


def plan(changed=(), app=(), *, old=REGISTRY, new=REGISTRY, outdated=(), restart_front=False,
         active=lambda unit: True):
    return plan_restarts(list(changed), list(app), old, new, list(outdated), restart_front=restart_front, active=active)


def registry_of(tmp_path, *edits):
    """The fixture registry with each (old, new) edit made, each of which must change it once, loaded."""
    text = (FIX / "models.yaml").read_text()
    for old, new in edits:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path = tmp_path / "models.yaml"
    path.write_text(text)
    return load_registry(path)


def test_apply_shows_a_diff_of_what_it_would_change(tmp_path):
    etc = seeded(tmp_path / "config")
    (etc / "llama-swap.yaml").write_text("healthCheckTimeout: 600\nttl: 0\n")
    code, ran, logs, _ = run_apply(etc, FILES | {"llama-swap.yaml": "healthCheckTimeout: 180\nttl: 0\n"})
    assert code == 0 and ran == [["systemctl", "restart", LLAMA]]
    start = logs.index(f"--- deployed: {etc}/llama-swap.yaml")
    assert logs[start:start + 6] == [f"--- deployed: {etc}/llama-swap.yaml", "+++ rendered: llama-swap.yaml",
                                     "@@ -1,2 +1,2 @@", "-healthCheckTimeout: 600", "+healthCheckTimeout: 180",
                                     " ttl: 0"]
    assert [line for line in logs if line.startswith(("--- ", "+++ "))] == logs[start:start + 2]  # only what changes
    # A changed unit: root's installed copy against the copy apply stages for `make install-units`.
    unit = "systemd/local-ai-llama-swap.service"
    etc = seeded(tmp_path / "unit")
    code, ran, logs, _ = run_apply(etc, FILES | {unit: "[Service]\nRestartSec=5\n"},
                                   installed=ROOTS | {unit: "[Service]\nRestartSec=2\n"})
    assert code == 0 and ran == []
    assert logs[1:7] == ["--- installed: /etc/systemd/system/local-ai-llama-swap.service", f"+++ staged: {etc}/{unit}",
                         "@@ -1,2 +1,2 @@", " [Service]", "-RestartSec=2", "+RestartSec=5"]


def test_the_diff_shows_a_new_file_whole_and_escapes_control_characters(tmp_path):
    # A file with no copy yet diffs against nothing. A control character is shown escaped, as `make install-units`
    # refuses one, so no line of the diff can hide another on the terminal.
    (tmp_path / "models.yaml").write_text("m\n")  # unchanged: no diff
    files = {"llama-swap.yaml": "ttl: 0\n", "models.yaml": "m\n", "systemd/local-ai-gate.service": "a\x1b[2Kb\n"}
    text = diff_text(files, tmp_path, {"systemd/local-ai-gate.service": "a\r\n"})
    assert text.splitlines() == ["--- /dev/null", "+++ rendered: llama-swap.yaml", "@@ -0,0 +1 @@", "+ttl: 0",
                                 "--- installed: /etc/systemd/system/local-ai-gate.service",
                                 f"+++ staged: {tmp_path}/systemd/local-ai-gate.service", "@@ -1 +1 @@", "-a\\x0d",
                                 "+a\\x1b[2Kb"]
    assert diff_text({"models.yaml": "m\n"}, tmp_path, {}) == ""


def test_apply_names_each_file_of_the_app_it_changes(tmp_path):
    code, _, logs, _ = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/cli.py", "src/spark/gate/core.py"])
    assert code == 0 and logs[:2] == ["apply: changes app/src/spark/cli.py", "apply: changes app/src/spark/gate/core.py"]


def test_a_change_to_the_gates_code_restarts_the_gate_and_the_brake_at_once():
    assert plan(app=["src/spark/gate/admission.py"]) == RestartPlan(now=[BRAKE, GATE], after_quiet=[])


@pytest.mark.parametrize("rel", ["src/spark/leakcheck.py", "src/spark/gate/core.py", "src/spark/brake.py",
                                 "tests/test_front_forward.py"])
def test_a_change_elsewhere_in_the_app_never_restarts_the_front(rel):
    assert plan(app=[rel]) == RestartPlan(now=[BRAKE, GATE], after_quiet=[])


@pytest.mark.parametrize("rel", ["src/spark/front/parse.py", "src/spark/messages.py", "src/spark/front/__init__.py",
                                 "src/spark/__init__.py"])
def test_a_change_to_the_fronts_modules_restarts_it_after_the_quiet_moment(rel):
    assert plan(app=[rel]) == RestartPlan(now=[BRAKE, GATE], after_quiet=[FRONT])


@pytest.mark.parametrize("rel", ["uv.lock", "pyproject.toml", ".python-version"])
def test_a_change_to_the_apps_environment_restarts_the_front_too(rel):
    # uvicorn and httpx are code the front runs too: a bump changes it, and `uv sync` replaces them under it.
    assert plan(app=[rel]) == RestartPlan(now=[BRAKE, GATE], after_quiet=[FRONT])


def test_every_front_module_is_a_file_whose_change_restarts_the_front():
    from spark.front import FRONT_MODULES
    files = [path.relative_to(ROOT / "spark").as_posix() for path in (ROOT / "spark/src").rglob("*.py")]
    fronts = [rel for rel in files if spark_apply.front_file(rel)]
    assert sorted(spark_apply.module_of(rel) for rel in fronts) == sorted(FRONT_MODULES)


@pytest.mark.parametrize("edit", [
    pytest.param(("roles: [coder]", "roles: [coder, agentic]"), id="a role added"),
    pytest.param(("\n  coder:\n", "\n  big-coder:\n"), id="a model renamed"),
    pytest.param(("footprint_gib: 28", "footprint_gib: 30"), id="a footprint changed"),
])
def test_a_change_to_the_registrys_names_or_roles_never_restarts_the_front(tmp_path, edit):
    # They reach the front through the gate's events (Dan's decision, 2026-10-07, after the forward-and-back council).
    assert plan(["models.yaml"], new=registry_of(tmp_path, edit)) == RestartPlan(now=[BRAKE, GATE], after_quiet=[])


@pytest.mark.parametrize("edit", [("wait_s: 600", "wait_s: 900"), ("max_waiting: 8", "max_waiting: 6")],
                         ids=["a wait", "a cap"])
def test_a_change_to_the_registrys_key_groups_restarts_the_front_after_the_quiet_moment(tmp_path, edit):
    # The front reads the key groups only at start, for each key's wait and caps (spark.front.main).
    assert plan(["models.yaml"], new=registry_of(tmp_path, edit)) == RestartPlan(now=[BRAKE, GATE],
                                                                                 after_quiet=[FRONT])


def test_a_deployed_registry_that_wont_load_restarts_the_front_too():
    # One apply can't load (an older shape, say) may have had other key groups.
    assert plan(["models.yaml"], old=None) == RestartPlan(now=[BRAKE, GATE], after_quiet=[FRONT])
    assert plan([], old=None) == RestartPlan(now=[], after_quiet=[])  # unless its text didn't change


def test_llama_swaps_restart_waits_for_the_quiet_moment():
    assert plan(["llama-swap.yaml"]) == RestartPlan(now=[], after_quiet=[LLAMA])
    assert plan(outdated=[LLAMA]) == RestartPlan(now=[], after_quiet=[LLAMA])  # its unit, once root's copy is installed


def test_restart_front_waits_too():
    # `make restart-front`, after a change of the key digests or the key list, which apply doesn't deploy. The gate
    # reads the key list at start too (spark.gate.main), so it restarts with the front, before it.
    assert plan(restart_front=True) == RestartPlan(now=[GATE], after_quiet=[FRONT])


def test_a_unit_whose_new_definition_is_installed_restarts_as_its_files_would():
    assert plan(outdated=[FRONT, GATE, COMPOSE, LLAMA, BRAKE]) == RestartPlan(now=[BRAKE, GATE, COMPOSE],
                                                                              after_quiet=[FRONT, LLAMA])


def test_a_unit_that_isnt_running_is_never_restarted(tmp_path):
    def running(unit):
        return unit != FRONT

    assert plan(app=["src/spark/front/parse.py"], active=running) == RestartPlan(now=[BRAKE, GATE], after_quiet=[],
                                                                                 not_running=[FRONT])
    assert plan(restart_front=True, active=running) == RestartPlan(now=[GATE], after_quiet=[], not_running=[FRONT])
    # apply never starts it: it starts with its new files.
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES, app_changes=["src/spark/front/parse.py"],
                                   active=(LLAMA, BRAKE, COMPOSE, GATE))
    assert code == 0 and ran == [["systemctl", "restart", BRAKE], ["systemctl", "restart", GATE]]
    assert logs[-1] == f"apply: {FRONT} isn't running; it starts with the new config"


def test_restarts_follow_a_fixed_order():
    for units in itertools.permutations([LLAMA, FRONT, GATE, BRAKE]):
        assert restart_order(list(units)) == [BRAKE, GATE, FRONT, LLAMA]
    # The compose unit restarts on its own, after them, as in Phase 1: its start can take minutes.
    assert restart_order([COMPOSE, LLAMA, BRAKE]) == [BRAKE, LLAMA, COMPOSE]


def test_apply_restarts_in_that_order_and_looks_again_before_each_restart_that_waits(tmp_path):
    events = []
    code = apply_files(FILES | {"llama-swap.yaml": "b", "models.yaml": "m2"}, seeded(tmp_path), installed=ROOTS,
                       unreadable=set(), outdated=[COMPOSE], active=lambda unit: True,
                       app_changes=["src/spark/front/app.py"], running=[], now_ok=False, dry_run=False,
                       sync_app=lambda: events.append("sync"), run_cmd=lambda cmd: events.append(cmd[-1]),
                       log=lambda line: None, registry_old=REGISTRY, registry_new=REGISTRY,
                       recheck=lambda: events.append("look") or [])
    assert code == 0 and events == ["sync", BRAKE, GATE, "look", FRONT, "look", LLAMA, COMPOSE]


def test_until_the_quiet_wait_the_fronts_restart_waits_for_no_model_loaded_or_now(tmp_path):
    # Task 32 builds the wait. Until then the front's restart, which cuts off every request in flight, goes ahead as
    # llama-swap's does in Phase 1: with no model loaded, or with --now.
    front = ["src/spark/front/app.py"]
    code, ran, logs, synced = run_apply(seeded(tmp_path), FILES, app_changes=front, running=["coder"])
    assert (code, ran, synced) == (1, [], [])
    assert logs[-1] == ("apply: models are loaded (coder), and restarting the front cuts off every request in flight; "
                        "re-run when idle, or with --now (nothing was changed)")
    code, _, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"}, app_changes=front, running=None)
    assert code == 1 and logs[-1] == ("apply: can't tell which models are loaded, and restarting the front cuts off "
                                      "every request in flight, and restarting llama-swap stops every model; re-run "
                                      "when idle, or with --now (nothing was changed)")
    restarted = [["systemctl", "restart", unit] for unit in (BRAKE, GATE, FRONT)]
    assert run_apply(seeded(tmp_path), FILES, app_changes=front, running=["coder"], now_ok=True)[:2] == (0, restarted)
    assert run_apply(seeded(tmp_path), FILES, app_changes=front, running=[])[:2] == (0, restarted)
    # A stopped llama-swap has nothing loaded, whatever /running said.
    assert run_apply(seeded(tmp_path), FILES, app_changes=front, running=None,
                     active=(BRAKE, COMPOSE, FRONT, GATE))[:2] == (0, restarted)


def test_a_model_loaded_before_the_fronts_restart_holds_back_every_restart_that_waits(tmp_path):
    looks = iter([["coder"]])  # one look only: once one is held back, so is the rest
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"llama-swap.yaml": "b"},
                                   app_changes=["src/spark/front/app.py"], recheck=lambda: next(looks))
    assert code == 1 and ran == [["systemctl", "restart", BRAKE], ["systemctl", "restart", GATE]]
    assert logs[-2:] == [f"apply: didn't restart {FRONT}: models are now loaded (coder), and restarting it cuts off "
                         "every request in flight; once no model is loaded, `make restart-front` restarts it",
                         f"apply: didn't restart {LLAMA}: models are now loaded (coder), and restarting it stops every "
                         "model; its new files are deployed, so re-run when idle, or with --now"]


def test_the_gate_and_the_front_are_outdated_by_their_own_files():
    started = dict.fromkeys(RUNNING_UNITS, 100.0)
    assert outdated_units(started, {"models.yaml": 100.25}) == [BRAKE, GATE]
    assert outdated_units(started, {spark_apply.APP: 100.25}) == [BRAKE, GATE]
    assert outdated_units(started, {spark_apply.FRONT_APP: 100.25}) == [FRONT]
    assert outdated_units(started, {"systemd/local-ai-front.service": 100.25,
                                    "systemd/local-ai-gate.service": 100.25}) == [FRONT, GATE]
    assert outdated_units(started, {"systemd/local-ai-front.socket": 100.25}) == []  # apply restarts no socket


def test_the_fronts_files_are_dated_by_their_last_write(tmp_path):
    etc, app = tmp_path / "etc", tmp_path / "app"
    put(app / "src/spark/front/parse.py", "x", 200.0)
    put(app / "uv.lock", "x", 250.0)
    put(app / "src/spark/gate/core.py", "x", 400.0)  # not the front's
    put(app / ".venv/lib/python3.12/site-packages/uvicorn/main.py", "x", 500.0)  # uv's, not the app's
    assert spark_apply.deployed_times(etc, app) == {spark_apply.FRONT_APP: 250.0}


def test_run_restarts_the_front_for_its_own_modules_and_a_cut_off_run_leaves_it_owed(box):
    box.deployed()
    box.answers = [[]]
    put(box.repo / "spark/src/spark/front/parse.py", "v2", time.time())
    box.interrupted = {FRONT}
    with pytest.raises(KeyboardInterrupt):
        box.apply()
    assert box.events == ["-validate", "GET /running", "uv sync", f"restart {BRAKE}", f"restart {GATE}",
                          "GET /running", f"restart {FRONT}"]
    box.interrupted = set()
    code, lines, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "GET /running", f"restart {FRONT}"])
    assert f"apply: {FRONT} started before its latest files were in place, so it still runs older ones" in lines


@pytest.mark.parametrize("old", [OLDER_REGISTRY.replace("wait_s: 600", "wait_s: 900"), "an older shape: [\n"],
                         ids=["other key groups", "one that won't load"])
def test_run_restarts_the_front_for_the_deployed_registrys_key_groups(box, old):
    box.deployed(old={"models.yaml": old})
    box.answers = [[]]
    code, _, events = box.apply()
    assert (code, events) == (0, ["-validate", "GET /running", "write models.yaml", f"restart {BRAKE}",
                                  f"restart {GATE}", "GET /running", f"restart {FRONT}"])


def test_run_says_renders_warnings_before_its_diff(box, monkeypatch):
    # The controller's note at Task 5: only `spark render` and CI showed them.
    monkeypatch.setattr(spark_apply, "check_budget", lambda registry: ["every model loaded at once leaves 20 GiB"])
    box.deployed(old={"llama-swap.yaml": "old config\n"})
    box.answers = [[]]
    code, lines, _ = box.apply("--dry-run")
    assert code == 0
    assert lines.index("apply: warning — every model loaded at once leaves 20 GiB") < lines.index(
        f"--- deployed: {box.etc}/llama-swap.yaml")


def test_make_restart_front_is_apply_restarting_the_front(box):
    makefile = (ROOT / "Makefile").read_text()
    assert re.search(r"^restart-front: ## On the Spark: .*\n\t\$\(SPARK\) apply --restart local-ai-front\.service$",
                     makefile, re.M)
    assert "restart-front" in re.search(r"^\.PHONY: (.*)$", makefile, re.M).group(1).split()
    # Through run(), with nothing else changed: the gate, then the front.
    box.deployed()
    box.answers = [[]]
    code, _, events = box.apply("--restart", FRONT)
    assert (code, events) == (0, ["-validate", "GET /running", f"restart {GATE}", "GET /running", f"restart {FRONT}"])
    with pytest.raises(SystemExit):
        cli.main(["apply", "--restart", BRAKE])  # the front's only


def test_restart_front_stages_roots_files_first_and_says_to_run_it_again(tmp_path):
    code, ran, logs, _ = run_apply(seeded(tmp_path), FILES | {"systemd/local-ai-llama-swap.service": "u2"},
                                   restart_front=True)
    assert code == 0 and ran == [] and logs[-1].endswith("then run `make restart-front` again")
