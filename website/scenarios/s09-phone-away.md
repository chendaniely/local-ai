---
title: "S09 · Phone away from home"
scenario-id: S09
phase: 1
status: verified
verified: 2026-09-28
---

**Situation.** I'm away from home, Tailscale is on, and I open Open WebUI on my Android
phone.

**What happens.** It's served over HTTPS through `tailscale serve` and installs as an app.
Image questions go to the always-loaded vision model; voice input goes to speech-to-text.
*(Corrected 2026-09-28, from Phase 1's council: in Phase 1 the vision model is resident, not
preloaded. After a restart it loads on its first request, then stays loaded; Phase 2 preloads it.
That it installs as an app is not yet recorded as checked.)*

**What I see.** The real model name on every reply. *(Not yet recorded as checked on the phone,
2026-09-28.)*

**How to override.** None needed. A chat can run to the model's full context, 262,144 tokens on
Gemma, and a photo costs up to about 1,120 of them. *(2026-10-08: the registry's coder, now
Qwen3.8-27B, is held at 163,840 of its 262,144 tokens until Phase 2a's Task 42, Dan's decision.)*
The phone can't change the context: Open WebUI's `num_ctx` parameter is for Ollama, and v0.11.4
never sends it to llama-server. What it can change, per chat under *Controls → Advanced Params* or
as a model's default, includes `max_tokens` and *Reasoning Effort*. `max_tokens` counts the
thinking too, so set too low it leaves the answer empty. llama-server b11146 hands *Reasoning
Effort* to the model's chat template, and `none` turns thinking off (its source). In Task 13's one
probe each, the coder (then Qwen3.6-35B-A3B; Qwen3.8-27B since 2026-10-08, not yet probed)
followed low and high, and Gemma barely did. Open WebUI can also compact a long chat itself,
summarizing its older messages once it passes a token threshold, 80,000 by default. That is off
unless the admin turns it on, under *Admin Panel → Settings → Interface → Context Compaction*, and
then a chat's
*Context Compaction Threshold* can lower the threshold. Dan leaves it off (2026-09-28): at
262,144 tokens a chat rarely needs it, and a chat that ever outgrows the context is refused with
an error, so a new chat is the way on. (Added 2026-09-28, Dan's decision: every model at its full
context. The labels are read from Open WebUI v0.11.4's source, and Dan found each of them in the
web UI in the Mac's browser the same day; not yet checked on the phone.)

*Status: verified, 2026-09-28 (Phase 1, Task 14): on the phone, at home and on mobile data, the
web UI answered chats, a photo question, dictation and voice mode. The settings under *How to
override* were checked in the web UI in the Mac's browser the same day (Dan's decision); the
claims marked above as not yet checked stay marked.*
