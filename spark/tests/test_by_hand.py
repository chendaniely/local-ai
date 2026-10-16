"""make fast-coder and make flash-next's scripts, `stack/host/by-hand-fast-coder` and `stack/host/by-hand-flash-next`:
each by-hand model's *Run it again* in one command (Dan's decision, 2026-10-15).

TEMPORARY: remove once Phase 4a puts the fast coder and Flash-Next in the registry. TEMPORARY, below, lists every piece
to delete then, this file among them; plan.md's Phase 4a has the same list.

The scripts stop the stack, start containers and use sudo, so they never run here for real: every tool they call that
acts on the box is a stand-in on PATH that logs its call and changes only the test's own files, `/proc/meminfo` is a
file of the test's (BY_HAND_MEMINFO), and the box's paths are the test's folders (BY_HAND_OPT, BY_HAND_UNIT_DIR,
BY_HAND_HOLD). Box.env checks that every such tool resolves to its stand-in before a script runs. The commands they run
are compared with the runbooks' own blocks, read from the pages and expanded by bash as the script expands them."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FAST_CODER = ROOT / "stack" / "host" / "by-hand-fast-coder"
FLASH_NEXT = ROOT / "stack" / "host" / "by-hand-flash-next"
SGLANG_PAGE = ROOT / "website" / "how-to" / "qwen3.8-27b-sglang.md"
FLASH_NEXT_PAGE = ROOT / "website" / "how-to" / "qwen3.8-flash-next.md"
DEPLOY_PAGE = ROOT / "website" / "how-to" / "deploy.md"

# TEMPORARY: remove once Phase 4a puts the fast coder and Flash-Next in the registry (Dan's decision, 2026-10-15).
# Every piece of make fast-coder and make flash-next, and what of it goes: a search for NAMES finds exactly these files,
# so none is left behind and none is added without this list (test_the_temporary_pieces_are_these).
TEMPORARY = {
    "Makefile": "the comment above the two targets, the targets, their .PHONY names and their scripts in lint's list",
    "stack/host/by-hand-fast-coder": "the file",
    "stack/host/by-hand-flash-next": "the file",
    "spark/tests/test_by_hand.py": "the file",
    "spark/tests/test_makefile.py": "the by-hand targets' tests, at its end",
    "website/how-to/qwen3.8-27b-sglang.md": "Run it again's note naming make fast-coder",
    "website/how-to/qwen3.8-flash-next.md": "Run it again's note naming make flash-next",
    "website/design/plan.md": "nothing: Phase 4a's removal item is marked done, and its Revisions line stays",
}
NAMES = ("make fast-coder", "make flash-next", "by-hand-fast-coder", "by-hand-flash-next")

GIB = 1024 * 1024  # kB in a GiB, as /proc/meminfo counts
CODER = r"models--unsloth--Qwen3\.8-27B-GGUF/"  # the llama.cpp coder's model folder, in its engine's argv
HELD_DOWN = "/run/systemd/system/local-ai-llama-swap.service.d/held-down.conf"
DROP_IN = "[Unit]\nConditionPathExists=/dev/null/held-down\n"

# The tools the scripts call that act on the box, each a stand-in; set_gib writes the test's /proc/meminfo, as the
# real tool's effect on memory would show there. sudo runs nothing: it logs, keeps what it was piped, and acts as the
# command it was given would. The deployed spark, in BY_HAND_OPT, is a stand-in too (Box.__init__).
SET_GIB = ('set_gib() { printf "MemTotal:       127533356 kB\\nMemFree:         1000000 kB\\n'
           'MemAvailable:   %s kB\\n" "$(( $1 * 1048576 ))" > "$BY_HAND_MEMINFO"; }')
STANDINS = {
    "sudo": ('case "$1" in\n'
             '  install|tee) cat > "$BOX/stdin-$1" ;;\n'
             '  docker) if [ "$2" = run ]; then echo "sglang: the server says it is up"; fi ;;\n'
             '  systemctl) if [ "$*" = "systemctl stop local-ai-llama-swap" ] && [ -n "${STOP_GIB:-}" ]; then\n'
             '    set_gib "$STOP_GIB"; fi ;;\n'
             'esac'),
    "systemctl": ('if [ "$*" = "stop local-ai-llama-swap" ] && [ -n "${STOP_GIB:-}" ]; then set_gib "$STOP_GIB"; fi'),
    "docker": "exit 0",
    "pgrep": 'exit "${PGREP_EXIT:-1}"',
    "pkill": ('status="${PKILL_EXIT:-0}"\n'
              'if [ "$status" = 0 ] && [ -n "${PKILL_GIB:-}" ]; then set_gib "$PKILL_GIB"; fi\n'
              'exit "$status"'),
    "curl": ('n=$(( $(cat "$BOX/curls" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$BOX/curls"\n'
             '[ "$n" -gt "${CURL_FAILS:-0}" ]'),
    "free": ('echo "               total        used        free      shared  buff/cache   available"\n'
             'echo "Mem:             121          4        110           0           6        117"'),
    "choom": 'echo "llama-server: the server says it is up"',
    "sync": "exit 0",
    # Each sleep sets the next value in $BOX/sleep-gib, if any: memory coming back while the script waits.
    "sleep": ('if [ -s "$BOX/sleep-gib" ]; then\n'
              '  set_gib "$(head -n 1 "$BOX/sleep-gib")"\n'
              '  tail -n +2 "$BOX/sleep-gib" > "$BOX/sleep-gib.next"; mv "$BOX/sleep-gib.next" "$BOX/sleep-gib"\n'
              'fi'),
}


class Box:
    """The box as a script sees it, in tmp_path: before Phase 2a's cutover or after it (the front's socket unit
    installed), with the brake holding or not, and MemAvailable at `gib`."""

    def __init__(self, tmp_path: Path, *, cutover: bool = False, hold: bool = False, gib: int = 117):
        self.root = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in STANDINS.items():
            self._standin(self.bin / name, f'echo "{name}${{*:+ $*}}" >> "$CALLS"\n{body}')
        self.home = tmp_path / "home"
        (self.home / "scratch").mkdir(parents=True)
        (self.home / "sglang" / "scratch").mkdir(parents=True)
        self.opt = tmp_path / "opt"
        self.spark = self.opt / "app" / ".venv" / "bin" / "spark"
        self.spark.parent.mkdir(parents=True)
        self._standin(self.spark, 'echo "$0 $*" >> "$CALLS"')
        self.units = tmp_path / "systemd"
        self.units.mkdir()
        if cutover:
            (self.units / "local-ai-front.socket").write_text("[Socket]\n")
        self.hold = tmp_path / "brake" / "hold.json"
        self.hold.parent.mkdir()
        if hold:
            self.hold.write_text('{"since": "2026-10-15T09:00:00", "reason": "stand-in", "unloaded": []}\n')
        self.meminfo = tmp_path / "meminfo"
        self.set_kib(gib * GIB)

    @staticmethod
    def _standin(path: Path, body: str) -> None:
        path.write_text(f"#!/usr/bin/env bash\n{SET_GIB}\n{body}\n")
        path.chmod(0o755)

    def set_kib(self, kib: int) -> None:
        self.meminfo.write_text(f"MemTotal:       127533356 kB\nMemFree:         1000000 kB\nMemAvailable:   {kib} kB\n")

    def sleeps_bring(self, *gib: int) -> None:
        (self.root / "sleep-gib").write_text("".join(f"{each}\n" for each in gib))

    def env(self, **extra: str) -> dict[str, str]:
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "HOME": str(self.home), "CALLS": str(self.root / "calls"),
               "BOX": str(self.root), "BY_HAND_OPT": str(self.opt), "BY_HAND_MEMINFO": str(self.meminfo),
               "BY_HAND_UNIT_DIR": str(self.units), "BY_HAND_HOLD": str(self.hold), **extra}
        for name in STANDINS:  # never the real tool
            assert shutil.which(name, path=env["PATH"]) == str(self.bin / name), name
        return env

    def run(self, script: Path, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(script), *args], env=self.env(**extra), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)

    def calls(self) -> list[str]:
        path = self.root / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def piped(self, command: str) -> str:
        return (self.root / f"stdin-{command}").read_text()

    def logs(self, folder: str, pattern: str) -> list[Path]:
        return sorted((self.home / folder).glob(pattern))


def bash_blocks(text: str) -> list[str]:
    """Each ```bash block in a page's text, without the indent a list item gives it."""
    blocks = []
    for found in re.finditer(r"^( *)```bash\n(.*?)^\1```$", text, re.M | re.S):
        indent = found.group(1)
        blocks.append("\n".join(line.removeprefix(indent) for line in found.group(2).splitlines()))
    return blocks


def section(page: Path, heading: str) -> str:
    text = page.read_text()
    assert heading in text, heading
    return text.split(heading, 1)[1].split("\n#", 1)[0]


def block(page: Path, heading: str, start: str) -> str:
    """The one ```bash block under `heading` that starts with `start`."""
    [found] = [each for each in bash_blocks(section(page, heading)) if each.startswith(start)]
    return found


RUN_IT_AGAIN = "## Run it again {#run-it-again}"
KEEPING_IT_STOPPED = "### Keeping llama-swap stopped"
DOCKER_RUN = block(SGLANG_PAGE, RUN_IT_AGAIN, "sudo docker run ")
FLASH_NEXT_128K = block(FLASH_NEXT_PAGE, RUN_IT_AGAIN, "choom -n 1000 -- /opt/local-ai/bin/llama.cpp/b11146/")
FLASH_NEXT_MTP = block(FLASH_NEXT_PAGE, RUN_IT_AGAIN, "choom -n 1000 -- ~/scratch/llama.cpp-b11330/")
HOLD_DOWN = block(DEPLOY_PAGE, KEEPING_IT_STOPPED, "printf '[Unit]")
END_HOLD_DOWN = block(DEPLOY_PAGE, KEEPING_IT_STOPPED, "sudo rm ")


def expanded(command: str, home: Path) -> str:
    """A runbook's command line as its first stand-in logs it, `name $*`: the words bash makes of it, with `home` as
    HOME, without its `2>&1 | tee …` tail."""
    words = command.split(" 2>&1 | tee ", 1)[0]
    run = subprocess.run(["bash", "-c", 'words() { printf "%s" "$*"; }; words ' + words],
                         env={"PATH": os.environ["PATH"], "HOME": str(home)}, capture_output=True, text=True, check=True)
    return run.stdout


def lines_of(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines()]


def holds_block(script: Path, runbook_block: str) -> bool:
    """The block's lines, each as written, one after another in the script; only their indent may differ."""
    have, want = lines_of(script.read_text()), lines_of(runbook_block)
    return any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1))


