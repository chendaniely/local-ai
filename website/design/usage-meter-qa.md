---
title: "The usage meter — questions and answers"
description: "How the Spark's usage meter is being decided: the tokens its models use, what they would have cost at Claude's prices, and how far the box is from paying for itself, shown in each harness and on the Mac's menu bar. Each answer with its reason."
date: 2026-10-22
---

# The usage meter — questions and answers

On 2026-10-22 Dan asked for a way to count the tokens the Spark's models use, what that work would
have cost, and how far the box is from paying for itself, with "a fun little menu bar like we do
for claude on cost". This page keeps the questions and his answers in the order they came, as
[Phase 2a's](phase-2a-qa.md) does. Once Dan approves the design, it goes into [the plan](plan.md).

## Where the work starts

The front, which every request to the models passes through, exists only on `phase-2a` since that
branch's cutover (2026-10-16), and nothing deploys from `main` until Phase 2a merges.

**Dan:** "we should worktree this off phase-2a then, not main. so your context is correct." The
work branches from `phase-2a`.

## What "spend" is priced at

**Q: The Spark charges nothing per token, so its spend is what the same tokens would have cost
where they are charged for. At whose prices?**

**Dan: Claude's.** "we can pin to the claude prices."

## What each place shows

**Dan**, in his words: "in the harness itself, I like everything that I am looking at in claude code
so we should have some parody there so there's things around current session running cost with some
information around sub agent running cost, and then the monthly total running cost along with the
current number of tokens in out and thinking tokens. The current session cost is useful, but in the
menu bar, I really only need the current monthly cost of token spent on the spark, and then we can
have a global cost as a percentage of the cost of the spark as a denominator."

So there are two places:

- **In the harness:** the session's running cost, what its subagents cost, the month's total, and
  the tokens in, out and thinking, as his Claude Code status line shows them for Claude.
- **On the menu bar:** only this month's spend on the Spark, and the spend of all time as a
  percentage of what the Spark cost.

## Which harnesses

**Dan:** "i'd like this for the pi, opencode, posit assistant, hermes, pretty much any harness i
want to use." The harness half can't be built for pi alone: whatever each harness needs has to be
small, and adding one more harness has to be cheap.

## One codebase

**Dan:** "so we really need 1 codebase to calculate everything and then the individual harnesses
are reading/using the same code." The counting, the prices and the sums live in one place, and
each harness only reads from it or calls it; no harness keeps its own copy of the prices or its
own arithmetic.

## Which Claude model a Spark model costs as

**Q: An afternoon in pi with the coder (`qwen3.8-27b`): 4M tokens in, 3M of them from cache, and
200K out. What should the meter say that cost?**

- A: every model that writes text at Sonnet 5.5's prices, $4.60.
- B: every one at Opus 5.5's, $8.60.
- C: each matched by size: the coder at Sonnet 5.5's, the smaller Gemma 4 and Qwen3.6 at Haiku
  5.5's.

**Dan: none of them as asked: a mapping he edits.** In his words: "we should have a way where i can
map a local model to a claude model and price, and if i change it the cost shoudl also be able to
update. we can store this in a yaml/json file of sorts. i pretty much only use opus for all my work,
so there's the simple way of just saying that, but true, not all models are opus level. i know this
is for fun, but we do want to be able to have a somewhat accurate calculation,a nd make
updates/changes to the pricing as needed. also prices might change over time, so i need a way to
update that as well. let's think though how this data collection might work."

So each local model maps to a Claude model in a file Dan edits, and Claude's prices are a file too,
since they change over time. A change to either shows in the costs, so what is stored is tokens, and
the cost is worked out from them each time.

## A lifetime baseline

**Dan:** "once this counter is on the dgx spark and the coutner is working i want to work backwards
on all harnesses i have on this current computer and tally up the model for an accurate lifetime
cost as well so we can use that as a baseline and then it gets updated on the spark." The harnesses'
own session files on the Mac give the tokens from before the counter, and the Spark's counter
carries it on from there.

## When the mapping or a price changes

**Q: In January the coder does $300 of work, charged as Opus 5.5 at $4/$20. In March two things
change: Anthropic cuts Opus to $3/$15, and Dan remaps Gemma from Opus to Sonnet. What happens to the
numbers from before March?**

- A: remaps reprice, and prices are dated. January stays $300, at what Opus cost then; Gemma's past
  is recharged at Sonnet, since a remap corrects a judgement about the model, while a price is a
  fact about its day. Each price line carries the date it took effect.
- B: everything at today's prices. January becomes $225, and Gemma's past is recharged too; the
  lifetime figure, and the payback bar with it, moves whenever Anthropic changes a price.
- C: everything dated. January stays $300 and Gemma's past stays at Opus; a remap applies only from
  March on.

**Dan: A**, and in his words: "re shoudl allow remapping. i could also make the misttake of putting
a model at a lower/highter priced claude and want to chagne/update that. so tracking model + tokens
will allow calculation flexibility."

## What the baseline covers

**Q: The counter goes live, and the baseline tallies everything the Spark served before it. Besides
the Mac's pi, OpenCode, Hermes and Posit Assistant sessions, the box has Dan's Open WebUI chats and
`agent`'s pi sessions since 2026-09-28. What goes into the baseline?**

- A: all of it, wherever it kept token counts. The lifetime figure is the box's, so everything it
  served counts; `agent`'s sessions are read by one command Dan runs with `sudo -u agent`.
- B: the Mac's harnesses only, as first asked; the web chats and `agent`'s work before the counter
  are left out.
- C: the Mac's first, and Open WebUI and `agent` later, once reading up shows what they kept.

**Dan: A**, the recommendation, for the reason it gave. What has no file — scripts, raw `curl`
calls, llama-swap's activity, which each restart wipes — can't be counted either way.

## Only brightroar's models

**Dan**, in his words: "note that i have access righ tnow to multiple "local" models, i'm
speficilaly looking for the ones connected to the dgx spark (brightroar) i shoudl not be counting
local models runnign on the actaul mac, and i should not be counting my [work] dgx spark (under [its
address] which is what i had access to earlier int he year)." *(The work machine's owner and its
address are left out here: a third party's machine, and a name that resolves from outside, don't go
in this public repo.)*

