"""The failure notifier's script, `stack/host/local-ai-notify` (phase-2a.md, Task 26), run as its unit runs it, with
stand-ins: a curl that records its arguments, its standard input and its environment; a date that gives the tests'
clock; a uname that gives the box's name; and temporary credential and state folders. Nothing here reaches ntfy: the
stand-in address is `.invalid`, and the one test with Ubuntu's curl sends to a stand-in HTTP server on a free port."""

import http.server
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

import pytest

from spark import gateproto, messages
from spark.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "stack" / "host" / "local-ai-notify"
UNIT_TEMPLATE = ROOT / "stack" / "templates" / "local-ai-notify@.service"
REGISTRY = load_registry(ROOT / "stack" / "models.yaml")
CURL_ARGS = ["-q", "--fail", "--silent", "--max-time", "10", "--config", "-"]

# Stand-in values, none of them a secret: ntfy's address, the notifier's topic and its token's header line.
URL = "http://ntfy.stand-in.invalid:8080"
TOPIC = "stand-in-topic-n4"
TOKEN_LINE = "Authorization: Bearer stand-in-token-n4"
T0 = int(datetime(2026, 10, 14, 9, 14, tzinfo=timezone.utc).timestamp())  # the stand-in date's clock reads 09:14

# Each unit the notifier alerts for: its notification type, and the variable its unit gives that type's priority in.
UNITS = {"local-ai-gate.service": ("gate_down", "NOTIFY_GATE"),
         "local-ai-front.service": ("front_down", "NOTIFY_FRONT"),
         "local-ai-llama-swap.service": ("llama_swap_down", "NOTIFY_LLAMA_SWAP"),
         "local-ai-brake.service": ("brake_down", "NOTIFY_BRAKE")}
# $MONITOR_SERVICE_RESULT, as systemd sets it for an OnFailure= unit, in words; None: systemd set none.
RESULTS = {"exit-code": "it crashed", "signal": "it crashed", "core-dump": "it crashed",
           "watchdog": "it stopped answering", "oom-kill": "it ran out of memory", "timeout": "it timed out",
           "start-limit-hit": "it failed", None: "it failed"}
GATE_DOWN = ("The gate on brightroar stopped at 09:14 (it crashed; it is restarting). Loaded models still answer; new "
             "loads are refused until it's back. On the Spark, `make doctor` shows what's wrong.")


class Call:
    def __init__(self, folder: Path):
        self.args = (folder / "args").read_text().splitlines()
        self.raw = (folder / "config").read_text()
        self.config = curl_config(self.raw)
        self.env = (folder / "env").read_text().splitlines()

    @property
    def data(self) -> str:
        [(name, text)] = [line for line in self.config if line[0] == "data-raw"]
        return text


def curl_config(text: str) -> list[tuple[str, str]]:
    """curl's --config text, each `name = "value"` line read back as curl reads it, in order: a backslash takes the
    next character as itself (\\n, \\t, \\r and \\v as their controls), and an unescaped quote can't come before the
    last."""
    found = []
    for line in text.splitlines():
        name, sep, quoted = line.partition(" = ")
        assert sep and len(quoted) >= 2 and quoted[0] == quoted[-1] == '"', line
        chars, value, i = quoted[1:-1], [], 0
        while i < len(chars):
            if chars[i] == "\\":
                i += 1
                value.append({"n": "\n", "t": "\t", "r": "\r", "v": "\v"}.get(chars[i], chars[i]))
            else:
                assert chars[i] != '"', line
                value.append(chars[i])
            i += 1
        found.append((name, "".join(value)))
    return found


