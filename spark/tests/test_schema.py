import hashlib
import json
import shutil
from pathlib import Path

import pytest

import spark.render
from spark import schema
from spark.registry import load_registry
from spark.render import RenderError, llama_swap_config, render
from spark.versions import load_versions

ROOT = Path(__file__).resolve().parents[2]
FIX = Path(__file__).parent / "fixtures"
SCHEMA_FILE = ROOT / "stack/llama-swap/config-schema.v257.json"
# llama-swap v257's own config-schema.json, https://raw.githubusercontent.com/mostlygeek/llama-swap/v257/config-schema.json:
# its SHA-256 as read on 2026-10-07, and again on the Spark on 2026-10-14, when Task 24 vendored it.
V257_SHA256 = "d75ebf1e18806194ce674ebd59cb70b6f8bf93cb6ec65c2392e0e17fc9d0b076"
V257 = json.loads(SCHEMA_FILE.read_text())


def config_of(path=ROOT / "stack/models.yaml"):
    return llama_swap_config(load_registry(path))


def test_the_vendored_schema_is_v257s():
    assert hashlib.sha256(SCHEMA_FILE.read_bytes()).hexdigest() == V257_SHA256
    assert SCHEMA_FILE.name == f"config-schema.{spark.render.TESTED_AGAINST['llama-swap']}.json"


@pytest.mark.parametrize("path", [ROOT / "stack/models.yaml", FIX / "models.yaml"], ids=["real", "fixture"])
def test_the_rendered_config_passes_v257s_schema(path):
    assert schema.check(config_of(path), V257) == []


def test_a_mistyped_group_key_fails_the_schema_check():
    # v257 decodes its config with yaml.v3, which drops a key it doesn't know, and its schema leaves a group open: a
    # group whose `swap` is misspelled would pass both, and swap at v257's default, true, so loads would evict each
    # other. The check reads an object whose schema lists its keys as closed.
    config = config_of()
    group = config["routing"]["router"]["settings"]["groups"]["stack"]
    group["swapp"] = group.pop("swap")
    problems = schema.check(config, V257)
    assert len(problems) == 1, problems
    assert problems[0].startswith("/routing/router/settings/groups/stack/swapp: ") and "'swapp'" in problems[0]


def test_a_wrong_enum_fails_the_schema_check():
    problems = schema.check(config_of() | {"logToStdout": "everything"}, V257)
    assert len(problems) == 1, problems
    assert problems[0].startswith("/logToStdout: ") and "'everything'" in problems[0] and "proxy" in problems[0]


# A schema that uses every keyword and annotation the check knows, and a config that passes it.
EVERY = {
    "$schema": "https://json-schema.org/draft-07/schema#", "$id": "https://example.org/every", "title": "every",
    "description": "each keyword once", "default": {},
    "definitions": {"name": {"type": "string", "minLength": 1, "maxLength": 8, "pattern": "^[a-z]+$"}},
    "type": "object",
    "required": ["name"],
    "minProperties": 1,
    "properties": {
        "name": {"$ref": "#/definitions/name"},
        "port": {"type": "integer", "minimum": 1},
        "url": {"type": "string", "format": "uri"},
        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
        "mode": {"enum": ["a", "b"]},
        "either": {"oneOf": [{"type": "string"}, {"type": "null"}]},
        "map": {"type": "object", "additionalProperties": {"type": "integer"},
                "propertyNames": {"not": {"enum": ["PORT"]}}},
    },
    "additionalProperties": False,
    "dependencies": {"url": ["port"], "either": {"required": ["mode"]}},
    "allOf": [{"if": {"required": ["mode"]}, "then": {"required": ["tags"]}}],
}
GOOD = {"name": "gemma", "port": 800, "url": "not a uri", "tags": ["a"], "mode": "a", "either": None, "map": {"x": 1}}


def test_an_unknown_keyword_fails_closed():
    # A keyword the check doesn't know is refused, wherever it is and whatever the config: a later schema's new keyword
    # would otherwise be skipped, and what it forbids let through.
    for where in ({"dependentSchemas": {}}, {"properties": {"a": {"dependentSchemas": {}}}},
                  {"definitions": {"unused": {"else": {}}}}, {"items": {"contains": {}}}):
        for config in ({}, {"a": 1}, [1], None):
            problems = schema.check(config, where)
            assert len(problems) == 1, (where, config, problems)
            assert "dependentSchemas" in problems[0] or "else" in problems[0] or "contains" in problems[0]
    assert schema.check(GOOD, EVERY) == []


