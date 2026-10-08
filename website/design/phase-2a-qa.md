---
title: "Phase 2a — questions and answers"
description: "How Phase 2a's design was decided: the scenario questions Dan answered, the architecture question, the coder and the budget, what the council raised, how Dan wants the gate to read to him, the decisions on the implementation plan's forward-and-back council, and how the scenario pages read before their rewrite. Each answer with its reason."
date: 2026-10-07
---

# Phase 2a — questions and answers

Phase 2a's design ([the plan](plan.md), Phase 2a) was decided by talking through scenarios: a
situation Dan recognizes, then "what do you expect?", with options and a recommendation. This page
keeps those questions, his answers and the reasons, in the order they were asked (2026-10-05 to
2026-10-07). The plan holds the decisions; this page is how they were reached, so that the next
question about a case has an answer to start from. Each [scenario page](../scenarios/index.qmd)
the answers shape links back here.

The recommendation was option A unless a question says otherwise; where Dan chose differently, the
answer says so. The questions keep the words of the day: where one says "free" for the box's
available memory, the plan now says *available*, and keeps *free for a load* for what a model can
use (*How it should read to Dan*, below).

## The shape of Phase 2

**Q: Phase 2 as written is four pieces that could each be a phase — the gate, visibility, the lab,
and hardening and measurement. How should it be carved?**

- A: three sub-phases, 2a the gate → 2b visibility → 2c the lab, each with its own plan, review and
  merge; each hardening item lands with whichever sub-phase touches its code.
- B: one Phase 2 plan, as Phase 1 was, about twice its size.
- C: the lab and the Qwen3.8 trial first, before the gate.

**Dan: A.** The gate comes first because the menu bar and the notifications report its events, and
the lab needs it to admit trial models.

## Scenarios for the gate

**S05, the brake overnight. *Overnight a data-science job grew, the brake fired at 20 GiB available
and unloaded the coder. The job finished hours ago; 90 GiB is free. At 9 am you ask the coder
something. What do you expect?***

- A: the pause lifts by itself once memory has stayed above the warn line (28 GiB) for a few
  minutes; the request loads the coder; the morning notification says when it fired and released.
- B: it stays until `make brake-release`, as in Phase 1.
- C: it lifts only after a one-click release on the menu bar.

**Dan: A.** *(The council later bounded it — see* After the council.*)*

**S03 and S04, a load that doesn't fit. *A GPU job leaves 40 GiB free. Your pi on the Mac asks the
coder, which doesn't fit with the reserve. Meanwhile `agent`'s pi, running unattended overnight,
asks it too.***

- A: waits set per key — Dan's keys wait up to 30 s (memory may be freeing), then a refusal with the
  numbers, the top holders and the options; `agent`'s key waits up to 10 minutes, then a refusal and
  a notification, since nobody is watching and a refusal fails its step.
- B: every key refuses at once.
- C: every key waits as long as it takes.

**Dan: A.** This is the answer that shaped the architecture: a gate that gives each key its own wait
has to know which key asked.

**S05 again, after the brake. *It had to unload Gemma as well. At 3:40 memory is back and the pause
lifts. At 7 am you ask Gemma about a photo from your phone.***

- A: the gate brought Gemma back by itself once it fit, the always-loaded models one at a time, as
  at boot; the notification lists what came back; on-demand models wait for a request.
- B: nothing reloads by itself; the phone's first question loads Gemma.
- C: like A, but only after a quiet half hour.

**Dan: A.**

**Notifications in 2a. *The gate makes the events you'd want on your phone, but ntfy was 2b's.***

- A: Dan sets up ntfy on the Synology at the start of 2a, so the gate notifies from day one; the
  watchdog, the menu bar and the agent hooks stay in 2b.
- B: 2a records events only in the journal and `spark status`.
- C: ntfy and the watchdog both move into 2a.

**Dan: A.** ntfy is deployed as a Docker Compose file, as all of Dan's homelab apps are (Portainer
stacks today, any Compose helper tomorrow).

**S17, changing a model mid-task. *`agent`'s pi is mid-task on the coder, you used Gemma a minute
ago, and you run `make apply` on a change that restarts llama-swap.***

- A: it shows the diff, waits until no request has been in flight for about 60 s, then restarts;
  the always-loaded models come back one at a time; Ctrl-C changes nothing; `make apply-now` asks,
  then restarts at once; a change that needs no restart applies at once.
- B: as A, without the 60 s quiet period.
- C: Phase 1's refusal, plus the diff.

**Dan: A.** *(After the council, the session's ruling added a 15-minute deadline, after which it
offers to drain.)*

**S01, a working day. *The coder unloaded overnight. You start pi at 9, then spend 45 minutes
reading code without sending anything.***

- A: nothing scheduled by default; `spark pin coder 8h` keeps it loaded through a pause; a weekday
  preload is a setting for later.
- B: a weekday preload and work-hours pin from day one.
- C: no pins at all.

