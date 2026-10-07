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

*Revised 2026-10-07, after the design's council:* pins are mine only, set from my own account;
`agent` keeps its model with a session of its own instead. `spark load <coder>` and
`spark pin <coder>` reach the gate on the Spark with no API key. The "loaded" notification reaches
my phone in 2a, and my Mac from 2b. Whether 2a builds the weekday preload's setting, or leaves it
until I turn it on, I decide with 2a's plan. After 2a this page stays `planned`, with a dated note
recording its gate part, until 2b's menu bar completes it.

*What I see, worded at the design's UX pass (2026-10-07):* pi shows only a slower first reply;
nothing is added to its answer. My phone gets two silent notifications, *Loading the coder for pi
on the Mac (24 s last time)…* and then *Loaded the coder in 24 s.* `spark pin coder 8h` answers
*The coder stays loaded until 18:00 (`spark unpin coder` ends the pin)*, and when it runs out a
silent *The pin on the coder ended at 18:00; it unloads after 60 min idle.* `spark status` lists
the coder as *loads when asked*, the others as *always loaded*. The plan's *What you see in Phase
2a* has every message.

*Ruled 2026-10-07, with 2a's plan (I had left it to the session):* the weekday preload isn't
built in 2a. It waits in the plan's Backlog until I ask for it, so `spark load <coder>` and
`spark pin <coder>` are 2a's way in the morning.

*Why it works this way: the questions and Dan's answers are in [Phase 2a — questions and answers](../design/phase-2a-qa.md).*