# Both scripts: on the Spark only, and the runbooks' commands exactly.

@pytest.mark.parametrize("script", [FAST_CODER, FLASH_NEXT], ids=["fast-coder", "flash-next"])
@pytest.mark.parametrize("do", ["", "wait", "stop"])
def test_off_the_spark_it_refuses_and_runs_nothing(tmp_path, script, do):
    box = Box(tmp_path)
    result = box.run(script, do, BY_HAND_OPT=str(tmp_path / "absent"))
    assert result.returncode == 1 and box.calls() == []
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1 and "runs on the Spark" in result.stderr


@pytest.mark.parametrize(("script", "runbook_block"), [
    (FAST_CODER, DOCKER_RUN), (FLASH_NEXT, FLASH_NEXT_128K), (FLASH_NEXT, FLASH_NEXT_MTP),
    (FLASH_NEXT, HOLD_DOWN), (FLASH_NEXT, END_HOLD_DOWN),
], ids=["docker run", "128K", "MTP", "hold-down", "hold-down's end"])
def test_it_holds_the_runbooks_block_as_written(script, runbook_block):
    assert holds_block(script, runbook_block)


@pytest.mark.parametrize("script", [FAST_CODER, FLASH_NEXT], ids=["fast-coder", "flash-next"])
def test_it_says_its_usage_for_any_other_do(tmp_path, script):
    box = Box(tmp_path)
    result = box.run(script, "go")
    assert result.returncode == 2 and box.calls() == []
    assert result.stderr.startswith("usage: make ")