So the baseline counts only what brightroar served. A model's name can't decide it, since the
Mac's own models or the work Spark may use the same names: a session counts when its provider
reached brightroar, and nothing before 2026-09-28, when brightroar began serving, counts at all.

## Breakdowns by app and user

**Dan:** "on the spark side, since i'll having different apps and users, we should have a way to
track that as well. so we can have breakdowns; again, not sure if agentsview will come into play
for this." Every request already reaches the front with a key, and the private key list gives each
key a group (who) and a label (which app), so each record keeps its key's name, and the group and
label are looked up when the numbers are read, as the mapping is. agentsview reads session files
on the machine it runs on, so it can't see what the front sees: Open WebUI, `agent`, pipelines, and
from Phase 6 other people's devices.

**Q: Next month the Spark served 6M tokens to the Mac. Dan opens the breakdown to see how much was
pi, OpenCode, Hermes and Posit Assistant. How should the Spark know which was which?** Today pi,
OpenCode and Hermes on the Mac share one key, `SPARK_API_KEY`.

- A: a key per harness. `make clients` gives each harness a key of its own, each with its own
  label on the Spark; apps get theirs from Phase 3 and other people from Phase 6, as planned. The
  breakdown is exact, and a refusal names the right harness: today one sent to OpenCode says "pi
  on the Mac".
- B: one key, and each request's User-Agent recorded beside it. No new keys, but harnesses on the
  same SDK may send the same User-Agent (not checked), and scripts send only a library's name.
- C: both, the key for who and which app, the User-Agent within a shared key.

**Dan: A**, the recommendation, for the reason it gave.

## The fun part and the dashboard

**Dan:** "this is almost becomming a dashboard app on the sparkside, but the simple numbers i want
tracked in the harness / menubar that's a fun thing to look at." The simple numbers in each
harness and on the menu bar are what this design builds; a dashboard of the breakdowns on the
Spark is a project of its own. The records keep what one would need.

## The goal, and power

**Dan**, asking for a council to look for what's missing: "the very long term end goal is to see it
on a dashbaord, but really i want to track the dgx usage cost and know how far away we are from
breaking even (we're not tracking power consumption, unless you think that's remotely possible)."
So the measure of the design is the Spark's spend at Claude's prices and the distance to break-even;
the dashboard is the long-term end, and power is out unless the council finds it within reach.

## As fine-grained as possible

**Dan:** "as a datascientist i like looking at data, so when possible capture data in as granualar
as possible, so we can aggregate things up." So the records are one row per request, never totals,
with every number and id the front can see; the baseline keeps one row per past message, not a sum
per session. Numbers and ids only: never what was asked or answered.

