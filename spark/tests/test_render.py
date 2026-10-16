import copy
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import spark.render
from spark import cli, tickets
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


def test_llama_swap_keeps_the_engines_output_out_of_the_journal(files):
    # Dan's decision (2026-09-28, at Phase 1's close), reversing the same day's "both" before it was deployed:
    # whisper-server v1.9.4 logs each upload's file name, and its ffmpeg conversion reports the file's metadata, which
    # the journal would keep. So llama-swap stays at v257's default, "proxy": an engine's output, a refused start's
    # reason included, reaches only an in-memory buffer that every restart wipes. Pinned, so a change is deliberate;
    # Phase 2 revisits it with a check that covers speech.
    assert yaml.safe_load(files["llama-swap.yaml"])["logToStdout"] == "proxy"


LOGGING = [  # (the model, an engine option that would log what a model is sent), each refused by the allowlist
    *[("coder", flag) for flag in ("-v", "--verbose", "--log-verbose", "-lv", "--verbosity", "--log-verbosity",
                                   "--log-file")],
    ("stt", "-pr"), ("stt", "--print-realtime"),  # whisper-server prints each transcript as it goes
]


@pytest.mark.parametrize("model, flag", LOGGING)
def test_args_may_not_turn_up_an_engines_logging(tmp_path, model, flag):
    # These options would make an engine log what a model is sent, to llama-swap's buffer now and to the journal if
    # llama-swap's logging ever moves there; the allowlist refuses them either way.
    path = registry_with(tmp_path, lambda d: d["models"][model]["args"].extend([flag, "5"]))
    with pytest.raises(RenderError, match=f"^{re.escape(model)}: args may not set {re.escape(flag)}: {UNLISTED}"):
        rendered(path)


def test_llama_swap_takes_only_the_three_internal_keys(files):
    # From the cutover, llama-swap's callers are the front, the gate and the brake, each with an internal key of its
    # own; the client keys reach only the front, as digests, and LLAMASWAP_KEY_SPARK retires (plan.md, *The keys*).
    assert spark.render.INTERNAL_KEY_ENVS == ("LLAMASWAP_KEY_FRONT", "LLAMASWAP_KEY_GATE", "LLAMASWAP_KEY_BRAKE")
    assert yaml.safe_load(files["llama-swap.yaml"])["apiKeys"] == [
        "${env.LLAMASWAP_KEY_FRONT}", "${env.LLAMASWAP_KEY_GATE}", "${env.LLAMASWAP_KEY_BRAKE}"]
    assert not hasattr(spark.render, "KEY_ENVS")


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


def unit_settings(text: str, section: str) -> dict[str, list[str]]:
    """A rendered unit's settings in one section, each name with every value it's given, in order."""
    lines = text.split(f"\n[{section}]\n", 1)[1].split("\n[", 1)[0].splitlines()
    settings: dict[str, list[str]] = {}
    for line in lines:
        if line and not line.startswith("#"):
            name, value = line.split("=", 1)
            settings.setdefault(name, []).append(value)
    return settings


# Phase 2a's Task 37 (Dan's decision, 2026-10-07): the web containers run as root on the host's network, and with
# Docker's default capabilities either could bind a free port below 1024 on 127.0.0.1. So each drops them all, and a
# capability comes back only once its container is shown to need it on the box, with what failed without it.

def test_the_web_containers_drop_every_capability(files):
    services = yaml.safe_load(files["compose/compose.yaml"])["services"]
    # A new service gets the same treatment, so this fails until it does.
    assert set(services) == {"open-webui", "searxng"}
    for name, service in services.items():
        assert service.get("cap_drop") == ["ALL"], name


def capabilities_added(text: str) -> list[tuple[str, str]]:
    """Each capability a Compose file's `cap_add:` lists, with the comment on its line ('' for none). Only a block
    list, an entry to a line, lets each entry carry its own reason, so a flow list (`cap_add: [X]`) is refused."""
    found, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        key = re.match(r"\s*cap_add:(.*)", line)
        if not key:
            continue
        assert key[1].split("#", 1)[0].strip() == "", f"cap_add as a flow list, its entries can't each say why: {line}"
        for item in lines[i + 1:]:
            entry = re.fullmatch(r"\s*-\s*([^\s#]+)\s*(?:#\s*(.*?))?\s*", item)
            if not entry:
                break
            found.append((entry[1], entry[2] or ""))
    return found