@pytest.mark.parametrize("script", [FAST_CODER, FLASH_NEXT], ids=["fast-coder", "flash-next"])
def test_it_is_shellcheck_clean(script):
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")
    subprocess.run(["shellcheck", str(script)], check=True)


def test_make_lint_shellchecks_both():
    run = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "lint"], capture_output=True, text=True, check=True)
    assert {"stack/host/by-hand-fast-coder", "stack/host/by-hand-flash-next"} <= set(run.stdout.split())


def test_the_temporary_pieces_are_these():
    # A search for the targets' and scripts' names finds exactly TEMPORARY's files, tracked or about to be.
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard"],
                            capture_output=True, text=True, check=True).stdout.splitlines()
    found = set()
    for name in listed:
        path = ROOT / name
        if path.is_file() and not path.is_symlink():
            text = path.read_bytes().decode("utf-8", errors="replace")
            if any(each in text for each in NAMES):
                found.add(name)
    assert found == set(TEMPORARY)


@pytest.mark.parametrize("piece", ["Makefile", "stack/host/by-hand-fast-coder", "stack/host/by-hand-flash-next"])
def test_each_comment_names_every_file_with_something_to_delete(piece):
    # The comment that starts at the one TEMPORARY line, up to the first line that isn't a comment.
    lines = (ROOT / piece).read_text().splitlines()
    [start] = [i for i, line in enumerate(lines) if "TEMPORARY: remove once Phase 4a" in line]
    comment = []
    for line in lines[start:]:
        if not line.startswith("#"):
            break
        comment.append(line.lstrip("#").strip())
    for name in TEMPORARY:
        if name != "website/design/plan.md":  # its item is marked done, not deleted
            assert name in " ".join(comment), (piece, name)


