---
title: "S22 · On another tailnet over WireGuard"
scenario-id: S22
phase: 3
status: planned
---

**Situation.** My device is logged into a different tailnet, and I come home over WireGuard.

**What happens.** SSH works over the LAN address, and from Phase 3 so does the API. The web
UI is Tailscale-only, so it waits.

**What I see.** pi and my scripts working; the web UI out of reach.

**How to override.** None — HTTPS on the LAN is in the backlog.
