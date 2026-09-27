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


class LlamaSwapAnswered(LlamaSwapError):
    """It answered, but with an error, or with something it can't read: so it's up. A request never sent
    (a bad URL, a key a header can't carry) is neither this nor LlamaSwapUnreachable."""


@dataclass(frozen=True)
class Running:
    model: str
    state: str


def key_from_env(name: str) -> str | None:
    return os.environ.get(name) or None


def _url_ok(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
        parts.port  # a ValueError unless the port is a number from 0 to 65535
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


class LlamaSwap:
    def __init__(self, base_url: str, api_key: str | None, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # No proxy from the environment (http_proxy): the key would go to it, even for 127.0.0.1.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _call(self, method: str, path: str) -> bytes:
        where = f"llama-swap {method} {path}"
        if not _url_ok(self.base_url):
            raise LlamaSwapError(f"{where}: invalid URL {self.base_url!r} (want http://host:port)")
        if self.api_key and not (self.api_key.isascii() and self.api_key.isprintable()):
            # Never the value, nor any part of it. The likely cause: a CRLF line in the file the key was set from.
            raise LlamaSwapError(f"{where}: the API key isn't printable ASCII (a stray CR or LF?), so it wasn't sent")
        try:
            request = urllib.request.Request(self.base_url + path, method=method)
            if self.api_key:
                request.add_header("Authorization", f"Bearer {self.api_key}")
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as err:
            raise LlamaSwapAnswered(f"{where}: HTTP {err.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            raise LlamaSwapUnreachable(f"llama-swap unreachable at {self.base_url}: {err}") from None
        except http.client.InvalidURL:  # one urlsplit lets through, such as a control character in the host
            raise LlamaSwapError(f"{where}: invalid URL {self.base_url!r} (want http://host:port)") from None
        except http.client.HTTPException as err:  # it answered, but the answer broke off or wasn't HTTP
            raise LlamaSwapAnswered(f"{where}: a broken answer ({type(err).__name__})") from None
        except ValueError as err:  # a request it couldn't send, and its text can name a header's value
            refused = type(err).__name__
        # Raised here, not in the except, so that ValueError isn't even chained to it.
        raise LlamaSwapError(f"{where}: a request it couldn't send ({refused})")

    def running(self) -> list[Running]:
        body = self._call("GET", "/running")
        try:
            listed = json.loads(body)["running"]
        except (ValueError, KeyError, TypeError, RecursionError) as err:  # unparsable, or has no "running"
            unreadable = type(err).__name__
            raise LlamaSwapAnswered(f"llama-swap GET /running: an answer it can't read ({unreadable})") from None
        # v257 always sends {"running": [...]}, each entry with a string model and state. Anything else must not
        # read as nothing running: apply would restart llama-swap over loaded models.
        if not (isinstance(listed, list) and all(isinstance(r, dict) and isinstance(r.get("model"), str)
                                                 and isinstance(r.get("state"), str) for r in listed)):
            raise LlamaSwapAnswered("llama-swap GET /running: an answer it can't read (not v257's shape)")
        return [Running(r["model"], r["state"]) for r in listed]

    def unload(self, model: str) -> None:
        self._call("POST", "/api/models/unload/" + urllib.parse.quote(model, safe=""))
