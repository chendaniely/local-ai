import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from spark import cli, launch, memory, paths
from spark.hold import Hold, write_hold
from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, Running
from spark.memory import MemInfo
from spark.registry import load_registry
from spark.status import format_text, gather

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def test_headroom_is_measured_to_the_brake():
    status = gather(MemInfo(121.7, 70.0), REG, [], None)
    assert status["memory"]["headroom_before_brake_gib"] == 50.0


def test_loaded_models_carry_their_class_and_footprint():
    status = gather(MemInfo(121.7, 70.0), REG, [Running("coder", "ready")], None)
    assert status["loaded"] == [{"model": "coder", "state": "ready", "resident": False, "footprint_gib": 28.0}]


def test_text_names_the_models_and_the_brake():
    text = format_text(gather(MemInfo(121.7, 70.0), REG, [Running("coder", "ready")], None))
    assert "70 GiB available of 122 GiB" in text and "50 GiB before the brake" in text
    assert "coder (on demand, ~28 GiB, ready)" in text and "brake    no hold" in text


def test_unreachable_llama_swap_is_reported_not_raised():
    assert "llama-swap unreachable" in format_text(gather(MemInfo(121.7, 70.0), REG, None, None))


def test_a_hold_is_shown_with_how_to_release():
    text = format_text(gather(MemInfo(121.7, 70.0), REG, [], Hold("t0", "18.0 GiB available", ("coder",))))
    assert "HOLDING since t0" in text and "spark brake --release" in text


def test_the_last_refused_load_is_explained():
    refusal = {"at": "t1", "model": "coder", "reason": "needs ~28 GiB, 40 GiB available (24 GiB reserve kept)"}
    text = format_text(gather(MemInfo(121.7, 40.0), REG, [], None, refusal))
    assert "refused  coder at t1: needs ~28 GiB, 40 GiB available (24 GiB reserve kept)" in text


# Fix round 1: status says only what's true for whoever runs it, and exits 0 whatever it can't read. Most of these run
# `spark status` through the CLI, on a fake box.

FIXTURE = Path(__file__).parent / "fixtures" / "models.yaml"
CODER = (Running("coder", "ready"),)
HTTP_401 = LlamaSwapError("llama-swap GET /running: HTTP 401")


def standard(text: str):
    """JSON as a strict parser reads it: NaN and Infinity aren't JSON."""

    def refuse(constant):
        raise ValueError(f"{constant} isn't standard JSON")

    return json.loads(text, parse_constant=refuse)


@pytest.fixture()
def spark_status(tmp_path, monkeypatch, capsys):
    """`spark status [flags]` on a fake box: the fixture registry, the state folder tmp_path/"state", 70 of 121.7 GiB
    available, and llama-swap running the coder, with no key in the environment. A keyword changes what the box has:
    `answer` is what LlamaSwap(...).running() returns, or raises when it's an error, or None for the real client.
    Gives the exit code, what it printed, and how each LlamaSwap was made."""
    (tmp_path / "state").mkdir()
    monkeypatch.delenv("SPARK_API_KEY", raising=False)

    def run(*flags, registry=FIXTURE, mem=MemInfo(121.7, 70.0), answer=CODER):
        made = []

        class FakeLlamaSwap:
            def __init__(self, base_url, api_key, timeout=10.0):
                made.append({"url": base_url, "key": api_key, "timeout": timeout})

            def running(self):
                if isinstance(answer, Exception):
                    raise answer
                return list(answer)

        monkeypatch.setattr(paths, "REGISTRY", registry)
        monkeypatch.setattr(paths, "STATE", tmp_path / "state")
        monkeypatch.setattr("spark.status.read_meminfo", mem if callable(mem) else lambda: mem)
        monkeypatch.setattr("spark.status.LlamaSwap", LlamaSwap if answer is None else FakeLlamaSwap)
        code = cli.main(["status", *flags])
        return code, capsys.readouterr().out, made

    return run


def test_status_through_the_cli(spark_status):
    code, out, made = spark_status()
    assert code == 0 and out == ("memory   70 GiB available of 122 GiB · 50 GiB before the brake (20 GiB)\n"
                                 "loaded   coder (on demand, ~28 GiB, ready)\n"
                                 "brake    no hold\n")
    assert made == [{"url": paths.LLAMASWAP_URL, "key": None, "timeout": 3}]