def stand_in(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


class Box:
    """The notifier's world: its stand-in tools, its credential folder holding the token's header line, its state
    folder, and the clock, at T0 until `at` moves it."""

    def __init__(self, tmp_path: Path):
        self.bin, self.calls_dir = tmp_path / "bin", tmp_path / "calls"
        self.creds, self.state, self.clock = tmp_path / "credentials", tmp_path / "state", tmp_path / "clock"
        for folder in (self.bin, self.calls_dir, self.creds, self.state, self.clock):
            folder.mkdir()
        self.token(TOKEN_LINE + "\n")
        calls, clock = shlex.quote(str(self.calls_dir)), shlex.quote(str(self.clock))
        # curl runs with an empty environment, so its stand-in finds its folders by name, and its tools by path.
        stand_in(self.bin / "curl", f"""n=0
[ -f {calls}/count ] && read -r n < {calls}/count
n=$((n + 1))
echo "$n" > {calls}/count
/bin/mkdir {calls}/"$n"
printf '%s\\n' "$@" > {calls}/"$n"/args
/bin/cat > {calls}/"$n"/config
/usr/bin/env > {calls}/"$n"/env
code=0
[ -f {calls}/exit ] && read -r code < {calls}/exit
exit "$code"
""")
        stand_in(self.bin / "date", f"""case $* in
+%s) exec /bin/cat {clock}/seconds ;;
+%H:%M) exec /bin/cat {clock}/hhmm ;;
esac
echo "stand-in date: no format $*" >&2
exit 64
""")
        stand_in(self.bin / "uname",
                 '[ "$*" = -n ] && { echo brightroar.local; exit 0; }\necho "stand-in uname: $*" >&2\nexit 64\n')
        self.at(0)

    def token(self, content: str | None) -> None:
        """The ntfy-token credential's file: `content`, or none at all."""
        path = self.creds / "ntfy-token"
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(content)

    def at(self, offset_s: int) -> None:
        now = T0 + offset_s
        (self.clock / "seconds").write_text(f"{now}\n")
        (self.clock / "hhmm").write_text(datetime.fromtimestamp(now, timezone.utc).strftime("%H:%M") + "\n")

    def curl_exits(self, code: int) -> None:
        (self.calls_dir / "exit").write_text(f"{code}\n")

    def env(self, result: str | None = "exit-code", **changes: str | None) -> dict[str, str]:
        """The unit's environment for one run (Task 25's unit: the values file's two, the registry's priorities, and
        what systemd sets), with the stand-ins first on PATH; a change to None leaves that variable out."""
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "CURL": str(self.bin / "curl"), "NTFY_URL": URL,
               "NTFY_TOPIC_NOTIFY": TOPIC, "CREDENTIALS_DIRECTORY": str(self.creds),
               "STATE_DIRECTORY": str(self.state), **dict.fromkeys((var for _, var in UNITS.values()), "high")}
        if result is not None:
            env["MONITOR_SERVICE_RESULT"] = result
        env.update(changes)
        return {name: value for name, value in env.items() if value is not None}

    def run(self, unit: str = "local-ai-gate.service", result: str | None = "exit-code", *, shell: Sequence[str] = (),
            **changes: str | None) -> subprocess.CompletedProcess[str]:
        """The script, as its unit starts it: executed by its own `#!` line, the failed unit its one argument."""
        return subprocess.run([*shell, str(SCRIPT), unit], env=self.env(result, **changes), capture_output=True,
                              text=True, timeout=30, check=False)

    def calls(self) -> list[Call]:
        return [Call(folder) for folder in sorted((p for p in self.calls_dir.iterdir() if p.is_dir()),
                                                  key=lambda p: int(p.name))]

    def stamps(self) -> list[str]:
        return sorted(path.name for path in self.state.iterdir())


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def lines(run: subprocess.CompletedProcess[str]) -> list[str]:
    return run.stderr.splitlines()


def test_the_notifier_sends_unit_result_and_time_only(box):
    run = box.run("local-ai-gate.service", "exit-code")
    assert run.returncode == 0, run.stderr
    [call] = box.calls()
    assert call.args == CURL_ARGS  # -q first, so no .curlrc is read; its config on stdin
    assert call.config == [("url", f"{URL}/{TOPIC}"), ("header", f"@{box.creds}/ntfy-token"),
                           ("header", "Priority: high"), ("data-raw", GATE_DOWN)]
    assert run.stdout == "" and lines(run) == ["Sent the alert: local-ai-gate.service failed at 09:14 (it crashed)."]


