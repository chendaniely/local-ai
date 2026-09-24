import re
import subprocess
from pathlib import Path

import pytest

from spark import leakcheck
from spark.leakcheck import DenylistMissing, load_denylist, scan_text


def ip(*parts: int) -> str:
    """Build addresses at run time so this file never contains a literal private address."""
    return ".".join(str(p) for p in parts)


def mac(*parts: str) -> str:
    return ":".join(parts)


def kinds(text: str, denylist=()) -> list[str]:
    return [f.kind for f in scan_text("t.md", text, list(denylist))]


@pytest.mark.parametrize("address", [ip(10, 1, 2, 3), ip(172, 20, 0, 5), ip(192, 168, 77, 5)])
def test_private_ipv4_is_flagged(address):
    assert kinds(f"server at {address} today") == ["private IPv4 address"]


def test_tailnet_address_is_flagged():
    assert kinds(f"node {ip(100, 101, 102, 103)}") == ["tailnet IPv4 address"]


@pytest.mark.parametrize(
    "harmless",
    [
        "reserved as .201 on the wired NIC",
        f"docs example {ip(192, 0, 2, 7)} and {ip(203, 0, 113, 9)}",
        f"public {ip(100, 12, 1, 1)}",
        "uv 0.12.18 and CUDA 13.0.2",
        "at 12:34:56 it froze",
        "patterns for MACs and `ts.net` names",
    ],
)
def test_harmless_text_is_not_flagged(harmless):
    assert kinds(harmless) == []


def test_mac_address_is_flagged():
    assert kinds(f"nic {mac('a4', 'bb', '6d', '01', '02', 'ef')}") == ["MAC address"]


def test_tailnet_hostname_is_flagged():
    name = "brightroar." + "tail" + "1a2b" + ".ts.net"
    assert kinds(f"https://{name}/") == ["tailnet hostname"]


def test_denylisted_term_is_flagged_case_insensitively():
    deny = [re.compile("secret-project", re.IGNORECASE)]
    assert kinds("about the Secret-Project plan", deny) == ["denylisted term"]


def test_allow_marker_skips_a_line():
    text = f"example {ip(192, 168, 0, 1)}  <!-- leakcheck: allow -->"
    assert kinds(text) == []


def test_excerpt_is_redacted():
    finding = scan_text("t.md", ip(10, 9, 8, 7), [])[0]
    assert finding.excerpt == "10.…"


def test_missing_denylist_fails_closed(tmp_path):
    with pytest.raises(DenylistMissing):
        load_denylist(tmp_path / "nope")


def test_denylist_skips_blank_lines_and_comments(tmp_path):
    path = tmp_path / "denylist"
    path.write_text("# a comment\n\nsecret-project\n")
    assert [p.pattern for p in load_denylist(path)] == ["secret-project"]


def test_staged_file_with_private_address_is_found(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "notes.md").write_text(f"box at {ip(192, 168, 50, 7)}\n")
    subprocess.run(["git", "add", "notes.md"], cwd=tmp_path, check=True)
    texts = leakcheck.staged_texts(cwd=tmp_path)
    assert texts == [("notes.md", f"box at {ip(192, 168, 50, 7)}\n")]
    assert kinds(texts[0][1]) == ["private IPv4 address"]


def test_cli_refuses_ci_mode_outside_ci(monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    from spark import cli

    assert cli.main(["leakcheck", "--tracked", "--ci"]) == 2
    assert "only in CI" in capsys.readouterr().err


def test_cli_missing_denylist_exits_2(tmp_path, capsys):
    from spark import cli

    message = tmp_path / "msg"
    message.write_text("hello\n")
    assert cli.main(["leakcheck", "--message", str(message), "--denylist", str(tmp_path / "none")]) == 2
    assert "denylist not found" in capsys.readouterr().err
