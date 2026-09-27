import io
import logging
import re
import tomllib
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import pytest

from spark import cli, paths
from spark import models as spark_models
from spark.models import pull
from spark.registry import load_registry

FIX = Path(__file__).parent / "fixtures" / "models.yaml"
REG = load_registry(FIX)
SPARK = Path(__file__).parents[1]
# The five files the fixture registry pins, in its order: (model, repo, file, revision).
FIVE = [("vision-chat", "example-org/vision-GGUF", "vision.gguf", "1" * 40),
        ("vision-chat", "example-org/vision-GGUF", "vision-mmproj.gguf", "1" * 40),
        ("embed", "example-org/embed-GGUF", "embed.gguf", "2" * 40),
        ("stt", "example-org/whisper", "whisper.bin", "3" * 40),
        ("coder", "example-org/coder-GGUF", "coder.gguf", "4" * 40)]
# Every fake token holds SENTINEL, so it is found in output in any form: whole, repr'd, escaped, as bytes.
VALID = "hf_SENTINELfakeTOKEN0123456789"
BAD_TOKENS = {"a vertical tab": "hf_SENTINEL\x0bfake", "a form feed": "hf_SENTINEL\x0cfake",
              "a tab": "hf_SENTINEL\tfake", "a space": "hf_SENTINEL fake", "a control character": "hf_SENTINEL\x01fake",
              "a backslash": "hf_SENTINEL\\fake", "a quote": "hf_SENTINEL'fake", "a double quote": 'hf_SENTINEL"fake',
              "a non-ASCII letter": "hf_SENTINELéfake"}


def test_pull_fetches_every_file_at_its_pinned_revision(tmp_path):
    calls = []

    def fake(**kw):
        calls.append(kw)
        return f"/cache/{kw['filename']}"

    assert pull(REG, download=fake, hf_home=str(tmp_path), log=lambda m: None) == 0
    hub = f"{tmp_path}/hub"
    assert calls == [{"repo_id": repo, "filename": file, "revision": rev, "cache_dir": hub} for _, repo, file, rev in FIVE]
    assert (tmp_path / "tmp").is_dir()  # whisper-server's --tmp-dir


def test_a_failed_file_is_reported_and_the_rest_still_download(tmp_path):
    tried, logs = [], []

    def flaky(**kw):
        tried.append(kw["filename"])
        if kw["filename"] == "embed.gguf":
            raise OSError("404 Client Error")
        return "/cache/" + kw["filename"]

    assert pull(REG, download=flaky, hf_home=str(tmp_path), log=logs.append) == 1
    assert tried == [file for _, _, file, _ in FIVE]
    assert logs == ["pull: vision-chat: vision.gguf → /cache/vision.gguf",
                    "pull: vision-chat: vision-mmproj.gguf → /cache/vision-mmproj.gguf",
                    "pull: embed: embed.gguf: FAILED — 404 Client Error",  # the reason, on the file's own line
                    "pull: stt: whisper.bin → /cache/whisper.bin",
                    "pull: coder: coder.gguf → /cache/coder.gguf"]


def test_a_failed_download_logs_one_line_with_no_token_and_no_url_query(tmp_path):
    # Task 8's review: a failure's text is the library's. It can hold the token (a server or proxy that echoes it), a
    # presigned URL (its signature is a credential for the file) and newlines (a journal entry each, only one `pull:`).
    logs = []

    def echo(**kw):
        raise OSError("401 Client Error.\n\nRepository Not Found for url: https://cdn.example/blob"
                      f"?X-Amz-Signature=SENTINELsig&X-Amz-Credential=SENTINELcred.\ndenied: Bearer {VALID}")

    assert pull(REG, download=echo, hf_home=str(tmp_path), log=logs.append, redact=VALID) == 1
    assert logs[0] == ("pull: vision-chat: vision.gguf: FAILED — 401 Client Error. Repository Not Found for url: "
                       "https://cdn.example/blob. denied: Bearer <token>")
    assert len(logs) == 5 and not any("SENTINEL" in line or "\n" in line for line in logs)


def test_a_failure_with_no_message_is_named_by_its_type(tmp_path):
    logs = []

    def timeout(**kw):
        raise TimeoutError()

    assert pull(REG, download=timeout, hf_home=str(tmp_path), log=logs.append) == 1
    assert logs[0] == "pull: vision-chat: vision.gguf: FAILED — TimeoutError"