def test_each_capability_added_back_carries_its_reason(files):
    text = files["compose/compose.yaml"]
    services = yaml.safe_load(text)["services"]
    added = capabilities_added(text)
    # The lines read here are the entries Compose reads, so an entry this misses can't pass unread.
    assert sorted(cap for cap, _ in added) == sorted(cap for s in services.values() for cap in s.get("cap_add", []))
    assert [cap for cap, reason in added if not reason] == []


# Phase 1 (Task 13, 2026-09-28) saw duckduckgo take 2.7 s from the Spark against SearXNG's default 3 s, and the cold
# first search found nothing.
SLOWEST_ENGINE_S = 2.7


def test_searxng_waits_longer_than_its_slowest_engine():
    settings = yaml.safe_load(rendered()["compose/searxng/settings.yml"])
    timeout = (settings.get("outgoing") or {}).get("request_timeout")
    assert isinstance(timeout, (int, float)) and timeout > SLOWEST_ENGINE_S, timeout


LLAMA_SWAP_UNIT = "systemd/local-ai-llama-swap.service"


def llama_swap_unit(section: str = "Service") -> dict[str, list[str]]:
    return unit_settings(rendered()[LLAMA_SWAP_UNIT], section)


def test_llama_swap_listens_on_900_and_the_engines_start_at_800(files):
    # Below 1024, so no unprivileged account can take either while llama-swap restarts (plan.md, Phase 2a); the front
    # holds 9100 from the cutover.
    assert (spark.render.LLAMASWAP_LISTEN, spark.render.ENGINE_START_PORT) == ("127.0.0.1:900", 800)
    [start] = unit_settings(files[LLAMA_SWAP_UNIT], "Service")["ExecStart"]
    assert start.endswith(" -config /opt/local-ai/etc/llama-swap.yaml -listen 127.0.0.1:900"), start
    assert yaml.safe_load(files["llama-swap.yaml"])["startPort"] == 800


def stand_ins(tmp_path, count: int) -> Path:
    """The fixture registry with on-demand stand-ins added, `count` models in all."""
    def add(data):
        coder = data["models"]["coder"]
        for i in range(count - len(data["models"])):
            data["models"][f"stand-in-{i:03}"] = copy.deepcopy(coder) | {
                "label": f"stand-in {i:03}", "roles": [], "footprint_gib": 1}
    return registry_with(tmp_path, add)


def test_render_refuses_engine_ports_that_would_reach_900(tmp_path):
    # v257 hands each model a port from startPort, one each in their names' order: a 101st model would get 900,
    # llama-swap's own.
    with pytest.raises(RenderError, match=r"^101 models would take engine ports 800 to 900, reaching llama-swap's own "
                                          r"port, 900: .* at most 100 "):
        rendered(stand_ins(tmp_path, 101))
    cfg = yaml.safe_load(rendered(stand_ins(tmp_path, 100))["llama-swap.yaml"])
    assert len(cfg["models"]) == 100


def test_every_llama_cpp_engine_answers_as_its_registry_name(files):
    # A reply names its model as the registry does, not by its file (Phase 1's close), and the memory sampler and the
    # drills find an engine by it.
    cfg = yaml.safe_load(files["llama-swap.yaml"])
    for name, model in cfg["models"].items():
        words = shlex.split(model["cmd"])
        if "whisper-server" in words[4]:
            assert not {"-a", "--alias"} & set(words), name
        else:
            assert values(words, "--alias") == [name] and "-a" not in words, name


@pytest.mark.parametrize("flag", ["-a", "--alias"])
def test_alias_is_render_owned_in_every_spelling(tmp_path, flag):
    path = registry_with(tmp_path, lambda d: d["models"]["coder"]["args"].extend([flag, "other"]))
    with pytest.raises(RenderError, match=f"^coder: args may not set {re.escape(flag)}: render sets it"):
        rendered(path)


