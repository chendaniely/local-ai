import re
import shlex
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


def real():
    """What the real registry and versions render: the safety checks run on it too, not only on the fixture."""
    return render(load_registry(ROOT / "stack/models.yaml"), load_versions(ROOT / "stack/versions.yaml"),
                  (ROOT / "stack/models.yaml").read_text(), templates=ROOT / "stack/templates")


@pytest.fixture(params=["fixture", "real"])
def files(request):
    return rendered() if request.param == "fixture" else real()


def registry_with(tmp_path, change) -> Path:
    """The fixture registry after `change` (a function of its data), in a file of its own."""
    data = yaml.safe_load((FIX / "models.yaml").read_text())
    change(data)
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def values(words: list[str], flag: str) -> list[str | None]:
    """Every value `words` give `flag`, as `flag value` or `flag=value`; None for a flag with nothing after it."""
    found = [words[i + 1] if i + 1 < len(words) else None for i, word in enumerate(words) if word == flag]
    return found + [word.split("=", 1)[1] for word in words if word.startswith(flag + "=")]


def test_every_model_starts_through_the_launch_check(files):
    cfg = yaml.safe_load(files["llama-swap.yaml"])
    for name, model in cfg["models"].items():
        words = shlex.split(model["cmd"])  # as llama-swap splits it: POSIX rules, quotes and backslashes included
        assert words[:4] == [SPARK_BIN, "launch", name, "--"]
        # An engine takes the last value a flag is given, so each of these appears once, with render's value.
        assert values(words, "--host") == ["127.0.0.1"], name
        assert values(words, "--port") == ["${PORT}"], name
        # No key, and nothing for llama-swap to fill in but the port: GET /running shows every command.
        assert not [w for w in words if "api-key" in w.replace("_", "-") or "${" in w.replace("${PORT}", "")], name
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


def test_compose_binds_locally_and_pins_images(files):
    services = yaml.safe_load(files["compose/compose.yaml"])["services"]
    webui, searxng = services["open-webui"]["environment"], services["searxng"]["environment"]
    # Exact values, not substrings: "HOST: 127.0.0.1" is also inside "GRANIAN_HOST: 127.0.0.1". Open WebUI
    # v0.11.4 listens on 0.0.0.0:8080 unless told otherwise, and host networking would put that on every interface.
    assert (webui.get("HOST"), webui.get("PORT")) == ("127.0.0.1", "3000")
    assert (searxng.get("GRANIAN_HOST"), searxng.get("GRANIAN_PORT")) == ("127.0.0.1", "8888")
    for key in ("OPENAI_API_BASE_URLS", "RAG_OPENAI_API_BASE_URL", "AUDIO_STT_OPENAI_API_BASE_URL"):
        assert webui.get(key) == "http://127.0.0.1:9100/v1", key
    assert webui.get("SEARXNG_QUERY_URL") == "http://127.0.0.1:8888/search"
    for service in services.values():
        assert re.fullmatch(r"[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}", service["image"]), service["image"]
        assert service["logging"] == {"driver": "journald"}  # container logs readable without docker access


def test_open_webui_is_given_the_registrys_task_embedding_and_stt_models():
    webui = yaml.safe_load(rendered()["compose/compose.yaml"])["services"]["open-webui"]["environment"]
    assert (webui["TASK_MODEL_EXTERNAL"], webui["RAG_EMBEDDING_MODEL"], webui["AUDIO_STT_MODEL"]) == (
        "vision-chat", "embed", "stt")


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


# Task 6's fix round 1: a registry edit can't rebind an engine, or put a key where llama-swap shows it.

OWNED = [  # (the model, the args an edit adds to it)
    pytest.param("coder", ["--host", "0.0.0.0"], id="llama.cpp --host"),
    pytest.param("coder", ["--host=0.0.0.0"], id="llama.cpp --host=value"),
    pytest.param("coder", ["--port", "8080"], id="llama.cpp --port"),
    pytest.param("coder", ["-m", "/tmp/other.gguf"], id="llama.cpp -m"),
    pytest.param("coder", ["--model", "/tmp/other.gguf"], id="llama.cpp --model"),
    pytest.param("vision-chat", ["-mm", "/tmp/other.gguf"], id="llama.cpp -mm"),
    pytest.param("vision-chat", ["--mmproj", "/tmp/other.gguf"], id="llama.cpp --mmproj"),
    pytest.param("coder", ["--api-key", "not-a-real-key"], id="llama.cpp --api-key"),
    pytest.param("coder", ["--api_key", "not-a-real-key"], id="llama.cpp --api_key"),  # llama-server reads _ as -
    pytest.param("coder", ["--api-key=not-a-real-key"], id="llama.cpp --api-key=value"),
    pytest.param("coder", ["--api-key-file", "/tmp/keys"], id="llama.cpp --api-key-file"),
    pytest.param("coder", ["--hf-token", "not-a-real-token"], id="llama.cpp --hf-token"),
    pytest.param("coder", ["-hft", "not-a-real-token"], id="llama.cpp -hft"),
    pytest.param("stt", ["--host", "0.0.0.0"], id="whisper.cpp --host"),
    pytest.param("stt", ["--port", "8080"], id="whisper.cpp --port"),
    pytest.param("stt", ["-m", "/tmp/other.bin"], id="whisper.cpp -m"),
    pytest.param("stt", ["--model", "/tmp/other.bin"], id="whisper.cpp --model"),
]


