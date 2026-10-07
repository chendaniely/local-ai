---
title: "S04 · Doesn't fit (unattended)"
scenario-id: S04
phase: 3
status: planned
---

**Situation.** An app or agent asks at 2 am for a model that doesn't fit.

**What happens.** It waits up to its key's `wait_for_fit_s`, and nothing is ever evicted to
make room. If it still doesn't fit, it gets a refusal with a reason and a `retry_after_s`.

**What I see.** A notification if it was refused — quiet hours are respected.

**How to override.** The key's wait setting.

*Added 2026-10-07, from Phase 2a's design:* an agent's request gets this from Phase 2a, through the
front: `agent`'s key waits up to 10 minutes, then gets a refusal with its reason and a retry-after,
and ntfy sends a default-priority "refused", which makes no sound in quiet hours (00:00–05:00).
Apps get it with Phase 3.
