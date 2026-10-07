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

*Revised 2026-10-07, after the design's council:*

- **The brake keeps running.** It stays a unit of its own, so a gate that is down never means no
  brake; only its automatic release waits for the gate.
- **At boot it's worse.** "Models already loaded keep serving" holds only for what is loaded. At
  boot, or after a restart that stopped every engine, a gate that is down means nothing can load,
  so the API is down in effect until the gate starts.
- **llama-swap down is its own case.** Requests wait for their key's wait, then are refused with
  `llama_swap_down`, and the brake can hold new loads but not unload anything.
- **What sends the alert.** The notifier also covers the brake, runs outside the app's
  environment, and sends at most one alert per unit in a while. It fires on a crash; a hang of the
  front or the gate trips their watchdogs and becomes a crash; a clean exit fires nothing, since
  the unit restarts by itself.
- **The drill**, in 2a: kill each of the four services, freeze the front with SIGSTOP to trip its
  watchdog, and send llama-swap a clean SIGTERM, checking each alert, or its absence, on my phone.
