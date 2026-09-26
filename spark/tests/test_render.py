from pathlib import Path

import pytest
import yaml

from spark.registry import load_registry
from spark.render import COMPOSE_DIR, SPARK_BIN, UNIT_DIR, RenderError, installed_path, render
from spark.versions import load_versions

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]


def rendered(registry_path=FIX / "models.yaml"):
    return render(load_registry(registry_path), load_versions(FIX / "versions.yaml"),
                  Path(registry_path).read_text(), templates=ROOT / "stack/templates")


def test_every_model_starts_through_the_launch_check():
    cfg = yaml.safe_load(rendered()["llama-swap.yaml"])
    for name, model in cfg["models"].items():
        assert model["cmd"].startswith(f"{SPARK_BIN} launch {name} -- ")
        assert "--host 127.0.0.1" in model["cmd"] and "api-key" not in model["cmd"]
        assert model["proxy"] == "http://127.0.0.1:${PORT}" and model["ttl"] == 0


def test_the_group_never_evicts_and_holds_every_model():
    cfg = yaml.safe_load(rendered()["llama-swap.yaml"])
    group = cfg["routing"]["router"]["settings"]["groups"]["stack"]
    assert cfg["routing"]["router"]["use"] == "group"
    assert (group["swap"], group["exclusive"], group["persistent"]) == (False, False, True)
    assert group["members"] == sorted(cfg["models"])
    assert cfg["captureBuffer"] == 0


def test_api_keys_are_env_references_only():
    keys = yaml.safe_load(rendered()["llama-swap.yaml"])["apiKeys"]
    assert keys and all(k.startswith("${env.LLAMASWAP_KEY_") and k.endswith("}") for k in keys)


def test_compose_binds_locally_and_pins_images():
    compose = rendered()["compose/compose.yaml"]
    for needle in ("HOST: 127.0.0.1", "RAG_OPENAI_API_BASE_URL: http://127.0.0.1:9100/v1",
                   "AUDIO_STT_OPENAI_API_BASE_URL: http://127.0.0.1:9100/v1", "GRANIAN_HOST: 127.0.0.1",
                   "TASK_MODEL_EXTERNAL: vision-chat", "RAG_EMBEDDING_MODEL: embed", "AUDIO_STT_MODEL: stt"):
        assert needle in compose
    assert compose.count("@sha256:") == 2
    assert compose.count("driver: journald") == 2  # container logs readable without docker access


def test_llama_swap_listens_on_localhost_only():
    assert "-listen 127.0.0.1:9100" in rendered()["systemd/local-ai-llama-swap.service"]


def test_engines_and_downloads_cache_in_folders_bootstrap_gives_spark():
    # /var/lib/local-ai is spark's home but root's, so spark can't write a cache under $HOME. The
    # units that run engines or pull models name the spark-owned folders bootstrap creates.
    files = rendered()
    for unit in ("local-ai-llama-swap.service", "local-ai-pull.service"):
        text = files[f"systemd/{unit}"]
        assert "Environment=XDG_CACHE_HOME=/var/lib/local-ai/cache" in text, unit
        assert "Environment=CUDA_CACHE_PATH=/var/lib/local-ai/cuda-cache" in text, unit


def test_what_root_runs_has_a_root_owned_copy_and_the_rest_has_none():
    # Dan's decision (2026-09-25): the units and the Compose project that root runs are root's own
    # copies, which `make install-units` installs. `spark apply` deploys the rest itself.
    copies = {rel: installed_path(rel) for rel in rendered()}
    assert copies == {
        "llama-swap.yaml": None,
        "models.yaml": None,
        "compose/compose.yaml": "/etc/local-ai/compose/compose.yaml",
        "compose/searxng/settings.yml": "/etc/local-ai/compose/searxng/settings.yml",
        "systemd/local-ai-llama-swap.service": "/etc/systemd/system/local-ai-llama-swap.service",
        "systemd/local-ai-brake.service": "/etc/systemd/system/local-ai-brake.service",
        "systemd/local-ai-compose.service": "/etc/systemd/system/local-ai-compose.service",
        "systemd/local-ai-pull.service": "/etc/systemd/system/local-ai-pull.service",
    }
    assert (UNIT_DIR, COMPOSE_DIR) == ("/etc/systemd/system", "/etc/local-ai/compose")


def test_root_runs_compose_in_its_own_copy_of_the_project():
    # Compose reads every file in its project folder, a .env and an override file included, so root
    # runs it in root's copy, never in /opt/local-ai/etc, which Dan can write.
    unit = rendered()["systemd/local-ai-compose.service"]
    assert f"\nWorkingDirectory={COMPOSE_DIR}\n" in unit and "/opt/local-ai" not in unit


def test_a_set_that_breaks_the_budget_is_refused(tmp_path):
    data = yaml.safe_load((FIX / "models.yaml").read_text())
    data["models"]["coder"]["footprint_gib"] = 70
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(RenderError, match="budget"):
        rendered(path)


def test_the_real_registry_renders():
    registry = load_registry(ROOT / "stack/models.yaml")
    files = render(registry, load_versions(ROOT / "stack/versions.yaml"), (ROOT / "stack/models.yaml").read_text(),
                   templates=ROOT / "stack/templates")
    assert set(files) == set(rendered())  # the same eight files as the fixture renders
    assert set(yaml.safe_load(files["llama-swap.yaml"])["models"]) == set(registry.models)