## Costs needn't be live

**Dan:** "the cost values don't need to be refuleted in realtime, it's okay if those are delayed /
updated preiodically/on-demand." So the Spark's totals can be worked out now and then, or when
asked, rather than kept current with every request.

## After the council

Four reviewers, each with a lens (break-even and power, the records, parity with Dan's Claude Code
status line, and privacy and operations), and three readers of the harnesses and engines, looked for
what was missing on 2026-10-22. Their findings go into the design; the questions only Dan could
answer follow.

**Q: Phase 4's bake-off runs real tasks through pi on each candidate model, plus speed tests, which
could be a few million tokens over a weekend. Should those count toward break-even?**

- A: tests get their own key in a `test` group, recorded and shown in breakdowns but left out of the
  payback by default.
- B: count everything; it all ran on the box. The payback bar jumps during a bake-off.
- C: no new key; test rows are left out afterwards by the dates the tests ran.

**Dan: B.** Everything the box serves counts toward break-even, tests included.

**Q: A year from now the menu bar says 60% paid back. How should electricity figure in that
number?** The box can't measure its own draw: `nvidia-smi` reports the GPU alone, and there is no
management controller to ask. A metering smart plug with a local API can, at the wall.

- A: estimate now, plug later. Published figures for this model (about 37 W idle, 100–160 W
  working) times the hours on and the busy hours, at Dan's rate, subtracted from savings and
  labelled an estimate; a ~$25 metering plug's readings replace the estimate from the day one is
  added.
- B: the estimate only, never a plug.
- C: leave power out; the bar reaches 100% a little early, by about $3–10 a month.

**Dan: A**, the recommendation, for the reason it gave. The plug, when it comes, is reviewed as a
new device on the network in the task that adds it, since its relay can cut the Spark's power.

**Q: Dan glances up at the Mac's menu bar mid-afternoon. Phase 2b's plan put the loaded models and
the free room in the title; he asked for this month's spend and the percentage. What does he see?**

- A: one icon, with this month's spend and the percentage in the title and a coloured dot for ok,
  loading, brake or unreachable; the models, the free room, the tunnel and the break-even date in
  its dropdown.
- B: two icons, 2b's status icon as planned and a meter icon of its own.
- C: one icon with 2b's title, and the money in the dropdown only.

**Dan: A, without the money in the title.** In his words: "money does not need to be in the title.
i dont' need that when i'm sharing my screen. the menubar just needs a conection status dot. allt
he other details should be showen when clicked." So the title is a dot alone, and every number — the
month, last month, today, all time and its percentage, the break-even date, the loaded models, the
free room and the tunnel — is in the dropdown. Phase 2b's planned title, the models and the free
room, moves into the dropdown too. It also keeps the price private on a shared screen: the all-time
spend and its percentage together would give it away.

## After the builder check

A fresh reviewer read [the design](usage-meter.md) as its builder would. Three of its questions
were settled in the design as calls for Dan to check; two were his.

**Q: Phase 2a is at Task 40 of 52, with its soak, its drills of the front and the gate, its review
and its merge still to come. The counter changes the front, the gate, bootstrap's folders and every
Mac key. Where does it go?**

- A: the keys per harness and the counter become Phase 2a tasks before its soak, so the soak, the
  drills and 2a's review cover them, deployed from `phase-2a` as usual; the rest (the summary, the
  menu bar, the harness lines, the baseline) becomes Phase 2b's first part, and the menu bar is
  2b's planned SwiftBar plugin, built once. Posit Assistant's line and key wait for its branch to
  merge.
- B: its own phase from `main` once 2a merges; counting starts weeks later.
- C: deployed from this branch now, as an exception to the deploy rule, under 2a's drills and
  without its review.

**Dan: A**, the recommendation, for the reason it gave.

**Q: The baseline reads every session on the Mac since 2026-09-28 from a provider named `spark`,
`spark-sglang`, `spark-flash-next`, or Posit Assistant's `openai-compatible`. Did any of those ever
point at the work Spark, or anywhere but brightroar, in that time?** pi and OpenCode record only a
provider's name, so their files can't tell.

- A: no, all brightroar; the readers decide by name, and from now on the work Spark's provider gets
  a different name in every harness.
- B: yes, sometimes; those sessions are left out by date.
- C: not sure; counted, but marked as inferred.

**Dan: A.** Those names only ever reached brightroar.

## The design's council, and spark-menu

