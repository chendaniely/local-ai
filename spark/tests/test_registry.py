import datetime
import re
from pathlib import Path

import pytest
import yaml

from spark.registry import (
    NOTIFICATION_TYPES,
    PRIORITIES,
    ClientKey,
    GateSettings,
    KeyGroup,
    RegistryError,
    load_keys,
    load_registry,
)

FIXTURE = Path(__file__).parent / "fixtures" / "models.yaml"
KEYS = Path(__file__).parent / "fixtures" / "keys.yaml"


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
        # Phase 2a's sections (Task 4): each value of the type its field has, and nothing the section doesn't hold.
        pytest.param(
            lambda d: d["budget"].pop("idle_available_gib"),
            "budget: idle_available_gib is required",
            id="idle-available-missing",
        ),
        pytest.param(
            lambda d: d["budget"].update(allocatable_measured="no"),
            "budget: allocatable_measured must be true or false",
            id="measured-quoted",
        ),
        pytest.param(lambda d: d.update(key_groups=["dan"]), "key_groups: must be a mapping", id="groups-a-list"),
        pytest.param(
            lambda d: d["key_groups"].update(dan="30"),
            "key_groups: dan: a group must be a mapping",
            id="group-a-string",
        ),
        pytest.param(
            lambda d: d["key_groups"].update(Robots=d["key_groups"].pop("agent")),
            "key_groups: 'Robots': names are lowercase letters, digits and '-'",
            id="group-name-uppercase",
        ),
        pytest.param(
            lambda d: d["key_groups"]["agent"].update(wait=600),
            "key_groups: agent: 'wait' is not one of",
            id="group-key-misspelled",
        ),
        pytest.param(
            lambda d: d["key_groups"]["agent"].pop("uses_hold"),
            "key_groups: agent: uses_hold is required",
            id="group-flag-missing",
        ),
        pytest.param(
            lambda d: d["key_groups"]["agent"].update(reloads_marked="false"),
            "key_groups: agent: reloads_marked must be true or false",
            id="group-flag-quoted",
        ),
        pytest.param(
            lambda d: d["key_groups"]["agent"].update(queue=1.5),
            "key_groups: agent: queue must be a whole number",
            id="queue-a-fraction",
        ),
        pytest.param(
            lambda d: d["key_groups"]["agent"].update(max_open="32"),
            "key_groups: agent: max_open must be a whole number",
            id="max-open-quoted",
        ),
        pytest.param(lambda d: d.update(notifications=None), "notifications: must be a mapping", id="notify-null"),
        pytest.param(lambda d: d.update(gate=60), "gate: must be a mapping", id="gate-a-number"),
        pytest.param(lambda d: d["gate"].update(idle_min=60), "gate: 'idle_min' is not one of", id="gate-key-unknown"),
        pytest.param(
            lambda d: d["gate"].pop("idle_unload_min"), "gate: idle_unload_min is required", id="idle-missing"
        ),
        pytest.param(
            lambda d: d["gate"].update(idle_unload_min=0),
            "gate: idle_unload_min must be a positive whole number of minutes",
            id="idle-zero",
        ),
        pytest.param(
            lambda d: d["gate"].update(idle_unload_min="60"),
            "gate: idle_unload_min must be a whole number",
            id="idle-quoted",
        ),
        pytest.param(
            lambda d: d["gate"].update(owed_reads_rss=False),
            "gate: owed_reads_rss must be a mapping of each engine kind to true or false",
            id="owed-a-flag",
        ),
        pytest.param(
            lambda d: d["gate"]["owed_reads_rss"].update({"llama.cpp": "no"}),
            "gate: owed_reads_rss: llama.cpp must be true or false",
            id="owed-quoted",
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
        pytest.param([("  stt:\n", "  embed:\n")], "'embed' is repeated on line 33", id="model-twice"),
        pytest.param(
            [("    ctx: 131072\n", "    ctx: 131072\n    ctx: 8192\n")],
            "'ctx' is repeated on line 54",
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


# Task 6's fix round 2: a model's files come from its pinned snapshot, and from nowhere else.

OUTSIDE = [  # (the source field, a value that leaves the pinned snapshot, or wouldn't reach it as one path)
    pytest.param("file", "../../../../../../../tmp/other.gguf", id="file climbs out"),  # --model at /var/tmp/other.gguf
    pytest.param("file", "Q4_K_M/../../other.gguf", id="file climbs out of a folder"),
    pytest.param("file", "/tmp/other.gguf", id="file absolute"),
    pytest.param("file", "vision gguf", id="file with whitespace"),
    pytest.param("file", "${env.LLAMASWAP_KEY_AGENT}.bin", id="file with a reference"),
    pytest.param("file", "vision\\.gguf", id="file with a backslash"),
    pytest.param("file", "vision.gguf\n", id="file with a trailing newline"),
    pytest.param("mmproj", "../vision-mmproj.gguf", id="mmproj climbs out"),
    pytest.param("mmproj", "/tmp/vision-mmproj.gguf", id="mmproj absolute"),
]


@pytest.mark.parametrize("key, value", OUTSIDE)
def test_a_source_file_stays_inside_the_pinned_snapshot(tmp_path, key, value):
    path = mutated(tmp_path, lambda d: d["models"]["vision-chat"]["source"].update({key: value}))
    with pytest.raises(RegistryError, match=f"^vision-chat: source.{key} must be a relative path inside the pinned "):
        load_registry(path)


def test_a_source_file_may_sit_in_a_folder_of_the_repo(tmp_path):
    file = "Q4_K_M/vision-00001-of-00002.gguf"
    path = mutated(tmp_path, lambda d: d["models"]["vision-chat"]["source"].update(file=file))
    assert load_registry(path).models["vision-chat"].source.file == file


REPOS = [  # none is a Hugging Face repo id, org/name
    pytest.param("vision-GGUF", id="no org"),  # render's model_path raised a bare ValueError on it
    pytest.param("example-org/vision/GGUF", id="three parts"),
    pytest.param("/example-org/vision-GGUF", id="absolute"),
    pytest.param("../example-org/vision-GGUF", id="climbs out"),
    pytest.param("example-org/vision GGUF", id="whitespace"),
    pytest.param("example--org/vision-GGUF", id="a double dash"),  # the Hub cache's separator: models--org--name
    pytest.param("example-org/vision..GGUF", id="a double dot"),
    pytest.param("example-org/vision-GGUF\n", id="a trailing newline"),
    pytest.param("example-org/${env.LLAMASWAP_KEY_AGENT}", id="a reference"),
]


@pytest.mark.parametrize("repo", REPOS)
def test_a_source_repo_is_org_and_name(tmp_path, repo):
    path = mutated(tmp_path, lambda d: d["models"]["vision-chat"]["source"].update(repo=repo))
    with pytest.raises(RegistryError, match="^vision-chat: source.repo must be org/name"):
        load_registry(path)


@pytest.mark.parametrize("change, message", [
    pytest.param(lambda d: d["models"]["vision-chat"]["source"].update(revision="1" * 40 + "\n"),
                 "vision-chat: source.revision must be a 40-hex commit", id="revision"),
    pytest.param(lambda d: d["models"].update({"coder\n": d["models"].pop("coder")}),
                 "'coder\\n': names are lowercase letters", id="model name"),
])
def test_a_trailing_newline_never_passes(tmp_path, change, message):
    # A pattern's `$` matches just before a final newline, so each is matched whole.
    with pytest.raises(RegistryError, match=f"^{re.escape(message)}"):
        load_registry(mutated(tmp_path, change))


# Phase 2a's Task 4: each model's label, the key groups, the notifications, the gate's settings, and the key list,
# which lives in a private file of its own.

def refused(path: Path, message: str) -> None:
    """load_registry refuses the registry at `path` with a message that starts with `message`."""
    with pytest.raises(RegistryError, match="^" + re.escape(message)):
        load_registry(path)


def test_the_fixture_loads_its_2a_sections():
    registry = load_registry(FIXTURE)
    assert registry.key_groups["agent"] == KeyGroup("agent", 600, 1, False, False, "agent", False, 4, 32)
    assert registry.gate == GateSettings(60, {"llama.cpp": False, "whisper.cpp": False})
    assert registry.budget.idle_available_gib == 117
    assert registry.budget.allocatable_measured is False  # the fixture leaves it out: false by default
    assert registry.notifications["brake_fired"] == "high"
    assert registry.models["coder"].label == "the coder"
    assert registry.models["coder"].needs_room is False


def keys_mutated(tmp_path, change) -> Path:
    data = yaml.safe_load(KEYS.read_text())
    change(data)
    path = tmp_path / "keys.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_the_key_list_loads_from_its_own_file():
    keys = load_keys(KEYS, load_registry(FIXTURE).key_groups)
    assert set(keys) == {"dan-mac", "open-webui", "agent"}
    assert keys["dan-mac"] == ClientKey("dan-mac", "dan", "pi on the Mac", None)
    assert keys["agent"].account == "agent"


def test_a_key_must_name_a_group_that_exists(tmp_path):
    path = keys_mutated(tmp_path, lambda d: d["keys"]["agent"].update(group="robots"))
    with pytest.raises(RegistryError, match="^" + re.escape("keys: agent: group 'robots' is not one of dan, agent")):
        load_keys(path, load_registry(FIXTURE).key_groups)


# The key list is written by hand on the box, as the registry is: a slip comes back naming the key and the field.
@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(lambda d: d["keys"]["agent"].pop("label"), "keys: agent: label is required", id="no-label"),
        pytest.param(
            lambda d: d["keys"]["agent"].update(label=""),
            "keys: agent: label must be text on one line, not ''",
            id="label-empty",
        ),
        pytest.param(lambda d: d["keys"]["agent"].pop("group"), "keys: agent: group is required", id="no-group"),
        pytest.param(
            lambda d: d["keys"]["agent"].update(account=""),
            "keys: agent: account must be text on one line, not ''",
            id="account-empty",
        ),
        pytest.param(
            lambda d: d["keys"]["agent"].update(acount="agent"),
            "keys: agent: 'acount' is not one of group, label, account",
            id="key-field-misspelled",
        ),
        pytest.param(
            lambda d: d["keys"].update({"Dan-Mac": d["keys"].pop("dan-mac")}),
            "keys: 'Dan-Mac': names are lowercase letters, digits and '-'",
            id="name-uppercase",
        ),
        pytest.param(
            lambda d: d["keys"].update({"dan.mac": d["keys"].pop("dan-mac")}),
            "keys: 'dan.mac': names are lowercase letters, digits and '-'",
            id="name-a-dot",
        ),
        pytest.param(
            lambda d: d["keys"].update({2024: d["keys"].pop("dan-mac")}),
            "keys: 2024: names are lowercase letters, digits and '-'",
            id="name-a-number",
        ),
        pytest.param(
            lambda d: d["keys"].update(agent="agent"),
            "keys: agent: a key must be a mapping of group, label, account",
            id="key-a-string",
        ),
        pytest.param(lambda d: d.update(keys=["agent"]), "keys: must be a mapping", id="keys-a-list"),
        pytest.param(lambda d: d.pop("keys"), "keys: the list is required", id="no-keys"),
        pytest.param(
            lambda d: d.update(key_groups={}),
            "the key list: 'key_groups' is not one of keys",
            id="groups-in-the-key-list",
        ),
    ],
)
def test_a_malformed_key_list_names_the_key_and_the_field(tmp_path, change, message):
    with pytest.raises(RegistryError, match="^" + re.escape(message)):
        load_keys(keys_mutated(tmp_path, change), load_registry(FIXTURE).key_groups)


