---
title: "S24 · A model that takes the box"
scenario-id: S24
phase: 5
status: planned
---

**Situation.** Gemma is loaded but idle, and I pick Qwen3.8-Flash-Next in opencode. It is bigger
than the room beside the always-loaded models, so the registry marks it as taking the box.

**What happens.** llama-swap unloads every other model first — Gemma, the embedding model, whisper
and any coder — and then starts Flash-Next through the fit check, which it passes on an empty box.
While it is loaded, a request for any other model is refused with the reason, naming Flash-Next:
from Open WebUI, from pi, or one of Open WebUI's background calls, so nothing evicts it mid-task.
It unloads after 60 minutes idle, and the others load again on their next request.

**What I see.** opencode waits while Flash-Next loads, then answers. Open WebUI shows an error
while Flash-Next holds the box, and `make status` says why.

**How to override.** `make unload m=qwen3.8-flash-next` frees the box now.

*Designed 2026-10-13, Phase 5a (the plan's Phases): this is rule 2's one exception, chosen for this
model in advance. Not built yet.*
