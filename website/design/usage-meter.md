---
title: "The usage meter — design"
description: "Counting every token brightroar serves, pricing it at Claude's prices, and showing how far the Spark is from paying for itself: in each harness's status line and in the Mac's menu bar. A draft for Dan's approval."
date: 2026-10-22
---

# The usage meter — design

**Status: a draft for Dan's approval (2026-10-22).** Every decision here comes from [its questions
and answers](usage-meter-qa.md), which keep Dan's words and reasons; what this page adds is marked
*(my call)*, and listed together at the end for checking. Once Dan approves it, [the plan](plan.md)
takes it with a Revisions line, the [Architecture page](../architecture.qmd) draws its parts dashed
(diagrams live only there), and an implementation plan follows.

## What it's for

Dan wants to know what the Spark has been worth: every token brightroar serves, priced at what the
same work would have cost on Claude, set against what the box cost, so he can see how far it is
from breaking even. In his words: "really i want to track the dgx usage cost and know how far away
we are from breaking even". The numbers should be fun to glance at, the way his Claude Code status
line's cost is, in every harness he uses and on the Mac's menu bar. Being a data scientist, he wants
the records as fine-grained as possible, so that a dashboard, the long-term end, can add them up any
way later.

### Goals

1. **Count every request brightroar serves**, from every client and key: the Mac's harnesses over
   the tunnel, Open WebUI, `agent`, scripts and, later, apps and other people.
2. **Price it at Claude's prices** through a mapping Dan edits (each local model to a Claude model)
   and a dated price list. A remap reprices the past; a price change applies from its date.
3. **Break-even:** all-time spend, less an electricity estimate, as a share of what the Spark cost,
   with a date it pays for itself at the current pace.
4. **The fun numbers:** each harness's line, at parity with Dan's Claude Code status line, and the
   menu bar's dropdown.
5. **A lifetime baseline,** built once from the harnesses' past sessions and Open WebUI's stored
   chats, brightroar's only, so the meter starts from 2026-09-28, not from the day it is deployed.
6. **One row per request, never totals,** with every number and id the stack can see.

### Non-goals

