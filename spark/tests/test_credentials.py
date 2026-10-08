"""Task 9's credentials: files systemd's LoadCredential= puts in $CREDENTIALS_DIRECTORY, the key digests file, and
constant-time key matching. Every key, token and digest here is a stand-in made up for the test."""

import hashlib
import hmac

import pytest

from spark import credentials
from spark.credentials import CredentialError


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@pytest.fixture
def creds(tmp_path):
    """A credentials folder, and the environment that names it, as systemd gives a unit with LoadCredential=."""
    folder = tmp_path / "credentials"
    folder.mkdir()
    return folder, {"CREDENTIALS_DIRECTORY": str(folder)}


def test_a_credential_is_read_from_its_directory_and_never_printed(creds):
    folder, env = creds
    (folder / "llamaswap-key").write_text("abc123\n")
    assert credentials.read_credential("llamaswap-key", env) == "abc123"

    for value in ("ab\x07c", "ab c"):
        (folder / "llamaswap-key").write_text(value)
        with pytest.raises(CredentialError) as refused:
            credentials.read_credential("llamaswap-key", env)
        assert "llamaswap-key" in str(refused.value)
        assert value not in str(refused.value) and repr(value) not in str(refused.value)


def test_a_missing_or_empty_credential_is_refused_by_name(creds):
    folder, env = creds
    with pytest.raises(CredentialError, match="CREDENTIALS_DIRECTORY"):
        credentials.read_credential("llamaswap-key", {})
    with pytest.raises(CredentialError, match="llamaswap-key"):
        credentials.read_credential("llamaswap-key", env)
    for empty in ("", "\n"):
        (folder / "llamaswap-key").write_text(empty)
        with pytest.raises(CredentialError, match="llamaswap-key"):
            credentials.read_credential("llamaswap-key", env)


def test_the_header_credential_reads_one_header_line(creds):
    folder, env = creds
    (folder / "ntfy-token").write_text("Authorization: Bearer tk_x\n")
    assert credentials.read_header_credential("ntfy-token", env) == ("Authorization", "Bearer tk_x")

    for text in ("Authorization: Bearer tk_x\nX-Other: tk_y\n", "Authorization Bearer tk_x\n", "Authorization:\n",
                 "Author ization: Bearer tk_x\n", "Authorization: Bearer\ttk_x\n", ""):
        (folder / "ntfy-token").write_text(text)
        with pytest.raises(CredentialError) as refused:
            credentials.read_header_credential("ntfy-token", env)
        assert "ntfy-token" in str(refused.value) and "tk_" not in str(refused.value)


def test_digests_parse_and_a_duplicate_name_is_refused_by_line_number():
    one, two, three = _digest("k1"), _digest("k2"), _digest("k3")
    text = f"# the client keys' digests\ndan-mac {one}\n\nagent {two}\n"
    assert credentials.parse_digests(text) == {"dan-mac": bytes.fromhex(one), "agent": bytes.fromhex(two)}

    with pytest.raises(CredentialError) as refused:
        credentials.parse_digests(f"# the client keys' digests\ndan-mac {one}\nagent {two}\nagent {three}\n")
    message = str(refused.value)
    assert "line 4" in message
    assert not any(digest in message for digest in (one, two, three)) and "agent" not in message

    for bad in (f"dan-mac {one[:63]}", f"dan-mac {one.upper()}", f"dan-mac {one} extra", one):
        with pytest.raises(CredentialError) as refused:
            credentials.parse_digests(f"# the client keys' digests\n{bad}\n")
        message = str(refused.value)
        assert "line 2" in message and one[:16] not in message and one[:16].upper() not in message


def test_match_key_compares_every_digest_and_returns_the_name(monkeypatch):
    digests = {"dan-mac": bytes.fromhex(_digest("k1")), "agent": bytes.fromhex(_digest("k2"))}
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    assert credentials.match_key("k2", digests) == "agent"
    assert len(calls) == 2
    calls.clear()
    assert credentials.match_key("k1", digests) == "dan-mac"
    assert len(calls) == 2  # the first digest matched, and the second was still compared, in full
    assert all(len(a) == len(b) == 32 for a, b in calls)


def test_match_key_refuses_an_unknown_key():
    digests = {"dan-mac": bytes.fromhex(_digest("k1")), "agent": bytes.fromhex(_digest("k2"))}
    assert credentials.match_key("k3", digests) is None
    assert credentials.match_key("", digests) is None
    assert credentials.match_key("", {"empty": hashlib.sha256(b"").digest()}) is None  # never the empty key
