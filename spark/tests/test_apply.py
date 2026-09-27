import socket
import subprocess
from datetime import datetime, timezone

import pytest

from spark.apply import (UNKNOWN_START, app_diff, apply_files, diff_tree, models_loaded, not_installed,
                         outdated_units, read_copies, start_times, started_at, units_to_restart, validation_env)
from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, Running
from spark.render import KEY_ENVS

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
    # The next apply finds nothing to change, so this is the one time to say which unit still needs it.
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
