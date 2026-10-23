---
title: "The usage meter — design"
description: "Counting every token brightroar serves, pricing it at Claude's prices, and showing how far the Spark is from paying for itself: in each harness's status line and in the Spark's menu bar. A draft for Dan's approval."
date: 2026-10-22
---

# The usage meter — design

**Status: a draft for Dan's approval (2026-10-22), revised after its design council.** Every
decision here comes from [its questions and answers](usage-meter-qa.md), which keep Dan's words and
reasons; what this page adds is marked *(my call)*, and listed together at the end for checking.
Five reviewers read the first draft (the code and engines, privacy and security, the numbers, the
plan and its docs, and a skeptic); their fixes are folded in. Once Dan approves it, the two design
pages merge into `phase-2a`, and the Spark session, which owns that branch, writes the plan's
Revisions line, the Phase 2a tasks and Phase 2b's part (*Where it goes*, below). The
[Architecture page](../architecture.qmd) draws its parts dashed then; diagrams live only there.

## What it's for

Dan wants to know what the Spark has been worth: every token brightroar serves, priced at what the
same work would have cost on Claude, set against what the box cost, so he can see how far it is
from breaking even. In his words: "really i want to track the dgx usage cost and know how far away
we are from breaking even". The numbers should be fun to glance at, the way his Claude Code status
line's cost is, in every harness he uses and in the Spark's menu bar. Being a data scientist, he
wants the records as fine-grained as possible, so that a dashboard, the long-term end, can add them
up any way later.

### Goals

1. **Count every request that passes through the front,** from every client and key: the Mac's
   harnesses over the tunnel, Open WebUI, `agent`, scripts and, later, apps and other people. What
   doesn't pass through it is listed under *What the counter can't see*.
2. **Price it at Claude's prices** through a mapping Dan edits (each local model to a Claude model)
   and a dated price list. A remap reprices the past; a price change applies from its date.
3. **Break-even:** all-time spend, less an electricity estimate, as a share of what the Spark cost,
   with a date it pays for itself at the current pace.
4. **The fun numbers:** each harness's line, at parity with Dan's Claude Code status line, and the
   menu bar's dropdown.
5. **A lifetime baseline,** built from the harnesses' past sessions and Open WebUI's stored chats,
   brightroar's only, so the meter starts from 2026-09-28, not from the day it is deployed.
6. **One row per request, never totals,** with every number the stack can see.

### Non-goals

- **The dashboard.** A project of its own, later: the plan's Backlog already holds a "usage and
  utilization dashboard"; the records keep what it would need.
