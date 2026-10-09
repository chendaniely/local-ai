"""The front's parsing (website/design/phase-2a.md, Task 20): its routes, the client key from a request's headers,
JSON bodies and multipart forms read within their caps and rebuilt, the body budget, the model list and its roles, the
headers that never go upstream, each key's caps, and the journal line. The stand-ins are front_helpers.py's."""

import json
import re

import anyio
import pytest

from front_helpers import (BOUNDARY, CONTENT_TYPE, DIGESTS, GROUPS, KEYS, MODELS, ROOT, field, file_part, form_body,
                           receiver)
from spark import messages
from spark.front import parse
from spark.registry import load_registry

BOUND_S = 10  # every async test's bound


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def read_form(chunks, spool, content_type=CONTENT_TYPE, **kwargs):
    with anyio.fail_after(BOUND_S):
        return await parse.read_form(receiver(chunks), content_type, spool_dir=spool,
                                     max_bytes=kwargs.pop("max_bytes", 200 << 20), **kwargs)


def refused(excinfo) -> tuple[int, str, str]:
    err = excinfo.value
    return err.status, err.code, err.message


# Routes.

def test_the_routes_are_the_six_inference_posts_and_the_model_list():
    assert parse.ROUTES == frozenset({
        ("GET", "/v1/models"), ("POST", "/v1/chat/completions"), ("POST", "/v1/completions"),
        ("POST", "/v1/responses"), ("POST", "/v1/messages"), ("POST", "/v1/embeddings"),
        ("POST", "/v1/audio/transcriptions"),
    })
    assert ("GET", "/health") not in parse.ROUTES  # the front's own, never forwarded
    # route_not_served's words list exactly the paths served.
    words = messages.refusal("route_not_served", messages.Moment()).message
    assert set(re.findall(r"/v1/[a-z/]+", words)) == {path for _, path in parse.ROUTES}


# Keys.

@pytest.mark.parametrize(("headers", "name"), [
    ([(b"authorization", b"Bearer k1")], "dan-mac"),
    ([(b"authorization", b"bearer k1")], "dan-mac"),  # the scheme's case doesn't matter (RFC 9110)
    ([(b"x-api-key", b"k2")], "agent"),
    ([(b"authorization", b"Bearer k3"), (b"x-api-key", b"k3")], "open-webui"),  # both, the same key
    ([(b"authorization", b"Bearer k1"), (b"authorization", b"Bearer k1")], "dan-mac"),
])
def test_a_key_is_read_from_bearer_or_x_api_key(headers, name):
    assert parse.authenticate(headers, DIGESTS) == name


@pytest.mark.parametrize("headers", [
    [],
    [(b"authorization", b"Bearer nope")],
    [(b"authorization", b"Bearer ")],
    [(b"authorization", b"Basic k1")],  # only a bearer key
    [(b"authorization", b"k1")],
    [(b"x-api-key", b"")],
    [(b"authorization", b"Bearer k1"), (b"x-api-key", b"k2")],  # two keys that disagree: neither
    [(b"authorization", b"Bearer k1"), (b"authorization", b"Bearer k2")],
    [(b"authorization", b"Bearer k\xc3\xa9")],  # no key the Spark issued holds a byte outside ASCII
    [(b"cookie", b"k1"), (b"proxy-authorization", b"Bearer k1")],  # never read from anywhere else
])
def test_anything_else_authenticates_no_one(headers):
    assert parse.authenticate(headers, DIGESTS) is None


# JSON bodies.

@pytest.mark.anyio
async def test_a_json_body_is_parsed_and_reserialized_compact():
    raw = b'{"model":"coder",  "x" : 1, "y": [1.5, "\xc3\xa9"]}'
    with anyio.fail_after(BOUND_S):
        body, sent = await parse.read_json(receiver([raw[:10], raw[10:]]), max_bytes=1000)
    assert body == {"model": "coder", "x": 1, "y": [1.5, "é"]}
    # Text outside ASCII goes on as UTF-8, never as \\u escapes, which take up to three times the bytes (the
    # controller's ruling, at Task 20's review).
    assert sent == json.dumps(json.loads(raw), separators=(",", ":"), ensure_ascii=False).encode()
    assert sent == b'{"model":"coder","x":1,"y":[1.5,"\xc3\xa9"]}'


