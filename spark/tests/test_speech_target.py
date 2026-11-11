"""make speech's script, stack/host/by-hand-speech: the speech service in its locked-down container, by hand (Dan's
decision, 2026-11-09; website/design/speech-service.md).

TEMPORARY: remove at the speech service's Stage 2. PIECES, below, lists every piece and what of it goes.

The script uses sudo and starts a container, so it never runs here for real: every tool it calls that acts on the box is
a stand-in on PATH that logs its call and changes only the test's own files, and the box's paths are the test's folders
(BY_HAND_OPT, BY_HAND_RUN_DIR, HOME)."""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "stack" / "host" / "by-hand-speech"
ASR = "7278e1e70fe206f11671096ffdd38061171dd6e5"
ALIGNER = "c7cbfc2048c462b0d63a45797104fc9db3ad62b7"
IMAGE = "ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852"

# TEMPORARY: every piece of make speech, and what of it goes at Stage 2: a search for NAMES finds only these files.
PIECES = {
    "Makefile": "the target, its comment, its .PHONY name, its script in lint's list and make tunnel's second forward",
    "stack/host/by-hand-speech": "the file",
    "spark/tests/test_speech_target.py": "the file",
    "stack/speech/serve.py": "the words naming it, when Stage 2 changes what starts the service",
    "stack/speech/pyproject.toml": "the words naming it, when Stage 2 changes what builds the environment",
    "website/how-to/speech-by-hand.md": "the file",
    "website/reference/projects.md": "the speech service's contract row, when Stage 2 moves it behind the front and the gate",
    "website/architecture.qmd": "the by-hand speech service, when Stage 2 moves it behind the front and the gate",
    "website/design/plan.md": "nothing: its dated notes and Revisions lines stay",
    "website/design/phase-2a.md": "nothing: a dated note",
    "website/design/speech-service.md": "nothing: it records the design",
    "website/design/speech-service-plan.md": "nothing: it records the build",
    "README.md": "Current state's line, when Stage 2 replaces it",
    "changelog.md": "nothing: a dated log",
}
NAMES = ("make speech", "by-hand-speech", "speech-by-hand")

STANDINS = {
    # $BOX/container is the container: it holds its state, and its absence means there is none.
    "sudo": ('case "$1 $2" in\n'
             '  "docker ps")\n'
             '    if [ "${PS_EXIT:-0}" != 0 ]; then echo "sudo: 3 incorrect password attempts" >&2; exit "$PS_EXIT"; fi\n'
             '    if [ -f "$BOX/container" ]; then cat "$BOX/container"; fi\n'
             '    exit 0 ;;\n'
             '  "docker stop")\n'
             '    if [ ! -f "$BOX/container" ]; then echo "Error response from daemon: No such container" >&2; exit 1; fi\n'
             '    if [ "${STOP_EXIT:-0}" != 0 ]; then exit "$STOP_EXIT"; fi\n'
             '    rm -f "$BOX/container"\n'
             '    exit 0 ;;\n'
             '  "docker run")\n'
             '    if [ -f "$BOX/container" ]; then echo "docker: the name is already in use" >&2; exit 125; fi\n'
             '    echo running > "$BOX/container"\n'
             '    ls -ld "$BY_HAND_RUN_DIR/local-ai-speech" | cut -c1-10 > "$BOX/mode-at-run"\n'
             '    ls -A "$BY_HAND_RUN_DIR/local-ai-speech" > "$BOX/listing-at-run"\n'
             '    echo "speech: the server says it is up"\n'
             '    rm -f "$BOX/container"\n'
             '    exit "${RUN_EXIT:-0}" ;;\n'
             'esac'),
    "uv": 'echo "UV_PROJECT_ENVIRONMENT=$UV_PROJECT_ENVIRONMENT" >> "$CALLS"\nexit "${UV_EXIT:-0}"',
    "id": 'echo "${ID_U:-1000}"',
    "free": 'echo "Mem:             121          4        110           0           6        117"',
}


def status(free, available=60.0, paused=None, held=None) -> str:
    return json.dumps({"memory": {"available_gib": available, "free_for_a_load_gib": free}, "paused": paused, "held": held,
                       "gate": "ok"})