def test_a_key_list_that_isnt_a_mapping_or_repeats_a_name_is_refused(tmp_path):
    path = tmp_path / "keys.yaml"
    path.write_text("- dan-mac\n")
    with pytest.raises(RegistryError, match="^the key list must be a mapping"):
        load_keys(path, load_registry(FIXTURE).key_groups)
    text = KEYS.read_text()
    assert text.count("  agent:") == 1
    path.write_text(text.replace("  agent:", "  dan-mac:"))  # YAML would keep the second dan-mac alone
    with pytest.raises(RegistryError, match="^'dan-mac' is repeated on line"):
        load_keys(path, load_registry(FIXTURE).key_groups)


def test_the_key_list_loader_refuses_python_tags_rather_than_running_them(tmp_path):
    # As the registry's: under yaml's unsafe loaders this harmless call would run, and load as the label 2.
    path = tmp_path / "keys.yaml"
    path.write_text("keys:\n  agent: {group: agent, label: !!python/object/apply:builtins.len [[1, 2]]}\n")
    with pytest.raises(yaml.YAMLError, match="python/object/apply"):
        load_keys(path, load_registry(FIXTURE).key_groups)


def test_the_registry_holds_no_key_list(tmp_path):
    keys = {"dan-mac": {"group": "dan", "label": "pi on the Mac"}}
    with pytest.raises(RegistryError, match=re.escape("/etc/local-ai/keys.yaml")):
        load_registry(mutated(tmp_path, lambda d: d.update(keys=keys)))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(lambda m: m.pop("label"), "coder: label is required", id="absent"),
        pytest.param(lambda m: m.update(label=""), "coder: label must be text on one line, not ''", id="empty"),
        pytest.param(lambda m: m.update(label="  "), "coder: label must be text on one line, not '  '", id="blank"),
        pytest.param(lambda m: m.update(label=3), "coder: label must be text on one line, not 3", id="a-number"),
        pytest.param(
            lambda m: m.update(label="the\ncoder"),
            "coder: label must be text on one line, not 'the\\ncoder'",
            id="two-lines",
        ),
    ],
)
def test_every_model_needs_a_plain_words_label(tmp_path, change, message):
    refused(mutated(tmp_path, lambda d: change(d["models"]["coder"])), message)


