---
title: "S19 · Claude Code building a pipeline elsewhere"
scenario-id: S19
phase: 4
status: planned
---

**Situation.** Claude Code, working in another repo, needs to call the Spark.

**What happens.** The `spark-endpoints` skill points it at the endpoint reference. I run
`spark keys create <app>`, which stores the key without ever printing it.

**What I see.** A working client.

**How to override.** None.

*(2026-10-14, Dan's decision: Phases 4 and 5 swapped, the bake-off now 4 and the NAS 5. This page's
`phase` goes from 5 to 4 with the bake-off, clients and Claude Code docs.)*