@pytest.mark.parametrize("model, args", OWNED)
def test_args_may_not_set_a_flag_render_owns(tmp_path, model, args):
    # An engine takes the last value a flag is given, and the registry's args come after render's own. The refusal
    # names the flag as written, never the value given to it: that could be a key.
    path = registry_with(tmp_path, lambda d: d["models"][model]["args"].extend(args))
    flag, value = args[0].split("=")[0], args[-1].split("=")[-1]
    with pytest.raises(RenderError, match=f"^{re.escape(model)}: args may not set {re.escape(flag)}: ") as err:
        rendered(path)
    assert value not in str(err.value)


def test_a_flag_that_only_starts_like_an_owned_one_is_left_alone(tmp_path):
    path = registry_with(tmp_path, lambda d: d["models"]["vision-chat"]["args"].append("--mmproj-offload"))
    assert "--mmproj-offload" in yaml.safe_load(rendered(path)["llama-swap.yaml"])["models"]["vision-chat"]["cmd"]


FILLED_IN = [  # (the edit, the model it lands in, what llama-swap would fill in)
    pytest.param(lambda d: d["models"]["coder"]["args"].extend(["--alias", "${env.LLAMASWAP_KEY_SPARK}"]),
                 "coder", "${env.LLAMASWAP_KEY_SPARK}", id="an arg"),
    pytest.param(lambda d: d["models"]["stt"]["source"].update(file="${env.LLAMASWAP_KEY_AGENT}.bin"),
                 "stt", "${env.LLAMASWAP_KEY_AGENT}", id="a source file"),
    pytest.param(lambda d: d["engines"].update({"whisper.cpp": "/opt/${MODEL_ID}/whisper-server"}),
                 "stt", "${MODEL_ID}", id="an engine's path"),
]


@pytest.mark.parametrize("change, model, word", FILLED_IN)
def test_nothing_in_a_command_is_filled_in_but_the_port(tmp_path, change, model, word):
    # llama-swap v257 fills in ${env.…} anywhere in its config, and GET /running shows every command whole: a key
    # referenced here would show there, and in the engine's argv.
    with pytest.raises(RenderError, match=f"^{re.escape(model)}: .*{re.escape(word)}"):
        rendered(registry_with(tmp_path, change))


def test_a_role_is_never_filled_in(tmp_path):
    path = registry_with(tmp_path, lambda d: d["models"]["coder"].update(roles=["${env.LLAMASWAP_KEY_SPARK}"]))
    with pytest.raises(RenderError, match=r"^coder: role '\$\{env\.LLAMASWAP_KEY_SPARK\}'"):
        rendered(path)


SPLIT = [  # (the edit, the model it lands in): each word would reach the engine as other words
    pytest.param(lambda d: d["models"]["coder"]["args"].extend(["--ho\\st", "0.0.0.0"]), "coder",
                 id="a backslash"),  # --ho\st is --host once llama-swap unescapes it
    pytest.param(lambda d: d["models"]["stt"]["source"].update(file="whisper.bin --host 0.0.0.0"), "stt",
                 id="whitespace"),
    pytest.param(lambda d: d["models"]["vision-chat"]["source"].update(mmproj='vision-"mmproj".gguf'), "vision-chat",
                 id="quotes"),
]


@pytest.mark.parametrize("change, model", SPLIT)
def test_a_word_llama_swap_would_split_or_unescape_is_refused(tmp_path, change, model):
    # llama-swap splits a command as a POSIX shell does, so the checks above only hold if every word stays one.
    with pytest.raises(RenderError, match=f"^{re.escape(model)}: .* wouldn't reach the engine as one word"):
        rendered(registry_with(tmp_path, change))
