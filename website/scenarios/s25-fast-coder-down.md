---
title: "S25 · The fast coder won't start"
scenario-id: S25
phase: 5
status: planned
---

**Situation.** One morning the fast coder, `qwen3.8-27b` on SGLang, won't start — a bad image
update, or one of its open upstream bugs — while I'm mid-task in opencode.

**What happens.** The load fails, and nothing is substituted: opencode gets an error, not a smaller
model answering in its place. `make status` shows the failure, and the engine unit's log has
SGLang's own reason.

**What I see.** An error in opencode.

**How to override.** Pick `qwen3.8-27b-gguf`, the llama.cpp coder kept as the fallback, in opencode
or pi. The two share llama-swap's coder group, so only one is ever loaded.

*Designed 2026-10-13, Phase 5a (the plan's Phases). Not built yet.*
