import argparse
import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from spark import cli, clients
from spark.clients import (hermes_provider, merge_hermes, merge_opencode, merge_pi, merge_posit, opencode_provider,
                           pi_provider, posit_provider)
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
    assert clients.run_pi(argparse.Namespace(registry=registry, base_url="u", key_env="K", write=True,
                                             http_idle_timeout_ms=None)) == 0
    assert "(there was no previous file)" in capsys.readouterr().out


def test_the_models_file_is_found_when_the_command_runs_not_at_import(tmp_path, monkeypatch, capsys):
    # Task 9's rulings, R10. The tests import clients when they're collected, before conftest gives each a HOME of its
    # own: a path fixed then is the real ~/.pi of whoever runs them, and a test that forgot to patch it wrote there.
    written = []
    monkeypatch.setattr(clients, "merge_pi", lambda path, provider: written.append(path))  # writes nothing, anywhere
    monkeypatch.setenv("HOME", str(tmp_path))
    registry = Path(__file__).parent / "fixtures" / "models.yaml"
    assert clients.run_pi(argparse.Namespace(registry=registry, base_url="u", key_env="K", write=True,
                                             http_idle_timeout_ms=None)) == 0
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


# Phase 2a's Task 34: agent's pi waits 15 minutes for a response, since agent's key waits up to 10 for its model.
# `--http-idle-timeout-ms` merges pi's httpIdleTimeoutMs into ~/.pi/agent/settings.json, a user's own pi config, so
# each test runs the command under conftest's empty HOME, never the real one.

REGISTRY = Path(__file__).parent / "fixtures" / "models.yaml"
STAND_IN = "a-stand-in-value"  # a setting's value that must never reach the output


def _pi(*flags: str) -> int:
    return cli.main(["clients", "pi", "--write", "--registry", str(REGISTRY), *flags])


def _agent_dir() -> Path:
    return Path(os.environ["HOME"]) / ".pi" / "agent"


def _settings_dir_with(data: bytes) -> Path:
    """pi's settings file holding `data`, in the test's HOME; its folder empty but for it."""
    folder = _agent_dir()
    folder.mkdir(parents=True)
    (folder / "settings.json").write_bytes(data)
    return folder / "settings.json"


def _nothing_written(settings: Path, before: bytes | None) -> None:
    """Neither of pi's files written, no backup made, no temporary file left."""
    assert not (settings.parent / "models.json").exists()
    assert not (settings.parent / "settings.json.bak").exists()
    if before is not None:
        assert settings.read_bytes() == before
    assert sorted(p.name for p in settings.parent.iterdir()) == (["settings.json"] if before is not None else [])


def test_agents_settings_get_the_15_minute_idle_timeout(capsys):
    umask = os.umask(0o022)  # a new file gets what an ordinary write gives it: 0644
    try:
        assert _pi("--http-idle-timeout-ms", "900000") == 0
    finally:
        os.umask(umask)
    settings = _agent_dir() / "settings.json"
    assert json.loads(settings.read_text()) == {"httpIdleTimeoutMs": 900000}
    assert stat.S_IMODE(settings.stat().st_mode) == 0o644
    assert json.loads((_agent_dir() / "models.json").read_text())["providers"]["spark"]["models"]  # and the provider
    assert not (_agent_dir() / "settings.json.bak").exists()
    out = capsys.readouterr().out
    assert out.endswith(f"clients: wrote httpIdleTimeoutMs 900000 to {settings} (there was no previous file)\n")


def test_other_settings_are_kept_and_backed_up(capsys):
    old = b'{"theme": "dark"}'
    settings = _settings_dir_with(old)
    settings.chmod(0o600)
    backup = settings.with_name("settings.json.bak")
    backup.write_text("{}")  # an earlier run's, looser: replaced, at the file's mode
    backup.chmod(0o644)
    umask = os.umask(0o022)
    try:
        assert _pi("--http-idle-timeout-ms", "900000") == 0
    finally:
        os.umask(umask)
    assert json.loads(settings.read_text()) == {"theme": "dark", "httpIdleTimeoutMs": 900000}
    assert backup.read_bytes() == old
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600 == stat.S_IMODE(settings.stat().st_mode)
    assert f"to {settings} (the previous file is {backup})\n" in capsys.readouterr().out


