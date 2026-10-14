---
title: "S24 · The fast coder won't start"
scenario-id: S24
phase: 4
status: planned
---

**Situation.** One morning the fast coder, `qwen3.8-27b` on SGLang, won't start (a bad image
update, or one of its open upstream bugs) while I'm mid-task in opencode.

**What happens.** The load fails, and nothing is substituted: opencode gets the refusal that says
the start failed, not a smaller model answering in its place. `spark status` shows the failed
start.

**What I see.** The refusal in opencode.

**How to override.** Pick `qwen3.8-27b-gguf`, the llama.cpp coder kept as the fallback, in opencode
or pi. The two don't fit together beside the always-loaded models, so while the fast coder still
holds memory the fallback is refused with the reason, and `spark unload qwen3.8-27b` frees it.

*Designed 2026-10-14, Phase 4a (the plan's Phases), replacing the design of 2026-10-13 for `main`.
Until then the fast coder runs by hand ([The fast coder, by hand](../how-to/qwen3.8-27b-sglang.md)),
and `qwen3.8-27b` is the deployed llama.cpp coder. Not built yet.*