# make fast-coder: the fast coder on SGLang, beside the stack.

def test_fast_coder_starts_step_2s_container_in_the_foreground(tmp_path):
    box = Box(tmp_path, gib=80)
    result = box.run(FAST_CODER)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0].startswith("make fast-coder: ask the Spark's build session first")
    assert box.calls() == [f"pgrep -u spark -f {CODER}", expanded(DOCKER_RUN, box.home)]
    [log] = box.logs("sglang/scratch", "boot-*.log")
    assert re.fullmatch(r"boot-\d{4}-\d\d-\d\d-\d{4}\.log", log.name)
    assert log.read_text() == "sglang: the server says it is up\n"


@pytest.mark.parametrize(("cutover", "how"), [
    (False, "systemctl stop local-ai-llama-swap && systemctl start local-ai-llama-swap"),
    (True, "/app/.venv/bin/spark unload coder"),
], ids=["before the cutover", "after it"])
def test_fast_coder_refuses_while_the_llama_cpp_coder_is_loaded(tmp_path, cutover, how):
    box = Box(tmp_path, cutover=cutover)
    result = box.run(FAST_CODER, PGREP_EXIT="0")
    assert result.returncode == 1
    assert box.calls() == [f"pgrep -u spark -f {CODER}"]
    assert result.stderr.count("\n") == 1 and "qwen3.8-27b" in result.stderr and how in result.stderr


