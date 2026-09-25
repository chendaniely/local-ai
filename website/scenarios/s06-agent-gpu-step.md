---
title: "S06 · An agent's long GPU step"
scenario-id: S06
phase: 2
status: planned
---

**Situation.** pi's test step runs a 40-minute GPU job that makes no model calls.

**What happens.** The harness hook registered the session, so its model counts as in use and
doesn't idle-unload.

**What I see.** The session on the menu bar.

**How to override.** End the session, or `spark unpin`.
