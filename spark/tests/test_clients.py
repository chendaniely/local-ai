import argparse
import json
from pathlib import Path

from spark import clients
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
