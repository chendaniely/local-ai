import argparse
import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from spark import cli, clients
from spark.clients import hermes_provider, merge_hermes, merge_opencode, merge_pi, opencode_provider, pi_provider
from spark.registry import load_registry

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


def test_provider_lists_chat_models_by_real_name_with_an_env_key():
    p = pi_provider(REG, "http://127.0.0.1:9100/v1", "SPARK_API_KEY")
    assert p["apiKey"] == "${SPARK_API_KEY}" and p["api"] == "openai-completions"
    assert p["compat"]["supportsDeveloperRole"] is False and p["compat"]["maxTokensField"] == "max_tokens"
    by_id = {m["id"]: m for m in p["models"]}
    assert set(by_id) == {"vision-chat", "coder"}  # no embeddings or speech models in a chat picker
    assert by_id["vision-chat"]["input"] == ["text", "image"] and by_id["coder"]["input"] == ["text"]
    assert by_id["vision-chat"]["contextWindow"] == 32768  # its 2 slots share the pool: one request can use it all
    assert by_id["vision-chat"]["maxTokens"] == 16384 and by_id["coder"]["maxTokens"] == 32768


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


def test_the_models_file_is_found_when_the_command_runs_not_at_import(tmp_path, monkeypatch, capsys):
    # Task 9's rulings, R10. The tests import clients when they're collected, before conftest gives each a HOME of its
    # own: a path fixed then is the real ~/.pi of whoever runs them, and a test that forgot to patch it wrote there.
    written = []
    monkeypatch.setattr(clients, "merge_pi", lambda path, provider: written.append(path))  # writes nothing, anywhere
    monkeypatch.setenv("HOME", str(tmp_path))
    registry = Path(__file__).parent / "fixtures" / "models.yaml"
    assert clients.run_pi(argparse.Namespace(registry=registry, base_url="u", key_env="K", write=True)) == 0
    assert written == [tmp_path / ".pi/agent/models.json"]
    assert f"to {tmp_path / '.pi/agent/models.json'} (there was no previous file)" in capsys.readouterr().out


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
    # json's error and the codec's keep the file's text, other providers' keys included: none is chained to it.
    assert err.value.__cause__ is None and err.value.__context__ is None
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


# OpenCode's config and Hermes's get the same care as pi's file: each holds other providers' settings and keys.

def test_opencode_provider_lists_chat_models_with_their_limits_and_an_env_key():
    p = opencode_provider(REG, "http://127.0.0.1:9100/v1", "SPARK_API_KEY")
    assert p["npm"] == "@ai-sdk/openai-compatible"
    assert p["options"] == {"apiKey": "{env:SPARK_API_KEY}", "baseURL": "http://127.0.0.1:9100/v1"}
    assert set(p["models"]) == {"vision-chat", "coder"}  # no embeddings or speech models in a chat picker
    assert p["models"]["vision-chat"]["limit"] == {"context": 32768, "output": 16384}
    assert p["models"]["coder"]["limit"] == {"context": 131072, "output": 32768}


def test_opencode_offers_only_the_efforts_qwen38s_template_takes():
    # OpenCode offers low, medium and high for an OpenAI-compatible model, and Qwen3.8's template refuses high:
    # "Supported types are xhigh (default), medium, and low" (seen 2026-10-14). A model's own variants replace
    # OpenCode's, so these three are all it offers; its default sends no effort, which that template reads as xhigh.
    p = opencode_provider(REG, "http://127.0.0.1:9100/v1", "SPARK_API_KEY")
    for name in ("vision-chat", "coder"):
        assert p["models"][name]["variants"] == {"low": {"reasoningEffort": "low"},
                                                 "medium": {"reasoningEffort": "medium"},
                                                 "xhigh": {"reasoningEffort": "xhigh"}}
    p["models"]["coder"]["variants"]["low"]["reasoningEffort"] = "changed"  # each model's are its own, not one shared
    assert p["models"]["vision-chat"]["variants"]["low"] == {"reasoningEffort": "low"}


