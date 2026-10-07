---
title: "S03 · Doesn't fit (interactive)"
scenario-id: S03
phase: 2
status: planned
---

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

*Why it works this way: the questions and Dan's answers are in [Phase 2a — questions and answers](../design/phase-2a-qa.md).*
