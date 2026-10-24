---
title: "The usage meter's counter — implementation plan"
description: "Phase 2a's part of the usage meter: the front writes a row per request, the gate a ledger of its events, in folders only Dan reads; keys for OpenCode and Hermes; doctor's checks, the units, bootstrap, the docs. Tasks 44a onward, run after Task 44 and before Task 45. Revised after its council."
date: 2026-10-23
---

# The usage meter's counter — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** from Phase 2a's soak on, every request through the front leaves one row of numbers in a folder only Dan
reads, and the gate leaves a row for each load, unload, refusal, brake event and start or stop, so nothing the meter
will price is lost before the rest of it (Phase 2b) is built.

**Architecture:** a bounded scanner in the front reads each response's counts from a fixed head and tail
(`front/usage.py`); a shared writer appends JSONL rows from a thread of its own (`rowfile.py`); the front builds one row
per request when it ends (`front/rows.py`); the gate's admission answer carries a load's seconds, and a gate ledger
(`gate/ledger.py`) writes its events; systemd units, bootstrap and doctor give the two folders their owners, modes and
checks.

**Tech Stack:** Python (the `spark` uv project), pytest with anyio, raw ASGI on uvicorn, systemd units rendered from
`stack/templates/`, bash for bootstrap.

**Spec:** [the usage meter's design](usage-meter.md), with [its questions and answers](usage-meter-qa.md). This plan
builds its §1, §2, §6 (2a's half) and the 2a parts of §8 and §9.

**Where it runs.** Each task's label says where its work belongs by the repo's rule: **[Spark]** for code, tests and
docs, **[Dan]** for keys and sudo. *(Dan's instruction, 2026-10-23: this round writes and tests Tasks 44a–44j on the
Mac, on branch `chendaniely/harness-status-bar`, cut from `phase-2a`. The Mac skips the Linux-only tests, so a full
`make test` on the Spark comes before the merge. Task 44k is made on `phase-2a` by the Spark session after 44a–44j
merge into it, so its plan edits don't conflict with Tasks 40–44's; Task 44l, the deploy, and every [Dan] step stay for
the Spark session and Dan. 44l finishes before Task 45 starts: nothing deploys during the soak.)* Tests run with
`make test ARGS="…"`, which is `uv run --frozen --project spark pytest …`.

**The test listings** are specifications: each names the test, the rig it uses and its assertions. The task writes
them against the existing rigs it names (`test_front_forward.py`'s `rig` and `Rig`, `test_gate_admission.py`'s `Gate`,
`test_gate_room.py`'s `World`, `test_doctor.py`'s `FakeProbe`, `test_status.py`'s `text(view)`, `test_units.py`'s
`settings` and `words`, `test_bootstrap.py`'s `dry_run()` and `install_d_line`), widening a rig where the test needs
it, and sees each new test fail for the reason given before making it pass (the Phase 0 lessons).

## Global Constraints

- Nothing the meter stores holds a prompt, an answer, thinking text, a file name, a title, or any string a client
  chose. Text in a row is from a fixed set, a registry name, or `[A-Za-z0-9._:-]{1,64}` from the engine; a model is
  the registry's name it resolved to, never the client's.
- No row for a 401, `/health` or `GET /v1/models`.
- In 2a the front reads counts on `/v1/chat/completions`, `/v1/completions` and `/v1/embeddings` only; every other
  route's row has `"from": "none"`.
- The cached count `c` is absent (null), never 0, when the engine didn't report it.
- The front never changes a request to get counts (no `stream_options`). No failure of counting, and no state of either
  folder, refuses or slows a request, or keeps the front or the gate from starting.
- One JSONL file per UTC month: `/var/lib/local-ai/usage/usage-YYYY-MM.jsonl` (`spark-front:spark-admin`, folder
  `2750`, files `0640`) and `/var/lib/local-ai/gate-ledger/ledger-YYYY-MM.jsonl` (`spark:spark-admin`, folder `2750`,
  files `0640`). `SPARK_USAGE_DIR` and `SPARK_GATE_LEDGER` override the folders, as `paths.py`'s other entries are
  overridden, so no test writes the live folders.
- The ledger holds no key names; it joins the front's rows on the request id.
- One request id per request, minted once, kept through the front's resends.
- Durations come from a monotonic clock; `t0`, `t1` and `t` are UTC epoch seconds.
- pi keeps `SPARK_API_KEY`; OpenCode's key is `SPARK_OPENCODE_KEY`, Hermes's `SPARK_HERMES_KEY`; nothing is retired in
  2a; the caps stay as they are (`max_waiting: 8`), a per-group cap waits for 2b.
- Every new field between the front and the gate is optional both ways: an old peer's message without it, or with a
  bad value, is read as "not reported", never refused.
- No live figure from the meter goes in the repo, the site or a commit; fixtures and examples are invented; a check
  against real rows prints a verdict only.
- Commits: Conventional Commits with 🤖 after the prefix, files staged by path, and the trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; never `--no-verify`.

## The row and the ledger row

These are the names every task uses. A field whose value is None is left out of the line.

```text
usage row:  v=1, id (16 hex), t0, t1, key, route, model, role, engine, asked_len,
            end (done|cut|gone|error|refused), status, code,
            p, c, o, from (usage|timings|none), ev_think, ev_text,
            wait_s, load_s, first_s, prompt_ms, gen_ms,
            stream, attempts, n, images, bytes_in, effort, resp_id, dropped, refused_n
route:      chat | completions | embeddings | responses | messages | transcriptions
end:        done (whole); cut; gone; error (the engine's error event, or a non-2xx answer from upstream);
            refused (a refusal of the front's or the gate's, its code in `code`)
ledger row: v=1, t, kind (load|unload|refusal|brake_fired|brake_released|start|stop|counting_off|counting_on),
            who (front|gate, for start, stop and counting), model, id, why, code, wait_s, seconds, outcome
```

`load_s` is on the row of the request that started the load only; the others that waited on it carry their `wait_s`.
`refused_n` sums the refused rows a key's per-minute limit folded into this one (Task 44b).

## Review Focus

1. **Embeddings and other bodies whose usage isn't at the end:** llama-server writes an embeddings answer's `usage`
   before its vectors, so the scanner reads the head as well as the tail. Test in Task 44a.
2. **A cut stream's unclosed last object, or a multi-megabyte line:** the scanner's regex runs in linear time and its
   memory stays constant, so the event loop never stalls into the watchdog. Tests in Task 44a.
3. **A scanner or writer that raises, a full disk, a folder that can't be written:** every request is still served
   byte for byte, rows are dropped and counted, the front and the gate still start. Tests in Tasks 44b, 44d and 44e.
4. **A client string that passes the text pattern** (`secretproj-q3` as a model, an effort or a route): no row and no
   ledger row holds it. Tests in Tasks 44d and 44e.
5. **A mixed deploy** (a new front with an old gate, or the reverse) and Task 38's rollback: every new field is read
   leniently, so the front's snapshots, admits and model lists keep working. Tests in Tasks 44c and 44e.

***

### Task 44a [Spark]: the usage scanner

**Files:**
- Create: `spark/src/spark/front/usage.py`
- Test: `spark/tests/test_front_usage.py`

**Interfaces:**
- Produces: `ROUTE_NAMES` (each of `parse.INFERENCE_PATHS` to its row name); `ROUTES_COUNTED` (the three counted
  paths); `@dataclass(frozen=True) Counts(p, c, o, source, ev_think, ev_text, prompt_ms, gen_ms, resp_id)` (ints or
  None; `source` one of `usage|timings|none`); `class UsageScan(path: str, *, streamed: bool)` with `feed(chunk:
  bytes) -> None`, `finish() -> Counts` (idempotent), `held_bytes() -> int`; `scan_for(path, status, headers) ->
  UsageScan | None` (None outside `ROUTES_COUNTED` or 2xx; `streamed` from the media type read as
  `upstream.StreamEnd.watching` reads it: split on `;`, lowercased, identity encoding only); `safe_text(value) -> str
  | None`; `HEAD = 4096`, `TAIL = 16384`.

**How it reads.** Per data line of a stream, or per body when not streamed, it keeps a head of the first `HEAD` bytes
and a tail of the last `TAIL`, never more. At a line's end (or at `finish()` for a body) it looks for the **last**
`"usage"` and the **last** `"timings"` object in the tail, and in the head when the tail has none (embeddings), each
matched by exactly `\{(?:[^{}]|\{[^{}]*\})*\}` after its key (linear time; no other pattern), and parses only that
object with `json.loads`. A value that isn't a non-negative int below 2**53 is not reported. `usage` gives `p =
prompt_tokens`, `o = completion_tokens`, and `c = prompt_tokens_details.cached_tokens`, else `timings.cache_n` when
`timings` came, else None; with no `usage`, `timings` gives `p = prompt_n + cache_n`, `c = cache_n`, `o = predicted_n`;
embeddings give `o = 0`. Events count by regex on each data line's head: `"reasoning_content"\s*:\s*"` gives
`ev_think`, `"content"\s*:\s*"` gives `ev_text` (a `null` is no event). `resp_id` is the top-level `"id"` of the first
data line of a stream, or of a body's tail outside any `"tool_calls"`, through `safe_text`. Lines end at CR, LF or
CRLF, a CR's LF possibly in the next chunk; a `:` line is a ping; a line past `HEAD + TAIL` keeps only those two parts.

- [ ] **Step 1: Write the failing tests**, with fixtures in llama-server b11146's real shape: chunk fields in the order
  `choices, created, id, model, system_fingerprint, object[, usage], timings`; the first delta
  `{"role":"assistant","content":null}`; `timings` keys `cache_n, prompt_n, prompt_ms, prompt_per_token_ms,
  prompt_per_second, predicted_n, predicted_ms, …`; a non-streamed body ending `…,"usage":{…},"id":"chatcmpl-…",
  "timings":{…}}`; an embeddings body `{"model":…,"object":"list","usage":{…},"data":[…]}`.

  - `test_a_chat_stream_without_usage_is_counted_from_the_last_timings`: (p, c, o, source) = (100, 90, 5, "timings"),
    ev_think 1, ev_text 1, prompt_ms and gen_ms from timings, resp_id from the first chunk.
  - `test_timings_repeated_at_a_stop_word_count_once`: predicted_n 4 then 5 gives o == 5.
  - `test_usage_wins_over_timings_and_the_last_copy_of_each_wins`: (200, 150, 7, "usage").
  - `test_one_feed_holding_the_finish_the_usage_and_done`: the counts from the usage chunk.
  - `test_a_completions_stream_is_counted`: usage on its final chunk.
  - `test_no_cache_reported_is_none_not_zero`, and `"prompt_tokens_details":null` keeps p and o with c None.
  - `test_embeddings_usage_sits_before_the_vectors`: one 1024-float vector and eight; (12, None, 0, "usage").
  - `test_a_line_split_across_chunks_and_a_cr_lf_split_are_read_whole`, with a ping before the first data line.
  - `test_a_non_streamed_resp_id_is_the_top_level_id_never_a_tool_calls`: a `"id":"call_report-q3.md"` in the head is
    not taken.
  - `test_an_unclosed_usage_or_timings_object_is_dropped_in_linear_time`: n in (30, 16_000), streamed and not, CPU time
    by `time.thread_time()` under 0.05 s, source "none".
  - `test_a_multi_megabyte_line_costs_constant_memory_and_little_cpu`: 128 chunks of 64 KiB then the usage, under
    `tracemalloc` peak below 256 KiB above one chunk, under 0.25 s of thread time, the counts found.
  - `test_bad_numbers_are_not_reported`: `true`, `-1`, `1.5`, `2**53`.
  - `test_a_quoted_usage_inside_content_is_not_counted` and `test_multibyte_and_invalid_bytes_at_the_edges`.
  - `test_nothing_found_is_source_none`, `test_scan_for_counts_only_the_three_routes_and_2xx`,
    `test_safe_text_takes_only_the_pattern`, `test_finish_is_idempotent`.

- [ ] **Step 2: Run them to see them fail.** **On the Mac or the Spark**, in the clone:

```bash
make test ARGS="spark/tests/test_front_usage.py -q"
```

Expected: FAIL, `ModuleNotFoundError: No module named 'spark.front.usage'`.

- [ ] **Step 3: Implement `front/usage.py`** as *How it reads* says.
- [ ] **Step 4: Run the tests to see them pass** (the same command). Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark): 🤖 the front's usage scanner: counts from a fixed head and tail, in linear time`.

### Task 44b [Spark]: the row file writer

**Files:**
- Create: `spark/src/spark/rowfile.py`
- Test: `spark/tests/test_rowfile.py`

**Interfaces:**
- Produces: `class RowFile(folder: Path, prefix: str, *, clock=time.time, monotonic=time.monotonic, write=os.write,
  statvfs=os.statvfs, queue_bytes: int = 4 << 20, mode: int = 0o640, retry_s: float = 30.0, free_floor: int = 20 <<
  30, on_off: Callable[[float, str], None] = lambda since, why: None, on_on: Callable[[], None] = lambda: None)`;
  `put(row: dict, at: float) -> None` (never blocks, never raises, also after `close`); `off_since() -> float | None`;
  `close(timeout_s: float) -> None`; `class RefusedLimit(per_minute: int = 60)` with `allow(key: str, now: float) ->
  bool` and `folded(key: str) -> int` (the count it held back since the key's last allowed row, then reset).

**How it writes.** `put` serializes the row at once (`json.dumps(row, separators=(",", ":"), allow_nan=False)`, inside
`try/except Exception`: a row that won't serialize is dropped and counted) and queues the bytes, bounded by
`queue_bytes` in total, dropping and counting past it. One daemon thread writes each line as **one** `write()` on a
descriptor opened `O_WRONLY|O_APPEND|O_CREAT|O_CLOEXEC`, then `os.fchmod(fd, mode)` when it created the file, so the
mode never depends on the umask; it reopens when the UTC month changes (`{prefix}-{YYYY-MM}.jsonl` by `at`). A short
write or any `Exception` drops the row, sets `off_since` (calling `on_off` once per spell, with the error's class name,
never its text), closes the descriptor, and makes the next write start with `\n`; it doesn't reopen before `retry_s`
has passed, by `monotonic`. At each reopen, and every 1,000 rows, `statvfs` below `free_floor` turns counting off with
why `disk`. The next line written carries `"dropped": n` and calls `on_on`. The thread tries the month's file once at
start, so a folder it can't write shows as off at once. `on_off` and `on_on` only log. `close` sets a stop event,
doesn't block on a full queue, and joins the thread for at most `timeout_s`.

- [ ] **Step 1: Write the failing tests**, with the `write`, `statvfs` and clocks injected, never patching `os`:
  - `test_rows_append_one_line_each_to_the_months_file_by_utc`: TZ set to `America/Vancouver` (with `time.tzset()`), a
    row at 2026-10-01T03:00Z lands in `usage-2026-10.jsonl`.
  - `test_the_files_mode_is_0640_under_any_umask`: umask 0o000, 0o022 and 0o077.
  - `test_a_missing_folder_is_counting_off_at_start_and_never_raises`, then the folder made, `on_on` once, the next row
    carrying `dropped`, `on_off` called once with `FileNotFoundError` only, no reopen before `retry_s`.
  - `test_a_short_write_leaves_one_torn_line_and_the_next_row_whole`: the fake writes 7 bytes; the raw lines are
    `['{"v":1,', '{"v":1,"p":2,"dropped":1}']`.
  - `test_a_row_that_wont_serialize_is_dropped_and_the_writer_lives`: bytes and NaN, then `{"p":3}`, giving
    `[{"v":1,"p":3,"dropped":2}]`, the thread alive.
  - `test_a_full_queue_drops_without_blocking`: a write blocked on an event; five puts from a thread joined within 1 s;
    rows written plus `dropped` equal five.
  - `test_below_the_free_floor_counting_turns_off_with_why_disk`.
  - `test_close_returns_within_its_bound_with_a_stuck_write`: the stuck write waits on an event the test sets in
    teardown, and the thread is joined.
  - `test_put_after_close_never_raises`, and `test_refused_limit_folds_past_its_rate`.
- [ ] **Step 2: Run them to see them fail.** `make test ARGS="spark/tests/test_rowfile.py -q"`. Expected: FAIL, no
  module `spark.rowfile`.
- [ ] **Step 3: Implement `rowfile.py`** as *How it writes* says.
- [ ] **Step 4: Run them to see them pass.** Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark): 🤖 a row file: JSONL rows from a thread of its own, never in a request's way`.

### Task 44c [Spark]: the admit answer gains a load's seconds, the model list each model's engine

**Files:**
- Modify: `spark/src/spark/gate/app.py` (the admit answer, ~line 460), `spark/src/spark/gate/admission.py` (whose
  request started a load), `spark/src/spark/gate/core.py` (`_model_list`, ~line 1218), `spark/src/spark/gateproto.py`
  (`Admitted`, `ModelEntry`), `spark/src/spark/front/gatelink.py` (`clear`, `_admit_answer`), `spark/src/spark/front/parse.py`
  (`_entry`, `ModelList.from_registry`), `spark/tests/gate_standin.py` (its default admit answer gains
  `"load_s": None`)
- Test: `spark/tests/test_gate_app.py`, `test_front_gatelink.py`, `test_front_parse.py`, `test_gate_core.py`; update
  the exact-shape tests `test_front_parse.py:483`, `test_gate_core.py:1494-1499` and the Linux-only
  `test_gate_app.py:615`.

**Interfaces:**
- Produces: the admit answer `{"ok": True, "model": m, "load_s": float | None}`, a number only for the request that
  started the load (`admission.py`'s starter, not every requester in `_requesters(load)`); `gateproto.Admitted` gains
  `load_s: NotRequired[float | None]`; `ModelEntry` gains `engine: NotRequired[str]`; `parse._entry` copies `engine`
  when it is a non-empty str, never requires it; `_admit_answer` reads `load_s` as None unless a finite number ≥ 0;
  `GateLink.clear(..., loaded: Callable[[float], None] | None = None)` calls `loaded(load_s)` for a number.
- [ ] **Step 1: Write the failing tests:**
  - `test_gate_app.py`: `test_the_request_that_started_a_load_hears_how_long_it_took` (the `Gate(tmp_path, …)` rig,
    `gate.swap.hold_loads = True`, `gate.clock.now += 41.5` between the ticket and `gate.swap.ready(CODER)`; the
    starter's answer `load_s == 41.5`, a second request that waited on the same load `load_s is None`), and
    `test_an_admit_for_a_loaded_model_has_no_load_seconds`.
  - `test_front_gatelink.py`: `test_clear_reports_a_loads_seconds_to_its_caller`, and
    `test_an_old_gates_answer_or_a_bad_load_s_is_none` (absent, `True`, `"12"`, NaN, −1).
  - `test_front_parse.py`: `test_the_model_list_carries_each_models_engine`, and
    `test_an_entry_without_engine_is_still_listed`; `test_gate_core.py`: the hello's models carry `engine`.
- [ ] **Step 2: Run them to see them fail.** `make test ARGS="spark/tests/test_gate_app.py spark/tests/test_front_gatelink.py spark/tests/test_front_parse.py spark/tests/test_gate_core.py -q"`.
  Expected: FAIL on the new assertions (`KeyError: 'load_s'`, `'engine'`).
- [ ] **Step 3: Implement** as the Interfaces say.
- [ ] **Step 4: Run them and the front's suites:** add `spark/tests/test_front_app.py spark/tests/test_front_forward.py`
  to the command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark): 🤖 the admit answer says how long the load it started took; the model list names each engine`.

### Task 44d [Spark]: the front writes a row per request

**Files:**
- Create: `spark/src/spark/front/rows.py`
- Modify: `spark/src/spark/front/app.py` (`_Request`, `FrontApp.__init__`, `__call__`, `_cleared`, `_relay`, `_read`,
  `_refuse`), `spark/src/spark/front/main.py`, `spark/src/spark/front/__init__.py` (`FRONT_MODULES` gains
  `spark.front.usage`, `spark.front.rows` and `spark.rowfile`, and its docstring lists them), `spark/src/spark/paths.py`
  (`USAGE_DIR`, overridden by `SPARK_USAGE_DIR`)
- Test: `spark/tests/test_front_rows.py`, `spark/tests/test_front_forward.py`, `spark/tests/test_front_app.py` (each
  `clear` stand-in, at `test_front_app.py:109` and `:713` and `test_front_forward.py:106` and `:874`, gains
  `loaded=None`)

**Interfaces:**
- Consumes: Tasks 44a, 44b, 44c.
- Produces: `rows.usage_row(request, ended: float, monotonic_ended: float, roles: frozenset[str]) -> dict`;
  `FrontApp(..., rows: RowFile | None = None, refused_limit: RefusedLimit | None = None)` (None writes nothing);
  `_Request` gains `id` (`secrets.token_hex(8)` by default), `mono_started`, `mono_forwarded`, `mono_first`, `attempts`,
  `load_s`, `scan`, `refused` (a code), and the scalars `stream`, `n`, `images`, `effort`, `bytes_in`, set in `_read`
  from the parsed body, which isn't kept.

**How it fits.** `_cleared` passes `request.id` and `loaded=` (setting `request.load_s`). `_relay` counts `attempts`,
and each of its calls into the scanner (`scan_for`, `feed`, `finish`) is wrapped in `try/except Exception` that sets
`request.scan = None` and logs the class once, so a scanner fault can never touch the stream. `_refuse` sets
`request.refused = refused.code`. `__call__`'s `finally`, after the journal line, when `request.key` is set and the
route isn't `MODEL_LIST`, builds the row and puts it, inside its own `try/except Exception` that logs where it broke and
counts the row dropped; a refused row goes through `refused_limit` first, a held-back one folded into the key's next
row's `refused_n`. `usage_row` keeps `role` only when `request.asked` is one of the resolved entry's roles, `effort`
only from `none|minimal|low|medium|high|xhigh|max`, `asked_len` only for an unresolved model (its length), `engine`
from the entry when present, and every other text through `safe_text`; `end` follows *The row* above, an upstream
non-2xx being `error`. `main.run` builds the `RowFile(paths.USAGE_DIR, "usage", …)` before the `GateLink`, passes
`rows.off_since` to it (Task 44e), and its `shutdown` calls `await asyncio.to_thread(rows.close, ROWS_DRAIN_S)` with
`ROWS_DRAIN_S = 5` after `app.aclose()`. Rows still in flight when uvicorn's graceful time ends are cut: their rows are
put before `close`.

- [ ] **Step 1: Write the failing tests**, in `test_front_forward.py`'s `rig`, which gains `rows` (a stand-in whose
  `put` keeps each row) and `gate`; each test waits for its rows with `until(...)`:
  - `test_a_stream_leaves_one_row_with_its_counts`: (v, key, route, model, end, status, p, c, o, from) as streamed,
    `id` 16 hex, `t1 ≥ t0`, `attempts == 1`.
  - `test_each_ending_leaves_its_row`, parametrized: a cut after a chunk with timings (`cut`, `timings`, p set), the
    engine's error event (`error`), the gate's refusal (`refused`, `code`), the front's 429 (`refused`), and a
    non-streamed JSON answer through the fake's canned `application/json` (`done`, `usage`, `resp_id`).
  - `test_no_client_string_reaches_a_row_even_one_that_passes_the_pattern`: `secretproj-q3` as a model (404),
    `reasoning_effort: "secret-effort"` on a known model, a POST to `/v1/secret-route`; `"secret"` not in the rows,
    `asked_len` set for the 404.
  - `test_no_row_for_a_401_health_or_the_model_list`: those three, then one valid request; the only row is its.
  - `test_one_request_id_across_a_resend` (in `test_front_gatelink.py`, with its `r.swap.exited = 1` pattern): the row's
    `id` is the admit's `request_id`, `attempts == 2`, `load_s` kept; two requests, two ids.
  - `test_a_scanner_that_raises_never_cuts_the_stream`: `feed` raising on its second call, then `scan_for`, then
    `finish`: the client gets every byte, the journal line reads 200, the row has `from: "none"`.
  - `test_a_rows_writer_that_raises_never_touches_the_request`: the journal line is written, one error line names only
    the class, a second request is served.
  - `test_refused_rows_past_the_limit_are_folded_into_refused_n`.
  - `test_front_rows.py`: `usage_row` on its own for `role`, `effort`, `images`, `n` above 1 only, `wait_s` and
    `first_s` from the monotonic marks, `engine`, None fields absent; and after `_read`, no `_Request` attribute is a
    dict or a list.
  - A Linux-only subprocess test beside `test_spark_front_takes_9100_from_systemd`: with `SPARK_USAGE_DIR` missing, a
    plain file, or `0o500`, `spark front` serves `/health` and a chat; and a stream held through SIGTERM leaves its row
    with `end: "cut"`.
- [ ] **Step 2: Run them to see them fail.** `make test ARGS="spark/tests/test_front_rows.py spark/tests/test_front_forward.py spark/tests/test_front_gatelink.py -q"`.
  Expected: FAIL on the new tests (no rows put).
- [ ] **Step 3: Implement** as *How it fits* says.
- [ ] **Step 4: Run the front's suites** (add `spark/tests/test_front_app.py spark/tests/test_front_parse.py`).
  Expected: PASS, the existing tests changed only by the stand-ins' `loaded=None`.
- [ ] **Step 5: Commit** `feat(spark): 🤖 the front writes a row of numbers per request, one id through its resends`.

### Task 44e [Spark]: the gate's ledger, and counting reported through the front's snapshot

**Files:**
- Create: `spark/src/spark/gate/ledger.py`
- Modify: `spark/src/spark/paths.py` (`GATE_LEDGER_DIR`, overridden by `SPARK_GATE_LEDGER`), `spark/src/spark/gateproto.py`
  (`InflightSnapshot` gains `counting_off_since` and `front_started_at`, both optional; `HealthView` gains `counting`
  as `NotRequired`), `spark/src/spark/front/gatelink.py` (the snapshot; `GateLink(..., counting_off=…)`),
  `spark/src/spark/front/main.py`, `spark/src/spark/gate/main.py`, `spark/src/spark/gate/admission.py`,
  `spark/src/spark/gate/policy.py`, `spark/src/spark/gate/room.py`, `spark/src/spark/gate/core.py`,
  `spark/src/spark/gate/app.py`
- Test: `spark/tests/test_gate_ledger.py`; one each in `test_gate_admission.py`, `test_gate_room.py`,
  `test_gate_core.py`, `test_gate_app.py`, `test_front_gatelink.py`; update `test_gate_app.py:468-479` (the status
  socket's health keys are `HealthView`'s less `counting`) and `test_gateclient.py:751`.

**Interfaces:**
- Consumes: `RowFile` and `off_since()` (Task 44b).
- Produces: `class Ledger(rows: RowFile | None, clock, models: Callable[[], frozenset[str]])` with `load(model,
  request_id, wait_s, seconds, outcome)`, `unload(model, why)`, `refusal(code, model, request_id)`, `brake(kind)`,
  `lifecycle(kind, who)`, `counting(off_since, who)`, `off_since()`; each method never raises (it logs the class), keeps
  `model` only when it is a registry name, `id` only when 16 hex, `why` only from `gateproto.DRAIN_WHY` plus `brake`,
  `apply` and `gone`, `code` only from the refusal codes; `counting` writes only on a change. `GateLink(...,
  counting_off: Callable[[], float | None] = lambda: None)` puts `counting_off_since` in a snapshot only when it is a
  number, and `front_started_at` always; the gate's `_snapshot` reads both leniently (absent, null or a bad value is
  "not reported", never a 400). `HealthView["counting"] = {"front_off_since": float | None, "ledger_off_since": float |
  None}`, on the control socket's view only.

**Where each event is written.** `Admitter`, `Drainer`, `Room` and `GateCore` each take `ledger: Ledger | None =
None`, `GateCore` handing its own to each. A load once per load where admission marks it ready (`_ready`), with id
`load.starter.req.id if load.starter else None`, and where a load fails; a refusal where a `RefusalRecord` is appended
(`admission.py:1257`, `app.py:550` with `model if known else None`), and in `core.py`'s backstop only when the record
is new to `self._backstop`; an unload where the Drainer reports `unloaded` (`policy.py`, near its `unloaded` event) and
in `_lost` (why `gone`), and where the brake's kills are ingested (`core._ingest_brake`'s loop, why `brake`, and
`brake_fired`); `brake_released` where `room.py` records it; `unload` with why `apply` around apply's llama-swap
restart; `lifecycle("start"|"stop", "gate")` in `gate/main.py`, and the front's (`who: "front"`) when a snapshot shows a
new `front_started_at` or the front's events call drops; `counting(..., "front")` from each snapshot, and the gate's
own writer's state as `ledger_off_since`. `FrontRefusal` gains `request_id`, which the front's `refused()` sends.

- [ ] **Step 1: Write the failing tests:**
  - `test_gate_ledger.py`: `test_each_event_is_one_row_with_no_key_name`; `test_a_clients_string_never_reaches_the_ledger`
    (`refusal("model_not_found", "secretproj-q3", None)` has no `model` and no "secret"; an id that isn't 16 hex is
    dropped); `test_counting_writes_only_on_a_change` for (None, 5.0, 5.0, 7.0, None); `test_a_put_that_raises_never_reaches_the_caller`.
  - `test_gate_admission.py` (its `Gate` rig gains `ledger_rows`): `test_a_load_writes_one_row_with_its_starters_id`, two
    requests on one load giving one row.
  - `test_gate_room.py` (its `World` gains `ledger_rows`): `test_a_brake_release_reaches_the_ledger`, through
    `world.room.release(uid, room=False, brake=True)` with a brake hold.
  - `test_gate_app.py`: `test_a_bad_counting_field_never_fails_the_snapshot` (`"x"`, NaN, null, absent each 200, the
    counts taken); `test_counting_shows_on_the_control_socket_only`; and, posting `/v1/front/refused` with a key named
    `needlekey-q3` and a model `needlemodel-q3`, no "needle" in the ledger.
  - `test_front_gatelink.py`: `test_the_snapshot_carries_counting_off_since_only_when_it_is_a_number`.
- [ ] **Step 2: Run them to see them fail.** `make test ARGS="spark/tests/test_gate_ledger.py spark/tests/test_gate_admission.py spark/tests/test_gate_room.py spark/tests/test_gate_app.py spark/tests/test_front_gatelink.py -q"`.
  Expected: FAIL, no module `spark.gate.ledger`, then the new assertions.
- [ ] **Step 3: Implement** as above.
- [ ] **Step 4: Run the gate's and the front's suites** (add `test_gate_core.py test_gate_policy.py test_gate_notify.py
  test_gateclient.py test_front_forward.py test_front_app.py`). Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark): 🤖 the gate's ledger: loads, unloads, refusals, the brake and starts, joined to the front's rows by request id`.

### Task 44f [Spark]: the units and bootstrap's folders

**Files:**
- Modify: `stack/templates/local-ai-front.service` (`ReadWritePaths=-/var/lib/local-ai/usage`, `UMask=0027`, its stop
  comment: 20 s graceful plus the 5 s drain inside its 30 s), `stack/templates/local-ai-gate.service`
  (`-/var/lib/local-ai/gate-ledger` added to its `ReadWritePaths=`, its stop comment), `stack/templates/local-ai-llama-swap.service`
  (`InaccessiblePaths=` gains `-/var/lib/local-ai/usage -/var/lib/local-ai/gate-ledger` beside its secrets entry),
  `stack/host/bootstrap.sh` (the *State* block, after the brake's folder: root makes both folders, beside, never inside,
  `spark`'s, a comment saying why), `website/how-to/deploy.md` (the rollback's "what it leaves" gains both folders)
- Test: `spark/tests/test_units.py`, `spark/tests/test_bootstrap.py`, `spark/tests/test_render.py`; update
  `test_units.py:195` (the front's `ReadWritePaths`), `test_units.py:251-252` (the gate's four paths), the stop-time sums
  at `test_units.py:307-319`, and `test_render.py:305-316` (`InaccessiblePaths`).
- [ ] **Step 1: Write the failing tests**, exact, never by substring: `words(service["ReadWritePaths"]) ==
  ["-/var/lib/local-ai/usage"]` and `service["UMask"] == ["0027"]` for the front; the gate's paths as their exact list;
  llama-swap's `InaccessiblePaths` as its exact list; bootstrap's `install_d_line("/var/lib/local-ai/usage")` holding
  `-o spark-front -g spark-admin -m 2750` and the ledger's `-o spark -g spark-admin -m 2750`, each after
  `/var/lib/local-ai` is made and after `spark-front` exists.
- [ ] **Step 2: Run them to see them fail.** `make test ARGS="spark/tests/test_units.py spark/tests/test_bootstrap.py spark/tests/test_render.py -q"`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them to see them pass, then in Ubuntu 24.04.** **On the Mac**, then in a throwaway `ubuntu:24.04`
  container with `systemd` installed, so `systemd-analyze verify` runs (`-rs` shows it passed, not skipped), and
  bootstrap's folder block run as root with stand-in users: `stat -c '%U:%G %a'` gives `spark-front:spark-admin 2750`;
  a file made by `spark-front` under umask 0027 is `640`; a stand-in in `spark-admin` reads it and an `agent` stand-in
  can't. Each command is run before it is written here.
- [ ] **Step 5: Commit** `feat(stack): 🤖 the usage and ledger folders, Dan's to read, writable only by the front and the gate`.

### Task 44g [Spark]: doctor's checks, `spark status`, and the meter's scenario page

**Files:**
- Modify: `spark/src/spark/doctor.py` (three checks and the canary), `spark/src/spark/status.py`
- Create: `website/scenarios/s26-what-it-cost.md` (front matter `scenario-id`, `phase`, `status`, then **Situation.**,
  **What happens.**, **What I see.**, **How to override.**, naming the three checks)
- Test: `spark/tests/test_doctor.py` (its `NAMES` gains `usage records`, `gate ledger` and `counting`, in that order,
  after the folders' checks), `spark/tests/test_status.py`

**Interfaces:** `Probe` gains `files(folder) -> list[tuple[str, str, str, int]]` (name, owner, group, mode; raises
`PermissionError` as the real listing does) and `holds(path, needle) -> bool` (bounded, never returns content);
`FakeProbe` gets the folders and files by default and its `holds` answers from the posted needle, as its journal does.
`usage records` and `gate ledger` pass for owner, group `spark-admin`, mode `2750` with setgid, no file readable by
others, no link inside; they fail naming `make bootstrap` for a missing folder and "log in again" for a listing refused.
`counting` runs after doctor's end-to-end chat and passes when this month's usage file holds a row with `t0` after the
check began (counted, nothing printed), the ledger's file exists, and health's `counting` shows neither off; an old
gate's health without `counting` reads "unknown". The canary also sends its needle as a model (expect 404) and as a
`reasoning_effort`, and fails if either folder's month files hold it. *(Corrected 2026-10-23, the final review's C1: a
second needle, `doctor-canary-model-…`, is the one sent as the model and the effort, and it is looked for only in those
month files. The first, sent as a model, was found in the gate's refusal history on every run, since that history
keeps the name a client asked for in the front's words.)* `spark status` prints `counting off since <time>` for the
front or the ledger.
- [ ] **Step 1: Write the failing tests:** one passing case on real tmp folders with real `chmod` (the owners
  monkeypatched to the running user); failing cases for a file readable by others, `0o750` (setgid lost), `0o2755`, a
  missing folder, a refused listing and a link; `test_counting_needs_a_fresh_row`; `test_the_canary_sends_its_needle_as_a_model_and_an_effort`;
  `test_status_says_when_counting_is_off` through `text(view)`.
- [ ] **Step 2: Run them to see them fail.** `make test ARGS="spark/tests/test_doctor.py spark/tests/test_status.py -q"`.
- [ ] **Step 3: Implement**, in doctor's style; each check prints a verdict only, never a file's content or a key name.
  Write the scenario page.
- [ ] **Step 4: Run them to see them pass**, and `make docs` on the Mac renders.
- [ ] **Step 5: Commit** (the checks and the page together, as `CLAUDE.md` requires)
  `feat(spark): 🤖 doctor checks the meter's folders and that counting is on; its scenario page`.

### Task 44h [Spark]: OpenCode's and Hermes's own keys

**Files:**
- Modify: `spark/src/spark/clients.py` (the `--key-env` defaults at `:365` and `:373`; `hermes_placeholder_left` checks
  `SPARK_API_KEY` too), `spark/src/spark/messages.py` (`UNKNOWN_KEY` names no single variable), `stack/keys.example.yaml`
  (two invented lines), `website/how-to/opencode-hermes.md`, `website/how-to/secret-files.md`
- Test: `spark/tests/test_clients.py`; update `test_messages.py:658`, `test_front_app.py:242` and `test_registry.py:695`.
- [ ] **Step 1: Write the failing tests** through the parser: `parse_args(["opencode"]).key_env ==
  "SPARK_OPENCODE_KEY"`, Hermes's `"SPARK_HERMES_KEY"`, pi's still `"SPARK_API_KEY"`; the placeholder check finds an
  old `SPARK_API_KEY=` line.
- [ ] **Step 2: Run them to see them fail.** `make test ARGS="spark/tests/test_clients.py spark/tests/test_messages.py -q"`.
- [ ] **Step 3: Implement.** The runbooks gain Dan's steps, in this order:
  1. **On the Mac:** each key made into `~/.secrets` with the `printf` builtin, guarded by `grep -c` so it isn't made
     twice.
  2. **On the Mac:** only its digest goes to the Spark:
     `( . ~/.secrets; printf '%s' "$SPARK_OPENCODE_KEY" | shasum -a 256 | cut -d' ' -f1 ) | ssh brightroar 'umask 077; cat > ~/.spark-digest-in'`.
  3. **On the Spark:** the digests file's line by the secret-files runbook's "another machine" step (checked as 64 hex),
     the key list's line with a label pi's retry list won't match, then its restart steps.
  4. **On the Mac:** each key checked off the command line:
     `printf 'Authorization: Bearer %s\n' "$SPARK_OPENCODE_KEY" | curl -s -o /dev/null -w '%{http_code}\n' -H @- <front URL>/v1/models`,
     expecting `200`.
  5. **On the Mac:** only then `make clients`. A note says the new key names go into the denylist on both machines,
     and that names the docs use (`opencode`, `hermes`) would trip the leak hook. Task 38's rollback gains its own
     step: on the Mac,
     `uv run --frozen --project spark spark clients opencode --write --key-env SPARK_API_KEY`, the same for `hermes`,
     then a restart of both, since Phase 1's llama-swap doesn't know the new keys.
- [ ] **Step 4: Run them to see them pass.**
- [ ] **Step 5: Commit** `feat(clients): 🤖 OpenCode and Hermes get keys of their own; the 401 names no one variable`.

### Task 44i [Spark]: the by-hand SGLang reports its cache

**Files:** Modify `stack/host/by-hand-fast-coder` (its `sglang.launch_server` line) and the *Run it again* block of
`website/how-to/qwen3.8-27b-sglang.md`, byte for byte the same; Test `spark/tests/test_by_hand.py`.

- [ ] **Step 1:** a test that the launch line holds `--enable-cache-report`. **Step 2:** see it fail. **Step 3:** add
  the flag to both. **Step 4:** see it and `test_it_holds_the_runbooks_block_as_written` pass. **Step 5:** commit
  `feat(stack): 🤖 the by-hand SGLang reports its cached tokens, so its rows aren't read as uncached`.

### Task 44j [Spark]: the Architecture page

**Files:** Modify `website/architecture.qmd` (the front's records and the gate's ledger, dashed until Task 44l deploys
them; the scenario table's row for S26; line 195's "the same key" corrected, dated), and `_overview.qmd` only if it
shows the front's outputs.

- [ ] **Step 1:** draw the two stores dashed. **Step 2:** `make docs` on the Mac renders. **Step 3:** commit
  `docs(website): 🤖 the usage meter's two stores, dashed until deployed`.

### Task 44k [Spark]: the plan, Phase 2a's plan and the rules, on `phase-2a`

Made by the Spark session on `phase-2a` once Tasks 44a–44j have merged into it with Dan's OK, so these edits never
conflict with Tasks 40–44's own.

**Files:** `website/design/plan.md` (a Revisions line in its `- **YYYY-MM-DD** — …` form; Phase 2a's line gaining the
counter; Phase 2b's gaining the rest and a per-group cap; the Requirements row "always see what model is being used",
the SwiftBar plugin's row, the menu bar line, 2b's item and its done-when each annotated with the dot-only title; the
`invalid_api_key` row at `:1108`; Phase 3's, 4a's, 5's (with S18's rebuild), 2c's and 6's lines as the design's §8 lists,
Phase 6's "nothing anyone sends is logged" corrected; the Open items the design's §8 names), `website/design/phase-2a.md`
(a pointer to this plan for Tasks 44a–44l; Task 45 recording the front's peak memory with the scanner, no rows
dropped, and rows checked against pi's own counts as a verdict only ("within N %"); Task 49: after a SIGKILL there are
no rows for the killed requests, and the ledger shows the front's stop and start, and an unwritable folder never stops
the front; Task 50's `cut` and refusal rows with their codes; both done-when lists; Task 51's file lists; Task 52's
`make clients` check; `:380` and `:1300`'s 401 words), `website/design/usage-meter.md` (its status: approved, dated),
`CLAUDE.md` and README §Conventions (key names never reused; no live figure from the meter in the repo).

- [ ] **Step 1:** each edit, correcting rather than deleting, dated. **Step 2:** `make docs` renders. **Step 3:** commit
  `docs(plan): 🤖 the usage meter's counter joins Phase 2a as Tasks 44a–44l; the rest opens 2b`.

### Task 44l [Spark] [Dan]: deploy, snapshot the Spark's sources, record

Box work, for the Spark session and Dan, after Task 44k, and finished before Task 45 begins. Each command is run once
before it is relied on (the Phase 0 lesson); the box facts marked are checked first.

- [ ] **Step 1 [Dan]:** the two keys, by Task 44h's runbook, through `make clients` on the Mac; the key names into the
  denylist on both machines.
- [ ] **Step 2 [Dan], on the Mac:** a fresh read-only copy of Hermes's `state.db` into a new dated folder beside the
  2026-10-23 snapshot, for its sessions since then.
- [ ] **Step 3 [Dan], on the Spark, interactively:** the snapshots, before counting starts. Open WebUI's database as a
  root-only copy outside every home, since it holds password hashes and users' keys as well as every chat (box facts to
  check: the database's path in the container, `python3` in the image):

```bash
sudo install -d -o root -g root -m 0700 /var/backups/local-ai-usage
sudo docker compose --project-directory /etc/local-ai/compose exec -T open-webui python3 -c 'import sqlite3; s=sqlite3.connect("/app/backend/data/webui.db"); d=sqlite3.connect("/tmp/usage-snap.db"); s.backup(d); d.close(); s.close()'
sudo docker compose --project-directory /etc/local-ai/compose cp open-webui:/tmp/usage-snap.db /var/backups/local-ai-usage/open-webui-$(date -u +%F).db
sudo docker compose --project-directory /etc/local-ai/compose exec -T open-webui rm /tmp/usage-snap.db
sudo chmod 0600 /var/backups/local-ai-usage/open-webui-$(date -u +%F).db; sudo -k
```

  And `agent`'s pi sessions, read by `agent` and written by Dan's shell, so root never touches `agent`'s paths; the
  archive is counted, never extracted (box fact to check: the sessions' path):

```bash
umask 077; d="$HOME/.local/state/local-ai/usage-snapshots/$(date -u +%F)"; mkdir -p "$d"
cd / && sudo -u agent env -i HOME=/home/agent PATH=/usr/bin:/bin tar -C /home/agent/.pi/agent -cf - sessions > "$d/agent-pi-sessions.tar"
tar -tf "$d/agent-pi-sessions.tar" | wc -l; sudo -k
```

- [ ] **Step 4 [Dan], on the Spark:** `make bootstrap-dry-run`, read for the two `install -d` lines; then, with a
  second SSH session open, `make bootstrap`; then
  `stat -c '%U:%G %a %n' /var/lib/local-ai/usage /var/lib/local-ai/gate-ledger` shows `spark-front:spark-admin 2750`
  and `spark:spark-admin 2750`. Any apt change goes in `changelog.md`.
- [ ] **Step 5 [Dan], on the Spark, in tmux:** `make apply`, which stages the three units and stops; `git status
  --short -- Makefile stack/host` is empty; `make install-units`, reading the `ReadWritePaths`, `UMask` and
  `InaccessiblePaths` diffs; `make apply` again: llama-swap's unit changed, so every model stops and the residents
  reload. Then `make status` and `make doctor`, which passes the three new checks.
- [ ] **Step 6 [Spark], on the Spark:** after one request from pi on the Mac, the newest row's field names only:
  `tail -n 1 /var/lib/local-ai/usage/usage-$(date -u +%Y-%m).jsonl | jq -c keys`, showing `p`, `o` and `from`.
- [ ] **Step 7 [Spark]:** `changelog.md`, README §Current state, the Architecture page's two stores solid, S26's `status:
  built`, `CLAUDE.md`'s private-material paragraph and README §My environment (the two folders, the Spark's
  snapshots in `/var/backups/local-ai-usage` and Dan's `usage-snapshots`), and the vault's note (Dan): commit
  `docs(machine): 🤖 the usage meter's counter is running`.

**If counting misbehaves:** revert Tasks 44a–44e on `phase-2a` and `make apply`, which restarts only the front and the
gate; the units' `-` paths are inert and stay. For a disk problem alone nothing is needed: the front serves with
counting off, and doctor says so.

***

## After the Mac round (2026-10-23)

Tasks 44a–44j are built and reviewed on `chendaniely/harness-status-bar`: each task had a fresh implementer and a
fresh reviewer, then a whole-branch review and one fix wave. Nothing is pushed or merged. What is left, in order:

1. **[Spark] The Linux-only tests.** The Mac skips them (the front's and the gate's `spark front` / `spark gate`
   subprocess tests among them), so a full `make test` on the Spark comes before the merge. On the Mac, the failures
   in `test_bootstrap` (68), `test_brake` (14), `test_tested_against`, `test_tickets`, `test_launch`,
   `test_notifier_script` and `test_ntfy_records` are the Mac's environment: the same tests fail at the plan's base
   commit.
2. **[Spark] Task 44f's Ubuntu 24.04 check,** which needed Docker the Mac doesn't have. *Not yet run*: the Spark
   session runs each step before relying on it. In a throwaway `ubuntu:24.04` container with the clone mounted
   read-only and `systemd` installed: `systemd-analyze verify` on the rendered `local-ai-front.service`,
   `local-ai-gate.service` and `local-ai-llama-swap.service`, seen to run, not skip; then, as root with stand-in
   users (`spark-front`, `spark`, one in `spark-admin`, an `agent`), bootstrap's two `install -d` lines, after which
   `stat -c '%U:%G %a'` shows `spark-front:spark-admin 2750` and `spark:spark-admin 2750`, a file `spark-front` makes
   under umask 0027 is `640`, the `spark-admin` stand-in reads it, and the `agent` stand-in can't.
3. **[Dan] The merge into `phase-2a`,** with Dan's OK, and the denylist scan before any push. `website/design/plan.md`
   gained one dated correction here (the `invalid_api_key` row's 401 words), which may conflict with `phase-2a`'s own
   edits.
4. **[Spark] Task 44k,** on `phase-2a`, which also carries what this round made untrue there: `phase-2a.md`'s "one
   request id for each admit" (~line 5810), its `/v1/front/inflight` and `/v1/front/refused` shapes (~2070), its quotes
   of the old 401 words (~380, ~1300), its Task 38 rollback (the two `--key-env SPARK_API_KEY` commands), its lines on
   llama-swap's `InaccessiblePaths` (~6460) and the gate's `ReadWritePaths` (~6620), lines ~156–157; `plan.md` ~276–277
   and ~1750; Task 49's wording (a request still waiting on the gate at SIGTERM leaves `end: gone`, not `cut`); and the
   design's §2 on the ledger's rows.
5. **[Spark] [Dan] Task 44l, the deploy,** as written above. Until its keys exist, `make clients` on the Mac gives
   OpenCode and Hermes 401s: their runbook's rollback commands point them back at `SPARK_API_KEY`. Each
   `make doctor --full` run may send one "no model by that name" notification, for the canary's own made-up model name.
