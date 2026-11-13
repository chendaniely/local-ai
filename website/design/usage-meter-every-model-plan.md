---
title: "Every model priced: the implementation plan"
description: "Tasks E1–E6: proxies beyond Claude's tiers, audio minutes priced, the front learning each Whisper recording's length, the by-size suggestion and make prices, then the Spark's deploy."
---

# Every model priced: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** every request that passes the front and did work is priced, speech and embeddings by the proxies Dan
chose, with a by-size suggestion for a new model and `make prices`.

**Architecture:** the price files gain proxies beyond Claude's four tiers (per token or per audio minute); the store
and pricing gain an audio length per row; the front learns a Whisper recording's length from whisper's own
`verbose_json` and scans Qwen3-ASR's token counts; the suggestion and `make prices` sit on the price module.

**Tech Stack:** Python 3 under uv (`spark/`), pytest, PyYAML, raw ASGI (the front), GNU make 3.81, Quarto docs.

**Spec:** [usage-meter-every-model.md](usage-meter-every-model.md) (with [the decisions](usage-meter-qa.md)).

## Global Constraints

- Branch `meter-every-model`, in the Mac worktree; tasks E1–E5 **[Mac]**, E6 **[Spark] [Dan]**.
- Tests on the Mac: `uv run --frozen --quiet --project spark pytest <files> -q -p no:cacheprovider`. Python only
  through uv; no pip.
- Commits: `type(scope): 🤖 description`, staged by explicit path, never `--no-verify`, ending with the line
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; a message with backticks, `$` or `*` goes in a file and
  `git commit -F`. Never amend.
- No live figure from the meter, no key name, no price paid or time zone, in the repo or a commit. Fixtures are
  invented. Never read `~/.secrets`, any `.env`, or `~/.config/local-ai/denylist`.
- Docs are true in the same commit that changes the behaviour: a `website/` Markdown mid-document rule is `***`;
  every command in the docs says in bold prose above it where it runs.
- Exact values, used verbatim:
  - proxies in `prices.yaml`, each with one line `from: 2026-09-01`:
    `whisper-1` {name: "OpenAI whisper-1", per_minute: 0.006};
    `gpt-4o-mini-transcribe` {name: "OpenAI gpt-4o-mini-transcribe", input: 1.25, output: 5.00, cache_read: 1.25,
    write_5m: 0, write_1h: 0};
    `voyage-4-lite` {name: "Voyage voyage-4-lite", input: 0.02, output: 0, cache_read: 0.02, write_5m: 0, write_1h: 0}.
  - `mapping.yaml`: both `whisper-*` → `whisper-1`; the four `qwen3-asr-*` → `gpt-4o-mini-transcribe`;
    `qwen3-embedding-0.6b` → `voyage-4-lite`.
  - Row fields: `audio_s` (seconds, a float rounded to 3 places), `audio_est` (true only when estimated; left out
    otherwise). Summary period field: `audio_s`. Summary contract stays `v` 1.
  - The size estimate: 16,000 bytes a second. The front's held-answer cap: 16 MiB.
  - New unpriced reason: `no_audio_length`. Verb: `spark usage prices`. Make target: `prices`.
  - The suggestion's threshold: 6B parameters used per token.

## Review Focus

1. A `json` or `text` answer the front converts must be byte for byte what whisper.cpp v1.9.4 itself sends for
   that format (its `examples/server/server.cpp`), content type included.
2. `detect_language` set, an unknown format, a whisper error answer, or an answer the front can't parse: the client
   gets exactly what whisper sent, unchanged, and the row has no `audio_s`.
3. The answer the front holds counts against its memory budget and the 16 MiB cap; a stream or a chat route is
   never held.
4. An embeddings row (its `c` absent) and a Qwen3-ASR row are priced, never `cache_unknown`: a missing cache count
   doesn't matter where the proxy's cache rate equals its input rate.
5. A name with no size in it gives no suggestion; `27B`, `35B-A3B`, `26B-A4B` and `0.6B` read as 27, 3, 4 and 0.6.

***

### Task E1 [Mac]: proxies beyond Claude's tiers, per token or per audio minute

**Files:** Modify `spark/src/spark/meter/prices.py`, `prices.yaml`, `mapping.yaml`, `pricing.py`;
`website/reference/model-prices.md`. Test: `spark/tests/test_meter_prices.py`, `test_meter_pricing.py`.

