import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from spark import cli, paths, watch
from spark.llamaswap import Running
from spark.status import gather
from spark.memory import MemInfo
from spark.registry import load_registry

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")
FIXTURE = Path(__file__).parent / "fixtures" / "models.yaml"


def test_parse_metrics_reads_prometheus_lines():
    text = "\n".join([
        "# HELP llamacpp:requests_processing the requests being processed now",
        "# TYPE llamacpp:requests_deferred gauge",
        "llamacpp:requests_processing 2",
        "llamacpp:requests_deferred 3",
        'llamacpp:tokens_generated_total{model="coder"} 100',
        "not a sample",
        "llamacpp:broken nan",
        "llamacpp:infinite inf",
    ])
    assert watch.parse_metrics(text) == {
        "llamacpp:requests_processing": [2.0],
        "llamacpp:requests_deferred": [3.0],
        "llamacpp:tokens_generated_total": [100.0],
    }


@pytest.mark.parametrize("url, loopback", [
    pytest.param("http://127.0.0.1:5801", True, id="127.0.0.1"),
    pytest.param("http://localhost:5801", True, id="localhost"),
    pytest.param("http://[::1]:5801", True, id="ipv6 loopback"),
    pytest.param("http://10.0.0.5:5801", False, id="a LAN address"),  # leakcheck: allow
    pytest.param("https://127.0.0.1:5801", False, id="https"),
    pytest.param("http://127.0.0.1:99999", False, id="no port"),
    pytest.param("127.0.0.1:5801", False, id="no scheme"),
])
def test_only_http_on_a_loopback_address_is_fetched(url, loopback):
    assert watch._loopback(url) is loopback


class Metrics(BaseHTTPRequestHandler):
    """A llama.cpp engine's /metrics: the two gauges render's --metrics serves."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/metrics":
            body = b"llamacpp:requests_processing 2\nllamacpp:requests_deferred 1\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@contextmanager
def serving(handler):
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_an_engine_reads_its_own_counters():
    with serving(Metrics) as proxy:
        problems = {}
        entries = watch.engine_entries([Running("coder", "ready", proxy)], REG, problems)
    assert entries == [{"model": "coder", "in_flight": 2, "deferred": 1, "note": None}]
    assert problems == {}


def test_a_whisper_engine_has_no_metrics_endpoint():
    problems = {}
    entries = watch.engine_entries([Running("stt", "ready", "http://127.0.0.1:5803")], REG, problems)
    assert entries == [{"model": "stt", "in_flight": None, "deferred": None,
                        "note": "no metrics endpoint (whisper.cpp)"}]
    assert problems == {}


def test_an_answer_without_a_port_is_said_not_fetched():
    problems = {}
    entries = watch.engine_entries([Running("coder", "ready")], REG, problems)
    assert entries[0]["note"] == "no engine port in llama-swap's /running answer"
    assert problems == {}


def test_a_port_beyond_loopback_is_reported_and_never_fetched():
    # Nothing listens at 10.0.0.5: the guard refuses before any request, so this runs with no server at all.  # leakcheck: allow
    problems = {}
    entries = watch.engine_entries([Running("coder", "ready", "http://10.0.0.5:5801")], REG, problems)  # leakcheck: allow
    assert entries[0]["note"] == "the engine's port http://10.0.0.5:5801 isn't loopback, so it isn't fetched"  # leakcheck: allow
    assert problems == {"engines": f"coder: {entries[0]['note']}"}


def test_an_engine_whose_metrics_are_off_is_said():
    # b11146's /metrics is off until render's --metrics reaches the engine and `make apply` restarts it: the 404
    # says that, instead of reading as nothing.
    class NoMetrics(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

    with serving(NoMetrics) as proxy:
        problems = {}
        entries = watch.engine_entries([Running("coder", "ready", proxy)], REG, problems)
    assert entries[0]["note"] == "its /metrics answers HTTP 404 (the engine needs render's --metrics: `make apply`)"
    assert "engines" in problems


def test_an_engine_that_isnt_listening_is_said():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    problems = {}
    entries = watch.engine_entries([Running("coder", "ready", f"http://127.0.0.1:{port}")], REG, problems)
    assert entries[0]["note"] == "its /metrics is unreachable (URLError)"
    assert "engines" in problems


def test_the_engines_line_of_the_text_view():
    with serving(Metrics) as proxy:
        running = [Running("coder", "ready", proxy)]
        status = watch.format_text(gather(MemInfo(121.7, 70.0), REG, running, None),
                                   watch.engine_entries(running, REG, {}))
    assert "engines  coder: 2 in flight, 1 deferred" in status.splitlines()


@pytest.fixture()
def spark_watch(tmp_path, monkeypatch, capsys):
    """`spark watch [flags]` on a fake box: the fixture registry, the state folder tmp_path/"state", launch's
    folder tmp_path/"launch", 70 of 121.7 GiB available, and a fake llama-swap whose /running answers with a
    keyword: a list of Running (a test points a proxy at a real metrics server), an error, or None for the real
    client. Gives the exit code, what it printed, and how each LlamaSwap was made."""
    (tmp_path / "state").mkdir()
    (tmp_path / "launch").mkdir()
    monkeypatch.delenv("SPARK_API_KEY", raising=False)

    def run(*flags, answer=()):
        made = []

        class FakeLlamaSwap:
            def __init__(self, base_url, api_key, timeout=10.0):
                made.append({"url": base_url, "key": api_key, "timeout": timeout})

            def running(self):
                if isinstance(answer, Exception):
                    raise answer
                return list(answer)

        monkeypatch.setattr(paths, "REGISTRY", FIXTURE)
        monkeypatch.setattr(paths, "STATE", tmp_path / "state")
        monkeypatch.setattr(paths, "LAUNCH", tmp_path / "launch")
        monkeypatch.setattr("spark.watch.read_meminfo", lambda: MemInfo(121.7, 70.0))
        monkeypatch.setattr("spark.watch.LlamaSwap", FakeLlamaSwap)
        code = cli.main(["watch", *flags])
        return code, capsys.readouterr().out, made

    return run


def test_watch_shows_the_engines_line(spark_watch):
    code, out, made = spark_watch("--once")
    assert code == 0 and out.splitlines() == [
        "memory   70 GiB available of 122 GiB · 50 GiB before the brake (20 GiB)",
        "loaded   nothing",
        "engines  nothing",
        "brake    no hold · no start check recorded (`make logs s=brake`)",
    ]
    assert made == [{"url": paths.LLAMASWAP_URL, "key": None, "timeout": 3}]


def test_watch_reads_the_engine_counts_end_to_end(spark_watch):
    with serving(Metrics) as proxy:
        code, out, _ = spark_watch("--once", answer=(Running("coder", "ready", proxy),))
    assert code == 0
    assert "loaded   coder (on demand, ~28 GiB, ready)" in out.splitlines()
    assert "engines  coder: 2 in flight, 1 deferred" in out.splitlines()


def test_watch_json_gives_the_engines(spark_watch):
    with serving(Metrics) as proxy:
        code, out, _ = spark_watch("--json", answer=(Running("coder", "ready", proxy),))
    status = json.loads(out)
    assert code == 0 and status["engines"] == [{"model": "coder", "in_flight": 2, "deferred": 1, "note": None}]


def test_the_live_loop_refreshes_and_stops_at_interrupt(spark_watch, monkeypatch):
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr("spark.watch.time.sleep", sleep)
    code, out, _ = spark_watch("--interval", "0.5")
    assert code == 0 and sleeps == [0.5] and "engines" in out