def group_changed(tmp_path, **change) -> Path:
    return mutated(tmp_path, lambda d: d["key_groups"]["agent"].update(change))


def test_a_groups_words_are_dan_or_agent(tmp_path):
    refused(group_changed(tmp_path, words="guest"), "key_groups: agent: words must be dan or agent, not 'guest'")


@pytest.mark.parametrize(
    ("queue", "message"),
    [
        pytest.param(-1, "key_groups: agent: queue must be a whole number from 0, not -1", id="negative"),
        pytest.param("0", "key_groups: agent: queue must be a whole number, not '0'", id="quoted"),
    ],
)
def test_a_groups_queue_is_a_whole_number(tmp_path, queue, message):
    refused(group_changed(tmp_path, queue=queue), message)


@pytest.mark.parametrize("waiting", [10, 0])
def test_waiting_caps_stay_below_llama_swaps_ten(tmp_path, waiting):
    refused(group_changed(tmp_path, max_waiting=waiting),
            f"key_groups: agent: max_waiting must be from 1 to 9, below llama-swap's 10 per model, not {waiting}")
    assert load_registry(group_changed(tmp_path, max_waiting=9)).key_groups["agent"].max_waiting == 9


def test_open_connections_cover_the_waiting_ones(tmp_path):
    refused(group_changed(tmp_path, max_open=3, max_waiting=4),
            "key_groups: agent: max_open must be at least max_waiting (4), not 3")
    assert load_registry(group_changed(tmp_path, max_open=4, max_waiting=4)).key_groups["agent"].max_open == 4


