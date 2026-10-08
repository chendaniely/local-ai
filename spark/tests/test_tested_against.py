"""Version assumptions in the code, tied to stack/versions.yaml and the lock (Phase 2a, Task 3)."""

import importlib
import pkgutil
import re
import subprocess
import sys
import textwrap
import tomllib
import types
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import spark
from spark import versions
from spark.versions import load_versions

ROOT = Path(__file__).resolve().parents[2]
SPARK = ROOT / "spark"
TESTS = Path(__file__).resolve().parent


def _components():
    return load_versions(ROOT / "stack/versions.yaml")


def _modules():
    modules = [importlib.import_module(info.name) for info in pkgutil.walk_packages(spark.__path__, prefix="spark.")]
    # Task 12's llama-swap stand-in, once it exists, imported as the other tests import it: pytest puts this folder on
    # sys.path, since it has no __init__.py.
    if (TESTS / "fake_llamaswap.py").exists():
        modules.append(importlib.import_module("fake_llamaswap"))
    return modules


def _standin(tested_against):
    module = types.ModuleType("spark.standin")
    module.TESTED_AGAINST = tested_against
    return module


def test_every_tested_against_matches_versions_yaml_or_the_lock():
    modules = _modules()
    # The walk has to reach the modules that carry one, or this test passes on nothing.
    assert {"spark.llamaswap", "spark.render"} <= {m.__name__ for m in modules if hasattr(m, "TESTED_AGAINST")}
    assert versions.tested_against_problems(modules, _components()) == []


def test_a_wrong_assumption_is_named():
    components = _components()
    [problem] = versions.tested_against_problems([_standin({"llama-swap": "v999"})], components)
    assert problem.startswith("spark.standin: ")
    assert "llama-swap" in problem and "v999" in problem and "v257" in problem

    [problem] = versions.tested_against_problems([_standin({"pypi:uvicorn": "0.1.0"})], components)
    assert problem.startswith("spark.standin: ")
    assert "0.1.0" in problem and version("uvicorn") in problem

    [problem] = versions.tested_against_problems([_standin({"nonesuch": "1"})], components)
    assert problem.startswith("spark.standin: ") and "nonesuch" in problem

    def not_installed(name):
        raise PackageNotFoundError(name)

    [problem] = versions.tested_against_problems([_standin({"pypi:uvicorn": "0.54.0"})], components, not_installed)
    assert problem == "spark.standin: TESTED_AGAINST pypi:uvicorn 0.54.0, but the environment has none"

    # And one that is right gives none.
    right = _standin({"llama-swap": "v257", "pypi:uvicorn": version("uvicorn")})
    assert versions.tested_against_problems([right], components) == []


def _dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()  # PEP 503's form


def test_uvicorn_is_pinned_exactly():
    # From Task 9, protocols.py subclasses uvicorn's internals, so any other uvicorn is untested (updates.md says so).
    dependencies = tomllib.loads((SPARK / "pyproject.toml").read_text())["project"]["dependencies"]
    uvicorn = [d for d in dependencies if _dist(re.match(r"[A-Za-z0-9._-]+", d).group()) == "uvicorn"]
    assert uvicorn == ["uvicorn==0.54.0"]


def test_the_lock_adds_only_uvicorn_and_starlette():
    lock = tomllib.loads((SPARK / "uv.lock").read_text())
    before = ("anyio certifi click colorama filelock fsspec h11 hf-xet httpcore httpx huggingface-hub idna iniconfig "
              "packaging pluggy pygments pytest pyyaml spark tqdm typing-extensions").split()
    assert sorted(p["name"] for p in lock["package"]) == sorted([*before, "starlette", "uvicorn"])


CLI_PATH = textwrap.dedent(
    """
    import sys
    from spark import cli
    p = cli.build_parser()
    p.parse_args(["launch", "m", "--", "/bin/x"])
    p.parse_args(["status", "--json"])
    heavy = ("uvicorn", "starlette", "httpx")
    print(sorted(name for name in sys.modules if name.split(".")[0] in heavy))
    """
)


def test_the_cli_status_and_launch_import_neither_uvicorn_nor_httpx(tmp_path):
    # Every spark command runs build_parser(), which imports every command's module: so each registers with its heavy
    # imports inside its handler (Tasks 19, 21 and 30), and `spark launch` and `spark status --json` stay light.
    result = subprocess.run([sys.executable, "-c", CLI_PATH], cwd=tmp_path, capture_output=True, text=True,
                            timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "[]\n"