@pytest.mark.parametrize("result", list(RESULTS), ids=lambda r: r or "unset")
def test_each_result_reads_in_words(box, result):
    assert box.run("local-ai-front.service", result).returncode == 0
    [call] = box.calls()
    assert call.data.startswith(f"The front on brightroar stopped at 09:14 ({RESULTS[result]}; it is restarting). ")


def test_the_notifier_sends_at_most_one_alert_per_unit_per_5_minutes(box):
    script = SCRIPT.read_text()
    assert f"\nNOTIFIER_EVERY_S={gateproto.NOTIFIER_EVERY_S} " in script  # the gate's 5 minutes, and Task 7's words
    assert box.run("local-ai-gate.service").returncode == 0
    assert box.stamps() == ["local-ai-gate.service.last-alert"]
    box.at(10)
    held = box.run("local-ai-gate.service", "signal")
    assert held.returncode == 0 and len(box.calls()) == 1
    assert lines(held) == ["No alert was sent: local-ai-gate.service failed at 09:14 (it crashed), and its last "
                           "alert went 10 s ago (at most one per unit per 5 minutes)."]
    assert box.run("local-ai-front.service").returncode == 0 and len(box.calls()) == 2  # each unit its own
    box.at(299)  # 5 minutes from the gate's last alert, not from the failure held back at 10 s
    assert box.run("local-ai-gate.service").returncode == 0 and len(box.calls()) == 2
    box.at(301)
    assert box.run("local-ai-gate.service").returncode == 0 and len(box.calls()) == 3
    assert box.calls()[2].data == GATE_DOWN.replace("09:14", "09:19")
    assert box.stamps() == ["local-ai-front.service.last-alert", "local-ai-gate.service.last-alert"]


@pytest.mark.parametrize("stamp", ["garbage\n", f"{T0 + 3600}\n"], ids=["damaged", "from a clock since put back"])
def test_a_stamp_it_cant_use_holds_nothing_back(box, stamp):
    (box.state / "local-ai-gate.service.last-alert").write_text(stamp)
    run = box.run()
    assert run.returncode == 0 and len(box.calls()) == 1
    assert lines(run) == ["Sent the alert: local-ai-gate.service failed at 09:14 (it crashed)."]
    assert (box.state / "local-ai-gate.service.last-alert").read_text() == f"{T0}\n"


def test_without_its_state_folder_every_failure_sends_and_says_so(box):
    for offset in (0, 10):
        box.at(offset)
        run = box.run(STATE_DIRECTORY=None)
        assert run.returncode == 0 and lines(run) == [
            "STATE_DIRECTORY isn't a folder, so the limit of one alert per unit per 5 minutes can't hold; this one "
            "goes.", "Sent the alert: local-ai-gate.service failed at 09:14 (it crashed)."]
    assert len(box.calls()) == 2


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only folder")
def test_a_stamp_it_cant_write_still_sends_and_holds_nothing_back(box):
    box.state.chmod(0o500)
    try:
        run = box.run()
        assert run.returncode == 0 and lines(run) == [
            "Its stamp couldn't be written in STATE_DIRECTORY, so the next failure's alert isn't held back.",
            "Sent the alert: local-ai-gate.service failed at 09:14 (it crashed)."]
        assert box.run().returncode == 0 and len(box.calls()) == 2
    finally:
        box.state.chmod(0o700)


@pytest.mark.parametrize("priority", [None, "urgent", ""], ids=["unset", "not one of the four", "empty"])
def test_a_priority_it_doesnt_know_sends_at_high_and_says_so(box, priority):
    run = box.run(NOTIFY_GATE=priority)
    assert run.returncode == 0 and lines(run) == [
        "NOTIFY_GATE isn't high, default, low or off, so the alert goes at high.",
        "Sent the alert: local-ai-gate.service failed at 09:14 (it crashed)."]
    [call] = box.calls()
    assert call.config[2] == ("header", "Priority: high")