def test_gemmas_slots_are_chosen_by_prompt_only_when_half_of_it_matches():
    # The controller's ruling, 2026-10-14, at Task 24: at b11146's default, 0.10, a short new prompt can take a long
    # chat's slot, and a chat with two checkpoints is too big for the 1 GiB prompt cache, so it re-reads whole. What 0.5
    # costs in its turn is in plan.md's dated note, in the Phase 2a line.
    assert ("-sps", "--slot-prompt-similarity") in spark.render.ALLOWED["llama.cpp"]
    cfg = yaml.safe_load(real()["llama-swap.yaml"])
    assert values(shlex.split(cfg["models"]["gemma-4-26b-a4b"]["cmd"]), "--slot-prompt-similarity") == ["0.5"]


def test_llama_swap_binds_below_1024_with_only_cap_net_bind_service():
    # The engines inherit it across `spark launch`'s exec; no other unit gets it (plan.md, Phase 2a).
    service = llama_swap_unit()
    assert service["AmbientCapabilities"] == ["CAP_NET_BIND_SERVICE"]
    assert service["CapabilityBoundingSet"] == ["CAP_NET_BIND_SERVICE"]
    others = {rel: text for rel, text in rendered().items() if rel.startswith("systemd/") and rel != LLAMA_SWAP_UNIT}
    assert others and not [rel for rel, text in others.items() if "CAP_NET_BIND_SERVICE" in text]


SANDBOX = {
    "NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes",
    "InaccessiblePaths": "/etc/local-ai/secrets", "RestrictSUIDSGID": "yes", "ProtectKernelTunables": "yes",
    "ProtectControlGroups": "yes",
}


def test_llama_swap_is_sandboxed_without_private_devices_or_mdwe():
    # Dan's decision, 2026-10-07; CUDA needs the GPU's device nodes and memory it can write and run. A device policy or
    # allow-list would block /dev/nvidia* as surely as PrivateDevices=.
    service = llama_swap_unit()
    assert {name: service.get(name) for name in SANDBOX} == {name: [value] for name, value in SANDBOX.items()}
    assert not {"PrivateDevices", "MemoryDenyWriteExecute", "DevicePolicy", "DeviceAllow"} & set(service)


LLAMASWAP_URL_DEFAULT = """
from spark import paths, render
print(paths.LLAMASWAP_URL, f"http://{render.LLAMASWAP_LISTEN}")
"""