Five reviewers read the written design (fit with the code and engines, privacy and security, the
numbers, fit with the plan and its docs, and a skeptic looking for what could be simpler). While
they worked, Dan pointed to another session building **spark-menu** in his private dotfiles: a
SwiftBar menu for the Spark, with its own tunnel, poller and dropdown, which "will be pulling data
from this". Its own design calls it a prototype of Phase 2b's menu bar that can move into this repo.

**Q: The usage meter and spark-menu both want a menu bar, a Mac poller and the Spark's numbers.
spark-menu is mid-build in dotfiles. How should the code come together?**

- A: one contract now, merge later: this repo counts and calculates, spark-menu only displays, and
  the code merges when spark-menu moves here for Phase 2b.
- B: move spark-menu here now.
- C: keep them apart for good.

**Dan: wait, then consolidate here.** In his words: "let's wait for the other repo to finish their
build and we can move it over here and just consolidate it all into this repo now. so i will wait".
So spark-menu finishes its build in dotfiles, then moves into this repo, and this repo holds all of
it: the counting, the arithmetic, the menu bar and its tunnel. The meter builds no menu bar or
poller of its own; it gives spark-menu the numbers to show.

**Q: Mid-session in pi, Dan glances at its footer. Where should that session's dollar figure come
from?**

- A: the harnesses' own cost display, with rates `make clients` writes from the one price file; a
  thin adapter adds the month.
- B: each harness's adapter hands its usage to `spark usage` and shows what comes back.
- C: the built-ins now, ours later where they fall short.

