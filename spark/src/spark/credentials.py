"""Credentials for the new services (website/design/plan.md, *Users, access and security*): each reaches its service
as a file through systemd's LoadCredential=, in the folder $CREDENTIALS_DIRECTORY names, never on a command line or
in the environment; the client keys reach the front only as their SHA-256 digests, compared in constant time.

No error here shows a credential's content, a digest or a key's name: only which credential, and for the digests file,
which line. A refusal can reach the journal, and Dan's keys list is private."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import unicodedata
from collections.abc import Mapping
from pathlib import Path

DIGEST = re.compile(r"[0-9a-f]{64}")  # SHA-256, as `sha256sum` writes it
HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")  # RFC 9110's token


class CredentialError(Exception):
    """A credential missing or malformed. Its text names the credential, never its content."""


def _raw(name: str, env: Mapping[str, str]) -> bytes:
    """The credential's bytes, as they are; refused when the folder or the file is missing or can't be read."""
    directory = env.get("CREDENTIALS_DIRECTORY")
    if not directory:
        raise CredentialError(f"CREDENTIALS_DIRECTORY isn't set, so the credential {name} can't be read: "
                              f"its unit gives it with LoadCredential=")
    try:
        raw = (Path(directory) / name).read_bytes()
    except FileNotFoundError:
        raise CredentialError(f"the credential {name} is missing: its unit gives it with LoadCredential=") from None
    except OSError as exc:
        raise CredentialError(f"the credential {name} can't be read: {exc.strerror}") from None
    return raw


def _read(name: str, env: Mapping[str, str]) -> bytes:
    """The credential's bytes, with one trailing newline dropped; refused when missing or empty."""
    raw = _raw(name, env)
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if not raw:
        raise CredentialError(f"the credential {name} is empty")
    return raw


def read_credential(name: str, env: Mapping[str, str] = os.environ) -> str:
    """The credential `name`, a key or a token: printable ASCII without spaces, one trailing newline dropped."""
    raw = _read(name, env)
    if not all(0x21 <= byte <= 0x7E for byte in raw):
        raise CredentialError(f"the credential {name} holds something other than printable ASCII without spaces")
    return raw.decode("ascii")


def read_header_credential(name: str, env: Mapping[str, str] = os.environ) -> tuple[str, str]:
    """The credential `name` as one HTTP header, a `Name: value` line, such as ntfy's `Authorization: Bearer …`, the
    form curl's `-H @file` reads too: (name, value)."""
    raw = _read(name, env)
    if not all(0x20 <= byte <= 0x7E for byte in raw):  # a second line, a tab or a control character
        raise CredentialError(f"the credential {name} isn't one 'Name: value' header line in printable ASCII")
    header, colon, value = raw.decode("ascii").partition(":")
    value = value.strip(" ")
    if not colon or not HEADER_NAME.fullmatch(header) or not value:
        raise CredentialError(f"the credential {name} isn't one 'Name: value' header line")
    return header, value


def read_credential_text(name: str, env: Mapping[str, str] = os.environ) -> str:
    """The credential `name` as text of many lines, kept whole for its own parser: the key list (`keys`) and the key
    digests. Refused when missing, empty or blank, not UTF-8, or holding a control character other than a tab or a
    line break; the refusal names the credential and never quotes it, as UnicodeDecodeError's text would."""
    raw = _raw(name, env)
    if not raw.strip():
        raise CredentialError(f"the credential {name} is empty")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialError(f"the credential {name} isn't UTF-8 text") from None
    if any(unicodedata.category(char) == "Cc" and char not in "\t\n\r" for char in text):
        raise CredentialError(f"the credential {name} holds a control character other than a tab or a line break")
    return text


def parse_digests(text: str) -> dict[str, bytes]:
    """The client keys' digests file: `<key name> <64 lowercase hex>` lines, the hex SHA-256 of the key; `#` comments
    and blank lines skipped. {key name: the 32-byte digest}. A malformed line or a name given twice is refused by its
    line number."""
    digests: dict[str, bytes] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) != 2 or not DIGEST.fullmatch(fields[1]):
            raise CredentialError(f"line {number} of the key digests isn't '<key name> <64 lowercase hex>'")
        name, digest = fields
        if name in digests:
            raise CredentialError(f"line {number} of the key digests names a key an earlier line already named")
        digests[name] = bytes.fromhex(digest)
    return digests


def match_key(presented: str, digests: dict[str, bytes]) -> str | None:
    """The name of the key whose digest the presented key's SHA-256 matches, or None. Every digest is compared, in
    full and in constant time, whichever matches, so the time taken says nothing about which or how much matched. An
    empty key matches nothing, nor does one no encoder takes (a lone surrogate)."""
    if not presented:
        return None
    try:
        digest = hashlib.sha256(presented.encode("utf-8")).digest()
    except UnicodeEncodeError:  # its text would quote the key's character, and no key the Spark issued holds one
        return None
    found = None
    for name, expected in digests.items():
        if hmac.compare_digest(digest, expected) and found is None:
            found = name
    return found