class Box:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in STANDINS.items():
            self._standin(self.bin / name, f'echo "{name}${{*:+ $*}}" >> "$CALLS"\n{body}')
        self.home = tmp_path / "home"
        self.hf = self.home / "speech" / "hf"
        self.snapshot("Qwen3-ASR-1.7B", ASR)
        self.snapshot("Qwen3-ForcedAligner-0.6B", ALIGNER)
        # As uv makes it: the environment's python links to the minor-version folder, itself a link to the real one.
        self.python_root = self.home / ".local" / "share" / "uv" / "python"
        self.real = self.python_root / "cpython-3.12.14-linux-aarch64-gnu"
        (self.real / "bin").mkdir(parents=True)
        (self.real / "bin" / "python3.12").write_text("")
        self.minor = self.python_root / "cpython-3.12-linux-aarch64-gnu"
        self.minor.symlink_to(self.real)
        self.venv = self.home / "speech" / "venv"
        (self.venv / "bin").mkdir(parents=True)
        (self.venv / "bin" / "python").symlink_to(self.minor / "bin" / "python3.12")
        self.opt = tmp_path / "opt"
        venv_bin = self.opt / "app" / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        self._standin(venv_bin / "spark", 'echo "spark $*" >> "$CALLS"\nif [ "$1" = status ] && [ "${2:-}" = --json ]; then cat "$BOX/status"; fi')
        self._standin(venv_bin / "python", f'exec "{sys.executable}" "$@"')
        self.run_dir = tmp_path / "run"
        self.run_dir.mkdir()
        self.socket_dir = self.run_dir / "local-ai-speech"
        self.set_status(status(58.0))

    @staticmethod
    def _standin(path: Path, body: str) -> None:
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)

    def snapshot(self, name: str, revision: str) -> None:
        folder = self.hf / name
        (folder / ".cache" / "huggingface" / "download").mkdir(parents=True)
        (folder / "config.json").write_text("{}")
        (folder / "model.safetensors").write_text("")
        (folder / ".cache" / "huggingface" / "download" / "config.json.metadata").write_text(f"{revision}\nabc\n1\n")

    def set_status(self, text: str) -> None:
        (self.root / "status").write_text(text)

    def container(self, state: str) -> None:
        (self.root / "container").write_text(f"{state}\n")

    def live_socket(self) -> Path:
        self.socket_dir.mkdir(mode=0o700)
        socket = self.socket_dir / "speech.sock"
        socket.write_text("")
        return socket

    def env(self, **extra: str) -> dict[str, str]:
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.home), "CALLS": str(self.root / "calls"),
               "BOX": str(self.root), "BY_HAND_OPT": str(self.opt), "BY_HAND_RUN_DIR": str(self.run_dir),
               "TMUX": "/tmp/tmux-1000/default,1,0", **extra}
        for name in STANDINS:  # never the real tool
            assert shutil.which(name, path=env["PATH"]) == str(self.bin / name), name
        return env

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = self.env(**extra)
        env = {key: value for key, value in env.items() if value != ""}
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def calls(self) -> list[str]:
        path = self.root / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def ran(self) -> bool:
        return any(call.startswith("sudo docker run") for call in self.calls())

    def built(self) -> bool:
        return any(call.startswith("uv ") for call in self.calls())


def refused(result, box, words: str) -> None:
    assert result.returncode == 1, result.stderr
    assert not box.ran()
    assert result.stderr.count("\n") == 1 and words in result.stderr, result.stderr


def test_off_the_spark_it_refuses_and_runs_nothing(tmp_path):
    box = Box(tmp_path)
    result = box.run(BY_HAND_OPT=str(tmp_path / "absent"))
    assert result.returncode == 1 and box.calls() == [] and result.stdout == ""
    assert result.stderr.count("\n") == 1 and "runs on the Spark" in result.stderr


def test_another_uid_is_refused(tmp_path):
    box = Box(tmp_path)
    refused(box.run(ID_U="1001"), box, "this is uid 1001")


def test_outside_tmux_it_is_refused(tmp_path):
    box = Box(tmp_path)
    refused(box.run(TMUX=""), box, "inside tmux")


@pytest.mark.parametrize("name", ["Qwen3-ASR-1.7B", "Qwen3-ForcedAligner-0.6B"])
@pytest.mark.parametrize("planted", ["modeling.py", "pytorch_model.bin", "weights.pt", "extra.pkl", "sub/deep.py"])
def test_a_snapshot_holding_code_is_refused(tmp_path, name, planted):
    box = Box(tmp_path)
    path = box.hf / name / planted
    path.parent.mkdir(exist_ok=True)
    path.write_text("")
    refused(box.run(), box, planted)


