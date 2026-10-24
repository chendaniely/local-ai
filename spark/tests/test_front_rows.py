"""A request's row (the usage meter's counter, Task 44d): rows.usage_row on its own, from a request as the front holds it
when it ends; and the shape the front reads from a body, into numbers on the request, the body never kept. Every name,
id and number here is a stand-in."""

import json

import pytest

from front_helpers import DIGESTS, GROUPS, KEYS, MODELS, field, file_part, form_body, receiver
from spark.front import app as front_app
from spark.front import parse, rows
from spark.front.usage import UsageScan

T = 1000.0
CODER = "qwen3.8-27b"
CHAT = ("POST", "/v1/chat/completions")
ENTRY = {"name": CODER, "roles": ["coder"], "label": "the coder", "resident": False, "engine": "llama.cpp"}
ROLES = frozenset(ENTRY["roles"])


@pytest.fixture
def anyio_backend():
    return "asyncio"


def served(**fields) -> front_app._Request:
    """A request the coder answered whole, asked for by its role: received at T (50.0 by the monotonic clock),
    forwarded 1.5 s later, its first event 0.25 s after that."""
    request = front_app._Request(started=T, held=parse.Held(parse.BodyBudget(1 << 20)), mono_started=50.0)
    values = {"key": "dan-mac", "route": CHAT, "asked": "coder", "model": CODER, "entry": ENTRY, "status": 200,
              "complete": True, "mono_forwarded": 51.5, "mono_first": 51.75, "attempts": 1, "stream": True}
    for name, value in {**values, **fields}.items():
        setattr(request, name, value)
    return request


def row(request: front_app._Request, roles: frozenset[str] = ROLES) -> dict:
    return rows.usage_row(request, T + 2.5, 52.5, roles)


def test_a_whole_answers_row_has_what_it_was_and_how_it_went():
    request = served()
    assert row(request) == {"v": 1, "id": request.id, "t0": T, "t1": T + 2.5, "key": "dan-mac", "route": "chat",
                            "model": CODER, "role": "coder", "engine": "llama.cpp", "end": "done", "status": 200,
                            "from": "none", "wait_s": 1.5, "first_s": 0.25, "stream": True, "attempts": 1}


def test_the_role_is_kept_only_when_the_client_asked_by_one_of_the_models_roles():
    assert row(served())["role"] == "coder"
    assert "role" not in row(served(asked=CODER))  # by its name
    assert "role" not in row(served(), roles=frozenset())  # not one of the roles given
    unresolved = row(served(asked="secretproj-q3", model=None, entry=None, status=404, refused="model_not_found"))
    assert "role" not in unresolved and "model" not in unresolved and "engine" not in unresolved
    assert unresolved["asked_len"] == len("secretproj-q3")
    assert "asked_len" not in row(served())  # only for a model it didn't resolve


@pytest.mark.parametrize("effort", sorted(rows.EFFORTS))
def test_an_effort_from_the_fixed_set_is_kept(effort):
    assert row(served(effort=effort))["effort"] == effort


@pytest.mark.parametrize("effort", ["secret-effort", "HIGH", "", 3])
def test_any_other_effort_is_left_out(effort):
    assert "effort" not in row(served(effort=effort))


def test_n_only_above_1_and_images_as_counted():
    assert "n" not in row(served(n=1))
    assert row(served(n=3))["n"] == 3
    assert row(served(images=2))["images"] == 2
    assert row(served(images=0))["images"] == 0
    assert row(served(bytes_in=1234))["bytes_in"] == 1234


def test_wait_and_first_come_from_the_monotonic_marks():
    got = row(served(started=T + 600))  # the wall clock moved: the durations don't
    assert (got["t0"], got["wait_s"], got["first_s"]) == (T + 600, 1.5, 0.25)
    # Never forwarded: it waited until it ended, refused here; no first event.
    refused = row(served(mono_forwarded=None, mono_first=None, status=409, refused="no_fit", attempts=0))
    assert (refused["wait_s"], refused["end"], refused["code"]) == (2.5, "refused", "no_fit")
    assert "first_s" not in refused
    assert row(served(load_s=41.5))["load_s"] == 41.5


def test_the_engine_comes_from_the_entry_when_it_names_one():
    assert row(served())["engine"] == "llama.cpp"
    old_gates = {name: value for name, value in ENTRY.items() if name != "engine"}
    assert "engine" not in row(served(entry=old_gates))