def test_dependencies_are_checked_in_both_forms():
    # v257's cors: every other setting needs allowedOrigins beside it (the array form).
    problems = schema.check(config_of() | {"security": {"cors": {"allowCredentials": True}}}, V257)
    assert len(problems) == 1, problems
    assert problems[0].startswith("/security/cors: ") and "'allowedOrigins'" in problems[0]
    assert schema.check(config_of() | {"security": {"cors": {"allowCredentials": True, "allowedOrigins": ["*"]}}},
                        V257) == []
    # The schema form: with `either`, the object must also pass {"required": ["mode"]}.
    problems = schema.check({key: value for key, value in GOOD.items() if key != "mode"}, EVERY)
    assert problems == ["/: needs 'mode'"]


REMOVE = object()  # in a change to GOOD: the key goes
EACH = [  # (a change to GOOD, a problem it must cause): each keyword, seen to refuse
    pytest.param({"port": "800"}, "/port: '800' is a string, not integer", id="type"),
    pytest.param({"port": True}, "/port: True is a boolean, not integer", id="type: a boolean is no integer"),
    pytest.param({"port": 0}, "/port: 0 is under its minimum, 1", id="minimum"),
    pytest.param({"name": "Gemma"}, "/name: 'Gemma' doesn't match ^[a-z]+$", id="$ref and pattern"),
    pytest.param({"name": "gemma\n"}, "/name: 'gemma\\n' doesn't match ^[a-z]+$", id="pattern: $ is the end"),
    pytest.param({"name": ""}, "/name: shorter than 1 character", id="minLength"),
    pytest.param({"name": "abcdefghi"}, "/name: longer than 8 characters", id="maxLength"),
    pytest.param({"tags": [1]}, "/tags/0: 1 is a number, not string", id="items"),
    pytest.param({"tags": []}, "/tags: fewer than 1 item", id="minItems"),
    pytest.param({"tags": ["a", "a"]}, "/tags: holds 'a' more than once", id="uniqueItems"),
    pytest.param({"mode": "c"}, "/mode: 'c' isn't one of 'a', 'b'", id="enum"),
    pytest.param({"either": 1}, "/either: matches 0 of oneOf's 2 schemas, not exactly one", id="oneOf"),
    pytest.param({"extra": 1}, "/extra: 'extra' isn't a key the schema allows here", id="additionalProperties: false"),
    pytest.param({"map": {"x": "1"}}, "/map/x: '1' is a string, not integer", id="additionalProperties: a schema"),
    pytest.param({"map": {"PORT": 1}}, "/map: the key 'PORT': matches what the schema forbids", id="propertyNames, not"),
    pytest.param({"port": REMOVE}, "/: 'url' needs 'port' beside it", id="dependencies: an array"),
    pytest.param({"tags": REMOVE}, "/: needs 'tags'", id="allOf, if and then"),
    pytest.param({"name": REMOVE}, "/: needs 'name'", id="required"),
]


@pytest.mark.parametrize("change, problem", EACH)
def test_each_keyword_is_checked(change, problem):
    config = {key: value for key, value in (GOOD | change).items() if value is not REMOVE}
    assert problem in schema.check(config, EVERY)


def test_min_properties_one_of_twice_and_boolean_schemas():
    assert schema.check({}, {"type": "object", "minProperties": 1}) == ["/: fewer than 1 key"]
    assert schema.check(1, {"oneOf": [{"type": "integer"}, {"type": "number"}]}) == [
        "/: matches 2 of oneOf's 2 schemas, not exactly one"]
    assert schema.check({"a": 1}, {"properties": {"a": False}}) == ["/a: the schema allows nothing here"]
    assert schema.check({"a": 1}, {"properties": {"a": True}}) == []


