import datetime
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


POSITIVE = "coder: footprint, ctx and parallel must be positive; cache_ram_mib ≥ 0"


# models.yaml is edited by hand: a slip must come back as a RegistryError that starts with the model
# or section and names the field — never a bare KeyError, TypeError or ValueError, never accepted.
@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(lambda m: m.pop("footprint_gib"), "coder: footprint_gib is required", id="no-footprint"),
        pytest.param(lambda m: m.pop("ctx"), "coder: ctx is required", id="no-ctx"),
        pytest.param(lambda m: m["source"].pop("repo"), "coder: source.repo is required", id="no-repo"),
        pytest.param(lambda m: m["source"].pop("file"), "coder: source.file is required", id="no-file"),
        pytest.param(
            lambda m: m.update(source="example-org/coder-GGUF"),
            "coder: source must be a mapping",
            id="source-a-string",
        ),
        pytest.param(
            lambda m: m.update(footprint_gib="big"),
            "coder: footprint_gib must be a number",
            id="footprint-a-word",
        ),
        pytest.param(lambda m: m.update(ctx="lots"), "coder: ctx must be a whole number", id="ctx-a-word"),
        pytest.param(
            lambda m: m.update(parallel="two"), "coder: parallel must be a whole number", id="parallel-a-word"
        ),
        pytest.param(
            lambda m: m.update(cache_ram_mib="x"),
            "coder: cache_ram_mib must be a whole number",
            id="cache-a-word",
        ),
        pytest.param(
            lambda m: m.update(footprint_gib=True),
            "coder: footprint_gib must be a number",
            id="footprint-a-bool",
        ),
        pytest.param(
            lambda m: m.update(footprint_gib="18"),
            "coder: footprint_gib must be a number",
            id="footprint-quoted",
        ),
        pytest.param(lambda m: m.update(ctx=3.5), "coder: ctx must be a whole number", id="ctx-a-fraction"),
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
        # A key the model doesn't have: were it ignored, a misspelled optional key would take its default.
        pytest.param(lambda m: m.update(paralel=4), "coder: 'paralel' is not one of", id="key-misspelled"),
        pytest.param(
            lambda m: m.update(cache_ram_mb=m.pop("cache_ram_mib")),
            "coder: 'cache_ram_mb' is not one of",
            id="key-renamed",
        ),
        pytest.param(
            lambda m: m.update(role=m.pop("roles")),
            "coder: 'role' is not one of",
            id="roles-singular",
        ),
        pytest.param(
            lambda m: m["source"].update(mmporj="coder-mmproj.gguf"),
            "coder: source: 'mmporj' is not one of",
            id="source-key-misspelled",
        ),
        # An args item that is neither text nor a number would reach the engine as its Python str().
        pytest.param(
            lambda m: m.update(args=["--spec-draft-n-max", None]),
            "coder: args may not hold None",
            id="arg-null",
        ),
        pytest.param(
            lambda m: m.update(args=["--since", datetime.date(2026, 9, 26)]),
            "coder: args may not hold datetime.date(2026, 9, 26)",
            id="arg-a-date",
        ),
        # A flag that isn't a YAML boolean: bool() reads the string "false", or "n", as true.
        pytest.param(
            lambda m: m.update(resident="false"), "coder: resident must be true or false", id="resident-quoted"
        ),
        pytest.param(
            lambda m: m.update(resident="n"),
            "coder: resident must be true or false",
            id="resident-n",
        ),
        pytest.param(
            lambda m: m.update(resident=None),
            "coder: resident must be true or false",
            id="resident-null",
        ),
        pytest.param(
            lambda m: m.update(footprint_measured="no"),
            "coder: footprint_measured must be true or false",
            id="measured-quoted",
        ),
        # A source repo or file that isn't a non-empty string; mmproj too, when it's there.
        pytest.param(
            lambda m: m["source"].update(repo=["a", "b"]),
            "coder: source.repo must be a string",
            id="repo-a-list",
        ),
        pytest.param(
            lambda m: m["source"].update(file=5),
            "coder: source.file must be a string",
            id="file-a-number",
        ),
        pytest.param(
            lambda m: m["source"].update(mmproj=""),
            "coder: source.mmproj must be a non-empty string",
            id="mmproj-empty",
        ),
        pytest.param(
            lambda m: m["source"].update(mmproj=None),
            "coder: source.mmproj must be a non-empty string",
            id="mmproj-null",
        ),
        # A number that isn't finite: nan <= 0 is false, so a nan footprint passed every size check.
        pytest.param(
            lambda m: m.update(footprint_gib=float("nan")),
            "coder: footprint_gib must be a finite number",
            id="footprint-nan",
        ),
        pytest.param(
            lambda m: m.update(footprint_gib=float("inf")),
            "coder: footprint_gib must be a finite number",
            id="footprint-inf",
        ),
        # The positive-values check, one case per clause.
        pytest.param(lambda m: m.update(footprint_gib=0), POSITIVE, id="footprint-zero"),
        pytest.param(lambda m: m.update(ctx=0), POSITIVE, id="ctx-zero"),
        pytest.param(lambda m: m.update(parallel=0), POSITIVE, id="parallel-zero"),
        pytest.param(lambda m: m.update(cache_ram_mib=-1), POSITIVE, id="cache-negative"),
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
        pytest.param(lambda d: d["brake"].update(x=1), "brake: 'x' is not one of", id="brake-key-unknown"),
        pytest.param(
            lambda d: d["budget"].update(allocatable_gib="lots"),
            "budget: allocatable_gib must be a number",
            id="budget-a-word",
        ),
        pytest.param(
            lambda d: d["brake"].update(poll_ms="fast"),
            "brake: poll_ms must be a whole number",
            id="brake-a-word",
        ),
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
        # A key that isn't a section, or a source key outside its source: a vision model lost its projector.
        pytest.param(
            lambda d: d.update(model=d.pop("models")),
            "the registry: 'model' is not one of",
            id="section-misspelled",
        ),
        pytest.param(
            lambda d: d["models"]["vision-chat"].update(
                mmproj=d["models"]["vision-chat"]["source"].pop("mmproj")
            ),
            "vision-chat: 'mmproj' is not one of",
            id="mmproj-outside-source",
        ),
        # An engine without a path to its binary would be run as "None".
        pytest.param(
            lambda d: d["engines"].update({"llama.cpp": None}),
            "engines: llama.cpp must be the path to its binary",
            id="engine-path-null",
        ),
        pytest.param(
            lambda d: d["engines"].update({"llama.cpp": ""}),
            "engines: llama.cpp must be the path to its binary",
            id="engine-path-empty",
        ),
        # Budget and brake values: finite, positive, and poll_ms whole.
        pytest.param(
            lambda d: d["budget"].update(allocatable_gib=float("inf")),
            "budget: allocatable_gib must be a finite number",
            id="budget-inf",
        ),
        pytest.param(
            lambda d: d["budget"].update(allocatable_gib=0),
            "budget: allocatable_gib must be positive",
            id="budget-zero",
        ),
        pytest.param(
            lambda d: d["brake"].update(poll_ms=0),
            "brake: poll_ms must be positive",
            id="poll-ms-zero",
        ),
        pytest.param(
            lambda d: d["brake"].update(poll_ms=250.5),
            "brake: poll_ms must be a whole number",
            id="poll-ms-a-fraction",
        ),
        pytest.param(
            lambda d: d["brake"].update(warn_gib=19),
            "brake: warn_gib must be above brake_gib",
            id="warn-below-brake",
        ),
        pytest.param(
            lambda d: d["models"].update(Coder=d["models"].pop("coder")),
            "'Coder': names are lowercase letters, digits, '.' and '-'",
            id="name-uppercase",
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


# Some slips show only when YAML reads the text a hand edit wrote. YAML 1.1, which PyYAML reads, takes an
# unquoted on/off, yes/no or true/false as a boolean, an empty list item as null, and a key given twice as
# the last one alone. These tests write the YAML text, so YAML does the reading.
def replaced(tmp_path, *swaps: tuple[str, str]) -> Path:
    """The fixture's text with each (old, new) swapped in; each old must occur exactly once."""
    text = FIXTURE.read_text()
    for old, new in swaps:
        assert text.count(old) == 1, old  # so a swap can't silently miss
        text = text.replace(old, new)
    path = tmp_path / "models.yaml"
    path.write_text(text)
    return path


CODER_ARGS = '    args: [--load-mode, none, --spec-type, draft-mtp, --spec-draft-n-max, "3"]\n'


def with_coder_args(tmp_path, args: str) -> Path:
    return replaced(tmp_path, (CODER_ARGS, f"    args: {args}\n"))


@pytest.mark.parametrize("word", ["on", "off", "yes", "no", "true", "false"])
def test_an_unquoted_boolean_in_args_is_refused(tmp_path, word):
    path = with_coder_args(tmp_path, f"[--flash-attn, {word}]")
    with pytest.raises(RegistryError, match=r"^coder: args may not hold a boolean \((True|False)\); quote "):
        load_registry(path)


def test_a_quoted_boolean_or_a_bare_number_in_args_loads_as_text(tmp_path):
    path = with_coder_args(tmp_path, '[--flash-attn, "on", --threads, 8]')
    assert load_registry(path).models["coder"].args == ("--flash-attn", "on", "--threads", "8")


@pytest.mark.parametrize(
    ("swaps", "message"),
    [
        pytest.param([("  stt:\n", "  embed:\n")], "'embed' is repeated on line 31", id="model-twice"),
        pytest.param(
            [("    ctx: 131072\n", "    ctx: 131072\n    ctx: 8192\n")],
            "'ctx' is repeated on line 50",
            id="field-twice",
        ),
        pytest.param([("  coder:\n", "  2024:\n")], "2024: names are lowercase", id="name-a-number"),
        pytest.param([("  coder:\n", "  on:\n")], "True: names are lowercase", id="name-a-boolean"),
        pytest.param(
            [(CODER_ARGS, "    args:\n      - --spec-draft-n-max\n      -\n")],
            "coder: args may not hold None",
            id="arg-an-empty-item",
        ),
    ],
)
def test_a_slip_that_only_yaml_reading_shows_is_refused(tmp_path, swaps, message):
    with pytest.raises(RegistryError, match="^" + re.escape(message)):
        load_registry(replaced(tmp_path, *swaps))


def test_a_merge_key_override_is_not_a_repeated_key(tmp_path):
    # `<<: *vision` copies vision-chat's keys into coder, and coder's own keys override every one of them.
    swaps = [("  vision-chat:\n", "  vision-chat: &vision\n"), ("  coder:\n", "  coder:\n    <<: *vision\n")]
    assert load_registry(replaced(tmp_path, *swaps)) == load_registry(FIXTURE)


def test_the_loader_refuses_python_tags_rather_than_running_them(tmp_path):
    # The loader that catches repeated keys must stay a SafeLoader: this harmless call would run under yaml's
    # unsafe loaders, and load as the argument 2.
    path = with_coder_args(tmp_path, "[--x, !!python/object/apply:builtins.len [[1, 2]]]")
    with pytest.raises(yaml.YAMLError, match="python/object/apply"):
        load_registry(path)


# Task 6's fix round 1: llama-swap v257 looks a name up among the models before the roles (Config.RealModelName).

def test_a_role_may_not_be_another_models_name(tmp_path):
    # Requests for it would reach vision-chat, never coder.
    path = mutated(tmp_path, lambda d: d["models"]["coder"].update(roles=["coder", "vision-chat"]))
    with pytest.raises(RegistryError, match="^coder: role 'vision-chat' is another model's name"):
        load_registry(path)


def test_a_role_may_repeat_its_own_models_name():
    # The fixture's embed and stt do: llama-swap finds the same model either way.
    registry = load_registry(FIXTURE)
    assert "embed" in registry.models["embed"].roles and "stt" in registry.models["stt"].roles