@pytest.mark.anyio
async def test_text_outside_ascii_goes_on_at_its_own_size():
    emoji = "\U0001F600".encode() * 1000
    raw = b'{"model":"coder","x":"' + emoji + b'"}'
    with anyio.fail_after(BOUND_S):
        _, sent = await parse.read_json(receiver([raw]), max_bytes=1 << 20)
    assert sent == raw  # 4 bytes an emoji, as it came; \\u escapes would be 12
    # A lone surrogate, which UTF-8 can't carry, goes on as its escape.
    with anyio.fail_after(BOUND_S):
        body, sent = await parse.read_json(receiver([b'{"model":"coder","x":"\\ud800\xc3\xa9"}']), max_bytes=100)
    assert body["x"] == "\ud800é"
    assert sent == b'{"model":"coder","x":"\\ud800\\u00e9"}'


@pytest.mark.anyio
async def test_a_json_key_given_twice_goes_on_once():
    # Python keeps the last; the body sent on holds only it, so the front and llama-swap read the same model.
    with anyio.fail_after(BOUND_S):
        body, sent = await parse.read_json(receiver([b'{"model":"a","model":"coder"}']), max_bytes=1000)
    assert body == {"model": "coder"} and sent == b'{"model":"coder"}'


@pytest.mark.anyio
@pytest.mark.parametrize(("raw", "words"), [
    (b"{", parse.NOT_JSON),
    (b"", parse.NOT_JSON),
    (b'{"model": NaN}', parse.NOT_JSON),  # Python reads it; JSON and llama-swap don't
    (b'{"model": "x", "n": Infinity}', parse.NOT_JSON),
    (b'{"model": "x", "n": -Infinity}', parse.NOT_JSON),
    # JSON the front can't read: too deep for the parser, past Python's digits limit, past a float's range (which
    # would go on as Infinity, which isn't JSON)
    (b"[" * 100000 + b"]" * 100000, parse.TOO_DEEP),
    (b'{"model": "x", "n": 1' + b"0" * 5000 + b"}", parse.NUMBER_TOO_BIG),
    (b'{"model": "x", "t": 1e400}', parse.NUMBER_TOO_BIG),
    (b'{"model": "x", "t": -1e400}', parse.NUMBER_TOO_BIG),
    (b"\xff\xfe{", parse.NOT_JSON),
    (b"[]", parse.NOT_AN_OBJECT),
    (b'"coder"', parse.NOT_AN_OBJECT),
    (b'{"x": 1}', parse.NO_MODEL),
    (b'{"model": 5}', parse.NO_MODEL),
    (b'{"model": null}', parse.NO_MODEL),
])
async def test_a_body_that_isnt_a_json_object_with_a_model_is_400(raw, words):
    with pytest.raises(parse.Refused) as excinfo, anyio.fail_after(BOUND_S):
        await parse.read_json(receiver([raw]), max_bytes=1 << 20)
    assert refused(excinfo) == (400, "invalid_request", words)


@pytest.mark.anyio
async def test_an_empty_model_name_is_read_as_a_name():
    # model_not_found words it, *There's no model by that name here.* (Task 6), never a 400.
    with anyio.fail_after(BOUND_S):
        body, _ = await parse.read_json(receiver([b'{"model": ""}']), max_bytes=100)
    assert body == {"model": ""}


@pytest.mark.anyio
async def test_a_json_body_over_its_cap_is_413_as_it_crosses():
    chunks = [b'{"model":"coder","x":"', b"a" * 60, b'"}']
    with pytest.raises(parse.Refused) as excinfo, anyio.fail_after(BOUND_S):
        await parse.read_json(receiver(chunks), max_bytes=80)
    assert refused(excinfo)[:2] == (413, "request_too_large")
    assert excinfo.value.message == parse.too_big(80, part_way=True).message
    assert excinfo.value.message.endswith("80 bytes, the most the front takes at this address, so it was refused "
                                          "part-way.")
    with anyio.fail_after(BOUND_S):  # one byte under it, read
        assert (await parse.read_json(receiver(chunks), max_bytes=84))[0]["model"] == "coder"


@pytest.mark.anyio
async def test_a_client_gone_while_its_body_comes_is_client_gone():
    with pytest.raises(parse.ClientGone), anyio.fail_after(BOUND_S):
        await parse.read_json(receiver([b'{"model":'], gone=True), max_bytes=100)


