"""The speech service's multipart and FLAC paths, run from stack/speech's environment, which has python-multipart and
soundfile. test_speech.py starts this as a child of its run, once per check, so each is under make test's memory cap:

    speech_env_checks.py --list
    speech_env_checks.py <check> <path to serve.py>

Imports of the environment's packages stay inside the functions, because --list runs from any Python."""

import asyncio
import concurrent.futures
import http.client
import importlib.util
import io
import json
import socket
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

CHECKS = {}
BOUNDARY = "speechboundary"
ROUTE = "/v1/audio/transcriptions"
HOST = b"127.0.0.1:58991"


def check(function):
    CHECKS[function.__name__] = function
    return function


@dataclass
class Result:
    text: str
    language: str
    time_stamps: list


class Model:
    def __init__(self):
        self.calls = []

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        return [Result("", "Chinese", [])]


def flac(seconds=1.0, rate=16000, channels=1):
    import numpy
    import soundfile

    frames = int(seconds * rate)
    samples = numpy.zeros((frames, channels) if channels > 1 else frames, dtype="float32")
    buffer = io.BytesIO()
    soundfile.write(buffer, samples, rate, format="FLAC", subtype="PCM_16")
    return buffer.getvalue()


def part(name, value):
    return f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()


def file_part(data, name="file"):
    head = f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"; filename="c.flac"\r\nContent-Type: audio/flac\r\n\r\n'
    return head.encode() + data + b"\r\n"


def form(audio, *, model="qwen3-asr-1.7b"):
    """As vid2sub's client sends a chunk: `timestamp_granularities[]` twice, which this service ignores."""
    return b"".join([file_part(audio), part("model", model), part("response_format", "verbose_json"), part("language", "zh"),
                     part("prompt", "词汇"), part("timestamp_granularities[]", "word"),
                     part("timestamp_granularities[]", "segment"), f"--{BOUNDARY}--\r\n".encode()])


CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}".encode()


def drive(app, chunks, *, content_type=CONTENT_TYPE, content_length=None):
    """The ASGI call uvicorn would make, returning (status, body, how many chunks were read)."""
    headers = [(b"host", HOST), (b"content-type", content_type)]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "path": ROUTE,
             "raw_path": ROUTE.encode(), "query_string": b"", "root_path": "", "scheme": "http", "headers": headers,
             "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 58991)}
    sent = []
    read = [0]
    pieces = iter(chunks)

    async def receive():
        try:
            chunk = next(pieces)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        read[0] += 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body")
    return status, json.loads(body), read[0]


