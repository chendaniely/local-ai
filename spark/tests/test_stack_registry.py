from pathlib import Path

from spark.registry import load_registry

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"
LLAMA_CPP_BATCH_DEFAULT = 2048  # llama-server b11146's --batch-size default; it caps --ubatch-size


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
