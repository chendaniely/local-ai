"""The front's usage scanner (Task 44a): counts from a fixed head and tail, in linear time. The fixtures have
llama-server b11146's shape (server-task.cpp): chunk fields in the order choices, created, id, model,
system_fingerprint, object[, usage], timings; the first delta has a null content; a body ends with usage, id, timings.
Then (Task E3) Qwen3-ASR's counts, and whisper's answers: the format whisper reads from a form, its recording's length,
and its json and text rebuilt from verbose_json byte for byte. Every id, text and number here is a stand-in."""

import json
import time
import tracemalloc
from pathlib import Path

import pytest

from front_helpers import SRT, VERBOSE, VTT
from spark.front import parse, usage
from spark.front.usage import Counts, UsageScan, safe_text, scan_for
from spark.registry import load_registry

CHAT = "/v1/chat/completions"
DONE = b"data: [DONE]\n\n"
PING = b":\n\n"
ID = "chatcmpl-AbC123"


def sse(data: str) -> bytes:
    return f"data: {data}\n\n".encode()


def chunk(delta: dict, *, tail: str = "", cid: str = ID) -> bytes:
    body = json.dumps([{"finish_reason": None, "index": 0, "delta": delta}], separators=(",", ":"))
    return sse(f'{{"choices":{body},"created":1790000000,"id":"{cid}","model":"m","system_fingerprint":"b11146",'
               f'"object":"chat.completion.chunk"{tail}}}')


def timings(prompt_n=10, cache_n=90, predicted_n=5, prompt_ms=12.5, predicted_ms=80.25) -> str:
    return (f',"timings":{{"cache_n":{cache_n},"prompt_n":{prompt_n},"prompt_ms":{prompt_ms},'
            f'"prompt_per_token_ms":1.0,"prompt_per_second":9.0,"predicted_n":{predicted_n},'
            f'"predicted_ms":{predicted_ms},"predicted_per_token_ms":1.0,"predicted_per_second":9.0}}')


def usage_json(p=200, o=7, cached: int | None = 150) -> str:
    details = "null" if cached is None else f'{{"cached_tokens":{cached}}}'
    return f',"usage":{{"completion_tokens":{o},"prompt_tokens":{p},"total_tokens":{p + o},"prompt_tokens_details":{details}}}'


def scan(chunks, path=CHAT, streamed=True) -> Counts:
    s = UsageScan(path, streamed=streamed)
    for piece in chunks:
        s.feed(piece)
    return s.finish()


def stream_without_usage(predicted=(5,)) -> list[bytes]:
    out = [PING, chunk({"role": "assistant", "content": None}),
           chunk({"reasoning_content": "hm"}), chunk({"content": "hi"})]
    for n in predicted:
        out.append(chunk({}, tail=timings(predicted_n=n)))
    return out + [DONE]


def test_a_chat_stream_without_usage_is_counted_from_the_last_timings():
    got = scan(stream_without_usage())
    assert (got.p, got.c, got.o, got.source) == (100, 90, 5, "timings")
    assert (got.ev_think, got.ev_text) == (1, 1)
    assert (got.prompt_ms, got.gen_ms) == (12, 80)
    assert got.resp_id == ID


def test_timings_repeated_at_a_stop_word_count_once():
    assert scan(stream_without_usage(predicted=(4, 5))).o == 5


def test_usage_wins_over_timings_and_the_last_copy_of_each_wins():
    chunks = [chunk({"content": "a"}, tail=usage_json(1, 1, 1) + timings(cache_n=3)),
              chunk({}, tail=usage_json(200, 7, 150) + timings(cache_n=3)), DONE]
    got = scan(chunks)
    assert (got.p, got.c, got.o, got.source) == (200, 150, 7, "usage")


def test_one_feed_holding_the_finish_the_usage_and_done():
    blob = chunk({"content": "x"}) + chunk({}, tail=usage_json(200, 7, 150)) + DONE
    got = scan([blob])
    assert (got.p, got.c, got.o, got.source) == (200, 150, 7, "usage")