def test_an_earlier_timeout_is_replaced_where_it_stood_and_no_setting_is_printed(capsys):
    old = {"httpIdleTimeoutMs": 300000, "compaction": {"reserveTokens": 16384}, "shellPath": STAND_IN}
    settings = _settings_dir_with(json.dumps(old).encode())
    assert _pi("--http-idle-timeout-ms", "900000") == 0
    new = json.loads(settings.read_text())
    assert new == {**old, "httpIdleTimeoutMs": 900000} and list(new) == list(old)
    out, err = capsys.readouterr()
    assert STAND_IN not in out + err


def test_without_the_flag_settings_are_untouched(capsys):
    old = b'{"theme": "dark"}'
    settings = _settings_dir_with(old)
    os.utime(settings, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))  # 2001: a rewrite would show
    before = settings.stat()
    assert _pi() == 0
    after = settings.stat()
    assert settings.read_bytes() == old
    assert (after.st_ino, after.st_mtime_ns, after.st_mode) == (before.st_ino, before.st_mtime_ns, before.st_mode)
    assert not settings.with_name("settings.json.bak").exists()
    assert (_agent_dir() / "models.json").exists()  # the provider still goes in
    assert "settings" not in capsys.readouterr().out


def test_a_settings_file_that_isnt_an_object_is_refused(capsys):
    settings = _settings_dir_with(b"[]")
    assert _pi("--http-idle-timeout-ms", "900000") == 1
    assert capsys.readouterr().err == (f"spark clients: pi's settings file {settings} holds an array at its top level, "
                                       "not the object pi reads\n")
    _nothing_written(settings, b"[]")  # models.json neither: the refusal comes before anything is written


SETTINGS_BAD = {"not JSON": b'{"shellPath": "' + STAND_IN.encode() + b'",',
                "NaN, which pi's JSON.parse refuses": b'{"x": NaN}',
                "a number JSON can't hold, which Python reads as infinity": b'{"x": 1e400}',
                "not UTF-8": b'{"theme": "\xff"}',
                "a byte-order mark alone": b"\xef\xbb\xbf",
                "nested too deep": b"[" * 100_000 + b"]" * 100_000}
WHY = {"NaN, which pi's JSON.parse refuses": "NaN isn't JSON",
       # read, but its write-back would hold Infinity, which pi can't read
       "a number JSON can't hold, which Python reads as infinity": "Out of range float values are not JSON compliant"}


@pytest.mark.parametrize("bad", list(SETTINGS_BAD))
def test_a_settings_file_that_wont_load_is_refused_by_name(bad, capsys):
    settings = _settings_dir_with(SETTINGS_BAD[bad])
    with pytest.raises(clients.ClientsError) as err:
        clients.plan_settings(settings, {"httpIdleTimeoutMs": 900000})
    assert str(err.value).startswith(f"pi's settings file {settings} won't load: {WHY.get(bad, '')}")
    assert STAND_IN not in str(err.value)
    # json's error and the codec's keep the file's text: none is chained to the refusal.
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert _pi("--http-idle-timeout-ms", "900000") == 1
    assert STAND_IN not in capsys.readouterr().err
    _nothing_written(settings, SETTINGS_BAD[bad])


@pytest.mark.parametrize("kind", ["a folder", "a FIFO"])
def test_a_settings_path_that_isnt_a_file_is_refused(kind, capsys):
    folder = _agent_dir()
    folder.mkdir(parents=True)
    settings = folder / "settings.json"
    settings.mkdir() if kind == "a folder" else os.mkfifo(settings)  # a FIFO would hang a plain open: no writer
    assert _pi("--http-idle-timeout-ms", "900000") == 1
    assert capsys.readouterr().err == (f"spark clients: pi's settings file {settings} isn't a regular file, so nothing "
                                       "was written\n")
    assert not (folder / "models.json").exists() and not (folder / "settings.json.bak").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file of mode 000")
