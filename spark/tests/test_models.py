import re
import tomllib
from importlib.metadata import version
from pathlib import Path

from spark.models import pull
from spark.registry import load_registry

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")
SPARK = Path(__file__).parents[1]


def test_pull_fetches_every_file_at_its_pinned_revision(tmp_path):
    calls = []

    def fake(**kw):
        calls.append(kw)
        return f"/cache/{kw['filename']}"

    assert pull(REG, download=fake, hf_home=str(tmp_path), log=lambda m: None) == 0
    files = {(c["repo_id"], c["filename"], c["revision"]) for c in calls}
    assert ("example-org/vision-GGUF", "vision-mmproj.gguf", "1" * 40) in files
    assert len(calls) == 5  # four models, plus the vision model's projector
    assert all(c["cache_dir"] == f"{tmp_path}/hub" for c in calls)
    assert (tmp_path / "tmp").is_dir()  # whisper-server's --tmp-dir


def test_a_failed_file_is_reported_and_the_rest_still_download(tmp_path):
    tried, logs = [], []

    def flaky(**kw):
        tried.append(kw["filename"])
        if kw["filename"] == "embed.gguf":
            raise OSError("404 Client Error")
        return "/cache/" + kw["filename"]

    assert pull(REG, download=flaky, hf_home=str(tmp_path), log=logs.append) == 1
    assert len(tried) == 5 and any("embed.gguf: FAILED" in line for line in logs)


def test_the_download_library_comes_from_the_lock():
    # `spark models pull` imports it only when it runs, and the Spark's venv holds exactly what uv.lock
    # holds. CI's venv is new each run, so a dependency missing from the lock fails here, not there.
    from huggingface_hub import hf_hub_download

    assert callable(hf_hub_download)


def _dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()  # PEP 503's form: huggingface_hub and huggingface-hub are one name


def test_the_lock_is_the_one_made_for_pyproject_and_the_venv_holds_it():
    # Task 8's fix round 1. `--frozen` installs what uv.lock holds and never compares it with pyproject.toml, so a
    # range changed there but not re-locked would go unnoticed, in CI too. uv.lock records what it was made for.
    lock = tomllib.loads((SPARK / "uv.lock").read_text())
    made_for = next(p for p in lock["package"] if p["name"] == "spark")["metadata"]["requires-dist"]
    asked = {}
    for requirement in tomllib.loads((SPARK / "pyproject.toml").read_text())["project"]["dependencies"]:
        name, specifier = re.fullmatch(r"([A-Za-z0-9._-]+)(.*)", requirement.replace(" ", "")).groups()
        asked[_dist(name)] = set(specifier.split(","))
    locked_for = {_dist(r["name"]): set(r.get("specifier", "").split(",")) for r in made_for}
    assert asked == locked_for, "uv.lock wasn't made for pyproject.toml's dependencies: run `uv lock --project spark`"
    locked = {p["name"]: p["version"] for p in lock["package"]}
    for name in asked:
        assert version(name) == locked[name], f"the venv's {name} isn't the lock's"
