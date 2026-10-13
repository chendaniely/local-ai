"""`spark watch` — the live view of the stack: memory, what's loaded, each engine's in-flight and deferred
requests, and the brake. It is `spark status`, refreshed in a loop, plus the per-engine counts only the engine
itself knows: render's `--metrics` serves Prometheus counters on the engine's port, `llamacpp:requests_processing`
is what it's working on now and `llamacpp:requests_deferred` is what's waiting for a slot. The engine's port is
llama-swap's /running answer, its `proxy` field (v257 has filled in its ${PORT}).

Only loopback proxies are fetched: render binds every engine to 127.0.0.1, so any other proxy is a
misconfiguration, which is reported, never followed. As `spark status`, it reports problems rather than failing
on them: what it can't read, it says so, shows the rest, and exits 0.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

from spark import paths
from spark.brake import read_key_check
from spark.hold import read_hold
from spark.launch import REFUSALS, newest_refusal
from spark.llamaswap import LlamaSwap, LlamaSwapError, Running, key_from_env
from spark.memory import read_meminfo
from spark.registry import Registry, load_registry
from spark.status import _brake, _closed, _loaded, _memory, _one_line, _shown, gather

# The two gauges `spark watch` reads, from the engine's own /metrics (render's --metrics, b11146).
IN_FLIGHT = "llamacpp:requests_processing"
DEFERRED = "llamacpp:requests_deferred"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is the answer, never a new destination: the engine's /metrics stays on the engine, whatever
    answers on its port."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# No proxy from the environment (http_proxy): whatever answers on the engine's port could send the request
# elsewhere. The engine takes no key (llama-swap checks them), so there is nothing to keep either.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def parse_metrics(text: str) -> dict[str, list[float]]:
    """Prometheus's text exposition into name → values: `name value` and `name{labels} value`, with `#` lines,
    lines without a value, and values that aren't finite numbers skipped."""
    samples: dict[str, list[float]] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition(" ")
        if not sep:
            continue
        try:
            number = float(value)
        except ValueError:
            continue
        if not math.isfinite(number):
            continue
        samples.setdefault(name.split("{", 1)[0], []).append(number)
    return samples


def _loopback(url: str) -> bool:
    """Whether `url` is where render binds an engine: http, on 127.0.0.1. Anything else is a misconfiguration:
    reported, never fetched."""
    try:
        parts = urllib.parse.urlsplit(url)
        parts.port
    except ValueError:
        return False
    return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")


def fetch_metrics(proxy: str, timeout: float = 1.0) -> dict[str, list[float]]:
    """The engine's /metrics, from its proxy URL. The caller has checked the URL is loopback."""
    request = urllib.request.Request(proxy.rstrip("/") + "/metrics")
    with _opener.open(request, timeout=timeout) as response:
        return parse_metrics(response.read().decode("utf-8", "replace"))


def engine_entries(running: list[Running] | None, registry: Registry | None, problems: dict[str, str]) -> list[dict] | None:
    """Each loaded model's engine: in_flight and deferred from its /metrics, or a note that says why not; None
    when /running couldn't be read. A note that names a fault also goes into `problems`, under "engines"."""
    if running is None:
        return None
    entries = []
    for r in running:
        model = None if registry is None else registry.models.get(r.model)
        entry = {"model": r.model, "in_flight": None, "deferred": None, "note": None}
        if model is not None and model.engine == "whisper.cpp":
            entry["note"] = "no metrics endpoint (whisper.cpp)"
        elif not r.proxy:
            entry["note"] = "no engine port in llama-swap's /running answer"
        elif not _loopback(r.proxy):
            entry["note"] = f"the engine's port {r.proxy} isn't loopback, so it isn't fetched"
            problems.setdefault("engines", f"{r.model}: {entry['note']}")
        else:
            try:
                samples = fetch_metrics(r.proxy)
            except urllib.error.HTTPError as err:
                entry["note"] = f"its /metrics answers HTTP {err.code}"
                if err.code == 404:  # b11146's /metrics is off until render's --metrics reaches the engine
                    entry["note"] += " (the engine needs render's --metrics: `make apply`)"
                problems.setdefault("engines", f"{r.model}: {entry['note']}")
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as err:
                entry["note"] = f"its /metrics is unreachable ({type(err).__name__})"
                problems.setdefault("engines", f"{r.model}: {entry['note']}")
            else:
                if IN_FLIGHT in samples and DEFERRED in samples:
                    entry["in_flight"] = int(sum(samples[IN_FLIGHT]))
                    entry["deferred"] = int(sum(samples[DEFERRED]))
                else:
                    entry["note"] = "its /metrics has no llamacpp:requests_processing / llamacpp:requests_deferred"
                    problems.setdefault("engines", f"{r.model}: {entry['note']}")
        entries.append(entry)
    return entries