def test_llama_swaps_callers_reach_it_where_render_binds_it(tmp_path):
    # The front, the gate, the brake, `spark apply`, `spark status` and `spark doctor` reach llama-swap at
    # paths.LLAMASWAP_URL's default; render binds it at LLAMASWAP_LISTEN. paths stays light (the front and launch import
    # it), so a test ties the two.
    env = {name: value for name, value in os.environ.items() if name != "SPARK_LLAMASWAP_URL"}
    result = subprocess.run([sys.executable, "-c", LLAMASWAP_URL_DEFAULT], cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    used, bound = result.stdout.split()
    assert used == bound == "http://127.0.0.1:900"


def test_llama_swap_restarts_always_and_alerts_on_failure():
    # v257 answers a stray SIGTERM with a clean exit, which on-failure left down (plan.md, Phase 2a).
    service = llama_swap_unit()
    assert (service["Restart"], service["RestartSec"]) == (["always"], ["5"])
    assert llama_swap_unit("Unit")["OnFailure"] == ["local-ai-notify@%n.service"]


def test_llama_swap_starts_after_what_loads_nvidia_uvm():
    # On this box DGX OS's nvidia-cdi-refresh.service loads nvidia-uvm, which an engine can't load itself under
    # NoNewPrivileges= (the setuid nvidia-modprobe).
    after = " ".join(llama_swap_unit("Unit")["After"]).split()
    assert {"nvidia-cdi-refresh.service", "nvidia-persistenced.service"} <= set(after)


def test_only_the_tickets_folder_whispers_tmp_and_the_caches_are_writable_to_llama_swap():
    # The fourth is launch's whole folder, never only launch/tickets: launch, inside the sandbox, renames in tickets/
    # and writes started/ and refusals/ (the controller's ruling at Task 11's review). The brake's hold folder stays
    # read-only, so an engine can't delete the hold.
    service = llama_swap_unit()
    assert " ".join(service["ReadWritePaths"]).split() == [
        "/var/lib/local-ai/whisper-tmp", "/var/lib/local-ai/cache", "/var/lib/local-ai/cuda-cache",
        "/var/lib/local-ai/launch"]
    assert "SPARK_LAUNCH=/var/lib/local-ai/launch" in service["Environment"]
    assert "SPARK_STATE=/var/lib/local-ai/brake" in service["Environment"]


def test_llama_swap_reads_only_internal_keys_env():
    # Its EnvironmentFile holds the three internal keys only; the client keys' file isn't read (plan.md, *The keys*).
    assert llama_swap_unit()["EnvironmentFile"] == ["/etc/local-ai/secrets/internal-keys.env"]


def test_health_check_timeout_is_180(files):
    # 2a's load deadline (plan.md: nothing waits inside cmd any more, so it covers only a load), one constant for the
    # rendered config and the gate's load call (llamaswap_async.load_timeout_for). A started record (Task 11) counts
    # for twice it, so the two move together.
    assert spark.render.HEALTH_CHECK_TIMEOUT_S == 180
    assert yaml.safe_load(files["llama-swap.yaml"])["healthCheckTimeout"] == spark.render.HEALTH_CHECK_TIMEOUT_S
    assert tickets.STARTED_EXPIRES_S == 2 * spark.render.HEALTH_CHECK_TIMEOUT_S


def test_engines_and_downloads_cache_in_folders_bootstrap_gives_them():
    # /var/lib/local-ai is spark's home but root's, so spark can't write a cache under $HOME. The unit that runs the
    # engines names the spark-owned folders bootstrap creates. The pull, which from 2a runs as spark-pull and runs no
    # CUDA, names a folder of its own (Task 25), which bootstrap gives spark-pull (Task 27).
    files = rendered()
    text = files["systemd/local-ai-llama-swap.service"]
    assert "Environment=XDG_CACHE_HOME=/var/lib/local-ai/cache" in text
    assert "Environment=CUDA_CACHE_PATH=/var/lib/local-ai/cuda-cache" in text
    text = files["systemd/local-ai-pull.service"]
    assert "Environment=XDG_CACHE_HOME=/var/lib/local-ai/pull-cache" in text and "CUDA_CACHE_PATH" not in text


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
        "systemd/local-ai-front.socket": "/etc/systemd/system/local-ai-front.socket",
        "systemd/local-ai-front.service": "/etc/systemd/system/local-ai-front.service",
        "systemd/local-ai-gate-status.socket": "/etc/systemd/system/local-ai-gate-status.socket",
        "systemd/local-ai-gate-control.socket": "/etc/systemd/system/local-ai-gate-control.socket",
        "systemd/local-ai-gate.service": "/etc/systemd/system/local-ai-gate.service",
        "systemd/local-ai-notify@.service": "/etc/systemd/system/local-ai-notify@.service",
        "systemd/local-ai-brake-drill.service": "/etc/systemd/system/local-ai-brake-drill.service",
    }
    assert (UNIT_DIR, COMPOSE_DIR) == ("/etc/systemd/system", "/etc/local-ai/compose")


def test_root_runs_compose_in_its_own_copy_of_the_project():
    # Compose reads every file in its project folder, a .env and an override file included, so root
    # runs it in root's copy, never in /opt/local-ai/etc, which Dan can write.
    unit = rendered()["systemd/local-ai-compose.service"]
    assert f"\nWorkingDirectory={COMPOSE_DIR}\n" in unit and "/opt/local-ai" not in unit


def test_a_model_that_cant_load_beside_the_residents_is_refused(tmp_path):
    # Rule 9: the gate's formula at idle with the fixture's residents loaded, min(117 − 22 − 24, 102 − 22) = 71.
    path = registry_with(tmp_path, lambda d: d["models"]["coder"].update(footprint_gib=72))
    with pytest.raises(RenderError, match=r"^coder: needs 72\.0 GiB, but 71\.0 GiB is free for a load beside the "):
        rendered(path)


