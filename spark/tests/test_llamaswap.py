import http.client
import json
import threading
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, Running, key_from_env

SEEN: list[tuple[str, str, str | None]] = []
ANSWER: dict = {}  # a test sets what GET /running answers with the good key: a body, or "short"
PROXIED: list[tuple[str, str | None]] = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: dict | str):
        data = body if isinstance(body, str) else json.dumps(body)
        self.send_response(code)
        self.end_headers()
        self.wfile.write(data.encode())

    def _sent(self) -> str:  # the target as the client sent it: self.path turns a leading "//" into "/"
        return self.requestline.split()[1]

    def do_GET(self):
        SEEN.append(("GET", self._sent(), self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer good":
            return self._reply(401, {"error": {"message": "unauthorized: invalid or missing API key"}})
        if ANSWER.get("short"):  # promises 100 bytes, sends 13, and the connection closes
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            return self.wfile.write(b'{"running": [')
        self._reply(200, ANSWER.get("body", {"running": [{"model": "coder", "state": "ready", "cmd": "x",
                                                          "proxy": "y", "ttl": 0}]}))

    def do_POST(self):
        SEEN.append(("POST", self._sent(), self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer good":
            return self._reply(401, {"error": {"message": "unauthorized: invalid or missing API key"}})
        if self.path.endswith("/nope"):
            return self._reply(404, {"error": {"message": "model not found"}})
        self._reply(200, "OK")


class Proxy(BaseHTTPRequestHandler):  # an HTTP proxy: records what reaches it, answers as an idle llama-swap
    def log_message(self, *args):
        pass

    def do_GET(self):
        PROXIED.append((self.path, self.headers.get("Authorization")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"running": []}')


@contextmanager
def serving(handler):
    httpd = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def server():
    SEEN.clear()
    ANSWER.clear()
    with serving(Fake) as url:
        yield url


def chained(err: BaseException) -> list[BaseException]:
    """The error, and every error chained to it as cause or context, whether a traceback shows it or not."""
    found, todo = [], [err]
    while todo:
        err = todo.pop()
        if err is not None and all(err is not seen for seen in found):
            found.append(err)
            todo += [err.__cause__, err.__context__]
    return found


def test_running_sends_the_key(server):
    assert LlamaSwap(server, "good").running() == [Running("coder", "ready")]
    assert SEEN == [("GET", "/running", "Bearer good")]


def test_wrong_key_is_a_clear_error(server):
    with pytest.raises(LlamaSwapError, match="401"):
        LlamaSwap(server, "bad").running()


def test_unload_posts_to_the_model(server):
    LlamaSwap(server, "good").unload("coder")
    assert SEEN == [("POST", "/api/models/unload/coder", "Bearer good")]


def test_unload_unknown_model_raises(server):
    with pytest.raises(LlamaSwapError, match="404"):
        LlamaSwap(server, "good").unload("nope")


def test_unreachable_is_a_clear_error():
    with pytest.raises(LlamaSwapUnreachable, match="unreachable"):
        LlamaSwap("http://127.0.0.1:9", "good", timeout=0.5).running()


def test_a_wrong_key_is_not_mistaken_for_unreachable(server):
    with pytest.raises(LlamaSwapError) as caught:
        LlamaSwap(server, "bad").running()
    assert not isinstance(caught.value, LlamaSwapUnreachable)



@pytest.mark.parametrize("answer", [{"body": "<html>busy</html>"}, {"body": "[1, 2]"}, {"body": {"running": "coder"}},
                                    {"body": {"running": [{"name": "coder"}]}}, {"short": True}],
                         ids=["not-json", "a-list", "a-string", "no-model", "cut-short"])
def test_an_answer_it_cant_read_is_an_error_not_a_crash(server, answer):
    # Not what v257 sends: an error that says so, which apply and the brake handle like a wrong key.
    ANSWER.update(answer)
    with pytest.raises(LlamaSwapError) as caught:
        LlamaSwap(server, "good").running()
    assert not isinstance(caught.value, LlamaSwapUnreachable)


@pytest.mark.parametrize("body", [
    pytest.param({}, id="no-running"),
    pytest.param({"running": {}}, id="running-an-object"),
    pytest.param({"running": ""}, id="running-empty-string"),
    pytest.param({"running": None}, id="running-null"),
    pytest.param({"running": [{"model": None, "state": "ready"}]}, id="model-null"),
    pytest.param({"running": [{"model": 7, "state": "ready"}]}, id="model-a-number"),
    pytest.param({"running": [{"model": "coder", "state": 1}]}, id="state-a-number"),
    pytest.param({"running": ["coder"]}, id="entry-not-an-object"),
    pytest.param("[" * 100_000 + "]" * 100_000, id="nested-too-deep"),  # json raises RecursionError
])
def test_an_answer_not_in_v257s_shape_is_an_error_not_an_empty_list(server, body):
    # v257 always answers {"running": [...]}, each entry with a string model and state. Read as [], anything
    # else would tell apply that restarting llama-swap stops nothing, and the brake that nothing can be unloaded.
    ANSWER["body"] = body
    with pytest.raises(LlamaSwapError) as caught:
        LlamaSwap(server, "good").running()
    assert not isinstance(caught.value, LlamaSwapUnreachable)


def test_nothing_running_is_an_empty_list(server):
    ANSWER["body"] = {"running": []}  # v257's answer when idle
    assert LlamaSwap(server, "good").running() == []


@pytest.mark.parametrize("key", [
    pytest.param("fake-key\r", id="cr-at-the-end"),  # a CRLF line in a sourced secrets file
    pytest.param("fake-key\nsecond-line", id="lf-inside"),
    pytest.param("fake-key-\u20ac", id="not-ascii"),
])
def test_a_key_a_header_cant_carry_is_refused_and_never_shown(server, key):
    with pytest.raises(LlamaSwapError, match="API key") as caught:
        LlamaSwap(server, key).running()
    assert not isinstance(caught.value, LlamaSwapUnreachable)
    assert all("fake-key" not in f"{err} {err!r}" for err in chained(caught.value))
    assert SEEN == []  # refused before anything was sent


def test_a_header_http_client_refuses_is_an_error_that_never_shows_it(server, monkeypatch):
    # http.client's ValueError names the header's value, so it must be neither shown nor chained.
    putheader = http.client.HTTPConnection.putheader

    def refuse_the_key(self, header, *values):
        if header == "Authorization":
            raise ValueError(f"Invalid header value {values!r}")
        return putheader(self, header, *values)

    monkeypatch.setattr(http.client.HTTPConnection, "putheader", refuse_the_key)
    with pytest.raises(LlamaSwapError) as caught:
        LlamaSwap(server, "fake-key").running()
    assert not isinstance(caught.value, LlamaSwapUnreachable)
    assert all("fake-key" not in f"{err} {err!r}" for err in chained(caught.value))


def test_a_proxy_in_the_environment_never_gets_the_request(server, monkeypatch):
    PROXIED.clear()
    with serving(Proxy) as proxy:
        monkeypatch.setenv("http_proxy", proxy)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.setattr(urllib.request, "_opener", None)  # urlopen keeps the proxies it read on first use
        running = LlamaSwap(server, "good").running()
    assert PROXIED == []  # nothing, so no key, reached the proxy
    assert running == [Running("coder", "ready")] and SEEN == [("GET", "/running", "Bearer good")]


@pytest.mark.parametrize("url", [
    pytest.param("127.0.0.1:9100", id="no-scheme"),
    pytest.param("localhost", id="host-only"),
    pytest.param("http://127.0.0.1:port", id="port-not-a-number"),
    pytest.param("http://127.0.0.1:99999", id="port-out-of-range"),
    pytest.param("http://", id="no-host"),
    pytest.param("http://127.0.0.1\x01:9100", id="control-character"),
])
def test_a_bad_url_is_an_error_that_says_so(url):
    with pytest.raises(LlamaSwapError, match="invalid URL") as caught:
        LlamaSwap(url, "good", timeout=0.5).running()
    assert not isinstance(caught.value, LlamaSwapUnreachable)


@pytest.mark.parametrize("key", [None, ""], ids=["none", "empty"])
def test_no_key_sends_no_header(server, key):
    with pytest.raises(LlamaSwapError, match="401"):
        LlamaSwap(server, key).running()
    assert SEEN == [("GET", "/running", None)]


def test_key_from_env_is_none_unless_set_and_not_empty(monkeypatch):
    monkeypatch.setenv("SPARK_TEST_KEY", "fake-key")
    assert key_from_env("SPARK_TEST_KEY") == "fake-key"
    monkeypatch.setenv("SPARK_TEST_KEY", "")
    assert key_from_env("SPARK_TEST_KEY") is None
    monkeypatch.delenv("SPARK_TEST_KEY")
    assert key_from_env("SPARK_TEST_KEY") is None


def test_a_trailing_slash_on_the_url_is_dropped(server):
    LlamaSwap(server + "/", "good").running()
    assert SEEN == [("GET", "/running", "Bearer good")]


def test_unload_sends_the_model_name_as_one_quoted_segment(server):
    LlamaSwap(server, "good").unload("a/b c")
    assert SEEN == [("POST", "/api/models/unload/a%2Fb%20c", "Bearer good")]
