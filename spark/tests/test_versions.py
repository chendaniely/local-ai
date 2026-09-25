import sys
import textwrap
from pathlib import Path

import pytest

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
