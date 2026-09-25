import codecs
import re
import subprocess
from pathlib import Path

import pytest

from spark import cli, leakcheck
from spark.leakcheck import DenylistInvalid, DenylistMissing, load_denylist, scan_text


def ip(*parts: int) -> str:
    """Build addresses at run time so this file never contains a literal private address."""
    return ".".join(str(p) for p in parts)


def mac(*parts: str) -> str:
    return ":".join(parts)


def ipv6(*groups: str) -> str:
    """IPv6 addresses are built at run time too."""
    return ":".join(groups)


def kinds(text: str, denylist=()) -> list[str]:
    return [f.kind for f in scan_text("t.md", text, list(denylist))]


@pytest.mark.parametrize("address", [ip(10, 1, 2, 3), ip(172, 20, 0, 5), ip(192, 168, 77, 5)])
def test_private_ipv4_is_flagged(address):
    assert kinds(f"server at {address} today") == ["private IPv4 address"]


def test_tailnet_address_is_flagged():
    assert kinds(f"node {ip(100, 101, 102, 103)}") == ["tailnet IPv4 address"]


@pytest.mark.parametrize(
    "address",
    [ip(10, 1, 2, 3), ip(172, 20, 0, 5), ip(192, 168, 77, 5)],
    ids=["10/8", "172.16/12", "192.168/16"],
)
def test_sentence_final_private_ipv4_is_flagged(address):
    assert kinds(f"The box is at {address}.") == ["private IPv4 address"]


def test_sentence_final_tailnet_address_is_flagged():
    assert kinds(f"The node is {ip(100, 101, 102, 103)}.") == ["tailnet IPv4 address"]


def test_tailnet_ipv6_address_is_flagged():
    # Tailscale's IPv6 range lies inside the unique-local range, so the broader pattern fires too.
    address = ipv6("fd7a", "115c", "a1e0", "", "5")
    assert kinds(f"node {address} today") == [
        "tailnet IPv6 address",
        "private or link-local IPv6 address",
    ]


@pytest.mark.parametrize(
    "address",
    [ipv6("fd12", "3456", "789a", "", "1"), ipv6("fe80", "", "1a2b", "3c4d", "5e6f", "7a8b")],
    ids=["unique-local", "link-local"],
)
def test_private_ipv6_is_flagged(address):
    assert kinds(f"host {address} today") == ["private or link-local IPv6 address"]


@pytest.mark.parametrize(
    "harmless",
    [
        "reserved as .201 on the wired NIC",
        f"docs example {ip(192, 0, 2, 7)} and {ip(203, 0, 113, 9)}",
        f"public {ip(100, 12, 1, 1)}",
        "uv 0.12.18 and CUDA 13.0.2",
        "at 12:34:56 it froze",
        "patterns for MACs and `ts.net` names",
        f"build {ip(10, 1, 2, 3, 4)} is longer than an address",
        "link-local fe80::/10 and unique-local fc00::/7 are ranges, not hosts",
    ],
)
def test_harmless_text_is_not_flagged(harmless):
    assert kinds(harmless) == []


def test_mac_address_is_flagged():
    assert kinds(f"nic {mac('a4', 'bb', '6d', '01', '02', 'ef')}") == ["MAC address"]


def test_tailnet_hostname_is_flagged():
    name = "brightroar." + "tail" + "1a2b" + ".ts.net"
    assert kinds(f"https://{name}/") == ["tailnet hostname"]


def test_bare_tailnet_name_is_flagged():
    name = "tail" + "1a2b" + ".ts.net"
    assert kinds(f"the tailnet is {name} today") == ["tailnet hostname"]


def test_denylisted_term_is_flagged_case_insensitively():
    deny = [re.compile("secret-project", re.IGNORECASE)]
    assert kinds("about the Secret-Project plan", deny) == ["denylisted term"]


def test_allow_marker_skips_a_line():
    text = f"example {ip(192, 168, 0, 1)}  <!-- leakcheck: allow -->"
    assert kinds(text) == []


def test_allow_marker_never_excuses_a_denylisted_term():
    # The marker lets a reviewed line past the generic patterns only; the address stays excused.
    deny = [re.compile("secret-project", re.IGNORECASE)]
    text = f"secret-project box at {ip(192, 168, 0, 1)}  <!-- leakcheck: allow -->"
    assert kinds(text, deny) == ["denylisted term"]


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


def test_cli_invalid_denylist_line_exits_2_and_never_shows_the_line(tmp_path, capsys):
    from spark import cli

    denylist = tmp_path / "denylist"
    denylist.write_text("first-term\nprivate-name(\n")
    message = tmp_path / "msg"
    message.write_text("hello\n")
    assert cli.main(["leakcheck", "--message", str(message), "--denylist", str(denylist)]) == 2
    err = capsys.readouterr().err
    assert "line 2" in err
    assert "private-name" not in err


@pytest.mark.parametrize("text", ["", "# a comment\n\n   \n"], ids=["empty", "comments-only"])
def test_a_denylist_with_no_terms_fails_closed(tmp_path, capsys, text):
    # An empty file is what `touch` makes; it would pass every commit as if nothing were private.
    path = tmp_path / "denylist"
    path.write_text(text)
    with pytest.raises(DenylistInvalid, match="denylist has no terms"):
        load_denylist(path)
    message = tmp_path / "msg"
    message.write_text("hello\n")
    assert cli.main(["leakcheck", "--message", str(message), "--denylist", str(path)]) == 2
    assert "denylist has no terms" in capsys.readouterr().err


