from pathlib import Path

from spark.registry import ClientKey, load_keys, load_registry
from spark.versions import load_versions

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
STACK_VERSIONS = STACK_REGISTRY.with_name("versions.yaml")
STACK_KEYS_EXAMPLE = STACK_REGISTRY.with_name("keys.example.yaml")
LLAMA_CPP_BATCH_DEFAULT = 2048  # llama-server b11146's --batch-size default; it caps --ubatch-size
LLAMA_CPP_UBATCH_DEFAULT = 512  # and its --ubatch-size default
# Each pinned GGUF's own context_length, read from its header on 2026-09-28, by (repo, revision, file). Every model runs
# at its maximum (Dan's decision, 2026-09-28): a model added, swapped or moved to a new revision needs its entry here,
# read the same way.
NATIVE_CTX = {
    ("google/gemma-4-26B-A4B-it-qat-q4_0-gguf", "d1c082be9cf3c8a514acf63b8761f4b41935842e",
     "gemma-4-26B_q4_0-it.gguf"): 262144,
    ("Qwen/Qwen3-Embedding-0.6B-GGUF", "370f27d7550e0def9b39c1f16d3fbaa13aa67728",
     "Qwen3-Embedding-0.6B-Q8_0.gguf"): 32768,
    ("unsloth/Qwen3.6-35B-A3B-MTP-GGUF", "5bc3e238d916f48a861bac2f8a1990a0e9b7e98d",
     "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"): 262144,
}


def flag(args: tuple[str, ...], *spellings: str) -> int | None:
    for i, word in enumerate(args[:-1]):
        if word in spellings:
            return int(args[i + 1])
    return None


def option(args: tuple[str, ...], *spellings: str) -> str | None:
    for i, w in enumerate(args[:-1]):
        if w in spellings:
            return args[i + 1]
    return None


def test_vision_models_decode_a_whole_image_in_one_ubatch():
    # llama.cpp decodes an image's tokens with non-causal attention, all in one micro-batch, and an image with more
    # tokens than --ubatch-size aborts the whole engine (GGML_ASSERT in llama-context.cpp): Gemma 4 died on every
    # phone photo at the default 512 (2026-09-28). b11146 gives a gemma4v image up to 1120 tokens, so each vision
    # model pins its image budget, and a micro-batch that holds it.
    vision = [m for m in load_registry(STACK_REGISTRY).models.values() if m.source.mmproj]
    assert vision
    for model in vision:
        image_tokens = flag(model.args, "--image-max-tokens")
        ubatch = flag(model.args, "-ub", "--ubatch-size")
        batch = flag(model.args, "-b", "--batch-size") or LLAMA_CPP_BATCH_DEFAULT
        assert image_tokens is not None and ubatch is not None, model.name
        assert image_tokens <= min(ubatch, batch), model.name


def test_every_llama_cpp_model_runs_at_its_full_context():
    llama = [m for m in load_registry(STACK_REGISTRY).models.values() if m.engine == "llama.cpp"]
    assert llama
    for model in llama:
        assert model.ctx == NATIVE_CTX[(model.source.repo, model.source.revision, model.source.file)], model.name


def test_an_embedding_model_pools_its_last_token_or_holds_its_whole_context_in_one_ubatch():
    # llama-server b11146 splits an input across micro-batches only when the model pools its last token (its KV cache
    # carries the rest: server-context.cpp, can_split). Otherwise an input longer than the micro-batch is refused, and
    # the micro-batch is the smaller of --ubatch-size and --batch-size (llama-context.cpp).
    embed = [m for m in load_registry(STACK_REGISTRY).models.values() if m.capability == "embeddings"]
    assert embed
    for model in embed:
        if option(model.args, "--pooling") == "last":
            continue
        ubatch = flag(model.args, "-ub", "--ubatch-size") or LLAMA_CPP_UBATCH_DEFAULT
        batch = flag(model.args, "-b", "--batch-size") or LLAMA_CPP_BATCH_DEFAULT
        assert min(ubatch, batch) >= model.ctx, model.name


def test_each_engine_runs_from_the_folder_of_its_pinned_version():
    # A version bump leaves the old version's folder in place, so a bump that edits only versions.yaml would go on
    # running the old engine while versions.yaml, the Stack page and README named the new one (Phase 1's council,
    # toolstack I2). Each engine's path names its version, as the llama-swap unit's does.
    registry, versions = load_registry(STACK_REGISTRY), load_versions(STACK_VERSIONS)
    assert set(registry.engines) == {"llama.cpp", "whisper.cpp"}
    for engine, path in registry.engines.items():
        assert f"/{versions[engine].version}/" in path, (engine, path, versions[engine].version)


