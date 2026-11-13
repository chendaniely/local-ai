---
title: "The usage meter's first numbers — implementation plan"
description: "Tasks U1–U14: the price files, `spark usage` (rows, summary, import, harvest), the box's parts and docs on this branch; spark-menu's poll, push and usage section in Dan's dotfiles; then the Spark's tests, merge and deploy with Task 44l, and the private file."
date: 2026-11-12
---

# The usage meter's first numbers — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** from the counter's deploy (Task 44l) on, spark-menu's dropdown shows what the Spark's work would have cost
at Claude's prices (today, this month, last month and all time, with all time as a share of the Spark's price), the
by-hand fast coder's tokens included, every figure worked out on the Spark.

**Architecture:** a `spark.meter` package reads the front's rows, the gate's ledger and the rows imported from the
Mac, prices each row when read from three YAML files in the package (mapping, prices, providers), and prints a
summary (`spark usage summary --json`) and every priced row (`spark usage rows`). On the Mac, `spark usage harvest`
reads OpenCode's database read-only and prints one row of numbers per message to the by-hand engines; spark-menu
~~pipes it to~~ sends it to `spark usage import` over its SSH master every 5 minutes *(2026-11-12, at the final review:
through a `0600` temp file, sent only when the harvest exited 0, never a pipe; Task U10's ruling)*, and fetches the
summary every 60 seconds into `usage.json`, which its panel draws.

**Tech Stack:** Python 3 (the `spark` uv project: standard library, `pyyaml`, `zoneinfo`, `sqlite3`), pytest; bash 3.2
and `jq` for spark-menu, with its fake-ssh test harness.

**Spec:** [the first numbers' design](usage-meter-now.md), approved by Dan on 2026-11-12, which amends [the usage
meter's design](usage-meter.md); Dan's words are in [its questions and answers](usage-meter-qa.md).

**Where it runs.** Tasks U1–U8 on the Mac, on branch `swiftbar-costs` (cut from `phase-2a` at `ac8a22c`), as the
counter's Tasks 44a–44j were: `spark usage` reads files and needs nothing of the box. Tasks U9–U11 on the Mac, in Dan's
dotfiles (`~/git/private/dotfiles`), on a branch `spark-menu-usage` cut from its `main`. Tasks U12–U14 are the Spark
session's and Dan's. Tests run with `make test ARGS="…"` here, and `make -C mac-heartsbane/spark-tunnel test` in the
dotfiles.

## Settled before the plan (2026-11-12)

The design's part 3 left three facts to settle before the harvest's rules were fixed. Counts-only checks on
heartsbane's `opencode.db` (OpenCode 2.2.7) settled all three; no message's content was read, and these are verdicts,
not figures:

1. **One stored assistant message is one model call.** Messages live in `session_message` (the `message` table is
   OpenCode's old one, and `session_part` is empty); every assistant message has one `finish`, and the agent loop's
   tool rounds are each a message of their own, ending `tool-calls`. So the cache estimate works per message.
2. **`tokens.input` leaves out cached tokens, and `tokens.output` leaves out reasoning.** Nearly every row of the
   `spark` provider with cache reads has `input` below `cache.read`, which a count holding them couldn't; and among
   rows that report reasoning, many have `output` below `reasoning`. So *P* = input + cache.read + cache.write and *O*
   = output + reasoning, as the meter's design's §7 has them. llama.cpp and SGLang report no reasoning count of their
   own (`reasoning` is 0 on their rows), so for them `output` holds the thinking.
3. **Flash-Next reports its cache; the fast coder never has.** Most of `spark-flash-next`'s rows with tokens have
   cache reads; none of `spark-sglang`'s do.

Also seen: `compaction` messages carry tokens and a model too, with only `time.created`; an aborted message
(`error.type` `aborted`) may carry tokens; error types are short fixed names (`provider.auth`, `provider.internal`,
…); times are milliseconds.

## Global Constraints

- No stored row holds a dollar. Prices are applied when read, so a remap or a corrected price line reprices the past.
- spark-menu prices nothing and adds no two numbers: it prints the summary's figures, formatted.
- The harvest's SQL extracts only the fields in Task U6's listing. Never `content`, `text`, `title`, `directory` or
  `error.message`; the session id is read only to hash it, and leaves the Mac only as that hash.
- An imported row's strings match `[A-Za-z0-9_.:-]{1,64}`; `sid` is 16 lowercase hex; every count is an int with
  0 ≤ n < 2**53. Anything else is refused, never stored.
- The cached count is absent, never 0, when its engine doesn't report one; an imported row keeps OpenCode's raw counts
  and the Spark decides, by the providers' file, whether its cache read is real.
- Error, refused and interrupted rows are counted and never priced. A front row with no cached count is unpriced and
  flagged `cache_unknown`. A model with no mapping line is unpriced and flagged `⚠map`; `none` is unpriced on purpose
  and never flagged.
- The front never imports `spark.meter`: a change to the price files restarts only the gate and the brake, never the
  front or llama-swap (Dan's decision, 2026-11-12). `spark.meter` imports only the standard library, `yaml` and
  `spark.paths` at module level, since `build_parser` imports every command's module.
- No live figure from the meter in the repo, the site or a commit; fixtures and examples are invented; a check
  against real rows prints a verdict only. The private file and Dan's real summary are checked by presence only.
- spark-menu: bash 3.2, never contacts the Spark while off, every remote call over the master by `with_timeout`, and
  every string printed through the panel's escaping with control characters removed. Dotfiles prose follows semantic
  line breaks (its `CLAUDE.md`).
- Commits: Conventional Commits; here with 🤖 after the prefix (the dotfiles' subjects carry no 🤖); files staged by
  path; the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; never `--no-verify`.

## Review Focus

1. **A day or month edge in Dan's zone:** a row at 23:30 local on the 31st belongs to that month though its UTC time
   is the next day's; days and months follow the zone through a DST change. Test in Task U4.
2. **A row on a price line's first day:** priced by the line in effect at its `t0`, so a Sonnet row at 23:59 UTC on
   2026-10-06 takes $0.20 cache reads, and one at 00:00 on 10-07 takes $0.10. Test in Task U3.
3. **A cut pipe, a re-sent batch, an overlapping `--since`:** a truncated last line is refused, not half-stored; a row
   sent twice is unchanged; the summary counts each message once. Tests in Task U5 and U2.
4. **OpenCode running, missing, or changed:** the harvest opens the database read-only beside OpenCode's writes; a
   missing file or a database without `session_message` exits 1 in words, and spark-menu keeps going. Tests in Task
   U6 and U10.
5. **A summary the menu can't trust** (an older `v`, a field missing, a string holding `|` or a control character, a
   summary made last month): the section shows "—" or the period's name, never breaks the menu. Test in Task U11.

***

### Task U1 [Mac]: the price files and their loader

**Files:**
- Create: `spark/src/spark/meter/__init__.py` (a docstring for now), `spark/src/spark/meter/mapping.yaml`,
  `spark/src/spark/meter/prices.yaml`, `spark/src/spark/meter/providers.yaml`, `spark/src/spark/meter/prices.py`
- Modify: `spark/src/spark/paths.py` (`METER_DIR`)
- Test: `spark/tests/test_meter_prices.py`

**Interfaces:**
- Produces, in `spark.meter.prices`:
  - `class PriceError(ValueError)`: the file and the reason, so `cli.main` prints it.
  - `@dataclass(frozen=True) class Rates`: `input`, `output`, `cache_read`, `write_5m`, `write_1h`, each a `Decimal`
    in US dollars per million tokens.
  - `@dataclass(frozen=True) class PriceLine`: `tier: str`, `start: float` (UTC epoch of 00:00 on its `from` day),
    `name: str` (e.g. `"Opus 5.5"`), `base: Rates`, `above: Rates | None`, `above_over: int | None`; and
    `label -> str`, `f"{tier}@{from-date}"`.
  - `@dataclass(frozen=True) class Mapping`: `tier: str` (one of `TIERS` or `"none"`), `factor: Decimal`.
  - `@dataclass(frozen=True) class Provider`: `harness: str`, `provider: str`, `front: bool`, `start: float | None`,
    `end: float | None`, `cache_from: float | None` (None: its engine doesn't report a cache yet).
  - `@dataclass(frozen=True) class PriceBook`: `mapping: dict[str, Mapping]`, `lines: dict[str, list[PriceLine]]`
    (each tier's, sorted by `start`), `uncached_as_writes: dict[str, bool]`, `providers: list[Provider]`.
  - `TIERS = ("opus", "sonnet", "haiku", "fable")`
  - `load(folder: Path = paths.METER_DIR) -> PriceBook`
  - `line_at(book: PriceBook, tier: str, t: float) -> PriceLine | None`: the last line with `start <= t`.
  - `cache_reported(book: PriceBook, provider: str, t: float) -> bool`: its line's `cache_from` is set and `<= t`.
  - `by_hand(book: PriceBook, harness: str) -> list[Provider]`: the harness's providers with `front: false`.
- `paths.METER_DIR = Path(os.environ.get("SPARK_METER_DIR", Path(__file__).parent / "meter"))`.
- Every `folder` or `path` default in `spark.meter` is resolved when called (`folder: Path | None = None`, then
  `paths.X`), never bound when the module is imported, so a test's `monkeypatch.setattr(paths, …)` reaches it.

The files' text, as the design's part 1 fixes it (comments say what each field means and that a line applies from
00:00 UTC on its day):

```yaml
# mapping.yaml
v: 1
tiers:               # per tier: charge uncached prompt tokens at the 5-minute cache-write rate (§3's switch)
  opus: {uncached_as_writes: false}
  sonnet: {uncached_as_writes: false}
  haiku: {uncached_as_writes: false}
  fable: {uncached_as_writes: false}
models:              # tier: opus | sonnet | haiku | fable | none; factor: optional, default 1
  gemma-4-26b-a4b: {tier: opus}
  qwen3.8-27b: {tier: opus}
  qwen3.6-35b-a3b: {tier: opus}
  qwen3.8-27b-sglang: {tier: opus}
  qwen3.8-flash-next: {tier: opus}
  qwen3.8-flash-next-mtp: {tier: opus}
  qwen3-embedding-0.6b: {tier: none}
  whisper-large-v3-turbo: {tier: none}
  whisper-large-v3: {tier: none}
  qwen3-asr-1.7b-32k: {tier: none}
  qwen3-asr-1.7b-64k: {tier: none}
  qwen3-asr-0.6b-32k: {tier: none}
  qwen3-asr-0.6b-64k: {tier: none}
```

```yaml
# prices.yaml: US dollars per million tokens; checked against Anthropic's pricing page and release notes 2026-11-12
v: 1
tiers:
  opus:
    - {from: 2026-09-22, name: Opus 5.5, input: 4, output: 20, cache_read: 0.20, write_5m: 5, write_1h: 8}
  sonnet:
    - {from: 2026-09-28, name: Sonnet 5.5, input: 2, output: 10, cache_read: 0.20, write_5m: 2.50, write_1h: 4}
    - {from: 2026-10-07, name: Sonnet 5.5, input: 2, output: 10, cache_read: 0.10, write_5m: 2.50, write_1h: 4}
  haiku:
    - {from: 2026-09-28, name: Haiku 4.5, input: 1, output: 5, cache_read: 0.10, write_5m: 1.25, write_1h: 2}
    - {from: 2026-10-07, name: Haiku 5.5, input: 0.10, output: 0.50, cache_read: 0.01, write_5m: 0.125, write_1h: 0.20,
       above: {over: 100000, input: 0.50, output: 2.50, cache_read: 0.05, write_5m: 0.625, write_1h: 1}}
  fable:
    - {from: 2026-09-01, name: Fable 5.1, input: 10, output: 50, cache_read: 0.25, write_5m: 12.50, write_1h: 20}
    - {from: 2026-11-09, name: Fable 5.2, input: 8, output: 40, cache_read: 0.20, write_5m: 10, write_1h: 16}
```

```yaml
# providers.yaml: the provider names that reached brightroar, per harness (the meter's design, §3)
v: 1
providers:
  - {harness: pi, provider: spark, front: true}
  - {harness: opencode, provider: spark, front: true}
  - {harness: hermes, provider: spark, front: true}
  - {harness: posit-assistant, provider: spark, front: true}
  - {harness: opencode, provider: spark-sglang, front: false, from: 2026-10-15, cache_from: null}
  - {harness: opencode, provider: spark-flash-next, front: false, from: 2026-10-15, cache_from: 2026-10-15}
```

- [ ] **Step 1: Write the failing tests** in `test_meter_prices.py`:
  - `test_the_shipped_files_load`: `load()` gives `book.lines["sonnet"]` two lines, the second's `start` equal to
    2026-10-07T00:00Z's epoch and `base.cache_read == Decimal("0.10")`; `book.mapping["qwen3.8-27b"].tier == "opus"`
    with factor 1; `book.mapping["whisper-large-v3"].tier == "none"`; `book.lines["haiku"][1].above_over == 100000`.
  - `test_every_registry_model_has_a_line`: every model in `stack/models.yaml` is in `book.mapping` (a new model
    without one fails here, not as `⚠map` on the box).
  - `test_line_at_picks_the_line_in_effect`: for sonnet, `t` one second before 2026-10-07T00:00Z gives the first line,
    `t` at it the second; for opus, a `t` before 2026-09-22 gives None.
  - `test_cache_reported`: `spark-sglang` False at any `t`; `spark-flash-next` True on 2026-10-15 and after;
    `spark` False (not a by-hand provider: its rows carry their own `c`).
  - `test_refusals_name_the_file` (parametrized over a tmp copy of the folder with one fault each): an unknown tier,
    a negative or non-numeric rate, a missing rate, a factor ≤ 0, two lines of a tier on one day, lines out of date
    order, an `above` without `over`, a `v` other than 1, a provider with `front: false` and no `from`. Each raises
    `PriceError` whose text names the file.
  - `test_the_front_never_imports_the_meter`: `spark.front.FRONT_MODULES` holds no `spark.meter` module, and
    `apply.front_file("src/spark/meter/prices.yaml")` is False.
  - `test_a_price_edit_restarts_only_the_gate_and_the_brake`: `apply.plan_restarts([], ["src/spark/meter/prices.yaml"],
    …, active=lambda unit: True)` gives `now` the brake and the gate and `after_quiet` empty (built as
    `test_apply.py`'s `plan_restarts` tests build their registries).
- [ ] **Step 2: Run** `make test ARGS="spark/tests/test_meter_prices.py"`. Expected: FAIL, `spark.meter.prices` not found.
- [ ] **Step 3: Implement** the three files as listed and `prices.py`. Rates go through `Decimal(str(value))`, so a
  YAML float never carries binary error into a price; a `from` may be a YAML date or an ISO date string.
- [ ] **Step 4: Run** the same command. Expected: PASS. Then `make test ARGS="spark/tests/test_apply.py
  spark/tests/test_tested_against.py"` still passes (the new package is walked by the latter).
- [ ] **Step 5: Commit** `feat(spark): 🤖 the meter's price files: each model's Claude tier, dated price lines, and
  the providers that reach brightroar`.

### Task U2 [Mac]: reading the rows

**Files:**
- Create: `spark/src/spark/meter/store.py`
- Modify: `spark/src/spark/paths.py` (`USAGE_SESSIONS_DIR`)
- Test: `spark/tests/test_meter_store.py`, `spark/tests/fixtures/meter/` (invented rows)

**Interfaces:**
- Consumes: `prices.PriceBook`, `prices.cache_reported`.
- Produces, in `spark.meter.store`:
  - `@dataclass(frozen=True) class Row`: `source: str` (`"front"` or `"opencode"`), `id: str`, `t0: float`,
    `t1: float | None`, `model: str | None`, `p: int | None`, `c: int | None`, `o: int | None`, `end: str`,
    `key: str | None` (front rows), `provider: str | None` (imported rows), `session: str | None` (the `sid` hash),
    `c_estimated: bool = False`, `dropped: int = 0`.
  - `front_rows(folder: Path = paths.USAGE_DIR) -> list[Row]`: every `usage-YYYY-MM.jsonl`; a line that isn't a JSON
    object with `v == 1`, a 16-hex `id` and a numeric `t0` is skipped and counted in `Store.bad_lines`.
  - `imported_rows(book: PriceBook, folder: Path = paths.USAGE_SESSIONS_DIR) -> list[Row]`: `opencode.jsonl`, the last
    line of each `mid` winning; *P* = `in + cr + cw`, *O* = `out + reas`, *C* = `cr` when
    `cache_reported(book, prov, t0)`, else None; `end` as stored; rows of a provider whose window (`start`, `end`)
    doesn't hold `t0` are left out.
  - `counting_off(folder: Path = paths.GATE_LEDGER_DIR) -> list[tuple[float, float | None]]`: from the ledger's
    `counting_off` and `counting_on` rows, each span `(start, end)`; a `counting_on` with `seconds` makes
    `(t - seconds, t)` even when its `counting_off` row is missing; an off with no on after it ends None. *(2026-11-12,
    at the final review: only the rows whose `who` is `front`, the front's usage rows' writer; the gate's own ledger,
    `who` `gate`, is left out, since its on would otherwise close the front's off.)*
  - `with_estimates(rows: list[Row]) -> list[Row]`: the design's cache estimate, below.
  - `@dataclass class Store`: `rows: list[Row]`, `counting_off: list[tuple[float, float | None]]`, `bad_lines: int`,
    `last_import: float | None` (from `opencode.imported`, Task U5), `imported: int`.
  - `read(book: PriceBook) -> Store`: all three folders, with estimates applied.
- `paths.USAGE_SESSIONS_DIR = Path(os.environ.get("SPARK_USAGE_SESSIONS", "/var/lib/local-ai/usage-sessions"))`.

The estimate (part 2's rule, the one algorithm the signature doesn't fix):

```python
def with_estimates(rows):
    out, previous = [], {}  # session -> the previous row's P, by t0
    for row in sorted(rows, key=lambda r: (r.session or "", r.t0, r.id)):
        if row.source == "front" or row.session is None or row.p is None:
            out.append(row)
            continue
        before = previous.get(row.session)
        previous[row.session] = row.p
        if row.c is not None:
            out.append(row)
            continue
        c = before if before is not None and row.p >= before else 0
        out.append(dataclasses.replace(row, c=c, c_estimated=True))
    return out
```

- [ ] **Step 1: Write the failing tests:**
  - `test_front_rows_read_every_month_and_skip_bad_lines`: two month files of invented rows plus a torn line, a line
    with `v: 2` and a line that isn't JSON; three rows come back, `bad_lines == 3`, and a row's absent `c` reads None.
  - `test_imported_rows_last_line_wins`: the same `mid` twice with different `out`; one row, the second's counts.
  - `test_imported_normalization`: an invented `spark-flash-next` row `in=10, cr=90, cw=0, out=5, reas=3` gives
    `p == 100, c == 90, o == 8`; the same as `spark-sglang` gives `c is None`.
  - `test_estimate_follows_the_session`: three sglang rows of one session with *P* 1000, 1500, 400 give *C* 0, 1000,
    0, each `c_estimated`; a second session's first row gives 0; a row with a reported `c` is untouched, and the next
    row of its session estimates from its *P*.
  - `test_counting_off_spans`: an off/on pair, an on with `seconds` and no off, and an off still open.
  - `test_a_provider_outside_its_window_is_left_out`: a row of `spark-sglang` dated before 2026-10-15.
  - `test_missing_folders_read_as_empty`: each folder absent gives no rows and no error; an unreadable one raises
    `OSError` (the summary reports it).
- [ ] **Step 2: Run** `make test ARGS="spark/tests/test_meter_store.py"`. Expected: FAIL, module not found.
- [ ] **Step 3: Implement** `store.py` and the path.
- [ ] **Step 4: Run** it. Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark): 🤖 the meter reads the front's rows, the imported rows and counting's gaps`.

### Task U3 [Mac]: pricing a row, and `spark usage rows`

**Files:**
- Create: `spark/src/spark/meter/pricing.py`
- Modify: `spark/src/spark/meter/__init__.py` (`register`), `spark/src/spark/cli.py` (register it after `commands`)
- Test: `spark/tests/test_meter_pricing.py`

**Interfaces:**
- Consumes: `prices.PriceBook`, `prices.line_at`, `store.Row`, `store.read`.
- Produces, in `spark.meter.pricing`:
  - `@dataclass(frozen=True) class Priced`: `usd: Decimal | None`, `tier: str | None`, `line: str | None`
    (`PriceLine.label`), `band: str | None` (`"base"` or `"above"`), `why: str | None`, one of `WHYS`.
  - `WHYS = ("not_done", "no_counts", "unmapped", "none", "no_line", "cache_unknown")`
  - `price(book: PriceBook, row: Row) -> Priced`, checked in ~~`WHYS`' order: `end != "done"`; `p` or `o` None; no
    mapping line; tier `none`; no line at `t0`; `c` None.~~ this order: `end != "done"`; tier `none`; `p` or `o`
    None; no mapping line; no line at `t0`; `c` None. *(2026-11-12, at the final review: tier `none` moved before
    the counts, so a speech row, which carries no counts, is `none`, unpriced on purpose, and never `no_counts`,
    which the summary counts as unpriced. `WHYS` names the reasons, not their order.)*
  - `row_json(row: Row, priced: Priced) -> dict`: the row's fields, `c_est` for `c_estimated`, and the four priced
    fields, `usd` a float rounded to 6 places; None values left out.
- `spark.meter.register(subparsers)` adds `usage` with sub-verbs; this task's is `rows [--since DATE|EPOCH]`, one
  JSON line per row in `t0` order (`--since` takes an ISO date, read as 00:00 UTC, or epoch seconds).

The price, §3's formula at the line in effect at `t0`, with `f` the mapping's factor: rates = `above` when the line
has one and `p × f > above_over`, else `base`; uncached = `(p − c) × f`, charged at `write_5m` when the tier's
`uncached_as_writes`, else `input`; `usd = (uncached × in + c × f × cache_read + o × f × output) / 1_000_000`.

- [ ] **Step 1: Write the failing tests:**
  - `test_opus_row`: `p=1_000_000, c=900_000, o=10_000` at 2026-11-01 gives `usd == Decimal("0.78")`
    (0.1M × 4 + 0.9M × 0.20 + 0.01M × 20 = 0.40 + 0.18 + 0.20), `line == "opus@2026-09-22"`.
  - `test_sonnet_cache_read_changes_on_its_day`: the same counts at 2026-10-06T23:59:59Z and 2026-10-07T00:00:00Z
    differ by exactly `0.9 × (0.20 − 0.10) = 0.09`.
  - `test_haiku_band_on_the_whole_prompt`: `p=100_000` is `base`, `p=100_001` is `above` with every rate switched; a
    factor of 2 puts `p=60_000` above.
  - `test_haiku_4_5_before_10_07`: a haiku row on 2026-10-01 takes `Haiku 4.5`'s rates.
  - `test_factor_scales_every_kind`: factor 0.5 halves the price.
  - `test_uncached_as_writes`: with the switch on for opus, the uncached part is charged at 5.
  - `test_why_each`: an `error` row is `not_done` with `usd None`; `cut` and `refused` likewise; a row with no `o`
    is `no_counts`; an unknown model `unmapped`; an embedding `none`; an opus row before 2026-09-22 `no_line`; a
    front row with `c` None `cache_unknown`. Never `usd == 0` for any of them.
  - `test_rows_verb_prints_json_lines` (CLI, with `SPARK_USAGE_DIR`, `SPARK_USAGE_SESSIONS` and `SPARK_GATE_LEDGER`
    pointed at fixtures): `cli.main(["usage", "rows"])` returns 0 and prints one JSON object per line, in `t0` order;
    `--since 2026-11-01` drops the older ones; no line holds a key other than the row's and the priced fields.
- [ ] **Step 2: Run** `make test ARGS="spark/tests/test_meter_pricing.py"`. Expected: FAIL.
- [ ] **Step 3: Implement** `pricing.py`, `register` and the `rows` handler, which imports `store` and `pricing` when
  run, never at import.
- [ ] **Step 4: Run** it, then `make test ARGS="spark/tests/test_cli.py spark/tests/test_tested_against.py"`.
  Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark): 🤖 spark usage rows: every row priced at Claude's rates when read`.

### Task U4 [Mac]: `spark usage summary`

**Files:**
- Create: `spark/src/spark/meter/summary.py`
- Modify: `spark/src/spark/meter/__init__.py` (the `summary` verb), `spark/src/spark/paths.py` (`METER_PRIVATE`)
- Test: `spark/tests/test_meter_summary.py`

**Interfaces:**
- Consumes: `store.Store`, `pricing.price`, `prices.PriceBook`.
- Produces, in `spark.meter.summary`:
  - `SUMMARY_VERSION = 1`
  - `@dataclass(frozen=True) class Private`: `price_usd: Decimal | None`, `zone: str | None`.
  - `read_private(path: Path = paths.METER_PRIVATE) -> Private`: absent gives both None; present and unreadable,
    not YAML, a price ≤ 0 or a zone `zoneinfo` doesn't know raises `ValueError` naming the file and the field, never
    a value.
  - `zone_of(private: Private) -> tuple[tzinfo, str, str]`: the zone, its name and `"private_file"` or `"system"`. The
    system's is `/etc/localtime`'s link target after `zoneinfo/` when it is one, else `"localtime"`, with
    `datetime.now().astimezone().tzinfo`.
  - `summarize(store: Store, book: PriceBook, private: Private, now: float, *, hours: bool = False) -> dict`: the
    design's contract, below.
  - `words(summary: dict) -> str`: the plain-words form `spark usage summary` prints without `--json`.
- `paths.METER_PRIVATE = Path(os.environ.get("SPARK_METER_PRIVATE", Path.home() / ".config/local-ai/meter.yaml"))`.
- The verb: `spark usage summary [--json] [--hours]`.

The contract, the design's *The summary's JSON* with these settled:
- `periods.today`, `this_month`, `last_month` by `t0` in the zone; `all_time` every row. Each has `label`
  (`YYYY-MM-DD`, `YYYY-MM`, `YYYY-MM`, `"all"`), `usd` (priced rows, estimates included; float, 4 places),
  `usd_estimated` (the priced rows whose `c` is estimated), `requests` (every row), `p`, `c`, `o` (sums over the rows
  that have them; `c` holds estimates), and `unpriced` (rows whose `why` is `no_counts`, `unmapped`, `no_line` or
  `cache_unknown`). `all_time` adds `share_of_price`: its `usd` over the private file's price, 4 places, null without
  one.
- `by_model`: for `this_month` and `all_time`, one entry per model with `model`, `tier` (or null), `period`, `usd`,
  `usd_estimated`, `requests`, `p`, `c`, `o`; sorted by `usd` falling.
- `flags`, over this month's rows: `unmapped` ({model: rows}), `interrupted` (`cut` and `gone`), `errors`,
  `cache_unknown`, `dropped` (the sum of the front rows' `dropped`), `bad_lines`.
- `coverage`: `counting_off`, the spans that end this month or are still open; `imported.opencode`:
  `{"last_import": …, "rows": …}`; `not_counted`: `["by-hand engines: counted from heartsbane's OpenCode only"]`.
- `made_at` (`now`), `zone`, `zone_from`, `counter_start` (the first front row's `t0`, or null), `last_row_at` (the
  newest `t0` of any row, or null), `electricity: null`, `break_even: null`.
- `hours` only with `--hours`: a list of `{"hour": <UTC epoch of the hour>, "source", "key", "provider", "model",
  "usd", "requests", "p", "c", "o"}`, for the dashboard later.

- [ ] **Step 1: Write the failing tests** (an invented `Store` built in the test; `now` fixed):
  - `test_periods_follow_the_zone`: zone `America/New_York`; a row at 2026-10-31 23:30 local (UTC the next day)
    counts in `last_month` when `now` is in November, not `this_month`; `today` holds only rows of the local date.
  - `test_dst`: rows either side of 2026-11-01's fall-back hour land on the right local dates.
  - `test_usd_includes_the_estimate`: one reported and one estimated row; `usd` is their sum, `usd_estimated` the
    second's.
  - `test_unpriced_and_flags`: an `error`, a `cut`, an unknown model, an embedding and a front row with no `c`; the
    error and cut count in `requests` and `flags` but not `unpriced`; the embedding counts nowhere but `requests`;
    `flags.unmapped == {"made-up-model": 1}`.
  - `test_share_of_price`: price 4000 and an all-time `usd` of 40 give 0.01; no private file gives null and
    `zone_from == "system"`.
  - `test_private_file_errors_never_show_values`: a file with `price_usd: -5` raises `ValueError` whose text holds
    `price_usd` and the path, not `-5`.
  - `test_by_model_sorted`: two models this month, the dearer first.
  - `test_hours_only_when_asked`: no `hours` key by default; with it, an hour's entry sums its rows.
  - `test_summary_verb` (CLI, fixtures as in Task U3): `cli.main(["usage", "summary", "--json"])` prints one JSON
    object with `v == 1` and every top-level key of the contract; without `--json`, lines starting `This month`,
    `Today`, `Last month`, `All time`.
  - `test_empty_store`: no rows give zeros, `counter_start` and `last_row_at` null, and no error.
- [ ] **Step 2: Run** `make test ARGS="spark/tests/test_meter_summary.py"`. Expected: FAIL.
- [ ] **Step 3: Implement** `summary.py` and the verb.
- [ ] **Step 4: Run** it. Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark): 🤖 spark usage summary: the spend at Claude's prices by day, month and all
  time, in Dan's zone`.

### Task U5 [Mac]: `spark usage import`

**Files:**
- Create: `spark/src/spark/meter/importer.py`
- Modify: `spark/src/spark/meter/__init__.py` (the `import` verb)
- Test: `spark/tests/test_meter_import.py`

**Interfaces:**
- Consumes: `paths.USAGE_SESSIONS_DIR`.
- Produces, in `spark.meter.importer`:
  - `FIELDS = ("v", "src", "mid", "sid", "prov", "model", "t0", "t1", "in", "out", "reas", "cr", "cw", "end")`, every
    one required; `ENDS = ("done", "error", "cut")`.
  - `check(obj: object, now: float) -> str | None`: None for a good row, else the reason in fixed words (`"field"`,
    `"text"`, `"count"`, `"time"`, `"end"`, `"version"`, `"source"`), never the value.
  - `@dataclass(frozen=True) class Verdict`: `new`, `changed`, `unchanged`, `refused: int`, `reasons: dict[str, int]`;
    `line() -> str`, e.g. `import: 12 new, 0 changed, 340 unchanged, 1 refused (text 1)`.
  - `import_rows(lines: Iterable[str], folder: Path, now: float) -> Verdict`.
- The verb: `spark usage import --source opencode`, rows on stdin; prints `Verdict.line()`; exit 0 when the batch was
  read and stored, 1 when the folder can't be written (cli.main's `OSError` words). *(2026-11-12, at the final review:
  and exit 3 when anything was refused, a batch refused whole included, after the good rows are stored; the verdict
  line is unchanged. A push with refusals then fails loudly: `make usage-push` exits non-zero, and spark-menu keeps
  its watermark and says the Spark refused rows, so they are sent again rather than lost.)*

Rules: `v == 1`; `src == "opencode"`; strings `mid`, `prov`, `model` match `[A-Za-z0-9_.:-]{1,64}`; `sid` is
`[0-9a-f]{16}`; counts are `int` (never `bool`), 0 ≤ n < 2**53; `t0` and `t1` numbers with 2026-01-01T00:00Z ≤ t0 ≤
t1 ≤ `now` + 86400; no other field. A line over 4096 bytes, or the batch past 200,000 lines, is refused. The whole
batch is read and checked before anything is written, so a cut pipe's torn last line is one refusal and the rest go
in. New and changed rows are appended to `opencode.jsonl` (created `0640`) as one `write()` under an `fcntl.flock` on
`.import.lock` in the folder; an unchanged row is not written. ~~Every run that stores writes `opencode.imported`, the
time,~~ A run whose batch had no refusals, an empty one included, writes `opencode.imported`, the time, through a temp
file and `os.replace`. *(2026-11-12, at the final review: written only when nothing was refused, Task U5's ruling.)*

- [ ] **Step 1: Write the failing tests:**
  - `test_new_then_unchanged`: two invented rows imported twice; the first verdict `2 new`, the second `2 unchanged`,
    and the file holds two lines.
  - `test_changed_row_appends`: the same `mid` with a larger `out` is `1 changed`, and `store.imported_rows` reads the
    new counts.
  - `test_each_refusal` (parametrized): an extra field, a missing one, `mid` with a space, `model` holding `/`,
    `sid` of 15 hex, a negative count, a `True` count, `t1 < t0`, a `t0` in 2025, `end: "stop"`, `v: 2`,
    `src: "pi"`; each is `1 refused` with its reason word, and nothing of it is in the file.
  - `test_torn_last_line`: three lines, the last cut mid-object; `2 new, 1 refused`.
  - `test_refusal_words_hold_no_value`: a row whose `model` is `secretproj/q3`; the verdict line doesn't hold
    `secretproj`.
  - `test_file_mode`: the created file is `0640` under a `0o022` umask.
  - `test_import_verb` (CLI, `SPARK_USAGE_SESSIONS` at a tmp folder, stdin from a `StringIO`): prints the verdict
    line, exit 0.
- [ ] **Step 2: Run** `make test ARGS="spark/tests/test_meter_import.py"`. Expected: FAIL.
- [ ] **Step 3: Implement** `importer.py` and the verb.
- [ ] **Step 4: Run** it, and Task U2's tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark): 🤖 spark usage import: the Mac's rows for the by-hand engines, checked and merged
  by message`.

### Task U6 [Mac]: `spark usage harvest`, run on the Mac

**Files:**
- Create: `spark/src/spark/meter/harvest.py`
- Modify: `spark/src/spark/meter/__init__.py` (the `harvest` verb)
- Test: `spark/tests/test_meter_harvest.py` (an invented database, built in the test with OpenCode's
  `session_message` table as Task U6's listing reads it)

**Interfaces:**
- Consumes: `prices.load`, `prices.by_hand`, `importer.check` (the harvest checks its own rows before printing).
- Produces, in `spark.meter.harvest`:
  - `OPENCODE_DB = Path(os.environ.get("SPARK_OPENCODE_DB", Path.home() / ".local/share/opencode/opencode.db"))`
  - `TESTED_WITH = "2.2.7"`, OpenCode's version the reader was written against (for the error's words).
  - `class HarvestError(ValueError)`.
  - `harvest(db: Path, providers: list[Provider], since: float | None) -> Iterator[dict]`: one row per message, in
    `importer.FIELDS`.
- The verb: `spark usage harvest --source opencode [--db PATH] [--since EPOCH]`, rows to stdout as JSON lines and one
  line to stderr, `harvest: N rows`; exit 1 with `HarvestError`'s words for a missing file or a database without
  `session_message`. *(2026-11-12, at the final review: also exit 1 when OpenCode has assistant messages but none of
  them holds `$.model.providerID`, `$.time.completed` or `$.tokens.input`, the words naming the path and nothing from
  the database, so a format change never reads as "0 rows". No assistant messages at all is still 0 rows, exit 0.)*

The query, ~~the only one it runs~~ the only one that extracts a field *(2026-11-12, at the final review: a table
check and the canary's `LIMIT 1` queries, one for any assistant message and one per path, run before it, and each
returns only 1)*, on `sqlite3.connect(f"file:{db}?mode=ro", uri=True)`; the provider list is bound as parameters, one
`?` each, from `by_hand(book, "opencode")`:

```sql
SELECT id, session_id, type,
       json_extract(data, '$.model.providerID'), json_extract(data, '$.model.id'),
       json_extract(data, '$.time.created'), json_extract(data, '$.time.completed'),
       json_extract(data, '$.tokens.input'), json_extract(data, '$.tokens.output'),
       json_extract(data, '$.tokens.reasoning'),
       json_extract(data, '$.tokens.cache.read'), json_extract(data, '$.tokens.cache.write'),
       json_extract(data, '$.error.type')
FROM session_message
WHERE type IN ('assistant', 'compaction')
  AND json_extract(data, '$.model.providerID') IN (?, ?)
  AND time_updated >= ?
ORDER BY time_created
```

Each result becomes a row: `mid` the id; `sid` the first 16 hex of SHA-256 of the session id; `prov`, `model`; `t0`
created ÷ 1000; `t1` completed ÷ 1000, or `t0` for a compaction, which has no completed time; the five counts; `end`
`cut` for error type `aborted`, `error` for any other error, else `done`. Skipped: an assistant message with no
completed time (still running), one with no token counts, one outside its provider's window, and one whose row
`importer.check` refuses (counted on stderr as `skipped N`, never shown). `--since` is compared with `time_updated`
in milliseconds.

- [ ] **Step 1: Write the failing tests:**
  - `test_rows_from_an_invented_database`: three messages (an sglang assistant, a flash-next compaction, an `aborted`
    sglang message with tokens) plus a `spark` provider message and a user message; three rows, with `in`, `out`,
    `reas`, `cr`, `cw` as stored, `t0`/`t1` in seconds, `end` `done`, `done`, `cut`; the compaction's `t1 == t0`.
  - `test_nothing_but_numbers_and_ids_leaves`: the invented messages carry a `content` and an `error.message` holding
    a marker word, and the session a title holding another; neither marker is in any printed row, and `sid` is not
    the session id.
  - `test_running_message_skipped`: an assistant message with no `time.completed`.
  - `test_since`: a message updated before `--since` is left out.
  - `test_read_only`: after a harvest the database file's bytes and mtime are unchanged, and a harvest runs while
    another connection holds a write transaction open in WAL mode.
  - `test_missing_and_changed_database`: no file, and a database without `session_message`, each raise
    `HarvestError` naming OpenCode 2.2.7, and the verb exits 1.
  - `test_rows_pass_the_import_check`: every harvested row gets `importer.check(row, now) is None`.
- [ ] **Step 2: Run** `make test ARGS="spark/tests/test_meter_harvest.py"`. Expected: FAIL.
- [ ] **Step 3: Implement** `harvest.py` and the verb.
- [ ] **Step 4: Run** it. Then the real check, which prints only a verdict, **on the Mac**:
  `bash -c 'uv run --frozen --project spark spark usage harvest --source opencode 2>&1 >/dev/null | tail -1'`
  shows `harvest: N rows`; and piping the harvest to a local import into a tmp folder
  (`SPARK_USAGE_SESSIONS=$(mktemp -d)`) gives `0 refused`. Neither output goes into the repo or a commit.
  *(Corrected 2026-11-12, after Task U6 ran it: written without `bash -c`, the Mac's zsh sends stdout to the pipe
  as well as to `/dev/null` (its `MULTIOS` option), so one harvested row (numbers and ids, no text) reached the
  session's context. Nothing was written from it.)*
- [ ] **Step 5: Commit** `feat(spark): 🤖 spark usage harvest: the fast coder's and Flash-Next's tokens from OpenCode,
  read on the Mac`.

### Task U7 [Mac]: the box's parts: the folder, doctor, the Makefile

**Files:**
- Modify: `stack/host/bootstrap.sh` (after the `gate-ledger` line: `run install -d -o "$ADMIN_USER" -g spark-admin -m
  2750 /var/lib/local-ai/usage-sessions`), `spark/src/spark/doctor.py`, `Makefile`
- Test: `spark/tests/test_bootstrap.py`, `spark/tests/test_doctor.py`, `spark/tests/test_makefile.py`

**Interfaces:**
- Consumes: `doctor._meter_folder`, `prices.load`, `summary.summarize`, `store.read`.
- Produces: doctor checks `usage sessions` (`_meter_folder` with owner `(getpass.getuser(), "spark-admin")`) and `meter
  prices` (the files load, every registry model has a mapping line, and a summary over the live folders runs: its
  verdict names missing models or the error's class, never a figure); `make usage` (**on the Spark**: `spark usage
  summary`) and `make usage-push` (**on the Mac**: the harvest ~~piped to~~ sent to `ssh brightroar
  /opt/local-ai/app/.venv/bin/spark usage import --source opencode`, for a push by hand). *(2026-11-12, at the final
  review: through a temp file, sent only when the harvest exited 0, never a pipe, since make's sh has no `pipefail`;
  Task U7's ruling. The target exits non-zero when either side fails, the import's refusals included.)*

- [ ] **Step 1: Write the failing tests:** `test_bootstrap.py`: `install_d_line("/var/lib/local-ai/usage-sessions")`
  holds `-g spark-admin -m 2750` and `-o` the dry run's user; `test_doctor.py`, with `FakeProbe`: a missing
  `usage-sessions` fails with `make bootstrap`; a registry model with no mapping line fails `meter prices` naming it; a
  summary that raises fails naming the class only; both pass on good fixtures, and `checks()` includes both;
  `test_makefile.py`: `make -n usage` and `make -n usage-push` print the commands above.
- [ ] **Step 2: Run** `make test ARGS="spark/tests/test_bootstrap.py spark/tests/test_doctor.py
  spark/tests/test_makefile.py"`. Expected: the new tests FAIL (on the Mac, the bootstrap file's known failures stand,
  as the counter's round found).
- [ ] **Step 3: Implement** them.
- [ ] **Step 4: Run** the same. Expected: the new tests PASS.
- [ ] **Step 5: Commit** `feat(stack): 🤖 the meter's imported rows get their folder, and doctor checks the price files`.

### Task U8 [Mac]: the docs that change with it

**Files:** Modify `website/scenarios/s26-what-it-cost.md` (*What I see*: `make usage`, the menu's section, the two
doctor lines; *not yet deployed* until 44l), `website/architecture.qmd` (dashed: the Mac's harvest → SSH → `spark usage
import` → `usage-sessions`, and spark-menu → SSH → `spark usage summary`; S26's row in the scenario table),
`website/reference/projects.md` (spark-menu's row gains `spark usage summary --json`, contract v1, and `spark usage
import`; the CLI's contract row names the `usage` verbs), `website/design/usage-meter.md` (dated annotations, never
deletions: §3's price lines and their dates, §4's 60-s call of its own, §7's slice, §9's order, *Open for later*'s
by-hand SGLang item pointing to the first numbers' part 5), `CLAUDE.md` and `README.md` §My environment (the private
file `~/.config/local-ai/meter.yaml` on the Spark, `0600`; `/var/lib/local-ai/usage-sessions`), and the design's own
status (being built, this plan).

- [ ] **Step 1:** each edit, correcting rather than deleting, dated 2026-11-12.
- [ ] **Step 2:** `make docs` on the Mac renders, and `make test ARGS="spark/tests/test_scenarios.py"` passes.
- [ ] **Step 3:** commit `docs(website): 🤖 the meter's first numbers on the site: S26, the diagrams and the projects
  page, dashed until deployed`.

***

### Task U9 [Mac, dotfiles]: the summary poll

**Files:**
- Create: `mac-heartsbane/spark-tunnel/lib/usage.sh`, `tests/test_usage.sh`, `tests/fixtures/usage-summary.json`
  (invented, following the contract), `tests/fixtures/usage-summary-v2.json`
- Modify: `lib/poll.sh` (`poll_once` calls `usage_poll_start` after `_poll_disk_start`, on an answered tick),
  `spark-tunnel` (sources `lib/usage.sh` after `lib/poll.sh`)

**Interfaces:**
- Consumes: `with_timeout`, `on_spark`, `SPARK_BIN`, `STATE_DIR`, `note`, the disk run's pid pattern.
- Produces, in `lib/usage.sh`: `USAGE_JSON="$STATE_DIR/usage.json"` (`{"at": <the Mac's epoch>, "summary": {…}}`),
  `USAGE_ERROR="$STATE_DIR/usage.error"` (one line: `not deployed`, `failed (exit N)` or `malformed`),
  `USAGE_EVERY=60`, `USAGE_BOUND=20`, `usage_poll_start` (starts `_usage_run` in the background unless one runs or
  the last started under `USAGE_EVERY` ago).

`_usage_run` runs `with_timeout "$USAGE_BOUND" on_spark "$SPARK_BIN" usage summary --json`. Output that `jq` reads as
an object with `v == 1` is written with `at` through a temp file and `mv`, and `usage.error` removed; otherwise the old
`usage.json` stays and `usage.error` says why: `not deployed` when the output holds `invalid choice: 'usage'`.

- [ ] **Step 1: Write the failing tests** in `test_usage.sh`, with the fake ssh and `SPARK_TUNNEL_SPARK_BIN` at a fake
  `spark` that prints a fixture: a good summary lands in `usage.json` with `at`; a second tick within 60 s doesn't
  call the fake again (its call log); a `v: 2` summary keeps the old file and writes `malformed`; a fake exiting 2
  with `invalid choice: 'usage'` writes `not deployed`; a fake that hangs is cut at the bound and the tick returns
  meanwhile; with the master down nothing is called.
- [ ] **Step 2: Run** `make -C mac-heartsbane/spark-tunnel test`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** them.
- [ ] **Step 4: Run** it. Expected: PASS, every older test too.
- [ ] **Step 5: Commit** `feat(spark-tunnel): fetch the Spark's usage summary every minute, keeping the last good one`.

### Task U10 [Mac, dotfiles]: the harvest push

**Files:** Modify `lib/usage.sh`; Test `tests/test_usage.sh`, a fake `uv` in `tests/fakebin/`.

**Interfaces:**
- Produces: `SPARK_TUNNEL_UV` (default `/opt/homebrew/bin/uv`: launchd's PATH has no Homebrew), ~~`SPARK_TUNNEL_LOCAL_AI`
  (default `$HOME/git/hub/local-ai`)~~ *(2026-11-12, at the final review: no new variable; the push reuses spark-menu's
  existing `LOCALAI_DIR`, overridden by `SPARK_LOCALAI_DIR` and read when called, as `lib/forwards.sh` does; Task
  U10's ruling)*, `HARVEST_EVERY=300`, `HARVEST_BOUND=90`, `HARVEST_SINCE="$STATE_DIR/harvest.since"`,
  `HARVEST_LAST="$STATE_DIR/harvest.last"` (the time and the import's verdict line), `HARVEST_NOTE="$STATE_DIR/harvest.note"`
  (why the last push failed, in words), `usage_push_start` (called beside `usage_poll_start`).

The push: `"$UV" run --frozen --project "$LOCAL_AI/spark" spark usage harvest --source opencode [--since S]` ~~piped to
`on_spark -i "$SPARK_BIN" usage import --source opencode`, the whole pipeline under one `with_timeout
"$HARVEST_BOUND"`~~ into a `0600` temp file, which `on_spark -i "$SPARK_BIN" usage import --source opencode` reads
only when the harvest exited 0, both steps under one `with_timeout "$HARVEST_BOUND"` *(2026-11-12, at the final
review: never a pipe, Task U10's ruling; `$LOCAL_AI` is `LOCALAI_DIR`, above)*. On success `harvest.since` becomes
the push's start less 3600 and `harvest.note` goes; on failure `harvest.note` says which side failed (`the Mac's clone
has no spark usage harvest`, `the Spark has no spark usage import yet`, `OpenCode's database: <the harvest's last
stderr line>`), and `note` logs it once per kind. *(2026-11-12, at the final review: every failure keeps
`harvest.since`, so the next push sends the same rows again; when the import exits 3, as it does when the Spark
refused any row, the note reads `the Spark refused N rows`, N from its verdict line, or `the Spark refused the batch`
for a batch refused whole or a verdict that can't be read.)*

- [ ] **Step 1: Write the failing tests:** a fake `uv` printing two invented rows and a fake `spark` whose `usage
  import` saves its stdin and prints a verdict; one push saves both rows and writes `harvest.last` and
  `harvest.since`; a second within 300 s doesn't run; the next passes `--since`; a fake `uv` exiting 2 with
  `invalid choice: 'usage'` writes the clone's note and logs it once over two failures; the fake import failing
  writes the Spark's note; with the master down nothing runs.
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** it.
- [ ] **Step 4: Run** them. Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark-tunnel): push the fast coder's token counts to the Spark every five minutes`.

### Task U11 [Mac, dotfiles]: the usage section, and the docs

**Files:** Modify `lib/panel.sh` (`panel_usage`, called from `panel_body` after `panel_disk`), `README.md` (*The
panel* gains *Usage at Claude's prices*; *What it will never do* gains pricing anything; *What's actually been
tested*), `mac-heartsbane/AGENTS.md` (the new file), `docs/superpowers/plans/2026-10-22-spark-menu-rulings.md` (R66
on: the 60-s call of its own, the 5-min push, greyed after 3 minutes, the month-name rule, uv by full path); Test
`tests/test_panel.sh`.

**Interfaces:**
- Consumes: `usage.json`, `usage.error`, `harvest.note`, `item`, `mono`, `spark_item`, `is_num`, `GREY`, `AMBER`.
- Produces: `panel_usage`; `usd_text X` (`$0.42`, `$12.30`, `$1,234`; `—` for a non-number); `tok_text N` (`950`,
  `12.3K`, `4.5M`, `1.2B`).

The section, as the design's part 4 draws it: a header `Spark usage at Claude prices` with `as of HH:MM`; `This month`
with `usd` and `p` in · `o` out; `Today`; `Last month`; `All time` with `share_of_price` as
`N.N % of the Spark (before electricity)` or `—`; `~$X of it estimates the fast coder's cache` when all time's
`usd_estimated` > 0; one flags line (`⚠map` and up to two model names then `+N`, interrupted, errors, cache unknown,
dropped, ~~the last counting-off span as `HH:MM–HH:MM`~~), amber unless old; `By model ▸` with this month's entries as
`--` rows (model, tier, `usd`, tokens). *(2026-11-12, at the final review: the flags line also gives `N unpriced`,
this month's `unpriced` as the summary gives it, after the errors; after `counting off` it lists every span the
summary gives, not only the last, each `HH:MM–HH:MM`, with its date when not today (`Nov 2 13:58–14:01`), and
`since HH:MM` for one still open, so an open span alone reads `counting off since HH:MM`; past 3, the last 3 and
`+N more`. By model shows `—`, not `$0.00`, for a model whose tier is null or `none`. A summary whose `v` is a whole
number above 1 is recorded by the poller as `newer (vN)` in `usage.error`, from which the panel prints its v2 line,
while any other `v` that isn't 1, a non-integer such as 1.5 included, stays `malformed`; with no
`usage.json`, any other `usage.error` but `not deployed` prints one grey line,
`Spark usage: the summary failed (<reason>)`; and `usage.json` and `usage.error` are written under `umask 077`.)*
`harvest.note`, when present, is a grey line `fast coder's tokens: <note>`; a `last_import` over 15 minutes old while
the tunnel is on is a grey line `fast coder's tokens last sent Nm ago`. `usage.json` older than 180 s greys the
section and the header says how old. When the summary's `this_month.label` isn't the Mac's month in the summary's
`zone` (`TZ=<zone> date +%Y-%m`), each line names its period's `label` instead of "This month", "Today" and "Last
month". No `usage.json` and `usage.error` `not deployed`: one grey line `Spark usage: not on the Spark yet`. Every
string from the summary goes through `spark_item` with control characters stripped (`tr -d '\000-\037\177'`).

- [ ] **Step 1: Write the failing tests** in `test_panel.sh`, from `usage-summary.json`: every line above appears in
  `status` (plain) with the fixture's figures formatted; an `at` 200 s old greys every line of the section; a
  `this_month.label` of a month before the Mac's shows `2026-10` where `This month` was; a model name holding `|` and
  `\x07` prints neither; `share_of_price: null` shows `—`; a `v: 2` file shows one grey line, `Spark usage: summary
  v2 is newer than this menu reads`, never figures; `not deployed` shows its line; the menu output stays a whole menu (`swiftbar_keys` passes).
- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** `panel_usage` and the docs.
- [ ] **Step 4: Run** the full suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat(spark-tunnel): the usage section: the Spark's spend at Claude's prices, from its summary`.

***

### Task U12 [Spark]: the tests, the merge, the plan

On the Spark, in the `phase-2a` clone, once Dan has OK'd pushing `swiftbar-costs` from the Mac (after the denylist
scan, *Before every push*):

- [ ] **Step 1:** `git fetch origin && git switch swiftbar-costs` then `make test`: the full suite, the Linux-only tests
  included. Every test passes.
- [ ] **Step 2 [Dan's OK]:** `git switch phase-2a && git merge --no-ff swiftbar-costs`.
- [ ] **Step 3:** the plan's edits, as Task 44k's were: `website/design/plan.md` (a Revisions line: the summary, the
  price files and spark-menu's section come forward from 2b to 2a; Phase 4a's line gains the fast coder behind the
  front, the design's part 5; *(2026-11-12, at the final review)* and two Backlog lines: **the summary re-reads and
  re-prices every row of every month each minute**, so it needs a measured trigger, its run time on the Spark checked
  as the rows grow, and work on it (a cache, or a month's totals kept once the month has closed) before it nears
  spark-menu's 20-second bound; and **after a rebuild of the Spark, the Mac's `harvest.since` stays**, so the first
  pushes send only the last hour or so: run `make usage-push` on the Mac once to send the fast coder's whole history
  again; it sends every row, which the import merges by message id, so sending one twice changes nothing),
  `website/design/phase-2a.md` (a pointer to this plan for Tasks U1–U14 between 44k and 44l; Task 44l's steps gain
  U13's), and the box fact the design left unverified: whether the running SGLang has
  `--enable-cache-report` on its command line, by a count only, since its command line may hold its key
  (`pgrep -af sglang.launch_server | grep -c enable-cache-report`; not yet run on the box, so run once before relying
  on it). A count of 1 means `providers.yaml`'s `spark-sglang` line gains a `cache_from` date, after the soak. Commit
  `docs(plan): 🤖 the meter's first numbers join Phase 2a before the counter's deploy`.

### Task U13 [Spark] [Dan]: deploy with Task 44l, and the private file

- [ ] **Step 1 [Dan], on the Spark:** Task 44l's steps, as written; its `make bootstrap` (Step 4) now also makes
  `/var/lib/local-ai/usage-sessions`, and `stat -c '%U:%G %a' /var/lib/local-ai/usage-sessions` shows `<Dan's
  login>:spark-admin 2750`.
- [ ] **Step 2 [Dan], on the Spark:** the private file, its values typed by Dan, never pasted into a session:
  `umask 077 && mkdir -p ~/.config/local-ai && nano ~/.config/local-ai/meter.yaml`, holding two lines, `price_usd:`
  the Spark's all-in price in US dollars and `zone:` an IANA zone name; then `stat -c '%a' ~/.config/local-ai/meter.yaml`
  shows `600`. Its line goes in the vault's entry note.
- [ ] **Step 3 [Spark]:** `make doctor` passes `usage sessions` and `meter prices`; `make usage` prints the four
  periods (read by Dan, never copied into the repo).
- [ ] **Step 4 [Spark]:** `changelog.md` and README §Current state; the Architecture page's new paths solid; S26's
  `status: built`; commit `docs(machine): 🤖 the meter's first numbers are running`.

### Task U14 [Mac]: the menu shows numbers

- [ ] **Step 1, on the Mac:** `git -C ~/git/hub/local-ai pull` on `phase-2a`, so the clone has `spark usage harvest`.
- [ ] **Step 2, on the Mac:** in the dotfiles, `spark-menu-usage` merged into `main` (Dan's OK), then `make -C
  ~/git/private/dotfiles/mac-heartsbane/spark-tunnel reload`.
- [ ] **Step 3:** within five minutes the dropdown shows the section and no `fast coder's tokens:` note. A
  `notice/spark-menu` issue in this repo records the new contract, once Dan OKs opening it (it is public).