@pytest.mark.parametrize("bad", ["missing", "a folder", "not YAML", "invalid", "nested too deep", "unreadable"])
def test_a_registry_that_wont_load_is_said_and_the_rest_still_shown(spark_status, tmp_path, bad):
    # The brake line falls back to the plan's (brake.FALLBACK), and llama-swap and the hold are still read (I1).
    registry = tmp_path / "models.yaml"
    if bad == "a folder":
        registry.mkdir()
    elif bad == "unreadable":
        if os.geteuid() == 0:
            pytest.skip("root reads any file")
        registry.write_text(FIXTURE.read_text())
        registry.chmod(0)
    elif bad != "missing":
        registry.write_text({"not YAML": "budget: {allocatable_gib: 102\n  : [\n",
                             "invalid": "budget: {allocatable_gib: 102, reserve_gib: 24}\nbrake: {warn_gib: 28}\n",
                             # load_registry raises RecursionError
                             "nested too deep": "budget: " + "[" * 100_000 + "]" * 100_000 + "\n"}[bad])
    write_hold(tmp_path / "state", Hold("t0", "18.0 GiB available", ("coder",)))
    code, out, _ = spark_status(registry=registry)
    assert code == 0 and out.splitlines()[:3] == [
        "memory   70 GiB available of 122 GiB · 50 GiB before the brake (20 GiB, the plan's default)",
        "loaded   coder (ready)",
        "brake    HOLDING since t0 (18.0 GiB available); unloaded: coder — `spark brake --release` to clear",
    ]
    assert f"problem  the registry {registry} won't load: " in out
    code, out, _ = spark_status("--json", registry=registry)
    status = standard(out)
    assert code == 0 and status["registry_loaded"] is False and status["memory"]["brake_gib"] == 20
    assert status["problems"][0].startswith(f"the registry {registry} won't load: ")


def test_the_refusal_a_broken_registry_caused_is_shown(spark_status, tmp_path, monkeypatch):
    # `spark launch` records why it refused; status used to die on the same registry and hide it (I1).
    registry = tmp_path / "models.yaml"
    registry.write_text("budget: {allocatable_gib: 102}\n")
    monkeypatch.setattr(launch, "_mark_first_to_kill", lambda: pytest.fail("must not mark the engine"))
    monkeypatch.setattr(launch.os, "execvpe", lambda f, a, env: pytest.fail("must not exec"))
    assert launch.main_launch(["coder", "--", "/bin/engine"], registry=registry, state=tmp_path / "state") == 3
    code, out, _ = spark_status(registry=registry)
    refused = [line for line in out.splitlines() if line.startswith("refused  coder at ")]
    assert code == 0 and refused and refused[0].endswith(
        f": the registry {registry} won't load: budget: reserve_gib is required")


@pytest.mark.parametrize("meminfo, error", [(None, "No such file or directory"),
                                            ("MemFree: 1024 kB\n", "/proc/meminfo has no MemTotal")],
                         ids=["missing", "no MemTotal"])
def test_memory_that_cant_be_read_is_said_and_the_rest_still_shown(spark_status, tmp_path, meminfo, error):
    fake = tmp_path / "meminfo"
    if meminfo is not None:
        fake.write_text(meminfo)
    code, out, _ = spark_status(mem=lambda: memory.read_meminfo(fake))
    assert code == 0 and out.splitlines()[:3] == [
        "memory   unknown", "loaded   coder (on demand, ~28 GiB, ready)", "brake    no hold"]
    assert "problem  can't read memory: " in out and error in out
    code, out, _ = spark_status("--json", mem=lambda: memory.read_meminfo(fake))
    status = standard(out)
    assert code == 0 and status["memory"] is None and error in status["problems"][0]


@pytest.mark.parametrize("answer", [HTTP_401,
                                    LlamaSwapError("llama-swap GET /running: an answer it can't read (not v257's shape)")],
                         ids=["401", "not v257"])
def test_a_llama_swap_that_answered_is_never_called_unreachable(spark_status, answer):
    # "Unreachable" invites a restart of llama-swap, which stops every loaded model (I2).
    code, out, _ = spark_status(answer=answer)
    assert code == 0 and "unreachable" not in out
    assert "loaded   unknown" in out.splitlines() and f"problem  {answer}" in out


def test_unreachable_is_said_when_nothing_answered(spark_status):
    down = LlamaSwapUnreachable("llama-swap unreachable at http://127.0.0.1:9100: [Errno 111] Connection refused")
    code, out, _ = spark_status(answer=down)
    assert code == 0 and "loaded   unknown" in out.splitlines() and f"problem  {down}" in out