**Dan: think long term.** In his words: "let's think long term: eventually i'd want a cost/usage
breakdown dashboard to view data on the spark, so we need a way to keep the spark data up to date
(even if it's not in real time)". So the Spark has to hold everything a breakdown would use, a
session's identity included, which only the harness knows, and keep it current, if not live.

**Q: A year from now Dan opens the dashboard on the Spark and filters to one long pi session from
last Tuesday, with its subagents. Only the harness knew that session existed. How should that
detail reach the Spark, and what does pi's footer show meanwhile?**

- A: a sync. Every minute or so the baseline's readers read what's new in each harness's session
  files and send per-message rows (session, parent, harness, response id) to the Spark, joined to
  the front's rows; the baseline is the sync's first run. pi's footer shows the Spark's own figure
  for the session, a minute or two old, so every number comes from one place.
- B: the same sync, and pi's footer also prices the current session itself so it updates every
  turn; the Spark's figure is the record.
- C: each adapter tags its requests with a session id the front records; the sync is still needed
  for the past and for subagents.

**Dan: A**, the recommendation, for the reason it gave: one place computes every number, and a
footer a minute or two behind is fine.

**Q: It's January, and Hermes has quietly pruned its October sessions before the sync's first run.
How does the baseline still have them?** Hermes deletes ended sessions after 90 days; its oldest
brightroar session is from 2026-10-09 (derived, not checked in Hermes).

- A: snapshot the Mac's sources now: read-only copies of Hermes's and OpenCode's databases and pi's
  and Posit Assistant's session folders, in a private folder outside the worktree; no ticket needed.
- B: a YouTrack ticket in DAN, due 2026-12-20.
- C: leave it to the Phase 2a task that starts the counter.

**Dan: A.** Taken on 2026-10-23, in `~/.local/state/local-ai/usage-snapshots/2026-10-23/` on the Mac,
`0700`, its files Dan's alone; both databases pass SQLite's integrity check. Hermes's request dumps,
which hold an Authorization header, and Posit Assistant's developer request logs were left out.

## What counts, projects, and the by-hand SGLang (2026-11-09)

**Posit Assistant's key. Dan:** "i'm not paying for posit assistant right now. just doign that through
the spark, so we can count those as spark tokens for now." So it stays on the shared key, and its
tokens count under Pi's key name for now: right in total, blended by harness.

**What the counter is for. Dan:** "Some of these harnesses will allow me to plug into other models
and other providers so I do wanna make sure that the token counter is really just for the models
that are going through the spark, and the list of models may change in the future so I do want to
be able to track all of that and potentially create a dashboard around spark model usage really
similar to how the KENN .io agentsView is able to look at model token usage, and project usage." The
front's rows already hold to the first two: a request sent to another provider never reaches the
front, and each row names the registry's model. The dashboard stays the project of its own it was.
**Project usage isn't designed yet:** the front can't see a project, and the sync's rows carry
session, parent, harness and response id, not the working folder. The design's *Open for later*
holds it for 2b.

**The by-hand SGLang. Dan:** "we should put a placeholder since right now all my tokens are coming
from the mac, we should be able to go back into the mac and backfill all the SGLang tokens for
measuring? i'm currently using the SGLang model as the default right nwo." The design's §7 already
makes the harness's files the record for the by-hand engines; *Open for later* holds what the
backfill needs.

**Lifetime and monthly cost in the menu bar. Dan:** "i'd also like my swiftbar to start tracking
lifetime token cost and maybe also by month. and then we can work on separate dashboard to view data
to break down by other dates and models". The design's §4 already holds that, in 2b: its dropdown
shows this month, last month, today and all time. A monthly figure needs the sync as well, since the
by-hand SGLang doesn't pass through the front. The separate dashboard stays the project of its own
it was.

## The menu's numbers now (2026-11-12)

**What Dan asked.** "the swiftbar application is located in the docfiles repo. i want to implmeent the
spark model token cost into it so that data is aviable on the spark dashboard and is constnatly
updated (doesn't need to be realtime a delay is okay)". The counter (Tasks 44a–44j) is merged into
`phase-2a` but not deployed (Task 44l), and `spark usage summary`, the mapping and the prices are
Phase 2b's, so the menu had nothing to show.

**Q: When does the Spark's half get built: the summary, the mapping and the prices?** Offered: with
the counter's deploy, in Phase 2a, before the soak (recommended); the menu now against invented data,
with the Spark's half kept for 2b; or the Mac pricing the raw rows itself.

**Dan: with the counter's deploy, in Phase 2a.** "i want to go with option 1, i want to get the token
cost estimates now. base it off of claude pricing. i should have some notes in the past plan on what
i want the data to be formatted like so it can be changed as models and pricing changes over time and
i want the data in enough granuar form that they can be aggregated and viewed in different ways for a
larger spark dashboard application". The notes are the design's §3: a mapping of each local model to
a Claude tier, dated price lines, rows that keep tokens and never dollars, and a summary that totals
by UTC hour, key and model.

**Q: The by-hand SGLang coder, Dan's default, doesn't pass through the front, so the counter can't
see its tokens. What does the menu do about it?** Offered: a placeholder line, with its tokens read
back from the Mac's harness files later (recommended, from Dan's words of 2026-11-09); a Mac reader
for them now; or the fast coder put behind the front now.

**Dan: read them on the Mac now, and plan the front for later.** "calculate the data from the mac now.
so we dont' need more infraastructure from the mac, then plan local-ai like option 3. OR i can just not
use the SGLang model". So the fast coder's tokens are read from the Mac's harness files now; the plan
gains putting the fast coder behind the front, which ends that reading's window; and if Dan stops
using SGLang, the Mac's reading is only needed for its past.

**After the builder check: the fast coder's cache.** Its rows never report a cache, and priced as all
uncached at Opus's rates they read about twenty times too high, since agent sessions send their whole
history every turn. Offered: estimate the cached part as the previous message's prompt in the same
session, worked out when read, shown apart and marked `~` (recommended); price it all as uncached,
flagged; or leave those rows unpriced until the fast coder reports its cache. **Dan: estimate it.** He
picked the recommended option, with no words of his own. [The first numbers' design](usage-meter-now.md),
part 2, has the rule.

**After the builder check: price edits during the soak.** Nothing deploys during Phase 2a's soak, and
`make apply` restarts the gate and the brake for any change to the app. Offered: the price files ship
with the app, and remaps wait for the soak's end, losing nothing since a remap reprices the past
(recommended); a restart-free path, with a written exception to the soak rule; or the live files kept
in Dan's home on the Spark, edited in place. **Dan: wait for the soak's end.** He picked the
recommended option, with no words of his own.

**The design approved.** Dan, 2026-11-12, after reading [the first numbers' design](usage-meter-now.md):
"plan look sgood. just let me know what commands on what computer and what branch need to be run". So
the Mac's half gets built on this branch and in the dotfiles, and Dan gets the commands for the Spark's
half, each with its machine and branch.

**How it gets built.** Offered, with [the implementation plan](usage-meter-now-plan.md) written: a fresh
implementer and reviewer per task, then a whole-branch review, as Tasks 44a–44j were built (recommended);
every task in one session, with one review at the end; or only the local-ai half now. **Dan: a subagent
per task.** He picked the recommended option, with no words of his own.