def test_the_too_big_words_name_the_cap():
    assert parse.too_big(200 << 20).message == (
        "The request is over 200 MiB, the most the front takes at this address, so it wasn't read.")
    assert parse.too_big(200 << 20, part_way=True).message == (
        "The request is over 200 MiB, the most the front takes at this address, so it was refused part-way.")
    assert "16 MiB" in parse.too_big(16 << 20).message
    assert "64 KiB" in parse.too_big(64 << 10).message


@pytest.mark.anyio
async def test_a_json_body_with_too_many_parts_is_refused_before_it_is_parsed():
    # [, { and , outside strings, counted before json.loads, so a body of a million empty arrays can't take the front's
    # memory: 16 MiB of them took ~400 MiB to parse (the controller's ruling, at Task 20's review).
    def body(zeros: int) -> bytes:  # {, the model's comma, [, then a comma between each two zeros
        return b'{"model":"coder","x":[' + b"0," * (zeros - 1) + b"0]}"

    assert parse.json_parts(body(999_998)) == parse.MAX_JSON_PARTS
    with anyio.fail_after(BOUND_S):
        assert (await parse.read_json(receiver([body(999_998)]), max_bytes=16 << 20))[0]["model"] == "coder"
    with pytest.raises(parse.Refused) as excinfo, anyio.fail_after(BOUND_S):
        await parse.read_json(receiver([body(999_999)]), max_bytes=16 << 20)
    assert refused(excinfo) == (413, "request_too_large", parse.TOO_MANY_PARTS_JSON)
    lists = b'{"model":"coder","x":[' + b"[]," * 2_000_000 + b"[]]}"
    with pytest.raises(parse.Refused) as excinfo, anyio.fail_after(BOUND_S):
        await parse.read_json(receiver([lists]), max_bytes=16 << 20)
    assert excinfo.value.message == parse.TOO_MANY_PARTS_JSON
    # What is inside a string isn't a part, escaped quotes included.
    commas = b'{"model":"coder","x":"\\"' + b",[{" * 1_000_000 + b'\\\\"}'
    assert parse.json_parts(commas) == 2
    with anyio.fail_after(BOUND_S):
        assert (await parse.read_json(receiver([commas]), max_bytes=16 << 20))[0]["model"] == "coder"


def test_declared_length():
    assert parse.declared_length([(b"content-length", b"12")]) == 12
    assert parse.declared_length([]) is None
    for bad in ([(b"content-length", b"x")], [(b"content-length", b"1"), (b"content-length", b"2")],
                [(b"content-length", b"-1")]):
        with pytest.raises(parse.Refused) as excinfo:
            parse.declared_length(bad)
        assert excinfo.value.status == 400


# Forms.

@pytest.mark.anyio
async def test_a_form_gives_its_model_fields_and_files(tmp_path):
    body = form_body([field("model", "stt"), field("language", "en"), file_part(b"RIFF-small")])
    form = await read_form([body[:7], body[7:50], body[50:]], tmp_path)
    assert form.model == "stt"
    assert form.fields == [("model", "stt"), ("language", "en")]
    assert form.files == [parse.FilePart("file", "talk.wav", "audio/wav", b"RIFF-small")]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_a_part_over_the_threshold_is_spooled_and_close_deletes_it(tmp_path):
    big = bytes(range(256)) * (3 * 4096)  # 3 MiB, every byte value, CRs and LFs among them
    body = form_body([field("model", "stt"), file_part(big)])
    chunks = [body[i:i + 65536] for i in range(0, len(body), 65536)]
    form = await read_form(chunks, tmp_path)
    [spooled] = list(tmp_path.iterdir())
    assert spooled.read_bytes() == big
    assert spooled.stat().st_mode & 0o777 == 0o600
    assert "talk" not in spooled.name  # never the client's file name
    assert form.files[0].path_or_bytes == spooled
    form.close()
    assert list(tmp_path.iterdir()) == []
    form.close()  # twice is safe