@pytest.mark.parametrize(("name", "revision"), [("Qwen3-ASR-1.7B", ALIGNER), ("Qwen3-ForcedAligner-0.6B", ASR)])
def test_a_snapshot_at_another_revision_is_refused(tmp_path, name, revision):
    box = Box(tmp_path)
    (box.hf / name / ".cache" / "huggingface" / "download" / "config.json.metadata").write_text(f"{revision}\n")
    refused(box.run(), box, "isn't at revision")


def test_a_missing_snapshot_is_refused(tmp_path):
    box = Box(tmp_path)
    shutil.rmtree(box.hf / "Qwen3-ASR-1.7B")
    refused(box.run(), box, "no weights at")


@pytest.mark.parametrize(("free", "starts"), [(12.0, True), (11.96, False), (11.95, False), (0.0, False), (58.0, True)],
                         ids=str)
def test_it_starts_only_with_12_gib_free_for_a_load(tmp_path, free, starts):
    box = Box(tmp_path)
    box.set_status(status(free))
    result = box.run()
    if starts:
        assert result.returncode == 0, result.stderr
        assert box.ran()
    else:
        refused(result, box, "unload Qwen3.6")
        assert "under the 12 it needs" in result.stderr
        assert "12.0 GiB free" not in result.stderr  # a message rounds down, so it can't say the line it's under
        assert "spark unload qwen3.6-35b-a3b" in result.stderr


@pytest.mark.parametrize("shape", [
    json.dumps({"memory": {"total_gib": 121, "available_gib": 60, "brake_gib": 20}, "gate": "not answering"}),
    status(None),
    status(58.0, available=None),
    json.dumps({"memory": {"free_for_a_load_gib": 58.0}, "paused": None, "held": None, "gate": "ok"}),
], ids=["the gate is down", "the gate has no figure", "available is null", "available is missing"])
def test_it_is_refused_when_the_gate_gives_no_figure(tmp_path, shape):
    box = Box(tmp_path)
    box.set_status(shape)
    refused(box.run(), box, "the gate isn't answering")


def test_it_is_refused_when_the_status_cant_be_read(tmp_path):
    box = Box(tmp_path)
    box.set_status("not json")
    refused(box.run(), box, "can't read")


@pytest.mark.parametrize("figure", ["58", float("nan")], ids=["a string", "not a number"])
def test_a_figure_that_cant_be_compared_is_unreadable(tmp_path, figure):
    box = Box(tmp_path)
    box.set_status(status(figure))
    refused(box.run(), box, "can't read")


def test_48_gib_available_is_enough(tmp_path):
    box = Box(tmp_path)
    box.set_status(status(58.0, available=48.0))
    result = box.run()
    assert result.returncode == 0, result.stderr
    assert box.ran()


@pytest.mark.parametrize(("available", "shown"), [(47.96, "47.9"), (35.0, "35.0")], ids=str)
def test_under_48_gib_available_is_refused_at_a_free_figure_that_passes(tmp_path, available, shown):
    box = Box(tmp_path)
    box.set_status(status(58.0, available=available))
    result = box.run()
    refused(result, box, f"{shown} GiB available, under the 48")
    assert "spark unload qwen3.6-35b-a3b" in result.stderr


@pytest.mark.parametrize(("holding", "cause", "next_step", "not_this"), [
    ({"paused": {"since": 1.0, "available_gib": 19.6, "releases_at": None, "waits_for_dan": False, "problem": None}},
     "the brake has paused new loads", "spark status says why", "make-room"),
    ({"held": {"size_gib": 40.0, "until": None}},
     "a make-room hold is keeping memory back for you", "spark make-room --done ends it", "brake"),
], ids=["paused", "held"])
def test_each_hold_is_refused_in_its_own_words(tmp_path, holding, cause, next_step, not_this):
    box = Box(tmp_path)
    box.set_status(status(58.0, **holding))
    result = box.run()
    refused(result, box, cause)
    assert next_step in result.stderr and not_this not in result.stderr


