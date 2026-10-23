"""The front's usage scanner (Task 44a): counts from a fixed head and tail, in linear time. The fixtures have
llama-server b11146's shape (server-task.cpp): chunk fields in the order choices, created, id, model,
system_fingerprint, object[, usage], timings; the first delta has a null content; a body ends with usage, id, timings.
Every id and number here is a stand-in."""

import json
import time
import tracemalloc

import pytest

from spark.front import parse, usage
from spark.front.usage import Counts, UsageScan, safe_text, scan_for

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


def test_scan_for_counts_only_the_three_routes_and_2xx():
    sse_h = [(b"Content-Type", b"text/event-stream; charset=utf-8")]
    json_h = [(b"content-type", b"application/json")]
    for path in parse.INFERENCE_PATHS:
        got = scan_for(path, 200, sse_h)
        assert (got is not None) == (path in usage.ROUTES_COUNTED), path
    assert set(usage.ROUTES_COUNTED) == {CHAT, "/v1/completions", "/v1/embeddings"}
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