@pytest.mark.anyio
async def test_the_spool_threshold_is_the_forms_not_each_parts(tmp_path):
    # Small file parts held in memory come to SPOOL_ABOVE in all, not each, so 31 parts just under it can't hold
    # 31 MiB (the controller's ruling, at Task 20's review): the first part that would take the form past it spools.
    parts = [field("model", "stt")] + [file_part(bytes([i]) * (600 << 10), filename=f"{i}.wav") for i in range(3)]
    form = await read_form([form_body(parts)], tmp_path)
    kept = [part.path_or_bytes for part in form.files]
    assert kept[0] == b"\x00" * (600 << 10)
    assert [path.read_bytes() for path in kept[1:]] == [b"\x01" * (600 << 10), b"\x02" * (600 << 10)]
    assert sorted(tmp_path.iterdir()) == sorted(kept[1:])
    form.close()
    many = [field("model", "stt")] + [file_part(b"s" * (100 << 10), filename=f"{i}.wav") for i in range(20)]
    form = await read_form([form_body(many)], tmp_path)
    in_memory = sum(len(part.path_or_bytes) for part in form.files if isinstance(part.path_or_bytes, bytes))
    assert in_memory <= parse.SPOOL_ABOVE and len(list(tmp_path.iterdir())) == 10
    form.close()


@pytest.mark.anyio
async def test_a_part_under_the_threshold_stays_in_memory(tmp_path):
    small = b"x" * (100 << 10)
    form = await read_form([form_body([field("model", "stt"), file_part(small)])], tmp_path)
    assert form.files[0].path_or_bytes == small
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_a_delimiter_split_across_chunks_is_found(tmp_path):
    # The data, then the closing delimiter split a byte at a time: the part holds exactly its data.
    data = b"a\r\n-" * 1000
    body = form_body([field("model", "stt"), file_part(data)])
    form = await read_form([body[i:i + 1] for i in range(len(body))], tmp_path)
    assert form.files[0].path_or_bytes == data


@pytest.mark.anyio
async def test_a_boundary_followed_by_other_bytes_is_data(tmp_path):
    # Only a delimiter followed by padding, a line's end or `--` ends a part, as Go's reader decides.
    data = b"before\r\n--" + BOUNDARY.encode() + b"X after"
    body = form_body([field("model", "stt"), file_part(data)])
    form = await read_form([body[:len(body) - len(data) - 20], body[len(body) - len(data) - 20:]], tmp_path)
    assert form.files[0].path_or_bytes == data


@pytest.mark.anyio
@pytest.mark.parametrize(("parts", "words"), [
    ([field("model", "stt"), field("model", "coder")], parse.TWO_MODELS),
    ([field("language", "en")], parse.NO_MODEL_IN_FORM),
    ([field("model", "stt"), ({"Content-Disposition": 'form-data; name="x"'}, b"\xff\xfe")], parse.FIELD_NOT_TEXT),
    ([field("model", "stt"), ({"Content-Type": "text/plain"}, b"x")], parse.MALFORMED_FORM),  # no disposition
    ([field("model", "stt"), ({"Content-Disposition": "attachment; name=\"x\""}, b"x")], parse.MALFORMED_FORM),
    ([field("model", "stt"), ({"Content-Disposition": "form-data"}, b"x")], parse.MALFORMED_FORM),  # no name
    ([({"Content-Disposition": 'form-data; name="model"; name="other"'}, b"stt")], parse.MALFORMED_FORM),
    ([field("model", "stt"), ({"Content-Disposition": 'form-data; name="f"; filename="a\\'}, b"x")],
     parse.MALFORMED_FORM),  # an unclosed quote
    ([field("model", "stt"), ({"Content-Disposition": 'form-data; name="f"; filename="a.wav"',
                               "Content-Transfer-Encoding": "quoted-printable"}, b"x")], parse.ENCODED_PART),
])
async def test_a_form_the_front_cant_take_is_400(tmp_path, parts, words):
    with pytest.raises(parse.Refused) as excinfo:
        await read_form([form_body(parts)], tmp_path)
    assert refused(excinfo) == (400, "invalid_request", words)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_too_many_parts_or_fields_too_big_are_400(tmp_path):
    many = [field("model", "stt")] + [field(f"f{i}", "x") for i in range(parse.MAX_PARTS)]
    with pytest.raises(parse.Refused) as excinfo:
        await read_form([form_body(many)], tmp_path)
    assert excinfo.value.message == parse.TOO_MANY_PARTS
    big = [field("model", "stt"), field("prompt", "x" * parse.FIELDS_MAX)]
    with pytest.raises(parse.Refused) as excinfo:
        await read_form([form_body(big)], tmp_path)
    assert excinfo.value.message == parse.FIELDS_TOO_BIG


