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
  brake; only its automatic release waits for the gate. *(Corrected after the re-review, the same
  day:* if the brake fires while the gate is down, it sends its own "brake fired" to ntfy, by the
  same independent path as the failure notifier, so I hear about the brake too, not only "gate
  down".)
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
  *(Added after the re-review:)* a crash-loop check as `agent`, killing the front again and again,
  shows that 9100 stays held and never answers as anyone else.

*What I see, worded at the design's UX pass (2026-10-07):* a high-priority *The gate on brightroar
stopped at 09:14 (it crashed; it is restarting). Loaded models still answer; new loads are refused
until it's back*, and, once it is, a default *The gate on brightroar is running again, after 12 s
down.* A request that needs a load meanwhile reads: *No new model can load: the gate on the Spark
isn't running. Models already loaded still answer. Your phone has the alert; on the Spark,
`make doctor` shows what's wrong.* The front, llama-swap and the brake each have an alert of their
own, worded the same way, and a crash loop sends at most one per unit in a while. The plan's *What
you see in Phase 2a* has every message.