def test_no_key_is_said_by_the_variable_s_name(spark_status):
    code, out, made = spark_status(answer=HTTP_401)
    assert code == 0 and made[0]["key"] is None
    assert "problem  llama-swap GET /running: HTTP 401 (no key in $SPARK_API_KEY)" in out.splitlines()


def test_the_key_comes_from_the_variable_key_env_names_and_is_never_shown(spark_status, monkeypatch):
    monkeypatch.setenv("SPARK_API_KEY", "k-not-this-one")
    monkeypatch.setenv("SPARK_TEST_STATUS_KEY", "k-status-test")
    for flags in ([], ["--json"]):
        code, out, made = spark_status("--key-env", "SPARK_TEST_STATUS_KEY", *flags, answer=HTTP_401)
        assert code == 0 and made == [{"url": paths.LLAMASWAP_URL, "key": "k-status-test", "timeout": 3}]
        assert "k-status-test" not in out and "k-not-this-one" not in out and "no key" not in out


class Refuses(BaseHTTPRequestHandler):  # llama-swap v257, asked for /running without the key it wants
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(401)
        self.end_headers()
        self.wfile.write(b'{"error": {"message": "unauthorized: invalid or missing API key"}}')


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_real_client_s_401_and_a_closed_port_are_told_apart(spark_status, monkeypatch):
    httpd = HTTPServer(("127.0.0.1", 0), Refuses)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        monkeypatch.setattr(paths, "LLAMASWAP_URL", f"http://127.0.0.1:{httpd.server_port}")
        code, answered, _ = spark_status(answer=None)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert code == 0 and "unreachable" not in answered
    assert "problem  llama-swap GET /running: HTTP 401 (no key in $SPARK_API_KEY)" in answered.splitlines()
    monkeypatch.setattr(paths, "LLAMASWAP_URL", f"http://127.0.0.1:{closed_port()}")
    code, down, _ = spark_status(answer=None)
    assert code == 0 and "problem  llama-swap unreachable at http://127.0.0.1:" in down


@pytest.mark.parametrize("inside", ["nothing", "a hold and a refusal"])
def test_a_state_folder_this_account_cant_read_is_unknown_to_it(spark_status, tmp_path, inside):
    # agent isn't in spark-admin, and the folder is 2770 spark:spark-admin; nor is a shell of Dan's started before
    # bootstrap added him to the group. Whether a hold stands is unknown to them, either way (I3).
    if os.geteuid() == 0:
        pytest.skip("root reads any folder")
    state = tmp_path / "state"
    if inside != "nothing":
        write_hold(state, Hold("t0", "18.0 GiB available", ("coder",)))
        launch.record_refusal(state, "coder", "needs 28.0 GiB")
    state.chmod(0)
    try:
        text, js = spark_status(), spark_status("--json")
    finally:
        state.chmod(0o700)
    code, out, _ = text
    assert code == 0 and "brake    unknown" in out.splitlines()
    assert "HOLDING" not in out and "--release" not in out
    assert not any(line.startswith("refused") for line in out.splitlines())
    assert (f"problem  this account can't read {state}, so the brake's hold and the last refusal are unknown to it; "
            "spark-admin can read them") in out.splitlines()
    code, out, _ = js
    status = standard(out)
    assert code == 0 and status["brake"]["state"] == "unknown" and status["last_refusal"] is None


def test_a_damaged_hold_still_holds_and_what_it_unloaded_is_unknown(spark_status, tmp_path):
    # `spark launch` refuses on a damaged hold too, so HOLDING is true here (I3).
    (tmp_path / "state" / "hold.json").write_text("{not json")
    code, out, _ = spark_status()
    assert code == 0 and "brake    HOLDING since an unknown time (hold file " in out
    assert "; unloaded: unknown — `spark brake --release` to clear" in out
    code, out, _ = spark_status("--json")
    brake = standard(out)["brake"]
    assert brake["state"] == "holding" and brake["since"] is None and brake["unloaded"] is None


