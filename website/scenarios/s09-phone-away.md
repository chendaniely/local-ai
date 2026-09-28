---
title: "S09 · Phone away from home"
scenario-id: S09
phase: 1
status: planned
---

**Situation.** I'm away from home, Tailscale is on, and I open Open WebUI on my Android
phone.

**What happens.** It's served over HTTPS through `tailscale serve` and installs as an app.
Image questions go to the always-loaded vision model; voice input goes to speech-to-text.

**What I see.** The real model name on every reply.

**How to override.** None needed. A chat can run to the model's full context, 262,144 tokens on
Gemma, and a photo costs up to about 1,120 of them. The phone can't change the context: Open
WebUI's `num_ctx` parameter is for Ollama, and v0.11.4 never sends it to llama-server. What it can
change, per chat under *Controls → Advanced Params* or as a model's default, is `max_tokens` and
*Reasoning Effort*. `max_tokens` counts the thinking too, so set too low it leaves the answer
empty. Open WebUI can also compact a long chat itself, summarizing its older messages once it
passes a token threshold, 80,000 by default. That is off unless the admin turns it on, under
*Admin Panel → Settings → Interface → Context Compaction*, and then a chat's
*Context Compaction Threshold* can lower the threshold. (Added 2026-09-28, Dan's decision: every
model at its full context. The labels are read from Open WebUI v0.11.4's source, not yet checked
on the phone.)