**Interfaces:** Produces: a `prices.yaml` key may be any proxy name (Claude's four as before); a line is per token
(the five rates) or per minute (`per_minute`, no rates); `PriceLine.per_minute: Decimal | None` (`base` is None for a
per-minute line); `mapping.yaml`'s `tier` must name a key of `prices.yaml` or be `none`, else PriceError naming it;
`table()` renders a per-minute line as `$0.006 per audio minute | — | —` in the three rate cells.
`pricing.WHYS` gains `no_audio_length`; `price()` on a per-minute line returns `Priced(tier=…, line=…,
why="no_audio_length")` until Task E2 gives rows a length. A per-token row whose `c` is None prices as uncached when
the line's `cache_read` equals its `input` (Review Focus 4).

- [ ] **Step 1: Tests.** The shipped files load the three proxies with the exact values; the mapping lines above;
  `tier: gold` and a tier missing from `prices.yaml` are refused; a per-minute line with a rate, or a per-token line
  with `per_minute`, is refused; `table()` has the whisper and embeddings rows as specified; an embeddings row
  (`p=1_000_000, c=None, o=0`) costs `0.02`; a Qwen3-ASR row (`p=1_000_000, c=0, o=1_000_000`) costs `6.25`; a
  per-minute row is `no_audio_length`. Keep `test_the_sites_table_is_the_price_files`.
- [ ] **Step 2:** run them; they fail.
- [ ] **Step 3:** implement. Keep the Claude tiers' behaviour (the long-prompt band, `uncached_as_writes`) unchanged.
- [ ] **Step 4:** paste the new table into `website/reference/model-prices.md` between its markers (the command
  on that page prints it); its *Speech and embeddings* section says they are priced now (Whisper's length from
  Task E3 on), names the three proxies with their prices and the day they were read, and says `make speech`'s
  requests are not counted until its Stage 2 puts it behind the front. Run all `spark/tests/test_meter_*.py` and
  `test_doctor.py`; they pass.
- [ ] **Step 5: Commit** `feat(spark): 🤖 proxies beyond Claude: speech by the minute or the token, embeddings by the token`.

### Task E2 [Mac]: audio minutes in the store, the price and the summary

**Files:** Modify `spark/src/spark/meter/store.py`, `pricing.py`, `summary.py`. Test: `test_meter_store.py`,
`test_meter_pricing.py`, `test_meter_summary.py`.

**Interfaces:** Consumes E1. Produces: `Row.audio_s: float | None = None`, `Row.audio_est: bool = False`,
`Row.bytes_in: int | None = None`, read from front rows (refuse nothing new: a malformed `audio_s` is read as None).
`store.read(book)` sets, for a `done` row whose model maps to a per-minute line and has no `audio_s` but a
`bytes_in`, `audio_s = bytes_in / 16000` and `audio_est = True`. `price()` on a per-minute line:
`audio_s / 60 × per_minute × factor`, or `no_audio_length` with no `audio_s`. The summary's sums gain `audio_s`;
each period's JSON gains `audio_s` (float, 3 places); `usd_estimated` counts a row with `audio_est` as it counts
`c_estimated`; `words()` adds ` · N.N min audio` to a period's line when its `audio_s` is above 0, and the
estimate note names an estimated length as well as an estimated cache. `row_json` gains `audio_s` and `a_est`.

- [ ] **Step 1: Tests:** a 90 s row costs `0.009`; a row with `bytes_in` 1,600,000 and no length is 100 s, estimated,
  and its cost counts in `usd_estimated`; a refused transcription row stays `not_done`; `MENU_READS` still passes;
  `words()` shows `1.5 min audio`.
- [ ] **Step 2:** fail. **Step 3:** implement. **Step 4:** all meter and doctor tests pass; S26
  (`website/scenarios/s26-what-it-cost.md`) says speech is priced by the audio minute and embeddings by the token,
  with the estimates marked, in a dated note.
- [ ] **Step 5: Commit** `feat(spark): 🤖 the meter prices speech by its minutes, estimated from size when unknown`.

### Task E3 [Mac]: the front learns each Whisper recording's length, and Qwen3-ASR's tokens

**Files:** Modify `spark/src/spark/front/app.py`, `rows.py`, `usage.py` (and `parse.py` only if the form needs a
helper); `spark/src/spark/doctor.py` (`--full`'s speech check); the speech scenario page under `website/scenarios/`;
`website/reference/projects.md`. Test: `spark/tests/test_front_app.py`, `test_front_usage.py`, `test_rows.py` or
wherever rows are tested, `test_doctor.py`.

**Interfaces:** Consumes the registry entry's `engine`. Produces the row's `audio_s` and `audio_est`. Behaviour, on
`/v1/audio/transcriptions`, by the resolved model's engine:
- **whisper.cpp:** the form's `response_format` decides. `verbose_json`: forwarded unchanged; the answer is held,
  `duration` read, sent unchanged. `json`, absent, or `text`, with no `detect_language` field: the form is forwarded
  with `response_format=verbose_json`; the answer is held, `duration` read, and the client gets whisper's own `json`
  or `text` for it (Review Focus 1: read whisper.cpp v1.9.4's `examples/server/server.cpp` for the exact bytes and
  content type, and pin them in tests). `srt`/`vtt`: forwarded unchanged; the last cue's end read as `audio_s` with
  `audio_est`. Any other format, a non-2xx answer, or an unconverted answer the front can't read: the client gets
  what whisper sent, unchanged, and the row has no length. A 2xx answer to a *converted* request that has no readable
  `text` and `duration` ends as `error`, with the front's own 502 (never whisper's `verbose_json` in place of the
  format the client asked for).
- **llama.cpp** (Qwen3-ASR): forwarded as now; its answer is scanned like a counted route, reading `usage.input_tokens`
  → `p`, `usage.output_tokens` → `o`, `usage.input_tokens_details.cached_tokens` → `c`, `from` `usage`.
- A held answer is charged to the front's body budget and capped at 16 MiB (past it: the request ends as `error`,
  journal line saying why); streams and other routes are never held. The app's docstring (its *A request's way*)
  says all this, and the measured memory bound is restated if the hold changes it.
