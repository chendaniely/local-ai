"""`spark models pull` — download each registry file at its pinned revision into the shared HF cache."""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path

from spark import paths
from spark.registry import Registry, load_registry
from spark.render import HF_HOME, _load

# The token the library sends goes into an HTTP header whole, and an error's repr() quotes it. Printable ASCII but a
# space, a quote or a backslash reads the same in both, so sanitise() finds it wherever it lands. Anything else is
# refused before a download: h11 prints a header value it can't send, whole ("Illegal header value b'Bearer …'").
TOKEN = re.compile(r"[!#-&(-\[\]-~]+")
# A URL's query string, up to what ends the URL: a space, a quote, a bracket, or a sentence's last stop. A presigned
# CDN URL's signature is a credential for its file.
URL_QUERY = re.compile(r"(https?://[^\s?#'\"<>()]*)\?[^\s#'\"<>()]*?(?=[.,;:]?(?:[\s#'\"<>()]|$))", re.IGNORECASE)
# The library's module that reads the token files, again for every request (build_hf_headers calls get_token). When a
# file won't parse, it logs the line it read, which can hold a token the pull was never told.
TOKEN_LOGGER = "huggingface_hub.utils._auth"


class PullError(ValueError):
    pass


def sanitise(text: str, token: str | None = None) -> str:
    """`text` fit for the pull's journal, which Dan's sessions read: the token replaced by a marker, every URL's query
    string dropped, and all on one line, so an entry that starts with `pull:` holds the whole reason."""
    text = URL_QUERY.sub(r"\1", text)  # first: a query can hold the token, and the marker's < > would end the URL there
    if token:
        text = text.replace(token, "<token>")
    return " ".join(text.split())


def check_token(token: str | None) -> None:
    """Refuse a token a header or a repr() would carry altered, and never show it. No token is fine."""
    if token is not None and not TOKEN.fullmatch(token):
        raise PullError(
            "the Hugging Face token (the HF_TOKEN variable, or else the token file under HF_HOME) holds whitespace, a "
            "control character, a quote, a backslash or a non-ASCII character, which an HTTP header or an error "
            "message would carry altered. Nothing was downloaded, and the token isn't shown: fix it where it's set, "
            "or remove it, since only a gated or private repo needs one")


class _Sanitised(logging.Filter):
    """The library's own lines (a retry, a resumed download) go through sanitise() too. A line it logs while the token
    is read (`holding`), or from the module that reads the token files, can quote a token sanitise() isn't told: the
    first becomes a note of where, never what, and the rest are dropped."""

    def __init__(self, folder: str) -> None:
        super().__init__()
        self.folder, self.token, self.holding, self.told = folder, None, False, False

    def filter(self, record: logging.LogRecord) -> bool:
        if self.holding or record.name == TOKEN_LOGGER:
            text, show = (f"pull: huggingface_hub logged a problem reading its token files in {self.folder}; it isn't "
                          f"shown, since it can quote a token"), not self.told
            self.told = True
        else:
            text, show = record.getMessage(), True
            if record.exc_info:
                text += " " + logging.Formatter().formatException(record.exc_info)
            text = sanitise(text, self.token)
        record.msg, record.args = text, None  # in place, so a handler after this one gets the same text
        record.exc_info = record.exc_text = record.stack_info = None
        return show


def pull(registry: Registry, *, download, hf_home: str = HF_HOME, log=print, redact: str | None = None) -> int:
    Path(hf_home, "tmp").mkdir(parents=True, exist_ok=True)  # whisper-server's --tmp-dir
    failed = 0
    for model in registry.models.values():
        for file in filter(None, (model.source.file, model.source.mmproj)):
            try:
                path = download(repo_id=model.source.repo, filename=file, revision=model.source.revision,
                                cache_dir=f"{hf_home}/hub")
            # Report it and keep going. The reason, the library's text through sanitise(), says which cause it is: a
            # wrong file name or revision, a missing or gated repo, a bad or refused token, the network or a server
            # error, or a full or unwritable disk.
            except Exception as err:
                log(f"pull: {model.name}: {file}: FAILED — {sanitise(str(err), redact) or type(err).__name__}")
                failed += 1
                continue
            log(f"pull: {model.name}: {file} → {path}")
    return 1 if failed else 0


def register(subparsers) -> None:
    p = subparsers.add_parser("models", help="model files")
    sub = p.add_subparsers(dest="models_command", metavar="{pull}", required=True)
    sub.add_parser("pull", help="download every file at its pinned revision").set_defaults(func=run_pull)


def run_pull(args: argparse.Namespace) -> int:
    from huggingface_hub import constants, get_token, hf_hub_download  # only this command needs it

    registry = _load("registry", paths.REGISTRY, load_registry)  # a refusal names the file, even one nested too deep
    handlers = list(logging.getLogger("huggingface_hub").handlers)
    sanitised = _Sanitised(folder=str(Path(constants.HF_TOKEN_PATH).parent))
    for handler in handlers:  # before the token is read: a line from reading it can quote it
        handler.addFilter(sanitised)
    try:
        sanitised.holding = True
        try:
            token = get_token()  # the one the library sends: HF_TOKEN, else the token file under HF_HOME
        finally:
            sanitised.holding = False
        check_token(token)
        sanitised.token = token
        return pull(registry, download=hf_hub_download, hf_home=HF_HOME, log=lambda m: print(m, flush=True),
                    redact=token)
    finally:
        for handler in handlers:
            handler.removeFilter(sanitised)
