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