def test_the_2a_set_renders_where_phase_1s_check_refused_it(tmp_path):
    # Phase 1's check summed every footprint against allocatable − reserve, 102 − 24 = 78, which counted the reserve
    # twice (plan.md, rule 9, corrected 2026-10-07). With 2a's coder at its ~41 the set comes to 84, within the
    # ceiling, and the coder fits beside the residents with the reserve kept: 41 of 117 − 43 − 24 = 50.
    # The set is the residents and the coder: Qwen3.6, back on demand beside it since 2026-10-16 (Dan's decision),
    # is left out here, since every model at once then passes the ceiling, which render only warns of
    # (test_the_real_registry_warns_only_that_all_cant_load_at_once).
    data = yaml.safe_load((ROOT / "stack/models.yaml").read_text())
    coder = next(name for name, m in data["models"].items() if "coder" in m.get("roles", []))
    data["models"] = {name: m for name, m in data["models"].items() if m["resident"] or name == coder}
    data["models"][coder]["footprint_gib"] = 41
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data))
    assert spark.render.check_budget(load_registry(path)) == []  # and no warning: 117 − 84 leaves 33, above 28
    assert "llama-swap.yaml" in rendered(path, ROOT / "stack/versions.yaml")


def test_the_real_registry_renders():
    registry = load_registry(ROOT / "stack/models.yaml")
    files = render(registry, load_versions(ROOT / "stack/versions.yaml"), (ROOT / "stack/models.yaml").read_text(),
                   templates=ROOT / "stack/templates")
    assert set(files) == set(rendered())  # the same eight files as the fixture renders
    assert set(yaml.safe_load(files["llama-swap.yaml"])["models"]) == set(registry.models)


def test_the_real_registry_warns_only_that_all_cant_load_at_once():
    # Since Qwen3.6 came back beside the coder (Dan's decision, 2026-10-16), the two on-demand models don't fit at once
    # beside the residents: render warns, and refuses nothing. Each fits alone: 41 and 33 of 117 − 43 − 24 = 50.
    warnings = spark.render.check_budget(load_registry(ROOT / "stack/models.yaml"))
    assert len(warnings) == 2
    assert "above the CUDA-allocatable ceiling" in warnings[0]
    assert "under the brake's warn line" in warnings[1]


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


def _rename(table: dict, old: str, new: str) -> None:
    table[new] = table.pop(old)


PI_WOULD_RETRY = [
    pytest.param(lambda d: d["models"]["coder"].update(label="the 500m coder"),
                 r"^coder: its label 'the 500m coder' holds '500'", id="label"),
    pytest.param(lambda d: d["models"]["coder"].update(roles=["coder", "timeout"]),
                 r"^coder: its role 'timeout' holds 'timeout'", id="role"),
    pytest.param(lambda d: _rename(d["models"], "coder", "coder-500m"),
                 r"^coder-500m: its name 'coder-500m' holds '500'", id="name"),
    pytest.param(lambda d: _rename(d["key_groups"], "agent", "agent-503"),
                 r"^key_groups: agent-503: its name 'agent-503' holds '503'", id="group"),
    pytest.param(lambda d: d["key_groups"]["agent"].update(wait_s=429 * 3600),
                 r"^key_groups: agent: its wait, 1544400 s, reads '429 hours', which holds '429'", id="wait"),
]