@pytest.mark.parametrize(("fields", "end"), [
    ({}, "done"),
    ({"complete": False}, "cut"),
    ({"ended": "cut", "complete": False}, "cut"),
    ({"ended": "gone", "complete": False}, "gone"),
    ({"status": None, "complete": False}, "gone"),
    ({"ended": "error"}, "error"),
    ({"status": 502}, "error"),  # upstream's non-2xx, passed on
    ({"status": 409, "refused": "no_fit"}, "refused"),
])
def test_each_ending(fields, end):
    assert row(served(**fields))["end"] == end


def test_the_counts_come_from_its_scan_and_none_is_ever_written():
    scan = UsageScan("/v1/chat/completions", streamed=True)
    scan.feed(b'data: {"choices":[{"delta":{"content":"hi"}}],"id":"chatcmpl-standin3","object":"x"}\n\n'
              b'data: {"choices":[],"usage":{"completion_tokens":5,"prompt_tokens":100,"total_tokens":105}}\n\n')
    got = row(served(scan=scan))
    assert {name: got.get(name) for name in ("p", "c", "o", "from", "ev_think", "ev_text", "resp_id")} == {
        "p": 100, "c": None, "o": 5, "from": "usage", "ev_think": 0, "ev_text": 1, "resp_id": "chatcmpl-standin3"}
    assert "c" not in got  # not reported: absent, never 0
    assert None not in got.values()
    # A request with the least the front knows of one: its key, and that it went before any answer.
    bare = front_app._Request(started=T, held=parse.Held(parse.BodyBudget(1)))
    bare.key = "agent"
    got = rows.usage_row(bare, T, bare.mono_started, frozenset())
    assert got == {"v": 1, "id": bare.id, "t0": T, "t1": T, "key": "agent", "end": "gone", "from": "none",
                   "wait_s": 0.0, "attempts": 0}
    json.dumps(got, allow_nan=False)


def app(tmp_path) -> front_app.FrontApp:
    return front_app.FrontApp(parse.ModelList(MODELS), KEYS, GROUPS, DIGESTS, upstream=None, gate=object(),
                              caps=parse.KeyCaps(KEYS, GROUPS), log=[].append, spool_dir=tmp_path)


def unread() -> front_app._Request:
    return front_app._Request(started=T, held=parse.Held(parse.BodyBudget(1 << 30)), key="dan-mac", route=CHAT)


@pytest.mark.anyio
async def test_after_read_the_body_is_kept_only_as_bytes_and_numbers(tmp_path):
    body = json.dumps({"model": "coder", "stream": True, "n": 2, "reasoning_effort": "high",
                       "messages": [{"role": "system", "content": "be brief"},
                                    {"role": "user", "content": [
                                        {"type": "text", "text": "what is this"},
                                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBBB"}}]}],
                       }).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    request = unread()
    assert await app(tmp_path)._read("/v1/chat/completions", headers, receiver([body]), request) == "coder"
    assert [name for name, value in vars(request).items() if isinstance(value, (dict, list))] == []
    assert (request.stream, request.n, request.images, request.effort, request.bytes_in) == (
        True, 2, 2, "high", len(request.json))
    # An effort or an n the row doesn't take is never kept, even for a moment: nothing a client chose stays.
    body = json.dumps({"model": "coder", "reasoning_effort": "secret-effort", "n": "two", "stream": "yes"}).encode()
    request = unread()
    await app(tmp_path)._read("/v1/chat/completions", [(b"content-type", b"application/json")], receiver([body]),
                              request)
    assert (request.stream, request.n, request.images, request.effort) == (False, None, 0, None)


@pytest.mark.anyio
async def test_the_responses_api_effort_and_images_are_read_too(tmp_path):
    body = json.dumps({"model": "coder", "reasoning": {"effort": "low"},
                       "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"},
                                                              {"type": "input_image", "image_url": "data:x"}]}]})
    request = unread()
    request.route = ("POST", "/v1/responses")
    await app(tmp_path)._read("/v1/responses", [(b"content-type", b"application/json")], receiver([body.encode()]),
                              request)
    assert (request.effort, request.images) == ("low", 1)


@pytest.mark.anyio
async def test_an_uploads_bytes_in_is_its_files(tmp_path):
    audio = b"RIFF" + b"a" * 1000
    body = form_body([field("model", "stt"), file_part(audio)])
    headers = [(b"content-type", b"multipart/form-data; boundary=spark-test-boundary")]
    request = unread()
    request.route = ("POST", "/v1/audio/transcriptions")
    assert await app(tmp_path)._read("/v1/audio/transcriptions", headers, receiver([body]), request) == "stt"
    assert request.bytes_in == len(audio)
    assert (request.stream, request.n, request.images, request.effort) == (None, None, None, None)
    request.form.close()
