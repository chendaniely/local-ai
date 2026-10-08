---
title: "S14 · Gate down"
scenario-id: S14
phase: 2
status: planned
---

**Situation.** `spark-gate` crashes.

**What happens.** Models already loaded keep serving. Only new loads are refused. A failure
notifier alerts without needing the gate itself.

**What I see.** A high-priority notification.

**How to override.** None needed.

**Phase 2a, as designed (2026-10-07): what happens and what I see.** The front, which takes
requests on 127.0.0.1:9100, keeps forwarding to the models llama-swap reports loaded while the gate
is down, and refuses only new loads, with `gate_down`, a `503`, which pi retries a few times while
the gate restarts. That holds only for what is loaded: at boot, or after a restart that stopped
every engine, a gate that is down means nothing can load, so the API is down in effect until it
starts. The brake, a unit of its own, keeps running, and if it fires meanwhile it sends its own
alert.

The failure notifier, on the front, the gate, the brake and llama-swap, sends a high-priority alert
itself, from outside the app's environment, at most one per unit in 5 minutes, so a crash loop
doesn't flood my phone. It fires on a crash; a hang of the front or the gate trips their watchdogs
and becomes a crash; a clean exit fires nothing, since the unit restarts by itself. llama-swap down
is its own case: requests wait for their key's wait, then are refused with `llama_swap_down`, and
the brake can hold new loads but not unload anything; once llama-swap is back, the gate reloads the
always-loaded models by themselves, one at a time, as it does for one that earlyoom kills (my
decision, 2026-10-07). ntfy reaches me over the tailnet or the home LAN only, so out of reach means
no alert, for now.

In the plan's words: a high-priority *The gate on brightroar stopped at 09:14 (it crashed; it is
restarting). Loaded models still answer; new loads are refused until it's back. On the Spark,
`make doctor` shows what's wrong.*; and once it has been back for a minute, at default priority,
*The gate on brightroar has been running again for a minute, after 12 s down. New loads work
again.* A request that needs a load meanwhile reads *No new model can load: the gate on the Spark
isn't running. Models already loaded still answer. Your phone has the alert; on the Spark, `make
doctor` shows what's wrong.* The front, llama-swap and the brake each have an alert of their own,
worded the same way. The plan's [*What you see in Phase
2a*](../design/plan.md#what-you-see-in-phase-2a) has every message.

The drill, in 2a: each of the four services killed in turn; the front frozen with SIGSTOP to trip
its watchdog; llama-swap sent a clean SIGTERM; the gate made to fail at start, since a gate merely
stopped is started again at once by its sockets; and, as `agent`, a crash-loop check that shows
9100 stays held and never answers as anyone else.

*Rewritten 2026-10-07 as one current account, after the implementation plan's forward-and-back
council. The notes this page gathered while the design moved, and the questions and answers behind
them, are in [Phase 2a — questions and answers](../design/phase-2a-qa.md#how-the-scenario-pages-read-before-the-rewrite).*
