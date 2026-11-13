"""The contract: what the projects that use the stack rely on ([Projects that use the stack], its contract table, and
CLAUDE.md's sync table), pinned here so a change to any of it fails before it reaches them.

Each part is read from the file that defines it and compared with CONTRACT below. When one differs, the change touches
something another project relies on. Then, in the same change: update website/reference/projects.md (the part's
contract row and its "As of" line), open a notice for each project that relies on the part (an issue in this repo
labelled notice/<project>, after Dan's OK, since the repo is public), and set the part's new value in CONTRACT. The
plan's open item *44l changes what projects rely on* (Dan's decision, 2026-11-10: "yes all your recommendations look
good"). No fixtures file is written for the projects to test against (my call, 2026-11-13: the notices reach them, and
none of them has a test that would read one).

The summary's keys are pinned apart, in test_meter_summary.py's MENU_READS, since spark-menu reads them one by one."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
from pathlib import Path

from spark import clients, gateproto, messages
from spark.cli import build_parser
from spark.front import app, parse
from spark.meter import summary
from spark.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
FIX = ("update website/reference/projects.md (the part's contract row and its 'As of' line), open a notice for each "
       "project that relies on it (notice/<project>, with Dan's OK), then set the new value in CONTRACT")

# What spark-menu runs on the Spark (projects.md, *The `spark` CLI and the `Makefile`*), each of which must still parse.
MENU_CALLS = (("status", "--json"), ("usage", "summary", "--json"), ("usage", "import", "--source", "opencode"),
              ("usage", "harvest", "--source", "opencode"))

# The contract as of 2026-11-13 (`phase-2a` at `be36d1b`), as the repo defined it when this test was written.
# Posit Assistant has no key_env: its TUI reads OPENAI_COMPATIBLE_API_KEY and Positron its own store (clients.py).
CONTRACT: dict = {
    "front": {
        "listen": "127.0.0.1:9100",
        "served": ["/v1/models", "/v1/chat/completions", "/v1/completions", "/v1/responses", "/v1/messages",
                   "/v1/embeddings", "/v1/audio/transcriptions"],
        "max_form_bytes": 200 * 2**20,
        "max_json_bytes": 16 * 2**20,
    },
    "refusals": {
        "codes_409": ["no_fit", "loading", "held_by_brake", "footprint_suspect", "load_failed", "not_downloaded",
                      "restarting", "llama_swap_down", "draining"],
        "codes_503": ["gate_down"],
        "retry_after_s": {"no_fit": 30, "held_by_brake": 300, "gate_down": 30, "restarting": 60, "draining": 60,
                          "llama_swap_down": 30, "too_many_requests": 10},
        "shapes": {
            "no_fit": "409 error retry_after_s | content-type x-should-retry retry-after",
            "loading": "409 error | content-type x-should-retry",
            "held_by_brake": "409 error retry_after_s | content-type x-should-retry retry-after",
            "footprint_suspect": "409 error | content-type x-should-retry",
            "load_failed": "409 error | content-type x-should-retry",
            "not_downloaded": "409 error | content-type x-should-retry",
            "restarting": "409 error retry_after_s | content-type x-should-retry retry-after",
            "llama_swap_down": "409 error retry_after_s | content-type x-should-retry retry-after",
            "draining": "409 error retry_after_s | content-type x-should-retry retry-after",
            "gate_down": "503 error retry_after_s | content-type x-should-retry retry-after",
            "model_not_found": "404 error | content-type",
            "too_many_requests": "429 error retry_after_s | content-type retry-after",
            "route_not_served": "404 error | content-type",
            "concurrency_limit": "429 error | content-type",
        },
        "front_shapes": {
            "invalid_request": "400 error | content-type",
            "request_too_large": "413 error | content-type",
            "front_busy": "429 error retry_after_s | content-type retry-after",
            "uploads_busy": "429 error retry_after_s | content-type retry-after",
        },
        "front_retry_after_s": {"front_busy": 10, "uploads_busy": 30},
        "error_keys": ["code", "message"],
        "unknown_key_code": "invalid_api_key",
        "unsent": "502 front_error | x-should-retry: false",
    },
    "models": {
        "gemma-4-26b-a4b": {"roles": ["vision"], "resident": False},
        "qwen3-embedding-0.6b": {"roles": ["embed"], "resident": True},
        "whisper-large-v3-turbo": {"roles": ["stt"], "resident": True},
        "whisper-large-v3": {"roles": [], "resident": False},
        "qwen3.8-27b": {"roles": ["coder"], "resident": False},
        "qwen3.6-35b-a3b": {"roles": ["small"], "resident": True},
        "qwen3-asr-1.7b-32k": {"roles": [], "resident": False},
        "qwen3-asr-1.7b-64k": {"roles": [], "resident": False},
        "qwen3-asr-0.6b-32k": {"roles": [], "resident": False},
        "qwen3-asr-0.6b-64k": {"roles": [], "resident": False},
    },
    # How long a key's request waits for its model's load, and its caps (too_many_requests, concurrency_limit).
    "key_groups": {
        "dan": {"wait_s": 30, "max_waiting": 8, "max_open": 32},
        "agent": {"wait_s": 600, "max_waiting": 4, "max_open": 32},
    },
    "tunnel": ["9100:127.0.0.1:9100", "127.0.0.1:58991:/run/user/1000/local-ai-speech/speech.sock"],
    "speech": {"hosts": ["127.0.0.1:58991", "localhost:58991"], "max_seconds": 300},
    "clients": {
        "pi": {"base_url": "http://127.0.0.1:9100/v1", "key_env": "SPARK_API_KEY"},
        "opencode": {"base_url": "http://127.0.0.1:9100/v1", "key_env": "SPARK_OPENCODE_KEY"},
        "hermes": {"base_url": "http://127.0.0.1:9100/v1", "key_env": "SPARK_HERMES_KEY"},
        "posit": {"base_url": "http://127.0.0.1:9100/v1", "key_env": None},
    },
    "status_schema": 1,
    "summary_version": 1,
    "menu_calls": {"status --json": True, "usage summary --json": True, "usage import --source opencode": True,
                   "usage harvest --source opencode": True},
}


def _socket_listen(name: str) -> str:
    text = (ROOT / "stack" / "templates" / name).read_text()
    return re.search(r"^ListenStream=(\S+)$", text, re.MULTILINE).group(1)


def _make_n(target: str) -> str:
    # -s: GNU make 4 prints "Entering directory" lines under -C otherwise (test_makefile.py's make()).
    done = subprocess.run(["make", "-s", "-n", "-C", str(ROOT), target], capture_output=True, text=True, check=True)
    return done.stdout


def _assigned(path: Path, name: str):
    """A module-level constant's literal value, read without running the module (the speech service's file runs only
    in its own container)."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path.name} assigns no {name}")


