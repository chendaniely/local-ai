"""The front (Phase 2a; plan.md, *The front and the gate*): `local-ai-front.service`, as `spark-front`, on 127.0.0.1:9100,
which `local-ai-front.socket` holds. It checks the client keys, held only as digests, forwards only the inference
routes to llama-swap with an internal key of its own, counts the requests in flight, and asks the gate for each load.

- `parse` — its routes, the client key, bodies read within their caps and rebuilt, the model list, each key's caps and
  the journal line (Task 20).
- `app` — the ASGI app (Tasks 20 and 21).
- `upstream` — forwarding to llama-swap, the count of what is in flight, and how a stream ended (Task 21).
- `gatelink` — the gate: admission, drains, apply's hold, the gate down, and the gate's model list (Task 22).
- `main` — `spark front` (Tasks 21 and 22).

This module imports nothing, so `spark apply` reads FRONT_MODULES from it without importing uvicorn (Task 31)."""

# Exactly the spark modules of the front's start, each imported before it serves. The unit runs `spark front`, so the
# front starts in spark.cli (its main and _front: the front's start and its exit), with what spark.cli imports at module
# level (spark.gateclient); _front imports spark.front.main, which imports the rest. spark.cli's build_parser also
# imports each other command's module, only to add its subcommand; those aren't the front's: none of their code runs
# once it serves (Task 31's review). `make apply` restarts the front when one of these changes, or the app's
# environment (apply.ENVIRONMENT: what installs uvicorn and httpx), and for no other change to the app (Task 31).
# test_front_forward.py checks the set against what importing spark.front.main and spark.cli loads.
FRONT_MODULES: frozenset[str] = frozenset({
    "spark", "spark.paths", "spark.registry", "spark.credentials", "spark.sockets", "spark.protocols",
    "spark.sdnotify", "spark.serve", "spark.gateproto", "spark.messages",
    "spark.front", "spark.front.parse", "spark.front.app", "spark.front.upstream", "spark.front.gatelink",
    "spark.front.main", "spark.cli", "spark.gateclient",
})
