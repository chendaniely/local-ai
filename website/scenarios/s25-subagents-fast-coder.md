---
title: "S25 · Sub-agents on the fast coder"
scenario-id: S25
phase: 4
status: planned
---

**Situation.** A task in opencode fans out into four sub-agents on `qwen3.8-27b`, the fast coder,
and meanwhile I ask Gemma something in Open WebUI.

**What happens.** The fast coder runs one request at a time, so the sub-agents queue, each at full
single-stream speed. Gemma answers alongside them: the fast coder fits beside the always-loaded
models.

**What I see.** The sub-agents finishing one after another, and Gemma answering as usual.

**How to override.** Nothing to override yet. One at a time stays while sglang #36548 (the
drafter's state corrupted by concurrent requests) is open, or until a concurrency check passes on
this box; then the registry raises the limit.

*Designed 2026-10-13 and moved to Phase 4a on 2026-10-14 (the plan's Phases). A sub-agent queued
behind a long request could pass opencode's 300 s wait for a reply and be sent again; a drill
checks it, and the timeout in opencode's config goes up if so. Not built yet.*