def test_fast_coder_refuses_when_it_cant_tell_whether_the_coder_is_loaded(tmp_path):
    # pgrep's 2 and 3 are its own errors, not "none found".
    box = Box(tmp_path)
    result = box.run(FAST_CODER, PGREP_EXIT="2")
    assert result.returncode == 1 and box.calls() == [f"pgrep -u spark -f {CODER}"]
    assert "pgrep" in result.stderr


@pytest.mark.parametrize(("kib", "starts"), [(79 * GIB, True), (79 * GIB - 1, False), (40 * GIB, False)],
                         ids=["79 GiB", "just under", "40 GiB"])
def test_fast_coder_starts_only_with_79_gib_available(tmp_path, kib, starts):
    box = Box(tmp_path)
    box.set_kib(kib)
    result = box.run(FAST_CODER)
    assert (result.returncode == 0) == starts, result.stderr
    assert any(call.startswith("sudo docker run ") for call in box.calls()) == starts
    if not starts:
        assert result.stderr.count("\n") == 1 and f"{kib // GIB} GiB available" in result.stderr
        assert "79" in result.stderr


def test_fast_coder_says_so_when_meminfo_has_no_memavailable(tmp_path):
    box = Box(tmp_path)
    box.meminfo.write_text("MemTotal:       127533356 kB\n")
    result = box.run(FAST_CODER)
    assert result.returncode == 1 and not any(call.startswith("sudo") for call in box.calls())
    assert "MemAvailable" in result.stderr


def test_fast_coder_wait_asks_its_health_until_it_answers_then_shows_the_time_and_memory(tmp_path):
    box = Box(tmp_path, gib=72)
    result = box.run(FAST_CODER, "wait", CURL_FAILS="2")
    assert result.returncode == 0, result.stderr
    health = "curl -sf http://127.0.0.1:990/health"
    assert box.calls() == [health, "sleep 10", health, "sleep 10", health, "free -g"]
    assert re.search(r"^\d\d:\d\d:\d\d$", result.stdout, re.M)


def test_fast_coder_wait_gives_up_after_about_10_minutes(tmp_path):
    box = Box(tmp_path)
    result = box.run(FAST_CODER, "wait", CURL_FAILS="1000")
    assert result.returncode == 1
    calls = box.calls()
    assert calls.count("curl -sf http://127.0.0.1:990/health") == 60 and calls.count("sleep 10") == 59
    assert "free -g" not in calls
    assert "10 minutes" in result.stderr and "make fast-coder DO=stop" in result.stderr


def test_fast_coder_wait_says_stop_it_under_the_warn_line(tmp_path):
    box = Box(tmp_path, gib=27)
    result = box.run(FAST_CODER, "wait")
    assert result.returncode == 1
    assert box.calls() == ["curl -sf http://127.0.0.1:990/health", "free -g"]
    assert "27 GiB available" in result.stderr and "make fast-coder DO=stop" in result.stderr


def test_fast_coder_stop_stops_the_container_and_shows_memory_and_status(tmp_path):
    box = Box(tmp_path)
    result = box.run(FAST_CODER, "stop")
    assert result.returncode == 0, result.stderr
    assert box.calls() == ["sudo docker stop -t 20 qwen38-coder", "free -g", f"{box.spark} status"]


# make flash-next: Flash-Next with the stack stopped.

def test_flash_next_before_the_cutover_stops_llama_swap_clears_the_cache_and_starts_the_128k_run(tmp_path):
    box = Box(tmp_path, gib=70)
    result = box.run(FLASH_NEXT, STOP_GIB="117")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0].startswith("make flash-next: ask the Spark's build session first")
    assert box.calls() == ["systemctl stop local-ai-llama-swap", "sync", "sudo tee /proc/sys/vm/drop_caches",
                           "free -g", expanded(FLASH_NEXT_128K, box.home)]
    assert box.piped("tee") == "1\n"
    [log] = box.logs("scratch", "flash-next-*.log")
    assert re.fullmatch(r"flash-next-\d{4}-\d\d-\d\d-\d{4}\.log", log.name)
    assert log.read_text() == "llama-server: the server says it is up\n"