def test_opencode_merge_keeps_other_providers_and_settings_and_backs_up(tmp_path):
    path = tmp_path / "opencode.json"
    old = {"theme": "x", "provider": {"other": {"options": {"apiKey": "a-stand-in"}}, "spark": {"old": True}}}
    path.write_text(json.dumps(old))
    path.chmod(0o600)
    backup = merge_opencode(path, {"npm": "n"})
    data = json.loads(path.read_text())
    assert data["theme"] == "x" and data["provider"]["other"] == old["provider"]["other"]
    assert data["provider"]["spark"] == {"npm": "n"}  # replaced whole, so a model the registry dropped goes too
    assert json.loads(backup.read_text()) == old and stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_a_first_opencode_write_starts_a_config_with_its_schema(tmp_path):
    path = tmp_path / "opencode" / "opencode.json"
    assert merge_opencode(path, {"npm": "n"}) is None
    assert json.loads(path.read_text()) == {"$schema": "https://opencode.ai/config.json",
                                            "provider": {"spark": {"npm": "n"}}}


@pytest.mark.parametrize("bad", [b'{\n  // a comment, as JSONC allows\n  "provider": {}\n}', b"[]",
                                 b'{"provider": ["spark"]}', b'{"provider": "\xff"}'])
def test_an_opencode_config_it_cant_read_is_refused_before_anything_is_written(tmp_path, bad):
    path = tmp_path / "opencode.json"
    path.write_bytes(bad)
    with pytest.raises(ValueError) as err:
        merge_opencode(path, {"npm": "n"})
    assert str(err.value).startswith(f"OpenCode's config {path}")
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert path.read_bytes() == bad and not (tmp_path / "opencode.json.bak").exists()


def test_hermes_provider_names_the_key_and_each_models_context_and_holds_no_key():
    p = hermes_provider(REG, "http://127.0.0.1:9100/v1", "SPARK_API_KEY")
    assert p == {"api": "http://127.0.0.1:9100/v1", "key_env": "SPARK_API_KEY", "transport": "chat_completions",
                 "models": {"vision-chat": {"context_length": 32768}, "coder": {"context_length": 131072}}}


def test_hermes_merge_keeps_the_model_it_had_and_other_providers(tmp_path):
    path = tmp_path / "config.yaml"
    old = {"model": {"provider": "openrouter", "default": "some/model", "fallback": "x"},
           "providers": {"work": {"api": "https://w/v1", "key_env": "W"}}, "terminal": {"backend": "local"}}
    path.write_text(yaml.safe_dump(old))
    path.chmod(0o600)
    backup = merge_hermes(path, {"api": "u"})
    data = yaml.safe_load(path.read_text())
    assert data["model"] == old["model"] and data["terminal"] == old["terminal"]
    assert data["providers"] == {"work": old["providers"]["work"], "spark": {"api": "u"}}
    assert yaml.safe_load(backup.read_text()) == old and stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_hermes_default_sets_its_model_and_keeps_its_other_model_settings(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"model": {"provider": "openrouter", "default": "some/model", "fallback": "x"}}))
    merge_hermes(path, {"api": "u"}, default="coder")
    assert yaml.safe_load(path.read_text())["model"] == {"provider": "custom:spark", "default": "coder",
                                                         "fallback": "x"}


def test_a_first_hermes_write_sets_only_the_provider(tmp_path):
    path = tmp_path / ".hermes" / "config.yaml"
    assert merge_hermes(path, {"api": "u"}) is None
    assert yaml.safe_load(path.read_text()) == {"providers": {"spark": {"api": "u"}}}


def test_a_hermes_config_that_wont_load_is_refused_without_quoting_it(tmp_path):
    # PyYAML's own message quotes the line it stopped at, which here holds another provider's key.
    path = tmp_path / "config.yaml"
    bad = "providers:\n  work:\n    api_key: a-stand-in-key: [\n"
    path.write_text(bad)
    with pytest.raises(ValueError) as err:
        merge_hermes(path, {"api": "u"})
    assert str(err.value).startswith(f"Hermes's config {path} won't load: it isn't valid YAML (line ")
    assert "a-stand-in-key" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert path.read_text() == bad and not (tmp_path / "config.yaml.bak").exists()


@pytest.mark.parametrize(("text", "says"), [
    ("- a\n- b\n", " holds a list at its top level, not the mapping Hermes reads"),
    ("providers: [spark]\n", ": its providers are a list, not the mapping Hermes reads"),
    ("model: some/model\n", ": its model is a string, not a mapping, so --default can't set it; pick the Spark model "
                            "in Hermes with /model instead")])
