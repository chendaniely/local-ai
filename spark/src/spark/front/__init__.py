"""The front (Phase 2a; plan.md, *The front and the gate*): `local-ai-front.service`, as `spark-front`, on 127.0.0.1:9100,
which `local-ai-front.socket` holds. It checks the client keys, held only as digests, forwards only the inference
routes to llama-swap with an internal key of its own, counts the requests in flight, and asks the gate for each load.

- `parse` — its routes, the client key, bodies read within their caps and rebuilt, the model list, each key's caps and
  the journal line (Task 20).
- `app` — the ASGI app (Tasks 20 and 21).
- `upstream` — forwarding to llama-swap, the count of what is in flight, and how a stream ended (Task 21).
- `main` — `spark front` (Task 21).

This module imports nothing, so `spark apply` reads FRONT_MODULES from it without importing uvicorn (Task 31)."""

# Exactly the spark modules the front imports at start, every one before it serves (spark.front.main imports them):
# `make apply` restarts the front when one of them changes, and for no change elsewhere in the app (Task 31).
# test_front_forward.py checks the set against what importing spark.front.main loads. Task 22 adds
# spark.front.gatelink.
FRONT_MODULES: frozenset[str] = frozenset({
    "spark", "spark.paths", "spark.registry", "spark.credentials", "spark.sockets", "spark.protocols",
    "spark.sdnotify", "spark.serve", "spark.gateproto", "spark.messages",
    "spark.front", "spark.front.parse", "spark.front.app", "spark.front.upstream", "spark.front.main",
})
