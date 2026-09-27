import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from spark.versions import VersionsError, load_versions, render_stack_page, unpinned

ROOT = Path(__file__).resolve().parents[2]


def test_uv_runs_the_pinned_python_minor_version():
    # spark/.python-version pins one minor version, so uv picks the same interpreter on the Mac, the
    # Spark and CI rather than whichever is newest on each. uv reads it from the project directory
    # only: a copy at the repo root is ignored under `uv run --project spark`.
    pinned = (ROOT / "spark/.python-version").read_text().strip()
    assert f"{sys.version_info.major}.{sys.version_info.minor}" == pinned


GOOD = textwrap.dedent(
    """
    components:
      llama-swap:
        version: v257
        where: [spark]
        pin: null
        deployed: true
        docs: https://github.com/mostlygeek/llama-swap
        context7: null
        changelog: https://github.com/mostlygeek/llama-swap/releases
        advisories: null
      quarto:
        version: 1.10.3
        where: [mac, ci]
        pin: null
        deployed: false
        docs: https://quarto.org/docs/
        context7: /quarto-dev/quarto-web
        changelog: https://github.com/quarto-dev/quarto-cli/releases
        advisories: null
    """
)


def write(tmp_path, text):
    path = tmp_path / "versions.yaml"
    path.write_text(text)
    return path


def test_loads_components(tmp_path):
    components = load_versions(write(tmp_path, GOOD))
    assert components["llama-swap"].version == "v257"
    assert components["quarto"].where == ("mac", "ci")


def test_unpinned_lists_only_deployed_components(tmp_path):
    assert unpinned(load_versions(write(tmp_path, GOOD))) == ["llama-swap"]


def test_rejects_a_malformed_pin(tmp_path):
    with pytest.raises(VersionsError, match="pin"):
        load_versions(write(tmp_path, GOOD.replace("pin: null\n    deployed: true", "pin: abc\n    deployed: true", 1)))


def test_rejects_an_unknown_machine(tmp_path):
    with pytest.raises(VersionsError, match="where"):
        load_versions(write(tmp_path, GOOD.replace("[spark]", "[toaster]")))


def test_stack_page_is_a_table_marked_generated(tmp_path):
    page = render_stack_page(load_versions(write(tmp_path, GOOD)))
    assert "generated from `stack/versions.yaml`" in page
    assert "| llama-swap | v257 | spark | not yet |" in page


def test_a_source_build_is_pinned_by_its_commit(tmp_path):
    pinned = GOOD.replace("pin: null\n    deployed: true", "pin: git:" + "a" * 40 + "\n    deployed: true", 1)
    assert load_versions(write(tmp_path, pinned))["llama-swap"].pin == "git:" + "a" * 40


def test_rejects_a_component_without_a_version(tmp_path):
    with pytest.raises(VersionsError, match="version"):
        load_versions(write(tmp_path, GOOD.replace("    version: v257\n", "", 1)))


# Task 6's fix round 2: a version and an image go into files root runs (the llama-swap unit's ExecStart, Compose's
# image:) and into the Stack page's table, so each must be plain text.

def with_llama_swap(tmp_path, **fields):
    """GOOD with llama-swap's fields changed, written as YAML writes them (quoted where it must be)."""
    data = yaml.safe_load(GOOD)
    data["components"]["llama-swap"].update(fields)
    return write(tmp_path, yaml.safe_dump(data))


UNSAFE = [
    pytest.param("version", "v257\nExecStartPre=/bin/true", id="version adds a unit line"),
    pytest.param("version", "v257\n", id="version with a trailing newline"),
    pytest.param("version", "v 257", id="version with a space"),
    pytest.param("version", "../v257", id="version climbs out of bin/llama-swap"),
    pytest.param("version", "v257|x", id="version with a table's bar"),
    pytest.param("version", "v257\x1b[0m", id="version with a control character"),
    pytest.param("version", 1.1, id="version YAML read as a number"),  # what `version: 1.10` becomes
    pytest.param("image", "ghcr.io/open-webui/open-webui\n", id="image with a trailing newline"),
    pytest.param("image", "ghcr.io/open-webui/open-webui:v0.11.4", id="image with a tag"),
    pytest.param("image", "ghcr.io/open-webui/open-webui@sha256:" + "0" * 64, id="image with a digest"),
    pytest.param("image", "ghcr.io/Open-WebUI/open-webui", id="image in capitals"),
    pytest.param("image", "ghcr.io//open-webui", id="image with an empty part"),
    pytest.param("image", "ghcr.io/open webui", id="image with a space"),
    pytest.param("pin", "sha256:" + "0" * 64 + "\n", id="pin with a trailing newline"),  # it goes into image: too
]


@pytest.mark.parametrize("field, value", UNSAFE)
def test_a_version_or_image_that_isnt_plain_text_is_refused(tmp_path, field, value):
    with pytest.raises(VersionsError, match=f"^llama-swap: {field} must be"):
        load_versions(with_llama_swap(tmp_path, **{field: value}))


def test_an_image_may_name_its_registry_and_port(tmp_path):
    image = "registry.example:5000/team/llama-swap"
    assert load_versions(with_llama_swap(tmp_path, image=image))["llama-swap"].image == image


def test_the_stacks_own_versions_load():
    components = load_versions(ROOT / "stack/versions.yaml")
    assert components["open-webui"].image == "ghcr.io/open-webui/open-webui"
    assert components["llama.cpp"].version == "b11146"