@pytest.mark.parametrize("text, shown", [
    ("for url 'https://h.example/p?sig=SENTINEL'", "for url 'https://h.example/p'"),
    ("for url (http://h.example/p?a=1.5&sig=SENTINEL)", "for url (http://h.example/p)"),
    ("at: HTTPS://h.example/p?sig=SENTINEL, then", "at: HTTPS://h.example/p, then"),
    ("at https://h.example/p?sig=SENTINEL: and more", "at https://h.example/p: and more"),
    ("a question? Not a URL?", "a question? Not a URL?"),
    ("two\n\n lines\tand  spaces", "two lines and spaces"),
])
def test_sanitise_drops_url_queries_and_keeps_one_line(text, shown):
    assert spark_models.sanitise(text) == shown


def test_a_token_is_refused_unless_a_header_and_a_repr_carry_it_as_written():
    # Printable ASCII but a space, a quote or a backslash: h11 refuses whitespace and prints the header, and a repr()
    # escapes a quote or a backslash, which would hide the token from sanitise().
    allowed = {chr(c) for c in range(0x21, 0x7F)} - {'"', "'", "\\"}
    spark_models.check_token(None)  # no token is fine: only a gated or private repo needs one
    for c in map(chr, range(0x100)):
        token = f"hf_{c}x"
        if c in allowed:
            spark_models.check_token(token)
        else:
            with pytest.raises(spark_models.PullError) as refused:
                spark_models.check_token(token)
            assert token not in str(refused.value)


@pytest.fixture
def hub(monkeypatch, tmp_path):
    """`spark models pull` as the unit runs it, with nothing real: the fixture registry, HF_HOME in tmp_path, the
    library's token files there too (never a real one), and a download that records each call and runs `hook`."""
    import huggingface_hub
    from huggingface_hub import constants

    home = tmp_path / "hf"
    monkeypatch.setattr(paths, "REGISTRY", FIX)
    monkeypatch.setattr(spark_models, "HF_HOME", str(home))
    monkeypatch.setattr(constants, "HF_TOKEN_PATH", str(home / "token"))
    monkeypatch.setattr(constants, "HF_STORED_TOKENS_PATH", str(home / "stored_tokens"))
    state = SimpleNamespace(home=home, calls=[], hook=None)

    def download(**kw):
        state.calls.append(kw)
        if state.hook:
            state.hook(**kw)
        return f"/cache/{kw['filename']}"

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    return state


def test_spark_models_pull_downloads_every_file_through_the_cli(hub, capsys):
    # What the unit runs reaches run_pull, and with no token set: only a gated or private repo needs one.
    assert cli.main(["models", "pull"]) == 0
    cache = f"{hub.home}/hub"
    assert hub.calls == [{"repo_id": r, "filename": f, "revision": v, "cache_dir": cache} for _, r, f, v in FIVE]
    assert capsys.readouterr().out.splitlines() == [f"pull: {m}: {f} → /cache/{f}" for m, _, f, _ in FIVE]
    assert (hub.home / "tmp").is_dir()


@pytest.mark.parametrize("kind", BAD_TOKENS)
def test_a_token_a_header_would_alter_is_refused_before_any_download_and_never_shown(hub, monkeypatch, capsys, kind):
    # Task 8's review: h11 refuses a header holding a VT or an FF and prints it whole ("Illegal header value b'Bearer
    # …'"), once a file, into a journal Dan's sessions read. So the pull refuses such a token first, naming where
    # tokens come from, and shows no part of it.
    monkeypatch.setenv("HF_TOKEN", BAD_TOKENS[kind])
    assert cli.main(["models", "pull"]) == 1
    out, err = capsys.readouterr()
    assert hub.calls == [] and out == ""
    assert err.startswith("spark models: the Hugging Face token (the HF_TOKEN variable, or else the token file under "
                          "HF_HOME) ")
    assert "SENTINEL" not in out + err


def test_a_token_file_a_header_would_alter_is_refused_too(hub, capsys):
    hub.home.mkdir()
    (hub.home / "token").write_text("hf_SENTINEL\x0bfake\n")
    assert cli.main(["models", "pull"]) == 1
    out, err = capsys.readouterr()
    assert hub.calls == [] and "the token file under HF_HOME" in err and "SENTINEL" not in out + err


