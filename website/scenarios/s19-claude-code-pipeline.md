---
title: "S19 · Claude Code building a pipeline elsewhere"
scenario-id: S19
phase: 5
status: planned
---

**Situation.** Claude Code, working in another repo, needs to call the Spark.

**What happens.** The `spark-endpoints` skill points it at the endpoint reference. I run
`spark keys create <app>`, which stores the key without ever printing it.

**What I see.** A working client.

**How to override.** None.
