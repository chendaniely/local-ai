---
title: "S14 · Gate down"
scenario-id: S14
phase: 2
status: planned
---

**Situation.** `spark-gate` crashes.

**What happens.** Models already loaded keep serving. Only new loads are refused. A failure
notifier alerts without needing the gate itself.

**What I see.** A high-priority notification.

**How to override.** None needed.

*Designed 2026-10-07, for Phase 2a:* the front, which takes requests on 127.0.0.1:9100, keeps
forwarding to the models llama-swap reports loaded while the gate is down, and refuses only new
loads, with `gate_down`. The failure notifier, `OnFailure=` on the front, the gate and llama-swap,
sends the high-priority ntfy alert itself, without the gate, so a failed front or llama-swap alerts
me too. ntfy reaches me over the tailnet or the home LAN only, so out of reach means no alert, for
now.