def test_a_hermes_config_of_the_wrong_shape_is_refused(tmp_path, text, says):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    with pytest.raises(ValueError) as err:
        merge_hermes(path, {"api": "u"}, default="coder")
    assert str(err.value) == f"Hermes's config {path}{says}"
    assert path.read_text() == text and not (tmp_path / "config.yaml.bak").exists()


def test_a_string_model_stays_when_no_default_is_asked_for(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("model: some/model\n")
    merge_hermes(path, {"api": "u"})
    assert yaml.safe_load(path.read_text()) == {"model": "some/model", "providers": {"spark": {"api": "u"}}}


def test_hermes_never_writes_its_env_file_and_flags_a_leftover_placeholder(capsys):
    # The command's first version wrote SPARK_API_KEY=your-llama-swap-key-here into Hermes's .env, which Hermes sent
    # as the key (a 401). Now the key comes only from the environment: the file is read for that one line, never
    # written, and only the verdict is said.
    env = Path(os.environ["HOME"]) / ".hermes" / ".env"
    env.parent.mkdir()
    env.write_text("OTHER=a-stand-in\nSPARK_API_KEY=your-llama-swap-key-here\n")
    registry = Path(__file__).parent / "fixtures" / "models.yaml"
    args = argparse.Namespace(registry=registry, base_url="u", key_env="SPARK_API_KEY", write=True, default=None)
    assert clients.run_hermes(args) == 0
    assert env.read_text() == "OTHER=a-stand-in\nSPARK_API_KEY=your-llama-swap-key-here\n"
    out = capsys.readouterr().out
    assert "in Hermes, /model custom:spark:coder picks a Spark model" in out
    assert "still has the line SPARK_API_KEY=your-llama-swap-key-here" in out and "a-stand-in" not in out
    env.write_text("OTHER=a-stand-in\n")
    assert clients.run_hermes(args) == 0
    assert "still has the line" not in capsys.readouterr().out and env.read_text() == "OTHER=a-stand-in\n"


def test_hermes_default_must_be_one_of_the_registrys_chat_models(capsys):
    registry = Path(__file__).parent / "fixtures" / "models.yaml"
    assert cli.main(["clients", "hermes", "--write", "--default", "embed", "--registry", str(registry)]) == 1
    assert capsys.readouterr().err == (f"spark clients: --default embed isn't a chat model in {registry}: "
                                       "vision-chat, coder\n")
    assert not (Path(os.environ["HOME"]) / ".hermes").exists()


def test_hermes_merge_sets_each_spark_models_reasoning_and_keeps_the_rest(tmp_path):
    # Hermes sends its configured effort as it is, and Qwen3.8's template refuses `max` with a 500 (2026-10-14).
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"agent": {"reasoning_effort": "max", "other": 1,
                                              "reasoning_overrides": {"openai/gpt-5": "low"}}}))
    merge_hermes(path, {"api": "u"}, reasoning={"coder": "xhigh"})
    assert yaml.safe_load(path.read_text())["agent"] == {
        "reasoning_effort": "max", "other": 1, "reasoning_overrides": {"openai/gpt-5": "low", "coder": "xhigh"}}


@pytest.mark.parametrize(("text", "says"), [
    ("agent: fast\n", ": its agent settings are a string, not the mapping Hermes reads"),
    ("agent:\n  reasoning_overrides: [coder]\n", ": its agent.reasoning_overrides are a list, not the mapping Hermes "
                                                  "reads")])
def test_agent_settings_of_the_wrong_shape_are_refused(tmp_path, text, says):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    with pytest.raises(ValueError) as err:
        merge_hermes(path, {"api": "u"}, reasoning={"coder": "xhigh"})
    assert str(err.value) == f"Hermes's config {path}{says}"
    assert path.read_text() == text and not (tmp_path / "config.yaml.bak").exists()


def test_hermes_write_sets_xhigh_for_the_chat_models_only(capsys):
    registry = Path(__file__).parent / "fixtures" / "models.yaml"
    args = argparse.Namespace(registry=registry, base_url="u", key_env="SPARK_API_KEY", write=True, default=None)
    assert clients.run_hermes(args) == 0
    data = yaml.safe_load((Path(os.environ["HOME"]) / ".hermes" / "config.yaml").read_text())
    assert data["agent"] == {"reasoning_overrides": {"vision-chat": "xhigh", "coder": "xhigh"}}
    assert "with reasoning effort xhigh for its models" in capsys.readouterr().out
