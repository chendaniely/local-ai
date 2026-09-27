"""`spark models pull` — download each registry file at its pinned revision into the shared HF cache."""

from __future__ import annotations

import argparse
from pathlib import Path

from spark import paths
from spark.registry import Registry, load_registry
from spark.render import HF_HOME


def pull(registry: Registry, *, download, hf_home: str = HF_HOME, log=print) -> int:
    Path(hf_home, "tmp").mkdir(parents=True, exist_ok=True)  # whisper-server's --tmp-dir
    failed = 0
    for model in registry.models.values():
        for file in filter(None, (model.source.file, model.source.mmproj)):
            try:
                path = download(repo_id=model.source.repo, filename=file, revision=model.source.revision,
                                cache_dir=f"{hf_home}/hub")
            except Exception as err:  # network, a gated repo, a renamed file — report it and keep going
                log(f"pull: {model.name}: {file}: FAILED — {err}")
                failed += 1
                continue
            log(f"pull: {model.name}: {file} → {path}")
    return 1 if failed else 0


def register(subparsers) -> None:
    p = subparsers.add_parser("models", help="model files")
    sub = p.add_subparsers(dest="models_command", required=True)
    sub.add_parser("pull", help="download every file at its pinned revision").set_defaults(func=run_pull)


def run_pull(args: argparse.Namespace) -> int:
    from huggingface_hub import hf_hub_download  # only this command needs it

    return pull(load_registry(paths.REGISTRY), download=hf_hub_download, log=lambda m: print(m, flush=True))