def test_a_token_in_a_failures_text_is_hidden(hub, monkeypatch, capsys):
    # A server or a proxy that echoes the Authorization header into its error: the line shows a marker instead.
    monkeypatch.setenv("HF_TOKEN", VALID)

    def echo(**kw):
        raise OSError(f"403 Forbidden.\ndenied: Bearer {VALID}")

    hub.hook = echo
    assert cli.main(["models", "pull"]) == 1
    out, err = capsys.readouterr()
    assert out.splitlines() == [f"pull: {m}: {f}: FAILED — 403 Forbidden. denied: Bearer <token>" for m, _, f, _ in FIVE]
    assert "SENTINEL" not in out + err


def test_the_librarys_own_lines_go_through_the_same_sanitiser(hub, monkeypatch):
    # huggingface_hub logs a retry or a resumed download itself, to stderr, naming the URL: a presigned one on the plain
    # HTTP path (file_download.py's http_get).
    handler = logging.getLogger("huggingface_hub").handlers[0]
    stream, filters = io.StringIO(), list(handler.filters)
    monkeypatch.setattr(handler, "stream", stream)
    monkeypatch.setenv("HF_TOKEN", VALID)

    def resume(**kw):
        logging.getLogger("huggingface_hub.file_download").warning(
            "Error while downloading from %s: %s\nTrying to resume download...",
            "https://cdn.example/blob?X-Amz-Signature=SENTINELsig", f"echo {VALID}")

    hub.hook = resume
    assert cli.main(["models", "pull"]) == 0
    assert stream.getvalue().splitlines() == 5 * [
        "Error while downloading from https://cdn.example/blob: echo <token> Trying to resume download..."]
    assert handler.filters == filters  # and the pull takes its filter off again


@pytest.mark.parametrize("bad", ["nested too deep", "not YAML"])
def test_a_registry_that_wont_load_is_refused_naming_the_file(hub, monkeypatch, tmp_path, capsys, bad):
    registry = tmp_path / "models.yaml"
    registry.write_text("budget: " + "[" * 100_000 + "]" * 100_000 + "\n" if bad == "nested too deep"
                        else "budget: {allocatable_gib: 102\n  : [\n")
    monkeypatch.setattr(paths, "REGISTRY", registry)
    assert cli.main(["models", "pull"]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"spark models: the registry {registry} won't load: ") and "Traceback" not in err
    assert hub.calls == []


def test_spark_models_without_a_subcommand_says_which_is_missing(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["models"])
    err = capsys.readouterr().err
    assert exc.value.code == 2
    assert "the following arguments are required: {pull}" in err and "models_command" not in err


def test_the_download_library_comes_from_the_lock():
    # `spark models pull` imports it only when it runs, and the Spark's venv holds exactly what uv.lock
    # holds. CI's venv is new each run, so a dependency missing from the lock fails here, not there.
    from huggingface_hub import hf_hub_download

    assert callable(hf_hub_download)


def _dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()  # PEP 503's form: huggingface_hub and huggingface-hub are one name


def test_the_lock_is_the_one_made_for_pyproject_and_the_venv_holds_it():
    # Task 8's fix round 1. `--frozen` installs what uv.lock holds and never compares it with pyproject.toml, so a
    # range changed there but not re-locked would go unnoticed, in CI too. uv.lock records what it was made for.
    lock = tomllib.loads((SPARK / "uv.lock").read_text())
    made_for = next(p for p in lock["package"] if p["name"] == "spark")["metadata"]["requires-dist"]
    asked = {}
    for requirement in tomllib.loads((SPARK / "pyproject.toml").read_text())["project"]["dependencies"]:
        name, specifier = re.fullmatch(r"([A-Za-z0-9._-]+)(.*)", requirement.replace(" ", "")).groups()
        asked[_dist(name)] = set(specifier.split(","))
    locked_for = {_dist(r["name"]): set(r.get("specifier", "").split(",")) for r in made_for}
    assert asked == locked_for, "uv.lock wasn't made for pyproject.toml's dependencies: run `uv lock --project spark`"
    locked = {p["name"]: p["version"] for p in lock["package"]}
    for name in asked:
        assert version(name) == locked[name], f"the venv's {name} isn't the lock's"
