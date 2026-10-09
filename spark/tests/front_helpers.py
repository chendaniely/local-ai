"""Stand-ins the front's tests share (test_front_parse.py, test_front_app.py): the key groups, the key list and its
digests, a model list, and multipart bodies. Every key, digest and name here is a stand-in; none was read from the
box."""

import hashlib
from pathlib import Path

from spark.registry import load_registry, parse_keys

ROOT = Path(__file__).resolve().parents[2]
GROUPS = load_registry(ROOT / "stack" / "models.yaml").key_groups
KEYS_TEXT = (Path(__file__).parent / "fixtures" / "keys.yaml").read_text()
KEYS = parse_keys(KEYS_TEXT, GROUPS)
# Stand-in client keys, each with its digest as the front holds it.
DIGESTS = {name: hashlib.sha256(key.encode()).digest() for name, key in
           (("dan-mac", "k1"), ("agent", "k2"), ("open-webui", "k3"))}
MODELS = [
    {"name": "gemma-4-26b-a4b", "roles": ["small", "vision"], "label": "Gemma", "resident": True},
    {"name": "qwen3-embedding-0.6b", "roles": ["embed"], "label": "the embeddings", "resident": True},
    {"name": "whisper-large-v3-turbo", "roles": ["stt"], "label": "whisper", "resident": True},
    {"name": "qwen3.8-27b", "roles": ["coder"], "label": "the coder", "resident": False},
]
BOUNDARY = "spark-test-boundary"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
MIB = 1 << 20


def receiver(chunks: list[bytes], *, gone: bool = False):
    """receive() as uvicorn gives it: each chunk in its own message, `more_body` on all but the last; with `gone`, the
    client goes after the chunks instead."""
    messages_ = [{"type": "http.request", "body": chunk, "more_body": True} for chunk in chunks]
    if gone:
        messages_.append({"type": "http.disconnect"})
    elif messages_:
        messages_[-1]["more_body"] = False
    else:
        messages_.append({"type": "http.request", "body": b"", "more_body": False})

    async def receive():
        return messages_.pop(0)

    return receive


def form_body(parts: list[tuple[dict[str, str], bytes]], boundary: str = BOUNDARY) -> bytes:
    """A multipart body: each part's headers, then its data."""
    out = b""
    for headers, data in parts:
        out += f"--{boundary}\r\n".encode()
        for name, value in headers.items():
            out += f"{name}: {value}\r\n".encode()
        out += b"\r\n" + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode()


def field(name: str, value: str) -> tuple[dict[str, str], bytes]:
    return {"Content-Disposition": f'form-data; name="{name}"'}, value.encode()


def file_part(data: bytes, name: str = "file", filename: str = "talk.wav") -> tuple[dict[str, str], bytes]:
    return {"Content-Disposition": f'form-data; name="{name}"; filename="{filename}"',
            "Content-Type": "audio/wav"}, data
