import re
from pathlib import Path

import pytest
import yaml

from spark.registry import RegistryError, load_registry

FIXTURE = Path(__file__).parent / "fixtures" / "models.yaml"


def mutated(tmp_path, change) -> Path:
    data = yaml.safe_load(FIXTURE.read_text())
    change(data)
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_loads_the_fixture():
    registry = load_registry(FIXTURE)
    assert set(registry.models) == {"vision-chat", "embed", "stt", "coder"}
    assert registry.models["vision-chat"].source.mmproj == "vision-mmproj.gguf"
    assert registry.models["coder"].resident is False
    assert registry.static_total_gib() == 50.0


def test_revision_must_be_a_pinned_commit(tmp_path):
    path = mutated(tmp_path, lambda d: d["models"]["embed"]["source"].update(revision="main"))
    with pytest.raises(RegistryError, match="revision"):
        load_registry(path)


def test_engine_must_serve_the_capability(tmp_path):
    path = mutated(tmp_path, lambda d: d["models"]["stt"].update(engine="llama.cpp"))
    with pytest.raises(RegistryError, match="capability"):
        load_registry(path)


def test_roles_are_unique(tmp_path):
    path = mutated(tmp_path, lambda d: d["models"]["coder"].update(roles=["small"]))
    with pytest.raises(RegistryError, match="role"):
        load_registry(path)


def test_reserve_must_exceed_the_brake(tmp_path):
    path = mutated(tmp_path, lambda d: d["budget"].update(reserve_gib=18))
    with pytest.raises(RegistryError, match="reserve"):
        load_registry(path)


def test_args_may_not_contain_whitespace_or_quotes(tmp_path):
    path = mutated(tmp_path, lambda d: d["models"]["coder"].update(args=["--x", "a b"]))
    with pytest.raises(RegistryError, match="args"):
        load_registry(path)


# models.yaml is edited by hand: a slip must come back as a RegistryError that starts with the model
# or section and names the field — never a bare KeyError, TypeError or ValueError, never accepted.
@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(lambda m: m.pop("footprint_gib"), "coder: footprint_gib", id="no-footprint"),
        pytest.param(lambda m: m.pop("ctx"), "coder: ctx", id="no-ctx"),
        pytest.param(lambda m: m["source"].pop("repo"), "coder: source.repo", id="no-repo"),
        pytest.param(lambda m: m["source"].pop("file"), "coder: source.file", id="no-file"),
        pytest.param(
            lambda m: m.update(source="example-org/coder-GGUF"),
            "coder: source must be a mapping",
            id="source-a-string",
        ),
        pytest.param(lambda m: m.update(footprint_gib="big"), "coder: footprint_gib", id="footprint-a-word"),
        pytest.param(lambda m: m.update(ctx="lots"), "coder: ctx", id="ctx-a-word"),
        pytest.param(lambda m: m.update(parallel="two"), "coder: parallel", id="parallel-a-word"),
        pytest.param(lambda m: m.update(cache_ram_mib="x"), "coder: cache_ram_mib", id="cache-a-word"),
        pytest.param(lambda m: m.update(footprint_gib=True), "coder: footprint_gib", id="footprint-a-bool"),
        pytest.param(lambda m: m.update(footprint_gib="18"), "coder: footprint_gib", id="footprint-quoted"),
        pytest.param(lambda m: m.update(ctx=3.5), "coder: ctx", id="ctx-a-fraction"),
        pytest.param(
            lambda m: m.update(capability=["chat", "embeddings"]), "coder: capability", id="capability-a-list"
        ),
        pytest.param(lambda m: m.update(engine=["llama.cpp"]), "coder: engine", id="engine-a-list"),
        pytest.param(lambda m: m.update(roles="coder"), "coder: roles must be a list", id="roles-a-string"),
        pytest.param(lambda m: m.update(args="--foo"), "coder: args must be a list", id="args-a-string"),
        pytest.param(lambda m: m.update(args=None), "coder: args must be a list", id="args-null"),
        pytest.param(
            lambda m: m.update(roles=[{"coder": True}]),
            "coder: roles must be a list of strings",
            id="role-a-mapping",
        ),
        pytest.param(
            lambda m: m.update(roles=[["coder"]]), "coder: roles must be a list of strings", id="role-a-list"
        ),
        pytest.param(
            lambda m: m.update(roles=["coder", None]), "coder: roles must be a list of strings", id="role-null"
        ),
        pytest.param(
            lambda m: m.update(args=["--ubatch-size", [8192]]),
            "coder: args must be a flat list",
            id="arg-a-list",
        ),
        pytest.param(
            lambda m: m.update(args=["--chat-template-kwargs", {"enable_thinking": False}]),
            "coder: args must be a flat list",
            id="arg-a-mapping",
        ),
    ],
)
def test_a_malformed_model_names_the_model_and_the_field(tmp_path, change, message):
    path = mutated(tmp_path, lambda d: change(d["models"]["coder"]))
    with pytest.raises(RegistryError, match="^" + re.escape(message)):
        load_registry(path)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(lambda d: d.pop("budget"), "budget: the section is required", id="no-budget"),
        pytest.param(lambda d: d.pop("brake"), "brake: the section is required", id="no-brake"),
        pytest.param(lambda d: d.update(budget=102), "budget: must be a mapping", id="budget-a-number"),
        pytest.param(
            lambda d: d["budget"].pop("reserve_gib"),
            "budget: reserve_gib is required",
            id="budget-key-missing",
        ),
        pytest.param(lambda d: d["brake"].update(x=1), "brake: 'x'", id="brake-key-unknown"),
        pytest.param(
            lambda d: d["budget"].update(allocatable_gib="lots"), "budget: allocatable_gib", id="budget-a-word"
        ),
        pytest.param(lambda d: d["brake"].update(poll_ms="fast"), "brake: poll_ms", id="brake-a-word"),
        pytest.param(
            lambda d: d.update(engines=["llama.cpp"]), "engines: must be a mapping", id="engines-a-list"
        ),
        pytest.param(lambda d: d.update(models=["coder"]), "models: must be a mapping", id="models-a-list"),
        pytest.param(lambda d: d.update(models=None), "models: must be a mapping", id="models-null"),
        pytest.param(
            lambda d: d["models"].update(coder="example-org/coder-GGUF"),
            "coder: a model must be a mapping",
            id="model-a-string",
        ),
    ],
)
def test_a_malformed_section_names_the_section_and_the_field(tmp_path, change, message):
    path = mutated(tmp_path, change)
    with pytest.raises(RegistryError, match="^" + re.escape(message)):
        load_registry(path)


def test_the_registry_must_be_a_mapping(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("- budget\n- brake\n")
    with pytest.raises(RegistryError, match="^the registry must be a mapping"):
        load_registry(path)


def test_absent_roles_and_args_are_empty(tmp_path):
    def drop(d):
        del d["models"]["coder"]["roles"], d["models"]["coder"]["args"]

    coder = load_registry(mutated(tmp_path, drop)).models["coder"]
    assert coder.roles == () and coder.args == ()
