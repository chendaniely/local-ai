---
title: "Phase 2a — questions and answers"
description: "How Phase 2a's design was decided: the scenario questions Dan answered, the architecture question, the coder and the budget, what the council raised, and how Dan wants the gate to read to him. Each answer with its reason."
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
answer says so.

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

**Dan: A.** *(The council added a 15-minute deadline before it offers to drain.)*

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

## The numbers Dan set

- **Quiet hours:** 00:00–05:00 — only high-priority alerts sound. *(They're set on the phone: ntfy has
  no server-side quiet hours.)*
- **ntfy's reach:** the tailnet or the home Wi-Fi only; out of reach means no alerts, for now.
- **The idle unload:** 60 minutes (the plan's default was 30), for models that load when asked;
  always-loaded models never unload for being idle.
- **make-room:** may unload everything, the always-loaded models included — "a great way to clean
  things up when I need to test something" — and `--all` clears the box after one confirmation.
- **Notification levels:** the plan's three, as designed.

## The coder

**Q: Dan wants Qwen3.8-27B working this round, as the default coder.** Route A (llama.cpp b11146 as
deployed, Unsloth's GGUF, MTP) needs nothing new, so it joined 2a.

- **Replace, or keep both coders?** **Dan: replace.** Qwen3.6-35B-A3B leaves the registry; its
  files stay on disk, to come back as a lab trial or a fallback.
- **Which file?** **Dan: UD-Q4_K_XL** (17.6 GB) — speed first; a dense model is bandwidth-bound here.
- **How does it fit at its full 262K context?** The recommendation was an 8-bit KV cache. **Dan asked
  why everything can't run at its maximum.** The answer: the budget check counted the 24 GiB reserve
  twice. **Dan: fix the check**, and at first also lower the reserve a little, to 22 GiB — which the
  council then showed wasn't needed (below). Every model runs at full context with an f16 KV cache.

## After the council

Four reviewers read the design; Dan took every recommendation (2026-10-07):

| Question | Answer, and why |
|---|---|
| The reserve | **Back to 24 GiB.** The corrected check is what makes the coder fit; 22 only halved the margin above the brake while every footprint is an estimate. |
| Where the brake runs | **Its own unit, as today.** Freeze protection doesn't share a process with a socket `agent` can reach, or with ntfy calls that might hang. |
| Limits on the automatic release | **One per hour, then it waits for Dan; a pause found after a reboot waits for Dan; it releases only if the reloads fit.** Otherwise a job hovering near the line fires and releases endlessly, loudly, at night. |
| The model that was loading when the brake fired | **It doesn't come back by itself.** Dan's own request loads it if it fits; `agent`'s requests wait, then get a refusal naming `spark load <model>`, so an unattended run can't fire the brake again. |
| make-room's space | **It's held for Dan** until `spark make-room --done`, a time he gives, or a reboot — and his own requests may load into it, so "make room, then try again" works. |
| `agent`'s 10-minute wait | **`agent`'s pi waits 15 minutes**, since pi gives up after 5 by default. |
| Port squatting | **systemd holds 9100 and the gate's sockets, even through a crash loop; llama-swap and the engines move below 1024.** No other local user can take a port a key goes to. |
| The front's user | **Its own, `spark-front`**, out of reach of the engines and the pull. |
| Hardening into 2a | **llama-swap's sandbox, root-only secret files, the pull's own user, `cap_drop` for the web services.** The engines' own user waits. |
| `agent`'s own GPU jobs | **An OOM score root sets, in 2a if it holds when tested as `agent`.** |
| The tools | **uvicorn, a plain ASGI front, a Starlette gate and httpx** — two new packages. |
| Alerts on the Mac | **Phone only in 2a; the Mac with 2b's menu bar.** |
| The gate down | **The brake sends its own alert** when the gate is down, so Dan hears "brake fired", not only "gate down". |

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
- **Nothing is written into a model's reply**: a wait shows as a slow reply, explained on the phone
  and in `spark status`.
- **Plain words:** *always loaded*, *loads when asked*, *paused*, models by their role first.

The plan's *What you see in Phase 2a* has every message and notification as written.