@pytest.mark.parametrize("topic", ['stand-in "quoted" back\\slash', 'stand-in\nurl = "elsewhere"\ttab'],
                         ids=["a quote and a backslash", "a line break and a tab"])
def test_a_quote_in_a_value_is_escaped_for_curls_config(box, topic):
    assert box.run(NTFY_TOPIC_NOTIFY=topic).returncode == 0
    [call] = box.calls()
    url_line = call.raw.splitlines()[0]
    escaped = topic.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    assert url_line == f'url = "{URL}/{escaped}"'
    assert len(call.raw.splitlines()) == 4 and call.config[0] == ("url", f"{URL}/{topic}")


def test_the_token_reaches_curl_as_a_header_file_never_argv(box):
    assert box.run().returncode == 0
    [call] = box.calls()
    assert ("header", f"@{box.creds}/ntfy-token") in call.config
    secret = TOKEN_LINE.split()[-1]
    for text in (call.args, [call.raw], call.env):
        assert not any(secret in item or "Bearer" in item for item in text)
    assert not any(URL in arg or "stand-in" in arg or TOPIC in arg for arg in call.args)
    # curl gets an empty environment (env -i): only what the stand-in's own shell sets, nothing of the unit's.
    assert {line.partition("=")[0] for line in call.env} <= {"PWD"}


def test_an_off_priority_sends_nothing(box):
    run = box.run("local-ai-gate.service", NOTIFY_GATE="off")
    assert (run.returncode, run.stderr, box.calls(), box.stamps()) == (0, "", [], [])
    # Its own unit's priority only: the front's, still high, sends.
    assert box.run("local-ai-front.service", NOTIFY_GATE="off").returncode == 0 and len(box.calls()) == 1


def test_each_unit_sends_at_its_own_priority(box):
    priorities = {"NOTIFY_GATE": "low", "NOTIFY_FRONT": "default", "NOTIFY_LLAMA_SWAP": "high", "NOTIFY_BRAKE": "low"}
    for unit in UNITS:
        assert box.run(unit, **priorities).returncode == 0
    assert [call.config[2] for call in box.calls()] == [("header", f"Priority: {p}") for p in priorities.values()]


NOT_SET_UP = [
    pytest.param({"NTFY_URL": None}, TOKEN_LINE, "NTFY_URL isn't set", id="no NTFY_URL"),
    pytest.param({"NTFY_TOPIC_NOTIFY": None}, TOKEN_LINE, "NTFY_TOPIC_NOTIFY isn't set", id="no NTFY_TOPIC_NOTIFY"),
    pytest.param({}, "not-set-up", "the credential ntfy-token is Task 25's placeholder", id="the placeholder"),
    pytest.param({}, None, "the credential ntfy-token is missing", id="no credential file"),
    pytest.param({"NTFY_URL": None, "NTFY_TOPIC_NOTIFY": None}, "not-set-up",
                 "NTFY_URL isn't set; NTFY_TOPIC_NOTIFY isn't set; the credential ntfy-token is Task 25's placeholder",
                 id="2a's state: none of them"),
    pytest.param({"CREDENTIALS_DIRECTORY": None}, TOKEN_LINE,
                 "CREDENTIALS_DIRECTORY isn't set, so the credential ntfy-token can't be read",
                 id="no credential folder"),
    pytest.param({}, "", "the credential ntfy-token is empty", id="an empty credential"),
    pytest.param({}, "stand-in-token-n4\n", "the credential ntfy-token isn't one 'Name: value' header line",
                 id="a bare token"),
    pytest.param({}, TOKEN_LINE + "\nX-Second: stand-in\n",
                 "the credential ntfy-token isn't one 'Name: value' header line", id="two lines"),
    pytest.param({"NTFY_URL": "ntfy.stand-in.invalid:8080"}, TOKEN_LINE,
                 "NTFY_URL isn't an http(s) URL with a host", id="NTFY_URL not a URL"),
]