@pytest.mark.anyio
@pytest.mark.parametrize(("body", "words"), [
    (form_body([field("model", "stt")])[:-4], parse.INCOMPLETE_FORM),  # the closing delimiter cut short
    (form_body([field("model", "stt")]).split(b"\r\n--" + BOUNDARY.encode() + b"--")[0], parse.INCOMPLETE_FORM),
    (b"preamble\r\n" + form_body([field("model", "stt")]), parse.MALFORMED_FORM),
    (b"--" + BOUNDARY.encode() + b"\r\n\r\nstt\r\n--" + BOUNDARY.encode() + b"--\r\n", parse.MALFORMED_FORM),
    (b"", parse.INCOMPLETE_FORM),
])
async def test_a_body_that_isnt_a_whole_form_is_400(tmp_path, body, words):
    with pytest.raises(parse.Refused) as excinfo:
        await read_form([body], tmp_path)
    assert excinfo.value.message == words


@pytest.mark.anyio
@pytest.mark.parametrize(("content_type", "words"), [
    ("multipart/form-data", parse.NO_BOUNDARY),
    ("multipart/form-data; boundary=", parse.NO_BOUNDARY),
    ("multipart/form-data; boundary=" + "b" * 71, parse.NO_BOUNDARY),
    ("multipart/form-data; boundary=a; boundary=b", parse.NO_BOUNDARY),
    ("application/json", parse.NOT_A_FORM),
    ("", parse.NOT_A_FORM),
])
async def test_a_content_type_without_a_usable_boundary_is_400(tmp_path, content_type, words):
    with pytest.raises(parse.Refused) as excinfo:
        await read_form([form_body([field("model", "stt")])], tmp_path, content_type=content_type)
    assert refused(excinfo) == (400, "invalid_request", words)


@pytest.mark.anyio
async def test_a_quoted_boundary_and_a_utf8_file_name_are_read(tmp_path):
    parts = [field("model", "stt"), file_part(b"x", filename='r\\"é.wav')]
    form = await read_form([form_body(parts, boundary="a b")], tmp_path, content_type='multipart/form-data; '
                                                                                      'boundary="a b"')
    assert form.files[0].filename == 'r"é.wav'
    ext = [field("model", "stt"),
           ({"Content-Disposition": "form-data; name=\"file\"; filename=\"x\"; filename*=UTF-8''r%C3%A9.wav"}, b"y")]
    form = await read_form([form_body(ext)], tmp_path)
    assert form.files[0].filename == "ré.wav"  # filename* wins, as Go's mime package reads it


@pytest.mark.anyio
async def test_an_empty_file_name_is_a_field_as_go_reads_it(tmp_path):
    # llama-swap's Go parser takes a part with filename="" as a value: so does the front, and a second model so made
    # is refused as a second model field.
    parts = [field("model", "stt"), ({"Content-Disposition": 'form-data; name="model"; filename=""'}, b"coder")]
    with pytest.raises(parse.Refused) as excinfo:
        await read_form([form_body(parts)], tmp_path)
    assert excinfo.value.message == parse.TWO_MODELS