def test_an_object_that_lists_its_keys_is_closed_unless_it_says_otherwise():
    listed = {"type": "object", "properties": {"a": {"type": "integer"}}}
    assert schema.check({"a": 1, "b": 2}, listed) == ["/b: 'b' isn't a key the schema allows here"]
    assert schema.check({"a": 1, "b": 2}, listed | {"additionalProperties": True}) == []
    assert schema.check({"b": 2}, {"type": "object"}) == []  # lists no keys: open, as the schema says


def test_the_stricter_readings_never_loosen_a_not_an_if_or_a_one_of():
    # Under not, if or oneOf, a stricter subschema can let more through: so there, an object that lists its keys stays
    # open and $ref stands alone, as JSON Schema reads them.
    lists_a = {"properties": {"a": {"type": "integer"}}}
    two_keys = {"a": 1, "b": 2}
    assert schema.check(two_keys, {"not": lists_a}) == ["/: matches what the schema forbids"]
    assert schema.check(two_keys, {"oneOf": [lists_a, {"required": ["a"]}]}) == [
        "/: matches 2 of oneOf's 2 schemas, not exactly one"]
    assert schema.check(two_keys, {"if": lists_a, "then": {"required": ["c"]}}) == ["/: needs 'c'"]
    beside_ref = {"definitions": {"any": {}}, "not": {"$ref": "#/definitions/any", "required": ["c"]}}
    assert schema.check(two_keys, beside_ref) == ["/: matches what the schema forbids"]
    # Outside them, both readings hold.
    assert schema.check(two_keys, {"allOf": [lists_a]}) == ["/b: 'b' isn't a key the schema allows here"]
    assert schema.check(two_keys, {"definitions": {"any": {}}, "$ref": "#/definitions/any", "required": ["c"]}) == [
        "/: needs 'c'"]


def test_a_ref_reaches_only_this_schema():
    assert schema.check(1, {"$ref": "https://example.org/other.json"}) == [
        "/: $ref 'https://example.org/other.json' isn't in this schema, and only its own are followed"]
    assert schema.check(1, {"$ref": "#/definitions/missing"}) == ["/: $ref '#/definitions/missing' finds nothing"]


def _keywords(node, found: set[str]) -> set[str]:
    """Every keyword a schema uses, read as JSON Schema reads it: a property's name, an enum's values and a default
    are no keywords."""
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            if key in ("properties", "definitions"):
                for sub in value.values():
                    _keywords(sub, found)
            elif key == "dependencies":
                for sub in value.values():
                    if isinstance(sub, dict):
                        _keywords(sub, found)
            elif key in ("additionalProperties", "items", "if", "then", "not", "propertyNames", "allOf", "oneOf"):
                for sub in value if isinstance(value, list) else [value]:
                    _keywords(sub, found)
    return found


def test_the_check_knows_exactly_the_keywords_v257s_file_uses():
    assert _keywords(V257, set()) == schema.CHECKED | schema.NOTES | schema.ANNOTATIONS
    assert _keywords(EVERY, set()) == schema.CHECKED | schema.NOTES | schema.ANNOTATIONS


def test_render_refuses_a_config_v257s_schema_rejects(monkeypatch):
    built = spark.render.llama_swap_config

    def mistyped(registry):
        config = built(registry)
        config["routing"]["router"]["settings"]["groups"]["stack"]["swapp"] = False
        return config

    monkeypatch.setattr(spark.render, "llama_swap_config", mistyped)
    with pytest.raises(RenderError, match=r"(?s)^llama-swap's config doesn't pass its v257 schema .*\n  "
                                          r"/routing/router/settings/groups/stack/swapp: "):
        render(load_registry(FIX / "models.yaml"), load_versions(FIX / "versions.yaml"),
               (FIX / "models.yaml").read_text(), templates=ROOT / "stack/templates")


def test_render_names_a_schema_it_cant_read(tmp_path):
    shutil.copytree(ROOT / "stack/templates", tmp_path / "stack/templates")
    with pytest.raises(RenderError, match=r"^the llama-swap v257 schema .*stack/llama-swap/config-schema\.v257\.json "
                                          "won't load: "):
        render(load_registry(FIX / "models.yaml"), load_versions(FIX / "versions.yaml"),
               (FIX / "models.yaml").read_text(), templates=tmp_path / "stack/templates")