**Dan: A.** *(The weekday preload's setting went to the backlog, since it would ship switched off.)*

## The architecture

**Q: llama-swap v257 doesn't tell an engine which key asked, turns any refusal into a bare 500,
shows requests in flight only through a lossy event stream, and listens on TCP only. Where does the
gate sit?**

- A: a two-part gate in front of llama-swap — a small front on 127.0.0.1:9100 that knows each
  request's key, counts what's in flight and returns refusals that say why, and a gate behind it
  that makes every decision.
- B: the gate beside llama-swap, admitting inside `spark launch`, as the plan first had it — simpler,
  but one shared wait and bare 500s.
- C: LiteLLM brought forward from Phase 3 as the front.

**Dan: A** (2026-10-07). The findings it rests on are in the plan's Revisions.

**Q: Everything on the Spark runs on bare metal. Should it move to Docker some day — and doesn't
Docker take memory the models need?** (Dan, 2026-10-07.)

Measured that day: Docker's own processes (`dockerd`, `containerd` and two shims) held **about 160
MiB together**, 0.13% of the box; a container is an ordinary process in its own namespaces, so a
model's weights and KV cache take the same memory in a container or out of one. So memory isn't the
reason. The reasons for the mix the plan already has — bare metal for the engines, llama-swap and
2a's front, gate and brake; Docker for apps that ship as images (Open WebUI, SearXNG, and later the
vLLM and SGLang routes) — are others: the GPU without a container toolkit in between; systemd
already gives restarts, sandboxing and a user per service; Docker access is root access, which is
why `spark` stays out of it; and CUDA images run 10–20 GB each on a 1 TB disk the models share. It's
worth revisiting if a needed engine ships only as a container, or if untrusted model code ever wants
stronger isolation. **Dan: keep the mix,** and revisit full containerization later if a reason
appears — "since everything is documented in this repository we could tear everything off and
reinstall" ([S18](../scenarios/s18-rebuild.md)): the repo, not a container image, is what makes the
box reproducible.

**Q: Since the Spark is installed on bare metal, would Ansible be a good way to manage it?** (Dan,
2026-10-07.)

Not for the Spark, not now: the repo already does what Ansible would bring, shaped for this one box.
`stack/host/bootstrap.sh` is idempotent (Phase 1's fresh-clone drill re-ran it and changed nothing),
`make bootstrap-dry-run`, `make apply-dry-run` and `make install-units-dry-run` show a change before
it's made, 82 bootstrap tests run it on stand-ins, and Phase 1's listings ran in an `ubuntu:24.04`
container too, the registry is
the declared state that `spark render` turns into root's own copies (installed with a diff shown
first), and `make doctor` checks the box matches. Ansible would duplicate that, and it works by
pushing from a control machine over SSH with sudo, where the repo runs work on the Spark itself and
asks for Dan's password at each root step; it's also one more tool chain to pin and update. Where it
would earn its place is **Dan's homelab as a whole** — users, SSH keys, Tailscale, Docker and Compose
stacks kept the same across the Synology and his other machines — as a project of its own, which
could include the Spark through a small role that runs this repo's `make bootstrap` and `make apply`
rather than replacing them. **Dan: later.** The Spark keeps its bootstrap and the `spark` CLI.

## The numbers Dan set

- **Quiet hours:** 00:00–05:00 — only high-priority alerts sound. *(They're set on the phone: ntfy has
  no server-side quiet hours.)*
