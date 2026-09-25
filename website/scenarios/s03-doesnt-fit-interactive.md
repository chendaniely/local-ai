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