@pytest.mark.parametrize("state", ["running", "paused", "restarting"])
def test_a_second_start_is_refused_and_leaves_the_running_ones_socket(tmp_path, state):
    box = Box(tmp_path)
    box.container(state)
    socket = box.live_socket()
    # Asked before the weights and the memory, so a live service is never told to unload Qwen3.6.
    shutil.rmtree(box.hf / "Qwen3-ASR-1.7B")
    box.set_status(status(0.0))
    refused(box.run(), box, "already running")
    assert socket.exists() and not box.built()


@pytest.mark.parametrize("state", ["exited", "dead", "created"])
def test_a_container_left_behind_is_refused_naming_docker_rm(tmp_path, state):
    box = Box(tmp_path)
    box.container(state)
    socket = box.live_socket()
    result = box.run()
    refused(result, box, "sudo docker rm local-ai-speech")
    assert f"in state {state}:" in result.stderr
    assert socket.exists() and not box.built()


def test_a_query_that_fails_is_refused_and_touches_nothing(tmp_path):
    box = Box(tmp_path)
    box.container("running")
    socket = box.live_socket()
    result = box.run(PS_EXIT="1")
    assert result.returncode == 1 and not box.ran() and not box.built()
    assert socket.exists()
    [theirs, ours] = result.stderr.splitlines()  # sudo's own error stays on the screen, above the refusal's one line
    assert theirs.startswith("sudo:") and "can't tell whether one is already running" in ours


def test_it_asks_docker_about_exactly_its_own_name_and_before_the_memory(tmp_path):
    box = Box(tmp_path)
    assert box.run().returncode == 0
    calls = box.calls()
    query = "sudo docker ps -a --filter name=^/local-ai-speech$ --format {{.State}}"
    assert calls.index(query) < calls.index("spark status --json")


def test_it_starts_the_container_with_the_designs_flags_and_mounts_exactly(tmp_path):
    box = Box(tmp_path)
    result = box.run()
    assert result.returncode == 0, result.stderr
    assert "speech: the server says it is up" in result.stdout
    assert "Phase 2a's drills" in result.stdout
    [run] = [call for call in box.calls() if call.startswith("sudo docker run")]
    assert run.split(" ") == [
        "sudo", "docker", "run", "--rm", "--init", "--name", "local-ai-speech",
        "--gpus", "all", "--user", "1000:1000",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,mode=1777,size=256m",
        "--network", "none", "--memory", "16g", "--memory-swap", "16g", "--pids-limit", "1024",
        "--oom-score-adj", "1000", "--log-driver", "none",
        "-e", "HOME=/tmp", "-e", "USER=speech", "-e", "XDG_CACHE_HOME=/tmp/.cache",
        "-e", "NUMBA_CACHE_DIR=/tmp/numba", "-e", "CUDA_CACHE_PATH=/tmp/nv", "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "HF_HOME=/tmp/hf", "-e", "HF_HUB_OFFLINE=1", "-e", "HF_HUB_DISABLE_TELEMETRY=1",
        "-v", f"{box.venv}:/venv:ro",
        "-v", f"{box.real}:{box.real}:ro",
        "-v", f"{ROOT}/stack/speech/serve.py:/app/serve.py:ro",
        "-v", f"{box.hf}:/hf:ro",
        "--mount", f"type=bind,source={box.socket_dir},target=/run/speech",
        "-v", f"{box.real}:{box.minor}:ro",
        IMAGE, "/venv/bin/python", "/app/serve.py"]


def test_it_builds_the_environment_from_the_lock_and_never_builds_a_package(tmp_path):
    box = Box(tmp_path)
    assert box.run().returncode == 0
    calls = box.calls()
    uv = calls.index(f"uv sync --quiet --frozen --no-build --no-install-project --project {ROOT}/stack/speech")
    assert calls[uv - 1] != "" and f"UV_PROJECT_ENVIRONMENT={box.venv}" in calls
    assert uv < next(i for i, call in enumerate(calls) if call.startswith("sudo docker run"))


def test_a_failed_build_starts_nothing(tmp_path):
    box = Box(tmp_path)
    result = box.run(UV_EXIT="3")
    assert result.returncode != 0 and not box.ran()


def test_the_socket_folder_is_made_0700_and_empty_and_goes_when_it_stops(tmp_path):
    box = Box(tmp_path)
    box.socket_dir.mkdir(mode=0o755)
    (box.socket_dir / "stale.sock").write_text("")
    assert box.run().returncode == 0
    assert (box.root / "mode-at-run").read_text().strip() == "drwx------"
    assert (box.root / "listing-at-run").read_text() == ""
    assert not box.socket_dir.exists()


