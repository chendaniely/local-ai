"""A minimal llama-swap API client (v257): what's running, and unload one model."""

from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class LlamaSwapError(RuntimeError):
    pass


class LlamaSwapUnreachable(LlamaSwapError):
    """Nothing answered in time: llama-swap is stopped, or hung with its engines still running. Only its
    unit's state tells which (`spark apply` asks systemd)."""


@dataclass(frozen=True)
class Running:
    model: str
    state: str


def key_from_env(name: str) -> str | None:
    return os.environ.get(name) or None


class LlamaSwap:
    def __init__(self, base_url: str, api_key: str | None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _call(self, method: str, path: str) -> bytes:
        request = urllib.request.Request(self.base_url + path, method=method)
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            raise LlamaSwapError(f"llama-swap {method} {path}: HTTP {err.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            raise LlamaSwapUnreachable(f"llama-swap unreachable at {self.base_url}: {err}") from None
        except http.client.HTTPException as err:  # it answered, but the answer broke off or wasn't HTTP
            raise LlamaSwapError(f"llama-swap {method} {path}: a broken answer ({type(err).__name__})") from None

    def running(self) -> list[Running]:
        body = self._call("GET", "/running")
        try:
            return [Running(r["model"], r["state"]) for r in json.loads(body).get("running", [])]
        except (ValueError, KeyError, TypeError, AttributeError) as err:  # not the JSON v257 sends
            raise LlamaSwapError(f"llama-swap GET /running: an answer it can't read ({type(err).__name__})") from None

    def unload(self, model: str) -> None:
        self._call("POST", "/api/models/unload/" + urllib.parse.quote(model, safe=""))
