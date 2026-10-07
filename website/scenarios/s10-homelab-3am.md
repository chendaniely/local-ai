---
title: "S10 · Homelab app at 3 am"
scenario-id: S10
phase: 3
status: planned
---

**Situation.** A homelab app asks for a model that isn't loaded and fits.

**What happens.** It loads under the same rule as my own requests, and idle-unloads after 30
minutes. Notifications for this are low priority and stay quiet at night. *(Corrected 2026-10-07:
an on-demand model idle-unloads after 60 minutes from Phase 2a, Dan's decision.)*

**What I see.** Nothing, unless I go looking.

**How to override.** The app key's access group.
