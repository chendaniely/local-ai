import json
import os
import re
import shlex
from pathlib import Path

import pytest
import yaml

import spark.render
from spark import cli
from spark.registry import load_registry
from spark.render import (COMPOSE_DIR, HF_HOME, SPARK_BIN, UNIT_DIR, RenderError, engine_cmd, installed_path, render,
                          write_tree)
from spark.versions import load_versions

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]


def rendered(registry_path=FIX / "models.yaml", versions_path=FIX / "versions.yaml"):
    return render(load_registry(registry_path), load_versions(versions_path),
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


def versions_with(tmp_path, change) -> Path:
    """The fixture versions after `change` (a function of its components), in a file of its own."""
    data = yaml.safe_load((FIX / "versions.yaml").read_text())
    change(data["components"])
    path = tmp_path / "versions.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def tree(out: Path) -> dict[str, str]:
    """Every file under `out`, by its path relative to `out`, as render names them."""
    return {path.relative_to(out).as_posix(): path.read_text() for path in out.rglob("*") if path.is_file()}


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


def test_open_webui_task_calls_run_without_thinking():
    # Titles, tags and search queries go to the 'small' model, which thinks by default: thousands of tokens
    # before a five-word title. Open WebUI v0.11.4 adds TASK_MODEL_PARAMS' keys to every task request, and
    # setting it replaces the title task's own 1000-token cap, so the cap comes along. Chats keep thinking.
    webui = yaml.safe_load(rendered()["compose/compose.yaml"])["services"]["open-webui"]["environment"]
    assert json.loads(webui["TASK_MODEL_PARAMS"]) == {
        "chat_template_kwargs": {"enable_thinking": False}, "max_tokens": 1000}


# Open WebUI v0.11.4's lockdown (Phase 1 council, security I2): each setting with the value that keeps it shut. S09's
# and S20's guarantees rest on these, and a template edit that opened one would pass every other test.
LOCKDOWN = {
    "ENABLE_SIGNUP": "false",  # no account after the first, Dan's (the admin)
    "ENABLE_CODE_EXECUTION": "false",  # no code run from a chat
    "ENABLE_CODE_INTERPRETER": "false",
    "ENABLE_DIRECT_CONNECTIONS": "false",  # no model endpoint of a user's own, around llama-swap's keys
    "ENABLE_OLLAMA_API": "false",  # llama-swap is the only backend
    "ENABLE_PERSISTENT_CONFIG": "false",  # these values, not ones saved from the admin panel, apply at every start
}


def test_open_webuis_lockdown_keeps_its_values(files):
    webui = yaml.safe_load(files["compose/compose.yaml"])["services"]["open-webui"]["environment"]
    assert {name: webui.get(name) for name in LOCKDOWN} == LOCKDOWN


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


def test_the_pull_unit_downloads_where_the_engines_read_and_sends_no_telemetry():
    # `spark models pull` puts each file where model_path says, under render's HF_HOME, and the library keeps its own
    # files (a token file, its Xet cache) under the unit's HF_HOME: the two are one folder. HF_HUB_DISABLE_TELEMETRY
    # stops the library's daily agent-registry request to the Hub (Task 8's review).
    service = rendered()["systemd/local-ai-pull.service"].splitlines()
    for line in (f"Environment=HF_HOME={HF_HOME}", "Environment=HF_HUB_DISABLE_PROGRESS_BARS=1",
                 "Environment=HF_HUB_DISABLE_TELEMETRY=1", f"ExecStart={SPARK_BIN} models pull"):
        assert line in service, line


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


# Dan's decision (2026-09-28, from Phase 1's council, security D1): a registry's args may set only the engine options on
# render's list. Before it, render refused only what it knew to refuse, and a flag that only started like an owned one,
# such as --mmproj-offload, went through.

UNLISTED = "render allows only the engine options on its list"


@pytest.mark.parametrize("model, args", [
    pytest.param("vision-chat", ["--mmproj-offload"], id="only starts like an owned flag"),
    pytest.param("coder", ["--path", "/etc/local-ai/secrets"], id="--path"),  # serves a folder, unkeyed
    pytest.param("coder", ["--agent"], id="--agent"),  # every built-in tool, a shell command's included
    pytest.param("coder", ["--media-path", "/var/lib/local-ai"], id="--media-path"),  # reads files from a folder
    pytest.param("coder", ["--path=/etc/local-ai/secrets"], id="--path=value"),
    pytest.param("stt", ["--public", "/etc/local-ai/secrets"], id="whisper.cpp --public"),
    pytest.param("stt", ["--load-mode", "none"], id="a llama.cpp option, given to whisper.cpp"),
])
def test_args_may_set_only_a_listed_option(tmp_path, model, args):
    # Refused, naming the flag as written and never its value, and saying how a flag gets on the list.
    path = registry_with(tmp_path, lambda d: d["models"][model]["args"].extend(args))
    flag = args[0].split("=")[0]
    with pytest.raises(RenderError, match=f"^{re.escape(model)}: args may not set {re.escape(flag)}: "
                                          f"{UNLISTED}.*check what it does") as err:
        rendered(path)
    assert "secrets" not in str(err.value) and "/var/lib" not in str(err.value)


@pytest.mark.parametrize("model, flag, reason", [
    ("coder", "--host", "binds every engine to 127.0.0.1"), ("coder", "--api-key", "an engine takes no key"),
    ("coder", "--hf-repo", "pinned revision"), ("coder", "--ctx-size", "render sets it"),
    ("vision-chat", "--kv-unified-per-slot", "share its whole context"), ("stt", "--port", "binds every engine"),
])
def test_a_refused_flag_keeps_its_own_reason(tmp_path, model, flag, reason):
    # The refusals are checked before the list, so a flag that is refused for a reason says that reason.
    path = registry_with(tmp_path, lambda d: d["models"][model]["args"].extend([flag, "1"]))
    with pytest.raises(RenderError, match=f"^{re.escape(model)}: args may not set {re.escape(flag)}: ") as err:
        rendered(path)
    assert reason in str(err.value) and UNLISTED not in str(err.value)


def test_every_spelling_of_a_listed_option_is_taken(tmp_path):
    # Each spelling b11146's and v1.9.4's --help give, on a model of that engine.
    for engine, model in (("llama.cpp", "coder"), ("whisper.cpp", "stt")):
        for group in spark.render.ALLOWED[engine]:
            for spelling in group:
                path = registry_with(tmp_path, lambda d: d["models"][model]["args"].extend([spelling, "1"]))
                cmd = yaml.safe_load(rendered(path)["llama-swap.yaml"])["models"][model]["cmd"].split()
                assert cmd[-2:] == [spelling, "1"], (engine, spelling)


@pytest.mark.parametrize("path", [FIX / "models.yaml", ROOT / "stack/models.yaml"], ids=["fixture", "real"])
def test_every_option_the_registry_sets_is_on_the_list(path):
    # So adding a flag to a registry fails here, as well as in render, until the flag is on the list.
    registry = load_registry(path)
    listed = {engine: {spelling for group in groups for spelling in group}
              for engine, groups in spark.render.ALLOWED.items()}
    for name, model in registry.models.items():
        engine = "whisper.cpp" if model.engine == "whisper.cpp" else "llama.cpp"
        for word in model.args:
            if word.startswith("-"):
                assert word.split("=", 1)[0] in listed[engine], (name, word)


DOWNLOADS = [  # every option llama-server b11146 takes that fetches weights at start (common/arg.cpp), all spellings
    "-hf", "-hfr", "--hf-repo", "-hff", "--hf-file", "--spec-draft-hf", "-hfd", "-hfrd", "--hf-repo-draft",
    "-mu", "--model-url", "-mmu", "--mmproj-url", "-dr", "--docker-repo",
    "--mmproj-auto", "--no-mmproj", "--no-mmproj-auto",  # whether -hf also fetches a projector
    "--embd-gemma-default", "--fim-qwen-1.5b-default", "--fim-qwen-3b-default", "--fim-qwen-7b-default",
    "--fim-qwen-7b-spec", "--fim-qwen-14b-spec", "--fim-qwen-30b-default", "--gpt-oss-20b-default",
    "--gpt-oss-120b-default", "--vision-gemma-4b-default", "--vision-gemma-12b-default",
]


@pytest.mark.parametrize("flag", [*DOWNLOADS, "--hf_repo", "--hf-repo=example-org/other-GGUF"])
def test_args_may_not_download_a_model(tmp_path, flag):
    # A download bypasses the registry's pinned revision (Review Focus 4), whatever the source.
    path = registry_with(tmp_path, lambda d: d["models"]["coder"]["args"].append(flag))
    refusal = f"^coder: args may not set {re.escape(flag.split('=')[0])}: .*pinned revision"
    with pytest.raises(RenderError, match=refusal):
        rendered(path)


SETTINGS = [  # (the model, a spelling of a flag render passes it), from each engine's own option table
    *[("coder", flag) for flag in ("-c", "--ctx-size", "--ctx_size", "-np", "--parallel", "-ngl", "--gpu-layers",
                                   "--n-gpu-layers", "--n_gpu_layers", "-cram", "--cache-ram", "--cache-ram=0")],
    ("embed", "--embedding"), ("embed", "--embeddings"),
    ("stt", "--inference-path"), ("coder", "--offline"),
]


@pytest.mark.parametrize("model, flag", SETTINGS)
def test_args_may_not_override_a_setting_render_passes(tmp_path, model, flag):
    # An engine takes the last value it's given: the model would run with more context, slots or cache than the
    # footprint it was admitted with assumes.
    path = registry_with(tmp_path, lambda d: d["models"][model]["args"].extend([flag, "1"]))
    with pytest.raises(RenderError, match=f"^{re.escape(model)}: args may not set {re.escape(flag.split('=')[0])}: "
                                          "render sets it"):
        rendered(path)


def test_every_flag_render_passes_is_refused_in_every_spelling(tmp_path):
    # The refusals follow what engine_cmd passes, so they can't drift: a flag it starts passing is refused with no list
    # to update, and a test fails until its spellings are known. The fixture's models take every branch: a projector,
    # embeddings, whisper and plain chat.
    registry = load_registry(FIX / "models.yaml")
    for name, model in registry.models.items():
        cmd = engine_cmd(model, registry)
        own = cmd[:len(cmd) - len(model.args)]
        engine = "whisper.cpp" if model.engine == "whisper.cpp" else "llama.cpp"
        for flag in (word for word in own[1:] if word.startswith("-")):
            spellings = next((group for group in spark.render.SPELLINGS[engine] if flag in group), None)
            assert spellings, f"{engine}: no spellings listed for {flag}, which render passes {name}"
            for spelling in spellings:
                path = registry_with(tmp_path, lambda d: d["models"][name]["args"].extend([spelling, "1"]))
                with pytest.raises(RenderError, match=f"^{re.escape(name)}: args may not set {re.escape(spelling)}: "):
                    rendered(path)


@pytest.mark.parametrize("model", ["vision-chat", "coder"])
def test_args_may_not_cap_a_slots_share_of_the_pool(tmp_path, model):
    # pi is told a request can use the model's whole context; a per-slot cap would make that untrue.
    path = registry_with(tmp_path, lambda d: d["models"][model]["args"].extend(["--kv-unified-per-slot", "4096"]))
    with pytest.raises(RenderError, match=f"^{re.escape(model)}: args may not set --kv-unified-per-slot: "):
        rendered(path)


FILLED_IN = [  # (the edit, the model it lands in, what llama-swap would fill in). The registry refuses one in a source
    # file (test_registry.py), so the engines' paths carry it here. registry_with writes models in name order, so coder
    # is the first llama.cpp model.
    pytest.param(lambda d: d["models"]["coder"]["args"].extend(["--spec-type", "${env.LLAMASWAP_KEY_SPARK}"]),
                 "coder", "${env.LLAMASWAP_KEY_SPARK}", id="an arg"),
    pytest.param(lambda d: d["engines"].update({"llama.cpp": "/opt/${env.LLAMASWAP_KEY_AGENT}/llama-server"}),
                 "coder", "${env.LLAMASWAP_KEY_AGENT}", id="llama.cpp's path"),
    pytest.param(lambda d: d["engines"].update({"whisper.cpp": "/opt/${MODEL_ID}/whisper-server"}),
                 "stt", "${MODEL_ID}", id="whisper.cpp's path"),
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


SPLIT = [  # (the edit, the model it lands in): each word would reach the engine as other words. The registry refuses
    # these in a source file or projector (test_registry.py), so the engines' paths carry them here.
    pytest.param(lambda d: d["models"]["stt"]["args"].extend(["\\--host", "0.0.0.0"]), "stt",
                 id="a backslash"),  # \--host is --host once llama-swap unescapes it: a flag the lists read as a value
    pytest.param(lambda d: d["engines"].update({"whisper.cpp": "/opt/local-ai/bin/whisper-server --host 0.0.0.0"}),
                 "stt", id="whitespace"),
    pytest.param(lambda d: d["engines"].update({"llama.cpp": '/opt/local-ai/bin/"llama-server"'}), "coder",
                 id="quotes"),
]


@pytest.mark.parametrize("change, model", SPLIT)
def test_a_word_llama_swap_would_split_or_unescape_is_refused(tmp_path, change, model):
    # llama-swap splits a command as a POSIX shell does, so the checks above only hold if every word stays one.
    with pytest.raises(RenderError, match=f"^{re.escape(model)}: .* wouldn't reach the engine as written"):
        rendered(registry_with(tmp_path, change))


# Task 6's fix round 1: every refusal names what's wrong, with numbers that can't read as a fit.

def test_an_image_is_pinned_by_its_digest_not_a_commit(tmp_path):
    # versions.yaml takes git:<40 hex> for a source build; Compose would refuse image@git:… only at `up`.
    path = versions_with(tmp_path, lambda c: c["open-webui"].update(pin="git:" + "a" * 40))
    with pytest.raises(RenderError, match="^open-webui: an image is pinned by its sha256: digest"):
        rendered(versions_path=path)


@pytest.mark.parametrize("change", [pytest.param(lambda c: c["open-webui"].pop("image"), id="no image"),
                                    pytest.param(lambda c: c["searxng"].update(pin=None), id="no pin")])
def test_an_image_needs_its_name_and_its_pin(tmp_path, change):
    with pytest.raises(RenderError, match="image and pin are required to deploy"):
        rendered(versions_path=versions_with(tmp_path, change))


@pytest.mark.parametrize("name", ["llama-swap", "open-webui", "searxng"])
def test_a_component_render_needs_is_named_when_it_is_missing(tmp_path, name):
    with pytest.raises(RenderError, match=f"^{re.escape(name)}: not in the versions file"):
        rendered(versions_path=versions_with(tmp_path, lambda c: c.pop(name)))


ONE_FOR_EACH_JOB = [  # (the edit, the refusal): Open WebUI is given one task model, one embeddings, one speech-to-text
    pytest.param(lambda d: d["models"]["vision-chat"].update(roles=["vision"]),
                 "need exactly one model with the 'small' role, found []", id="no small model"),
    pytest.param(lambda d: d["models"]["coder"].update(capability="embeddings"),
                 "need exactly one embeddings model, found ['coder', 'embed']", id="two embeddings models"),
    pytest.param(lambda d: d["models"].pop("stt"),
                 "need exactly one transcription model, found []", id="no transcription model"),
]


@pytest.mark.parametrize("change, refusal", ONE_FOR_EACH_JOB)
def test_open_webui_needs_exactly_one_model_for_each_job(tmp_path, change, refusal):
    with pytest.raises(RenderError, match=f"^{re.escape(refusal)}$"):
        rendered(registry_with(tmp_path, change))


@pytest.mark.parametrize("allocatable, coder, refusal", [
    pytest.param(102, 56.4, "needs 78.4 GiB but the budget allows 78.0 GiB (allocatable 102 − reserve 24)",
                 id="78.4 against 78"),
    pytest.param(102, 56.01, "needs 78.1 GiB but the budget allows 78.0 GiB (allocatable 102 − reserve 24)",
                 id="78.01 against 78"),
    pytest.param(102.05, 56.06, "needs 78.1 GiB but the budget allows 78.0 GiB (allocatable 102.05 − reserve 24)",
                 id="78.06 against 78.05"),
])
def test_a_refused_budget_never_reads_as_a_fit(tmp_path, allocatable, coder, refusal):
    # The fixture's other models take 22 GiB, and ":.0f" showed 78.4 against 78 GiB of room (102 − 24) as "needs ~78
    # GiB but the budget allows 78 GiB". Now one decimal, each rounded against the set: the need up, the room down.
    path = registry_with(tmp_path, lambda d: (d["budget"].update(allocatable_gib=allocatable),
                                              d["models"]["coder"].update(footprint_gib=coder)))
    with pytest.raises(RenderError) as err:
        rendered(path)
    assert str(err.value) == f"the model set {refusal}"


def test_a_set_that_fits_exactly_renders(tmp_path):
    # In binary floats 72.1 − 22.1 is 49.99999999999999, which refused the fixture's 50 GiB though it fits exactly.
    path = registry_with(tmp_path, lambda d: d["budget"].update(allocatable_gib=72.1, reserve_gib=22.1))
    assert "llama-swap.yaml" in rendered(path)


def test_an_absurd_footprint_is_still_refused_with_its_numbers(tmp_path):
    # Decimal's default 28 digits can't hold 1e30 to a tenth, and the refusal would crash instead of saying why.
    path = registry_with(tmp_path, lambda d: d["models"]["coder"].update(footprint_gib=1e30))
    with pytest.raises(RenderError, match=r"^the model set needs 1000000000000000000000000000022\.0 GiB "):
        rendered(path)


def test_each_engine_gets_its_own_flags():
    registry = load_registry(FIX / "models.yaml")
    vision, embed, stt, coder = (engine_cmd(registry.models[name], registry)
                                 for name in ("vision-chat", "embed", "stt", "coder"))
    # Weights and projector come from the pinned snapshot, where `spark models pull` puts them (the Hub's cache layout).
    snapshot = "/var/lib/local-ai/hf/hub/models--example-org--vision-GGUF/snapshots/" + "1" * 40
    assert values(vision, "--model") == [f"{snapshot}/vision.gguf"]
    assert values(vision, "--mmproj") == [f"{snapshot}/vision-mmproj.gguf"]
    assert "--mmproj" not in embed + stt + coder  # only a model with a projector gets one
    assert "--embedding" in embed and "--embedding" not in vision + stt + coder
    # A model with more than one slot gives them one shared pool, so any one request can use its whole context, and an
    # idle slot keeps its cache until the pool runs short (without the second flag, llama-server would save idle slots
    # to the prompt cache and clear them at every new task).
    assert vision.count("--kv-unified") == 1 and "--kv-unified" not in embed + stt + coder
    assert vision.count("--no-cache-idle-slots") == 1 and "--no-cache-idle-slots" not in embed + stt + coder
    # llama-server runs offline; whisper-server has no such flag, and would stop at one it doesn't know.
    assert [cmd.count("--offline") for cmd in (vision, embed, coder, stt)] == [1, 1, 1, 0]
    # An engine takes no key, so each llama-server serves neither its slots' in-flight state (/slots) nor its own web
    # UI to the box's other accounts; whisper-server has neither.
    assert [cmd.count("--no-slots") for cmd in (vision, embed, coder, stt)] == [1, 1, 1, 0]
    assert [cmd.count("--no-webui") for cmd in (vision, embed, coder, stt)] == [1, 1, 1, 0]
    # whisper-server answers where llama-swap and Open WebUI send audio, and gets none of llama-server's flags.
    assert values(stt, "--inference-path") == ["/v1/audio/transcriptions"]
    assert not {"--ctx-size", "--parallel", "--gpu-layers", "--cache-ram", "--embedding"} & set(stt)
    # llama-server gets the registry's settings for the model, and every engine gets the model's args, last.
    assert [values(coder, flag) for flag in ("--ctx-size", "--parallel", "--gpu-layers", "--cache-ram")] == [
        ["131072"], ["1"], ["all"], ["2048"]]
    for name, cmd in (("stt", stt), ("coder", coder)):
        args = list(registry.models[name].args)
        assert cmd[-len(args):] == args, name


def test_write_tree_writes_each_file_under_out_making_its_folders(tmp_path):
    files = rendered()
    write_tree(files, tmp_path / "out")  # out, compose/searxng/ and systemd/ don't exist yet
    assert tree(tmp_path / "out") == files


def test_write_tree_replaces_each_file_whole_and_never_writes_through_a_link(tmp_path):
    # Task 7's fix round 1: `spark launch` reads the deployed registry on every load, so a load during `spark apply`
    # must see the old file or the new one, never half of one. Each file is written beside the old one and renamed
    # over it: a new file (a new inode), whole, and a link planted where a file goes is replaced, not written through.
    out, roots = tmp_path / "out", tmp_path / "roots"
    write_tree({"models.yaml": "old", "systemd/local-ai-brake.service": "old"}, out)
    (out / "models.yaml").chmod(0o640)
    before = (out / "models.yaml").stat().st_ino
    roots.write_text("root's copy")
    (out / "systemd/local-ai-brake.service").unlink()
    (out / "systemd/local-ai-brake.service").symlink_to(roots)
    write_tree({"models.yaml": "new", "systemd/local-ai-brake.service": "staged", "llama-swap.yaml": "added"}, out)
    assert tree(out) == {"models.yaml": "new", "systemd/local-ai-brake.service": "staged", "llama-swap.yaml": "added"}
    assert (out / "models.yaml").stat().st_ino != before
    assert not (out / "systemd/local-ai-brake.service").is_symlink() and roots.read_text() == "root's copy"
    # A file replaced keeps its mode; a new one gets what an ordinary write gives it, 0666 less the umask.
    umask = os.umask(0)
    os.umask(umask)
    assert (out / "models.yaml").stat().st_mode & 0o777 == 0o640
    assert (out / "llama-swap.yaml").stat().st_mode & 0o777 == 0o666 & ~umask


@pytest.mark.parametrize("given", [True, False], ids=["the files given", "stack's own by default"])
def test_spark_render_writes_what_render_renders(tmp_path, monkeypatch, capsys, given):
    monkeypatch.chdir(ROOT)  # as `make` runs it: the templates and stack's own files are found from the repo root
    out = tmp_path / "out"
    files = ["--registry", str(FIX / "models.yaml"), "--versions", str(FIX / "versions.yaml")] if given else []
    assert cli.main(["render", "--out", str(out), *files]) == 0
    assert capsys.readouterr().out == f"render: 8 files → {out}\n"
    assert tree(out) == (rendered() if given else real())


@pytest.mark.parametrize("path", [FIX / "models.yaml", ROOT / "stack/models.yaml"], ids=["fixture", "real"])
def test_llama_server_always_runs_offline(path):
    # Defense in depth (Task 6's fix round 2): a download option the refusals miss, or one set before the command line
    # is read, still can't fetch anything. whisper-server has no such option.
    registry = load_registry(path)
    for name, model in registry.models.items():
        expected = 1 if model.engine == "llama.cpp" else 0
        assert engine_cmd(model, registry).count("--offline") == expected, name


def test_the_brake_and_llama_swap_keep_systemds_default_umask():
    # The hold and the refusal record they write stay readable by spark-admin, which `spark status` needs; a UMask
    # tighter than 0027 on either unit would hide them (Task 5's review).
    files = rendered()
    for unit in ("local-ai-brake.service", "local-ai-llama-swap.service"):
        assert "UMask" not in files[f"systemd/{unit}"], unit


def test_a_restart_of_the_brake_or_llama_swap_fails_when_its_binary_cant_start():
    # Task 7's fix round 1: with systemd's default Type=simple, `systemctl restart` succeeds as soon as systemd forks,
    # even when the binary is missing. Type=exec waits until it runs, so `spark apply` hears of a binary that can't be
    # run. One that stops once it runs still passes: apply checks each unit afterwards (Phase 1's council).
    files = rendered()
    for unit, kept in (("local-ai-llama-swap.service", ["Restart=on-failure", "RestartSec=5", "KillMode=control-group"]),
                       ("local-ai-brake.service", ["Restart=always", "RestartSec=2", "OOMScoreAdjust=-900"])):
        service = files[f"systemd/{unit}"].split("\n[Service]\n", 1)[1].split("\n[", 1)[0].splitlines()
        assert [line for line in service if line.startswith("Type=")] == ["Type=exec"], unit
        assert set(kept) <= set(service), unit  # and the rest as it was