@pytest.mark.parametrize(("changes", "token", "why"), NOT_SET_UP)
def test_without_ntfy_set_up_it_sends_nothing_and_says_so(box, changes, token, why):
    box.token(token)
    run = box.run("local-ai-gate.service", "exit-code", **changes)
    assert run.returncode == 0 and box.calls() == [] and box.stamps() == []
    assert lines(run) == ["ntfy isn't set up (Task 2, Phase 5), so no alert was sent: local-ai-gate.service failed at "
                          f"09:14 (it crashed). Why: {why}."]
    assert "stand-in" not in run.stderr  # what is missing, by its variable's or credential's name, never a value
    # Once it is all set up, the next failure sends at once: no stamp held it back.
    box.token(TOKEN_LINE + "\n")
    box.at(10)
    assert box.run("local-ai-gate.service").returncode == 0
    [call] = box.calls()
    assert call.data == GATE_DOWN


@pytest.mark.parametrize("unit", list(UNITS))
def test_the_script_and_messages_agree(box, monkeypatch, unit):
    # The controller's ruling at Task 7: result_words is the script's result in words, to which the words add "; it is
    # restarting" (and " within 2 s" for the brake); the host is the box's short host name.
    monkeypatch.setattr(socket, "gethostname", lambda: "brightroar.local")
    kind, _ = UNITS[unit]
    at = datetime(2026, 10, 14, 9, 14).astimezone()  # 09:14 on the local clock, as the stand-in date reads
    for n, (result, words) in enumerate(RESULTS.items(), start=1):
        box.at(n * (gateproto.NOTIFIER_EVERY_S + 1))
        box.clock.joinpath("hhmm").write_text("09:14\n")
        assert box.run(unit, result).returncode == 0
        expected = messages.notification(kind, REGISTRY, at=at, result_words=words)
        assert box.calls()[-1].data == expected.message
        assert box.calls()[-1].config[2] == ("header", f"Priority: {expected.priority}")


@pytest.mark.parametrize(("code", "why"), [(22, "ntfy answered with an error"), (28, "ntfy didn't answer within 10 s"),
                                           (6, "it couldn't reach ntfy"), (7, "it couldn't reach ntfy"),
                                           (35, "curl failed")])
def test_a_failed_send_says_why_and_holds_nothing_back(box, code, why):
    box.curl_exits(code)
    run = box.run("local-ai-brake.service", "oom-kill")
    assert run.returncode == 1 and len(box.calls()) == 1 and box.stamps() == []  # the notifier's unit fails, seen
    assert lines(run) == [f"No alert was sent: local-ai-brake.service failed at 09:14 (it ran out of memory), and "
                          f"{why} (curl's exit {code})."]
    box.curl_exits(0)
    box.at(10)
    assert box.run("local-ai-brake.service").returncode == 0 and len(box.calls()) == 2


def test_a_curl_that_cant_be_run_is_said(box, tmp_path):
    missing = tmp_path / "no-curl-here"
    run = box.run(CURL=str(missing))
    assert run.returncode == 1 and box.stamps() == []
    assert lines(run) == [f"No alert was sent: local-ai-gate.service failed at 09:14 (it crashed), and {missing} "
                          "couldn't be run (curl's exit 127)."]


def test_a_unit_it_doesnt_alert_for_is_refused(box):
    run = box.run("local-ai-pull.service")
    assert run.returncode == 2 and box.calls() == [] and box.stamps() == []
    assert lines(run) == ["local-ai-pull.service isn't a unit the failure notifier alerts for (the front, the gate, "
                          "the brake or llama-swap), so no alert was sent."]