def test_a_settings_file_it_cant_read_is_refused(capsys):
    settings = _settings_dir_with(b'{"theme": "dark"}')
    settings.chmod(0o000)
    try:
        assert _pi("--http-idle-timeout-ms", "900000") == 1
    finally:
        settings.chmod(0o600)
    assert capsys.readouterr().err == (f"spark clients: pi's settings file {settings} won't load: Permission denied\n")
    _nothing_written(settings, b'{"theme": "dark"}')


@pytest.mark.parametrize("target", ["a file", "nothing"])
def test_a_settings_file_that_is_a_link_is_refused(tmp_path, target, capsys):
    # Never written through: the link's target could be anything of this account's. Nor replaced: a dotfiles repo
    # keeps its file and links it in, and a rename over the link would quietly cut it loose.
    kept = tmp_path / "dotfiles" / "settings.json"
    kept.parent.mkdir()
    if target == "a file":
        kept.write_text('{"theme": "dark"}')
    folder = _agent_dir()
    folder.mkdir(parents=True)
    settings = folder / "settings.json"
    settings.symlink_to(kept)
    assert _pi("--http-idle-timeout-ms", "900000") == 1
    assert capsys.readouterr().err == (f"spark clients: pi's settings file {settings} is a link, and spark clients "
                                       "doesn't write through one, so nothing was written\n")
    assert settings.is_symlink() and os.readlink(settings) == str(kept)
    assert kept.read_text() == '{"theme": "dark"}' if target == "a file" else not kept.exists()
    assert sorted(p.name for p in folder.iterdir()) == ["settings.json"]


def test_settings_are_written_whole_never_in_place():
    settings = _settings_dir_with(b'{"theme": "dark"}')
    before = settings.stat().st_ino
    assert _pi("--http-idle-timeout-ms", "900000") == 0
    assert settings.stat().st_ino != before  # a new file renamed over the old: pi reads one or the other, whole
    assert sorted(p.name for p in settings.parent.iterdir()) == ["models.json", "settings.json", "settings.json.bak"]


def test_a_write_that_fails_leaves_the_old_file_and_no_temporary_one(monkeypatch, capsys):
    settings = _settings_dir_with(b'{"theme": "dark"}')
    real = os.replace

    def no_room(src, dst):
        if Path(dst).name == "settings.json":
            raise OSError(28, "No space left on device")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", no_room)
    assert _pi("--http-idle-timeout-ms", "900000") == 1
    assert capsys.readouterr().err == "spark clients: [Errno 28] No space left on device\n"
    assert settings.read_bytes() == b'{"theme": "dark"}'
    assert sorted(p.name for p in settings.parent.iterdir()) == ["models.json", "settings.json", "settings.json.bak"]


def test_a_backup_at_a_link_is_replaced_not_written_through(tmp_path):
    settings = _settings_dir_with(b'{"theme": "dark"}')
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_text("not pi's")
    settings.with_name("settings.json.bak").symlink_to(elsewhere)
    assert _pi("--http-idle-timeout-ms", "900000") == 0
    backup = settings.with_name("settings.json.bak")
    assert not backup.is_symlink() and backup.read_bytes() == b'{"theme": "dark"}'
    assert elsewhere.read_text() == "not pi's"


@pytest.mark.parametrize(("old", "kept"), [(b"", {}), (b'\xef\xbb\xbf{"theme": "dark"}', {"theme": "dark"})])
def test_a_settings_file_pi_reads_is_read_as_pi_reads_it(old, kept):
    # pi 0.85.1 reads an empty settings file as no settings, and strips a byte-order mark before it parses.
    settings = _settings_dir_with(old)
    assert _pi("--http-idle-timeout-ms", "900000") == 0
    assert json.loads(settings.read_text()) == {**kept, "httpIdleTimeoutMs": 900000}
    assert settings.with_name("settings.json.bak").read_bytes() == old