- **A menu bar of the meter's own.** spark-menu is the menu bar; it moves into this repo once its
  build is done (Dan's decision), and the meter gives it the numbers.
- **Live costs.** Totals may lag by minutes (Dan: "it's okay if those are delayed / updated
  preiodically/on-demand").
- **Billing anyone,** or limits on spend.
- **Measured power,** until a metering plug is added; an estimate stands in.

### Anti-goals: a failure even if it works

- Any prompt, answer, thinking text, file name or title stored by the meter, or any string a client
  chose: a model's name is recorded only as the registry's name it resolved to.
- The meter's own stores readable by `agent`, by an engine, or later by a guest. (The stack has
  exposures of its own outside the meter: the engines' ports take no key, and the engines'
  `--metrics`, if turned on in 2b, let any account read each request's sizes. The meter adds none.)
- A request slowed or failed because counting failed, or the front or the gate kept from starting.
- Money in the menu bar's title: Dan shares his screen.
- A silent $0: an unmapped model, a missing count, a missing price line or an interrupted turn
  priced as nothing without a flag. And the reverse: a count the engine never reported read as zero.
- Anything in Claude's path: no Spark segment in Claude Code's status line, no hook for the meter
  in Dan's or `agent`'s `~/.claude/settings.json`, and Claude Code's and Claude Desktop's files
  never read.
- Private material in this public repo or on its site: the records, key names, the purchase price,
  addresses, and any live figure from the meter. Examples and fixtures are invented.
- A token counted twice: across the baseline and the counter, from cumulative counters, from
  duplicate tables or copied sessions, or as both input and a cache read.

## Constraints

From this repo's rules (`CLAUDE.md`), which this design keeps:

- **Claude stays untouched,** as above.
- **The public repo:** the check happens before `git add`; the records, the summary, the private
  file, the snapshots and the key lists live outside the worktree.
- **Where work runs:** the front, the gate, bootstrap, `spark usage` and their tests are Spark work;
  the harness adapters and the Mac's readers are Mac work; keys, sudo and the purchase price are
  Dan's. Each task carries **[Spark]**, **[Mac]** or **[Dan]**.
- **Deploys come from `phase-2a`** until Phase 2a merges; the Spark session owns that branch, so the
  plan and task edits are made there, not on this branch.
- **Python through uv,** seven-day pins, the `Makefile` as the front door (GNU make 3.81 and bash
  3.2 for anything that runs on the Mac).
- **The front's limits:** `MemoryMax=512M`, of which its budgets already claim most; `WatchdogSec=30`;
  `ProtectSystem=strict` with no writable path today (`stack/templates/local-ai-front.service`).
- **Docs must be true:** a scenario page with its `spark doctor` check, the Architecture page's
  diagrams and scenario table, `changelog.md` and README §Current state change with the parts they
  describe.

## Decided

Dan's decisions, each with its reason in [the questions and answers](usage-meter-qa.md):

| Question | Decision |
|---|---|
| Priced at | Claude's prices |
| Which Claude model | a mapping Dan edits, local model → Claude model; tokens are stored, never dollars |
| When the mapping or a price changes | a remap reprices the past; prices are dated |
| Which harnesses | any: pi, OpenCode, Posit Assistant, Hermes, more later |
| The arithmetic | one codebase; every harness reads it or calls it |
| The baseline | everything brightroar served: the Mac's harnesses, Open WebUI, `agent`'s pi |
| What counts | brightroar only: not the Mac's own models, not the work Spark; nothing before 2026-09-28; the provider names used for brightroar never reached anything else |
| Breakdowns | by key: a key per harness and per app, each with its group (who) and label (which app) |
| Grain | one row per request and per past message |
| Freshness | totals may lag; worked out periodically or on demand |
| Test traffic | counts toward break-even like everything else |
| Electricity | estimated now and subtracted from savings, labelled an estimate; a metering plug later |
| The menu bar | one icon; its title a connection-status dot alone; every number in its dropdown |
| Where it goes | the counter in Phase 2a, before its soak; the rest is Phase 2b's first part |
| spark-menu | finishes its build in dotfiles, then moves here; everything consolidates in this repo |
| Sessions | a sync keeps the Spark current with what only the harnesses know (sessions, subagents); the baseline is its first run; each harness's footer shows the Spark's own figures, a minute or two old |
| The dashboard | a project of its own, later |

## The design

The parts, in the order a request meets them: the front writes a row; the gate writes its events;
the sync brings each harness's sessions to the Spark; `spark usage` turns all of it into a summary
on the Spark; spark-menu fetches it; the menu bar and each harness's line show it. Every number is
computed on the Spark, in one place.

### 1. A row per request, written by the front **[Spark]**

**Where** *(my call)*. One append-only JSONL file per UTC month, `usage-YYYY-MM.jsonl`, in
`/var/lib/local-ai/usage`, made by bootstrap as `spark-front:spark-admin`, mode `2750`, under root's
`/var/lib/local-ai`. The front's unit gains `ReadWritePaths=-/var/lib/local-ai/usage` (the `-` so a
missing folder can't fail the unit; a folder made after the front started stays read-only to it
until its next restart, which doctor's check catches) and `UMask=0027`, so each file is `0640`. Only
Dan, the one member of `spark-admin`, reads it, without sudo; `spark-users`, which holds `agent`,
and `spark`, the engines' user, can't. JSONL, not SQLite, so the request path holds no lock and a
reader never contends with the writer.

**Never in the way** *(my call)*. The front hands each row to one dedicated thread through a bounded
queue, so the event loop never waits on the disk. The thread writes each row as a single `os.write`
on an unbuffered `O_APPEND` descriptor; after a failed write (the disk is shared with the weights)
the next row starts with a newline, so a torn line stays one line, and readers skip and count lines
that don't parse. A full queue or a failed write drops the row and counts it; the next row written
carries the count. At a stop the thread is drained within a bound that fits the unit's stop timeout,
and a count still in memory is lost with the process, a gap the gate's ledger marks (§2). The folder
is opened lazily, outside the startup checks that refuse to start the front, so no state of the
folder can keep the front down. A failure writes one rate-limited journal line, and the front
reports "counting off since …" in the in-flight snapshot it already sends the gate, which `spark
status` shows on the control socket only, and `spark doctor` checks. The request is never touched.

**What is read, and what it means.** The routes disagree on whether a prompt count includes the
cached part, so each route has its own formula into the row's fields: *P*, every prompt token,
cached ones included; *C*, the cached part; *O*, every output token, thinking included. From the
engines' source at the pinned versions:

| Route | P | C | O |
|---|---|---|---|
| `/v1/chat/completions`, `/v1/completions`: `usage` | `prompt_tokens` | `prompt_tokens_details.cached_tokens` | `completion_tokens` |
| the same, from `timings` (the last copy only; it repeats and its numbers are running totals) | `prompt_n + cache_n` | `cache_n` | `predicted_n` |
| `/v1/embeddings` | `usage.prompt_tokens` | — | 0 |

llama-server sends `timings` on a stream's last chunk whether or not the client asked for usage, so
chat streams are counted without changing the request. **In Phase 2a the front reads these three
routes** *(my call, from the skeptic)*: every client here sends chat completions. `/v1/messages`,
`/v1/responses` and `/v1/audio/transcriptions` get a row with no counts, flagged, never $0, and
their parsing comes when a client uses them: `/v1/messages` puts P less C and C on its *first*
event, `/v1/responses`' last event repeats the whole answer, and whisper gives the audio's length
only in `verbose_json`. Non-streamed `n > 1` reports only the first choice, so such a row is flagged.
The front **never adds `stream_options.include_usage`** to a request: that adds a chunk the client
didn't ask for, and the stack injects nothing.

**The scanner** *(my call, from the code review)*. A constant amount of memory per response, outside
the front's body budget, so it can never refuse a stream already running: a fixed head (the first
event, or the first few KB of a body) and a fixed tail (the last ~4 KB of each data line and of a
body), parsed once a line ends, the last occurrence of a field winning. It never walks JSON byte by
byte on the event loop, and any failure turns counting off for that request alone. A test with a
multi-megabyte last event measures the added latency.

**Thinking.** llama.cpp reports no thinking count; thinking is inside its output count. The row
keeps the stream's events counted by kind (reasoning deltas, text deltas), saying where each number
came from; a display may show the reasoning count as an estimate, but it is not one token per event
under speculative decoding (the by-hand SGLang drafts up to 16, Flash-Next uses MTP), so the row
never calls it tokens. SGLang (Phase 4a) reports `reasoning_tokens` in thinking mode, where 0 can't
be read as "none". Thinking is priced as output either way.

**A cache that wasn't reported.** *C* is empty, never 0, when the engine doesn't report a cache: the
by-hand SGLang runs without `--enable-cache-report`, which its launch gains (and Phase 4a's), and
SGLang reports no cache without it. A row with *C* empty is flagged "cache unknown" and shown apart,
never priced as if nothing were cached: a 100K-token prompt that was mostly cached is ~$0.40 at
Opus's input rate and ~$0.02 as cache reads.

**Interrupted turns.** Esc in pi or Stop in Open WebUI cancels the request before its counts arrive,
though the prompt was read (and a cancel during prefill leaves no count at all). The row records
how it ended and the events counted; *(my call)* a row with no prompt count is left unpriced and
flagged, "N interrupted, not priced", so the totals are a lower bound and say so. The row keeps its
bytes in, key, model and time, so an estimate can be added later and reprice the past.

**The row** *(my call; names and types are the builder's)*. Numbers, and text only from a fixed set
of values or a short `[A-Za-z0-9._:-]` pattern with a length limit:

| Group | Fields |
|---|---|
| Who and what | schema version; request id, minted once per request and kept through the front's own resends; key name; route; the registry's model it resolved to, and the role if the client asked by role (an unresolved name is recorded as empty, with its length at most); the engine's name, from the gate's model list, which gains it (its version is in the changelog's dated deploys) |
| When | start and end, as UTC epoch seconds; durations from a monotonic clock |
| How it ended | done, cut, gone, error or refused; status; the refusal's code |
| Tokens | *P*; *C* (empty when not reported); *O*; where they came from: `usage`, `timings` or none; events counted by kind |
| Timing | the front's own wait for the gate; whether this request started a load (the gate's admission answer gains it, kept across the front's resends); time to first event; the engine's prompt and generation times |
| Shape | streamed or not; attempts; `n` when above 1; images; bytes in; reasoning effort when it is a known value; the engine's response id |
| Bookkeeping | rows dropped since the last one written |

Requests without a valid key (401s), `/health` and `/v1/models` get no row. Error and refused rows
are never priced.

### 2. The gate's events **[Spark]**

*(My call, for Dan's "as granular as possible": load history can't be recovered later.)* The gate
keeps its own ledger, in the same pattern, in `/var/lib/local-ai/gate-ledger`, made by root as
`spark:spark-admin`, mode `2750`, beside the gate's own folder, never inside it (root's `install`
would follow a link `spark` planted there). The gate's unit gains `ReadWritePaths=-…` for it and
opens it lazily; its files are written `0640` explicitly (the gate's own state files are `0600`).
llama-swap's unit gains `InaccessiblePaths=-…` for it. One row per model load (the model, the
request id that started it, the wait, the seconds it took, how it ended), unload (and why: idle,
make-room, brake, apply), refusal (code, model, request id), brake firing or release, the start and
stop of the front or the gate, and spans with counting off, as the front reports them. **No key
names:** a row joins the front's on the request id, which holds the key, so the gate's user, which
is also the engines', holds no per-key timeline. Today the gate keeps the newest 50 refusals with no
age limit, other events 50 for 24 hours, and no loads or unloads at all.

### 3. `spark usage`: the one codebase **[Spark]**

A module of the `spark` package that does all the arithmetic, on the Spark. Nothing else keeps a
price or adds a number: the harnesses' own lines show what it worked out (Dan's decision).

**Brightroar's providers.** One file in the repo lists, per harness, the provider names that reached
brightroar, each with the dates it did; readers and adapters read it, never a hard-coded name. A
rename, such as issue #28's for brightroar's own provider, adds a line.

**The mapping and the prices** *(where they live is my call)*. Two files in the repo, since nothing
in them is private, so `git log` is the history of every remap:

- **The mapping:** each local model to a Claude tier (`opus`, `sonnet`, `haiku`, `fable`), with an
  optional factor, default 1: a tokenizer ratio, applied to every token kind and to Haiku's band
  threshold, for a model known to read high or low. A switch per tier, off by default, charges
  uncached prompt tokens as cache writes, which is what Claude would mostly have charged in a
  cached session. A model with no line is unpriced: counted, shown with `⚠map`, never $0.
  Embeddings and speech start unpriced. *The starting mapping, my call:* every chat model at
  `opus`, since Opus is what Dan uses for his own work; he lowers any he thinks isn't Opus-level.
- **The prices:** each tier's lines, each with the instant it took effect (UTC): input, output,
  cache reads and cache writes per million tokens, and Haiku's second band for prompts over 100K
  tokens. A row takes the line in effect at its start; every tier's lines cover 2026-09-28 onward (a
  line for the model before the current one if it started later); a row with no line, or a line
  missing a rate it needs, is unpriced and flagged. The first lines, from Anthropic's list as of
  2026-10-06: Opus 5.5 $4/$20 with $0.20 cache reads; Sonnet 5.5 $2/$10, $0.20; Haiku 5.5
  $0.10/$0.50 up to 100K and $0.50/$2.50 above; Fable 5.1 $10/$50, $0.25. Cache-write rates, Haiku's
  cache-read rate and every start date are checked against Anthropic's pricing page by the builder;
  the cache-read rate matters most, since cache reads are most of the tokens.

**The price of a row:** ((*P* − *C*) × input + *C* × cache read + *O* × output) × factor, at the
line in effect at the row's start; Haiku's band tested on the whole prompt, switching every rate for
that request. Each row is priced on its own, never a sum, so the band works. **The error runs both
ways, and the summary says so:** uncached prompt tokens depend on llama-server's slot cache and on
unloads, not on Claude's cache, so they may read high; and cache writes, which Claude charges, read
low unless the switch is on. The summary shows the uncached-input share of the spend.

**The summary** *(my call)*. `spark usage summary --json`, with a schema version, gives: when it was
made and the last row's time; the counter's start; rows dropped, interrupted, cache-unknown and
unpriced (by model, for `⚠map`); totals by UTC hour, key and model, so any time zone can group them
into days and months; this month's, last month's, today's and all time's spend in Dan's zone; the
uncached-input share; the electricity estimate; the payback; the break-even date; the coverage
gaps (counting off, a harness whose sync is behind, the by-hand engines); and, for the harness
lines, each recent session's totals and its latest turn's, keyed by a hash of the harness's own
session id, so a line can find its own without the summary holding any name. It runs on demand;
whether it keeps closed months' totals is the builder's.

**The private file** *(my call, revised after the council)*: the purchase price, the rate per kWh
(dated) and Dan's time zone, in a file in Dan's own `0700` home on the Spark, mode `0600`, recorded
in the vault; never in the repo, never on the Mac. So the summary does all of the arithmetic, and
nothing on the Mac computes. Sessions building the meter check that file and the summary by
presence only, never reading them into a transcript.

- **Electricity (estimate):** idle watts × hours on, plus (working − idle watts) × busy hours, at
  the rate in effect. Busy hours are the union of every row's start-to-end across all models (not
  the hours models are loaded: three stay loaded around the clock). Hours on come from the system's
  record of boots if its journal is kept (a box fact to check), otherwise "on since 2026-09-23" is
  stated; the window matches the spend's. The watts start at published figures for the AI TOP ATOM,
  about 37 W idle and 100–160 W working, which go in README §Hardware marked unverified. A metering
  plug, later, replaces the estimate from its first day.
