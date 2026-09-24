---
title: "S08 · Batch versus interactive"
scenario-id: S08
phase: 3
status: planned
---

**Situation.** A backlog of recordings is processing, and I start chatting or coding.

**What happens.** Batch keys run with low concurrency, so my interactive requests keep their
slots.

**What I see.** Chat stays responsive. The batch slows down.

**How to override.** Per-key concurrency in the registry.