def test_a_completions_stream_is_counted():
    text = sse('{"choices":[{"text":"a","index":0,"finish_reason":null}],"created":1,"id":"cmpl-9","model":"m",'
               '"system_fingerprint":"b","object":"text_completion"}')
    last = sse('{"choices":[{"text":"","index":0,"finish_reason":"stop"}],"created":1,"id":"cmpl-9","model":"m",'
               '"system_fingerprint":"b","object":"text_completion"' + usage_json(20, 3, 10) + "}")
    got = scan([text, last, DONE], path="/v1/completions")
    assert (got.p, got.c, got.o, got.source, got.resp_id) == (20, 10, 3, "usage", "cmpl-9")


def test_no_cache_reported_is_none_not_zero():
    got = scan([chunk({}, tail=usage_json(20, 3, None)), DONE])
    assert (got.p, got.c, got.o) == (20, None, 3)
    body = '{"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2},"id":"chatcmpl-1"}'
    assert scan([body.encode()], streamed=False).c is None


def test_embeddings_usage_sits_before_the_vectors():
    vector = "[" + ",".join(["0.0123456"] * 1024) + "]"
    data = ",".join(f'{{"embedding":{vector},"index":{i},"object":"embedding"}}' for i in range(8))
    body = ('{"model":"e","object":"list","usage":{"prompt_tokens":12,"total_tokens":12},"data":[' + data + "]}")
    assert len(body) > usage.HEAD + usage.TAIL
    got = scan([body.encode()[i:i + 8000] for i in range(0, len(body), 8000)], path="/v1/embeddings", streamed=False)
    assert (got.p, got.c, got.o, got.source) == (12, None, 0, "usage")


def test_a_line_split_across_chunks_and_a_cr_lf_split_are_read_whole():
    raw = PING + chunk({"content": "a"}) + chunk({}, tail=usage_json(200, 7, 150)) + DONE
    for cut in range(0, len(raw), 7):
        got = scan([raw[:cut], raw[cut:]])
        assert (got.p, got.c, got.o) == (200, 150, 7), cut
    crlf = raw.replace(b"\n", b"\r\n")
    for cut in range(0, len(crlf), 5):
        got = scan([crlf[:cut], crlf[cut:]])
        assert (got.p, got.o, got.ev_text) == (200, 7, 1), cut
    cr = raw.replace(b"\n", b"\r")
    assert scan([bytes([b]) for b in cr]).o == 7


def test_a_non_streamed_resp_id_is_the_top_level_id_never_a_tool_calls():
    calls = '[{"id":"call_report-q3.md","type":"function","function":{"name":"f","arguments":"{}"}}]'
    pad = "x" * (usage.TAIL + 100)
    body = (f'{{"choices":[{{"finish_reason":"tool_calls","index":0,"message":{{"role":"assistant","content":"{pad}",'
            f'"tool_calls":{calls}}}}}],"created":1,"model":"m","system_fingerprint":"b","object":"chat.completion",'
            f'"usage":{{"completion_tokens":2,"prompt_tokens":3,"total_tokens":5}},"id":"{ID}"{timings()}}}')
    assert scan([body.encode()], streamed=False).resp_id == ID
    head_call = (f'{{"choices":[{{"message":{{"tool_calls":[{{"id":"call_report-q3.md"}}],"content":"{pad}"}}}}],'
                 f'"usage":{{"prompt_tokens":3,"completion_tokens":2}},"id":"{ID}"}}')
    assert scan([head_call.encode()], streamed=False).resp_id == ID
    short = ('{"choices":[{"message":{"tool_calls":[{"id":"call_report-q3.md"}]}}],"usage":{"prompt_tokens":3}}')
    assert scan([short.encode()], streamed=False).resp_id is None


def test_a_streamed_resp_id_is_the_top_level_id_never_a_tool_calls():
    first = sse('{"choices":[{"finish_reason":null,"index":0,"delta":{"tool_calls":[{"id":"call_report-q3.md",'
                '"index":0,"type":"function","function":{"name":"f","arguments":""}}]}}],"created":1,'
                f'"id":"{ID}","model":"m","system_fingerprint":"b","object":"chat.completion.chunk"}}')
    assert scan([first, DONE]).resp_id == ID