def _engines(entries: list[dict] | None) -> str:
    if entries is None:
        return "unknown"
    if not entries:
        return "nothing"
    parts = []
    for e in entries:
        if e["in_flight"] is not None:
            parts.append(f"{e['model']}: {e['in_flight']} in flight, {e['deferred']} deferred")
        else:
            parts.append(f"{e['model']}: {e['note']}")
    return " · ".join(parts)


def format_text(status: dict, engines: list[dict] | None) -> str:
    lines = [
        f"memory   {_memory(status['memory'], status['registry_loaded'])}",
        f"loaded   {_loaded(status['loaded'], status['registry_loaded'])}",
        f"engines  {_engines(engines)}",
        f"brake    {_brake(status['brake'])}",
    ]
    r = status["last_refusal"]
    if r:
        lines.append(f"refused  {r['model']} at {r['at']}: {r['reason']}")
    lines += [f"problem  {text}" for text in status["problems"]]
    return "\n".join(_shown(line) for line in lines)


def snapshot(key_env: str) -> tuple[dict, list[dict] | None]:
    """One round: the same reads as `spark status`, and each loaded engine's counts. The reads are copied from
    `spark status`, not shared: Phase 2a's Task 29 changes how status reads the refusals, and this view must not
    break when it does."""
    problems: dict[str, str] = {}
    try:
        registry = load_registry(paths.REGISTRY)
    except (OSError, ValueError, yaml.YAMLError, RecursionError) as err:  # RecursionError: YAML nested too deep
        registry = None
        problems["registry"] = f"the registry {paths.REGISTRY} won't load: {_one_line(err)}"
    key = key_from_env(key_env)
    try:
        running = LlamaSwap(paths.LLAMASWAP_URL, key, timeout=3).running()
    except LlamaSwapError as err:  # its text never holds the key, and says "unreachable" only when nothing answered
        running = None
        problems["llama-swap"] = str(err) if key else f"{err} (no key in ${key_env})"
    try:
        mem = read_meminfo()
    except (OSError, ValueError) as err:
        mem = None
        problems["memory"] = f"can't read memory: {err}"
    hold = refusal = key_check = None
    if _closed(paths.STATE):
        problems["state"] = (f"this account can't read {paths.STATE}, so the brake's hold and its start check are "
                             "unknown to it; spark-admin can read them")
    else:
        hold = read_hold(paths.STATE)
        key_check, damaged = read_key_check(paths.STATE)
        if damaged:
            problems["key check"] = damaged
    closed = [folder for folder in (paths.LAUNCH, paths.LAUNCH / REFUSALS) if _closed(folder)]
    if closed:
        problems["refusal"] = f"this account can't read {closed[0]}, so the last refusal is unknown to it"
    else:
        refusal, damaged = newest_refusal(paths.LAUNCH)
        if damaged:
            problems["refusal"] = damaged
    entries = engine_entries(running, registry, problems)
    status = gather(mem, registry, running, hold, refusal, problems=problems,
                    can_release=os.access(paths.STATE, os.W_OK | os.X_OK), key_check=key_check)
    return status, entries


def _positive(value: str) -> float:
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("the interval must be positive")
    return seconds


def run(args: argparse.Namespace) -> int:
    def once() -> None:
        status, engines = snapshot(args.key_env)
        if args.json:
            print(json.dumps({**status, "engines": engines}, indent=2, allow_nan=False))
        else:
            print(format_text(status, engines))

    if args.once or args.json:
        once()
        return 0
    try:
        while True:
            sys.stdout.write("\x1b[2J\x1b[H")  # clear the screen and home: the next frame replaces this one
            once()
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "watch", help="the live view: memory, loaded, each engine's in-flight and deferred, the brake")
    p.add_argument("--interval", type=_positive, default=2.0, help="seconds between refreshes (default 2)")
    p.add_argument("--once", action="store_true", help="one snapshot and out")
    p.add_argument("--json", action="store_true", help="one snapshot as JSON (implies --once)")
    p.add_argument("--key-env", default="SPARK_API_KEY", help="env var holding a llama-swap key")
    p.set_defaults(func=run)
