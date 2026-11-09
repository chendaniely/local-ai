"""The front (Phase 2a; plan.md, *The front and the gate*): `local-ai-front.service`, as `spark-front`, on 127.0.0.1:9100,
which `local-ai-front.socket` holds. It checks the client keys, held only as digests, forwards only the inference
routes to llama-swap with an internal key of its own, counts the requests in flight, asks the gate for each load, and
writes a row of numbers for each request.

- `parse` — its routes, the client key, bodies read within their caps and rebuilt, the model list, each key's caps and
  the journal line (Task 20).
- `app` — the ASGI app (Tasks 20 and 21).
- `upstream` — forwarding to llama-swap, the count of what is in flight, and how a stream ended (Task 21).
- `gatelink` — the gate: admission, drains, apply's hold, the gate down, and the gate's model list (Task 22).
- `usage` — the usage scan: an answer's counts, read from a fixed head and tail as it passes (Task 44a).
- `rows` — a request's row of numbers, and its shape read from its body (Task 44d), put on spark.rowfile's row file,
  outside this package because the gate writes its ledger with it too (Task 44b).
- `main` — `spark front` (Tasks 21, 22 and 44d).

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
    "spark.front.usage", "spark.front.rows", "spark.rowfile", "spark.front.main", "spark.cli", "spark.gateclient",
})