def test_a_container_that_fails_gives_its_status_and_still_empties_the_folder(tmp_path):
    box = Box(tmp_path)
    assert box.run(RUN_EXIT="3").returncode == 3
    assert not box.socket_dir.exists()


def test_stop_stops_it_empties_the_folder_and_shows_the_memory(tmp_path):
    box = Box(tmp_path)
    box.container("running")
    box.live_socket()
    result = box.run("stop")
    assert result.returncode == 0, result.stderr
    assert box.calls() == ["sudo docker stop -t 20 local-ai-speech", "free -g", "spark status"]
    assert not box.socket_dir.exists()


def test_stop_with_nothing_running_says_so_and_still_empties_the_folder(tmp_path):
    box = Box(tmp_path)
    box.live_socket()
    result = box.run("stop")
    assert result.returncode == 1 and "No such container" in result.stderr
    assert not box.socket_dir.exists()


def test_a_stop_that_fails_with_the_container_up_keeps_the_folder_and_says_so(tmp_path):
    box = Box(tmp_path)
    box.container("running")
    socket = box.live_socket()
    result = box.run("stop", STOP_EXIT="7")
    assert result.returncode == 7  # docker stop's
    assert socket.exists()
    assert result.stderr.count("\n") == 1 and "stays" in result.stderr
    assert box.calls()[-2:] == ["free -g", "spark status"]


@pytest.mark.parametrize("up", [True, False], ids=["up", "gone"])
def test_a_failed_query_never_counts_as_absent_when_the_stop_failed(tmp_path, up):
    box = Box(tmp_path)
    if up:
        box.container("running")
    socket = box.live_socket()
    result = box.run("stop", STOP_EXIT="7", PS_EXIT="1")
    assert result.returncode != 0
    assert socket.exists()
    assert "stays" in result.stderr


def test_it_says_its_usage_for_any_other_do(tmp_path):
    box = Box(tmp_path)
    result = box.run("wait")
    assert result.returncode == 2 and box.calls() == [] and result.stderr.startswith("usage: make speech")


def test_it_is_shellcheck_clean():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)


def test_its_file_is_executable_as_the_others_are():
    assert stat.S_IMODE(SCRIPT.stat().st_mode) & stat.S_IXUSR


# The Makefile.

def make(*args: str, **env: str) -> str:
    run = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), *args], capture_output=True, text=True, check=True,
                         env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], **env})
    return run.stdout


@pytest.mark.parametrize(("args", "line"), [
    (["speech"], "BY_HAND_OPT='/opt/local-ai' bash stack/host/by-hand-speech ''"),
    (["speech", "DO=stop"], "BY_HAND_OPT='/opt/local-ai' bash stack/host/by-hand-speech 'stop'"),
], ids=["start", "stop"])
def test_make_speech_runs_its_script_with_do(args, line):
    assert make(*args).strip() == line


def test_make_help_says_temporary_on_the_spark_and_to_stop_it_for_the_drills():
    help_text = subprocess.run(["make", "-s", "-C", str(ROOT), "help"], capture_output=True, text=True, check=True).stdout
    [line] = [each for each in help_text.splitlines() if each.strip().startswith("speech ")]
    assert "TEMPORARY" in line and "on the Spark" in line and "drills" in line


def test_make_tunnel_forwards_the_model_api_and_the_speech_socket():
    assert make("tunnel").strip() == ("ssh -N -L 9100:127.0.0.1:9100 -L 127.0.0.1:58991:/run/user/1000/local-ai-speech/"
                                      "speech.sock ${SPARK_SSH_HOST:-brightroar}")


def test_make_lint_shellchecks_it():
    assert "stack/host/by-hand-speech" in make("lint").split()


def test_every_file_naming_it_is_on_the_removal_list():
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
                            capture_output=True, text=True, check=True).stdout.splitlines()
    found = set()
    for name in listed:
        path = ROOT / name
        if path.is_file() and not path.is_symlink():
            text = path.read_bytes().decode("utf-8", errors="replace")
            if any(each in text for each in NAMES):
                found.add(name)
    assert found <= set(PIECES), found - set(PIECES)
    assert {"Makefile", "stack/host/by-hand-speech", "spark/tests/test_speech_target.py"} <= found