- **ntfy's reach:** the tailnet or the home LAN only; out of reach means no alerts, for now.
- **The idle unload:** 60 minutes (the plan's default was 30), for models that load when asked;
  always-loaded models never unload for being idle.
- **make-room:** may unload everything, the always-loaded models included — "a great way to clean
  things up when I need to test something" — and `--all` clears the box after one confirmation.
- **Notification levels:** three priorities and *off*, as designed; all twenty types on by
  default.

## The coder

**Q: Dan wants Qwen3.8-27B working this round, as the default coder.** Route A (llama.cpp b11146 as
deployed, Unsloth's GGUF, MTP) needs nothing new, so it joined 2a.

- **Replace, or keep both coders?** **Dan: replace.** Qwen3.6-35B-A3B leaves the registry; its
  files stay on disk, to come back as a lab trial or a fallback.
- **Which file?** **Dan: UD-Q4_K_XL** (17.6 GB) — speed first; a dense model is bandwidth-bound here.
- **How does it fit at its full 262K context?** The recommendation was an 8-bit KV cache. **Dan
  asked why everything can't run at its maximum.** The answer: the budget check counted the 24 GiB
  reserve twice. **Dan: fix the check**, and at first also lower the reserve a little — 22 GiB, the
  session's figure for his "a little" — which the council then showed wasn't needed (below). Every
  model runs at full context with an f16 KV cache.

## After the council

Four reviewers read the design; Dan took every recommendation (2026-10-07). Rows decided later,
after the re-review or the final re-review, say who decided and when:

| Question | Answer, and why |
|---|---|
| The reserve | **Back to 24 GiB.** The corrected check is what makes the coder fit; 22 only halved the margin above the brake while every footprint is an estimate. |
| Where the brake runs | **Its own unit, as today.** Freeze protection doesn't share a process with a socket `agent` can reach, or with ntfy calls that might hang. |
| Limits on the automatic release | **One per hour, then it waits for Dan; a pause found after a reboot waits for Dan; it releases only if the reloads fit.** Otherwise a job hovering near the line fires and releases endlessly, loudly, at night. |
| The model that was loading when the brake fired | **It doesn't come back by itself.** Dan's own request loads it if it fits (Dan's choice after the re-review, over the council's first design, which refused his requests too); `agent`'s requests wait, then get a refusal naming `spark load <model>`, so an unattended run can't fire the brake again. |
| make-room's space | **It's held for Dan** until `spark make-room --done`, a time he gives, or a reboot — and his own requests may load into it (the session's ruling after the final re-review), so "make room, then try again" works. |
| `agent`'s 10-minute wait | **`agent`'s pi waits 15 minutes**, since pi gives up after 5 by default. |
| Port squatting | **systemd holds 9100 and the gate's sockets, even through a crash loop; llama-swap and the engines move below 1024.** No other local user can take a port a key goes to. |
| The front's user | **Its own, `spark-front`**, out of reach of the engines and the pull. |
| Hardening into 2a | **llama-swap's sandbox, root-only secret files, the pull's own user**, and, by Dan's choice after the re-review, **`cap_drop` for the web services.** The engines' own user waits. |
| `agent`'s own GPU jobs | **An OOM score root sets, in 2a if it holds when tested as `agent`.** |
| The tools | **uvicorn, a plain ASGI front, a Starlette gate and httpx** — two new packages. |
| Alerts on the Mac | **Phone only in 2a; the Mac with 2b's menu bar.** |
| The gate down | **The brake sends its own alert** when the gate is down, so Dan hears "brake fired", not only "gate down" (the session's ruling after the re-review). |

## How it should read to Dan

Dan, 2026-10-07: *"The user experience is really important … there should be no confusion"*, and
*"I'd rather err on more notifications than something not being clear at the moment; we can handle
which types get turned on and off later."* What that settled:

- **Every refusal is a sentence, the numbers, and one next step**, in pi and the web UI; the code is
  for programs.
- **Two words, two numbers:** *available* is the box's free memory; *free for a load* is what a model
  can use after the reserve and what's already promised. Never "free" alone.
- **Every notification type has a name and a priority in one list, all on**; turning one off later
  is a one-line change. Loads and waits get silent notifications too, so nothing happens unexplained.
- **One notification per event**; a burst of the same refusal collapses into one, with a count.
- **A refusal after a wait is a 409, not a 503** (the session's ruling, with the plan): pi retries a
  503 by itself, up to three times, so a 30 s refusal would have arrived after about 2¼ minutes;
  a 409 it shows at once. A failed load and a model not yet downloaded are 409s too, since no
  retry changes them. ~~Only an outage stays a 503, where a retry makes sense.~~ After the
  implementation plan's forward-and-back council, Dan made `restarting`, `llama_swap_down` and
  `draining` 409s as well, since they too come after the key's wait; only `gate_down`, which the
  front answers at once while the gate is down, stays a 503, which pi retries.
- **Nothing is written into a model's reply**: a wait shows as a slow reply, explained on the phone
  and in `spark status`.
- **Plain words:** *always loaded*, *loads when asked*, *paused*, models by their role first.

The plan's *What you see in Phase 2a* has every message and notification as written.

## After the implementation plan's forward-and-back council

Four reviewers read the whole branch before any task ran: the docs, the box and its code against
what the plan assumes, the plan as its executors will meet it, and what it means for the later
phases. **Dan: "all recommended"** (2026-10-07):

| Question | Answer, and why |
|---|---|
| `restarting`, `llama_swap_down` and `draining` come after the key's wait too: 503 or 409? | **409.** pi would retry a 503 three times, each with its own wait, so they would reach Dan after about 2¼ minutes and `agent` after about 40. Only `gate_down`, answered at once while the gate is down, stays a 503. |
| A `make apply` from `phase-2a` before the cutover would break the running stack | **No deploys from `phase-2a` until the cutover;** an urgent fix goes from `main`. |
| The web containers could take llama-swap's new ports until `cap_drop` | **`cap_drop` comes before the cutover,** made and deployed from `main`. |
| Dependabot's `spark/` bumps, `huggingface-hub` 2.0.0 among them | **Held until 2a merges,** since the plan pins the lock's package list. |
| The residents after an unplanned llama-swap restart, or an earlyoom kill | **They reload by themselves,** one at a time, as at boot and after `apply`. |
| The CUDA ceiling's method stops at the reserve, about 93 GiB on an idle box | **Record it as *at least* what it reaches, measured;** going lower is the freeze band. |
| Who sets the values a task measures (Gemma's slot similarity, the OOM scores) | **The controller rules, and tells Dan.** |
| A stopped gate's sockets start it again at once | **The gate-down drills make it fail at start,** with Dan's sudo, and put it back after. |
| Three tasks too big for one implementer, two too small | **Split the words, the gate's sockets and the units; merge the two pi checks;** 52 tasks. |
| Render's whole-set check would refuse later phases' registries | **A warning, with a `needs_room` flag** for a model that loads only after make-room. |
| One `dan` flag decides five things, and the key list is public | **Five settings of a key group, and the key list in a private file,** before the code exists. |
| The front reads the model list once, so a rename restarts it | **The front takes the model list from the gate's events.** |
| 2a's front narrows Phase 3's LiteLLM choice | **Phase 3 weighs a third option, no LiteLLM.** |
| Sessions need a process on the Spark, which Orca's pi on the Mac hasn't got | **`spark session hold`, in 2a,** for the Mac's hooks to run over SSH. |

## How the scenario pages read before the rewrite

On 2026-10-07, after the implementation plan's forward-and-back council, the 2a scenario pages were
rewritten as one current account each. Until then each gathered dated notes as the design moved —
the brainstorm's answers, the council's revisions, the UX pass and its corrections — and a later
note often reversed an earlier one. They are kept here as they read that day, for the history: the
plan holds the decisions, and these notes are how the pages followed them.

### S01 · Morning start, as it read on 2026-10-07

**Situation.** It's 9 am. The coder model unloaded overnight, and I start pi.

**What happens.** My first request loads the coder if it fits — tens of seconds with
llama.cpp. An optional weekday preload can have it ready before I ask. A "stay loaded while I
work" pin stops it from idle-unloading under me.

**What I see.** "Loading" on the menu bar, a low-priority "loaded" notification, and a slower
first reply.

**How to override.** `spark load <coder>`, the menu bar's Load, or `spark pin <coder>`.

*Designed 2026-10-07, for Phase 2:* Phase 2a builds the gate's part. The coder idle-unloads after
60 minutes with no request and no active session (this was 30), and the always-loaded models never
idle-unload. `spark pin <coder> [duration]` keeps it loaded, for as long as I say. The weekday
preload, with a work-hours pin, is a setting that is off by default; it is fit-checked and notifies
me if it doesn't fit. The low-priority "loaded" notification comes with ntfy in 2a; "Loading" on the
menu bar comes with 2b. From 2a the coder is Qwen3.8-27B.

*Revised 2026-10-07, after the design's council:* pins are mine only, set from my own account;
`agent` keeps its model with a session of its own instead. `spark load <coder>` and
`spark pin <coder>` reach the gate on the Spark with no API key. The "loaded" notification reaches
my phone in 2a, and my Mac from 2b. Whether 2a builds the weekday preload's setting, or leaves it
until I turn it on, I decide with 2a's plan. After 2a this page stays `planned`, with a dated note
recording its gate part, until 2b's menu bar completes it.

*What I see, worded at the design's UX pass (2026-10-07):* pi shows only a slower first reply;
nothing is added to its answer. My phone gets two silent notifications, *Loading the coder for pi
on the Mac (24 s last time)…* and then *Loaded the coder in 24 s.* `spark pin coder 8h` answers
*The coder stays loaded until 18:00 (`spark unpin coder` ends the pin)*, and when it runs out a
silent *The pin on the coder ended at 18:00; it unloads after 60 min idle.* `spark status` lists
the coder as *loads when asked*, the others as *always loaded*. The plan's *What you see in Phase
2a* has every message.

*Ruled 2026-10-07, with 2a's plan (I had left it to the session):* the weekday preload isn't
built in 2a. It waits in the plan's Backlog until I ask for it, so `spark load <coder>` and
`spark pin <coder>` are 2a's way in the morning.

### S02 · Big job while an agent works, as it read on 2026-10-07

**Situation.** pi is mid-task with the coder loaded, and I start a cuDF job that needs about
70 GiB.

**What happens.** `spark make-room 70G` lists what would have to unload, with sizes, and
unloads only what I confirm. pi's next request is refused with a reason, never quietly
swapped.

**What I see.** The list, and headroom on the menu bar.

**How to override.** If I decline, nothing unloads. The brake remains the backstop.

*Designed 2026-10-07, for Phase 2a:* `spark make-room 70G` lists everything it could unload, the
always-loaded models included, largest first, and unloads what I confirm; `spark make-room --all`
unloads everything after one confirmation, my clean slate for testing. Nothing it unloads cuts off
a request in flight: it waits for pi's current request to finish first. pi's next request then
waits up to its key's wait, 30 s from the Mac or 10 minutes as `agent`, and if there's still no
room it gets a refusal in the client that says why. The headroom shows in `spark status` until the
menu bar arrives in 2b.

*Revised 2026-10-07, after the design's council:* `spark make-room 70G` frees enough that 70 GiB
are available beyond the reserve and the growth the loaded models are still owed, so the job can
take all of it without reaching the brake. It lists pinned models and those an agent's session
holds too, marked, with each one's requests in flight and how long they've run. Then it **holds
that room for me** until `spark make-room --done`, a duration I give, or the next boot: no reload,
no waiting request (`agent`'s included), no boot preload and no brake release takes it, so pi's
next request waits and is then refused with a reason that names the hold, never quietly loaded into
my job's memory. When the hold ends, the always-loaded models reload one at a time, if they fit,
and the coder waits for a request. `spark status` shows the hold, its size and when it ends.
*(Added after the re-review, the same day:* asked for more than unloading everything could free,
make-room says so, shows the most it can free, and unloads nothing unless I confirm that. An
always-loaded model that doesn't fit when the hold ends waits, loads once it fits, and shows as
waiting in `spark status`.)

*What I see, worded at the design's UX pass (2026-10-07):* with the always-loaded models and the
coder loaded and nothing else running, 9 GiB is free for a load, so `spark make-room 70G` lists
*the coder 41 GiB, loads when asked* and *Gemma 32 GiB, always loaded*, first, says that unloading
both frees 82 GiB, and asks once. Then: *Unloaded the coder and Gemma. 82 GiB free; 70 GiB held for
you until `spark make-room --done` or a reboot.* pi's next request waits 30 s and then reads: *The
coder didn't load: it needs 41 GiB, and 12 GiB is free after the 24 GiB reserve and the 70 GiB held
for you by make-room. On the Spark, `spark make-room --done` ends the hold; or try again later.*
When I end the hold, a default notification says *make-room's 70 GiB hold ended*, and Gemma
reloads. The plan's *What you see in Phase 2a* has every message.

*Corrected after the design's final re-review, the same day (the session's rulings):*

- **"Available" and "free for a load" are two numbers.** *Available* is the box's free memory;
  *free for a load* is what's left after the reserve, the growth the loaded models are still owed
  and any hold. So `spark make-room 70G` frees until 70 GiB is free for a load (above it said
  "available"), and its message reads *Unloaded the coder and Gemma. 82 GiB is free for a load, and
  70 GiB of it is held for you until `spark make-room --done` or a reboot.*
- **The hold is mine.** My own requests, from pi on the Mac or the web UI, may load into it, and
  it shrinks by what they take from it; only `agent`'s requests and the automatic reloads are kept
  out. So the request that gets refused here is the agent's: it waits its 10 minutes and then
  reads *The coder didn't load: it needs 41 GiB, and 12 GiB is free for a load, after the 24 GiB
  reserve and the 70 GiB make-room holds for Dan. On the Spark, `spark make-room --done` ends the
  hold.* A request of mine would load the coder into the hold, shrinking it, which is my call.

### S03 · Doesn't fit (interactive), as it read on 2026-10-07

**Situation.** I ask for a model that doesn't fit in free memory.

**What happens.** Nothing is evicted or substituted. The request is refused, with the memory
needed against what's available, the top memory holders, and my options.

**What I see.** An error in the client. From Phase 3 the explanation is inline; before that
it's on the menu bar and through ntfy. *(Corrected 2026-10-07, from Phase 2a's design: the
explanation is inline from Phase 2a, not Phase 3.)*

**How to override.** Free memory (`spark make-room`, stop a job), pick a model that fits, or
retry.

*Until Phase 2 (added 2026-09-28, during Phase 1):* Phase 1's launch check refuses the same way:
nothing is evicted or substituted. The client gets a plain error, and the explanation, the memory
needed against what's available, is the `refused` line of `make status`, on the Spark. There is no
menu bar or ntfy yet. Task 16's drill saw it: the coder's request got a `500`, and `make status`
said it needed 29.0 GiB, with 45.5 GiB available and the 24 GiB reserve kept, 7.5 GiB short.
*(Added 2026-09-28, from Phase 1's council: `make status` shows only the last refusal, and the next
load that starts clears it. Earlier ones stay in llama-swap's in-memory buffer until it restarts;
[Deploy the stack](../how-to/deploy.md) shows how to read them.)*

*Designed 2026-10-07, for Phase 2a:* the request reaches the front, which asks the gate. If the
model doesn't fit, the front holds the request for my key's wait, 30 s (`agent`'s is 10 minutes),
rechecking as memory changes, and the model loads if room appears in time. If not, the client gets
a refusal that reads like a normal API error: the memory needed against what's free after the
reserve, the top memory holders, my options (`spark make-room <size>`, or retry), a code such as
`no_fit` or `held_by_brake`, and a retry-after. `spark status` keeps a history of recent refusals,
not only the last, and ntfy sends a default-priority "refused".

*Revised 2026-10-07, after the design's council:* the gate, not the front, keeps the request
waiting, and my keys go ahead of `agent`'s. The 30 s cover waiting for memory and for the
one-at-a-time load slot; a load that has started is always waited for. Free memory also holds back
the growth the loaded models are still owed and any room make-room holds for me, and the refusal
names both. It is a `503` with `Retry-After` and `x-should-retry: false`, so the client doesn't
retry it by itself; how pi and Open WebUI show it is checked before 2a's plan is written. A start
that fails is refused with `load_failed`, and its reason. The "refused" notification reaches my
phone.

*What I see, worded at the design's UX pass (2026-10-07):* with a 32 GiB python job of mine
running beside the always-loaded models, pi shows only its usual "thinking" for 30 s, with nothing
added to the answer, and then: *The coder didn't load: it needs 41 GiB, and 18 GiB is free after
the 24 GiB reserve and the 6 GiB the loaded models may still grow into. Using memory now: python3
(chendaniely) 32 GiB, Gemma 27 GiB. On the Spark, `spark make-room 41G` frees room; or try again
later.* The code, `no_fit`, is in the error's `code` field, not the sentence. My phone gets a
default-priority *Refused the coder for pi on the Mac: needs 41 GiB, 18 free*, and a burst of the
same refusal collapses into one more, with a count. The plan's *What you see in Phase 2a* has the
message for every code.

*Corrected after the design's final re-review, the same day (the session's rulings):* the message
now keeps two numbers apart, *available* (the box's free memory) and *free for a load* (after the
reserve, the growth owed and any hold): *The coder didn't load: it needs 41 GiB, and 18 GiB is free
for a load (48 GiB available, less the 24 GiB reserve and the 6 GiB the loaded models may still
grow into). Using memory now: python3 (chendaniely) 32 GiB, Gemma 27 GiB. Free space with
`spark make-room 41G` on the Spark, then try again.* And that next step works: make-room's hold is
mine, so my own requests may load into it. `spark make-room 41G` unloads Gemma, leaving 50 GiB
free for a load, 41 of it held for me; my retry loads the coder into it, and the hold shrinks to
the 9 GiB left. `spark make-room --done` ends that, and Gemma comes back once there's room for it,
after my python job. The phone's *refused* reads *needs 41 GiB, 18 free for a load*.

*Corrected 2026-10-07, with 2a's implementation plan (the session's ruling; I had left the UX to
it):* the refusal is a `409`, not a `503`. pi retries any error whose text holds "503" by itself,
up to three times, so my 30 s refusal would have reached me after about 2¼ minutes; a `409` it
shows at once and doesn't retry, as `409: {"message":"The coder didn't load: …","code":"no_fit"}`,
and the web UI shows the sentence alone. A refusal for an outage, `gate_down` say, stays a `503`,
which pi does retry. A load that fails, or a model not yet downloaded, is a `409` too, since a
retry would change neither.

### S04 · Doesn't fit (unattended), as it read on 2026-10-07

**Situation.** An app or agent asks at 2 am for a model that doesn't fit.

**What happens.** It waits up to its key's `wait_for_fit_s`, and nothing is ever evicted to
make room. If it still doesn't fit, it gets a refusal with a reason and a `retry_after_s`.

**What I see.** A notification if it was refused — quiet hours are respected.

**How to override.** The key's wait setting.

*Added 2026-10-07, from Phase 2a's design:* an agent's request gets this from Phase 2a, through the
front: `agent`'s key waits up to 10 minutes, then gets a refusal with its reason and a retry-after,
and ntfy sends a default-priority "refused", which makes no sound in quiet hours (00:00–05:00).
Apps get it with Phase 3.

*Revised 2026-10-07, after the design's council:* `agent`'s pi gives up on a silent request after
5 minutes by default, half that wait, so `spark clients` sets it to about 15 minutes, and the
refusal reaches it. A refusal is a `503` that the client doesn't retry by itself. *(Corrected
2026-10-07, with 2a's implementation plan: a refusal after the wait is a `409`, which `agent`'s pi
doesn't retry; it would have retried a `503` up to three times, each with a 10-minute wait of its
own.)* Quiet hours are set on my phone, where only high-priority alerts get through Do Not
Disturb.

### S05 · Memory critically low, as it read on 2026-10-07

**Situation.** A job keeps growing, and free memory falls toward the band where this box has
been reported to freeze.

**What happens.** A warning fires at 28 GiB available. At 20 GiB the brake unloads a loading
engine first, then idle models of any class, then the least recently used, and holds them
there. earlyoom is the last resort. Phase 1 ships a minimal brake that unloads the on-demand
coder first, then the always-loaded models.

**What I see.** A high-priority "brake" notification, and a hold shown in `spark status`.
*(Added 2026-09-28: that line of `make status` also says whether llama-swap took the brake's own
key when the brake started, and `make doctor`'s `stack units` line fails if it didn't. A brake
whose key llama-swap refuses can unload nothing.)*

*Until Phase 2 (added 2026-09-28, from Phase 1's council):* nothing notifies me. The warning at
28 GiB is a line in the brake's journal (`make logs s=brake`), and the hold shows on `make status`'s
`brake` line, on the Spark. earlyoom, the last resort, chooses among the engines by their RSS,
which leaves out the models' GPU memory, and its dry run in Task 13, with every engine at the same
`oom_score_adj`, picked Gemma, a resident, before the on-demand coder. *(Corrected 2026-09-28, from
Phase 1's council: `spark launch` now gives a resident engine `oom_score_adj` 900 and an on-demand
one 1000, so earlyoom picks the on-demand coder first, as the brake does. Checked after that day's
deploy: earlyoom's dry run picked the coder's engine.)*

**How to override.** `spark brake --release` once memory is back. *(Corrected 2026-09-26:
`make brake-release`, on the Spark, in its clone, from an account in `spark-admin` — `spark` isn't
on my PATH, and the target runs it through uv.)*
Thresholds live in `stack/models.yaml`.

*Status: built, 2026-09-28 (Phase 1). Phase 1's minimal brake unloads the on-demand models first:
Task 16's drill, at raised thresholds, held new loads and unloaded the coder while the residents
stayed. The idle-first order and the notifications arrive in Phase 2.*

*Designed 2026-10-07, for Phase 2a:* the brake moves into the gate, with the same thresholds and
order, and a high-priority notification when it fires. It releases by itself once memory has
stayed above 28 GiB for 5 minutes, and a default-priority notification says when it fired and when
it released. The gate then reloads the always-loaded models one at a time, as at boot, and names
them; on-demand models wait for a request. While it holds, a request waits for its key's wait and
is then refused with `held_by_brake`. The brake is the only thing that may cut off a request in
flight. `make brake-release` stays.

*Revised 2026-10-07, after the design's council:* the brake stays a unit of its own, as today, so
it keeps running while the gate is down. "The same order" above means the plan's: a loading engine,
then idle models of any class, then the least recently used, with Phase 1's order, on-demand first,
as its fallback when it can't read which models are idle. It also acts early when memory falls
fast. The gate lifts the hold, with limits:

- only once memory has stayed above 28 GiB for 5 minutes, and only if what it would reload fits;
- at most once an hour: a brake within the hour after an automatic release holds until I release
  it, and its alert says so;
- a hold found after a reboot, likely left by a freeze, waits for me, with a high-priority alert;
- the model that was loading when the brake fired doesn't reload by itself: requests for it are
  refused with `held_by_brake` until I load it again.

Admission keeps 24 GiB free, not 22: the reserve went back to 24 the same day.

*Corrected after the design's re-review, the same day:*

- **The model that was loading** still doesn't reload by itself, but my own request, from pi on the
  Mac or the web UI, loads it as normal if it fits (my choice), as does `spark load`. `agent`'s
  requests for it wait, then get `footprint_suspect`, naming `spark load <model>`, until I have
  loaded it once; no `held_by_brake` without a hold.
- **While the gate is down,** the brake sends its own high-priority "brake fired" to ntfy, by the
  failure notifier's independent path, so I hear about the brake, not only that the gate is down.

*What I see, worded at the design's UX pass (2026-10-07):* a high-priority *Brake on brightroar at
03:12: 19.6 GiB free, under the 20 GiB line. Unloaded the coder, which was loading; new loads are
paused. They resume by themselves after 5 min above 28 GiB free.* Then, at default priority,
*Brake released at 03:40, 64 GiB free. Reloaded Gemma and the embeddings. The coder was loading
when it fired, so it loads again only when you ask.* A second brake within the hour, or a hold
found after a reboot, sends a high-priority *… new loads are still paused. On the Spark,
`make brake-release` resumes them.* While loads are paused, a request is refused with *Not loading
the coder now: memory ran low at 03:12 …*, and `spark status` shows *paused*. `agent`'s requests
for the coder afterwards get `footprint_suspect` and a notification naming `spark load coder`
(my decision the same day). The plan's *What you see in Phase 2a* has every message.

*Corrected after the design's final re-review, the same day:* the brake's numbers are *available*
memory, the box's free memory, never "free", which the messages keep for *free for a load*: *19.6
GiB available, under the 20 GiB line*, *after 5 min above 28 GiB available*, and *Brake released
at 03:40, 64 GiB available*. `brake_fired` names every model it unloads: a later unload in the same
episode sends a short follow-up (*also unloaded Gemma and the embeddings, both idle*), which is
how Gemma and the embeddings came to be reloaded. If the brake sent its own alert while the gate
was down, the gate doesn't send it again once it's back. A silent `memory_warning` marks the fall
past 28 GiB available, before the brake.

### S14 · Gate down, as it read on 2026-10-07

**Situation.** `spark-gate` crashes.

**What happens.** Models already loaded keep serving. Only new loads are refused. A failure
notifier alerts without needing the gate itself.

**What I see.** A high-priority notification.

**How to override.** None needed.

*Designed 2026-10-07, for Phase 2a:* the front, which takes requests on 127.0.0.1:9100, keeps
forwarding to the models llama-swap reports loaded while the gate is down, and refuses only new
loads, with `gate_down`. The failure notifier, `OnFailure=` on the front, the gate and llama-swap,
sends the high-priority ntfy alert itself, without the gate, so a failed front or llama-swap alerts
me too. ntfy reaches me over the tailnet or the home LAN only, so out of reach means no alert, for
now.

*Revised 2026-10-07, after the design's council:*

- **The brake keeps running.** It stays a unit of its own, so a gate that is down never means no
  brake; only its automatic release waits for the gate. *(Corrected after the re-review, the same
  day:* if the brake fires while the gate is down, it sends its own "brake fired" to ntfy, by the
  same independent path as the failure notifier, so I hear about the brake too, not only "gate
  down".)
- **At boot it's worse.** "Models already loaded keep serving" holds only for what is loaded. At
  boot, or after a restart that stopped every engine, a gate that is down means nothing can load,
  so the API is down in effect until the gate starts.
- **llama-swap down is its own case.** Requests wait for their key's wait, then are refused with
  `llama_swap_down`, and the brake can hold new loads but not unload anything.
- **What sends the alert.** The notifier also covers the brake, runs outside the app's
  environment, and sends at most one alert per unit in a while. It fires on a crash; a hang of the
  front or the gate trips their watchdogs and becomes a crash; a clean exit fires nothing, since
  the unit restarts by itself.
- **The drill**, in 2a: kill each of the four services, freeze the front with SIGSTOP to trip its
  watchdog, and send llama-swap a clean SIGTERM, checking each alert, or its absence, on my phone.
  *(Added after the re-review:)* a crash-loop check as `agent`, killing the front again and again,
  shows that 9100 stays held and never answers as anyone else.

*What I see, worded at the design's UX pass (2026-10-07):* a high-priority *The gate on brightroar
stopped at 09:14 (it crashed; it is restarting). Loaded models still answer; new loads are refused
until it's back*, and, once it is, a default *The gate on brightroar is running again, after 12 s
down.* A request that needs a load meanwhile reads: *No new model can load: the gate on the Spark
isn't running. Models already loaded still answer. Your phone has the alert; on the Spark,
`make doctor` shows what's wrong.* The front, llama-swap and the brake each have an alert of their
own, worded the same way, and a crash loop sends at most one per unit in a while. The plan's *What
you see in Phase 2a* has every message. *(Corrected after the final re-review, the same day:*
each of the four alerts ends with where to act, *On the Spark, `make doctor` shows what's wrong*;
and *running again* waits until the unit has stayed up for a minute, the brake's included, so a
crash loop doesn't alternate it with the alerts.)

### S17 · Changing models mid-task, as it read on 2026-10-07

**Situation.** I edit `stack/models.yaml` while pi is mid-task.

**What happens.** `spark apply` shows the diff and waits until models are idle, because a
llama-swap reload stops every engine. It only stops everything sooner if I confirm that.

**What I see.** The diff, and the wait.

**How to override.** Confirm to apply now.

*Until Phase 2 (added 2026-09-26, during Phase 1):* Phase 1's `make apply`, on the Spark, neither
shows the diff nor waits. It lists the files that change, and while models are loaded, or it can't
tell, it refuses: it names the loaded models, changes nothing and exits 1. I run it again once
they're idle, or run `make apply-now` to restart llama-swap anyway. A model that starts loading
after apply's first look puts off only llama-swap's restart: the files are deployed, apply says so
and exits 1, and the next `make apply` makes the restart once the models are idle.

*Designed 2026-10-07, for Phase 2a, correcting the wait above:* `make apply` shows the diff of what
it would change. When the change needs llama-swap restarted, it waits until no request has been in
flight for about 60 s on any engine, not until the models are idle: requests keep being served, and
it shows what it's waiting on. Then it restarts llama-swap; the gate reloads the always-loaded
models one at a time, and on-demand ones reload on their next request. Ctrl-C leaves nothing
changed, and `make apply-now` restarts at once, after asking me to confirm. A change that needs no
llama-swap restart applies at once.

*Revised 2026-10-07, after the design's council:*

- **The front waits too.** A change to the front's own code restarts it only through the same
  quiet moment, since its restart would cut off every request in flight; a change elsewhere in the
  app doesn't restart it at all. The gate and the brake restart at once, which cuts nothing off.
- **The wait has a deadline.** After 15 minutes without a quiet minute, apply offers "drain now",
  which holds new requests and lets those in flight finish, and then `make apply-now`.
- **Nothing is written until then,** so Ctrl-C leaves nothing changed.
- **During the restart,** a request waits for its key's wait, and is refused with `restarting` if
  llama-swap isn't back by then.
- **The coder swap** runs apply, whose restart stops every engine, the old coder included; then the
  pull of the new coder, during which a request for it is refused with `not_downloaded`; then
  `make clients`.

*What I see, worded at the design's UX pass (2026-10-07):* the diff, then *Waiting for a quiet
moment: the coder answered 20 s ago, and it needs 60 s with nothing in flight. Ctrl-C leaves
everything as it was; `make apply-now` restarts now.* After 15 minutes: *No quiet minute in 15
minutes. Drain now, holding new requests while the 2 in flight finish? [y/N]* `make apply-now`
asks first, naming the requests it would cut off. After the restart my phone gets a default *make
apply restarted the model service at 14:02. Gemma, the embeddings and whisper reloaded; the coder
loads on its next request.* During the swap, pi's request for the new coder reads *The coder isn't
downloaded yet. On the Spark, `make pull` fetches it (16 GiB)*, and one for the old name reads
*There's no model called qwen3.6-35b-a3b here …*, listing the models and saying `make clients`
updates pi's list. The plan's *What you see in Phase 2a* has every message.
