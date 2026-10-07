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
