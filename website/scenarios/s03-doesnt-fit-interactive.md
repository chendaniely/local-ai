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
it's on the menu bar and through ntfy.

**How to override.** Free memory (`spark make-room`, stop a job), pick a model that fits, or
retry.

*Until Phase 2 (added 2026-09-28, during Phase 1):* Phase 1's launch check refuses the same way:
nothing is evicted or substituted. The client gets a plain error, and the explanation, the memory
needed against what's available, is the `refused` line of `make status`, on the Spark. There is no
menu bar or ntfy yet. Task 16's drill saw it: the coder's request got a `500`, and `make status`
said it needed 29.0 GiB, with 45.5 GiB available and the 24 GiB reserve kept, 7.5 GiB short.
*(Added 2026-09-28, from Phase 1's council: `make status` shows only the last refusal, and the next
load that starts clears it. llama-swap's journal now keeps each one, as `spark launch`'s
`spark: not starting <model>: <reason>` line in `make logs s=llama-swap`, on the Spark.)*
