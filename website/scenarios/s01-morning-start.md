---
title: "S01 · Morning start"
scenario-id: S01
phase: 2
status: planned
---

**Situation.** It's 9 am. The coder model unloaded overnight, and I start pi.

**What happens.** My first request loads the coder if it fits — tens of seconds with
llama.cpp. An optional weekday preload can have it ready before I ask. A "stay loaded while I
work" pin stops it from idle-unloading under me.

**What I see.** "Loading" on the menu bar, a low-priority "loaded" notification, and a slower
first reply.

**How to override.** `spark load <coder>`, the menu bar's Load, or `spark pin <coder>`.

*Designed 2026-10-07, for Phase 2:* Phase 2a builds the gate's part. The coder idle-unloads after
60 minutes with no request and no active session (this was 30), and the always-loaded models never
idle-unload. `spark pin <coder> [duration]` keeps it loaded, for as long as I say. The weekday
preload, with a work-hours pin, is a setting that is off by default; it is fit-checked and notifies
me if it doesn't fit. The low-priority "loaded" notification comes with ntfy in 2a; "Loading" on the
menu bar comes with 2b. From 2a the coder is Qwen3.8-27B.
