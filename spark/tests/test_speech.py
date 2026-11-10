"""The speech service, stack/speech/serve.py (website/design/speech-service.md).

serve.py is loaded by path with a stand-in model and parts already parsed, so these need no torch. Its answer is run
through vid2sub_parser.py, a verbatim copy of vid2sub's parser, because the design's first constraint is that the
parser doesn't change. The multipart and FLAC paths need python-multipart and soundfile, which spark/'s lock doesn't
have: speech_env_checks.py runs them from stack/speech's environment, as a child of this run and so under its memory
cap, and they skip where that environment isn't built (the Mac, CI, any account but Dan's)."""

import asyncio
import concurrent.futures
import importlib.util
import logging
import random
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from vid2sub_parser import parse_openai

ROOT = Path(__file__).resolve().parents[2]
SERVE = ROOT / "stack" / "speech" / "serve.py"
CHECKS = Path(__file__).with_name("speech_env_checks.py")
# conftest.py gives each test a HOME of its own, so this is read at import, when HOME is still the account's.
SPEECH_PYTHON = Path.home() / "speech" / "venv" / "bin" / "python"
BASE = "http://127.0.0.1:58991"
ROUTE = "/v1/audio/transcriptions"
SECRET = "秘密的字幕文本"


@pytest.fixture(scope="module")
def serve():
    spec = importlib.util.spec_from_file_location("speech_serve", SERVE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["speech_serve"] = module  # dataclass needs its module registered
    spec.loader.exec_module(module)
    yield module
    del sys.modules["speech_serve"]


@dataclass
class Unit:
    text: str
    start_time: float
    end_time: float


@dataclass
class Result:
    text: str
    language: str
    time_stamps: Any


def is_cjk(ch: str) -> bool:
    return 0x4E00 <= ord(ch) <= 0x9FFF


def aligner_tokens(text: str) -> list[str]:
    """What the aligner's tokenize_space_lang gives: each space-split piece's kept characters, each CJK one on its own."""
    tokens: list[str] = []
    for piece in text.split():
        kept = "".join(ch for ch in piece if ch == "'" or unicodedata.category(ch)[0] in "LN")
        run = ""
        for ch in kept:
            if is_cjk(ch):
                if run:
                    tokens.append(run)
                    run = ""
                tokens.append(ch)
            else:
                run += ch
        if run:
            tokens.append(run)
    return tokens


def units_for(tokens: list[str]) -> list[Unit]:
    return [Unit(token, round(i * 0.25, 3), round(i * 0.25 + 0.2, 3)) for i, token in enumerate(tokens)]


def collapsed(text: str) -> str:
    return " ".join(text.split())


# The answer, through vid2sub's parser.

@pytest.mark.parametrize(("text", "name", "code"), [
    ("你好，世界。今天天气很好！", "Chinese", "zh"),
    ("你食咗飯未呀？我哋一齊去啦。", "Cantonese", "yue"),
    ("Hello, world. It's a test.", "English", "en"),
    ("The rate is 3.5 percent, up from 2.75.", "English", "en"),
    ("COVID-19 came first. Then e-mail.", "English", "en"),
    ("我用 Python 3.12 写了 COVID-19 的报告。", "Chinese", "zh"),
    ("你好　世界！（测试）「引号」", "Chinese", "zh"),
    ("Tabs\tand  double   spaces\nand newlines.", "English", "en"),
    ("「こんにちは」世界。", "Japanese", "ja"),
    ("A - B and C — D ... E", "English", "en"),
    ("- - hello there", "English", "en"),
    ("(a) (b) [c]", "English", "en"),
], ids=["mandarin", "cantonese", "english", "decimals", "hyphens", "mixed", "full-width", "whitespace", "japanese",
        "spaced dashes", "leading dashes", "brackets"])
def test_the_answer_keeps_every_word_through_vid2subs_parser(serve, text, name, code):
    tokens = aligner_tokens(text)
    parsed = parse_openai(serve.answer_for(Result(text, name, units_for(tokens)), 90.0))
    assert parsed.language == code and len(parsed.segments) == 1
    segment = parsed.segments[0]
    assert len(segment.words) == len(tokens)
    assert "".join(word.text for word in segment.words) == collapsed(text) == segment.text


PIECES = ["你", "好", "世", "界", "hello", "World", "3.5", "COVID-19", "it's", "x", "2026", "A", "Ω", "ñandú"]
BETWEEN = ["", " ", "  ", "　", "\t", ",", "，", "。", ", ", " - ", " — ", "...", " (", ") ", "「", "」", "—", " , ", ": "]


@pytest.mark.parametrize("seed", range(300))
def test_any_mix_of_words_and_punctuation_keeps_every_word(serve, seed):
    rng = random.Random(seed)
    text = rng.choice(BETWEEN).lstrip() + "".join(rng.choice(PIECES) + rng.choice(BETWEEN) for _ in range(rng.randint(1, 30)))
    tokens = aligner_tokens(text)
    parsed = parse_openai(serve.answer_for(Result(text, "Chinese", units_for(tokens)), 90.0))
    [segment] = parsed.segments
    assert len(segment.words) == len(tokens)
    assert "".join(word.text for word in segment.words) == collapsed(text)


def test_a_word_is_the_texts_own_characters_from_its_first_kept_to_its_last(serve):
    answer = serve.answer_for(Result("rate 3.5, COVID-19.", "English", units_for(["rate", "35", "COVID19"])), 10.0)
    assert [word["word"] for word in answer["words"]] == ["rate", "3.5", "COVID-19"]


def test_a_gap_with_two_spaces_goes_into_the_word_before_it(serve):
    answer = serve.answer_for(Result("A - B", "English", units_for(["A", "B"])), 10.0)
    assert [word["word"] for word in answer["words"]] == ["A -", "B"]


def test_the_answer_has_one_segment_from_the_first_words_start_to_the_last_words_end(serve):
    units = [Unit("你", 1.5, 1.7), Unit("好", 1.7, 2.25)]
    answer = serve.answer_for(Result("你好", "Chinese", units), 10.0)
    assert answer["segments"] == [{"id": 0, "start": 1.5, "end": 2.25, "text": "你好"}]
    assert answer["language"] == "zh" and answer["text"] == "你好" and answer["duration"] == 10.0


def test_a_zero_length_unit_is_widened_and_keeps_its_midpoint_in_the_segment(serve):
    units = [Unit("你", 1.0, 1.0), Unit("好", 1.0, 1.0)]
    answer = serve.answer_for(Result("你好", "Chinese", units), 10.0)
    assert all(word["end"] - word["start"] == pytest.approx(0.002) for word in answer["words"])
    segment = answer["segments"][0]
    for word in answer["words"]:
        assert segment["start"] <= (word["start"] + word["end"]) / 2 < segment["end"]
    assert len(parse_openai(answer).segments[0].words) == 2


def test_a_unit_ending_before_it_starts_is_widened_too(serve):
    answer = serve.answer_for(Result("你", "Chinese", [Unit("你", 2.0, 1.5)]), 10.0)
    assert answer["words"][0]["end"] == 2.002


@pytest.mark.parametrize("text", ["", "   ", "。。。", "（♪♪♪）"], ids=["empty", "spaces", "punctuation", "music"])
def test_no_kept_characters_is_a_success_with_nothing_in_it(serve, text):
    answer = serve.answer_for(Result(text, "Chinese", []), 30.0)
    assert answer["segments"] == [] and answer["words"] == [] and answer["language"] == "zh"
    assert parse_openai(answer).segments == ()


@pytest.mark.parametrize(("text", "tokens"), [
    ("你好世界", ["你", "好"]),
    ("你好", ["你", "坏"]),
    ("你好", ["你", "好", "吗"]),
    ("hello", ["HELLO"]),
], ids=["units stop early", "a different character", "a unit too many", "other case"])
def test_units_that_dont_spell_the_text_send_it_back_with_no_words_and_log_no_text(serve, caplog, text, tokens):
    with caplog.at_level(logging.WARNING, logger="speech"):
        answer = serve.answer_for(Result(text, "Chinese", units_for(tokens)), 30.0)
    assert answer["words"] == []
    assert answer["segments"] == [{"id": 0, "start": 0.0, "end": 30.0, "text": text}]
    assert caplog.records and text not in caplog.text


def test_time_stamps_of_none_send_the_text_back_with_no_words(serve):
    answer = serve.answer_for(Result("你好", "Chinese", None), 30.0)
    assert answer["words"] == [] and answer["segments"][0]["text"] == "你好"


@pytest.mark.parametrize(("name", "code"), [
    ("Chinese", "zh"), ("Cantonese", "yue"), ("English", "en"), ("Japanese", "ja"), ("Korean", "ko"),
    ("Chinese,English", "zh"), ("Cantonese,Chinese", "yue"), ("German", "german"), ("", ""), (None, ""),
], ids=str)
def test_language_goes_out_as_a_code_where_it_is_one(serve, name, code):
    assert serve.language_out(name) == code


# The service, with a stand-in model.

class Model:
    def __init__(self, *, result: Any = None, error: Exception | None = None, hold: bool = False):
        self.result = result if result is not None else Result("你好", "Chinese", [Unit("你", 0.1, 0.2), Unit("好", 0.2, 0.3)])
        self.error, self.calls, self.threads = error, [], []
        self.started, self.release = threading.Event(), threading.Event()
        self.hold = hold

    def transcribe(self, **kwargs):
        self.calls.append(kwargs)
        self.threads.append(threading.current_thread().name)
        self.started.set()
        if self.hold:
            assert self.release.wait(10)
        if self.error is not None:
            raise self.error
        return [self.result]


class Samples:
    shape = (160_000,)


def fixture_app(serve, model, **overrides):
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech-worker")

    async def upload(_request):
        return overrides.get("parts") or serve.Upload(b"fLaC audio", serve.MODEL_ID, "zh", "词汇")

    options = {"upload": upload, "decode": lambda _data: (Samples(), 16000), "mem_available": lambda: 60.0}
    options.update({key: value for key, value in overrides.items() if key != "parts"})
    return serve.create_app(model, worker, **options)


def client_for(serve, model, **overrides) -> TestClient:
    return TestClient(fixture_app(serve, model, **overrides), base_url=BASE, raise_server_exceptions=True)


def test_health_and_models_answer_as_the_design_says(serve):
    client = client_for(serve, Model())
    assert client.get("/health").status_code == 200
    answer = client.get("/v1/models")
    assert answer.status_code == 200 and answer.json() == {"object": "list", "data": [{"id": "qwen3-asr-1.7b"}]}


def test_a_chunk_reaches_the_model_as_samples_with_its_context_and_language_name(serve):
    model = Model()
    answer = client_for(serve, model).post(ROUTE)
    assert answer.status_code == 200
    [call] = model.calls
    assert isinstance(call["audio"], tuple) and isinstance(call["audio"][0], Samples) and call["audio"][1] == 16000
    assert call["context"] == "词汇" and call["language"] == "Chinese" and call["return_time_stamps"] is True
    body = answer.json()
    assert body["duration"] == 10.0 and [word["word"] for word in body["words"]] == ["你", "好"]


def test_no_language_leaves_the_package_to_detect_it(serve):
    model = Model()
    client_for(serve, model, parts=serve.Upload(b"fLaC", serve.MODEL_ID, None, None)).post(ROUTE)
    assert model.calls[0]["language"] is None and model.calls[0]["context"] == ""


def assert_refused(answer, status, code, *, retry=None):
    assert answer.status_code == status, answer.text
    assert answer.json() == {"error": {"code": code, "message": answer.json()["error"]["message"], "retry_after_s": retry}}
    assert answer.json()["error"]["message"]
    if retry is not None:
        assert answer.headers["retry-after"] == str(retry)


@pytest.mark.parametrize(("audio", "model_name", "language", "prompt", "status", "code"), [
    (b"RIFF", "qwen3-asr-1.7b", "zh", "", 400, "bad_request"),
    (b"", "qwen3-asr-1.7b", "zh", "", 400, "bad_request"),
    (b"fLaC", "whisper-1", "zh", "", 404, "model_not_found"),
    (b"fLaC", None, "zh", "", 404, "model_not_found"),
    (b"fLaC", "qwen3-asr-1.7b", "de", "", 400, "bad_request"),
    (b"fLaC", "qwen3-asr-1.7b", "zh", "字" * 1366, 400, "bad_request"),
], ids=["not flac", "empty audio", "another model", "no model", "another language", "a long prompt"])
def test_each_early_refusal_never_reaches_the_model(serve, audio, model_name, language, prompt, status, code):
    model = Model()
    answer = client_for(serve, model, parts=serve.Upload(audio, model_name, language, prompt)).post(ROUTE)
    assert_refused(answer, status, code)
    assert model.calls == []


def test_a_prompt_of_exactly_4_kib_is_taken(serve):
    model = Model()
    answer = client_for(serve, model, parts=serve.Upload(b"fLaC", serve.MODEL_ID, "zh", "a" * 4096)).post(ROUTE)
    assert answer.status_code == 200


@pytest.mark.parametrize(("status", "code", "maker"), [
    (400, "bad_request", "bad_request"), (413, "too_long", "too_long"),
], ids=["unreadable", "too long"])
def test_a_refusal_from_the_decode_is_answered_as_it_was_made(serve, status, code, maker):
    model = Model()

    def decode(_data):
        raise getattr(serve, maker)("words")

    assert_refused(client_for(serve, model, decode=decode).post(ROUTE), status, code)
    assert model.calls == []


@pytest.mark.parametrize(("available", "served"), [(18.5, True), (18.49, False), (10.0, False)], ids=["18.5", "just under", "10"])
def test_a_chunk_runs_only_if_memory_less_its_growth_stays_above_15_gib(serve, available, served):
    model = Model()
    answer = client_for(serve, model, mem_available=lambda: available).post(ROUTE)
    if served:
        assert answer.status_code == 200
    else:
        assert_refused(answer, 503, "no_fit", retry=30)
    assert bool(model.calls) == served


def test_unreadable_memory_is_a_no_fit(serve):
    model = Model()

    def broken():
        raise OSError("no")

    assert_refused(client_for(serve, model, mem_available=broken).post(ROUTE), 503, "no_fit", retry=30)
    assert model.calls == []


class OutOfMemoryError(RuntimeError):
    pass


@pytest.mark.parametrize("error", [OutOfMemoryError("no"), RuntimeError("CUDA out of memory. Tried to allocate 2 GiB")],
                         ids=["the type", "the words"])
def test_cuda_running_out_is_a_no_fit_and_the_next_chunk_is_served(serve, error):
    model = Model(error=error)
    client = client_for(serve, model)
    assert_refused(client.post(ROUTE), 503, "no_fit", retry=30)
    model.error = None
    assert client.post(ROUTE).status_code == 200


def test_any_other_failure_is_a_refusal_that_logs_no_text(serve, caplog):
    model = Model(error=ValueError(SECRET))
    client = client_for(serve, model)
    with caplog.at_level(logging.INFO, logger="speech"):
        answer = client.post(ROUTE)
    assert_refused(answer, 500, "failed")
    assert "ValueError" in caplog.text and SECRET not in caplog.text and "词汇" not in caplog.text
    assert SECRET not in answer.text
    model.error = None
    assert client.post(ROUTE).status_code == 200


def test_the_log_holds_lengths_and_status_and_never_the_text(serve, caplog):
    model = Model(result=Result(SECRET, "Chinese", units_for(list(SECRET))))
    with caplog.at_level(logging.INFO, logger="speech"):
        assert client_for(serve, model).post(ROUTE).status_code == 200
    assert "10.0 s of audio" in caplog.text and "status 200" in caplog.text
    assert SECRET not in caplog.text and "词汇" not in caplog.text


def in_the_background(call):
    done: list[Any] = []
    thread = threading.Thread(target=lambda: done.append(call()), daemon=True)
    thread.start()
    return thread, done


def test_during_a_chunk_health_models_and_a_429_answer_at_once_and_nothing_queues(serve):
    model = Model(hold=True)
    with client_for(serve, model) as client:
        thread, done = in_the_background(lambda: client.post(ROUTE))
        assert model.started.wait(10)
        began = time.monotonic()
        assert client.get("/health").status_code == 200
        assert client.get("/v1/models").status_code == 200
        assert_refused(client.post(ROUTE), 429, "busy", retry=5)
        assert time.monotonic() - began < 5
        assert len(model.calls) == 1 and done == []
        model.release.set()
        thread.join(10)
        assert done[0].status_code == 200
        model.hold = False
        assert client.post(ROUTE).status_code == 200


def test_the_model_runs_on_one_worker_thread(serve):
    model = Model()
    client = client_for(serve, model)
    assert client.post(ROUTE).status_code == 200
    assert client.post(ROUTE).status_code == 200
    assert len(set(model.threads)) == 1 and model.threads[0].startswith("speech-worker")


def test_a_cancelled_handler_keeps_the_chunk_counted_until_the_worker_is_done(serve):
    """A client that goes away (Ctrl-C on the Mac) mustn't let a second chunk queue behind the first."""
    model = Model(hold=True)
    worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    async def upload(_request):
        return serve.Upload(b"fLaC", serve.MODEL_ID, "zh", "")

    speech = serve.Speech(model, worker, upload=upload, decode=lambda _d: (Samples(), 16000), mem_available=lambda: 60.0)

    async def run():
        task = asyncio.create_task(speech.transcriptions(None))
        while not model.started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert speech._busy
        refused = await speech.transcriptions(None)
        assert refused.status_code == 429
        model.release.set()
        for _ in range(200):
            if not speech._busy:
                break
            await asyncio.sleep(0.01)
        assert not speech._busy

    asyncio.run(run())
    worker.shutdown(wait=True)


@pytest.mark.parametrize("path", ["/health", "/v1/models"])
@pytest.mark.parametrize(("headers", "allowed"), [
    ({}, True),
    ({"Host": "localhost:58991"}, True),
    ({"Host": "127.0.0.1:9100"}, False),
    ({"Host": "evil.example"}, False),
    ({"Origin": "https://evil.example"}, False),
    ({"Origin": "null"}, False),
], ids=["the tunnel", "localhost", "another port", "another name", "a page's origin", "a null origin"])
def test_only_the_tunnels_host_and_no_origin_get_an_answer(serve, path, headers, allowed):
    answer = client_for(serve, Model()).get(path, headers=headers)
    if allowed:
        assert answer.status_code == 200
    else:
        assert_refused(answer, 403, "forbidden")


def test_a_page_cant_reach_the_model(serve):
    model = Model()
    answer = client_for(serve, model).post(ROUTE, headers={"Origin": "https://evil.example"})
    assert_refused(answer, 403, "forbidden")
    assert model.calls == []


# Starting up, and the guards.

def run_watchdog(serve, readings):
    ended: list[int] = []
    finished = threading.Event()
    values = iter(readings)

    def read():
        try:
            value = next(values)
        except StopIteration:
            finished.set()
            threading.Event().wait()
        if isinstance(value, Exception):
            raise value
        return value

    def end(code):
        ended.append(code)
        finished.set()

    thread = serve.start_watchdog(read, end=end, every_s=0)
    assert finished.wait(10)
    assert thread.daemon
    return ended


def test_the_watchdog_ends_the_process_under_15_gib(serve):
    assert run_watchdog(serve, [40.0, 20.0, 14.99, 40.0]) == [serve.WATCHDOG_EXIT]


def test_the_watchdog_lets_15_gib_pass(serve):
    assert run_watchdog(serve, [15.0, 15.0]) == []


@pytest.mark.parametrize("error", [OSError("gone"), ValueError("no line")], ids=["unreadable", "no line"])
def test_the_watchdog_ends_the_process_when_it_cant_read_memory(serve, error):
    assert run_watchdog(serve, [40.0, error]) == [serve.WATCHDOG_EXIT]


def test_mem_available_reads_gib_from_meminfo(serve, tmp_path):
    path = tmp_path / "meminfo"
    path.write_text("MemTotal: 127533356 kB\nMemAvailable: 31457280 kB\n")
    assert serve.read_mem_available(str(path)) == 30.0
    path.write_text("MemTotal: 127533356 kB\n")
    with pytest.raises(ValueError):
        serve.read_mem_available(str(path))


class Sequences:
    def __init__(self, length):
        self.shape = (1, length)


class Output:
    def __init__(self, length):
        self.sequences = Sequences(length)


@pytest.mark.parametrize(("new", "logged"), [(4096, True), (4095, False)], ids=["at the limit", "under it"])
def test_an_answer_reaching_the_token_limit_is_logged(serve, caplog, new, logged):
    class Inner:
        def generate(self, **kwargs):
            return Output(10 + new)

    inner = Inner()
    serve.log_when_the_limit_is_reached(inner)
    with caplog.at_level(logging.WARNING, logger="speech"):
        output = inner.generate(input_ids=Sequences(10))
    assert output.sequences.shape == (1, 10 + new)
    assert ("4096-token limit" in caplog.text) == logged


def test_main_starts_the_watchdog_loads_on_the_worker_then_serves_the_socket(serve):
    order: list[str] = []
    seen: dict[str, Any] = {}

    def load():
        order.append("load")
        seen["thread"] = threading.current_thread().name
        return Model()

    def serve_it(app, **options):
        order.append("serve")
        seen["options"] = options
        seen["app"] = app

    assert serve.main(load=load, serve=serve_it, watchdog=lambda _read: order.append("watchdog")) == 0
    assert order == ["watchdog", "load", "serve"]
    assert seen["thread"].startswith("speech-worker")
    options = seen["options"]
    assert options["uds"] == "/run/speech/speech.sock" and options["http"] == "h11" and options["ws"] == "none"
    assert options["access_log"] is False and options["proxy_headers"] is False and options["server_header"] is False
    assert isinstance(seen["app"], serve.OnlyThroughTheTunnel)


def test_main_never_serves_if_the_load_fails(serve):
    served: list[Any] = []

    def load():
        raise RuntimeError("no GPU")

    with pytest.raises(RuntimeError):
        serve.main(load=load, serve=lambda *a, **k: served.append(a), watchdog=lambda _read: None)
    assert served == []


def test_the_models_are_loaded_with_the_designs_settings():
    # Read from the source: load_models needs torch and the weights, which run only on the box (the plan's first run).
    source = SERVE.read_text()
    for needle in ('device_map="cuda:0"', "dtype=torch.bfloat16", "forced_aligner=ALIGNER_PATH",
                   'forced_aligner_kwargs={"dtype": torch.bfloat16, "device_map": "cuda:0"}',
                   "max_inference_batch_size=1", "max_new_tokens=MAX_NEW_TOKENS", 'disable_dsl_names="triton"'):
        assert needle in source, needle
    assert "MAX_NEW_TOKENS = 4096" in source


# From stack/speech's environment.

speech_names = subprocess.run([sys.executable, "-IB", str(CHECKS), "--list"], capture_output=True, text=True).stdout.split()


@pytest.mark.skipif(not SPEECH_PYTHON.exists(), reason="stack/speech's environment isn't built here")
@pytest.mark.parametrize("name", speech_names)
def test_in_the_speech_environment(name):
    run = subprocess.run([str(SPEECH_PYTHON), "-IB", str(CHECKS), name, str(SERVE)], capture_output=True, text=True,
                         timeout=120, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stdout + run.stderr
    assert run.stdout.strip() == f"ok {name}"