def _parses(argv: tuple[str, ...]) -> bool:
    try:
        build_parser().parse_args(list(argv))
    except SystemExit:
        return False
    return True


def _shape(status: int, body: dict, headers) -> str:
    """A refusal's status, its body's keys and its headers' names, on one line: `409 error retry_after_s | content-type
    x-should-retry retry-after`. Every `error` holds `code` and `message` only (CONTRACT's `error_keys`)."""
    return f"{status} {' '.join(sorted(body))} | {' '.join(name.decode() for name, _ in headers)}"


def _refusals() -> dict:
    statuses = {**dict.fromkeys(messages.CODES_409, 409), **dict.fromkeys(messages.CODES_503, 503),
                **messages.FRONT_CODES}
    gate = [messages.Refusal(code, status, "words", messages.RETRY_AFTER_S.get(code)) for code, status in
            statuses.items()]
    front = [parse.Refused(400, parse.BAD_REQUEST, "words"), parse.Refused(413, parse.TOO_LARGE, "words"),
             parse.Refused(429, parse.BUSY_CODE, "words", parse.BUSY_RETRY_AFTER_S),
             parse.Refused(429, app.UPLOADS_BUSY_CODE, "words", app.UPLOADS_RETRY_AFTER_S)]
    return {
        "codes_409": list(messages.CODES_409),
        "codes_503": list(messages.CODES_503),
        "retry_after_s": dict(messages.RETRY_AFTER_S),
        "shapes": {r.code: _shape(r.status, r.body(), r.headers()) for r in gate},
        "front_shapes": {r.code: _shape(r.status, r.body(), r.headers()) for r in front},
        "front_retry_after_s": {r.code: r.retry_after_s for r in front if r.retry_after_s is not None},
        "error_keys": sorted({key for r in gate + front for key in r.body()["error"]}),
        "unknown_key_code": app.UNKNOWN_KEY_CODE,
        "unsent": f"{app.UNSENT_STATUS} {app.BROKE_CODE} | {app.NO_RETRY[0].decode()}: {app.NO_RETRY[1].decode()}",
    }


