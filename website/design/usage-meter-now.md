---
title: "The usage meter's first numbers — design"
description: "Bringing the usage meter's summary, its prices and spark-menu's usage section forward to the counter's deploy, and counting the by-hand fast coder from the Mac's OpenCode in the meantime, so the Spark's spend at Claude's prices shows in the menu bar's dropdown from the day counting starts."
date: 2026-11-12
---

# The usage meter's first numbers — design

**Status: approved by Dan on 2026-11-12** ("plan look sgood"); being built. It amends [the usage meter's design](usage-meter.md),
which stays the reference for everything not named here. Dan's words and the questions they answered
are in [its questions and answers](usage-meter-qa.md), *The menu's numbers now*. What this page adds
beyond his answers is marked *(my call)* and listed at the end for checking.

## What it's for

Dan, 2026-11-12: "i want to implmeent the spark model token cost into [the swiftbar app] so that data is
aviable on the spark dashboard and is constnatly updated (doesn't need to be realtime a delay is okay)",
and "i want to get the token cost estimates now. base it off of claude pricing". The dashboard he means
is spark-menu's dropdown *(my reading; the dashboard of its own stays the later project it was)*. The
meter's design put everything after the counter in Phase 2b, after spark-menu moves into this repo, so
nothing would show until Phase 2a closes. This page brings the smallest set forward:

| From the meter's design | Was | Now |
|---|---|---|
| §3: `spark usage`, the mapping, the prices, the providers' list | Phase 2b | built now, deployed with the counter, Task 44l, before the soak |
| §4: spark-menu's usage section | 2b, after spark-menu moves here | built now in spark-menu, in Dan's dotfiles; it moves here with spark-menu |
| §7: the sync | 2b, every harness | one slice now: OpenCode's messages to the by-hand engines, which the front can't see |
| The fast coder behind the front | Phase 4a's window end | planned (Dan: "plan local-ai like option 3"), which ends the slice for it |

Dan also said "OR i can just not use the SGLang model". If he stops, the slice still counts the fast
coder's past, from 2026-10-15, and simply finds nothing new.

### Goals

1. **The spend in the dropdown,** at Claude's prices: today, this month, last month and all time, with
   all time as a share of the Spark's price. A minute or so behind the front, about five behind for the
   fast coder.
2. **Every number worked out on the Spark,** by `spark usage`, the one codebase. spark-menu prints what
   the summary says and adds nothing.
3. **The finest grain kept, prices applied when read** (Dan: "i want the data in enough granuar form
   that they can be aggregated and viewed in different ways for a larger spark dashboard application").
   The front already writes a row per request; the fast coder gets a row per message; no stored row
   holds a dollar. `spark usage rows` prints every row with its price worked out, for Dan's own looking
   and for the dashboard later *(my call)*.
4. **Prices that change over time** (Dan: "so it can be changed as models and pricing changes over
   time"): a mapping Dan edits and dated price lines, as §3 has them. A remap reprices the past; a price
   line applies from its date.

### Non-goals, still Phase 2b's

The harness lines (§5); the electricity estimate and the break-even date (shown as "—" until they
land); the rest of the sync (Pi, Hermes, Posit Assistant, Open WebUI, `agent`'s Pi), the baseline from
before the counter, the seam and sessions for the front's rows; the reconciliation window; per-group
caps; the dashboard.

### Anti-goals

The meter's design's anti-goals all hold: no text a client chose is stored or sent, no silent $0, no
money in the menu bar's title, nothing private in this repo, no token counted twice, nothing in Claude's
path. And:

- **spark-menu pricing anything, or adding two numbers.** It shows the summary's figures as given.
- **The Mac sending anything but numbers and ids.** The fast coder's rows hold no prompt, answer, title,
  file path or folder name, and the Mac reads only the fields it sends.
- **Contacting the Spark while spark-menu is off,** as its README already rules out.
- **A price edit that restarts the front or llama-swap.** Editing a price must never cut a request.
  (Shipped with the app, an edit restarts only the gate and the brake: part 1.)

## The parts

### 1. The price files **[Spark]**

Three files in the repo, since nothing in them is private: `git log` is the history of every remap
and every price. **They ship with the app** (Dan's decision, 2026-11-12): they live in the `spark`
project, so `make apply` deploys them like any other file of it, which restarts the gate and the brake
and never the front or llama-swap. So a remap or a price edit waits for the soak to end, like every
deploy, and loses nothing, since a remap reprices the past. Where in the project they sit is the
builder's.

**The mapping.** Each model to a Claude tier (`opus`, `sonnet`, `haiku`, `fable`) with an optional
factor, default 1, as §3 has it; `none` marks a model left unpriced on purpose, so it doesn't raise
`⚠map`. A model with no line is unpriced and flagged `⚠map`. It covers the registry's models and the
by-hand engines' served names. *The starting mapping* (§3's call): every chat model at `opus`
(`gemma-4-26b-a4b`, `qwen3.8-27b`, `qwen3.6-35b-a3b`, `qwen3.8-27b-sglang`, `qwen3.8-flash-next`,
`qwen3.8-flash-next-mtp`); the embedding, whisper and Qwen3-ASR models at `none`. Dan lowers any he
thinks isn't Opus-level.

**The prices.** Per tier, dated lines, in US dollars per million tokens: input, output, cache reads,
5-minute and 1-hour cache writes, and Haiku's second band. A line starts at 00:00 UTC on the day
Anthropic's release notes give *(my call: the notes give days, not times)*. Checked against
Anthropic's pricing page and release notes on 2026-11-12:

| Tier | From | Model | In | Out | Cache read | Write 5m | Write 1h |
|---|---|---|---|---|---|---|---|
| opus | 2026-09-22 | Opus 5.5 | 4 | 20 | 0.20 | 5 | 8 |
| sonnet | 2026-09-28 | Sonnet 5.5 | 2 | 10 | 0.20 | 2.50 | 4 |
| sonnet | 2026-10-07 | Sonnet 5.5, cache reads cut | 2 | 10 | 0.10 | 2.50 | 4 |
| haiku | 2026-09-28 | Haiku 4.5 | 1 | 5 | 0.10 | 1.25 | 2 |
| haiku | 2026-10-07 | Haiku 5.5, prompt ≤ 100K | 0.10 | 0.50 | 0.01 | 0.125 | 0.20 |
| | | Haiku 5.5, prompt > 100K | 0.50 | 2.50 | 0.05 | 0.625 | 1 |
| fable | 2026-09-01 | Fable 5.1 | 10 | 50 | 0.25 | 12.50 | 20 |
| fable | 2026-11-09 | Fable 5.2 | 8 | 40 | 0.20 | 10 | 16 |

So §3's first lines were right for 2026-10-06: Sonnet 5.5's cache reads fell to $0.10 the next day,
and Haiku 5.5 arrived that day, with Haiku 4.5 before it. Every tier covers 2026-09-28 onward. Sonnet
5.5 launched on 2026-09-28 itself, and Sonnet 5's rates before it were the same, so no earlier line is
needed. The cache-write switch stays off (§3).

**The providers' list** (§3). For each harness, the provider names that reached brightroar, each with
its dates, whether it passes through the front, and from when its engine reports a cache:

| Harness | Provider | Reaches | Front? | Cache reported |
|---|---|---|---|---|
| Pi, OpenCode, Hermes, Posit Assistant | `spark` | the front, 9100 | yes | as the row says |
| OpenCode | `spark-sglang` | the fast coder, by hand, from 2026-10-15 | no | not yet: the running SGLang was started before its script gained `--enable-cache-report` *(unverified from the Mac; the Spark session checks its command line)* |
| OpenCode | `spark-flash-next` | Flash-Next, by hand | no | unknown until checked (part 3): its zeros may be "not reported", as SGLang's are |

Only the providers marked "no" are read from the Mac now. When the fast coder moves behind the front,
its line gets an end date, and the slice stops reading it from then.

### 2. `spark usage` **[Spark]**

A module of the `spark` package, as §3 has it, with four verbs *(names my call)*:

- **`spark usage summary --json`:** what spark-menu shows (the contract, below).
- **`spark usage rows`:** every row, one JSON line each, with its price line, its tier and its dollars
  worked out now, or why it isn't priced. For Dan's own looking and the dashboard later. `--since`
  narrows it.
- **`spark usage import --source opencode`:** reads the fast coder's rows on standard input and merges
  them into the Spark's store, keyed on the message's id, so sending a row again changes nothing. It
  refuses any field outside the row's fixed set and any string that isn't from a short `[A-Za-z0-9._:-]`
  pattern. It prints one verdict line: rows new, unchanged and refused.
- **`spark usage harvest --source opencode`:** runs on the Mac (part 3).

**Its inputs on the Spark:** the front's rows (`/var/lib/local-ai/usage`); the gate's ledger, for the
spans with counting off; the imported rows, in §7's `/var/lib/local-ai/usage-sessions`, `dan:spark-admin`,
mode `2750`, which bootstrap makes in Task 44l; the price files; and the private file.

**The private file** (§3): the Spark's all-in price in US dollars and Dan's time zone, in a file in
Dan's own home on the Spark *(path my call: `~/.config/local-ai/meter.yaml`, mode `0600`)*, which Dan
writes and the vault records; the rate per kWh joins it with the electricity estimate. Without it the
payback share is null, shown "—", and days and months follow the Spark's own time zone, which the
summary names. Sessions check it by presence only.

**Pricing a row** is §3's formula, at the line in effect at the row's start, each row on its own. Error,
refused and interrupted rows are counted and never priced. A front row with no cache count stays
unpriced and flagged, as §1 has it.

**The fast coder's cache, estimated** *(my call, for checking)*. None of its rows reports a cache, and
priced as all uncached they read about twenty times too high at Opus's rates, since agent sessions
re-send their whole history every turn. §1 rules out pricing them as if nothing were cached. So for an
imported row with no cache count, the summary estimates the cached part as what Claude's prompt cache
would have read: the previous message's prompt in the same session, when this prompt is at least as
long, else nothing. The estimate is made when read, never stored, so a better rule reprices the past.
The summary shows how much of each total rests on it, and the menu marks it `~`. Once the fast coder
reports its cache, its rows carry real counts and the estimate stops applying to them.

### 3. The fast coder's rows, read on the Mac **[Mac]**

`spark usage harvest --source opencode` runs on the Mac, from the Mac's clone of this repo, through `uv
run`, as the Makefile runs `spark`. It opens OpenCode's database read-only, in place, and selects only
these fields of assistant messages whose provider is one the providers' list marks "no": the message
id, the session id, provider, model, token counts (input, output, reasoning, cache read and write), the
times created and completed, and whether it ended in an error. The SQL extracts those fields itself, so
no message content enters the program.

**The row** *(fields my call; the names are the builder's)*: a version; `opencode` as the source; the
message id; a hash of the session id (§3 keys sessions by hash); provider; model; start and end, UTC
epoch seconds; *P* = input + cache read + cache write; *C* = cache read, left out while the provider's
cache isn't reported; *O* = output + reasoning (§7's trap: OpenCode's stored output leaves reasoning
out); the cache writes; and how it ended. One row per message, never a total. Messages still in
progress are skipped until they finish. OpenCode's ids hold `_`, which the import's pattern allows.

**Checked before its rules are fixed** *(my call, from the builder check)*. Three facts decide whether
these numbers are right, and only the installed OpenCode can settle them: whether one stored assistant
message is one model call or the sum of several (if several, the cache estimate in part 2 counts per
call, not per message); whether OpenCode's stored output leaves reasoning out for these providers too,
as §7 found for OpenCode in general (if it already holds it, *O* = output + reasoning counts thinking
twice); and whether Flash-Next's zero cache reads are real or "not reported". The build settles them
from OpenCode's source at the installed version and from checks on the real database that print only
counts and verdicts, never content, and records the answers in the plan. The harvest runs on that
database anyway, so the build reads nothing the finished harvest wouldn't.

**Who runs it: spark-menu**, so the Mac gains no job of its own (Dan: "so we dont' need more
infraastructure from the mac"). Every 5 minutes while connected, its poller runs the harvest and pipes
the rows to `spark usage import` over its SSH master, bounded, in the background, the way it measures
the disk now. It never runs while spark-menu is off. If the clone has no `spark usage harvest` (an
older branch checked out), the panel says so once.

### 4. spark-menu's usage section **[Mac]**, in Dan's dotfiles

**The poll.** Every 60 seconds, once a reading has come back, a call of its own (not part of the
10-second probe): `spark usage summary --json`, by the deployed `spark`'s full path, bounded, in the
background. The answer is kept whole in `usage.json` in its state folder, with the time it came. A
failed or malformed answer keeps the last good one.

**The section,** in the dropdown below the meters; the title stays the dot:

```
Spark usage at Claude prices          as of 14:02
This month   $X.XX    N.NM in · N.NM out
Today        $X.XX
Last month   $X.XX
All time     $X.XX    N.N % of the Spark (before electricity)
  ~$X.XX of it estimates the fast coder's cache
⚠map gemma-4-26b-a4b · 2 interrupted · counting off 13:58–14:01
By model ▸   (this month: each model, its tier, its spend and tokens)
```

Older than 3 minutes, the section greys and says how old it is; it is never blanked. A summary made in
an earlier month than the Mac's shows its month by name rather than as "This month", so last month's
figure is never carried into a new one (§4). Every string goes through the panel's escaping, with
control characters removed too.

### 5. The fast coder behind the front **[Spark]**, planned

The plan gains it, where Phase 4a's window end is (Dan: "then plan local-ai like option 3"): the front
gains the by-hand engine as an upstream its rows name, so the counter sees it; the providers' list ends
`spark-sglang`'s "no" on that date; and a restart with `--enable-cache-report` gives its rows real cache
counts. The meter's design's *Open for later* item on the by-hand SGLang points here.

## The summary's JSON

The contract spark-menu reads, and the projects page records. A version, so a change is visible:

```json
{
  "v": 1,
  "made_at": 1794470000.0,
  "zone": "America/Example", "zone_from": "private_file | system",
  "counter_start": 1794400000.0, "last_row_at": 1794469990.0,
  "periods": {
    "today":      {"label": "2026-11-12", "usd": 1.23, "usd_estimated": 0.45, "requests": 10,
                   "p": 123456, "c": 100000, "o": 2345, "unpriced": 0},
    "this_month": {"label": "2026-11", "...": "the same fields"},
    "last_month": {"label": "2026-10", "...": "the same fields"},
    "all_time":   {"label": "all", "...": "the same fields", "share_of_price": 0.0123}
  },
  "by_model": [{"model": "qwen3.8-27b", "tier": "opus", "period": "this_month", "usd": 1.0,
                "usd_estimated": 0.0, "requests": 5, "p": 1000, "c": 800, "o": 100}],
  "hours": "only with --hours: totals per UTC hour, key and model, for the dashboard",
  "flags": {"unmapped": {"model-name": 3}, "interrupted": 2, "errors": 1, "cache_unknown": 0,
            "dropped": 0},
  "coverage": {"counting_off": [[1794460000.0, 1794460180.0]],
               "imported": {"opencode": {"last_import": 1794469800.0, "rows": 123}},
               "not_counted": ["by-hand engines: counted from heartsbane's OpenCode only"]},
  "electricity": null, "break_even": null
}
```

The figures are invented, as every example here is. Months and days are in `zone`. `share_of_price`
is all time's spend over the private file's price, null without it; it becomes §3's payback once the
electricity estimate lands. `usd` includes `usd_estimated`.

## Where the work runs, and its order

1. **[Mac]**, on this branch, as the counter's Tasks 44a–44j were *(my call: `spark usage` reads files
   and needs nothing of the box, so its tests run on the Mac and in CI)*: an implementation plan; the
   price files; `spark usage` with its tests; the harvest; the docs below.
2. **[Mac]**, in the dotfiles: spark-menu's poll, push and section, tested against invented summaries
   that follow the contract.
3. **[Spark]**: the full `make test`; the merge into `phase-2a` with Dan's OK; the plan's edits (its
   Revisions line, Phase 2a's new tasks after 44k, the fast coder behind the front); then Task 44l
   deploys the counter and `spark usage` together, and makes `usage-sessions`.
4. **[Dan]**: the private file on the Spark, and its line in the vault's entry note.
5. **[Mac]**: the Mac's clone pulls `phase-2a`, and spark-menu starts showing numbers.

**What changes with it,** by CLAUDE.md's table: the Architecture page's diagrams and scenario table
(the summary over SSH, the menu's usage section, the Mac's harvest, dashed until deployed); a scenario
page and `spark doctor` checks (the price files cover every registry model, and the summary runs);
the projects page's spark-menu row and a `notice/spark-menu` issue (opening it waits on Dan's OK, since
the issue is public); the meter's design (§3's price lines, §4, §7 and §9, annotated, never deleted);
the plan; the private material table in `CLAUDE.md`, README §My environment and the vault (the private
file, `usage-sessions`); `changelog.md` and README §Current state at the deploy; and spark-menu's own
README and rulings.

## The calls made here, for checking

1. "The spark dashboard" read as spark-menu's dropdown.
2. `spark usage rows`, every row priced when read, added for Dan's own looking.
3. Price lines starting at 00:00 UTC on the release notes' day; Haiku 4.5 before 2026-10-07.
4. `none` in the mapping for models left unpriced on purpose; embeddings and speech at `none`.
5. The private file at `~/.config/local-ai/meter.yaml`; without it, the Spark's own time zone and no
   share of price.
6. ~~**The fast coder's cache estimated** from the previous message in its session, shown apart and
   marked `~`, rather than left unpriced.~~ *(Dan's decision since, 2026-11-12: "estimate it".)*
7. The harvest's row and its fields; the SQL selecting only those fields.
8. spark-menu running the harvest every 5 minutes and the summary every 60 seconds; the section's
   layout; greyed after 3 minutes.
9. `spark usage` built on the Mac, its tests in CI, as the counter's were.
10. All time shown against the Spark's price "before electricity" until the estimate lands.
11. OpenCode's per-message shape, its output count and Flash-Next's cache settled from its source and
    from counts-only checks on the real database, before the harvest's rules are fixed.
12. The summary always saying the by-hand engines are counted from heartsbane's OpenCode only, since
    another machine or harness using them would otherwise be missing without a word.