def test_a_message_that_is_not_utf8_is_still_scanned(tmp_path, capsys):
    # CI feeds every commit's patches through --message; one stray Latin-1 byte must not crash it.
    message = tmp_path / "msg"
    message.write_bytes(b"caf\xe9 at " + ip(192, 168, 50, 7).encode() + b"\n")
    assert cli.main(["leakcheck", "--message", str(message), "--denylist", str(denylist(tmp_path))]) == 1
    assert "private IPv4 address" in capsys.readouterr().err


def denylist(tmp_path: Path, *terms: str) -> Path:
    path = tmp_path / "denylist"
    path.write_text("".join(f"{term}\n" for term in terms or ("secret-project",)))
    return path


def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def git(repo: Path, *args: str) -> None:
    # No hooks and no signing, whatever the machine's global git config says.
    (repo.parent / "no-hooks").mkdir(exist_ok=True)
    subprocess.run(
        ["git", "-c", f"core.hooksPath={repo.parent / 'no-hooks'}", "-c", "commit.gpgsign=false",
         "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo, check=True, capture_output=True,
    )


def test_a_symlink_turned_into_a_file_is_scanned(tmp_path):
    # A type change (git status T) replaces the link's target path with the file's whole content.
    repo = git_repo(tmp_path)
    (repo / "notes.md").symlink_to("elsewhere.md")
    git(repo, "add", "notes.md")
    git(repo, "commit", "-q", "-m", "link")
    (repo / "notes.md").unlink()
    (repo / "notes.md").write_text(f"box at {ip(192, 168, 50, 7)}\n")
    git(repo, "add", "notes.md")
    assert leakcheck.staged_texts(cwd=repo) == [("notes.md", f"box at {ip(192, 168, 50, 7)}\n")]


def test_a_file_turned_into_a_symlink_has_its_target_scanned(tmp_path):
    # git stores a link as its target path, so a private path there is committed text too.
    repo = git_repo(tmp_path)
    (repo / "data").write_text("placeholder\n")
    git(repo, "add", "data")
    git(repo, "commit", "-q", "-m", "file")
    (repo / "data").unlink()
    target = f"/mnt/{ip(192, 168, 50, 7)}/share"
    (repo / "data").symlink_to(target)
    git(repo, "add", "data")
    assert leakcheck.staged_texts(cwd=repo) == [("data", target)]


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be", "utf-32", "utf-32-be"])
def test_utf16_and_utf32_text_with_a_byte_order_mark_is_scanned(tmp_path, encoding):
    # Their NUL bytes would otherwise mark them as binary. `utf-16` and `utf-32` write a BOM
    # themselves (little-endian here); the big-endian codecs don't, so it is added.
    text = f"box at {ip(192, 168, 50, 7)}\n"
    bom = {"utf-16-be": codecs.BOM_UTF16_BE, "utf-32-be": codecs.BOM_UTF32_BE}.get(encoding, b"")
    repo = git_repo(tmp_path)
    (repo / "notes.txt").write_bytes(bom + text.encode(encoding))
    git(repo, "add", "notes.txt")
    assert leakcheck.staged_texts(cwd=repo) == [("notes.txt", text)]


def test_a_binary_file_is_named_for_a_person_to_check(tmp_path, monkeypatch, capsys):
    # Nothing reads inside a screenshot, so it is named rather than skipped without a word.
    repo = git_repo(tmp_path)
    (repo / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + ip(192, 168, 50, 7).encode())
    git(repo, "add", "shot.png")
    monkeypatch.chdir(repo)
    assert cli.main(["leakcheck", "--staged", "--denylist", str(denylist(tmp_path))]) == 0
    assert "leakcheck: not scanned (binary): shot.png — check it by eye" in capsys.readouterr().err


def test_a_file_name_is_scanned(tmp_path, monkeypatch, capsys):
    repo = git_repo(tmp_path)
    (repo / f"notes-{ip(192, 168, 50, 7)}.md").write_text("nothing private here\n")
    git(repo, "add", ".")
    monkeypatch.chdir(repo)
    assert cli.main(["leakcheck", "--staged", "--denylist", str(denylist(tmp_path))]) == 1
    err = capsys.readouterr().err
    # The name is printed like an excerpt: each match cut to 3 characters.
    assert "leakcheck: notes-192….md (file name):1: private IPv4 address (192…)" in err
    assert ip(192, 168, 50, 7) not in err


def test_a_denylisted_term_in_a_file_name_is_never_printed_whole(tmp_path, monkeypatch, capsys):
    # Hook output can land in a Claude session's context; a path is no exception.
    repo = git_repo(tmp_path)
    (repo / "secret-project-plan.md").write_text(f"box at {ip(192, 168, 50, 7)}\n")
    git(repo, "add", ".")
    monkeypatch.chdir(repo)
    assert cli.main(["leakcheck", "--staged", "--denylist", str(denylist(tmp_path, "secret-project"))]) == 1
    err = capsys.readouterr().err
    assert "leakcheck: sec…-plan.md (file name):1: denylisted term (sec…)" in err
    assert "leakcheck: sec…-plan.md:1: private IPv4 address (192…)" in err
    assert "secret-project" not in err


def test_ci_scans_every_tracked_file_name(tmp_path, monkeypatch, capsys):
    repo = git_repo(tmp_path)
    (repo / f"notes-{ip(192, 168, 50, 7)}.md").write_text("nothing private here\n")
    git(repo, "add", ".")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("CI", "true")
    assert cli.main(["leakcheck", "--tracked", "--ci"]) == 1
    assert "(file name):1: private IPv4 address" in capsys.readouterr().err