def _clients() -> dict:
    sub = argparse.ArgumentParser().add_subparsers()
    clients.register(sub)
    parser = sub.choices["clients"]
    harnesses = re.findall(r"\bclients (\w+) --write\b", _make_n("clients"))
    out = {}
    for harness in harnesses:
        args = parser.parse_args([harness])
        out[harness] = {"base_url": args.base_url, "key_env": getattr(args, "key_env", None)}
    return out


def surface() -> dict:
    """Every part of the contract, as the repo defines it now."""
    registry = load_registry(ROOT / "stack" / "models.yaml")
    serve = ROOT / "stack" / "speech" / "serve.py"
    return {
        "front": {
            "listen": _socket_listen("local-ai-front.socket"),
            "served": list(messages._SERVED),
            "max_form_bytes": app.MAX_BODY_BYTES,
            "max_json_bytes": app.MAX_JSON_BYTES,
        },
        "refusals": _refusals(),
        "models": {name: {"roles": list(m.roles), "resident": m.resident} for name, m in registry.models.items()},
        "key_groups": {name: {"wait_s": g.wait_s, "max_waiting": g.max_waiting, "max_open": g.max_open}
                       for name, g in registry.key_groups.items()},
        "tunnel": re.findall(r"-L (\S+)", _make_n("tunnel")),
        "speech": {
            "hosts": [host.decode() for host in _assigned(serve, "ALLOWED_HOSTS")],
            "max_seconds": _assigned(serve, "MAX_SECONDS"),
        },
        "clients": _clients(),
        "status_schema": gateproto.STATUS_SCHEMA,
        "summary_version": summary.SUMMARY_VERSION,
        "menu_calls": {" ".join(argv): _parses(argv) for argv in MENU_CALLS},
    }


def _differences(pinned, now, path: str) -> list[str]:
    """Where `now` differs from `pinned`, down to the first value that isn't a dict, by its path:
    `refusals.retry_after_s.draining`."""
    if isinstance(pinned, dict) and isinstance(now, dict):
        return [line for key in [*pinned, *(k for k in now if k not in pinned)]
                for line in _differences(pinned.get(key), now.get(key), f"{path}.{key}" if path else str(key))]
    return [] if pinned == now else [f"{path}:\n  pinned: {pinned!r}\n  now:    {now!r}"]


def test_the_contract_is_unchanged():
    changed = _differences(CONTRACT, surface(), "")
    assert not changed, "The contract changed, so " + FIX + ":\n" + "\n".join(changed)


def test_a_difference_names_the_value_that_changed():
    pinned = {"refusals": {"retry_after_s": {"draining": 60, "no_fit": 30}}, "tunnel": ["9100:127.0.0.1:9100"]}
    now = {"refusals": {"retry_after_s": {"draining": 61, "no_fit": 30}}, "tunnel": ["9101:127.0.0.1:9100"],
           "new_part": 1}
    assert _differences(pinned, now, "") == [
        "refusals.retry_after_s.draining:\n  pinned: 60\n  now:    61",
        "tunnel:\n  pinned: ['9100:127.0.0.1:9100']\n  now:    ['9101:127.0.0.1:9100']",
        "new_part:\n  pinned: None\n  now:    1",
    ]
