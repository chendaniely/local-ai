---
title: "S11 · Trying a new model"
scenario-id: S11
phase: 2
status: planned
---

**Situation.** A promising open model is released.

**What happens.** `spark try <hf-repo>` serves it from a separate lab instance through an
uncommitted overlay. Its footprint is estimated, then measured, and a trial note opens in my
vault. `spark promote` adopts it after the bake-off; `spark forget` removes it.

**What I see.** The trial in `spark status`.

**How to override.** None needed.

*Noted 2026-10-07, from Phase 2a's design council:* the vault isn't reachable from the Spark, so
the trial note is written on the Spark and shown in `spark status`, and I file it in the vault.
Phase 2c also measures Qwen3.8-27B's route B here, and whether it becomes the coder is 2c's call,
with its measurements.

*Phase 2c, the lab (Phase 2's carving into 2a, 2b and 2c, 2026-10-07).* Before route B becomes the
coder, 2c checks it with two keys, Dan's and `agent`'s, and times its first token at long context
against the Mac pi's 300 s (noted after Phase 2a's implementation plan's forward-and-back
council).