def test_the_unit_runs_this_script_with_ubuntus_curl():
    # Task 25's unit starts it by the name bootstrap installs it under (Task 27), and never sets CURL.
    unit = UNIT_TEMPLATE.read_text()
    assert "ExecStart=/usr/local/libexec/local-ai-notify %i\n" in unit and "CURL" not in unit
    assert SCRIPT.read_text().startswith("#!/bin/sh\n") and os.access(SCRIPT, os.X_OK)
    assert re.search(r'^CURL=\$\{CURL:-/usr/bin/curl\}$', SCRIPT.read_text(), re.M)


@pytest.mark.parametrize("shell", [["dash"], ["bash", "--posix"]], ids=["dash", "bash --posix"])
def test_it_runs_alike_under_dash_and_bash(box, shell):
    if shutil.which(shell[0]) is None:
        pytest.skip(f"no {shell[0]} here")
    topic = 'stand-in "quoted" back\\slash\nline'
    assert box.run(shell=shell, NTFY_TOPIC_NOTIFY=topic).returncode == 0
    [call] = box.calls()
    assert call.config == [("url", f"{URL}/{topic}"), ("header", f"@{box.creds}/ntfy-token"),
                           ("header", "Priority: high"), ("data-raw", GATE_DOWN)]


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)


def test_make_lint_shellchecks_it():
    run = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), "lint"], capture_output=True, text=True, check=True)
    assert "stack/host/local-ai-notify" in run.stdout.split()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert port not in (9100, 900, 3000, 8888) and not 800 <= port < 1024 and not 5800 <= port < 6000
    return port


@pytest.mark.skipif(not os.access("/usr/bin/curl", os.X_OK), reason="no /usr/bin/curl here")
def test_ubuntus_curl_sends_it_as_its_config_says(box):
    # Ubuntu's curl, the unit's (CURL unset), against a stand-in HTTP server on a free port: never ntfy's, nor a live
    # one. It sends the topic, the token's header from its file, the priority and the text as they are; a refusal or no
    # server at all is said in words, and curl's own error, which can name ntfy's host, never reaches the journal.
    seen = []

    class Answer(http.server.BaseHTTPRequestHandler):
        status = 200

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen.append({"path": self.path, "body": body.decode(), "priority": self.headers.get("Priority"),
                         "auth": self.headers.get("Authorization")})
            self.send_response(self.status)
            self.end_headers()
            self.wfile.write(b'{"topic": "stand-in-topic-n4"}')

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Answer)
    port = server.server_address[1]
    assert port not in (9100, 900, 3000, 8888) and not 800 <= port < 1024 and not 5800 <= port < 6000
    real = {"CURL": None, "NTFY_URL": f"http://127.0.0.1:{port}/"}
    try:
        for status, offset in ((200, 0), (403, 400)):
            Answer.status = status
            box.at(offset)
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()
            run = box.run("local-ai-gate.service", **real)
            thread.join(10)
            if status == 200:
                assert run.returncode == 0, run.stderr
                assert run.stdout == "" and lines(run) == [
                    "Sent the alert: local-ai-gate.service failed at 09:14 (it crashed)."]
            else:
                assert run.returncode == 1 and lines(run) == [
                    "No alert was sent: local-ai-gate.service failed at 09:20 (it crashed), and ntfy answered with "
                    "an error (curl's exit 22)."]
    finally:
        server.server_close()
    first, refused = seen
    assert first == {"path": f"/{TOPIC}", "body": GATE_DOWN, "priority": "high", "auth": "Bearer stand-in-token-n4"}
    assert refused["body"] == GATE_DOWN.replace("09:14", "09:20")
    box.at(800)
    gone = box.run("local-ai-gate.service", CURL=None, NTFY_URL=f"http://127.0.0.1:{free_port()}")
    assert gone.returncode == 1 and lines(gone) == [
        "No alert was sent: local-ai-gate.service failed at 09:27 (it crashed), and it couldn't reach ntfy (curl's "
        "exit 7)."]
    assert "127.0.0.1" not in gone.stderr + run.stderr
