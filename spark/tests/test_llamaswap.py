import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from spark.llamaswap import LlamaSwap, LlamaSwapError, LlamaSwapUnreachable, Running

SEEN: list[tuple[str, str, str | None]] = []
ANSWER: dict = {}  # a test sets what GET /running answers with the good key: a body, or "short"


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, code: int, body: dict | str):
        data = body if isinstance(body, str) else json.dumps(body)
        self.send_response(code)
        self.end_headers()
        self.wfile.write(data.encode())

    def do_GET(self):
        SEEN.append(("GET", self.path, self.headers.get("Authorization")))
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
        SEEN.append(("POST", self.path, self.headers.get("Authorization")))
        if self.path.endswith("/nope"):
            return self._reply(404, {"error": {"message": "model not found"}})
        self._reply(200, "OK")


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    SEEN.clear()
    ANSWER.clear()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_running_sends_the_key(server):
    assert LlamaSwap(server, "good").running() == [Running("coder", "ready")]
    assert SEEN == [("GET", "/running", "Bearer good")]


def test_wrong_key_is_a_clear_error(server):
    with pytest.raises(LlamaSwapError, match="401"):
        LlamaSwap(server, "bad").running()


def test_unload_posts_to_the_model(server):
    LlamaSwap(server, "good").unload("coder")
    assert SEEN[-1][:2] == ("POST", "/api/models/unload/coder")


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
