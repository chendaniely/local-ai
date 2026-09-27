import argparse
import json
import os
import stat
from pathlib import Path

import pytest

from spark import cli, clients
from spark.clients import merge_pi, pi_provider
from spark.registry import load_registry

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def test_provider_lists_chat_models_by_real_name_with_an_env_key():
    p = pi_provider(REG, "http://127.0.0.1:9100/v1", "SPARK_API_KEY")
    assert p["apiKey"] == "${SPARK_API_KEY}" and p["api"] == "openai-completions"
    assert p["compat"]["supportsDeveloperRole"] is False and p["compat"]["maxTokensField"] == "max_tokens"
    by_id = {m["id"]: m for m in p["models"]}
    assert set(by_id) == {"vision-chat", "coder"}  # no embeddings or speech models in a chat picker
    assert by_id["vision-chat"]["input"] == ["text", "image"] and by_id["coder"]["input"] == ["text"]
    assert by_id["vision-chat"]["contextWindow"] == 16384  # 32768 split over 2 slots
    assert by_id["vision-chat"]["maxTokens"] == 8192 and by_id["coder"]["maxTokens"] == 32768


def test_merge_keeps_other_providers_and_backs_up(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"providers": {"other": {"x": 1}}}))
    backup = merge_pi(path, {"baseUrl": "u"})
    data = json.loads(path.read_text())
    assert data["providers"]["other"] == {"x": 1} and data["providers"]["spark"] == {"baseUrl": "u"}
    assert json.loads(backup.read_text()) == {"providers": {"other": {"x": 1}}}


def test_a_first_write_backs_up_nothing_and_says_so(tmp_path, monkeypatch, capsys):
    path = tmp_path / "models.json"
    (tmp_path / "models.json.bak").write_text("{}")  # left by an earlier run: not a backup of this one
    assert merge_pi(path, {"baseUrl": "u"}) is None
    assert json.loads(path.read_text()) == {"providers": {"spark": {"baseUrl": "u"}}}
    monkeypatch.setattr(clients, "PI_MODELS", tmp_path / "fresh" / "models.json")
    registry = Path(__file__).parent / "fixtures" / "models.yaml"
    assert clients.run_pi(argparse.Namespace(registry=registry, base_url="u", key_env="K", write=True)) == 0
    assert "(there was no previous file)" in capsys.readouterr().out


# Task 9's rulings, R9: pi's models file holds other providers' entries, their keys included, and can be the user's own
# hand-kept file.

def test_the_backup_is_no_more_readable_than_the_file(tmp_path):
    # The file can hold other providers' keys, so its backup keeps its mode, even over an earlier, looser backup.
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"providers": {"other": {"apiKey": "a-stand-in"}}}))
    path.chmod(0o600)
    (tmp_path / "models.json.bak").write_text("{}")
    (tmp_path / "models.json.bak").chmod(0o644)
    umask = os.umask(0o022)  # what a new file gets from an ordinary write: 0644
    try:
        backup = merge_pi(path, {"baseUrl": "u"})
    finally:
        os.umask(umask)
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600 == stat.S_IMODE(path.stat().st_mode)


BAD = {"not JSON": b"{not json", "empty": b"", "not UTF-8": b'{"providers": "\xff"}',
       "nested too deep": b"[" * 100_000 + b"]" * 100_000}


@pytest.mark.parametrize("bad", [*BAD, "a folder"])
def test_a_models_file_that_wont_load_is_refused_by_name(tmp_path, bad):
    # json's own message names no file. Nothing is backed up or written.
    path = tmp_path / "models.json"
    if bad == "a folder":
        path.mkdir()
    else:
        path.write_bytes(BAD[bad])
    with pytest.raises(ValueError) as err:
        merge_pi(path, {"baseUrl": "u"})
    assert str(err.value).startswith(f"pi's models file {path} won't load: ")
    assert path.is_dir() if bad == "a folder" else path.read_bytes() == BAD[bad]
    assert not (tmp_path / "models.json.bak").exists()


@pytest.mark.parametrize(("text", "kind"), [("[]", "an array"), ('"spark"', "a string"), ("3", "a number"),
                                            ("true", "true or false"), ("null", "null")])
def test_a_top_level_that_isnt_an_object_is_refused_not_a_traceback(tmp_path, text, kind):
    path = tmp_path / "models.json"
    path.write_text(text)
    with pytest.raises(ValueError) as err:
        merge_pi(path, {"baseUrl": "u"})
    assert str(err.value) == f"pi's models file {path} holds {kind} at its top level, not the object pi reads"
    assert path.read_text() == text and not (tmp_path / "models.json.bak").exists()


def test_providers_that_arent_an_object_are_refused_too(tmp_path):
    path = tmp_path / "models.json"
    path.write_text('{"providers": ["spark"]}')
    with pytest.raises(ValueError) as err:
        merge_pi(path, {"baseUrl": "u"})
    assert str(err.value) == f"pi's models file {path}: its providers are an array, not the object pi reads"
    assert path.read_text() == '{"providers": ["spark"]}' and not (tmp_path / "models.json.bak").exists()


def test_the_cli_says_a_refusal_in_one_line(tmp_path, monkeypatch, capsys):
    path = tmp_path / "models.json"
    path.write_text("[]")
    monkeypatch.setattr(clients, "PI_MODELS", path)
    registry = Path(__file__).parent / "fixtures" / "models.yaml"
    assert cli.main(["clients", "pi", "--write", "--registry", str(registry)]) == 1
    assert capsys.readouterr().err == (f"spark clients: pi's models file {path} holds an array at its top level, not "
                                       "the object pi reads\n")


def test_a_models_file_that_is_a_link_is_written_through_it(tmp_path):
    # A dotfiles repo can keep the file and link it into ~/.pi/agent: the write goes to the file the link names, and
    # the link stays a link. Writing a new file and renaming it over the old (write_atomic) would replace the link.
    kept = tmp_path / "dotfiles" / "models.json"
    kept.parent.mkdir()
    kept.write_text(json.dumps({"providers": {"other": {"x": 1}}}))
    path = tmp_path / "models.json"
    path.symlink_to(kept)
    backup = merge_pi(path, {"baseUrl": "u"})
    assert path.is_symlink() and json.loads(kept.read_text())["providers"]["spark"] == {"baseUrl": "u"}
    assert not backup.is_symlink() and json.loads(backup.read_text()) == {"providers": {"other": {"x": 1}}}
