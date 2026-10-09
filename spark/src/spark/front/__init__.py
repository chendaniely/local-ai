"""The front (Phase 2a; plan.md, *The front and the gate*): `local-ai-front.service`, as `spark-front`, on 127.0.0.1:9100,
which `local-ai-front.socket` holds. It checks the client keys, held only as digests, forwards only the inference
routes to llama-swap with an internal key of its own, counts the requests in flight, and asks the gate for each load.

- `parse` — its routes, the client key, bodies read within their caps and rebuilt, the model list, each key's caps and
  the journal line (Task 20).
- `app` — the ASGI app (Task 20).

This module imports nothing, so `spark apply` can read the front's module list from it without importing uvicorn
(Task 21)."""