@pytest.mark.anyio
async def test_a_form_over_its_cap_is_413_and_its_spool_file_goes(tmp_path):
    head = form_body([field("model", "stt")])[:-len(f"--{BOUNDARY}--\r\n")]
    head += (f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n\r\n').encode()
    chunks = [head] + [b"z" * 65536] * 40  # 2.5 MiB of a file that never ends
    with pytest.raises(parse.Refused) as excinfo:
        await read_form(chunks, tmp_path, max_bytes=2 << 20)
    assert refused(excinfo)[:2] == (413, "request_too_large")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_a_client_gone_mid_form_leaves_no_spool_file(tmp_path):
    head = form_body([field("model", "stt")])[:-len(f"--{BOUNDARY}--\r\n")]
    head += (f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n\r\n').encode()
    with pytest.raises(parse.ClientGone), anyio.fail_after(BOUND_S):
        await parse.read_form(receiver([head, b"z" * (2 << 20)], gone=True), CONTENT_TYPE, spool_dir=tmp_path,
                              max_bytes=200 << 20)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_encode_form_rebuilds_what_was_read(tmp_path):
    big = b"\r\n--" + b"q" * (2 << 20)
    # The file name as a client quotes it, a backslash before each '"' and '\': a"b\.wav.
    parts = [field("model", "stt"), field("prompt", 'say "hi"\\'), file_part(big, filename='a\\"b\\\\.wav'),
             file_part(b"small", name="extra", filename="s.wav")]
    form = await read_form([form_body(parts)], tmp_path)
    content_type, body = parse.encode_form(form)
    assert re.fullmatch(r"multipart/form-data; boundary=spark-[0-9a-f]{32}", content_type)
    raw = b"".join([chunk async for chunk in body])
    (tmp_path / "again").mkdir()
    again = await read_form([raw], tmp_path / "again", content_type=content_type)
    assert again.model == "stt" and again.fields == form.fields
    assert [(f.name, f.filename, f.content_type) for f in again.files] == [
        ("file", 'a"b\\.wav', "audio/wav"), ("extra", "s.wav", "audio/wav")]
    assert again.files[0].path_or_bytes.read_bytes() == big and again.files[1].path_or_bytes == b"small"
    form.close()
    again.close()


# The body budget.

def test_the_budget_holds_bytes_until_they_are_given_back():
    budget = parse.BodyBudget(100)
    first, second = parse.Held(budget), parse.Held(budget)
    first.take(60)
    with pytest.raises(parse.Refused) as excinfo:
        second.take(41)
    assert refused(excinfo) == (429, "front_busy", parse.BUSY)
    assert excinfo.value.body() == {"error": {"message": parse.BUSY, "code": "front_busy"}, "retry_after_s": 10}
    assert excinfo.value.headers() == [(b"content-type", b"application/json"), (b"retry-after", b"10")]
    assert (budget.held, second.bytes) == (60, 0)  # a refused take holds nothing
    second.take(40)
    first.give(100)  # never more than it holds
    assert (budget.held, first.bytes) == (40, 0)
    second.release()
    second.release()
    assert budget.held == 0


@pytest.mark.anyio
async def test_a_json_body_is_charged_as_it_comes_then_as_it_goes_on():
    budget = parse.BodyBudget(1000)
    held = parse.Held(budget)
    raw = b'{"model":"coder",   "x":   "' + b"a" * 100 + b'"}'
    with anyio.fail_after(BOUND_S):
        _, sent = await parse.read_json(receiver([raw[:50], raw[50:]]), max_bytes=1000, held=held)
    assert held.bytes == budget.held == len(sent) < len(raw)  # the bytes held now are what goes on
    held.release()
    # A body that would take the budget past its limit is refused as it comes, holding nothing more.
    small = parse.BodyBudget(100)
    held = parse.Held(small)
    with pytest.raises(parse.Refused) as excinfo, anyio.fail_after(BOUND_S):
        await parse.read_json(receiver([raw[:60], raw[60:]]), max_bytes=1000, held=held)
    assert excinfo.value.status == 429 and small.held == held.bytes == 60
    held.release()
    assert small.held == 0


# The model list.

def test_a_name_or_a_role_resolves():
    models = parse.ModelList(MODELS)
    assert parse.resolve("coder", models)["name"] == "qwen3.8-27b"
    assert parse.resolve("qwen3.8-27b", models)["label"] == "the coder"
    assert parse.resolve("stt", models)["name"] == "whisper-large-v3-turbo"
    assert parse.resolve("vision", models)["name"] == "gemma-4-26b-a4b"
    assert parse.resolve("qwen3.6-35b-a3b", models) is None
    assert parse.resolve("", models) is None
    assert parse.resolve("CODER", models) is None  # exactly, as llama-swap matches


def test_a_name_wins_over_a_role():
    # llama-swap looks a name up among the models before the roles.
    models = parse.ModelList([{"name": "a", "roles": ["b"], "label": "A", "resident": True},
                              {"name": "b", "roles": [], "label": "B", "resident": True}])
    assert parse.resolve("b", models)["name"] == "b"


def test_the_model_list_is_replaced_whole_and_checked():
    models = parse.ModelList(MODELS)
    models.replace(MODELS[:1])
    assert [e["name"] for e in models] == ["gemma-4-26b-a4b"]
    for bad in ([{"name": "a", "roles": ["b"], "label": "A"}], [{"name": 1, "roles": [], "label": "A", "resident": 1}],
                [{"name": "a", "roles": "b", "label": "A", "resident": True}]):
        with pytest.raises(ValueError):
            models.replace(bad)
    assert [e["name"] for e in models] == ["gemma-4-26b-a4b"]  # a bad list changes nothing


def test_the_first_model_list_is_the_registrys():
    registry = load_registry(ROOT / "stack" / "models.yaml")
    models = parse.ModelList.from_registry(registry)
    assert [e["name"] for e in models] == list(registry.models)
    coder = next(m for m in registry.models.values() if m.label == "the coder")
    assert parse.resolve("coder", models) == {"name": coder.name, "roles": list(coder.roles), "label": "the coder",
                                              "resident": False}


# Headers.

def test_clean_headers_drops_credentials_framing_and_hop_by_hop():
    headers = [(b"host", b"spark"), (b"authorization", b"Bearer k1"), (b"x-api-key", b"k1"), (b"cookie", b"c=1"),
               (b"proxy-authorization", b"Basic x"), (b"content-length", b"5"), (b"transfer-encoding", b"chunked"),
               (b"content-type", b"application/json"), (b"content-encoding", b"gzip"), (b"expect", b"100-continue"),
               (b"connection", b"keep-alive, x-gone"), (b"keep-alive", b"timeout=5"), (b"te", b"trailers"),
               (b"trailer", b"x"), (b"upgrade", b"websocket"), (b"proxy-connection", b"keep-alive"),
               (b"x-gone", b"named by connection"), (b"x-custom", b"1"), (b"accept", b"text/event-stream"),
               (b"x-forwarded-for", b"203.0.113.9"), (b"x-real-ip", b"203.0.113.9"),
               (b"forwarded", b"for=203.0.113.9"), (b"user-agent", b"pi")]
    assert parse.clean_headers(headers) == [(b"x-custom", b"1"), (b"accept", b"text/event-stream"),
                                            (b"user-agent", b"pi")]


def test_both_lengths():
    assert parse.both_lengths([(b"content-length", b"5"), (b"transfer-encoding", b"chunked")])
    assert not parse.both_lengths([(b"content-length", b"5")])
    assert not parse.both_lengths([(b"transfer-encoding", b"chunked")])


# Caps.

def test_each_keys_caps_come_from_its_group():
    caps = parse.KeyCaps(KEYS, GROUPS)
    assert [caps.enter_waiting("agent") for _ in range(5)] == [True] * 4 + [False]
    assert [caps.enter_waiting("dan-mac") for _ in range(9)] == [True] * 8 + [False]
    caps.leave_waiting("agent")
    assert caps.enter_waiting("agent")
    assert [caps.enter_open("open-webui") for _ in range(33)] == [True] * 32 + [False]
    assert caps.enter_open("dan-mac")  # another key's own count
    for _ in range(40):
        caps.leave_open("open-webui")
    assert caps.open("open-webui") == 0  # never below 0
    assert caps.waiting("agent") == 4
    assert not caps.enter_open("robot") and not caps.enter_waiting("robot")  # a key not in the list has no room


# The journal.

def test_the_journal_line_is_key_model_status_and_duration():
    assert parse.journal_line("agent", "qwen3.8-27b", 200, 1.2) == "front: agent qwen3.8-27b 200 1.2s"
    assert parse.journal_line("dan-mac", "-", 404, 0.04) == "front: dan-mac - 404 0.0s"
    assert parse.journal_line("-", "-", "gone", -0.5) == "front: - - gone 0.0s"


# Words.

def test_the_fronts_own_words_never_make_pi_retry():
    words = [value for name, value in vars(parse).items() if name.isupper() and isinstance(value, str)
             and value.endswith(".")]
    assert len(words) >= 12  # the walk reaches them
    words += [parse.too_big(200 << 20).message, parse.too_big(16 << 20).message]
    for text in words:
        for status in (400, 413):
            shown = f"{status}: " + json.dumps({"message": text, "code": "invalid_request"})
            assert messages.pi_retry_match(shown) is None, text
