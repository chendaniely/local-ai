---
title: "S13 · The Spark freezes while I'm away"
scenario-id: S13
phase: 5
status: planned
---

**Situation.** The box stops responding.

**What happens.** The watchdog on the Synology notices within minutes and sends a
high-priority notification. I fix it at home — remote power through Home Assistant is a
later phase. This is verified by a dated drill.

**What I see.** The notification, and "unreachable" on the menu bar.

**How to override.** None.

*Phase 2b, visibility (Phase 2's carving into 2a, 2b and 2c, 2026-10-07): the watchdog and the
menu bar come with 2b, beside the ntfy 2a sets up.*

*(2026-10-14, Dan's decision: ntfy optional, moving with the NAS phase; Phases 4 and 5 swapped, the
NAS phase now 5.)* ntfy moves to Phase 5, NAS and backups, and the watchdog, which needs it, moves
there with it, so this page's `phase` goes from 2 to 5. The menu bar stays in 2b, and says
"unreachable" from then; the watchdog's notification comes with Phase 5, which verifies this page.