@pytest.mark.parametrize(
    ("wait", "message"),
    [
        pytest.param(0, "key_groups: agent: wait_s must be a positive whole number of seconds, not 0", id="zero"),
        pytest.param("30", "key_groups: agent: wait_s must be a whole number, not '30'", id="quoted"),
    ],
)
def test_a_wait_must_be_positive(tmp_path, wait, message):
    refused(group_changed(tmp_path, wait_s=wait), message)


def test_the_notification_list_names_every_type_once(tmp_path):
    refused(mutated(tmp_path, lambda d: d["notifications"].pop("waiting")),
            "notifications: waiting is missing; every notification type is listed, with its priority")
    refused(mutated(tmp_path, lambda d: d["notifications"].update(coffee="low")),
            "notifications: 'coffee' is not one of brake_fired, ")
    refused(mutated(tmp_path, lambda d: d["notifications"].update(refused="loud")),
            "notifications: refused must be high, default, low or off, not 'loud'")
    refused(replaced(tmp_path, ("  waiting: low\n", "  waiting: low\n  waiting: high\n")),
            "'waiting' is repeated on line")


def test_an_unquoted_off_is_refused_and_a_quoted_one_turns_a_notification_off(tmp_path):
    # YAML 1.1 reads an unquoted off as false.
    refused(replaced(tmp_path, ("  pin_ended: low\n", "  pin_ended: off\n")),
            'notifications: pin_ended must be high, default, low or off, not False; quote it: "off"')
    path = replaced(tmp_path, ("  pin_ended: low\n", '  pin_ended: "off"\n'))
    assert load_registry(path).notifications["pin_ended"] == "off"


