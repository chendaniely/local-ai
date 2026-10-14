---
title: "S16 · Postgres down"
scenario-id: S16
phase: 3
status: planned
---

**Situation.** LiteLLM's database stops.

**What happens.** The API fails closed and a high-priority alert fires. A full disk is the
likeliest cause, so disk-low alerts come first.

**What I see.** Refusals, and the alert. *(2026-10-14, Dan's decision: ntfy optional, moving with
the NAS phase; Phases 4 and 5 swapped, the NAS phase now 5: the alert reaches my phone only once
ntfy is set up, in Phase 5, after this page's Phase 3; Phase 3's plan says how it is shown until
then.)*

**How to override.** None — fix the database or the disk.
