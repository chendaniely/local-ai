"""A small JSON-schema check of the rendered llama-swap config, against llama-swap's own config-schema.json.

The schema is vendored from llama-swap's tag, at stack/llama-swap/config-schema.<version>.json (render's
TESTED_AGAINST), and `spark render` refuses a config with any problem. The check knows exactly the keywords that file
uses (CHECKED, NOTES, ANNOTATIONS); a schema with any other keyword is refused whole, naming it, whatever the config,
so a later schema's new keyword is never skipped and what it forbids let through.

Two readings go past JSON Schema's letter, each stricter, never looser:
- An object whose schema lists its keys (`properties`) and says nothing of `additionalProperties` is read as closed: a
  key it doesn't list is a problem. v257 decodes its config with yaml.v3, which drops a key it doesn't know, and its
  schema leaves the top level, a model and a group open, so a misspelled `swap` would pass both and leave the group at
  v257's default, swap: true, where loads evict each other.
- `$ref` is checked beside its sibling keywords, not instead of them (v257's file gives it none).
Neither holds under `not`, `if` or `oneOf`, where a stricter subschema could let more through: there, JSON Schema's own
reading stands.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any
from urllib.parse import unquote

# The keywords v257's file uses, each checked.
CHECKED = frozenset({
    "type", "properties", "additionalProperties", "required", "enum", "items", "minimum", "$ref", "allOf", "if",
    "then", "not", "oneOf", "propertyNames", "pattern", "minLength", "maxLength", "minItems", "minProperties",
    "uniqueItems", "dependencies",
})
# Read as a note, not checked: the file's one format, "uri", is on a model's proxy (and a peer's), and render's proxy,
# http://127.0.0.1:${PORT}, is no URI until llama-swap fills in the port.
NOTES = frozenset({"format"})
# Skipped: they say nothing of what a config may hold. `definitions` holds the schemas `$ref` reaches, each read there.
ANNOTATIONS = frozenset({"$schema", "$id", "title", "description", "default", "definitions"})
_TYPES = {"object": "an object", "array": "an array", "string": "a string", "number": "a number",
          "integer": "a number", "boolean": "a boolean", "null": "null"}


def check(config: Any, schema: dict) -> list[str]:
    """Every problem `config` has against `schema`, one line each, with its path in the config ("/" its top, then each
    key or index: /models/<name>/cmd). An empty list: it passes."""
    unknown = list(_unknown_keywords(schema, "#"))
    if unknown:  # a schema the check can't read whole is refused before the config is read
        return unknown
    return _check(config, schema, "", schema, strict=True)


def _subschemas(schema: dict):
    """(where, subschema) for each schema inside `schema`, by the keywords this check knows."""
    for key in ("properties", "definitions"):
        for name, sub in schema.get(key, {}).items():
            yield f"{key}/{name}", sub
    for name, sub in schema.get("dependencies", {}).items():
        if not isinstance(sub, list):
            yield f"dependencies/{name}", sub
    for key in ("additionalProperties", "items", "if", "then", "not", "propertyNames"):
        if key in schema:
            yield key, schema[key]
    for key in ("allOf", "oneOf"):
        for i, sub in enumerate(schema.get(key, [])):
            yield f"{key}/{i}", sub


def _unknown_keywords(schema: Any, where: str):
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        yield f"schema {where}: {schema!r} isn't a schema"
        return
    for key in schema:
        if key not in CHECKED | NOTES | ANNOTATIONS:
            yield (f"schema {where}: uses {key!r}, a keyword this check doesn't know, so it refuses the config rather "
                   "than skip what that keyword asks")
    if isinstance(schema.get("items"), list):
        yield f"schema {where}: its items is a list, a form this check doesn't know"
        return
    for sub_where, sub in _subschemas(schema):
        yield from _unknown_keywords(sub, f"{where}/{sub_where}")


def _shown(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _is(value: Any, type_name: str) -> bool:
    if type_name == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, float) and math.isfinite(value) and value.is_integer())
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return _kind(value) == type_name


def _same(a: Any, b: Any) -> bool:
    """JSON's equality: true is no 1, and 1 is 1.0."""
    if _kind(a) != _kind(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def _ecma(pattern: str) -> str:
    """A JSON Schema pattern (ECMA 262) as Python reads it: its `$`, outside a class, is the input's end, never before a
    last newline as Python's is."""
    out, escaped, in_class = [], False, False
    for char in pattern:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "$" and not in_class:
            out.append(r"\Z")
            continue
        out.append(char)
    return "".join(out)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _ref(root: dict, ref: str) -> Any:
    """The schema a local `$ref` names (#, or a JSON pointer after it); KeyError when it names nothing."""
    if ref == "#":
        return root
    node: Any = root
    for part in ref.removeprefix("#/").split("/"):
        part = unquote(part).replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise KeyError(ref)
    return node


def _check(value: Any, schema: Any, at: str, root: dict, strict: bool) -> list[str]:
    """`value`'s problems against `schema`; `strict`, the module's two stricter readings, holds unless a `not`, an `if`
    or a `oneOf` is above."""
    where = at or "/"
    if schema is True:
        return []
    if schema is False:
        return [f"{where}: the schema allows nothing here"]
    problems: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#"):
            problems.append(f"{where}: $ref {ref!r} isn't in this schema, and only its own are followed")
        else:
            try:
                problems += _check(value, _ref(root, ref), at, root, strict)
            except KeyError:
                problems.append(f"{where}: $ref {ref!r} finds nothing")
        if not strict:  # JSON Schema draft-07's letter: $ref's siblings are ignored
            return problems
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        unknown = [t for t in types if t not in _TYPES]
        if unknown:
            return problems + [f"{where}: the schema's type {unknown[0]!r} isn't one this check knows"]
        if not any(_is(value, t) for t in types):
            kind = _kind(value)
            return problems + [f"{where}: {_shown(value)} is {_TYPES.get(kind, kind)}, not {' or '.join(types)}"]
    if "enum" in schema and not any(_same(value, choice) for choice in schema["enum"]):
        problems.append(f"{where}: {_shown(value)} isn't one of {', '.join(repr(c) for c in schema['enum'])}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        if value < schema["minimum"]:
            problems.append(f"{where}: {value} is under its minimum, {schema['minimum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(f"{where}: shorter than {_plural(schema['minLength'], 'character')}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            problems.append(f"{where}: longer than {_plural(schema['maxLength'], 'character')}")
        if "pattern" in schema and not re.search(_ecma(schema["pattern"]), value, re.ASCII):
            problems.append(f"{where}: {_shown(value)} doesn't match {schema['pattern']}")
    if isinstance(value, (list, tuple)):
        problems += _check_array(value, schema, at, root, strict)
    if isinstance(value, dict):
        problems += _check_object(value, schema, at, root, strict)
    for sub in schema.get("allOf", []):
        problems += _check(value, sub, at, root, strict)
    if "oneOf" in schema:
        matched = sum(not _check(value, sub, at, root, strict=False) for sub in schema["oneOf"])
        if matched != 1:
            problems.append(f"{where}: matches {matched} of oneOf's {len(schema['oneOf'])} schemas, not exactly one")
    if "not" in schema and not _check(value, schema["not"], at, root, strict=False):
        problems.append(f"{where}: matches what the schema forbids")
    if "if" in schema and "then" in schema and not _check(value, schema["if"], at, root, strict=False):
        problems += _check(value, schema["then"], at, root, strict)
    return problems


def _check_array(value: list | tuple, schema: dict, at: str, root: dict, strict: bool) -> list[str]:
    where = at or "/"
    problems: list[str] = []
    if "minItems" in schema and len(value) < schema["minItems"]:
        problems.append(f"{where}: fewer than {_plural(schema['minItems'], 'item')}")
    if schema.get("uniqueItems"):
        seen: list[Any] = []
        for item in value:
            if any(_same(item, other) for other in seen):
                problems.append(f"{where}: holds {_shown(item)} more than once")
                break
            seen.append(item)
    if "items" in schema:
        for i, item in enumerate(value):
            problems += _check(item, schema["items"], f"{at}/{i}", root, strict)
    return problems


def _check_object(value: dict, schema: dict, at: str, root: dict, strict: bool) -> list[str]:
    where = at or "/"
    problems: list[str] = []
    for key in schema.get("required", []):
        if key not in value:
            problems.append(f"{where}: needs {key!r}")
    if "minProperties" in schema and len(value) < schema["minProperties"]:
        problems.append(f"{where}: fewer than {_plural(schema['minProperties'], 'key')}")
    listed = schema.get("properties", {})
    # Absent, with keys listed, it is read as closed where `strict` holds (the module's docstring says why); otherwise
    # absent is open, as JSON Schema reads it.
    extra = schema.get("additionalProperties", False if strict and "properties" in schema else True)
    for key, item in value.items():
        if not isinstance(key, str):
            problems.append(f"{where}: the key {_shown(key)} isn't a string")
            continue
        if "propertyNames" in schema:
            problems += _check(key, schema["propertyNames"], f"{where}: the key {key!r}", root, strict)
        if key in listed:
            problems += _check(item, listed[key], f"{at}/{key}", root, strict)
        elif extra is False:
            problems.append(f"{at}/{key}: {key!r} isn't a key the schema allows here")
        else:
            problems += _check(item, extra, f"{at}/{key}", root, strict)
    for key, needs in schema.get("dependencies", {}).items():
        if key not in value:
            continue
        if isinstance(needs, list):
            problems += [f"{where}: {key!r} needs {need!r} beside it" for need in needs if need not in value]
        else:
            problems += _check(value, needs, at, root, strict)
    return problems


def load(text: str) -> dict:
    """A schema file's text, as a schema."""
    schema = json.loads(text)
    if not isinstance(schema, dict):
        raise ValueError("a schema file holds one JSON object")
    return schema
