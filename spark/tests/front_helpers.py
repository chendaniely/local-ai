"""Stand-ins the front's tests share (test_front_parse.py, test_front_app.py, test_front_usage.py): the key groups, the
key list and its digests, a model list, multipart bodies, and whisper's answers. Every key, digest and name here is a
stand-in; none was read from the box."""

import hashlib
from pathlib import Path

from spark.registry import load_registry, parse_keys
from frozen_registry import FROZEN_REGISTRY

GROUPS = load_registry(FROZEN_REGISTRY).key_groups
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


# Whisper's answers, with whisper.cpp v1.9.4's shape (examples/server/server.cpp, 1017-1176), every text and number
# invented. verbose_json as it sends it, dumped by nlohmann::ordered_json compact and in UTF-8, its fields in its order:
# task, language, duration (a float of samples / 16000), text (the segments' text, a line break after each), segments,
# then the language probabilities.
VERBOSE = ('{"task":"transcribe","language":"english","duration":12.345000267028809,'
           '"text":" Hello there.\\n Ça va? 你好 😀\\n","segments":[{"id":0,"text":" Hello there.","start":0.0,'
           '"end":5.12,"tokens":[50365,2425,456],"words":[{"word":" Hello","probability":0.9812},{"word":" there.",'
           '"probability":0.97}],"temperature":0.0,"avg_logprob":-0.21,"no_speech_prob":0.012},{"id":1,'
           '"text":" Ça va? 你好 😀","start":5.12,"end":12.34,"tokens":[1012],"words":[{"word":" Ça va? 你好 😀",'
           '"probability":0.88}],"temperature":0.0,"avg_logprob":-0.4,"no_speech_prob":0.02}],'
           '"detected_language":"english","detected_language_probability":0.9701,'
           '"language_probabilities":{"en":0.9701,"fr":0.021}}').encode()
# srt and vtt as it writes them (server.cpp, 1022-1064; common-whisper.cpp's to_timestamp, `%02d:%02d:%02d%s%03d`).
SRT = "1\n00:00:00,000 --> 00:00:05,120\n Hello there.\n\n2\n00:00:05,120 --> 00:01:02,345\n Ça va?\n\n".encode()
VTT = "WEBVTT\n\n00:00:00.000 --> 00:00:05.120\n Hello there.\n\n00:00:05.120 --> 01:00:02.345\n Ça va?\n\n".encode()
