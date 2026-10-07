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

*Noted 2026-10-07, from Phase 2a's design council:* an on-demand model idle-unloads after 60
minutes from 2a, so a 40-minute step would keep it loaded with or without the hook. Phase 2b's
drill runs a step longer than the idle time, or lowers the idle time for the drill, and sees it fail
once without the hook. A session is `agent`'s own, tied to a live process, and expires unless it
is renewed; pins are mine only, so `spark unpin` is for my own pins.
