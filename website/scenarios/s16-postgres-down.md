---
title: "S16 · Postgres down"
scenario-id: S16
phase: 3
status: planned
---

**Situation.** LiteLLM's database stops.

**What happens.** The API fails closed and a high-priority alert fires. A full disk is the
likeliest cause, so disk-low alerts come first.

**What I see.** Refusals, and the alert.

**How to override.** None — fix the database or the disk.