- **The dashboard.** A project of its own, later; the records keep what it would need.
- **Live costs.** Totals may lag by minutes (Dan: "it's okay if those are delayed / updated
  preiodically/on-demand").
- **Billing anyone,** or limits on spend.
- **Measured power,** until a metering plug is added; an estimate stands in.
- **LiteLLM.** The front does the counting; Phase 3's gateway decision is untouched.

### Anti-goals: a failure even if it works

- Any prompt, answer, thinking text, file name or title stored anywhere by the meter.
- `agent`, or later a guest, able to read Dan's usage, or anyone's but its own.
- A request slowed or failed because counting failed, or the front kept from starting.
- Money in the menu bar's title: Dan shares his screen.
- A silent $0: an unmapped model, a missing usage report or an interrupted turn priced as nothing
  without a flag.
- Anything in Claude's path: no Spark segment in Claude Code's status line, no hook for the meter in
  `~/.claude/settings.json`, and Claude Code's and Claude Desktop's files never read.
- Private material in this public repo: the records, key names, the purchase price, Lander's
  address, tailnet names or addresses.
- A token counted twice: across the baseline and the counter, from cumulative counters, from
  duplicate tables, or as both input and a cache read.

## Constraints

From this repo's rules (`CLAUDE.md`), which this design keeps:

- **Claude stays untouched,** as above.
- **The public repo:** the check happens before `git add`; the records, the Mac's cache, the price
  file and the key list live outside the worktree.
- **Where work runs:** the front, the gate, bootstrap, `spark usage` and their tests are Spark work;
  the menu bar, the harness adapters and the Mac's readers are Mac work; keys, sudo and the purchase
  price are Dan's. Each task in the implementation plan carries **[Spark]**, **[Mac]** or **[Dan]**.
- **Deploys come from `phase-2a`** until Phase 2a merges; this work branches from it.
- **Python through uv,** seven-day pins, the `Makefile` as the front door.
- **The front's limits:** `MemoryMax=512M`, of which its budgets already claim most; `WatchdogSec=30`;
  `ProtectSystem=strict` with no writable path today (`stack/templates/local-ai-front.service`).
- **Docs must be true:** a scenario page with its `spark doctor` check, the Architecture page's
  diagrams, `changelog.md` and README §Current state change with the parts they describe.

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
| What counts | brightroar only: not the Mac's own models, not Lander's Spark; nothing before 2026-09-28 |
| Breakdowns | by key: a key per harness and per app, each with its group (who) and label (which app) |
| Grain | one row per request and per past message |
| Freshness | totals may lag; worked out periodically or on demand |
| Test traffic | counts toward break-even like everything else |
| Electricity | estimated now and subtracted from savings, labelled an estimate; a metering plug later |
| The menu bar | one icon; its title a connection-status dot alone; every number in its dropdown |
| The dashboard | a project of its own, later |

## The design

The parts, in the order a request meets them: the front writes a row; the gate writes its events;
`spark usage` turns rows into a summary; the Mac fetches the summary; the menu bar and each harness's
line show it.

### 1. A row per request, written by the front **[Spark]**

**Where** *(my call, from the privacy review)*. One append-only JSONL file per UTC month,
`usage-YYYY-MM.jsonl`, in `/var/lib/local-ai/usage`, made by bootstrap as
`spark-front:spark-admin`, mode `2750`. The front's unit gains `ReadWritePaths=-/var/lib/local-ai/usage`
(the `-` so a missing folder can't fail the unit) and `UMask=0027`, so each file is `0640`. Only Dan,
the one member of `spark-admin`, reads it, without sudo; `spark-users`, which holds `agent`, and
`spark`, the engines' user, can't. JSONL, not SQLite, so the request path holds no lock and a
reader never contends with the writer; the summary loads it.

**Never in the way** *(my call)*. The front hands each row to one writer thread through a bounded
queue, so the event loop never waits on the disk. A full queue or a failed write (the disk is shared
with the weights) drops the row and counts it; the next row written carries the count, so the
summary knows it is short. The folder is opened lazily, outside the startup checks that refuse to
start the front, so no state of the folder can keep the front down. A failure writes one
rate-limited journal line, and `spark status` and `spark doctor` say "counting off since …". The
request is never touched.

**What is read from a response** (from the engines' source at the pinned versions):

| Route | Where the counts are |
|---|---|
| `/v1/chat/completions`, streamed | llama-server sends `timings` (`prompt_n`, `cache_n`, `predicted_n`) on its last chunk whether or not the client asked for usage; `usage` too when it asked |
| `/v1/completions`, streamed | `usage` and `timings` on the last chunk, always |
| `/v1/responses`, streamed | `response.usage` and `timings` on `response.completed`, the last event, which also carries the whole answer |
| `/v1/messages`, streamed | input and cache-read counts on `message_start`, the **first** event; output on `message_delta`; nothing on `message_stop`, the last |
| any route, not streamed | `usage` in the body; `timings` too on chat and completions |
| `/v1/embeddings` | `usage.prompt_tokens` |
| `/v1/audio/transcriptions` | no usage; the audio's length only in `verbose_json`'s `duration` |

`timings` can come more than once and its numbers are running totals: keep the last, never add
them. The model is the one the request named, never the response's `model`, which is the engine's
own alias. The front reads these with a bounded scanner that keeps only numeric fields and a small
window, never a whole line or body, and charges what it holds to the front's memory budget. **It
never adds `stream_options.include_usage`** to a request: that adds a chunk the client didn't ask
for, which some clients mishandle, and the stack injects nothing.

**Thinking.** llama.cpp reports no thinking count: thinking is inside its output count. The front
counts the reasoning events of a stream as they pass, about one token each, and records them as an
estimate (`think≈` where shown). SGLang (Phase 4a) reports `reasoning_tokens` when the request runs
in thinking mode, where 0 can't be read as "none". Thinking is priced as output either way, so it
changes no dollar figure; it is for the harness line.

**Interrupted turns.** Esc in pi or Stop in Open WebUI cancels the request before its counts arrive,
though the prompt was read. The row records what is known (how it ended, the events counted, for
`/v1/messages` the input from its first event). *(My call, from the builder check:)* a row with no
prompt count is left unpriced and flagged, "N interrupted, not priced", the way `⚠map` is, so the
totals are a lower bound and say so. The row keeps its bytes in, key, model and time, so an estimate
can be added later and reprice the past, as a remap does. An estimate now would have to guess the
cached share: a long pi prompt is ~$0.40 at Opus's input rate and ~$0.02 as cache reads.

**The row** *(my call, from the records review: names and types are the builder's)*. Numbers and
ids only.

| Group | Fields |
|---|---|
| Who and what | schema version; request id, minted once per request and kept through the front's own resends; key name; route; model asked for; engine and its version |
| When | start and end, as UTC epoch seconds; durations from a monotonic clock |
| How it ended | done, cut, gone, error or refused; status; the refusal's code |
| Tokens | prompt tokens in all; the part read from cache; output tokens in all; the part that was thinking (empty when not known, never 0); where the counts came from: `usage`, `timings`, counted events or none |
| Timing | the gate's wait; whether this request started a load; time to first event; the engine's prompt and generation times |
| Shape | streamed or not; attempts; images; bytes in; audio seconds; reasoning effort, when it is a known value; the engine's response id; the client's retry count; events counted by kind |
| Bookkeeping | rows dropped since the last one written |

Requests without a valid key (401s), `/health` and `/v1/models` get no row: they did no work, and a
401 names no one.

### 2. The gate's events **[Spark]**

*(My call, from the records review.)* The gate keeps its own ledger, in the same pattern, in a
folder of its own, `spark:spark-admin 2750`: one row per model load (the model, the request
that started it, its key, the wait, the seconds it took, how it ended), unload (and why: idle,
make-room, brake, apply), refusal (code, key, model), brake firing or release, and start of the
front or the gate. Today the gate keeps 50 of each for 24 hours. These rows give each model's
loaded hours, the busy hours the electricity estimate needs, and the dashboard's waits and load
times. The gate's admission answer gains whether the request started a load, and takes the
request's id, so the two ledgers join. *(My call, from the builder check:)* the gate runs as
`spark`, the engines' user, so `spark` can read its ledger, as it reads the gate's state today,
which keeps the last 50 refusals with their key names. A user of the gate's own, or handing its
events to the front, would change the gate's unit and protocol for little: the ledger holds loads
and refusals, not each request's sizes and times, which stay in the front's folder out of
`spark`'s reach.

### 3. `spark usage`: the one codebase **[Spark]** **[Mac]**

A module of the `spark` package, run on both machines, that does all the arithmetic. Nothing else
keeps a price or adds a number.

**The mapping and the prices** *(where they live is my call)*. Two files in the repo, since nothing
in them is private, so `git log` is the history of every remap:

- **The mapping:** each local model to a Claude tier (`opus`, `sonnet`, `haiku`, `fable`), with an
  optional factor, default 1, for a model known to read high or low. A model with no line is
  *unpriced*: counted, shown with `⚠map`, never $0. Embeddings and speech have no Claude equivalent
  and start unpriced, their units (tokens, audio seconds) recorded so a price can be added later.
  *The starting mapping, my call:* every chat model at `opus`, since Opus is what Dan uses for his
  own work, so it is what this work would have cost him; he lowers any he thinks isn't Opus-level.
- **The prices:** each tier's rates, each line with the date it took effect: input, output and
  cache reads per million tokens, and Haiku's second band for prompts over 100K tokens. Mapping to a
  tier, not a model id, makes a new Claude release a dated price change, not a remap that reprices
  the past. The first lines, from Anthropic's list as of 2026-10-06: Opus 5.5 $4/$20 with $0.20
  cache reads; Sonnet 5.5 $2/$10, $0.20; Haiku 5.5 $0.10/$0.50 up to 100K and $0.50/$2.50 above;
  Fable 5.1 $10/$50, $0.25. The builder checks them, Haiku's cache-read rate (not in that list) and
  each one's start date against Anthropic's pricing page.

**The price of a row:** uncached prompt tokens at the input rate, cached ones at the cache-read
rate, output (thinking included) at the output rate, times the factor. Engines report no cache
writes, so none are charged; the meter reads a little low for it, and says so. Each row is priced
on its own, never a sum: Haiku's band depends on each request's prompt.

**The summary** *(my call)*. `spark usage summary` reads the rows, the gate's ledger, the baseline,
the mapping, the prices and the private key list, and gives: when it was made and the last row's
time; the counter's start; rows dropped, estimated and unpriced (by model, for `⚠map`); totals by
day, key and model; this month's and all time's spend; the hours on and the busy hours; and today's
rate for each Spark model, so a harness can price its own session without a price table. It runs on
demand; whether it also keeps closed months' totals so a call stays fast is the builder's.

### 4. The Mac reads it over SSH **[Mac]**

*(My call, from the privacy review.)* One poller on the Mac, the menu bar plugin's own run, fetches
`spark usage summary --json` (with `spark status --json`) over SSH as Dan, with batch mode and a
short connect timeout, every minute or so for the status and every few minutes for the costs. It
writes an atomic cache file on the Mac, `0600`, outside the worktree. Each harness line reads that
cache; no harness opens SSH. No route is added to the front, which serves only inference routes so
that no key, `agent`'s included, can reach anything else; and usage stays off the gate's status
socket, which `agent` reads.

The purchase price, the rate per kWh and the time zone for months are in a private file on the Mac
only, never on the Spark. So the Mac's run of `spark usage` adds what the summary leaves out:

- **Electricity (estimate):** idle watts × hours on, plus (working − idle watts) × busy hours, at
  Dan's rate. The watts start at published figures for the AI TOP ATOM, about 37 W idle and
  100–160 W working, which go in the repo with their sources; whether idle with the residents
  loaded is higher isn't checked on this box. A metering plug, later, replaces the estimate from
  its first day, in a task that reviews it as a new device on the network.
- **Payback:** (all-time spend − electricity) ÷ the Spark's all-in price, in US dollars (price, tax
  and shipping; a price paid in another currency converted once, at the purchase date's rate).
- **The break-even date:** what is left, at the last 30 days' net daily pace; "—" until there are 14
  days, and "10y+" when the pace is negligible.
- **Months** in Dan's time zone, from the UTC times.

When the Spark can't be reached the cache keeps the last numbers, shown greyed with their time,
never blanked; a month's spend only grows, so they are a true lower bound. Last month's figure is
never carried into a new month.

### 5. The menu bar **[Mac]**

One SwiftBar plugin, Phase 2b's. **Its title is a dot alone:** ok, loading, brake, unreachable,
tunnel down. **Its dropdown** has the status line with the summary's age; this month (with the
electricity estimate beside it), last month and today; all time with its share of the Spark's
price; the break-even date; the loaded models and the room for a load; the tunnel, from the front's
keyless `/health` through it; "refresh now"; and 2b's planned actions and agent sessions. Phase 2b's
planned title, the models and the free room, moves into the dropdown.

### 6. Each harness's line **[Mac]**; `agent`'s **[Spark]**

**What it shows,** after Dan's Claude Code status line: the session's cost, labelled an API
equivalent; this turn's cost and time; subagents' cost, where the harness records them; this month's
Spark total, from the cache, greyed when stale; tokens in, out and `think≈`; `⚠map` for an unmapped
model; a ⏳ timer while a request has had no first token (a cold load looks like a slow first
token); tokens a second from the first token. The session numbers come from the harness's own
per-message usage, priced at the summary's rates; the month from the cache. Nothing blocks the
harness: refreshes run in the background.

**Per harness** (from reading each one):

| Harness | Where the line goes | Session data |
|---|---|---|
| pi | an extension: `ctx.ui.setStatus`, on `message_end` and `turn_end`; the shared code run without waiting on it | each message's `usage` |
| OpenCode | a TUI plugin in the `prompt.footer.status` slot | `session.usage.updated`; a session family covers its subagents |
| Posit Assistant | none of its own: a small Positron extension with a status bar item, watching its session files | its `lm-messages.jsonl` |
| Hermes | none a plugin can draw: a `/spark` command shows the session | its `post_api_request` hook |

Hermes's and Posit Assistant's requests still count at the front, so they are in the month and on
the menu bar either way.

**`agent`'s pi** shows its own session's numbers, from its own files and the shared code, and its
own key's figures if they are ever wanted; never the Spark-wide month, never the payback, which is
Dan's total in disguise, never another key's. The mapping and the prices, which aren't private, sit
where `agent` can read them; the summary and the records never do.

### 7. A key per harness **[Dan]** **[Mac]** **[Spark]**

`make clients` gives each harness a key variable of its own; today pi, OpenCode and Hermes share
`SPARK_API_KEY`, so a refusal sent to OpenCode says "pi on the Mac". For each: Dan creates the key,
keeps its value in `~/.secrets` by reference, puts its digest on the Spark and its line in the
private key list (a label pi's retry list won't match); one `make restart-front` takes the batch.
The shared key is retired after, including the copy the Spark's own runbook `curl`s use, which gets
a key of its own, and those runbooks change with it. Posit Assistant keeps its key in Positron's
secret store, so whether it can take one is checked first. **The caps:** today's are per key
(Dan's group may have 8 waiting, meant to sit below llama-swap's 10 per model), so five of Dan's
keys could queue 40; they become per group, or lower per key, with a test (the builder's choice).
**Key names are never reused,** and a retired key keeps a line in a private list, so its old rows
keep their label. The keys come in with the counter, so its rows are split by harness from the
first.

### 8. The lifetime baseline, once **[Mac]** **[Spark]** **[Dan]**

One row per past message, in the row's schema with three more fields: its source (harness), the
format read, and an id (a hash of the file and the message's id) that an import upserts on, so a
re-run never counts twice. The key is empty. **The seam:** the counter's first row's time is stored
once; the baseline takes messages from 2026-09-28 up to that time, per message, so a session that
runs across it isn't counted twice.

| Source | Where | Reading it |
|---|---|---|
| pi on the Mac | `~/.pi/agent/sessions/**/*.jsonl` (skip `*.lease`) | assistant messages whose `provider` is `spark`; usage gives uncached input, output, cache reads; a compaction's usage takes the model in effect |
| OpenCode | `~/.local/share/opencode/opencode.db`, read-only | `session_message` assistant and compaction rows of the brightroar providers; children counted once; the old v1 tables skipped, which repeat 9 sessions |
| Hermes | `~/.hermes/state.db`, read-only | `session_model_usage`, the finest it keeps (session × model × task), where its recorded endpoint is brightroar's; a model given as a GGUF path mapped to the registry's name. **Hermes deletes sessions after 90 days, so this runs before 2027-01-07** |
| Posit Assistant | `~/.posit/assistant/workspaces/**/lm-messages.jsonl` and `usage-events.jsonl` | messages from the `openai-compatible` provider; it stores uncached input as cache *writes*, which the reader moves back to input for every such message |
| Open WebUI | its database on the Spark, as a read-only snapshot in a fresh temporary folder | the `chat_message` table's usage and model; it may add several calls per message, and may count a doubled `timings` twice, which the builder checks |
| `agent`'s pi | `agent`'s sessions, same format as pi's | run as `agent` by Dan, interactively (`sudo -u agent`); its files treated as hostile (capped, model names checked against the registry); only numbers leave, into Dan's importer |

**Which provider was brightroar.** Only Hermes records the endpoint; the others record a provider's
name. Each reader takes the names that reached brightroar: what `spark clients` writes (`spark`),
the two by-hand engines that ran on it (`spark-sglang`, `spark-flash-next`, *counted: my call*), and
Posit Assistant's `openai-compatible`, inferred to be brightroar (one such account, all after
2026-10-19, brightroar's model names, llama.cpp answering). Everything else is left out: the Mac's
own Ollama and LM Studio, Lander's Spark, cloud providers. No hostname goes in code, tests or docs.
From now on brightroar's provider names are its own: a provider for the work Spark gets a different
name in every harness (the provider-rename note, issue #28, on the Posit Assistant branch).

**The by-hand engines after the counter starts** *(my call, from the builder check)*. `make
fast-coder` and `make flash-next` are reached over tunnels of their own, not through the front,
until Phase 4a puts them in the registry, so the counter never sees them. The readers keep importing
those two providers from the harness files after the counter's start, not only before it; the
upsert on each message's id makes a re-run safe, and the summary says when they were last imported.
No second upstream is added to the front for engines that are temporary.
No reader reads message text: only numbers, ids and times, and an error never quotes a file. Test
fixtures are invented. The baseline is snapshotted once, since harnesses prune their files.

A first pass over the Mac's harnesses (aggregates only) found about 38.6M uncached input, 2.45M
output and 163M cache-read tokens since 2026-09-28: roughly $236 at Opus's rates, or $134 at
Sonnet's. Open WebUI and `agent` aren't in it.

### 9. What changes with it

- **The Architecture page:** the front writes the records; the gate's ledger; the summary to the Mac
  over SSH; the menu bar; each harness's adapter. Dashed until built.
- **A scenario page** for the meter, with its `spark doctor` check: the folders' owner, group, mode
  and setgid bit, no file readable by others, counting on.
- **The plan:** where this goes in the phases, with a Revisions line; Phase 2b's menu bar line (its
  title); and a note on Phase 6's "nothing anyone sends is logged", since each request's counts are
  now kept for every key.
- **The private material table** (`CLAUDE.md`, README §My environment, the vault's entry note): the
  records' and the ledger's folders, the Mac's cache, the private file with the price and rate, the
  retired keys' list.
- **Ignore patterns** for any local output, and the key names and price in Dan's denylist, which he
  adds himself.
- **`changelog.md` and README §Current state** when it is deployed.

### 10. The order of the work *(my call)*

1. **[Dan] [Mac] [Spark]** The keys per harness, and **[Spark]** the counter: the front's rows, the
   gate's ledger, bootstrap's folders, doctor's check, the scenario page. Deployed on `phase-2a`'s
   stack. Counting starts; nothing to see yet.
2. **[Spark] [Mac]** `spark usage`, the mapping and the prices, the summary, the Mac's private file
   and cache, and the menu bar.
3. **[Mac]** pi's line first, then OpenCode's, Posit Assistant's extension and Hermes's command.
   **[Spark]** `agent`'s line.
4. **[Mac] [Spark] [Dan]** The baseline, before 2027-01-07.
5. The electricity estimate is in step 2; the plug is a task of its own, whenever Dan buys one.

## Left to the builder

The file formats' details and field names; the writer thread and the scanner; how audio seconds are
found when the client didn't ask for `verbose_json` (only bytes in is certain); whether the summary
keeps closed months; the poll's timing; the dropdown's exact layout; how busy hours are merged from
overlapping requests; the caps' fix; Hermes's command.

## Open for later

- **The dashboard,** its own project.
- **SGLang's streaming usage** (Phase 4a): it reports usage in a stream only when asked, or with a
  server flag that changes what every client gets; Phase 4a decides, and until then such rows say
  their counts are missing.
- **Phase 6:** what is kept of other people's rows, and for how long; and a per-person breakdown
  inside Open WebUI, whose users share one key.
- **Backups,** with Phase 5's NAS.
- **The metering plug.**
- **Outside this repo:** Dan's Claude Code status line prices from `~/.claude/model-pricing.json`,
  last checked 2026-08-27, at Opus 5's rates and with cache reads at 10%; its month-to-date reads
  high. Offered separately.

## The calls made here, for checking

1. The records' place and modes: `/var/lib/local-ai/usage`, `spark-front:spark-admin 2750`, files
   `0640`; JSONL, one file a month.
2. Counting never in the way: a writer thread, a bounded queue, dropped rows counted, the folder
   opened lazily.
3. The row's fields, the thinking estimate from counted events, and no row for 401s.
4. The gate's own ledger.
5. The mapping and prices in the repo; mapping to a tier; every chat model starting at `opus`.
6. The summary fetched over SSH by one poller on the Mac; no new route, nothing on the status socket.
7. The price, the rate and the time zone in a private file on the Mac only.
8. `agent`'s line limited to its own numbers.
9. The two by-hand engines and Posit Assistant's `openai-compatible` counted as brightroar.
10. The order of the work, with the keys first.
11. An interrupted turn with no prompt count left unpriced and flagged, not estimated.
12. The gate's ledger readable by `spark`, as the gate's state is.
13. The by-hand engines imported from the harness files after the counter starts, until Phase 4a.
14. Brightroar's provider names reserved; the work Spark's provider named apart.