def test_notification_types_are_the_twenty_in_the_plans_order():
    assert NOTIFICATION_TYPES == (
        "brake_fired", "brake_needs_release", "gate_down", "front_down", "llama_swap_down", "brake_down", "back_up",
        "refused", "footprint_suspect", "load_failed", "brake_released", "room_hold_ended", "resident_waiting",
        "apply_restarted", "load_started", "loaded", "unloaded", "waiting", "pin_ended", "memory_warning",
    )
    assert PRIORITIES == ("high", "default", "low", "off")


def test_idle_available_must_exceed_the_reserve(tmp_path):
    refused(mutated(tmp_path, lambda d: d["budget"].update(idle_available_gib=24, reserve_gib=24)),
            "budget: idle_available_gib (24) must exceed reserve_gib (24)")


def test_used_by_is_optional_text(tmp_path):
    assert load_registry(FIXTURE).models["vision-chat"].used_by is None
    path = mutated(tmp_path, lambda d: d["models"]["vision-chat"].update(used_by="the web UI uses it"))
    assert load_registry(path).models["vision-chat"].used_by == "the web UI uses it"
    refused(mutated(tmp_path, lambda d: d["models"]["vision-chat"].update(used_by=3)),
            "vision-chat: used_by must be text on one line, not 3")


def test_needs_room_is_for_on_demand_models_only(tmp_path):
    refused(mutated(tmp_path, lambda d: d["models"]["vision-chat"].update(needs_room=True)),
            "vision-chat: needs_room is only for an on-demand model, and vision-chat is resident")
    refused(mutated(tmp_path, lambda d: d["models"]["coder"].update(needs_room="yes")),
            "coder: needs_room must be true or false")
    coder = load_registry(mutated(tmp_path, lambda d: d["models"]["coder"].update(needs_room=True))).models["coder"]
    assert coder.needs_room is True


@pytest.mark.parametrize(
    ("change", "message"),
    [
        pytest.param(
            lambda owed: owed.pop("whisper.cpp"),
            "gate: owed_reads_rss must name every engine kind; whisper.cpp is missing",
            id="one-missing",
        ),
        pytest.param(
            lambda owed: owed.update(vllm=False),
            "gate: owed_reads_rss: 'vllm' is not one of llama.cpp, whisper.cpp",
            id="one-no-model-uses",
        ),
    ],
)
def test_owed_reads_rss_names_each_engine_kind(tmp_path, change, message):
    refused(mutated(tmp_path, lambda d: change(d["gate"]["owed_reads_rss"])), message)


@pytest.mark.parametrize("section", ["key_groups", "notifications", "gate"])
def test_a_new_section_may_not_be_missing(tmp_path, section):
    refused(mutated(tmp_path, lambda d: d.pop(section)), f"{section}: the section is required")