- **Payback:** (all-time spend − electricity) ÷ the Spark's all-in price, in US dollars (price, tax
  and shipping; a price paid in another currency converted once, at the purchase date's rate).
- **The break-even date:** what is left, at the net daily pace over the days with data, up to the
  last 30; "—" until there are 14 days, "not at this pace" when the pace is zero or less, "10y+"
  when it is negligible.

### 4. spark-menu reads it **[Mac]**

spark-menu already keeps one SSH master to the Spark and a poller that makes one remote call every
10 s, writing a reading to its state folder (`~/.local/state/spark-tunnel/`), which its plugin only
reads. The meter adds `spark usage summary --json` to that call about once a minute, by the
deployed `spark`'s full path, and the reading carries it; the harness lines read it from there. No route is added to the front, which serves only
inference routes so that no key, `agent`'s included, can reach anything else; and usage stays off
the gate's status socket, which `agent` reads. Whether the poll's reads use a key of their own with
a forced command (as Phase 2a's forward look prescribes for `spark status --json`), while its
actions keep Dan's, is decided when spark-menu moves here.

**What it shows** (Dan's decision): the title stays a dot. A usage section in the dropdown: this
month, with the electricity estimate beside it; last month; today; all time with its share of the
Spark's price; the break-even date; and the flags (`⚠map`, interrupted, cache unknown, coverage
gaps). When the Spark can't be reached the last numbers stay, greyed with their time, never blanked,
and last month's figure is never carried into a new month. Everything printed escapes SwiftBar's
`|` and control characters, though the summary holds no client text.

### 5. Each harness's line **[Mac]**; `agent`'s **[Spark]**

**What it shows,** after Dan's Claude Code status line: the session's cost, labelled an API
equivalent; the latest turn's; subagents' cost, where the harness records them; this month's Spark
total; tokens in and out, and thinking where the harness or engine reports it; `⚠map` for an
unmapped model. *(Deferred, my call: a ⏳ first-token timer and tokens a second go beyond parity;
the menu bar's dot already shows a load.)*

**Where its numbers come from** (Dan's decision): **the Spark's summary,** through spark-menu's
reading on the Mac, a minute or two behind, with its age shown and greyed when stale. The adapter
hashes its own session id to find its session's line in the summary; it prices nothing and adds
nothing, so the footer, the menu bar and the dashboard later agree, and a remap shows everywhere at
once. A turn's own cost shows once the sync has carried it, not the instant the turn ends.

**Per harness** (from reading each one):

| Harness | Where the line goes | Session data | When |
|---|---|---|---|
| pi | an extension, `ctx.ui.setStatus`, on `message_end` and `turn_end`; nothing waited on | each message's `usage` | first |
| OpenCode | a TUI plugin in the `prompt.footer.status` slot | `session.usage.updated`; a session family covers its subagents | second |
| Posit Assistant | none of its own: a small Positron extension with a status bar item | its `lm-messages.jsonl` | deferred, until its branch merges |
| Hermes | none a plugin can draw; a `/spark` command, or later its own pricing from a `pricing` field the front's `/v1/models` could give | its `post_api_request` hook | deferred |

Hermes's and Posit Assistant's requests count at the front either way, so they are in the month and
on the menu bar. **Where the adapters live:** `clients/<harness>/`, installed by `make clients`, one
extension per harness that also carries 2b's planned hook for it; no npm dependencies, or a lock
under the seven-day rule; the harness versions each is checked against recorded in README §My
environment. Everything an adapter prints escapes control characters.

**`agent`'s pi** *(my call, revised with Dan's decision)*: its line waits. `agent` can't read the
summary, the records or the private file, and must not, since the summary holds Dan's totals and
the payback, which is Dan's total in disguise. `agent`'s usage is counted at the front and synced
like any harness's, so it is in Dan's breakdowns; a line for `agent` needs a view of its own numbers
alone, filtered by its uid, which can come later if it is wanted.

### 6. A key per harness **[Dan]** **[Mac]** **[Spark]**

*(My call, from the plan and skeptic reviews.)* **pi keeps `SPARK_API_KEY`,** whose label on the
Spark is already pi's; OpenCode and Hermes get keys of their own in Phase 2a, and Posit Assistant
when its branch merges (its key name there is `OPENAI_COMPATIBLE_API_KEY`, with Positron's secret
store). **Nothing is retired in 2a:** Task 38's rollback runs Phase 1's llama-swap from
`llama-swap.env`, which holds Phase 1's client keys until Task 51, so a retired key would turn the
Mac's harnesses away in a rollback. The Spark's own copy of the shared key, which its runbooks'
`curl`s use, gets a key of its own after Task 51.

For each new key: Dan creates it on the Mac and keeps its value in `~/.secrets` by reference; only
its digest travels to the Spark, with its line in the private key list (a label pi's retry list
won't match); one `make restart-front` takes the batch. **The caps:** today's are per key, Dan's
group allowing 8 waiting each, meant to stay below llama-swap's 10 per model; Dan's group's cap per
key is lowered so his keys together stay below it (configuration only; per-group caps later if
needed). **Key names are never reused.** A retired key's name goes into a list of its own that holds
no digest, since a line in the key list requires one and would leave the key working; retiring
removes the digest and any copy in `llama-swap.env`, Dan removes the `~/.secrets` lines on both
machines and checks Hermes's `.env` by counting lines, never printing them, and a check confirms
the old key now gets a 401. The code and docs that name `SPARK_API_KEY` (`doctor.py`, the 401's
words in `messages.py` and the refusal tables, `clients.py`, `brake.py`, and the deploy, pi,
opencode-hermes, secret-files, updates and speed-tests runbooks) change where a key changes.

### 7. The sync, and the lifetime baseline **[Mac]** **[Spark]** **[Dan]**

**The sync** (Dan's decision). The front sees each request, but only a harness knows the session it
belonged to, its subagents, and what the by-hand engines served. So the readers below run every
minute or so on what is new in each harness's files, not once: on the Mac as Dan; on the Spark for
`agent`'s pi, from a system timer that reads as `agent` and imports as Dan *(my call)*; and for Open
WebUI, from a system timer through its container *(my call)*. They send per-message rows to the
Spark. The lifetime baseline is simply the sync's first run, reaching back to 2026-09-28. After the
counter starts, the front's rows are the spend, and the sync's rows add each request's session,
parent and harness, joined on the engine's response id where the harness keeps one (pi does) and
otherwise on key, time and counts; before the counter, and for the by-hand engines, the sync's rows
are the spend. A harness whose sync falls behind shows in the summary's coverage gaps.

**Snapshot first.** Harnesses prune: Hermes deletes ended sessions after 90 days (its oldest
brightroar session is from 2026-10-09, so the first would go about 2027-01-07; derived, not
checked). So read-only copies of every source are taken before anything else, into a private
`0700` folder outside the worktree, never synced to iCloud or the vault; the readers read the
copies. **The Mac's were taken on 2026-10-23** (Dan's decision), in
`~/.local/state/local-ai/usage-snapshots/2026-10-23/`: Hermes's and OpenCode's databases and pi's
and Posit Assistant's session folders, without Hermes's request dumps (they hold an Authorization
header) or Posit Assistant's developer logs. The Spark's sources are copied in the Phase 2a task
that starts the counter.

**The rows.** One per past message, in the row's schema with its source (harness), the format read,
and an id from the message itself (source, message id, its time, and the engine's response id where
the harness kept one), never from the file: pi's `/fork` and `/clone` copy messages into new files,
and a renamed project folder renames pi's. Copies across files collapse, and the import reports how
many. Each (source, provider) pair has a window with a start and an end. The rows live on the Spark
in `/var/lib/local-ai/usage-sessions`, `dan:spark-admin`, mode `2750`, the summary's only other
input; the Mac's readers send their rows there over SSH as Dan, with the same upsert, so a re-sent
row changes nothing.

**The seam.** The counter's first row's time is stored once. A synced row before it carries spend;
one after it carries only what the front's row lacks, so a session that runs across the seam is
priced once, per message. Hermes keeps only session totals that grow in place, so its spend before
the seam is read from the snapshot taken before the counter started, and its sync afterwards adds
sessions only. **The by-hand engines** (`make fast-coder`, `make flash-next`) don't pass through the
front, so their synced rows stay the spend until Phase 4a puts them behind it, which ends their
window.

| Source | Reading it |
|---|---|
| pi (Mac) | every entry that carries usage, filtered by provider: assistant messages, `usage` entries, compactions and branch summaries (these two with the model in effect, flagged inferred); a tool result's usage only when its nested calls have no file of their own; skip `*.lease` |
| OpenCode (Mac) | `opencode.db` opened read-only: `session_message` assistant and compaction rows; children counted once; the old v1 tables skipped, which repeat 9 sessions |
| Hermes (Mac) | `state.db`'s `session_model_usage`, the finest it keeps (session × model × task), where its recorded endpoint is brightroar's; a model given as a GGUF path mapped to the registry's name |
| Posit Assistant (Mac) | `lm-messages.jsonl` and `usage-events.jsonl`, as a union keyed by call id once their overlap is settled by matching ids; it stores uncached input as cache *writes*, which the reader moves back to input after checking input is 0, flagging the row if not |
| Open WebUI (Spark) | read inside its own container (`docker compose exec`), printing numbers only, so nothing copies its chats and a crafted file meets only the container's root; `chat_message`'s usage and model; it adds several calls per message, and may count a doubled `timings` twice |
| `agent`'s pi (Spark) | run by Dan, interactively, as `sudo -u agent env -i …` with the deployed `spark` by full path, `cwd /` and stdin from `/dev/null`, never `uv run` as `agent`; only numbers leave, piped to Dan's importer, which checks them again (schema, limits, registry names) and flags the rows as `agent`'s own report |

Every reader turns a cache count of 0 into empty for a provider whose engine doesn't report one, and
treats a zero-usage aborted or error message as interrupted, not priced. No reader reads message
text, an error never quotes a file, and test fixtures are invented. **The normalization**, so every
source fills the row the same way:

| Source | P | C | O | Trap |
|---|---|---|---|---|
| pi | `input + cacheRead + cacheWrite` | `cacheRead` | `output` | `reasoning` is already in `output`: never add it |
| OpenCode | `input + cache.read + cache.write` | `cache.read` | `output + reasoning` | stored `output` leaves reasoning out: add it back, the opposite of pi |
| Hermes | `input_tokens + cache_read_tokens` | `cache_read_tokens` | `output_tokens` | session grain: no band, no per-request cut |
| Posit Assistant | `inputTokens + cacheWriteTokens + cacheReadTokens` | `cacheReadTokens` | `outputTokens` | the cache-write swap |
| Open WebUI | `input_tokens` (already includes cached) | `cached_tokens`, else `cache_n` | `output_tokens` | summed calls; a doubled `timings` |

**A reconciliation window** *(my call, from the numbers review)*. Once the counter runs with a key
per harness, each reader runs over its first one or two weeks without importing, and is compared
with that key's rows, per request where the engine's response id joins them and in total. A reader
joins the baseline only within a stated tolerance; Open WebUI's doubling, Posit's swap, OpenCode's
reasoning and pi's forks are tested on real data there.

### 8. What changes with it

- **The Architecture page** (its diagrams, its scenario table, and `_overview.qmd` if it shows the
  part): the front's records, the gate's ledger, the summary over SSH, the menu bar's usage section,
  each harness's adapter. Dashed until built.
- **A scenario page** for the meter, with its `spark doctor` checks: each folder's owner, group, mode
  and setgid bit, no file readable by others, counting on; and the privacy canary extended to the
  records and the ledger, whose needle it sends today as a file name.
- **The plan:** its Revisions line; the Requirements row "always see what model is being used", the
  SwiftBar plugin's row, the menu bar line, 2b's item and 2b's done-when, each annotated with the
  dot-only title (correct, don't delete); Phase 3's line, since LiteLLM in the front's place
  rebuilds the counter and LiteLLM ahead of it leaves the front one key unless it trusts an
  identity header; Phase 4a's (SGLang's cache report and streaming usage, so the fast coder's tokens
  are counted, and the by-hand window's end); Phase 5's backups (the records, the ledger, the
  baseline and its snapshots) and S18's rebuild; 2c's second upstream (its rows name it); and Phase
  6: "nothing anyone sends is logged" corrected before the first key for anyone else, how long
  other groups' rows are kept, what people are told, and pseudonyms for their key names.
- **Rules,** into `CLAUDE.md` and README §Conventions: key names are never reused; brightroar's
  provider names come from their list; no live figure from the meter goes in the repo, the site or a
  commit.
- **The private material table** (`CLAUDE.md`, README §My environment, the vault's entry note): the
  records', the ledger's and the baseline's folders, the private file, the snapshots, the retired
  keys' list; and the ignore patterns for any local output. Dan adds key names to his denylist.
- **README §Hardware:** the published watts, unverified. **Open items:** the box facts the design
  assumes from the Mac (Open WebUI's `chat_message` table at v0.11.4, `agent`'s pi session format,
  whether the journal keeps boots) and the prices still to check.
- **`stack/keys.example.yaml`** and the front's per-key caps, where the caps change.
- **`changelog.md` and README §Current state** when it is deployed.

### 9. Where it goes, and the order of the work

**Where** (Dan's decision): the counter joins Phase 2a, before its soak; the rest is Phase 2b's
first part; spark-menu moves here once its build is done.

**Phase 2a** *(numbering and placement my call)*: new tasks numbered 44a onward, run after Task 44
and before Task 45, so no existing task is renumbered (they are cited across the repo).

1. **[Dan] [Mac] [Spark]** OpenCode's and Hermes's keys, the caps lowered. (The Mac's snapshots
   were taken on 2026-10-23.)
2. **[Spark]** The counter: the front's rows and scanner, the gate's ledger, bootstrap's folders, the
   units' paths, `--enable-cache-report` on the by-hand SGLang, doctor's checks, the scenario page,
   and the Spark's snapshots. Counting starts.

Then 2a's own tasks cover it: Task 45 records the front's peak memory with the scanner, under its
512M, with no rows dropped, and rows checked against pi's own counts; Task 49 sees `gone` rows when
the front is killed, and an unwritable folder never stopping the front; Task 50 sees S03's cut give
a `cut` row and refusals give rows with their codes. Both done-when lists gain the meter; Task 51's
file lists and Task 52's Mac check (`make clients`) gain its files.

**Phase 2b:** `spark usage`, the mapping, the prices, the providers' list, the summary and the
private file; the readers, the reconciliation window, then the sync, whose first run is the
baseline; spark-menu's usage section, after it moves here; pi's line, then OpenCode's; the shared
key's retirement from the Spark's runbooks; then Posit Assistant's and Hermes's lines when their
turn comes. `agent`'s line waits for a view of its own numbers. The plug is a task of its own,
whenever Dan buys one.

### What the counter can't see

- **The engines' own ports,** which take no key: a local account calling one directly isn't counted.
- **The by-hand engines,** until Phase 4a (§7's window).
- **Phase 3's apps,** if LiteLLM sits ahead of the front without passing who asked.

## Left to the builder

The file formats' details and field names; the writer thread and the scanner's sizes; whether the
summary keeps closed months; the poll's timing; the dropdown's layout; how busy hours are merged;
the caps' new number; the reconciliation's tolerance.

## Open for later

- **The dashboard,** its own project (the Backlog's item).
- **SGLang's streaming usage** (Phase 4a): it reports usage in a stream only when asked, or with a
  server flag that changes what every client gets.
- **The poll's own key,** decided when spark-menu moves here.
- **Phase 6,** as in §8.
- **Backups,** with Phase 5.
- **The metering plug:** no vendor cloud (its power timeline shows when someone is home),
  authentication on its local API, its relay locked on or a meter-only device, out of reach of guest
  networks, its address in the private values file and its credential by reference.
- **Outside this repo:** Dan's Claude Code status line prices from `~/.claude/model-pricing.json`,
  last checked 2026-08-27, at Opus 5's rates and with cache reads at 10%; its month-to-date reads
  high. Offered separately; no task here touches it.

## The calls made here, for checking

1. The records' place and modes: `/var/lib/local-ai/usage`, `spark-front:spark-admin 2750`, files
   `0640`; JSONL, one file a month; a single `os.write` per row.
2. Counting never in the way: a dedicated thread, a bounded queue, dropped rows counted, the folder
   opened lazily, "counting off" through the front's in-flight snapshot.
3. In 2a, only chat completions, completions and embeddings parsed; the other routes flagged.
4. The scanner's fixed head and tail, outside the body budget.
5. The row's fields; the model as the registry's name only; *C* empty, never 0, when not reported.
6. The gate's ledger, kept for load history, with no key names.
7. The mapping, the prices and the providers' list in the repo; mapping to a tier; every chat model
   starting at `opus`; the cache-write switch, off.
8. The private file on the Spark, in Dan's home, so the summary does all the arithmetic.
9. spark-menu's poller fetching the summary about once a minute; no new route, nothing on the
   status socket.
10. `agent`'s line waiting for a view of its own numbers.
11. pi keeping `SPARK_API_KEY`; nothing retired in 2a; Dan's per-key cap lowered.
12. Snapshots first; the synced rows' ids from messages, not files; their rows on the Spark.
13. The sync's timers on the Spark: `agent`'s pi read as `agent` and imported as Dan; Open WebUI
    read through its container.
14. The reconciliation window before the sync's first import.
15. Interrupted turns flagged and unpriced; error and refused rows never priced.
16. The ⏳ timer and tokens a second deferred; Posit Assistant's and Hermes's lines deferred.
17. Phase 2a's new tasks numbered 44a onward, between Tasks 44 and 45.
