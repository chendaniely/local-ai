from pathlib import Path

from spark.registry import load_registry
from spark.versions import load_versions

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
STACK_VERSIONS = STACK_REGISTRY.with_name("versions.yaml")
LLAMA_CPP_BATCH_DEFAULT = 2048  # llama-server b11146's --batch-size default; it caps --ubatch-size
LLAMA_CPP_UBATCH_DEFAULT = 512  # and its --ubatch-size default
# Each pinned GGUF's own context_length, read from its header on 2026-09-28, by (repo, revision, file). Every model runs
# at its maximum (Dan's decision, 2026-09-28): a model added, swapped or moved to a new revision needs its entry here,
# read the same way. Qwen3.8-27B's was read from its header by Phase 2a's council, on 2026-10-07. Qwen3.6-35B-A3B left
# the registry on 2026-10-08, and its entry stays for the way back.
NATIVE_CTX = {
    ("google/gemma-4-26B-A4B-it-qat-q4_0-gguf", "d1c082be9cf3c8a514acf63b8761f4b41935842e",
     "gemma-4-26B_q4_0-it.gguf"): 262144,
    ("Qwen/Qwen3-Embedding-0.6B-GGUF", "370f27d7550e0def9b39c1f16d3fbaa13aa67728",
     "Qwen3-Embedding-0.6B-Q8_0.gguf"): 32768,
    ("unsloth/Qwen3.6-35B-A3B-MTP-GGUF", "5bc3e238d916f48a861bac2f8a1990a0e9b7e98d",
     "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"): 262144,
    ("unsloth/Qwen3.8-27B-GGUF", "4ca720788d1e01f1bff70c033e0d0028fd02e502",
     "Qwen3.8-27B-UD-Q4_K_XL.gguf"): 262144,
}
# Qwen3.8-27B, the coder, was held at 163,840 by an exception here from its early swap on 2026-10-08 until render's
# corrected budget check reached main the same day (Dan's decision), which let it run at all 262,144.


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