def test_the_release_is_offered_only_to_an_account_that_can_write_the_folder(spark_status, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root writes any folder")
    state = tmp_path / "state"
    write_hold(state, Hold("t0", "18.0 GiB available", ("coder",)))
    state.chmod(0o550)
    try:
        code, out, _ = spark_status()
    finally:
        state.chmod(0o700)
    assert code == 0 and "--release" not in out
    assert "brake    HOLDING since t0 (18.0 GiB available); unloaded: coder — spark-admin can release it" in out


def test_a_model_the_registry_doesnt_list_is_shown_not_a_crash(spark_status):
    # apply deployed a registry without it but llama-swap's restart failed, or a hand edit (I4).
    code, out, _ = spark_status(answer=[Running("ghost", "ready"), Running("coder", "ready")])
    assert code == 0 and "loaded   ghost (not in the registry, ready) · coder (on demand, ~28 GiB, ready)" in out
    assert "problem  ghost is loaded but isn't in the registry" in out.splitlines()
    code, out, _ = spark_status("--json", answer=[Running("ghost", "ready")])
    assert standard(out)["loaded"] == [{"model": "ghost", "state": "ready", "resident": None, "footprint_gib": None}]


@pytest.mark.parametrize("damage", ["[1]", '"s"', "5", '{"x": 1}', "[" * 100_000],
                         ids=["a list", "a string", "a number", "none of its fields", "nested too deep"])
def test_a_damaged_refusal_record_is_said_not_a_crash(spark_status, tmp_path, damage):
    record = tmp_path / "state" / "last-refusal.json"
    record.write_text(damage)
    code, out, _ = spark_status()
    assert code == 0 and f"problem  the refusal record {record} " in out
    assert not any(line.startswith("refused") for line in out.splitlines())
    code, out, _ = spark_status("--json")
    status = standard(out)
    assert code == 0 and status["last_refusal"] is None
    assert any(problem.startswith(f"the refusal record {record} ") for problem in status["problems"])


@pytest.mark.parametrize("available, memory_line, headroom", [
    (70.0, "70 GiB available of 122 GiB · 50 GiB before the brake (20 GiB)", 50.0),
    (25.37, "25.3 GiB available of 122 GiB · 5.3 GiB before the brake (20 GiB)", 5.3),
    (20.04, "20.0 GiB available of 122 GiB · 0.0 GiB before the brake (20 GiB)", 0.0),
    (19.96, "19.9 GiB available of 122 GiB · past the brake line by 0.1 GiB (20 GiB)", -0.1),
    (19.6, "19.6 GiB available of 122 GiB · past the brake line by 0.4 GiB (20 GiB)", -0.4),
])
def test_near_the_brake_line_memory_shows_tenths_rounded_against_the_box(available, memory_line, headroom):
    # What's available rounds down, so a box past the line never reads as on it, and nothing shows as -0 (Minor 1).
    status = gather(MemInfo(121.7, available), REG, [], None)
    assert format_text(status).splitlines()[0] == f"memory   {memory_line}"
    assert status["memory"]["headroom_before_brake_gib"] == headroom
    assert "-0.0" not in json.dumps(status)


def test_footprints_show_as_the_registry_gives_them():
    text = format_text(gather(MemInfo(121.7, 70.0), REG, [Running("embed", "ready"), Running("stt", "ready")], None))
    assert "loaded   embed (resident, ~1.5 GiB, ready) · stt (resident, ~2.5 GiB, ready)" in text


def test_what_files_and_llama_swap_say_is_shown_escaped():
    # Reasons, names and states come from files and from whatever answers on llama-swap's port: none of it may drive
    # the terminal or forge a line of its own (Minor 2).
    refusal = {"at": "t1", "model": "coder", "reason": "a\x1b]0;title\x07b"}
    running = [Running("x\nbrake    no hold", "ready\x1b[2J")]
    text = format_text(gather(MemInfo(121.7, 70.0), REG, running, Hold("t0\r", "r‮", ("c\x1b",)), refusal))
    assert "refused  coder at t1: a\\x1b]0;title\\x07b" in text
    assert [line.split(" ", 1)[0] for line in text.splitlines()] == ["memory", "loaded", "brake", "refused", "problem"]
    assert all(char.isprintable() for char in text.replace("\n", ""))


def test_json_gives_the_brake_s_state_and_a_problems_list(spark_status):
    code, out, _ = spark_status("--json")
    status = standard(out)
    assert code == 0 and status["brake"]["state"] == "no hold" and status["problems"] == []
    assert status["loaded"] == [{"model": "coder", "state": "ready", "resident": False, "footprint_gib": 28.0}]


def test_json_stays_standard_for_memory_that_isnt_a_number(spark_status):
    code, out, _ = spark_status("--json", mem=MemInfo(float("nan"), float("inf")))
    status = standard(out)
    assert code == 0 and status["memory"] is None and "memory" in status["problems"][0]