def test_every_chat_model_caps_its_context_checkpoints():
    # llama-server keeps a chat's context checkpoints in host memory, after admission, 32 per slot unless capped: a
    # footprint can count them only when they're capped (Phase 1's council, reliability I2).
    chat = [m for m in load_registry(STACK_REGISTRY).models.values()
            if m.engine == "llama.cpp" and m.capability == "chat"]
    assert chat
    for model in chat:
        assert flag(model.args, "-ctxcp", "--ctx-checkpoints", "--swa-checkpoints") is not None, model.name


# Phase 2a's Task 4: the values the plan's Global Constraints rule, in the deployed registry.

def test_the_stack_registrys_waits_are_30s_for_dan_and_10_minutes_for_agent():
    groups = load_registry(STACK_REGISTRY).key_groups
    assert set(groups) == {"dan", "agent"}
    assert groups["dan"].wait_s == 30 and groups["agent"].wait_s == 600


def test_the_stack_registrys_groups_give_2as_behaviour():
    # The design's one `dan` flag, as five settings apart (Dan's decision, 2026-10-07): today's two groups give 2a's
    # behaviour exactly.
    groups = load_registry(STACK_REGISTRY).key_groups

    def behaviour(name):
        g = groups[name]
        return g.queue, g.uses_hold, g.reloads_marked, g.words, g.names_processes

    assert behaviour("dan") == (0, True, True, "dan", True)
    assert behaviour("agent") == (1, False, False, "agent", False)


def test_the_stack_registrys_caps_are_the_rulings():
    groups = load_registry(STACK_REGISTRY).key_groups
    assert (groups["dan"].max_waiting, groups["dan"].max_open) == (8, 32)
    assert (groups["agent"].max_waiting, groups["agent"].max_open) == (4, 32)


def test_on_demand_models_idle_unload_after_60_minutes():
    assert load_registry(STACK_REGISTRY).gate.idle_unload_min == 60


def test_owed_reads_rss_stays_off_until_the_soak():
    # The gate counts an engine's RssAnon growth as memory a model already holds only once the soak has shown that it
    # follows that engine's growth (plan.md, rule 9). Task 45 changes this test, with the soak's evidence.
    registry = load_registry(STACK_REGISTRY)
    assert registry.gate.owed_reads_rss == {engine: False for engine in registry.engines}


PLAN_PRIORITIES = {  # the Global Constraints' list, all on
    "brake_fired": "high", "brake_needs_release": "high", "gate_down": "high", "front_down": "high",
    "llama_swap_down": "high", "brake_down": "high", "back_up": "default", "refused": "default",
    "footprint_suspect": "default", "load_failed": "default", "brake_released": "default",
    "room_hold_ended": "default", "resident_waiting": "default", "apply_restarted": "default", "load_started": "low",
    "loaded": "low", "unloaded": "low", "waiting": "low", "pin_ended": "low", "memory_warning": "low",
}


def test_every_notification_is_on_at_the_plans_priority():
    notifications = load_registry(STACK_REGISTRY).notifications
    assert notifications == PLAN_PRIORITIES
    assert "off" not in notifications.values()


def test_the_stack_registrys_labels_are_the_plans_words():
    models = load_registry(STACK_REGISTRY).models
    assert {name: m.label for name, m in models.items()} == {
        "gemma-4-26b-a4b": "Gemma",
        "qwen3-embedding-0.6b": "the embeddings",
        "whisper-large-v3-turbo": "whisper",
        "qwen3.6-35b-a3b": "the coder",
    }
    assert {name: m.used_by for name, m in models.items() if m.used_by} == {
        "gemma-4-26b-a4b": "the web UI and photos use it"}


def test_the_budget_records_idle_memavailable():
    budget = load_registry(STACK_REGISTRY).budget
    assert budget.idle_available_gib == 117
    assert budget.allocatable_measured is False


def test_the_example_key_list_loads_and_names_no_real_key():
    # The real list is /etc/local-ai/keys.yaml, on the Spark only; the example shows its shape with placeholder names.
    keys = load_keys(STACK_KEYS_EXAMPLE, load_registry(STACK_REGISTRY).key_groups)
    assert keys and all(name.startswith("example-") for name in keys)
    assert keys["example-mac"] == ClientKey("example-mac", "dan", "pi on the Mac", None)
    assert keys["example-web-ui"] == ClientKey("example-web-ui", "dan", "the web UI", None)
    assert keys["example-agent"] == ClientKey("example-agent", "agent", "agent", "agent")