def load(path):
    spec = importlib.util.spec_from_file_location("speech_serve", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["speech_serve"] = module
    spec.loader.exec_module(module)
    return module


def app_of(serve, model):
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    return serve.create_app(model, worker, mem_available=lambda: 60.0)


def post(serve, body, model=None, **options):
    model = model if model is not None else Model()
    return (*drive(app_of(serve, model), [body], **options), model)


def code_of(answer):
    return answer["error"]["code"]


@check
def a_chunk_as_vid2sub_sends_it_is_decoded_and_transcribed(serve):
    status, body, _, model = post(serve, form(flac(seconds=2.0)))
    assert status == 200, body
    assert body["duration"] == 2.0 and body["segments"] == [] and body["words"] == [] and body["language"] == "zh"
    [call] = model.calls
    samples, rate = call["audio"]
    assert rate == 16000 and samples.shape == (32000,) and str(samples.dtype) == "float32"
    assert call["language"] == "Chinese" and call["context"] == "词汇"


@check
def forty_eight_khz_stereo_is_taken(serve):
    status, body, _, model = post(serve, form(flac(rate=48000, channels=2)))
    assert status == 200, body
    assert model.calls[0]["audio"][1] == 48000 and model.calls[0]["audio"][0].shape == (48000, 2)


@check
def over_48_khz_is_a_400(serve):
    status, body, _, model = post(serve, form(flac(rate=96000)))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def three_channels_is_a_400(serve):
    status, body, _, model = post(serve, form(flac(channels=3)))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def wav_audio_is_a_400(serve):
    import numpy
    import soundfile

    buffer = io.BytesIO()
    soundfile.write(buffer, numpy.zeros(1600, dtype="float32"), 16000, format="WAV")
    status, body, _, model = post(serve, form(buffer.getvalue()))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def garbage_after_the_magic_is_a_400(serve):
    status, body, _, model = post(serve, form(b"fLaC" + b"\x00" * 4096))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def a_flac_cut_short_is_answered_and_never_a_500(serve):
    data = flac(seconds=5.0)
    status, body, _, _ = post(serve, form(data[:len(data) // 2]))
    assert status in (200, 400), (status, body)


@check
def a_flac_holding_only_its_header_is_a_400(serve):
    status, body, _, model = post(serve, form(flac(seconds=5.0)[:42]))
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def over_300_seconds_is_a_413(serve):
    status, body, _, model = post(serve, form(flac(seconds=300.5, rate=8000)))
    assert (status, code_of(body)) == (413, "too_long") and model.calls == []


@check
def exactly_300_seconds_is_taken(serve):
    status, body, _, model = post(serve, form(flac(seconds=300.0, rate=8000)))
    assert status == 200, body
    assert model.calls[0]["audio"][0].shape == (2_400_000,)


@check
def a_body_declared_over_100_mib_is_refused_before_any_of_it_is_read(serve):
    status, body, read = drive(app_of(serve, Model()), [b"x"], content_length=100 * 1024 * 1024 + 1)
    assert (status, code_of(body)) == (413, "too_long") and read == 0


@check
def a_body_over_100_mib_is_refused_as_it_arrives(serve):
    megabyte = b"x" * (1024 * 1024)
    chunks = [file_part(megabyte)[:-2]] + [megabyte] * 200
    status, body, read = drive(app_of(serve, Model()), chunks)
    assert (status, code_of(body)) == (413, "too_long")
    assert read <= 102, read


@check
def a_malformed_form_is_a_400(serve):
    status, body, _, model = post(serve, b"--" + BOUNDARY.encode() + b"\r\nnot a header\r\n\r\n")
    assert (status, code_of(body)) == (400, "bad_request") and model.calls == []


@check
def a_form_with_no_boundary_is_a_400(serve):
    status, body, _, _ = post(serve, form(flac()), content_type=b"multipart/form-data")
    assert (status, code_of(body)) == (400, "bad_request")


@check
def json_instead_of_a_form_is_a_400(serve):
    status, body, _, _ = post(serve, b"{}", content_type=b"application/json")
    assert (status, code_of(body)) == (400, "bad_request")


@check
def a_form_with_no_file_part_is_a_400(serve):
    status, body, _, _ = post(serve, part("model", "qwen3-asr-1.7b") + f"--{BOUNDARY}--\r\n".encode())
    assert (status, code_of(body)) == (400, "bad_request")


@check
def a_form_with_the_audio_as_text_is_a_400(serve):
    status, body, _, _ = post(serve, part("file", "/etc/passwd") + part("model", "qwen3-asr-1.7b") + f"--{BOUNDARY}--\r\n".encode())
    assert (status, code_of(body)) == (400, "bad_request")


@check
def two_files_are_a_400(serve):
    body_in = file_part(flac()) + file_part(flac(), name="other") + f"--{BOUNDARY}--\r\n".encode()
    status, body, _, _ = post(serve, body_in)
    assert (status, code_of(body)) == (400, "bad_request")


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("127.0.0.1", 58991, timeout=30)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(30)
        self.sock.connect(self.path)


@check
def uvicorn_serves_it_on_a_unix_socket(serve):
    """The real server on a real socket, with the options main gives it, a Host header as the tunnel's client sends."""
    import uvicorn

    with tempfile.TemporaryDirectory() as folder:
        path = str(Path(folder) / "speech.sock")
        config = uvicorn.Config(app_of(serve, Model()), uds=path, http="h11", ws="none", loop="asyncio", lifespan="off",
                                log_level="warning", access_log=False, server_header=False, date_header=False,
                                proxy_headers=False, timeout_graceful_shutdown=5)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not Path(path).exists():
            assert time.monotonic() < deadline, "no socket"
            time.sleep(0.05)

        connection = UnixConnection(path)
        connection.request("GET", "/health")
        answer = connection.getresponse()
        assert answer.status == 200, answer.read()
        answer.read()

        connection.putrequest("POST", ROUTE, skip_accept_encoding=True)
        body = form(flac())
        connection.putheader("Content-Type", CONTENT_TYPE.decode())
        connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        answer = connection.getresponse()
        text = answer.read()
        assert answer.status == 200, text
        assert json.loads(text)["language"] == "zh"

        other = UnixConnection(path)
        other.putrequest("GET", "/health", skip_host=True)
        other.putheader("Host", "evil.example")
        other.endheaders()
        answer = other.getresponse()
        assert answer.status == 403, answer.read()
        answer.read()
        server.should_exit = True
        thread.join(15)
        assert not thread.is_alive()


def main(argv):
    if argv[1:] == ["--list"]:
        print("\n".join(CHECKS))
        return 0
    CHECKS[argv[1]](load(argv[2]))
    print(f"ok {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
