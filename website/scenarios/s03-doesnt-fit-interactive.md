---
title: "S03 · Doesn't fit (interactive)"
scenario-id: S03
phase: 2
status: planned
---

**Situation.** I ask for a model that doesn't fit in free memory.

**What happens.** Nothing is evicted or substituted. The request is refused, with the memory
needed against what's available, the top memory holders, and my options.

**What I see.** An error in the client: today a plain one, with the explanation on the Spark; from
Phase 2a the explanation itself, inline in the client, and a notification on my phone.

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

**Phase 2a, as designed (2026-10-07): what happens and what I see.** The request reaches the front,
which asks the gate. The gate keeps it waiting for my key's wait, 30 s (`agent`'s is 10 minutes),
my keys ahead of `agent`'s, rechecking as memory changes: the 30 s cover waiting for memory and for
the one-at-a-time load slot, and a load that has started is always waited for. If room appears in
time, the model loads. If not, the client gets a refusal that reads like a normal API error, a
`409`, which pi shows at once and doesn't retry, with one sentence, the numbers and one next step.
*Free for a load* is the box's available memory less the reserve, the growth the loaded models are
still owed and any room make-room holds that isn't mine.

With a 32 GiB python job of mine running beside the always-loaded models, pi shows only its usual
"thinking" for 30 s, with nothing added to the answer, and then the refusal, as `409:
{"message":"…","code":"no_fit"}`, its message the plan's: *The coder didn't load: it needs 41 GiB,
and 18 GiB is free for a load (48 GiB available, less the 24 GiB reserve and the 6 GiB the loaded
models may still grow into). Using memory now: python3 (chendaniely) 32 GiB, Gemma 27 GiB. Free
space with `spark make-room 41G` on the Spark, then try again.* The web UI shows the sentence alone.
My phone gets a default-priority *Refused the coder for pi on the Mac: needs 41 GiB, 18 free for a
load; python3 (chendaniely) holds 32. Free space with `spark make-room 41G` on the Spark, then try
again.*, and a burst of the same refusal collapses into one more, with a count. `spark status` keeps
a history of recent refusals.

The next step works: make-room's hold is mine, so `spark make-room 41G` unloads Gemma, leaving
50 GiB free for a load, 41 of it held for me; my retry loads the coder into it, and the hold
shrinks to the 9 GiB left. `spark make-room --done` ends that, and Gemma comes back once there's
room for it, after my python job.

Every refusal but one is a `409`, which pi shows at once; a start that fails reads `load_failed`,
with its reason, and a model not yet downloaded `not_downloaded`. Only `gate_down`, which the front
answers at once while the gate itself is down, is a `503`, which pi retries a few times as the gate
restarts. The plan's [*What you see in Phase 2a*](../design/plan.md#what-you-see-in-phase-2a) has
the message for every code.

*Rewritten 2026-10-07 as one current account, after the implementation plan's forward-and-back
council. The notes this page gathered while the design moved, and the questions and answers behind
them, are in [Phase 2a — questions and answers](../design/phase-2a-qa.md#how-the-scenario-pages-read-before-the-rewrite).*