- doctor `--full`: its transcription request's row has `audio_s`.
- The speech scenario page: the front asks whisper for `verbose_json` and answers with the client's format; the
  doctor check. `projects.md`: the transcription contract row says answers are unchanged, and the summary's
  periods gain `audio_s` (contract `v` stays 1).

*(2026-11-13, at the task's reviews: past the 16 MiB cap or the body budget, an answer the front doesn't convert
(`verbose_json`, `srt`, `vtt`) goes on unheld, with no length, and only a converted one ends as `error`, with the
front's own 502. `json` and `text` are converted whatever the upload's size, so doctor `--full`'s 170 MB `json` upload
has its length on its row. The converted form also asks for no language probabilities, and whisper's answers are never
scanned for counts. Answers are unchanged but for the cases `projects.md` names: that 502, `text`'s U+FFFD where
whisper's segment bytes aren't UTF-8, and an unknown `language`, which gets whisper's 500.)*

- [ ] **Step 1: Tests** for each case above, with whisper answers shaped as v1.9.4 sends them (invented text), the
  converted bytes compared exactly; the cap; the budget charge; a Qwen3-ASR answer's counts; a chat stream unaffected.
- [ ] **Step 2:** fail. **Step 3:** implement. **Step 4:** `spark/tests/test_front_*.py`, `test_doctor.py` and the
  meter tests pass on the Mac (the Linux-only ones skip; E6 runs them).
- [ ] **Step 5: Commit** `feat(front): 🤖 a Whisper recording's length on its row, its answer as the client asked`.

### Task E4 [Mac]: the by-size suggestion, and make prices

**Files:** Modify `spark/src/spark/meter/prices.py`, `spark/src/spark/commands.py` or wherever `spark usage`'s verbs
are registered, `spark/src/spark/doctor.py` (`meter_prices`' message), the `Makefile`,
`website/reference/model-prices.md`. Test: `test_meter_prices.py`, `test_doctor.py`, the Makefile's test if one
lists targets.

**Interfaces:** `prices.suggest(name: str, entry: Model) -> str | None`, the mapping line to add
(`"<name>: {tier: <proxy>}"`), by the design's rule: capability `embeddings` → `voyage-4-lite`; `transcription` on
`whisper.cpp` → `whisper-1`, on `llama.cpp` → `gpt-4o-mini-transcribe`; `chat`: parameters used per token from the
model's name, else its source file's or repo's name (`A<n>B` is what it uses, else `<n>B`), 6 or more → `sonnet`,
under 6 → `haiku`; no size found → None. `test_every_registry_model_has_a_line` fails naming each missing model with
its suggestion; doctor's `meter prices` failure names them too. `spark usage prices` prints `table(load())`;
`make prices` runs it (help: `On the Mac or the Spark: every model's pricing proxy and price`).

- [ ] **Step 1: Tests:** today's registry models get the suggestion that matches their line; Review Focus 5's names;
  a model with no size gives None; the verb prints the table; `make -n prices` names the verb.
- [ ] **Step 2:** fail. **Step 3:** implement. **Step 4:** the Model prices page's *Adding a model* uses
  `make prices` and describes the suggestion; tests pass.
- [ ] **Step 5: Commit** `feat(spark): 🤖 a new model's proxy suggested by size, and make prices`.

### Task E5 [Mac]: the plan's record

**Files:** `website/design/plan.md` (a Revisions line: every model priced, from Dan's decisions of 2026-11-12, this
plan; a Backlog line: `make speech`'s requests counted when its Stage 2 puts it behind the front),
`website/design/usage-meter-every-model.md` (any sentence the build made untrue).

- [ ] **Step 1:** edit; render the changed pages with Quarto on the Mac (`quarto render <page>` in `website/`) and
  check the links resolve. **Step 2: Commit** `docs(plan): 🤖 every model priced, built on the Mac`.

### Task E6 [Spark] [Dan]: test, merge, deploy, check

- [ ] **Step 1 [Spark]:** `git fetch origin && git switch meter-every-model && make test`: every test passes.
- [ ] **Step 2 [Dan], on the Spark, before the deploy:** one short recording sent through the front to
  `whisper-large-v3-turbo` as `json` and as `text`, each answer saved to a file in `mktemp -d` (the Spark session
  gives the two `curl` commands, the key by reference).
- [ ] **Step 3 [Spark]:** `git switch phase-2a && git merge --no-ff meter-every-model`. **[Dan]:** `make apply`
  (it restarts the front; it waits for a quiet minute).
- [ ] **Step 4:** the same two requests again: `cmp` says each answer is identical to its saved one; the newest
  transcription row has `audio_s` (field names only: `tail -n 1 … | jq -c keys`); `make doctor` passes; `make usage`
  runs. Timing the same file before and after shows the `verbose_json` cost; noted in the changelog.
- [ ] **Step 5 [Spark]:** `changelog.md` and README §Current state; the before-push scan; push `phase-2a` with Dan's
  OK. Commit `docs(machine): 🤖 every model priced, deployed`.

*(2026-11-13, at the final whole-branch review: Task E6's steps above stand; these are added to them.)*

- **Before Step 1 [Mac]:** the before-push denylist scan, as *Before every push* in
  [the leak guards](../how-to/leak-guards.md) gives it, then push `meter-every-model` with Dan's OK, so the Spark can
  fetch it.
- **Step 4 also [Spark]:** `spark doctor --full`, from the clone's root, next to `make doctor`: it is the only real
  test of the converted path and its 1 % length check.
- **During that `--full` run [Spark]:** read the front unit's peak memory with
  `systemctl show -p MemoryPeak local-ai-front.service` and record it in the changelog, since the app's step 6 says the
  held-answer bound isn't measured on the Spark.
- **Step 5 also flips** every "built on the `meter-every-model` branch, not yet deployed" marker to deployed with the
  date: `grep -rn "meter-every-model" website README.md CLAUDE.md`, leaving `website/_site` alone.
- **A count-only check [Spark]:** whether any Qwen3-ASR rows without counts exist, with `jq` over the usage files
  printing a number and never a figure of spend, to say in the changelog how many rows stay unpriced.
- **With Dan's OK, the notices:** a comment on #55 (spark-menu: the summary's `audio_s`, `usd_estimated`'s wider
  meaning, and the header rename on `spark-menu-by-model`); notices under `notice/vid2sub`, `notice/skills` and
  `notice/recordings` naming the front's transcription exceptions (the 502 `front_error` for a converted answer past
  the cap, U+FFFD in `text`, and whisper's 500 for an unknown language on a converted request); and `spark-menu-by-model`
  merged and reloaded on the Mac.
