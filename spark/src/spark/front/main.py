"""`spark front` (Phase 2a; plan.md, *The front and the gate*): the front as systemd runs it,
`local-ai-front.service` as `spark-front`, on 127.0.0.1:9100, the socket `local-ai-front.socket` holds, passed by its
name, `front` (sockets.listen_fds).

- **What it reads once, at start**: the deployed registry's key groups, and its model names, roles and labels, the
  front's first model list (parse.ModelList.from_registry); and three credentials LoadCredential= gives: `keys`, the
  private key list, and `client-keys`, the client keys' digests, each read whole and checked against the other
  (app.read_keys), and `llamaswap-key`, the front's own key for llama-swap. llama-swap's address is
  paths.LLAMASWAP_URL.
- **Its modules** are spark.front.FRONT_MODULES, every one imported before it serves, so the code it runs is the code
  it started with, and `spark apply` knows which changes restart it (Task 31): spark.cli, which `spark front` starts it
  from, with spark.gateclient, which spark.cli imports (Task 31's review), and the rest imported here.
- **The gate** (Task 22's GateLink), on the gate's status socket, paths.GATE_STATUS_SOCKET: started in the serving
  loop before the front serves (run_servers' `startup`), which returns at once, so the front serves whether the gate
  answers or not; its model list replaces the registry's from the gate's first `hello`; while the gate is down, what's
  loaded is what llama-swap's `/running` shows ready (Upstream.ready_models). The gate link and the app count requests
  in flight in one InFlight.
- **Its logging** (configure_logging, called before anything else: serve's, and one line more): its lines on stderr,
  which the journal takes; its journal line for each request (parse.journal_line) under `spark.front`; uvicorn's
  lines without a traceback or the upgrade warnings; httpx's and httpcore's request lines quieted
  (serve.quiet_http_loggers). And without uvicorn's *ASGI callable returned without completing response.*: the
  front leaves a response unfinished on purpose, to pass a cut on as a cut, and its own journal line says `cut` (the
  controller's ruling, at Task 21's review). The gate keeps that line, where it would flag a bug, and both keep
  *… without starting response.*, which is one here too.
- **Its stop**: SIGTERM or SIGINT; uvicorn's graceful shutdown, within gateproto.GRACEFUL_S, below the unit's
  TimeoutStopSec=, lets the answers in flight finish and cuts those that outlast it, the requests it holds waiting
  among them; then the cuts' journal lines still waiting for why written without it, the gate link closed, and each
  upstream client closed.

A startup refusal is one line on stderr, exit 1: a socket missing or one too many, a credential missing or malformed
(named, never quoted), a registry or a key list that won't load, digests and a key list that disagree (a key's name,
never a key or a digest), or an address or a key for llama-swap the front can't use (the key never quoted)."""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import MutableMapping

import yaml

from spark import paths, serve
from spark.credentials import CredentialError, read_credential
from spark.front import parse
from spark.front.app import FrontApp, read_keys
from spark.front.gatelink import GateLink
from spark.front.upstream import InFlight, Upstream
from spark.gateproto import GRACEFUL_S
from spark.protocols import make_protocol
from spark.registry import load_registry
from spark.serve import run_servers
from spark.sockets import FRONT, SocketsError, listen_fds

# The front forwards to llama-swap v257's routes and reads its answers (upstream.py cites the lines).
# test_tested_against.py fails once stack/versions.yaml moves on without it.
TESTED_AGAINST = {"llama-swap": "v257"}
HEADER_TIMEOUT_S = 10  # a request's head, and a body nobody will read, past which a connection closes (Task 9)
JOURNAL = "spark.front"  # the logger of the front's journal lines
LLAMASWAP_KEY_CREDENTIAL = "llamaswap-key"
# uvicorn 0.54.0 logs this at ERROR when an app returns with its response begun and unfinished (h11_impl.py:430-433),
# which is how the front passes a cut on.
INCOMPLETE_RESPONSE = "ASGI callable returned without completing response."
__all__ = ["configure_logging", "run"]
_journal = logging.getLogger(JOURNAL)
_REFUSALS = (SocketsError, CredentialError, ValueError, OSError, yaml.YAMLError)  # KeyListError is a ValueError


def configure_logging() -> None:
    """The front's logging, before it serves: serve.configure_logging's, with uvicorn's INCOMPLETE_RESPONSE line
    dropped too (the controller's ruling, at Task 21's review). Called again, it adds nothing."""
    serve.configure_logging(also_dropped=(INCOMPLETE_RESPONSE,))


def run(env: MutableMapping[str, str] = os.environ) -> int:
    """`spark front`: until SIGTERM or SIGINT, then 0; a startup refusal, one line on stderr, then 1."""
    configure_logging()
    try:
        sockets = listen_fds(env)
        if set(sockets) != {FRONT}:
            raise SocketsError(f"systemd passed the front the sockets {', '.join(sorted(sockets)) or 'none'}; it takes "
                               f"exactly {FRONT}")
        registry = load_registry(paths.REGISTRY)
        keys, digests = read_keys(registry.key_groups, env)
        upstream = Upstream(paths.LLAMASWAP_URL, read_credential(LLAMASWAP_KEY_CREDENTIAL, env))
        models, inflight = parse.ModelList.from_registry(registry), InFlight()
        gate = GateLink(paths.GATE_STATUS_SOCKET, time.time, inflight=inflight, models=models,
                        running=upstream.ready_models)
        app = FrontApp(models, keys, registry.key_groups, digests, upstream=upstream, gate=gate,
                       caps=parse.KeyCaps(keys, registry.key_groups), log=_journal.info, inflight=inflight)
    except _REFUSALS as err:
        print(f"spark front: {err}", file=sys.stderr)
        return 1

    async def shutdown() -> None:
        try:
            await app.aclose()
            await gate.aclose()
        finally:
            await upstream.aclose()

    run_servers([(app, [sockets[FRONT]])], protocol=make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S),
                graceful_s=GRACEFUL_S, startup=gate.start, shutdown=shutdown)
    return 0