@pytest.mark.parametrize("streamed", [True, False])
@pytest.mark.parametrize("n", [30, 16_000])
def test_an_unclosed_usage_or_timings_object_is_dropped_in_linear_time(n, streamed):
    junk = b'"usage":{' + b"a" * n
    junk2 = b',"timings":{' + b'"a":{' * (n // 5)
    line = b'data: {"choices":[],' + junk + junk2 + b"\n\n" if streamed else b'{"choices":[],' + junk + junk2
    begin = time.thread_time()
    got = scan([line])
    assert time.thread_time() - begin < 0.05
    assert got.source == "none"
    many = (b'"usage":{' * 2000)
    begin = time.thread_time()
    scan([b"data: " + many + b"\n\n" if streamed else many], streamed=streamed)
    assert time.thread_time() - begin < 0.05


def test_a_multi_megabyte_line_costs_constant_memory_and_little_cpu():
    one = b"x" * 65536
    s = UsageScan(CHAT, streamed=True)
    s.feed(b'data: {"choices":[{"delta":{"content":"')
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    begin = time.thread_time()
    for _ in range(128):
        s.feed(one)
        assert s.held_bytes() <= usage.HEAD + usage.TAIL
    s.feed(('"}}]' + usage_json(200, 7, 150) + "}\n\n").encode() + DONE)
    got = s.finish()
    cpu = time.thread_time() - begin
    peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.stop()
    assert peak < len(one) + 256 * 1024
    assert cpu < 0.25
    assert (got.p, got.c, got.o) == (200, 150, 7)


@pytest.mark.parametrize("bad", ["true", "-1", "1.5", str(2**53), '"7"', "null", "1e3"])
def test_bad_numbers_are_not_reported(bad):
    body = f'{{"usage":{{"prompt_tokens":{bad},"completion_tokens":2,"prompt_tokens_details":{{"cached_tokens":{bad}}}}}}}'
    got = scan([body.encode()], streamed=False)
    assert (got.p, got.c, got.o, got.source) == (None, None, 2, "usage")
    assert scan([f'{{"usage":{{"prompt_tokens":{2**53 - 1}}}}}'.encode()], streamed=False).p == 2**53 - 1
    t = f'{{"timings":{{"prompt_n":{bad},"cache_n":{bad},"predicted_n":{bad}}}}}'
    assert scan([t.encode()], streamed=False) == Counts()


def test_a_quoted_usage_inside_content_is_not_counted():
    text = json.dumps('he said "usage": {"prompt_tokens": 999, "completion_tokens": 999} and "timings": {"predicted_n": 9}')
    line = sse(f'{{"choices":[{{"delta":{{"content":{text}}}}}],"id":"{ID}"}}')
    got = scan([line, DONE])
    assert (got.source, got.p, got.o, got.ev_text) == ("none", None, None, 1)
    got = scan([f'{{"choices":[{{"message":{{"content":{text}}}}}]}}'.encode()], streamed=False)
    assert got.source == "none"


def test_multibyte_and_invalid_bytes_at_the_edges():
    word = "é€😀".encode()
    pad = word * (usage.TAIL // len(word) + 5) + b"\xff\xfe"
    body = b'{"choices":[{"message":{"content":"' + b"\xff" + pad + b'"}}],' + usage_json(5, 6, 1)[1:].encode() + b"}"
    got = scan([body], streamed=False)
    assert (got.p, got.c, got.o) == (5, 1, 6)
    line = b'data: {"choices":[{"delta":{"content":"' + pad + b'"}}]' + usage_json(5, 6, 1).encode() + b"}\n\n"
    assert scan([line, DONE]).o == 6
    junk = b'data: {"usage":{"prompt_tokens":\xff}}\n\n'
    assert scan([junk]).source == "none"


def test_nothing_found_is_source_none():
    assert scan([PING, DONE]) == Counts(ev_think=0, ev_text=0)
    assert scan([b"{}"], streamed=False) == Counts()
    assert scan([]) == Counts(ev_think=0, ev_text=0)


def test_scan_for_counts_only_the_four_routes_and_2xx():
    sse_h = [(b"Content-Type", b"text/event-stream; charset=utf-8")]
    json_h = [(b"content-type", b"application/json")]
    for path in parse.INFERENCE_PATHS:
        got = scan_for(path, 200, sse_h)
        assert (got is not None) == (path in usage.ROUTES_COUNTED), path
    assert set(usage.ROUTES_COUNTED) == {CHAT, "/v1/completions", "/v1/embeddings", "/v1/audio/transcriptions"}
    assert set(usage.ROUTE_NAMES) == set(parse.INFERENCE_PATHS)
    assert scan_for(CHAT, 200, sse_h).streamed is True
    assert scan_for(CHAT, 204, json_h).streamed is False
    assert scan_for(CHAT, 199, json_h) is None and scan_for(CHAT, 300, json_h) is None
    assert scan_for(CHAT, 200, []).streamed is False
    assert scan_for(CHAT, 200, sse_h + [(b"content-encoding", b"gzip")]) is None
    assert scan_for(CHAT, 200, sse_h + [(b"Content-Encoding", b"Identity")]) is not None


def test_safe_text_takes_only_the_pattern():
    assert safe_text("chatcmpl-AbC1.2_3:x") == "chatcmpl-AbC1.2_3:x"
    assert safe_text("a" * 64) and safe_text("a" * 65) is None
    for bad in ("", "a b", "a\n", "é", "a/b", "../x", "<b>", None, 5, b"abc"):
        assert safe_text(bad) is None


def test_finish_is_idempotent():
    s = UsageScan(CHAT, streamed=True)
    for piece in stream_without_usage():
        s.feed(piece)
    first = s.finish()
    s.feed(chunk({}, tail=usage_json(1, 1, 1)))
    assert s.finish() is first
    assert s.held_bytes() == 0


# Qwen3-ASR's counts (Task E3): llama-server b11146's transcription answer (server-task.cpp,
# to_json_oaicompat_asr), its usage last. Every number here is a stand-in.

ASR_ANSWER = ('{"type":"transcript.text.done","text":"Hello there.","usage":{"type":"tokens","input_tokens":412,'
              '"output_tokens":9,"total_tokens":421,"input_tokens_details":{"cached_tokens":12}}}').encode()


def test_a_qwen3_asr_answer_is_counted_from_its_usage():
    got = scan([ASR_ANSWER], path=usage.TRANSCRIPTIONS, streamed=False)
    assert (got.p, got.c, got.o, got.source) == (412, 12, 9, "usage")
    no_cache = ASR_ANSWER.replace(b',"input_tokens_details":{"cached_tokens":12}', b"")
    assert scan([no_cache], path=usage.TRANSCRIPTIONS, streamed=False).c is None  # not reported: absent, never 0


def test_the_transcription_keys_count_only_on_the_transcriptions_route():
    # A chat answer with a transcription's usage keys counts nothing: chat rows read only chat's own.
    got = scan([ASR_ANSWER], path=CHAT, streamed=False)
    assert (got.p, got.c, got.o, got.source) == (None, None, None, "none")
    # And a transcription with chat's keys counts nothing either.
    chat_keys = b'{"text":"x","usage":{"prompt_tokens":4,"completion_tokens":2}}'
    assert scan([chat_keys], path=usage.TRANSCRIPTIONS, streamed=False).source == "none"


# Whisper's recording length (Task E3; website/design/usage-meter-every-model.md, *The front learns each Whisper
# recording's length*). Whisper's answers have whisper.cpp v1.9.4's shape (examples/server/server.cpp, 1017-1176):
# `verbose_json` and `json` dumped by nlohmann::ordered_json's dump(-1, ' ', false, replace), compact and in UTF-8;
# `text` the segments' text, each followed by a line break; srt and vtt as its to_timestamp writes their cues. Every
# text, number and name here is invented.

UPLOAD = 1 << 20  # an upload's file bytes, well under what the front converts


def form(*fields: tuple[str, str], files: tuple[str, ...] = ("file",)) -> parse.Form:
    return parse.Form("stt", [("model", "stt"), *fields],
                      [parse.FilePart(name, "talk.wav", "audio/wav", b"RIFF") for name in files])


@pytest.mark.parametrize(("fields", "plan"), [
    ((), usage.Whisper("json", convert=True, token_timestamps="false")),
    ((("response_format", "json"),), usage.Whisper("json", convert=True, token_timestamps="false")),
    ((("response_format", "text"),), usage.Whisper("text", convert=True, token_timestamps="false")),
    ((("response_format", "verbose_json"),), usage.Whisper("verbose_json", convert=False)),
    ((("response_format", "srt"),), usage.Whisper("srt", convert=False)),
    ((("response_format", "vtt"),), usage.Whisper("vtt", convert=False)),
    # Whisper reads the first of a field given twice (httplib's multimap find), as the front does.
    ((("response_format", "srt"), ("response_format", "json")), usage.Whisper("srt", convert=False)),
    # verbose_json with detect_language is still read: whisper's answer has its length.
    ((("response_format", "verbose_json"), ("detect_language", "true")), usage.Whisper("verbose_json", convert=False)),
    # The form's own token_timestamps is what whisper uses for either format: nothing to add.
    ((("token_timestamps", "true"),), usage.Whisper("json", convert=True)),
    # Otherwise whisper turns token timestamps on for the formats that aren't verbose_json only with max_len above 0 or
    # split_on_word, and never with no_timestamps; with them on, its max_len wraps the segments (server.cpp, 627-636).
    ((("max_len", "42"),), usage.Whisper("json", convert=True, token_timestamps="true")),
    ((("max_len", " +7 words"),), usage.Whisper("json", convert=True, token_timestamps="true")),  # std::stoi's
    ((("max_len", "0"),), usage.Whisper("json", convert=True, token_timestamps="false")),
    ((("max_len", "-3"),), usage.Whisper("json", convert=True, token_timestamps="false")),
    ((("response_format", "text"), ("split_on_word", "yes")),
     usage.Whisper("text", convert=True, token_timestamps="true")),
    ((("response_format", "text"), ("split_on_word", "on")),  # not one of the four words whisper takes as true
     usage.Whisper("text", convert=True, token_timestamps="false")),
    ((("max_len", "42"), ("no_timestamps", "1")), usage.Whisper("json", convert=True, token_timestamps="false")),
])
def test_whisper_plan_by_the_format_whisper_reads(fields, plan):
    assert usage.whisper_plan(form(*fields), UPLOAD) == plan


@pytest.mark.parametrize("fields", [
    (("response_format", "JSON"),),  # whisper matches its formats exactly: anything else answers as json
    (("response_format", "lrc"),),
    (("response_format", ""),),
    (("detect_language", "false"),),  # present at all, whatever its value: only a form without it is converted
    (("response_format", "text"), ("detect_language", "true")),
    (("max_len", "lots"),),  # std::stoi throws: whisper answers 500 whatever the format
    (("max_len", "99999999999"),),  # past an int: the same
])
def test_whisper_plan_leaves_any_other_request_as_it_came(fields):
    assert usage.whisper_plan(form(*fields), UPLOAD) is None


@pytest.mark.parametrize("name", ["response_format", "detect_language", "token_timestamps", "no_timestamps", "max_len",
                                  "split_on_word"])
def test_whisper_plan_leaves_a_form_alone_when_a_file_part_has_a_name_whisper_reads(name):
    assert usage.whisper_plan(form(("response_format", "srt"), files=("file", name)), UPLOAD) is None


@pytest.mark.parametrize("asked", ["json", "text"])
def test_only_an_upload_of_at_most_16_mib_is_converted(asked):
    # Its transcript then can't come near the held answer's cap; a larger one goes as it came, its length estimated
    # from its size when the meter reads its row.
    assert usage.CONVERT_UPLOAD_MAX == 16 * 2**20
    assert usage.whisper_plan(form(("response_format", asked)), usage.CONVERT_UPLOAD_MAX).convert
    assert usage.whisper_plan(form(("response_format", asked)), usage.CONVERT_UPLOAD_MAX + 1) is None
    assert usage.whisper_plan(form(("response_format", asked)), None) is None  # its size unread: never converted
    # The formats that go as they came are held whatever the upload's size.
    for kept in ("verbose_json", "srt", "vtt"):
        assert usage.whisper_plan(form(("response_format", kept)), 200 * 2**20) == usage.Whisper(kept, convert=False)


def test_a_converted_form_asks_for_verbose_json_once_with_its_token_timestamps_and_no_language_probabilities():
    # Language probabilities cost whisper another encoder pass (server.cpp, 1076-1088), and json and text never
    # carry them: a converted form asks for none, whatever the client's form said.
    asked = form(("language", "en"), ("response_format", "json"), ("prompt", "hi"), ("response_format", "srt"),
                 ("no_language_probabilities", "false"))
    plan = usage.Whisper("json", convert=True, token_timestamps="false")
    assert usage.verbose_fields(asked, plan) == [("model", "stt"), ("language", "en"), ("prompt", "hi"),
                                                 ("response_format", "verbose_json"), ("token_timestamps", "false"),
                                                 ("no_language_probabilities", "true")]
    assert usage.verbose_fields(form(("token_timestamps", "1")), usage.Whisper("json", convert=True)) == [
        ("model", "stt"), ("token_timestamps", "1"), ("response_format", "verbose_json"),
        ("no_language_probabilities", "true")]


JSON_PLAN = usage.Whisper("json", convert=True, token_timestamps="false")
TEXT_PLAN = usage.Whisper("text", convert=True, token_timestamps="false")


def test_a_converted_json_answer_is_whispers_own_json_byte_for_byte():
    heard = usage.hear(JSON_PLAN, VERBOSE)
    # server.cpp, 1160-1172: {"text": results}, dumped compact with ensure_ascii false, as application/json.
    assert heard.body == b'{"text":" Hello there.\\n \xc3\x87a va? \xe4\xbd\xa0\xe5\xa5\xbd \xf0\x9f\x98\x80\\n"}'
    assert heard.content_type == b"application/json"
    assert (heard.audio_s, heard.audio_est) == (12.345, False)


def test_a_converted_text_answer_is_whispers_own_text_byte_for_byte():
    heard = usage.hear(TEXT_PLAN, VERBOSE)
    # server.cpp, 1017-1021: the segments' text, a line break after each, as text/html; charset=utf-8.
    assert heard.body == " Hello there.\n Ça va? 你好 😀\n".encode()
    assert heard.content_type == b"text/html; charset=utf-8"
    assert (heard.audio_s, heard.audio_est) == (12.345, False)


def verbose(escaped_text: str, duration: str = "1.5") -> bytes:
    """A verbose_json answer whose text is `escaped_text`, as whisper escaped it."""
    return (f'{{"task":"transcribe","language":"english","duration":{duration},"text":"{escaped_text}",'
            '"segments":[]}').encode()


def test_json_escapes_as_nlohmann_does_and_text_stops_at_a_nul():
    # nlohmann's dump_escaped with ensure_ascii false: \b \t \n \f \r \" \\ short, any other control character as
    # \u00xx in lower case, everything else, 0x7f, / and non-ASCII among it, as its own bytes. The same escaper writes
    # verbose_json's text and json's, so the converted text is verbose_json's, byte for byte.
    escaped = '\\u0000\\u0001\\b\\t\\n\\u000b\\f\\r\\u001f \x7f/\\"\\\\é '
    assert usage.hear(JSON_PLAN, verbose(escaped)).body == f'{{"text":"{escaped}"}}'.encode()
    # text: res.set_content(results.c_str(), …) makes a std::string of a C string, which ends at the first NUL.
    assert usage.hear(TEXT_PLAN, verbose("a\\u0000b")).body == b"a"
    assert usage.hear(TEXT_PLAN, verbose("a\\u0001\\tb\\n")).body == b"a\x01\tb\n"


def nlohmann_escaped(text: str) -> str:
    """nlohmann's dump_escaped with ensure_ascii false (whisper.cpp v1.9.4's examples/json.hpp, 18275-18370), for a
    valid string, written out a character at a time."""
    short = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r", 0x22: '\\"', 0x5C: "\\\\"}
    return "".join(short.get(ord(char)) or (f"\\u{ord(char):04x}" if ord(char) <= 0x1F else char) for char in text)


def test_the_json_escaper_is_nlohmanns_over_every_code_point():
    text = "".join(chr(c) for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF)
    escaped = nlohmann_escaped(text)
    assert escaped.count("\\u") == 32 - 5  # the control characters without a short form
    assert usage.hear(JSON_PLAN, verbose(escaped)).body == f'{{"text":"{escaped}"}}'.encode()


@pytest.mark.parametrize("answer", [
    b"", b"not json", b"[1,2]", b'"text"', verbose("x").replace(b'"text":"x",', b""),
    verbose("x", "null"), verbose("x", "-0.5"), verbose("x", "true"), verbose("x", '"12.3"'), verbose("x", "1e999"),
    verbose("x", "NaN"), verbose("x", "Infinity"), verbose("x", str(10**400)), verbose("x", str(2**53)),
    verbose("x").replace(b'"x"', b"7"), verbose("\\ud800"),  # a lone surrogate, which UTF-8 can't carry
    b'{"text":"x","duration":1.5,"a":' + b"[" * 100_000 + b"]" * 100_000 + b"}",  # past the parse's depth
    b'{"text":"x","duration":1.5,"a":[' + b"0," * parse.MAX_JSON_PARTS + b"0]}",  # past its parts
], ids=["empty", "not json", "an array", "a string", "no text", "null", "negative", "true", "a string length",
        "past a float", "NaN", "Infinity", "past an int", "past LIMIT", "text a number", "a lone surrogate", "too deep",
        "too many parts"])
def test_a_converted_answer_without_a_readable_text_and_length_is_none(answer):
    assert usage.hear(JSON_PLAN, answer) is None
    assert usage.hear(TEXT_PLAN, answer) is None


def test_a_verbose_json_answer_goes_unchanged_with_its_length():
    plan = usage.Whisper("verbose_json", convert=False)
    heard = usage.hear(plan, VERBOSE)
    assert heard.body is VERBOSE and heard.content_type is None
    assert (heard.audio_s, heard.audio_est) == (12.345, False)
    assert usage.hear(plan, verbose("x", "0")).audio_s == 0.0
    for unreadable in (b"not json", verbose("x", "null"), b'{"error":"failed"}'):
        heard = usage.hear(plan, unreadable)
        assert (heard.body, heard.content_type, heard.audio_s, heard.audio_est) == (unreadable, None, None, False)


def test_srt_and_vtt_go_unchanged_with_their_last_cues_end_as_an_estimate():
    heard = usage.hear(usage.Whisper("srt", convert=False), SRT)
    assert (heard.body, heard.content_type, heard.audio_s, heard.audio_est) == (SRT, None, 62.345, True)
    heard = usage.hear(usage.Whisper("vtt", convert=False), VTT)
    assert (heard.body, heard.content_type, heard.audio_s, heard.audio_est) == (VTT, None, 3602.345, True)
    # Each format's own separator only; no cue (silence), or a body that isn't one, gives no length.
    assert usage.hear(usage.Whisper("srt", convert=False), VTT).audio_s is None
    for body in (b"", b"WEBVTT\n\n", b"\xff\xfe", b'{"error":"x"}'):
        heard = usage.hear(usage.Whisper("vtt", convert=False), body)
        assert (heard.body, heard.audio_s, heard.audio_est) == (body, None, False)


def test_only_a_cues_own_timing_line_is_read():
    # A transcript line that looks like a timing line isn't one: a timing line follows a blank line, or opens the
    # answer, and in srt comes after its cue's number (whisper's own, offset_n added, so it can be negative).
    srt = ("1\n00:00:00,000 --> 00:00:05,120\n Hello there.\n00:00:00,000 --> 99:59:59,999\n\n"
           "-2\n00:00:05,120 --> 00:01:02,345\n Ça va?\n12:00:00,000 --> 99:00:00,000\n\n").encode()
    assert usage.hear(usage.Whisper("srt", convert=False), srt).audio_s == 62.345
    vtt = ("WEBVTT\n\n00:00:00.000 --> 00:00:05.120\n Hello there.\n\n00:00:05.120 --> 00:01:02.345\n"
           "12:00:00.000 --> 99:00:00.000\n\n").encode()
    assert usage.hear(usage.Whisper("vtt", convert=False), vtt).audio_s == 62.345
    assert usage.hear(usage.Whisper("srt", convert=False), b"00:00:00,000 --> 00:00:05,120\n x\n\n").audio_s is None
    # A length past usage.LIMIT is none.
    huge = b"1\n00:00:00,000 --> 9999999999999:00:00,000\n x\n\n"  # 13 digits of hours: past 2**53 seconds
    assert usage.hear(usage.Whisper("srt", convert=False), huge).audio_s is None


def test_a_converted_answers_headers_are_whispers_own_for_its_format():
    upstream = [(b"content-type", b"application/json"), (b"content-length", b"999"), (b"date", b"Thu, 12 Nov 2026")]
    assert usage.whisper_headers(upstream, b"text/html; charset=utf-8", 17) == [
        (b"content-type", b"text/html; charset=utf-8"), (b"content-length", b"17"), (b"date", b"Thu, 12 Nov 2026")]
    # No length known (a cut), or none given upstream.
    assert usage.whisper_headers(upstream, b"application/json", None) == [
        (b"content-type", b"application/json"), (b"date", b"Thu, 12 Nov 2026")]
    assert usage.whisper_headers([(b"date", b"d")], b"application/json", 5) == [
        (b"date", b"d"), (b"content-type", b"application/json"), (b"content-length", b"5")]


def test_the_held_answers_cap_is_16_mib():
    assert usage.HELD_MAX == 16 * 2**20


def test_the_stacks_whisper_servers_keep_the_defaults_the_conversion_assumes():
    # whisper_plan reads max_len, split_on_word, no_timestamps and detect_language from the form, falling back on
    # whisper-server's defaults; a flag that changes one of those defaults would change json's answer from the one the
    # front rebuilds from verbose_json.
    registry = load_registry(Path(__file__).resolve().parents[2] / "stack" / "models.yaml")
    whispers = [model for model in registry.models.values() if model.engine == usage.WHISPER_ENGINE]
    assert whispers
    for model in whispers:
        assert not set(model.args) & usage.WHISPER_FLAGS_ASSUMED_OFF, model.name


@pytest.mark.parametrize("plan", [JSON_PLAN, TEXT_PLAN])
def test_reading_a_held_answer_stays_within_the_parse_bound(plan):
    # app.py's memory bound counts one parse at a time, measured on the Spark at Task 20's review at 192 MiB at its
    # worst, its bytes included: 16 MiB of ASCII text with one emoji, which Python holds at 4 bytes a character. A held
    # answer of that shape, read and rebuilt, stays within 8 times its own bytes (128 MiB) over them: 144 MiB in all.
    text = "a" * (usage.HELD_MAX - 200) + "\U0001f600"
    body = json.dumps({"task": "transcribe", "language": "english", "duration": 1.5, "text": text, "segments": []},
                      ensure_ascii=False, separators=(",", ":")).encode()
    del text
    assert len(body) <= usage.HELD_MAX
    tracemalloc.start()
    base = tracemalloc.get_traced_memory()[0]
    heard = usage.hear(plan, body)
    peak = tracemalloc.get_traced_memory()[1] - base
    tracemalloc.stop()
    assert heard is not None and heard.audio_s == 1.5
    assert peak <= 8 * len(body) + (1 << 20)