@pytest.mark.parametrize("bad", ["-1", "15m", "1.5", ""])
def test_the_timeout_is_a_whole_number_of_milliseconds(bad, capsys):
    with pytest.raises(SystemExit) as stop:
        _pi("--http-idle-timeout-ms", bad)
    assert stop.value.code == 2
    assert f"argument --http-idle-timeout-ms: {bad!r} isn't a whole number of milliseconds, 0 or more" in (
        capsys.readouterr().err)
    assert not _agent_dir().exists()


def test_the_flag_needs_write(capsys):
    assert cli.main(["clients", "pi", "--registry", str(REGISTRY), "--http-idle-timeout-ms", "900000"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == ("spark clients: --http-idle-timeout-ms writes pi's settings file, so it goes with "
                                 "--write; nothing was written\n")
    assert not _agent_dir().exists()


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


# Posit Assistant's providers.json gets the same care as pi's and OpenCode's files: it holds other providers'
# settings. Its key never goes in it, though — providers.json holds no credential: OPENAI_COMPATIBLE_API_KEY for the
# TUI, Positron's credential store for Positron.

def test_posit_provider_lists_the_chat_models_with_their_windows_and_holds_no_key():
    p = posit_provider(REG, "http://127.0.0.1:9100/v1")
    assert p["baseUrl"] == "http://127.0.0.1:9100/v1" and p["enabled"] is True
    assert "apiKey" not in json.dumps(p) and "key" not in json.dumps(p).lower().replace("openai", "")
    assert p["models"]["allow"] == ["vision-chat", "coder"]  # no embeddings or speech models in its picker
    overrides = p["models"]["overrides"]
    assert overrides["vision-chat"] == {"name": "the vision model", "maxContextLength": 32768, "supportsTools": True,
                                        "supportsImages": True, "supportsToolResultImages": False,
                                        "supportsWebSearch": False}
    assert overrides["coder"] == {"name": "the coder", "maxContextLength": 131072, "supportsTools": True,
                                  "supportsImages": False, "supportsToolResultImages": False,
                                  "supportsWebSearch": False}


def test_posit_merge_keeps_other_providers_and_replaces_its_own_and_backs_up(tmp_path):
    path = tmp_path / "providers.json"
    old = {"$schema": "https://assistant.posit.co/schemas/providers.schema.json", "version": 1,
           "providers": {"openai": {"enabled": True},
                         "openai-compatible": {"enabled": True, "baseUrl": "http://old"},
                         "custom": {"acme": {"type": "openai-compatible", "baseUrl": "https://a/v1"}}}}
    path.write_text(json.dumps(old))
    path.chmod(0o600)
    backup = merge_posit(path, {"enabled": True, "baseUrl": "u", "models": {}})
    data = json.loads(path.read_text())
    assert data["$schema"] == old["$schema"] and data["version"] == 1
    assert data["providers"]["openai"] == {"enabled": True} and data["providers"]["custom"] == old["providers"]["custom"]
    assert data["providers"]["openai-compatible"] == {"enabled": True, "baseUrl": "u", "models": {}}  # replaced whole
    assert json.loads(backup.read_text()) == old and stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_a_first_posit_write_starts_the_file_with_its_schema_and_version(tmp_path):
    path = tmp_path / ".posit" / "ai" / "providers.json"
    assert merge_posit(path, {"baseUrl": "u"}) is None
    assert json.loads(path.read_text()) == {"$schema": "https://assistant.posit.co/schemas/providers.schema.json",
                                            "version": 1,
                                            "providers": {"openai-compatible": {"baseUrl": "u"}}}


@pytest.mark.parametrize("bad", [*BAD, "a folder"])
def test_a_posit_file_that_wont_load_is_refused_by_name(tmp_path, bad):
    # json's own message names no file. Nothing is backed up or written.
    path = tmp_path / "providers.json"
    if bad == "a folder":
        path.mkdir()
    else:
        path.write_bytes(BAD[bad])
    with pytest.raises(ValueError) as err:
        merge_posit(path, {"baseUrl": "u"})
    assert str(err.value).startswith(f"Posit Assistant's providers file {path} won't load: ")
    # json's error and the codec's keep the file's text, other providers' settings included: none is chained to it.
    assert err.value.__cause__ is None and err.value.__context__ is None
    assert path.is_dir() if bad == "a folder" else path.read_bytes() == BAD[bad]
    assert not (tmp_path / "providers.json.bak").exists()


@pytest.mark.parametrize(("text", "kind"), [("[]", "an array"), ('"spark"', "a string"), ("3", "a number"),
                                            ("true", "true or false"), ("null", "null")])
def test_a_posit_top_level_that_isnt_an_object_is_refused_not_a_traceback(tmp_path, text, kind):
    path = tmp_path / "providers.json"
    path.write_text(text)
    with pytest.raises(ValueError) as err:
        merge_posit(path, {"baseUrl": "u"})
    assert str(err.value) == (f"Posit Assistant's providers file {path} holds {kind} at its top level, not the object "
                              "Posit Assistant reads")
    assert path.read_text() == text and not (tmp_path / "providers.json.bak").exists()


def test_posit_providers_that_arent_an_object_are_refused_too(tmp_path):
    path = tmp_path / "providers.json"
    path.write_text('{"providers": ["spark"]}')
    with pytest.raises(ValueError) as err:
        merge_posit(path, {"baseUrl": "u"})
    assert str(err.value) == (f"Posit Assistant's providers file {path}: its providers are an array, not the object "
                              "Posit Assistant reads")
    assert path.read_text() == '{"providers": ["spark"]}' and not (tmp_path / "providers.json.bak").exists()


def test_the_posit_cli_writes_its_file_from_the_registry_and_names_the_key_never_its_value(capsys):
    assert cli.main(["clients", "posit", "--write", "--registry", str(REGISTRY)]) == 0
    path = Path(os.environ["HOME"]) / ".posit" / "ai" / "providers.json"
    data = json.loads(path.read_text())
    assert data["providers"]["openai-compatible"]["models"]["allow"] == ["vision-chat", "coder"]
    assert data["providers"]["openai-compatible"]["baseUrl"] == "http://127.0.0.1:9100/v1"
    out = capsys.readouterr().out
    assert f"to {path} (there was no previous file)" in out
    assert "OPENAI_COMPATIBLE_API_KEY" in out  # the key's name is said, its value never is


def test_the_posit_cli_says_a_refusal_in_one_line(tmp_path, monkeypatch, capsys):
    path = tmp_path / "providers.json"
    path.write_text("[]")
    monkeypatch.setattr(clients, "POSIT_PROVIDERS", path)
    assert cli.main(["clients", "posit", "--write", "--registry", str(REGISTRY)]) == 1
    assert capsys.readouterr().err == (f"spark clients: Posit Assistant's providers file {path} holds an array at its "
                                       "top level, not the object Posit Assistant reads\n")


def test_posits_providers_file_is_found_when_the_command_runs_not_at_import(tmp_path, monkeypatch, capsys):
    # As for pi's models file (Task 9's rulings, R10): the tests import clients at collection, before conftest gives
    # each test a HOME of its own: a path fixed at import is the real ~/.posit of whoever runs them.
    written = []
    monkeypatch.setattr(clients, "merge_posit", lambda path, provider: written.append(path))  # writes nothing
    monkeypatch.setenv("HOME", str(tmp_path))
    registry = Path(__file__).parent / "fixtures" / "models.yaml"
    assert clients.run_posit(argparse.Namespace(registry=registry, base_url="u", write=True)) == 0
    assert written == [tmp_path / ".posit/ai/providers.json"]
    assert f"to {tmp_path / '.posit/ai/providers.json'} (there was no previous file)" in capsys.readouterr().out
