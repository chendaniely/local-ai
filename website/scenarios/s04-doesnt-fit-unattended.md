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

**From Phase 2a, as designed (2026-10-07), for `agent`.** An agent's request gets this through the
front from Phase 2a; apps get it with Phase 3. `agent`'s key waits up to 10 minutes, then gets a
refusal with its reason, a `409`, which `agent`'s pi shows and doesn't retry, and a retry-after
where its code has one. By default `agent`'s pi gives up on a silent request after 5 minutes, half
that wait, so `spark clients` sets it to about 15 minutes, and the refusal reaches it. My phone gets
a default-priority *refused* notification, which makes no sound in quiet hours, 00:00–05:00: they
are set on my phone, where only high-priority alerts get through Do Not Disturb.

*Rewritten 2026-10-07 as one current account, after the implementation plan's forward-and-back
council. The notes this page gathered while the design moved, and the questions and answers behind
them, are in [Phase 2a — questions and answers](../design/phase-2a-qa.md#how-the-scenario-pages-read-before-the-rewrite).*