def test_flash_next_after_the_cutover_holds_llama_swap_down_as_deploy_md_does(tmp_path):
    box = Box(tmp_path, cutover=True, gib=70)
    result = box.run(FLASH_NEXT, STOP_GIB="117")
    assert result.returncode == 0, result.stderr
    assert box.calls() == [f"sudo install -D -m 0644 /dev/stdin {HELD_DOWN}", "sudo systemctl daemon-reload",
                           "sudo systemctl stop local-ai-llama-swap", "sudo -k",
                           "sync", "sudo tee /proc/sys/vm/drop_caches", "free -g", expanded(FLASH_NEXT_128K, box.home)]
    assert box.piped("install") == DROP_IN


def test_flash_next_mtp_starts_the_64k_mtp_run_with_b11330(tmp_path):
    box = Box(tmp_path, gib=70)
    result = box.run(FLASH_NEXT, MTP="1", STOP_GIB="117")
    assert result.returncode == 0, result.stderr
    assert box.calls()[-1] == expanded(FLASH_NEXT_MTP, box.home)
    assert "/llama.cpp-b11330/build/bin/llama-server" in box.calls()[-1] and "--ctx-size 65536" in box.calls()[-1]
    [log] = box.logs("scratch", "flash-next-*.log")
    assert re.fullmatch(r"flash-next-mtp-\d{4}-\d\d-\d\d-\d{4}\.log", log.name)


@pytest.mark.parametrize("mtp", ["0", ""])
def test_flash_next_without_mtp_1_is_the_128k_run(tmp_path, mtp):
    box = Box(tmp_path, gib=117)
    assert box.run(FLASH_NEXT, MTP=mtp).returncode == 0
    assert box.calls()[-1] == expanded(FLASH_NEXT_128K, box.home)


def test_flash_next_refuses_any_other_mtp_before_it_stops_anything(tmp_path):
    box = Box(tmp_path)
    result = box.run(FLASH_NEXT, MTP="yes")
    assert result.returncode == 2 and box.calls() == []
    assert "MTP=1" in result.stderr


def test_flash_next_waits_for_memory_to_come_back_before_it_starts(tmp_path):
    box = Box(tmp_path, gib=70)
    box.sleeps_bring(105, 112)
    result = box.run(FLASH_NEXT, STOP_GIB="100")
    assert result.returncode == 0, result.stderr
    assert box.calls() == ["systemctl stop local-ai-llama-swap", "sync", "sudo tee /proc/sys/vm/drop_caches",
                           "sleep 5", "sleep 5", "free -g", expanded(FLASH_NEXT_128K, box.home)]


def test_flash_next_refuses_to_start_when_memory_never_comes_back(tmp_path):
    # The fast coder still running, say: llama-swap is stopped, and the refusal says how to get it back.
    box = Box(tmp_path, gib=70)
    result = box.run(FLASH_NEXT, STOP_GIB="72")
    assert result.returncode == 1
    calls = box.calls()
    assert calls[:3] == ["systemctl stop local-ai-llama-swap", "sync", "sudo tee /proc/sys/vm/drop_caches"]
    assert calls[3:] == ["sleep 5"] * 23
    assert "72 GiB available" in result.stderr and "110" in result.stderr
    assert "make flash-next DO=stop" in result.stderr


def test_flash_next_wait_asks_its_health_until_it_answers(tmp_path):
    box = Box(tmp_path, gib=34)
    result = box.run(FLASH_NEXT, "wait", CURL_FAILS="1")
    assert result.returncode == 0, result.stderr
    health = "curl -sf http://127.0.0.1:8080/health"
    assert box.calls() == [health, "sleep 5", health, "free -g"]
    assert re.search(r"^\d\d:\d\d:\d\d$", result.stdout, re.M)