@pytest.mark.parametrize("change, refusal", PI_WOULD_RETRY)
def test_registry_text_pi_would_retry_on_is_refused(tmp_path, change, refusal):
    # pi retries a turn three times when its error's text matches its list (messages.PI_RETRY_PATTERNS): a refusal
    # naming such a model, role or wait would reach Dan only after the retries, each a new wait.
    with pytest.raises(RenderError, match=refusal) as err:
        rendered(registry_with(tmp_path, change))
    assert "pi would retry every refusal" in str(err.value)


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
    pytest.param(102, 71.4, "needs 71.4 GiB, but 71.0 GiB", id="71.4 against 71"),
    pytest.param(102, 71.01, "needs 71.1 GiB, but 71.0 GiB", id="71.01 against 71"),
    # The ceiling term binds: 72.05 − 22 leaves 50.05.
    pytest.param(72.05, 50.06, "needs 50.1 GiB, but 50.0 GiB", id="50.06 against 50.05"),
])
def test_a_refused_budget_never_reads_as_a_fit(tmp_path, allocatable, coder, refusal):
    # Phase 1's ":.0f" showed 78.4 against 78 GiB of room as "needs ~78 GiB but the budget allows 78 GiB". One decimal,
    # each rounded against the load: the need up, the room down. The fixture's residents take 22 GiB.
    path = registry_with(tmp_path, lambda d: (d["budget"].update(allocatable_gib=allocatable),
                                              d["models"]["coder"].update(footprint_gib=coder)))
    with pytest.raises(RenderError) as err:
        rendered(path)
    assert str(err.value).startswith(f"coder: {refusal} is free for a load beside the always-loaded models ")


@pytest.mark.parametrize("term, coder", [({"allocatable_gib": 72.1}, 50.1), ({"idle_available_gib": 102.1}, 56.1)],
                         ids=["ceiling", "idle"])
def test_a_model_that_fits_exactly_renders(tmp_path, term, coder):
    # Beside the fixture's 22 GiB of residents, in binary floats 72.1 − 22 is 50.099999999999994 and 102.1 − 22 − 24 is
    # 56.099999999999994, which would refuse a coder of 50.1 or 56.1 GiB, though each fits exactly.
    path = registry_with(tmp_path, lambda d: (d["budget"].update(term),
                                              d["models"]["coder"].update(footprint_gib=coder)))
    assert "llama-swap.yaml" in rendered(path)


def test_an_absurd_footprint_is_still_refused_with_its_numbers(tmp_path):
    # Decimal's default 28 digits can't hold 1e30 to a tenth, and the refusal would crash instead of saying why. Nor
    # could they hold the residents' sum: 1e30 + 1.5 + 2.5.
    path = registry_with(tmp_path, lambda d: d["models"]["vision-chat"].update(footprint_gib=1e30))
    with pytest.raises(RenderError, match=r"^the always-loaded models need 1000000000000000000000000000004\.0 GiB "
                                          r"together, but 93\.0 GiB is free for a load"):
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
    # The llama-swap config, the registry, the Compose project's two files and the eleven units (Task 25). The stack's
    # own registry adds render's two warnings since Qwen3.6 came back (2026-10-16): every model at once can't load.
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == f"render: 15 files → {out}"
    assert len(printed) == (1 if given else 3)
    assert all(line.startswith("render: warning — every model loaded at once") for line in printed[1:])
    assert tree(out) == (rendered() if given else real())


def test_spark_render_prints_its_warnings(tmp_path, monkeypatch, capsys):
    # A warning doesn't refuse the set: render writes it, says what it found, and exits 0. Here every model loaded
    # leaves 117 − 50 = 67 GiB available, under a warn line of 70.
    monkeypatch.chdir(ROOT)
    out, registry = tmp_path / "out", registry_with(tmp_path, lambda d: d["brake"].update(warn_gib=70))
    assert cli.main(["render", "--out", str(out), "--registry", str(registry),
                     "--versions", str(FIX / "versions.yaml")]) == 0
    assert capsys.readouterr().out == (f"render: 15 files → {out}\n"
                                       "render: warning — every model loaded at once would leave 67.0 GiB available, "
                                       "under the brake's warn line, 70.0 GiB\n")
    assert tree(out) == rendered(registry)


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
    for unit, kept in (("local-ai-llama-swap.service", ["Restart=always", "RestartSec=5", "KillMode=control-group"]),
                       ("local-ai-brake.service", ["Restart=always", "RestartSec=2", "OOMScoreAdjust=-900"])):
        service = files[f"systemd/{unit}"].split("\n[Service]\n", 1)[1].split("\n[", 1)[0].splitlines()
        assert [line for line in service if line.startswith("Type=")] == ["Type=exec"], unit
        assert set(kept) <= set(service), unit  # and the rest as it was
