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
