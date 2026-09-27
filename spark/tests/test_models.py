from pathlib import Path

from spark.models import pull
from spark.registry import load_registry

REG = load_registry(Path(__file__).parent / "fixtures" / "models.yaml")


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
