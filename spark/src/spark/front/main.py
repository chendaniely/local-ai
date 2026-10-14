"""`spark front` (Phase 2a; plan.md, *The front and the gate*): the front as systemd runs it,
`local-ai-front.service` as `spark-front`, on 127.0.0.1:9100, the socket `local-ai-front.socket` holds, passed by its
name, `front` (sockets.listen_fds).

- **What it reads once, at start**: the deployed registry's key groups, and its model names, roles and labels, the
  front's first model list (parse.ModelList.from_registry); and three credentials LoadCredential= gives: `keys`, the
  private key list, and `client-keys`, the client keys' digests, each read whole and checked against the other
  (app.read_keys), and `llamaswap-key`, the front's own key for llama-swap. llama-swap's address is
  paths.LLAMASWAP_URL.
- **Its modules** are spark.front.FRONT_MODULES, every one imported here, before it serves, so the code it runs is the
  code it started with, and `spark apply` knows which changes restart it (Task 31).
- **The gate.** Until Task 22's GateLink, the front asks no gate: it takes every model for loaded (_NoGateLinkYet), so
  each request is forwarded as for a loaded model, and llama-swap answers. Nothing deploys from this state: Task 22
  comes before the cutover.
- **Its logging** (configure_logging, serve's, called before anything else): its lines on stderr, which the journal
  takes; its journal line for each request (parse.journal_line) under `spark.front`; uvicorn's lines without a
  traceback or the upgrade warnings; httpx's and httpcore's request lines quieted (serve.quiet_http_loggers).
- **Its stop**: SIGTERM or SIGINT; uvicorn's graceful shutdown, within gateproto.GRACEFUL_S, below the unit's
  TimeoutStopSec=, lets the answers in flight finish and cuts those that outlast it; then each upstream client
  closed.

A startup refusal is one line on stderr, exit 1: a socket missing or one too many, a credential missing or malformed
(named, never quoted), a registry or a key list that won't load, digests and a key list that disagree (a key's name,
never a key or a digest), or an address or a key for llama-swap the front can't use (the key never quoted)."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import MutableMapping

import yaml

from spark import paths
from spark.credentials import CredentialError, read_credential
from spark.front import parse
from spark.front.app import FrontApp, read_keys
from spark.front.upstream import Upstream
from spark.gateproto import GRACEFUL_S
from spark.protocols import make_protocol
from spark.registry import load_registry
from spark.serve import configure_logging, run_servers
from spark.sockets import FRONT, SocketsError, listen_fds

# The front forwards to llama-swap v257's routes and reads its answers (upstream.py cites the lines).
# test_tested_against.py fails once stack/versions.yaml moves on without it.
TESTED_AGAINST = {"llama-swap": "v257"}
HEADER_TIMEOUT_S = 10  # a request's head, and a body nobody will read, past which a connection closes (Task 9)
JOURNAL = "spark.front"  # the logger of the front's journal lines
LLAMASWAP_KEY_CREDENTIAL = "llamaswap-key"
__all__ = ["configure_logging", "run"]
_journal = logging.getLogger(JOURNAL)
_REFUSALS = (SocketsError, CredentialError, ValueError, OSError, yaml.YAMLError)  # KeyListError is a ValueError


class _NoGateLinkYet:
    """The gate as the front sees it until Task 22's GateLink: every model loaded, so nothing is admitted."""

    async def loaded(self, model: str) -> bool:
        return True

    async def admit(self, model: str, key: str, deadline: float, request_id: str) -> dict:
        raise RuntimeError("the front has no link to the gate before Task 22, so it admits nothing")


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
        app = FrontApp(parse.ModelList.from_registry(registry), keys, registry.key_groups, digests,
                       upstream=upstream, gate=_NoGateLinkYet(), caps=parse.KeyCaps(keys, registry.key_groups),
                       log=_journal.info)
    except _REFUSALS as err:
        print(f"spark front: {err}", file=sys.stderr)
        return 1
    run_servers([(app, [sockets[FRONT]])], protocol=make_protocol(peer_cred=False, header_timeout_s=HEADER_TIMEOUT_S),
                graceful_s=GRACEFUL_S, shutdown=upstream.aclose)
    return 0
