from pathlib import Path

from spark.registry import load_registry

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
LLAMA_CPP_BATCH_DEFAULT = 2048  # llama-server b11146's --batch-size default; it caps --ubatch-size
# Each pinned GGUF's own context_length, read from its header on 2026-09-28. Every model runs at its maximum (Dan's
# decision, 2026-09-28): a model added or swapped needs its entry here, read the same way.
NATIVE_CTX = {"gemma-4-26B_q4_0-it.gguf": 262144, "Qwen3-Embedding-0.6B-Q8_0.gguf": 32768,
              "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf": 262144}


def flag(args: tuple[str, ...], *spellings: str) -> int | None:
    for i, word in enumerate(args[:-1]):
        if word in spellings:
            return int(args[i + 1])
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
        assert model.ctx == NATIVE_CTX[model.source.file], model.name


def test_embedding_models_take_a_whole_input_in_one_ubatch():
    # An embedding model reads its input with non-causal attention, all in one micro-batch: an input longer than
    # --ubatch-size is refused, so the micro-batch holds the whole context.
    embed = [m for m in load_registry(STACK_REGISTRY).models.values() if m.capability == "embeddings"]
    assert embed
    for model in embed:
        assert (flag(model.args, "-ub", "--ubatch-size") or 0) >= model.ctx, model.name