def test_flash_next_wait_gives_up_after_about_10_minutes(tmp_path):
    box = Box(tmp_path)
    result = box.run(FLASH_NEXT, "wait", CURL_FAILS="1000")
    assert result.returncode == 1
    calls = box.calls()
    assert calls.count("curl -sf http://127.0.0.1:8080/health") == 120 and calls.count("sleep 5") == 119
    assert "10 minutes" in result.stderr and "make flash-next DO=stop" in result.stderr


def test_flash_next_wait_says_stop_it_under_the_warn_line(tmp_path):
    box = Box(tmp_path, gib=27)
    result = box.run(FLASH_NEXT, "wait")
    assert result.returncode == 1
    assert "27 GiB available" in result.stderr and "make flash-next DO=stop" in result.stderr


PKILL = "pkill -INT -f alias qwen3.8-flash-next"


@pytest.mark.parametrize("hold", [False, True], ids=["no hold", "the brake holding"])
def test_flash_next_stop_before_the_cutover_releases_a_hold_then_starts_llama_swap(tmp_path, hold):
    box = Box(tmp_path, hold=hold, gib=34)
    result = box.run(FLASH_NEXT, "stop", PKILL_GIB="117")
    assert result.returncode == 0, result.stderr
    release = [f"{box.spark} brake --release"] if hold else []
    assert box.calls() == [PKILL, "free -g", *release, "systemctl start local-ai-llama-swap", "sleep 5",
                           f"{box.spark} status"]


@pytest.mark.parametrize("hold", [False, True], ids=["no hold", "the brake holding"])
def test_flash_next_stop_after_the_cutover_ends_the_hold_down_then_releases(tmp_path, hold):
    box = Box(tmp_path, cutover=True, hold=hold, gib=34)
    result = box.run(FLASH_NEXT, "stop", PKILL_GIB="117")
    assert result.returncode == 0, result.stderr
    release = [f"{box.spark} brake --release"] if hold else []
    assert box.calls() == [PKILL, "free -g", f"sudo rm {HELD_DOWN}", "sudo systemctl daemon-reload",
                           "sudo systemctl start local-ai-llama-swap", "sudo -k", *release, "sleep 5",
                           f"{box.spark} status"]


def test_flash_next_stop_waits_for_memory_to_come_back(tmp_path):
    box = Box(tmp_path, gib=34)
    box.sleeps_bring(90, 116)
    result = box.run(FLASH_NEXT, "stop", PKILL_GIB="60")
    assert result.returncode == 0, result.stderr
    assert box.calls()[:4] == [PKILL, "sleep 5", "sleep 5", "free -g"]


def test_flash_next_stop_goes_on_when_flash_next_wasnt_running(tmp_path):
    # pkill's 1: nothing matched, as after the server stopped by itself; the stack still comes back.
    box = Box(tmp_path, gib=117)
    result = box.run(FLASH_NEXT, "stop", PKILL_EXIT="1")
    assert result.returncode == 0, result.stderr
    assert box.calls() == [PKILL, "free -g", "systemctl start local-ai-llama-swap", "sleep 5", f"{box.spark} status"]
    assert "wasn't running" in result.stdout


def test_flash_next_stop_stops_when_pkill_fails(tmp_path):
    box = Box(tmp_path, gib=117)
    result = box.run(FLASH_NEXT, "stop", PKILL_EXIT="3")
    assert result.returncode == 1 and box.calls() == [PKILL]
    assert "pkill" in result.stderr


@pytest.mark.parametrize("cutover", [False, True], ids=["before the cutover", "after it"])
def test_flash_next_stop_restarts_nothing_while_memory_stays_held(tmp_path, cutover):
    box = Box(tmp_path, cutover=cutover, hold=True, gib=34)
    result = box.run(FLASH_NEXT, "stop")
    assert result.returncode == 1
    assert box.calls() == [PKILL, *["sleep 5"] * 23]
    assert "34 GiB available" in result.stderr and "make flash-next DO=stop" in result.stderr
