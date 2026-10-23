---
title: "The usage meter's counter — implementation plan"
description: "Phase 2a's part of the usage meter: the front writes a row per request, the gate a ledger of its events, in folders only Dan reads; keys for OpenCode and Hermes; doctor's checks, the units, bootstrap, the docs. Tasks 44a onward, run after Task 44 and before Task 45."
date: 2026-10-23
---

# The usage meter's counter — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** from Phase 2a's soak on, every request through the front leaves one row of numbers in a folder only Dan
reads, and the gate leaves a row for each load, unload, refusal and brake event, so nothing the meter will price is
lost before the rest of it (Phase 2b) is built.

**Architecture:** a bounded scanner in the front reads each response's counts from a fixed head and tail
(`front/usage.py`); a shared writer appends JSONL rows from a thread of its own (`rowfile.py`); the front builds one row
per request when it ends; the gate's admission answer carries a load's seconds, and a gate ledger writes its events;
systemd units, bootstrap and doctor give the two folders their owners, modes and checks.

**Tech Stack:** Python (the `spark` uv project), pytest with anyio, raw ASGI on uvicorn, systemd units rendered from
`stack/templates/`, bash for bootstrap.

**Spec:** [the usage meter's design](usage-meter.md), with [its questions and answers](usage-meter-qa.md). This plan
builds its §1, §2, §6 (2a's half) and the 2a parts of §8 and §9; Phase 2b's plan builds the rest.

**Where it runs.** Each task's label says where its work belongs by the repo's rule: **[Spark]** for code, tests and
docs, **[Dan]** for keys and sudo. *(Dan's instruction, 2026-10-23: this round writes and tests the [Spark] tasks on
the Mac, on branch `chendaniely/harness-status-bar`, cut from `phase-2a`; Task 44l, the deploy, and every [Dan] step
stay for the Spark session and Dan.)* Tests run with `make test ARGS="…"`, which is `uv run --frozen --project spark
pytest …`.

## Global Constraints

- Rows hold numbers, and text only from a fixed set or `[A-Za-z0-9._:-]{1,64}`; never a prompt, an answer, a file
  name, a title or a model name the client chose (the model is the registry's name it resolved to).
- No row for a 401, `/health` or `GET /v1/models`.
- In 2a the front reads counts on `/v1/chat/completions`, `/v1/completions` and `/v1/embeddings` only; every other
  route's row has `"from": "none"`.
- The cached count `c` is absent (null), never 0, when the engine didn't report it.
- The front never changes a request to get counts (no `stream_options`), and never refuses or slows a request because
  counting failed.
- One JSONL file per UTC month: `/var/lib/local-ai/usage/usage-YYYY-MM.jsonl` (`spark-front:spark-admin`, folder
  `2750`, files `0640`) and `/var/lib/local-ai/gate-ledger/ledger-YYYY-MM.jsonl` (`spark:spark-admin`, folder
  `2750`, files `0640`).
- The ledger holds no key names; it joins the front's rows on the request id.
- One request id per request, minted once, kept through the front's resends.
- pi keeps `SPARK_API_KEY`; OpenCode's key is `SPARK_OPENCODE_KEY`, Hermes's `SPARK_HERMES_KEY`; nothing is retired in
  2a.
- No live figure from the meter goes in the repo, the site or a commit; fixtures and examples are invented.
- Commits: Conventional Commits with 🤖 after the prefix, files staged by path, and the trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; never `--no-verify`.

## The row and the ledger row

These are the names every task uses. A field whose value is None is left out of the line.

```text
usage row:  v=1, id (16 hex), t0, t1 (UTC epoch s), key, route, model, role, engine,
            end (done|cut|gone|error|refused), status, why,
            p, c, o, from (usage|timings|none), ev_think, ev_text,
            wait_s, load_s, first_s, prompt_ms, gen_ms,
            stream, attempts, n, images, bytes_in, effort, resp_id, dropped
route:      chat | completions | embeddings | responses | messages | transcriptions
ledger row: v=1, t, kind (load|unload|refusal|brake_fired|brake_released|start|stop|counting_off|counting_on),
            model, id, why, code, wait_s, seconds, outcome
```

## Review Focus

1. **A multi-megabyte last event or body** (a long answer on `/v1/responses`, a non-streamed chat with a huge reply):
   the scanner's memory stays constant and the stream isn't slowed. Test in Task 44a.
2. **A full disk or a folder that can't be written:** every request is still served; rows are dropped and counted;
   the front still starts. Tests in Tasks 44b and 44d.
3. **A client that names a model that doesn't exist, or sends long strings:** the row holds no client text. Test in
   Task 44d.
4. **A stream cut or a client gone mid-answer:** the row is written with how it ended and whatever counts were seen,
   and `from` is `none` when none were. Test in Task 44d.
5. **`timings` sent twice, or `usage` and `timings` both:** the last copy wins, `usage` before `timings`, and nothing
   is added up. Test in Task 44a.

***

### Task 44a [Spark]: the usage scanner

**Files:**
- Create: `spark/src/spark/front/usage.py`
- Test: `spark/tests/test_front_usage.py`

**Interfaces:**
- Produces: `ROUTES_COUNTED = {"/v1/chat/completions": "chat", "/v1/completions": "completions", "/v1/embeddings":
  "embeddings"}`; `ROUTE_NAMES` mapping every `parse.INFERENCE_PATHS` path to its row name;
  `@dataclass Counts(p: int|None, c: int|None, o: int|None, source: str, ev_think: int, ev_text: int,
  prompt_ms: float|None, gen_ms: float|None, resp_id: str|None)`;
  `class UsageScan: __init__(self, path: str, *, streamed: bool)`, `feed(self, chunk: bytes) -> None`,
  `finish(self) -> Counts`; `scan_for(path: str, status: int, headers) -> UsageScan | None` (None for a route not in
  `ROUTES_COUNTED` or a status outside 2xx; `streamed` from `content-type: text/event-stream`); `safe_text(value) ->
  str | None` (the pattern above, else None).

- [ ] **Step 1: Write the failing tests**

```python
def sse(data: str) -> bytes: return f"data: {data}\n\n".encode()

def test_a_chat_stream_without_usage_is_counted_from_the_last_timings():
    s = UsageScan("/v1/chat/completions", streamed=True)
    s.feed(sse('{"id":"chatcmpl-ab12","choices":[{"delta":{"reasoning_content":"hm"}}]}'))
    s.feed(sse('{"choices":[{"delta":{"content":"ok"}}]}'))
    s.feed(sse('{"choices":[{"delta":{},"finish_reason":"stop"}],"timings":{"prompt_n":10,"cache_n":90,'
               '"predicted_n":5,"prompt_ms":12.5,"predicted_ms":40.0}}'))
    s.feed(b"data: [DONE]\n\n")
    c = s.finish()
    assert (c.p, c.c, c.o, c.source) == (100, 90, 5, "timings")
    assert (c.ev_think, c.ev_text, c.prompt_ms, c.gen_ms, c.resp_id) == (1, 1, 12.5, 40.0, "chatcmpl-ab12")

def test_usage_wins_over_timings_and_the_last_copy_of_each_wins():
    s = UsageScan("/v1/chat/completions", streamed=True)
    s.feed(sse('{"choices":[],"timings":{"prompt_n":1,"cache_n":1,"predicted_n":1}}'))
    s.feed(sse('{"choices":[],"usage":{"prompt_tokens":200,"completion_tokens":7,'
               '"prompt_tokens_details":{"cached_tokens":150}},"timings":{"prompt_n":50,"cache_n":150,"predicted_n":7}}'))
    c = s.finish()
    assert (c.p, c.c, c.o, c.source) == (200, 150, 7, "usage")

def test_no_cache_reported_is_none_not_zero():
    s = UsageScan("/v1/chat/completions", streamed=False)
    s.feed(b'{"choices":[{"message":{"content":"x"}}],"usage":{"prompt_tokens":30,"completion_tokens":4}}')
    assert (s.finish().p, s.finish().c) == (30, None)

def test_embeddings_count_prompt_tokens_only():
    s = UsageScan("/v1/embeddings", streamed=False)
    s.feed(b'{"data":[{"embedding":[0.1]}],"usage":{"prompt_tokens":12,"total_tokens":12}}')
    c = s.finish()
    assert (c.p, c.c, c.o, c.source) == (12, None, 0, "usage")

def test_a_line_split_across_chunks_is_read_whole():
    s = UsageScan("/v1/chat/completions", streamed=True)
    line = sse('{"choices":[],"timings":{"prompt_n":3,"cache_n":0,"predicted_n":2}}')
    for i in range(0, len(line), 7):
        s.feed(line[i:i + 7])
    assert s.finish().p == 3

def test_a_huge_line_keeps_constant_memory_and_still_finds_the_tail(monkeypatch):
    s = UsageScan("/v1/chat/completions", streamed=False)
    s.feed(b'{"choices":[{"message":{"content":"' + b"x" * (8 << 20) + b'"}}],')
    s.feed(b'"usage":{"prompt_tokens":5,"completion_tokens":6}}')
    assert s.held_bytes() <= 32 << 10
    assert (s.finish().p, s.finish().o) == (5, 6)

def test_nothing_found_is_source_none():
    s = UsageScan("/v1/chat/completions", streamed=True)
    s.feed(sse('{"choices":[{"delta":{"content":"o"}}]}'))
    c = s.finish()
    assert (c.p, c.c, c.o, c.source) == (None, None, None, "none")

def test_scan_for_counts_only_the_three_routes_and_2xx():
    stream = [(b"content-type", b"text/event-stream")]
    assert scan_for("/v1/chat/completions", 200, stream) is not None
    assert scan_for("/v1/responses", 200, stream) is None
    assert scan_for("/v1/chat/completions", 500, stream) is None

def test_safe_text_takes_only_the_pattern():
    assert safe_text("chatcmpl-ab12") == "chatcmpl-ab12"
    assert safe_text("a b") is None and safe_text("x" * 65) is None and safe_text(None) is None
```

- [ ] **Step 2: Run them to see them fail**

**On the Mac or the Spark**, in the clone:

```bash
make test ARGS="spark/tests/test_front_usage.py -q"
```

Expected: FAIL, `ModuleNotFoundError: No module named 'spark.front.usage'`.

- [ ] **Step 3: Implement `front/usage.py`**

Per data line of a stream (or per body when not streamed) keep a head of the first 512 bytes and a tail of the last
16 KiB, never the whole; at a line's end (or `finish()` for a body) take the **last** `"usage"` object and the
**last** `"timings"` object found in the tail with a regex for a JSON object of at most one nested level, and
`json.loads` only those; events count by a regex on the head (`"reasoning_content"\s*:\s*"` → `ev_think`,
`"content"\s*:\s*"` → `ev_text`); `resp_id` from the first data line's head (`"id"\s*:\s*"…"`, through `safe_text`).
Formulas: `usage` gives `p = prompt_tokens`, `o = completion_tokens`, `c = prompt_tokens_details.cached_tokens`, else
`timings.cache_n` if `timings` came, else None; with no `usage`, `timings` gives `p = prompt_n + cache_n`, `c =
cache_n`, `o = predicted_n`; embeddings give `o = 0`. `held_bytes()` returns the bytes the scanner holds now. Lines end
at CR, LF or CRLF, a CR's LF possibly in the next chunk, as `upstream.StreamEnd` reads them.

- [ ] **Step 4: Run the tests to see them pass**

```bash
make test ARGS="spark/tests/test_front_usage.py -q"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/front/usage.py spark/tests/test_front_usage.py
git commit -m "feat(spark): 🤖 the front's usage scanner: counts from a fixed head and tail, the last copy winning" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 44b [Spark]: the row file writer

**Files:**
- Create: `spark/src/spark/rowfile.py`
- Test: `spark/tests/test_rowfile.py`

**Interfaces:**
- Produces: `class RowFile: __init__(self, folder: Path, prefix: str, *, clock=time.time, queue_max: int = 10_000,
  mode: int = 0o640, retry_s: float = 30.0, on_off: Callable[[float, str], None] = …, on_on: Callable[[], None] = …)`;
  `put(self, row: dict, at: float) -> None` (never blocks, never raises); `off_since(self) -> float | None`;
  `close(self, timeout_s: float) -> None`. `at` picks the month: `{prefix}-{YYYY-MM}.jsonl` by UTC.

- [ ] **Step 1: Write the failing tests**

```python
def test_rows_append_one_line_each_to_the_months_file(tmp_path):
    rows = RowFile(tmp_path, "usage")
    rows.put({"v": 1, "p": 3}, at=1_790_000_000.0)          # 2026-09-21 UTC
    rows.put({"v": 1, "p": 4}, at=1_793_000_000.0)          # 2026-10-26 UTC
    rows.close(timeout_s=2)
    assert [json.loads(l)["p"] for l in (tmp_path / "usage-2026-09.jsonl").read_text().splitlines()] == [3]
    assert (tmp_path / "usage-2026-10.jsonl").stat().st_mode & 0o777 == 0o640

def test_a_missing_folder_is_counting_off_and_never_raises(tmp_path):
    offs = []
    rows = RowFile(tmp_path / "absent", "usage", on_off=lambda since, why: offs.append(why))
    rows.put({"v": 1}, at=1_793_000_000.0)
    rows.close(timeout_s=2)
    assert rows.off_since() is not None and len(offs) == 1

def test_a_failed_write_is_counted_and_the_next_row_carries_the_count(tmp_path, monkeypatch):
    rows = RowFile(tmp_path, "usage", retry_s=0)
    real, failed = os.write, []
    def flaky(fd, data):
        if not failed:
            failed.append(1)
            raise OSError(errno.ENOSPC, "full")
        return real(fd, data)
    monkeypatch.setattr(rowfile.os, "write", flaky)
    rows.put({"v": 1, "p": 1}, at=1_793_000_000.0)
    rows.put({"v": 1, "p": 2}, at=1_793_000_001.0)
    rows.close(timeout_s=2)
    lines = [json.loads(l) for l in (tmp_path / "usage-2026-10.jsonl").read_text().splitlines() if l]
    assert lines == [{"v": 1, "p": 2, "dropped": 1}]

def test_a_full_queue_drops_without_blocking(tmp_path):
    rows = RowFile(tmp_path, "usage", queue_max=1)
    rows.pause_for_test()                       # the writer holds before its next row
    for p in range(5):
        rows.put({"v": 1, "p": p}, at=1_793_000_000.0)
    rows.resume_for_test(); rows.close(timeout_s=2)
    written = (tmp_path / "usage-2026-10.jsonl").read_text().splitlines()
    assert sum(json.loads(l).get("dropped", 0) for l in written) + len(written) == 5

def test_close_returns_within_its_bound_with_a_stuck_write(tmp_path, monkeypatch):
    monkeypatch.setattr(rowfile.os, "write", lambda fd, data: time.sleep(10))
    rows = RowFile(tmp_path, "usage")
    rows.put({"v": 1}, at=1_793_000_000.0)
    started = time.monotonic(); rows.close(timeout_s=0.5)
    assert time.monotonic() - started < 2
```

- [ ] **Step 2: Run them to see them fail**

```bash
make test ARGS="spark/tests/test_rowfile.py -q"
```

Expected: FAIL, no module `spark.rowfile`.

- [ ] **Step 3: Implement `rowfile.py`**

A daemon thread reads a `queue.Queue(maxsize=queue_max)`; `put` uses `put_nowait` and counts a `queue.Full` as
dropped. Each row is `json.dumps(row, separators=(",", ":"))` plus `\n`, written as **one** `os.write` on a descriptor
opened `O_WRONLY|O_APPEND|O_CREAT|O_CLOEXEC` with `mode`, reopened when the month changes; a short write or an
`OSError` counts the row dropped, sets `off_since` (calling `on_off` once per spell, with the error's class name, never
its text), closes the descriptor, and makes the next write start with `\n`; no reopen before `retry_s` has passed. The
next row written carries `"dropped": n` (rows lost since the last one written) and `on_on` is called. `close` puts a
sentinel and joins the thread for at most `timeout_s`. `pause_for_test` / `resume_for_test` hold the thread on an
event, for the queue test only.

- [ ] **Step 4: Run the tests to see them pass**

```bash
make test ARGS="spark/tests/test_rowfile.py -q"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/rowfile.py spark/tests/test_rowfile.py
git commit -m "feat(spark): 🤖 a row file: JSONL rows appended from a thread of its own, never in a request's way" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 44c [Spark]: the gate's answer gains a load's seconds, and the model list its engine

**Files:**
- Modify: `spark/src/spark/gate/app.py` (the admit answer, ~line 460), `spark/src/spark/gateproto.py` (`Admitted`,
  `ModelEntry`), `spark/src/spark/front/gatelink.py` (`clear`), `spark/src/spark/front/parse.py`
  (`ModelList.from_registry`), and wherever the gate builds `ModelEntry`s
- Test: `spark/tests/test_gate_app.py`, `spark/tests/test_front_gatelink.py`, `spark/tests/test_front_parse.py`

**Interfaces:**
- Produces: the admit answer `{"ok": True, "model": m, "load_s": float | None}` (`load_s` is the internal
  `Admitted.seconds` when `loaded_now`, else None); `gateproto.Admitted` gains `load_s: NotRequired[float | None]`;
  `GateLink.clear(..., loaded: Callable[[float], None] | None = None)` calls `loaded(load_s)` when an admit answer it
  acts on carries a number; `ModelEntry` gains `engine: str` (the registry model's engine, such as `llama.cpp`).

- [ ] **Step 1: Write the failing tests**

```python
# test_gate_app.py
async def test_an_admit_that_waited_for_a_load_says_how_long_it_took(gate_rig):
    answer = await gate_rig.admit_after_load("qwen3.8-27b", load_seconds=41.5)
    assert answer == {"ok": True, "model": "qwen3.8-27b", "load_s": 41.5}

async def test_an_admit_for_a_loaded_model_has_no_load_seconds(gate_rig):
    assert (await gate_rig.admit_loaded("qwen3.8-27b"))["load_s"] is None

# test_front_gatelink.py
async def test_clear_reports_a_loads_seconds_to_its_caller(link_rig):
    seen = []
    await link_rig.link.clear("m", "k1", link_rig.deadline(), "ab" * 8, enter=lambda: "in",
                              admit=True, loaded=seen.append)
    assert seen == [12.0]                        # the stand-in gate answered load_s 12.0

# test_front_parse.py
def test_the_model_list_carries_each_models_engine(registry):
    assert {e["name"]: e["engine"] for e in parse.ModelList.from_registry(registry)}["qwen3.8-27b"] == "llama.cpp"
```

Use each test file's existing rigs and stand-ins (`gate_rig`, `link_rig` and `registry` name whatever those files
already provide); a stand-in gate's admit answer gains `"load_s"`.

- [ ] **Step 2: Run them to see them fail**

```bash
make test ARGS="spark/tests/test_gate_app.py spark/tests/test_front_gatelink.py spark/tests/test_front_parse.py -q"
```

Expected: FAIL on the four new tests.

- [ ] **Step 3: Implement**, as the Interfaces say. The front copies only the admit fields it knows
  (`gatelink._admit_answer`), so an old gate's answer without `load_s` is read as None.

- [ ] **Step 4: Run the whole front and gate suites**

```bash
make test ARGS="spark/tests/test_gate_app.py spark/tests/test_front_gatelink.py spark/tests/test_front_parse.py spark/tests/test_front_app.py spark/tests/test_front_forward.py -q"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/gate/app.py spark/src/spark/gateproto.py spark/src/spark/front/gatelink.py spark/src/spark/front/parse.py spark/tests/test_gate_app.py spark/tests/test_front_gatelink.py spark/tests/test_front_parse.py
git commit -m "feat(spark): 🤖 the gate's admit answer says how long a load took, and the model list names each engine" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 44d [Spark]: the front writes a row per request

**Files:**
- Create: `spark/src/spark/front/rows.py`
- Modify: `spark/src/spark/front/app.py` (`_Request`, `FrontApp.__init__`, `__call__`, `_cleared`, `_relay`,
  `_read`), `spark/src/spark/front/main.py`, `spark/src/spark/paths.py`
- Test: `spark/tests/test_front_rows.py`, `spark/tests/test_front_forward.py`

**Interfaces:**
- Consumes: `UsageScan`, `scan_for`, `safe_text`, `ROUTE_NAMES` (Task 44a); `RowFile` (Task 44b); `clear(...,
  loaded=…)` and `ModelEntry["engine"]` (Task 44c).
- Produces: `paths.USAGE_DIR = Path("/var/lib/local-ai/usage")`; `rows.usage_row(request, ended: float) -> dict`;
  `FrontApp(..., rows: RowFile | None = None)` (None writes nothing); `_Request.id: str` (16 hex, minted at creation,
  passed to every `clear`).

- [ ] **Step 1: Write the failing tests** (in `test_front_forward.py`'s style, with its `rig`, `sse`, `DONE`, `CODER`
  and a `Rows` stand-in that keeps what `put` was given)

```python
async def test_a_stream_leaves_one_row_with_its_counts(tmp_path):
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, [sse('{"id":"chatcmpl-1","choices":[{"delta":{"content":"o"}}]}'),
                              sse('{"choices":[{"delta":{},"finish_reason":"stop"}],'
                                  '"timings":{"prompt_n":4,"cache_n":6,"predicted_n":2}}'), DONE])
        await r.complete_stream()
        await until(lambda: len(r.rows) == 1)
        row = r.rows[0]
        assert {k: row[k] for k in ("v", "key", "route", "model", "end", "status", "p", "c", "o", "from")} == {
            "v": 1, "key": "dan-mac", "route": "chat", "model": CODER, "end": "done", "status": 200,
            "p": 10, "c": 6, "o": 2, "from": "timings"}
        assert len(row["id"]) == 16 and row["t1"] >= row["t0"] and row["attempts"] == 1

async def test_an_unknown_model_leaves_a_refused_row_without_the_clients_text(tmp_path):
    async with rig(tmp_path) as r:
        await r.post_json({"model": "my secret project name", "messages": []})
        await until(lambda: len(r.rows) == 1)
        assert r.rows[0]["end"] == "refused" and "model" not in r.rows[0]
        assert "secret" not in json.dumps(r.rows[0])

async def test_a_client_gone_mid_stream_leaves_a_gone_row(tmp_path):
    async with rig(tmp_path) as r:
        r.fake.stream(CODER, [sse('{"choices":[{"delta":{"content":"o"}}]}')] * 50, delay_s=0.05)
        await r.leave_mid_stream()
        await until(lambda: len(r.rows) == 1)
        assert r.rows[0]["end"] == "gone" and r.rows[0]["from"] == "none"

async def test_no_row_for_a_401_or_the_model_list(tmp_path):
    async with rig(tmp_path) as r:
        await r.get("/v1/models"); await r.post_json(chat(), key="not-a-key")
        await asyncio.sleep(0.05)
        assert r.rows == []

async def test_one_request_id_across_a_resend(tmp_path):
    async with rig(tmp_path, gate=Resending()) as r:     # the stand-in records each clear's request_id
        await r.post_json(chat(stream=False))
        assert len(set(r.gate.request_ids)) == 1 and r.rows[0]["attempts"] == 2

async def test_a_rows_writer_that_raises_never_touches_the_request(tmp_path):
    async with rig(tmp_path, rows=Exploding()) as r:     # put() raises
        response = await r.post_json(chat(stream=False))
        assert response.status_code == 200
```

`test_front_rows.py` covers `usage_row` alone: a role asked by name gives `role`; `effort` kept only when one of
`none|minimal|low|medium|high|xhigh|max`; `images` counts `image_url` parts; `n` only when above 1; `wait_s` is
forwarded-at less started; `load_s` from `loaded()`; `engine` from the entry; None fields absent.

- [ ] **Step 2: Run them to see them fail**

```bash
make test ARGS="spark/tests/test_front_rows.py spark/tests/test_front_forward.py -q"
```

Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**

`_Request` gains `id` (`secrets.token_hex(8)` by default factory), `attempts`, `load_s`, `first_at`, `scan`, `body`
(the parsed JSON dict, for `stream`, `n`, `images`, `effort`) and `bytes_in`; `_cleared` passes `request.id` and
`loaded=lambda s: setattr(request, "load_s", s)`; `_relay` counts `attempts`, sets `request.scan = scan_for(path,
response.status, response.headers)` and feeds it every chunk, setting `first_at` at the first; `__call__`'s `finally`,
after the journal line, calls `self.rows.put(usage_row(request, ended), at=request.started)` inside its own
`try/except Exception` that only logs where it broke, when `request.key` is set and the route isn't `MODEL_LIST`.
`usage_row` maps `outcome()` to `end` (`refused` for a refusal of the front's or the gate's, from `request.why` or a
4xx/5xx sent by `_refuse`), and every text through `safe_text` or its fixed set. `main.run` builds `RowFile(
paths.USAGE_DIR, "usage", on_off=…, on_on=…)` (each logging one line by `_log`, the error's class only), passes it, and
its `shutdown` calls `rows.close(timeout_s=ROWS_DRAIN_S)` with `ROWS_DRAIN_S = 5` after `app.aclose()`. Opening the
folder is the writer's, so no state of it can make `run` return 1.

- [ ] **Step 4: Run the front's suites**

```bash
make test ARGS="spark/tests/test_front_rows.py spark/tests/test_front_forward.py spark/tests/test_front_app.py -q"
```

Expected: PASS, the existing tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/front/rows.py spark/src/spark/front/app.py spark/src/spark/front/main.py spark/src/spark/paths.py spark/tests/test_front_rows.py spark/tests/test_front_forward.py
git commit -m "feat(spark): 🤖 the front writes a row of numbers per request, one id through its resends" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 44e [Spark]: the gate's ledger, and counting off reported through the front's snapshot

**Files:**
- Create: `spark/src/spark/gate/ledger.py`
- Modify: `spark/src/spark/paths.py`, `spark/src/spark/gateproto.py` (`InflightSnapshot`, `StatusView`'s health),
  `spark/src/spark/front/gatelink.py` (the snapshot), `spark/src/spark/gate/main.py`, `spark/src/spark/gate/admission.py`,
  `spark/src/spark/gate/room.py`, `spark/src/spark/gate/policy.py`, `spark/src/spark/gate/core.py`,
  `spark/src/spark/gate/app.py`
- Test: `spark/tests/test_gate_ledger.py`, and one new test each in `test_gate_admission.py` and `test_gate_room.py`

**Interfaces:**
- Consumes: `RowFile` and its `off_since()` (Task 44b).
- Produces: `paths.GATE_LEDGER_DIR = Path("/var/lib/local-ai/gate-ledger")`; `class Ledger: __init__(self, rows:
  RowFile | None, clock)`, with `load(model, request_id, wait_s, seconds, outcome)`, `unload(model, why)`,
  `refusal(code, model, request_id)`, `brake(kind)` (`brake_fired`|`brake_released`), `lifecycle(kind)`
  (`start`|`stop`), `counting(off_since: float | None)` (writes `counting_off` or `counting_on` on a change only);
  `InflightSnapshot` gains `counting_off_since: float` (optional); `StatusView`'s health gains `counting: {"off_since":
  float | None}`, on the control socket's view only; `GateLink(..., counting_off: Callable[[], float | None] = lambda:
  None)`, which `front/main.py` gives the front's `RowFile.off_since`.

- [ ] **Step 1: Write the failing tests**

```python
def test_each_event_is_one_row_with_no_key_name():
    rows = Collect()                                     # a RowFile stand-in keeping (row, at)
    ledger = Ledger(rows, clock=lambda: 1_793_000_000.0)
    ledger.load("qwen3.8-27b", "ab" * 8, wait_s=2.0, seconds=40.5, outcome="ready")
    ledger.refusal("model_not_found", None, "cd" * 8)
    ledger.unload("qwen3.8-27b", "idle")
    assert [r["kind"] for r, _ in rows.got] == ["load", "refusal", "unload"]
    assert all("key" not in r for r, _ in rows.got)

def test_counting_writes_only_on_a_change():
    rows = Collect(); ledger = Ledger(rows, clock=lambda: 1.0)
    for since in (None, 5.0, 5.0, None):
        ledger.counting(since)
    assert [r["kind"] for r, _ in rows.got] == ["counting_off", "counting_on"]

# test_gate_admission.py
async def test_a_load_and_a_refusal_reach_the_ledger(admission_rig):
    await admission_rig.load_for("qwen3.8-27b", request_id="ab" * 8)
    assert ("load", "ab" * 8) in [(r["kind"], r.get("id")) for r, _ in admission_rig.ledger_rows]

# test_gate_room.py
async def test_a_brake_release_reaches_the_ledger(room_rig):
    await room_rig.release_brake()
    assert "brake_released" in [r["kind"] for r, _ in room_rig.ledger_rows]
```

- [ ] **Step 2: Run them to see them fail**

```bash
make test ARGS="spark/tests/test_gate_ledger.py spark/tests/test_gate_admission.py spark/tests/test_gate_room.py -q"
```

Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**

`Ledger` builds the ledger row (None fields left out) and calls `rows.put(row, at=now)`; with `rows=None` it writes
nothing. Wire it once in `gate/main.py` (`RowFile(paths.GATE_LEDGER_DIR, "ledger", …)`, `lifecycle("start")` after
startup, `lifecycle("stop")` then `rows.close(timeout_s=5)` in shutdown) and hand it to the core. Call it where each
event already happens: a load's end where admission resolves its requesters (near `admission.py:1053`, `outcome
"ready"`) and where a load fails; each refusal where a `RefusalRecord` is appended (`admission.py:1257`, `core.py:1190`,
`app.py:550`), with the request id where that path has one; each unload where a drain starts with its why (`policy.py`,
`room.py:533`); `brake_released` where `room.py` records it (`:646`, `:800`) and `brake_fired` where the brake's
events are ingested (`notify.py`, `ingest_brake_events`). The front's `GateLink` puts `counting_off_since` into each
snapshot it posts; the gate's snapshot handler calls `ledger.counting(...)` and keeps the value for `StatusView`'s
control-socket health.

- [ ] **Step 4: Run the gate's and the front's suites**

```bash
make test ARGS="spark/tests/test_gate_ledger.py spark/tests/test_gate_admission.py spark/tests/test_gate_room.py spark/tests/test_gate_app.py spark/tests/test_gate_core.py spark/tests/test_front_gatelink.py -q"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/gate/ledger.py spark/src/spark/paths.py spark/src/spark/gateproto.py spark/src/spark/front/gatelink.py spark/src/spark/gate/main.py spark/src/spark/gate/admission.py spark/src/spark/gate/room.py spark/src/spark/gate/policy.py spark/src/spark/gate/core.py spark/src/spark/gate/app.py spark/src/spark/gate/notify.py spark/tests/test_gate_ledger.py spark/tests/test_gate_admission.py spark/tests/test_gate_room.py
git commit -m "feat(spark): 🤖 the gate's ledger: loads, unloads, refusals and the brake, joined to the front's rows by request id" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 44f [Spark]: the units and bootstrap's folders

**Files:**
- Modify: `stack/templates/local-ai-front.service`, `stack/templates/local-ai-gate.service`,
  `stack/templates/local-ai-llama-swap.service`, `stack/host/bootstrap.sh` (the *State* block, after the brake's folder)
- Test: `spark/tests/test_units.py`, `spark/tests/test_bootstrap.py`

**Interfaces:** the two folders and modes in *Global Constraints*.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_front_writes_only_its_usage_folder_with_a_private_umask(units):
    front = units["local-ai-front.service"]
    assert "ReadWritePaths=-/var/lib/local-ai/usage" in front and "UMask=0027" in front

def test_the_gate_may_write_its_ledger_folder_and_a_missing_one_never_fails_it(units):
    assert "-/var/lib/local-ai/gate-ledger" in _line(units["local-ai-gate.service"], "ReadWritePaths=")

def test_the_engines_cant_reach_the_records(units):
    line = _line(units["local-ai-llama-swap.service"], "InaccessiblePaths=")
    assert "-/var/lib/local-ai/usage" in line and "-/var/lib/local-ai/gate-ledger" in line

# test_bootstrap.py, in its dry-run style
def test_bootstrap_makes_the_meters_folders_readable_by_dan_alone(dry_run):
    assert "install -d -o spark-front -g spark-admin -m 2750 /var/lib/local-ai/usage" in dry_run
    assert "install -d -o spark -g spark-admin -m 2750 /var/lib/local-ai/gate-ledger" in dry_run
```

- [ ] **Step 2: Run them to see them fail**

```bash
make test ARGS="spark/tests/test_units.py spark/tests/test_bootstrap.py -q"
```

Expected: FAIL on the new tests.

- [ ] **Step 3: Implement.** Root makes both folders under root's `/var/lib/local-ai`, beside, never inside, `spark`'s
  own folders (a comment says why: `install -d` follows a link). `InaccessiblePaths=` on llama-swap's unit keeps its
  secrets entry.

- [ ] **Step 4: Run them to see them pass, and on Ubuntu 24.04**

**On the Mac**, then in a throwaway `ubuntu:24.04` container if Docker is there (the Phase 0 lesson):

```bash
make test ARGS="spark/tests/test_units.py spark/tests/test_bootstrap.py -q"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add stack/templates/local-ai-front.service stack/templates/local-ai-gate.service stack/templates/local-ai-llama-swap.service stack/host/bootstrap.sh spark/tests/test_units.py spark/tests/test_bootstrap.py
git commit -m "feat(stack): 🤖 the usage and ledger folders, Dan's to read, writable only by the front and the gate" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 44g [Spark]: doctor's checks, the privacy canary, and `spark status`

**Files:**
- Modify: `spark/src/spark/doctor.py`, `spark/src/spark/status.py`
- Test: `spark/tests/test_doctor.py`, `spark/tests/test_status.py`

**Interfaces:**
- Consumes: the folders (Task 44f); `StatusView` health's `counting` (Task 44e).
- Produces: doctor checks `usage records` and `gate ledger` (owner, group `spark-admin`, mode `2750` with setgid, no
  file in them readable by others) and `counting` (pass unless the control socket's health says `off_since`); the
  privacy canary also reads this month's files in both folders for its needle; `spark status` prints
  `counting off since <time>` when it is.

- [ ] **Step 1: Write the failing tests**

```python
def test_doctor_fails_a_usage_file_others_can_read(box):
    box.usage_folder(owner="spark-front", group="spark-admin", mode=0o2750)
    box.usage_file("usage-2026-10.jsonl", mode=0o644)
    assert box.doctor_check("usage records").failed

def test_doctor_passes_the_folders_as_bootstrap_makes_them(box):
    box.usage_folder(owner="spark-front", group="spark-admin", mode=0o2750)
    box.ledger_folder(owner="spark", group="spark-admin", mode=0o2750)
    assert box.doctor_check("usage records").passed and box.doctor_check("gate ledger").passed

def test_the_canarys_needle_must_not_be_in_the_records(box):
    box.usage_file("usage-2026-10.jsonl", text='{"v":1}\n' + box.needle)
    assert box.doctor_check("privacy canary").failed

def test_status_says_when_counting_is_off(status_rig):
    out = status_rig.print_with_health(counting={"off_since": 1_793_000_000.0})
    assert "counting off since" in out
```

Use `test_doctor.py`'s existing box stand-in, widened as the tests need.

- [ ] **Step 2: Run them to see them fail**

```bash
make test ARGS="spark/tests/test_doctor.py spark/tests/test_status.py -q"
```

Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**, in doctor's existing check style; each check prints a verdict only, never a file's
  content or a key name.

- [ ] **Step 4: Run them to see them pass**

```bash
make test ARGS="spark/tests/test_doctor.py spark/tests/test_status.py -q"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/doctor.py spark/src/spark/status.py spark/tests/test_doctor.py spark/tests/test_status.py
git commit -m "feat(spark): 🤖 doctor checks the meter's folders and that counting is on; status says when it isn't" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 44h [Spark]: OpenCode's and Hermes's own keys, and the caps

**Files:**
- Modify: `spark/src/spark/clients.py` (the `--key-env` defaults), `stack/models.yaml` (`key_groups.dan.max_waiting`),
  `stack/keys.example.yaml`, `website/how-to/opencode-hermes.md`, `website/how-to/secret-files.md`
- Test: `spark/tests/test_clients.py`

**Interfaces:** `spark clients opencode` and `spark clients hermes` default `--key-env` to `SPARK_OPENCODE_KEY` and
`SPARK_HERMES_KEY`; pi's stays `SPARK_API_KEY`. `key_groups.dan.max_waiting: 2` (four of Dan's keys together stay
below llama-swap's 10 per model).

- [ ] **Step 1: Write the failing tests**

```python
def test_each_harness_names_its_own_key(capsys):
    assert "{env:SPARK_OPENCODE_KEY}" in render_opencode_provider()
    assert render_hermes_provider()["key_env"] == "SPARK_HERMES_KEY"
    assert render_pi_provider()["apiKey"] == "${SPARK_API_KEY}"
```

Use `test_clients.py`'s existing helpers for each provider's render.

- [ ] **Step 2: Run it to see it fail**

```bash
make test ARGS="spark/tests/test_clients.py -q"
```

- [ ] **Step 3: Implement**; the runbooks gain Dan's steps for the two keys (made on the Mac, values by reference in
  `~/.secrets`, only digests to the Spark, a line each in the key list, one `make restart-front`), and the example key
  list two invented lines.

- [ ] **Step 4: Run it to see it pass, and render's tests for the cap**

```bash
make test ARGS="spark/tests/test_clients.py spark/tests/test_render.py -q"
```

- [ ] **Step 5: Commit**

```bash
git add spark/src/spark/clients.py stack/models.yaml stack/keys.example.yaml website/how-to/opencode-hermes.md website/how-to/secret-files.md spark/tests/test_clients.py
git commit -m "feat(clients): 🤖 OpenCode and Hermes get keys of their own; Dan's cap per key lowered to keep the group under llama-swap's" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 44i [Spark]: the by-hand SGLang reports its cache

**Files:** Modify `stack/host/by-hand-fast-coder` (its `sglang.launch_server` line); Test `spark/tests/test_by_hand.py`.

- [ ] **Step 1:** a test that the launch line holds `--enable-cache-report`. **Step 2:** see it fail. **Step 3:** add
  the flag. **Step 4:** see it pass. **Step 5:** commit
  `feat(stack): 🤖 the by-hand SGLang reports its cached tokens, so its rows aren't read as uncached`.

### Task 44j [Spark]: the scenario page and the Architecture page

**Files:** Create `website/scenarios/s26-what-it-cost.md` (the meter's scenario, tied to doctor's three checks);
Modify `website/scenarios/index.qmd`, `website/architecture.qmd` (the front's records and the gate's ledger, dashed
until Task 44l deploys them; its scenario table), and `_overview.qmd` only if it shows the front's outputs.

- [ ] **Step 1:** write the scenario in the pages' existing shape (*What happens*, *What I see*, *How to check*, the
  doctor checks by name). **Step 2:** draw the two stores dashed. **Step 3:** `make docs` on the Mac renders the site
  with no errors. **Step 4:** commit `docs(website): 🤖 the usage meter's scenario and its parts, dashed until
  deployed`.

### Task 44k [Spark]: the plan, Phase 2a's plan and the rules

**Files:** Modify `website/design/plan.md` (a Revisions line in its `- **YYYY-MM-DD** — …` form; Phase 2a's line
gaining the counter; Phase 2b's gaining the rest; the Requirements row "always see what model is being used", the
SwiftBar plugin's row, the menu bar line, 2b's item and done-when each annotated with the dot-only title; Phase 3's,
4a's, 5's, 2c's and 6's lines as the design's §8 lists, Phase 6's "nothing anyone sends is logged" corrected),
`website/design/phase-2a.md` (a pointer to this plan for Tasks 44a–44l; Task 45's peak memory with the scanner, no
rows dropped, rows checked against pi's own counts; Task 49's `gone` rows and an unwritable folder never stopping the
front; Task 50's `cut` and refusal rows; both done-when lists; Task 51's file lists; Task 52's `make clients` check),
`CLAUDE.md` and README §Conventions (key names never reused; no live figure from the meter in the repo).

- [ ] **Step 1:** make each edit, correcting rather than deleting, each dated. **Step 2:** `make docs` renders.
  **Step 3:** commit `docs(plan): 🤖 the usage meter's counter joins Phase 2a as Tasks 44a–44l; the rest opens 2b`.

### Task 44l [Spark] [Dan]: deploy, snapshot the Spark's sources, record

Box work, for the Spark session and Dan, after Tasks 44a–44k are merged into `phase-2a` with Dan's OK.

- [ ] **Step 1 [Dan]:** the two keys, by the runbook Task 44h wrote.
- [ ] **Step 2 [Dan], on the Spark:** `make bootstrap`, for the two folders (it asks for sudo once).
- [ ] **Step 3 [Spark], on the Spark:** `make apply` (it waits for a quiet minute), then `make restart-front` for the
  keys; `make doctor` passes the three new checks.
- [ ] **Step 4 [Spark], on the Spark:** one request from pi on the Mac, then, as Dan, the newest row's field names
  only: `tail -1 /var/lib/local-ai/usage/usage-$(date -u +%Y-%m).jsonl | python3 -c 'import json,sys;
  print(sorted(json.load(sys.stdin)))'`. Expected: the row's names, with `p`, `o` and `from`.
- [ ] **Step 5 [Dan], on the Spark:** snapshots, into a private `0700` folder: Open WebUI's database through its
  container, and `agent`'s pi sessions as `agent`, by the design's §7.
- [ ] **Step 6 [Spark]:** `changelog.md`, README §Current state, the Architecture page's two stores solid, the vault's
  note (Dan): commit `docs(machine): 🤖 the usage meter's counter is running`.
