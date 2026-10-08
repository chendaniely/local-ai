"""Dan's words (website/design/plan.md, *What you see in Phase 2a*): the refusals, each with its status and headers,
what pi and the web UI show when the gate or the front says no; the notifications on Dan's phone; and what each command
says in the terminal."""

import json
import re
import socket
import subprocess
import sys
import textwrap
import time
from dataclasses import MISSING, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from spark import messages
from spark.budget import Candidate
from spark.messages import (CODES_409, CODES_503, FRONT_CODES, PI_RETRY_PATTERNS, RETRY_AFTER_S, UNKNOWN_KEY, Holder,
                            Moment, Refusal, duration, pi_retry_match, refusal)
from spark.registry import NOTIFICATION_TYPES, load_registry

STACK_REGISTRY = Path(__file__).resolve().parents[2] / "stack" / "models.yaml"

# The tests' own time zone, seven hours behind UTC: POSIX's TZ writes it "PDT+7", its sign the other way round.
TZ = "PDT+7"
ZONE = timezone(timedelta(hours=-7))


@pytest.fixture(autouse=True)
def local_zone(monkeypatch):
    """Every test here reads the clock in ZONE, on a box called brightroar (the notifications name it, by its short
    name); the process gets its own zone and name back afterwards."""
    monkeypatch.setenv("TZ", TZ)
    time.tzset()
    monkeypatch.setattr(socket, "gethostname", lambda: "brightroar.local")
    yield
    monkeypatch.undo()
    time.tzset()


# The examples' moment (plan.md): the residents loaded, the coder not, and a 32 GiB python job of Dan's running.
AT_0312 = datetime(2026, 10, 7, 3, 12, tzinfo=ZONE)
PYTHON = Holder("python3 (chendaniely)", 32, True)
GEMMA = Holder("Gemma", 27, False)
CODER = {"model_label": "the coder", "model_command": "coder"}
DAN = {"key_label": "pi on the Mac", "words": "dan", "names_processes": True, "wait_s": 30}
AGENT = {"key_label": "agent", "words": "agent", "names_processes": False, "wait_s": 600}

# free_gib is the gate's own figure, budget.free_for_a_load's for the request: 48 − 24 − 6 here, the ceiling not binding
NO_FIT_DAN = Moment(needed_gib=41, free_gib=Decimal(18), available_gib=48, reserve_gib=24, owed_gib=6, held_gib=0,
                    hold_counted=False, holders=[PYTHON, GEMMA], **CODER, **DAN)
NO_FIT_AGENT = Moment(needed_gib=41, free_gib=Decimal(12), available_gib=106, reserve_gib=24, owed_gib=0, held_gib=70,
                      hold_counted=True, holders=[Holder("the embeddings", 8, False), Holder("whisper", 3, False)],
                      **CODER, **AGENT)
# Dan's job running in Dan's own hold: free for a load, 36 − 24 − 70, is below 0.
NO_FIT_AGENT_CLAMPED = replace(NO_FIT_AGENT, free_gib=Decimal(-58), available_gib=36,
                               holders=[Holder("python3 (chendaniely)", 70, True), Holder("the embeddings", 8, False)])
LOADING = Moment(loading_label="Gemma", **CODER, **DAN)
HELD = Moment(brake_at=AT_0312, brake_available_gib=19.6, release_waits_for_dan=False, **CODER, **DAN)
LOAD_FAILED = Moment(engine_said="failed to load model", **CODER, **DAN)
NOT_DOWNLOADED = Moment(download_gib=16, **CODER, **DAN)
SUSPECT = Moment(brake_at=AT_0312, **CODER, **AGENT)
DRAINING_DAN = Moment(inflight=1, drain_for="make-room", **CODER, **DAN)
DRAINING_AGENT = Moment(inflight=1, drain_for="make-room", **CODER, **AGENT)
MODELS = [("the coder", "qwen3.8-27b"), ("Gemma", "gemma-4-26b-a4b"), ("the embeddings", "qwen3-embedding-0.6b"),
          ("whisper", "whisper-large-v3-turbo")]

NO_FIT_DAN_TEXT = (
    "The coder didn't load: it needs 41 GiB, and 18 GiB is free for a load (48 GiB available, less the 24 GiB reserve "
    "and the 6 GiB the loaded models may still grow into). Using memory now: python3 (chendaniely) 32 GiB, Gemma "
    "27 GiB. Free space with `spark make-room 41G` on the Spark, then try again."
)
NO_FIT_DAN_BREAKDOWN = ("18 GiB is free for a load (48 GiB available, less the 24 GiB reserve and the 6 GiB the loaded "
                        "models may still grow into)")
NO_FIT_CLAMPED_TEXT = (
    "The coder didn't load: it needs 41 GiB, and nothing is free for a load while make-room holds 70 GiB for Dan "
    "(36 GiB available, less the 24 GiB reserve). Using memory now: a process of Dan's, 70 GiB, the embeddings 8 GiB. "
    "Only Dan can free memory for it, on the Spark; try again after that."
)
LOADING_TEXT = (
    "The coder didn't start in time: it was waiting its turn while Gemma loads, since one model loads at a time, and "
    "your 30 s ran out. Try again in a minute."
)
LOAD_FAILED_TEXT = (
    'The coder started loading but failed: the engine stopped with "failed to load model". On the Spark, '
    "`spark logs coder` shows the engine's last lines."
)
LOAD_FAILED_UNQUOTED_TEXT = (
    "The coder started loading but failed: the engine stopped. On the Spark, `spark logs coder` shows the engine's "
    "last lines."
)
RESTARTING_TEXT = (
    "The model service on the Spark is restarting for a configuration change, and your 30 s ran out. Try again in a "
    "minute."
)
DRAINING_UNLOAD_TEXT = (
    "The coder is being unloaded once its 1 request in flight finishes, and your 30 s ran out. Try again in a minute."
)
MODEL_NOT_FOUND_TEXT = (
    "There's no model called qwen3.6-35b-a3b here. The models are the coder (qwen3.8-27b), Gemma (gemma-4-26b-a4b), "
    "the embeddings (qwen3-embedding-0.6b) and whisper (whisper-large-v3-turbo). On the Mac, `make clients` updates "
    "pi's list."
)
CONCURRENCY_TEXT = "Too many requests for the coder at once; try again in a moment."
GATE_DOWN_TEXT = (
    "No new model can load: the gate on the Spark isn't running. Models already loaded still answer. Your phone has "
    "the alert; on the Spark, `make doctor` shows what's wrong."
)
DANS_ALERT = "Dan's phone has the alert, and `make doctor` on the Spark shows Dan what's wrong."

# plan.md's refusal table, each row with the inputs phase-2a.md's Task 6 gives it: (id, code, moment, the text).
ROWS = [
    ("no_fit, Dan", "no_fit", NO_FIT_DAN, NO_FIT_DAN_TEXT),
    ("no_fit, agent", "no_fit", NO_FIT_AGENT,
     "The coder didn't load: it needs 41 GiB, and 12 GiB is free for a load (106 GiB available, less the 24 GiB "
     "reserve and the 70 GiB make-room holds for Dan). Using memory now: the embeddings 8 GiB, whisper 3 GiB. The "
     "hold ends when Dan runs `spark make-room --done` on the Spark."),
    ("no_fit, agent, Dan's job in the hold", "no_fit", NO_FIT_AGENT_CLAMPED, NO_FIT_CLAMPED_TEXT),
    ("loading", "loading", LOADING, LOADING_TEXT),
    ("held_by_brake", "held_by_brake", HELD,
     "Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads are paused. They resume "
     "by themselves after 5 minutes above 28 GiB available, if what would reload fits; on the Spark, "
     "`make brake-release` resumes them now."),
    ("held_by_brake, waits for Dan", "held_by_brake", replace(HELD, release_waits_for_dan=True),
     "Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads stay paused until you "
     "release them: on the Spark, `make brake-release`."),
    ("gate_down", "gate_down", Moment(**DAN), GATE_DOWN_TEXT),
    ("load_failed", "load_failed", LOAD_FAILED, LOAD_FAILED_TEXT),
    # A duration of a minute or more reads in minutes (the controller's ruling, fix round 1): never a bare 5xx.
    ("load_failed, deadline", "load_failed", replace(LOAD_FAILED, engine_said=None, deadline_s=180),
     "The coder started loading but didn't finish within 3 minutes. On the Spark, `spark logs coder` shows the "
     "engine's last lines."),
    ("not_downloaded", "not_downloaded", NOT_DOWNLOADED,
     "The coder isn't downloaded yet. On the Spark, `make pull` fetches it (16 GiB)."),
    ("restarting", "restarting", Moment(**CODER, **DAN), RESTARTING_TEXT),
    ("llama_swap_down", "llama_swap_down", Moment(**CODER, **DAN),
     "The model service on the Spark isn't answering, and your 30 s ran out. Your phone has the alert; on the Spark, "
     "`make doctor` shows what's wrong."),
    ("draining, Dan", "draining", DRAINING_DAN,
     "The coder is being unloaded for make-room once its 1 request in flight finishes, and your 30 s ran out. Try "
     "again in a minute: your request can load it again, into the room make-room holds for you, if it fits."),
    ("draining, agent", "draining", DRAINING_AGENT,
     "The coder is being unloaded for make-room once its 1 request in flight finishes, and your 10 minutes ran out. "
     "It won't load for agent while make-room's hold stands. The hold ends when Dan runs `spark make-room --done` "
     "on the Spark."),
    ("footprint_suspect", "footprint_suspect", SUSPECT,
     "Not loading the coder for agent: it was loading when the brake fired at 03:12, so only Dan can load it again: "
     "`spark load coder` on the Spark, or one of Dan's requests from pi on the Mac or the web UI, which loads it if "
     "it fits."),
    ("model_not_found", "model_not_found", Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, **DAN),
     MODEL_NOT_FOUND_TEXT),
    # agent's pi is on the Spark, and after the cutover agent runs the deployed CLI (Task 34; the controller's ruling).
    ("model_not_found, agent", "model_not_found", Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, **AGENT),
     MODEL_NOT_FOUND_TEXT.replace("On the Mac, `make clients` updates pi's list.",
                                  "On the Spark, as `agent`, `/opt/local-ai/app/.venv/bin/spark clients pi --write "
                                  "--registry /opt/local-ai/etc/models.yaml` updates pi's list.")),
    ("too_many_requests", "too_many_requests", Moment(**AGENT),
     "agent already has as many requests waiting or open as its key allows; this one wasn't queued. Try again when "
     "one finishes."),
    ("route_not_served", "route_not_served", Moment(),
     "This address isn't served here: the Spark's model API answers only /v1/models, /v1/chat/completions, "
     "/v1/completions, /v1/responses, /v1/messages, /v1/embeddings and /v1/audio/transcriptions."),
    ("draining, not make-room", "draining", Moment(inflight=1, drain_for="unload", **CODER, **DAN),
     DRAINING_UNLOAD_TEXT),
    ("concurrency_limit", "concurrency_limit", Moment(model_label="the coder"), CONCURRENCY_TEXT),
]
# agent's words where the plan's table gives Dan's alone: each says whose step it is, and never asks agent to run what
# only Dan can (the controller's rulings, fix round 1). Each is in plan.md's table, with a dated note.
AGENT_ROWS = [
    ("no_fit, agent, no hold of Dan's", "no_fit", replace(NO_FIT_DAN, **AGENT),
     NO_FIT_DAN_TEXT.replace("python3 (chendaniely) 32 GiB", "a process of Dan's, 32 GiB").replace(
         "Free space with `spark make-room 41G` on the Spark, then try again.",
         "Only Dan can free memory for it, on the Spark; try again after that.")),
    ("held_by_brake, agent", "held_by_brake", replace(HELD, **AGENT),
     "Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads are paused. They resume "
     "by themselves after 5 minutes above 28 GiB available, if what would reload fits, or when Dan runs "
     "`make brake-release` on the Spark."),
    ("held_by_brake, agent, waits for Dan", "held_by_brake", replace(HELD, release_waits_for_dan=True, **AGENT),
     "Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads stay paused until Dan "
     "releases them: on the Spark, `make brake-release`."),
    ("gate_down, agent", "gate_down", Moment(**AGENT), GATE_DOWN_TEXT.replace(
        "Your phone has the alert; on the Spark, `make doctor` shows what's wrong.", DANS_ALERT)),
    ("load_failed, agent", "load_failed", replace(LOAD_FAILED, **AGENT), LOAD_FAILED_TEXT.replace(
        "On the Spark, `spark logs coder` shows the engine's last lines.",
        "Dan can see why with `spark logs coder` on the Spark.")),
    ("not_downloaded, agent", "not_downloaded", replace(NOT_DOWNLOADED, **AGENT),
     "The coder isn't downloaded yet. Dan can fetch it with `make pull` on the Spark (16 GiB)."),
    ("llama_swap_down, agent", "llama_swap_down", Moment(**CODER, **AGENT),
     f"The model service on the Spark isn't answering, and your 10 minutes ran out. {DANS_ALERT}"),
]
EVERY_ROW = ROWS + AGENT_ROWS
EVERY_CODE = CODES_409 + CODES_503 + tuple(FRONT_CODES)
MOMENT = {code: moment for _, code, moment, _ in reversed(ROWS)}  # each code's first row
JSON_TYPE = (b"content-type", b"application/json")
DONT_RETRY = (b"x-should-retry", b"false")


def retry_after(seconds: int) -> tuple[bytes, bytes]:
    return (b"retry-after", str(seconds).encode())


def pi_retries(text: str) -> bool:
    """pi-ai's own test: its patterns joined with '|', matched case-insensitively (`utils/retry.js`)."""
    return re.search("|".join(PI_RETRY_PATTERNS), text, re.IGNORECASE) is not None


def pi_shows(status: int, code: str, moment: Moment) -> str:
    """The text pi shows for a refusal (*Before Task 1*): the status, then the body's error object as JSON."""
    return f"{status}: " + json.dumps(refusal(code, moment).body()["error"])


@pytest.mark.parametrize("code, moment, text", [pytest.param(c, m, t, id=i) for i, c, m, t in ROWS])
def test_each_refusal_reads_word_for_word(code, moment, text):
    assert refusal(code, moment).message == text


@pytest.mark.parametrize("code, moment, text", [pytest.param(c, m, t, id=i) for i, c, m, t in AGENT_ROWS])
def test_agents_words_read_word_for_word(code, moment, text):
    assert refusal(code, moment).message == text


def test_every_code_has_its_status():
    assert CODES_409 == ("no_fit", "loading", "held_by_brake", "footprint_suspect", "load_failed", "not_downloaded",
                         "restarting", "llama_swap_down", "draining")
    assert CODES_503 == ("gate_down",)
    assert FRONT_CODES == {"model_not_found": 404, "too_many_requests": 429, "route_not_served": 404,
                           "concurrency_limit": 429}
    assert {code: refusal(code, MOMENT[code]).status for code in EVERY_CODE} == {
        **dict.fromkeys(CODES_409, 409), "gate_down": 503, "model_not_found": 404, "route_not_served": 404,
        "too_many_requests": 429, "concurrency_limit": 429}
    headers = {code: refusal(code, MOMENT[code]).headers() for code in EVERY_CODE}
    assert headers["no_fit"] == [JSON_TYPE, DONT_RETRY, retry_after(30)]
    assert headers["loading"] == [JSON_TYPE, DONT_RETRY]
    assert headers["held_by_brake"] == [JSON_TYPE, DONT_RETRY, retry_after(300)]
    assert headers["footprint_suspect"] == [JSON_TYPE, DONT_RETRY]
    assert headers["load_failed"] == [JSON_TYPE, DONT_RETRY]
    assert headers["not_downloaded"] == [JSON_TYPE, DONT_RETRY]
    assert headers["restarting"] == [JSON_TYPE, DONT_RETRY, retry_after(60)]
    assert headers["llama_swap_down"] == [JSON_TYPE, DONT_RETRY, retry_after(30)]
    assert headers["draining"] == [JSON_TYPE, DONT_RETRY, retry_after(60)]
    assert headers["gate_down"] == [JSON_TYPE, DONT_RETRY, retry_after(30)]
    assert headers["model_not_found"] == [JSON_TYPE]
    assert headers["route_not_served"] == [JSON_TYPE]
    assert headers["too_many_requests"] == [JSON_TYPE, retry_after(10)]
    assert headers["concurrency_limit"] == [JSON_TYPE]
    # A 404 carries neither, even when it is given a retry-after; a 429 built with one, as the front may pass
    # llama-swap's own on, carries it.
    assert Refusal("model_not_found", 404, "…", 10).headers() == [JSON_TYPE]
    assert Refusal("concurrency_limit", 429, "…", 5).headers() == [JSON_TYPE, retry_after(5)]


def test_an_unknown_code_is_refused_by_name():
    with pytest.raises(ValueError, match="'no_room'"):
        refusal("no_room", NO_FIT_DAN)


@pytest.mark.parametrize("code, moment", [pytest.param(c, m, id=i) for i, c, m, _ in EVERY_ROW if c in CODES_409])
def test_pi_wont_retry_a_refusal(code, moment):
    assert not pi_retries(pi_shows(409, code, moment))
    assert pi_retries(pi_shows(503, code, moment))  # so the test can fail


def _boundaries() -> list[dict]:
    """Every value at which a number or an outside name could put one of pi's patterns in a sentence: sizes of 429
    and up (no memory on this box comes near them), durations of 500 s and up, and a name ending in -500m."""
    sizes = {"needed_gib": 500, "free_gib": 503, "available_gib": 520, "reserve_gib": 524, "owed_gib": 429,
             "held_gib": 502, "hold_counted": True, "starting_gib": 504, "download_gib": 500,
             "brake_available_gib": 503, "warn_gib": 520, "inflight": 2,
             "holders": [Holder("srv-500m (agent)", 500, False), Holder("python3 (chendaniely)", 503, True)]}
    ceiling = {**sizes, "ceiling_gib": 524, "committed_gib": 500, "free_gib": Decimal("-0.5")}
    waits = [{"wait_s": s, "release_after_s": s, "deadline_s": s, "engine_said": None} for s in (500, 503, 520, 529)]
    hours = [{"wait_s": s, "release_after_s": s} for s in (30000, 30180, 31200)]  # 500, 503 and 520 minutes
    names = [{"asked_name": "qwen-500m", "holders": [Holder("srv-500m (agent)", 8, False)]}]
    return [sizes, ceiling, *waits, *hours, *names]


# The one refusal whose own words hold one of pi's patterns: the plan's concurrency_limit, "Too many requests …". It
# is a 429, which pi retries by its status alone, as the plan intends (llama-swap's cap frees in a moment).
RETRIED_BY_STATUS = {"concurrency_limit"}


@pytest.mark.parametrize("change", _boundaries())
def test_no_refusal_makes_pi_retry_at_any_boundary(change):
    checked = 0
    for _, code, moment, _ in EVERY_ROW:
        for words in ("dan", "agent"):
            for names in (True, False):
                message = refusal(code, replace(moment, words=words, names_processes=names, **change)).message
                if code in RETRIED_BY_STATUS:
                    assert pi_retry_match(message) == "Too many requests", message
                    continue
                assert not pi_retries(message) and not pi_retries(json.dumps(message)), message
                checked += 1
    assert checked == 4 * (len(EVERY_ROW) - 1)


def test_a_size_over_400_gib_reads_more_than_400_gib():
    # A number a message shows is never altered. No size over 400 GiB can occur on this box (render keeps every
    # footprint under the ceiling), and "more than 400 GiB" is true and matches nothing in pi's list, whose smallest
    # number is 429.
    big = replace(NO_FIT_DAN, needed_gib=500, free_gib=401, available_gib=520, holders=[Holder("Gemma", 502, False)])
    assert refusal("no_fit", big).message == (
        "The coder didn't load: it needs more than 400 GiB, and more than 400 GiB is free for a load (more than "
        "400 GiB available, less the 24 GiB reserve and the 6 GiB the loaded models may still grow into). Using memory "
        "now: Gemma more than 400 GiB. The Spark can never free that much.")
    assert "(more than 400 GiB available)" in refusal("held_by_brake", replace(HELD, brake_available_gib=503)).message
    assert "above more than 400 GiB available" in refusal("held_by_brake", replace(HELD, warn_gib=520)).message
    assert "(more than 400 GiB)" in refusal("not_downloaded", replace(NOT_DOWNLOADED, download_gib=429)).message
    # Up to 400, every number shows as it is: exactly 400 is "400 GiB", and a need of 399.5 rounds up to it.
    under = refusal("no_fit", replace(NO_FIT_DAN, needed_gib=399, free_gib=369.9, available_gib=399.9)).message
    assert "it needs 399 GiB, and 369 GiB is free for a load (399 GiB available," in under
    assert "`spark make-room 399G`" in under
    at = refusal("no_fit", replace(NO_FIT_DAN, needed_gib=399.5, free_gib=370.5, available_gib=400.5)).message
    assert "it needs 400 GiB, and 370 GiB is free for a load (400 GiB available," in at
    assert "`spark make-room 400G` on the Spark, then try again." in at
    assert "(399.9 GiB available)" in refusal("held_by_brake", replace(HELD, brake_available_gib=399.99)).message


def test_a_load_failed_never_quotes_words_pi_would_retry():
    timed_out = replace(LOAD_FAILED, engine_said="CUDA error: operation timed out")
    assert refusal("load_failed", timed_out).message == LOAD_FAILED_UNQUOTED_TEXT
    assert not pi_retries(pi_shows(409, "load_failed", timed_out))
    assert refusal("load_failed", LOAD_FAILED).message == LOAD_FAILED_TEXT
    # JSON writes a character outside ASCII as \uXXXX, so a box-drawing line, U+2503, would show pi a "503".
    boxed = replace(LOAD_FAILED, engine_said="┃ failed to load model")
    assert refusal("load_failed", boxed).message == LOAD_FAILED_UNQUOTED_TEXT
    assert not pi_retries(pi_shows(409, "load_failed", boxed))
    # A line break or a tab never reaches the sentence: the words around it are quoted on one line.
    broken = replace(LOAD_FAILED, engine_said="failed to load\nmodel\t")
    assert refusal("load_failed", broken).message == LOAD_FAILED_TEXT
    # Nothing the engine said, and no deadline: the plain variant.
    assert refusal("load_failed", replace(LOAD_FAILED, engine_said=None)).message == LOAD_FAILED_UNQUOTED_TEXT
    assert refusal("load_failed", replace(LOAD_FAILED, engine_said=" \n")).message == LOAD_FAILED_UNQUOTED_TEXT


def test_pi_retry_match_names_what_pi_would_match():
    assert pi_retry_match("the 500m model") == "500"
    assert pi_retry_match("┃top") == "503"  # as JSON writes it: ┃top
    assert pi_retry_match("the coder") is None


def test_retry_after_follows_the_ruling():
    assert RETRY_AFTER_S == {"no_fit": 30, "held_by_brake": 300, "gate_down": 30, "restarting": 60, "draining": 60,
                             "llama_swap_down": 30, "too_many_requests": 10}
    for code in EVERY_CODE:
        refused = refusal(code, MOMENT[code])
        assert refused.retry_after_s == RETRY_AFTER_S.get(code), code
        expected = [] if refused.retry_after_s is None else [str(refused.retry_after_s).encode()]
        assert [value for name, value in refused.headers() if name == b"retry-after"] == expected, code


def test_409s_and_503s_say_dont_retry():
    for code in EVERY_CODE:
        refused = refusal(code, MOMENT[code])
        assert (DONT_RETRY in refused.headers()) == (refused.status in (409, 503)), code


def test_the_body_is_openais_error_shape_with_no_detail():
    assert refusal("no_fit", NO_FIT_DAN).body() == {
        "error": {"message": NO_FIT_DAN_TEXT, "code": "no_fit"}, "retry_after_s": 30}
    assert refusal("loading", LOADING).body() == {"error": {"message": LOADING_TEXT, "code": "loading"}}
    for code in EVERY_CODE:
        body = refusal(code, MOMENT[code]).body()
        assert set(body) <= {"error", "retry_after_s"} and set(body["error"]) == {"message", "code"}, code


def test_nothing_free_for_a_load_never_reads_negative():
    assert refusal("no_fit", NO_FIT_AGENT_CLAMPED).message == NO_FIT_CLAMPED_TEXT
    checked = 0
    for base in (NO_FIT_DAN, NO_FIT_AGENT):
        for free in (Decimal(-58), -6, -0.1, 0, 0.4, Decimal("0.99")):
            for available in (0, 24.9, 36, 90):
                for held, counted in ((0, False), (70, False), (70, True)):
                    moment = replace(base, free_gib=free, available_gib=available, held_gib=held, hold_counted=counted)
                    message = refusal("no_fit", moment).message
                    assert "nothing is free for a load" in message, moment
                    assert not re.search(r"[-−]\s*\d", message), message
                    assert " 0 GiB is free" not in message, message
                    checked += 1
    assert checked == 144
    # Below 1 GiB, but above 0: still nothing, never "0 GiB".
    almost = refusal("no_fit", replace(NO_FIT_DAN, free_gib=0.9, available_gib=30.9)).message
    assert "and nothing is free for a load (30 GiB available, less the 24 GiB reserve and the 6 GiB" in almost


def test_free_for_a_load_is_the_gates_own_figure():
    # The words never work out a figure of their own: 17.99 from the gate reads 17, rounded down as admission's is.
    assert "and 17 GiB is free for a load" in refusal("no_fit", replace(NO_FIT_DAN, free_gib=Decimal("17.99"))).message


def test_a_breakdown_adds_up_as_shown():
    # Whole GiB when the whole terms already add up to the figure shown: 48.3 − 24 − 6.2 = 18.1 reads 48 − 24 − 6.
    whole = refusal("no_fit", replace(NO_FIT_DAN, free_gib=Decimal("18.1"), available_gib=48.3, owed_gib=6.2)).message
    assert whole == NO_FIT_DAN_TEXT
    # Off by one in whole GiB (48 − 24 − 7 = 17 against 18): every term goes to one decimal, and the figure stays
    # whole, rounded down (the controller's ruling, Task 6's fix round 3). No number is altered.
    tenths = replace(NO_FIT_DAN, free_gib=Decimal("18.3"), available_gib=48.9, owed_gib=6.6)
    assert refusal("no_fit", tenths).message == NO_FIT_DAN_TEXT.replace(
        NO_FIT_DAN_BREAKDOWN,
        "18 GiB is free for a load (48.9 GiB available, less the 24 GiB reserve and the 6.6 GiB the loaded models may "
        "still grow into)")
    # The ceiling's term the same way: 101 − 92 = 9 against 10, so 101.9 − 91.6 = 10.3.
    ceiling = replace(NO_FIT_DAN, free_gib=Decimal("10.3"), ceiling_gib=101.9, committed_gib=91.6)
    assert ("10 GiB is free for a load (the 101.9 GiB the GPU can allocate, less the 91.6 GiB the loaded models may "
            "grow to)." in refusal("no_fit", ceiling).message)
    # Where one decimal is off too (48.9 − 24 − 7.0 = 17.9 against 18), two: 48.95 − 24 − 6.95 = 18.
    hundredths = replace(NO_FIT_DAN, free_gib=Decimal("18.00"), available_gib=48.95, owed_gib=6.95)
    assert ("18 GiB is free for a load (48.95 GiB available, less the 24 GiB reserve and the 6.95 GiB the loaded "
            "models may still grow into)." in refusal("no_fit", hundredths).message)
    # A hold counts in the sum too, named after the figure when nothing is left: 36.4 − 24 − 70 is nothing either way.
    clamped = replace(NO_FIT_AGENT_CLAMPED, free_gib=Decimal("-57.6"), available_gib=36.4)
    assert refusal("no_fit", clamped).message == NO_FIT_CLAMPED_TEXT


def test_the_breakdown_is_the_term_that_gave_the_figure():
    # The ceiling binds: free for a load is ceiling − committed, and the parenthesis shows that sum, not memory's.
    ceiling = replace(NO_FIT_DAN, free_gib=Decimal(10), ceiling_gib=102, committed_gib=92)
    assert refusal("no_fit", ceiling).message == NO_FIT_DAN_TEXT.replace(
        NO_FIT_DAN_BREAKDOWN,
        "10 GiB is free for a load (the 102 GiB the GPU can allocate, less the 92 GiB the loaded models may grow to)")
    # A model still starting: its footprint comes off too (rule 9's − starting).
    starting = replace(NO_FIT_DAN, free_gib=Decimal(17), available_gib=74, starting_gib=27)
    assert refusal("no_fit", starting).message == NO_FIT_DAN_TEXT.replace(
        NO_FIT_DAN_BREAKDOWN,
        "17 GiB is free for a load (74 GiB available, less the 24 GiB reserve, the 6 GiB the loaded models may still "
        "grow into and the 27 GiB the model still starting may take)")
    # Both: the ceiling binds while a model is starting.
    both = replace(ceiling, free_gib=Decimal(13), committed_gib=62, starting_gib=27)
    assert refusal("no_fit", both).message == NO_FIT_DAN_TEXT.replace(
        NO_FIT_DAN_BREAKDOWN,
        "13 GiB is free for a load (the 102 GiB the GPU can allocate, less the 62 GiB the loaded models may grow to "
        "and the 27 GiB the model still starting may take)")
    # agent's counted hold under the ceiling, and that hold's clamp.
    held = replace(NO_FIT_AGENT, free_gib=Decimal(10), ceiling_gib=102, committed_gib=62, held_gib=30)
    assert ("10 GiB is free for a load (the 102 GiB the GPU can allocate, less the 62 GiB the loaded models may grow "
            "to and the 30 GiB make-room holds for Dan)." in refusal("no_fit", held).message)
    clamped = replace(held, free_gib=Decimal(-20), held_gib=60)
    assert ("nothing is free for a load while make-room holds 60 GiB for Dan (the 102 GiB the GPU can allocate, less "
            "the 62 GiB the loaded models may grow to)." in refusal("no_fit", clamped).message)


def test_no_outside_text_makes_pi_retry_a_refusal():
    # A process's name comes from /proc, not the registry: one that pi's list matches is named only as a process, so pi
    # never retries the refusal because of it. The guard is load_failed's, for the engine's line.
    holders = [Holder("timeout (chendaniely)", 32, True), Holder("terminated (agent)", 8, False), GEMMA]
    moment = replace(NO_FIT_DAN, holders=holders)
    message = refusal("no_fit", moment).message
    assert "Using memory now: a process of Dan's, 32 GiB, a process, 8 GiB, Gemma 27 GiB." in message
    assert not pi_retries(pi_shows(409, "no_fit", moment))
    # One that would match only as JSON writes it (U+2503 becomes ┃), and one with a line break in its name.
    odd = replace(NO_FIT_DAN, holders=[Holder("┃top (agent)", 4, False), Holder("python3\n(chendaniely)", 32, True)])
    assert "Using memory now: a process, 4 GiB, python3 (chendaniely) 32 GiB." in refusal("no_fit", odd).message
    assert not pi_retries(pi_shows(409, "no_fit", odd))
    # A client's own model name, in a 404 that pi would otherwise retry three times.
    asked = Moment(asked_name="gpt-timeout", models=MODELS, **DAN)
    assert refusal("model_not_found", asked).message == MODEL_NOT_FOUND_TEXT.replace(
        "There's no model called qwen3.6-35b-a3b here.", "There's no model by that name here.")
    assert not pi_retries(pi_shows(404, "model_not_found", asked))
    assert refusal("model_not_found", replace(asked, asked_name="qwen3.6\n35b")).message.startswith(
        "There's no model called qwen3.6 35b here.")
    # A client can send an empty name, or one of spaces: it is answered, never an error.
    for empty in ("", " \n"):
        assert refusal("model_not_found", replace(asked, asked_name=empty)).message.startswith(
            "There's no model by that name here. The models are")


# The commands only Dan can run, on the control socket or with Dan's account. agent's words may name one only in a
# sentence that says it is Dan's step (the controller's rulings, Task 6's fix rounds 1 and 2: no exception is left).
DANS_COMMANDS = ("`spark make-room", "`make brake-release`", "`spark load", "`spark logs", "`make pull`",
                 "`make doctor`", "`make clients`")


def test_agent_is_never_told_to_run_what_only_dan_can():
    checked = set()
    for _, code, moment, _ in EVERY_ROW:
        message = refusal(code, replace(moment, words="agent", names_processes=False, key_label="agent")).message
        for sentence in re.split(r"(?<=[.;])\s+", message):
            if any(command in sentence for command in DANS_COMMANDS):
                assert "Dan" in sentence, sentence
                checked.add(sentence)
    # make-room's end (no_fit's and draining's, one sentence), the brake's release (two forms), spark load, spark logs,
    # make pull and make doctor.
    assert len(checked) == 7


def test_dans_own_hold_is_never_counted_against_dan():
    # make-room's hold is Dan's to load into (rule 4): a hold standing changes nothing in a no_fit for Dan's key.
    assert refusal("no_fit", replace(NO_FIT_DAN, held_gib=41, hold_counted=False)).message == NO_FIT_DAN_TEXT


def test_agent_names_dans_processes_only_as_a_process_of_dans():
    message = refusal("no_fit", replace(NO_FIT_DAN, **AGENT)).message
    assert "Using memory now: a process of Dan's, 32 GiB, Gemma 27 GiB." in message
    assert "python3" not in message and "chendaniely" not in message


def test_the_wording_follows_the_groups_words():
    # agent's words at the examples' moment, with no hold of Dan's to end: only Dan can make room, and make-room's
    # hold would bar agent anyway (rule 4).
    assert refusal("no_fit", replace(NO_FIT_DAN, words="agent", names_processes=True)).message == (
        NO_FIT_DAN_TEXT.replace("Free space with `spark make-room 41G` on the Spark, then try again.",
                                "Only Dan can free memory for it, on the Spark; try again after that."))
    assert refusal("no_fit", replace(NO_FIT_DAN, words="dan", names_processes=False)).message == (
        NO_FIT_DAN_TEXT.replace("python3 (chendaniely) 32 GiB", "a process of Dan's, 32 GiB"))
    # agent's words beside Dan's hold, with Dan's processes named because the group's names_processes says so.
    held = refusal("no_fit", replace(NO_FIT_AGENT, holders=[PYTHON, GEMMA], names_processes=True)).message
    assert held.endswith("Using memory now: python3 (chendaniely) 32 GiB, Gemma 27 GiB. The hold ends when Dan runs "
                         "`spark make-room --done` on the Spark.")
    # A group with Dan's words whose hold is counted (one without uses_hold) keeps the plan's step for Dan.
    assert refusal("no_fit", replace(NO_FIT_AGENT, words="dan")).message.endswith(
        "On the Spark, `spark make-room --done` ends the hold.")


def test_sizes_round_against_the_load():
    moment = replace(NO_FIT_DAN, needed_gib=40.2, free_gib=17.9, available_gib=47.9,
                     holders=[Holder("Gemma", 26.6, False)])
    message = refusal("no_fit", moment).message
    assert "it needs 41 GiB" in message and "`spark make-room 41G`" in message
    assert "(47 GiB available" in message
    assert "and 17 GiB is free for a load" in message  # the gate's 17.9, rounded down
    assert "Using memory now: Gemma 27 GiB." in message
    assert "(19.6 GiB available)" in refusal("held_by_brake", replace(HELD, brake_available_gib=19.64)).message
    # A reading just under the brake's line rounds down, so it never reads as at the line.
    assert "(19.9 GiB available)" in refusal("held_by_brake", replace(HELD, brake_available_gib=19.96)).message
    # Read exactly: as a binary float 19.7 is 19.69999…, which would round down to 19.6.
    assert "(19.7 GiB available)" in refusal("held_by_brake", replace(HELD, brake_available_gib=19.7)).message
    # An owed growth that rounds to 0 is left out of a whole-GiB breakdown, never shown as "0 GiB".
    small = refusal("no_fit", replace(NO_FIT_DAN, free_gib=24.0, available_gib=48.3, owed_gib=0.3)).message
    assert "and 24 GiB is free for a load (48 GiB available, less the 24 GiB reserve)." in small


def test_held_by_brake_names_the_warn_line_and_the_release_time_it_is_given():
    # The registry's brake.warn_gib and the gate's release time, so the words stay true when either changes.
    changed = refusal("held_by_brake", replace(HELD, warn_gib=30.0, release_after_s=600)).message
    assert "They resume by themselves after 10 minutes above 30 GiB available," in changed
    assert "after 5 minutes above 28.5 GiB available," in refusal("held_by_brake", replace(HELD, warn_gib=28.5)).message


def test_times_are_the_local_24_hour_clock():
    afternoon = datetime(2026, 10, 7, 22, 7, tzinfo=timezone.utc)  # 15:07 in the tests' zone
    assert "memory ran low at 15:07 (" in refusal("held_by_brake", replace(HELD, brake_at=afternoon)).message
    tokyo = datetime(2026, 10, 8, 7, 7, tzinfo=timezone(timedelta(hours=9)))  # the same moment
    assert "the brake fired at 15:07, so" in refusal("footprint_suspect", replace(SUSPECT, brake_at=tokyo)).message


def test_durations_read_in_seconds_under_a_minute_then_in_minutes_and_seconds():
    for seconds, words in ((30, "30 s"), (59, "59 s"), (59.6, "1 minute"), (60, "1 minute"), (90, "1 minute 30 s"),
                           (180, "3 minutes"), (500, "8 minutes 20 s"), (600, "10 minutes")):
        assert duration(seconds) == words
        assert f"and your {words} ran out." in refusal("restarting", replace(MOMENT["restarting"],
                                                                             wait_s=seconds)).message


def test_a_label_starting_with_the_is_capitalised_only_at_a_sentences_start():
    web_ui = refusal("too_many_requests", Moment(key_label="the web UI")).message
    assert web_ui.startswith("The web UI already has as many requests")
    assert refusal("not_downloaded", Moment(model_label="whisper", download_gib=2, words="dan")).message.startswith(
        "whisper isn't downloaded yet.")
    assert "Not loading Gemma for the web UI:" in refusal(
        "footprint_suspect", replace(SUSPECT, model_label="Gemma", key_label="the web UI")).message


def test_requests_in_flight_read_as_words():
    two = refusal("draining", replace(MOMENT["draining"], inflight=2)).message
    assert two.startswith("The coder is being unloaded for make-room once its 2 requests in flight finish, and")
    none = refusal("draining", replace(MOMENT["draining"], inflight=0, drain_for="idle")).message
    assert none == "The coder is being unloaded, and your 30 s ran out. Try again in a minute."


# What each code's words use, which a Moment must carry: a field left at its default is refused, by code and name.
NEEDS = {
    "no_fit": ("model_label", "needed_gib", "free_gib", "available_gib", "reserve_gib", "words"),
    "loading": ("model_label", "loading_label", "wait_s"),
    "held_by_brake": ("model_label", "brake_at", "brake_available_gib", "words"),
    "footprint_suspect": ("model_label", "key_label", "model_command", "brake_at"),
    "load_failed": ("model_label", "model_command", "words"),
    "not_downloaded": ("model_label", "words"),
    "restarting": ("wait_s",),
    "llama_swap_down": ("wait_s", "words"),
    "draining": ("model_label", "wait_s", "drain_for", "words"),
    "gate_down": ("words",),
    "model_not_found": ("asked_name", "models", "words"),
    "too_many_requests": ("key_label",),
    "route_not_served": (),
    "concurrency_limit": ("model_label",),
}


def _left_out(name: str):
    """What a Moment holds for a field nobody filled in: its default."""
    field = next(f for f in fields(Moment) if f.name == name)
    return field.default_factory() if field.default is MISSING else field.default


@pytest.mark.parametrize("code, name", [(code, name) for code, names in NEEDS.items() for name in names])
def test_each_code_refuses_a_moment_without_the_fields_its_words_use(code, name):
    with pytest.raises(ValueError, match=rf"^{code}'s words need {name}\b"):
        refusal(code, replace(MOMENT[code], **{name: _left_out(name)}))


def test_some_fields_are_needed_only_where_the_words_use_them():
    assert set(NEEDS) == set(EVERY_CODE)
    # With the ceiling binding, the breakdown is the ceiling's: MemAvailable and the reserve aren't used.
    ceiling = replace(NO_FIT_DAN, ceiling_gib=102, committed_gib=84, available_gib=None, reserve_gib=None)
    assert "(the 102 GiB the GPU can allocate, less the 84 GiB" in refusal("no_fit", ceiling).message
    # agent's draining for make-room names agent's key; any other drain doesn't.
    with pytest.raises(ValueError, match="^draining's words need key_label"):
        refusal("draining", replace(DRAINING_AGENT, key_label=""))
    assert refusal("draining", replace(DRAINING_DAN, key_label="")).message.endswith("if it fits.")


def test_the_front_builds_its_own_refusals_with_defaults():
    assert refusal("restarting", Moment(model_label="the coder", wait_s=30)).message == RESTARTING_TEXT
    assert refusal("draining", Moment(model_label="the coder", wait_s=30, inflight=1, drain_for="unload",
                                      words="dan", key_label="pi on the Mac")).message == DRAINING_UNLOAD_TEXT
    assert refusal("model_not_found", Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, words="dan",
                                             key_label="pi on the Mac")).message == MODEL_NOT_FOUND_TEXT
    assert refusal("concurrency_limit", Moment(model_label="the coder")).message == CONCURRENCY_TEXT
    assert refusal("gate_down", Moment(words="dan")).message == GATE_DOWN_TEXT


IMPORTS = textwrap.dedent(
    """
    import json, sys
    import spark.messages
    print(json.dumps(sorted(name for name in sys.modules if name.split(".")[0] == "spark")))
    """
)


def test_messages_imports_no_more_than_the_registry(tmp_path):
    # The front imports messages (Task 21's FRONT_MODULES): were the budget pulled in, a change there would restart it.
    result = subprocess.run([sys.executable, "-c", IMPORTS], cwd=tmp_path, capture_output=True, text=True, timeout=60,
                            check=False)
    assert result.returncode == 0, result.stderr
    loaded = set(json.loads(result.stdout))
    assert "spark.messages" in loaded and "spark.budget" not in loaded
    assert loaded <= {"spark", "spark.messages", "spark.registry"}


def test_the_unknown_key_text_is_the_rulings():
    assert UNKNOWN_KEY == "That API key isn't one the Spark knows. Check SPARK_API_KEY on this machine."


# The notifications, on Dan's phone (plan.md, *Notifications, on the phone*), and what each command says (*Each command
# says what it did, and how to undo it*). The stack's registry gives each type's priority, the warn line and the labels.
REGISTRY = load_registry(STACK_REGISTRY)


def at(hour: int, minute: int) -> datetime:
    """A moment on the examples' day, in the tests' zone."""
    return datetime(2026, 10, 7, hour, minute, tzinfo=ZONE)


def say(kind: str, **given) -> str:
    return messages.notification(kind, REGISTRY, **given).message


FIRED = {"at": AT_0312, "available_gib": 19.6, "line_gib": 20, "unloaded": [("the coder", "starting")],
         "release_waits_for_dan": False, "release_after_s": 300}
DOWN = {"at": at(9, 14), "result_words": "it crashed"}
SUSPECT_FIELDS = {"model_label": "the coder", "key_label": "agent", "fired_at": AT_0312, "command": "coder"}
FAILED_FIELDS = {"model_label": "the coder", "command": "coder", "engine_said": "failed to load model"}
HOLD_ENDED = {"why": "--done", "unused_gib": 40, "total_gib": 40, "reloading": [], "next_label": "the coder"}
WAITING = {"label": "the coder", "key_label": "pi on the Mac", "why": "memory", "wait_s": 30, "needed_gib": 41,
           "free_gib": 18}
BURST = {"model_label": "the coder", "key_label": "agent", "count": 4, "since": at(9, 12)}

# plan.md's notification table, each row's Example with the inputs that give it (the fields as phase-2a.md's Task 7
# gives them, with the controller's additions of 2026-10-07), and the follow-up and the variant it gives in full.
NOTIFICATION_ROWS = [
    ("brake_fired", FIRED,
     "Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line. Unloaded the coder, which was loading; "
     "new loads are paused. They resume by themselves after 5 min above 28 GiB available."),
    ("brake_fired", {"at": at(3, 13), "unloaded": [("Gemma", "idle"), ("the embeddings", "idle")], "follow_up": True},
     "Brake, 03:13: also unloaded Gemma and the embeddings, both idle."),
    ("brake_needs_release", {"fired_at": at(2, 58)},
     "After the reboot, new loads are still paused from the brake at 02:58. On the Spark, `make brake-release` "
     "resumes them."),
    ("gate_down", DOWN,
     "The gate on brightroar stopped at 09:14 (it crashed; it is restarting). Loaded models still answer; new loads "
     "are refused until it's back. On the Spark, `make doctor` shows what's wrong."),
    ("front_down", DOWN,
     "The front on brightroar stopped at 09:14 (it crashed; it is restarting). Requests wait for it, and any in "
     "flight were cut off. On the Spark, `make doctor` shows what's wrong."),
    ("llama_swap_down", {"at": at(9, 14)},
     "The model service on brightroar stopped at 09:14. No model answers until it's back; requests wait, then are "
     "refused. On the Spark, `make doctor` shows what's wrong."),
    ("brake_down", DOWN,
     "The memory brake on brightroar stopped at 09:14 (it crashed; it is restarting within 2 s). earlyoom stays the "
     "backstop. On the Spark, `make doctor` shows what's wrong."),
    ("back_up", {"unit": "gate", "down_s": 12},
     "The gate on brightroar has been running again for a minute, after 12 s down. New loads work again."),
    ("refused", {"code": "no_fit", "moment": NO_FIT_DAN},
     "Refused the coder for pi on the Mac: needs 41 GiB, 18 free for a load; python3 (chendaniely) holds 32. Free "
     "space with `spark make-room 41G` on the Spark, then try again."),
    ("footprint_suspect", SUSPECT_FIELDS,
     "Didn't load the coder for agent: it was loading when the brake fired at 03:12. On the Spark, `spark load coder` "
     "allows it again; your own requests load it if it fits."),
    ("load_failed", FAILED_FIELDS,
     'The coder failed to load: the engine stopped with "failed to load model". On the Spark, `spark logs coder` '
     "shows the engine's last lines."),
    ("brake_released",
     {"at": at(3, 40), "available_gib": 64, "reloaded": ["Gemma", "the embeddings"], "loading_label": "the coder"},
     "Brake released at 03:40, 64 GiB available. Reloaded Gemma and the embeddings. The coder was loading when it "
     "fired, so it loads again only when you ask."),
    ("room_hold_ended", HOLD_ENDED,
     "make-room's hold for you ended (`--done`), all 40 GiB of it unused. Nothing to reload: the coder loads on its "
     "next request."),
    ("room_hold_ended", {**HOLD_ENDED, "reloading": ["Gemma"], "next_label": None},
     "make-room's hold for you ended (`--done`), all 40 GiB of it unused. Reloading Gemma."),
    ("resident_waiting", {"label": "Gemma", "needed_gib": 32, "free_gib": 9, "after": "hold"},
     "Gemma didn't fit after the hold ended: it needs 32 GiB, and 9 GiB is free for a load. It loads by itself once "
     "there's room."),
    ("apply_restarted",
     {"at": at(14, 2), "reloaded": ["Gemma", "the embeddings", "whisper"], "on_demand": ["the coder"]},
     "make apply restarted the model service at 14:02. Gemma, the embeddings and whisper reloaded; the coder loads on "
     "its next request."),
    ("load_started", {"label": "the coder", "key_label": "pi on the Mac", "last_s": 24},
     "Loading the coder for pi on the Mac (24 s last time)…"),
    ("loaded", {"label": "the coder", "seconds": 24}, "Loaded the coder in 24 s."),
    ("unloaded", {"label": "the coder", "why": "idle", "idle_min": 60}, "Unloaded the coder after 60 min idle."),
    ("waiting", WAITING,
     "Waiting for memory: the coder for pi on the Mac, up to 30 s. It needs 41 GiB, and 18 GiB is free for a load."),
    ("pin_ended", {"label": "the coder", "at": at(18, 0), "idle_min": 60},
     "The pin on the coder ended at 18:00; it unloads after 60 min idle."),
    ("memory_warning", {"available_gib": 27.4, "warn_gib": 28, "brake_gib": 20},
     "Memory is getting low on brightroar: 27.4 GiB available, under the 28 GiB warning line. The brake acts at 20."),
]
# The variants phase-2a.md and plan.md give in full elsewhere: the brake's own alert, the second brake within the hour
# (rule 5: its own alert says it waits for Dan; the controller's ruling, 2026-10-07), and the burst (*One notification
# per event*).
WITHIN_THE_HOUR = {**FIRED, "at": at(3, 50), "release_waits_for_dan": True, "released_at": at(3, 40)}
DANS_RELEASE = ("It fired within an hour of the automatic release at 03:40, so they stay paused until you release "
                "them: on the Spark, `make brake-release`.")
PLAN_VARIANTS = [
    ("brake_fired", {**FIRED, "by_brake": True},
     "Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line. Unloaded the coder, which was loading; "
     "new loads are paused. They resume once the gate is back and memory has stayed above 28 GiB available for "
     "5 min."),
    ("brake_fired", WITHIN_THE_HOUR,
     "Brake on brightroar at 03:50: 19.6 GiB available, under the 20 GiB line. Unloaded the coder, which was loading; "
     f"new loads are paused. {DANS_RELEASE}"),
    ("footprint_suspect", {**BURST, "code": "footprint_suspect"},
     "Didn't load the coder for agent 4 more times since 09:12: same reason."),
]


def _plain(markdown: str) -> str:
    return markdown.replace("*", "")


@pytest.mark.parametrize("kind, given, text",
                         [pytest.param(k, g, t, id=f"{k}-{i}") for i, (k, g, t) in enumerate(NOTIFICATION_ROWS)])
def test_each_notification_reads_word_for_word(kind, given, text):
    built = messages.notification(kind, REGISTRY, **given)
    assert built.message == text
    assert (built.type, built.priority) == (kind, REGISTRY.notifications[kind])
    # The notifications page shows each example as the plan's table has it, so the page stays true to the words.
    assert text in _plain(messages.NOTIFICATION_DOC[kind][1])


@pytest.mark.parametrize("kind, given, text",
                         [pytest.param(k, g, t, id=f"{k}-{i}") for i, (k, g, t) in enumerate(PLAN_VARIANTS)])
def test_the_plans_other_variants_read_word_for_word(kind, given, text):
    assert say(kind, **given) == text


def test_a_brake_within_the_hour_of_an_automatic_release_sends_one_alert_for_dans_release():
    # Rule 5: a brake within the hour after an automatic release holds until Dan releases it, and its high-priority
    # alert says so: brake_fired itself, one event, one notification, in held_by_brake's words for Dan.
    for given in (WITHIN_THE_HOUR, {**WITHIN_THE_HOUR, "by_brake": True}):
        built = messages.notification("brake_fired", REGISTRY, **given)
        assert built.priority == "high"
        assert built.message.endswith(f"new loads are paused. {DANS_RELEASE}")
        assert "resume by themselves" not in built.message and "once the gate is back" not in built.message
    # It says why this hold waits, unlike the last (the review's wording, the controller's ruling), and ends with
    # held_by_brake's words for Dan.
    tail = "stay paused until you release them: on the Spark, `make brake-release`."
    assert DANS_RELEASE.endswith(tail)
    assert refusal("held_by_brake", replace(HELD, release_waits_for_dan=True)).message.endswith(tail)
    with pytest.raises(ValueError, match="^brake_fired's words need released_at"):
        say("brake_fired", **{**WITHIN_THE_HOUR, "released_at": None})
    # brake_needs_release is sent only for a hold found after a reboot: it no longer words the second brake.
    with pytest.raises(ValueError, match="^brake_needs_release's words take no why"):
        say("brake_needs_release", fired_at=at(3, 50), why="again", released_at=at(3, 40))
    assert DANS_RELEASE in _plain(messages.NOTIFICATION_DOC["brake_fired"][1])
    assert "within an hour" not in messages.NOTIFICATION_DOC["brake_needs_release"][1]


def test_notification_doc_has_each_type_once_with_its_when_and_example():
    assert list(messages.NOTIFICATION_DOC) == list(NOTIFICATION_TYPES)
    assert all(when and example for when, example in messages.NOTIFICATION_DOC.values())


def test_a_load_failed_notification_quotes_the_engine_either_way():
    # pi never sees a notification, so the engine's line is quoted even where the refusal leaves it out.
    for line in ("CUDA error: operation timed out", "failed to load model"):
        assert f'the engine stopped with "{line}".' in say("load_failed", **{**FAILED_FIELDS, "engine_said": line})
    assert say("load_failed", **{**FAILED_FIELDS, "engine_said": "failed to load\nmodel\t"}) == NOTIFICATION_ROWS[10][2]


def test_two_requests_joined_to_one_failed_start_send_one_load_failed():
    # One failed load is one event: every request that was waiting on it gets the load_failed refusal, and the phone
    # gets one load_failed, keyed by the load's ticket (the controller's ruling on the review's I-3). Every other
    # refusal is its request's own.
    joined = {messages.refusal_notification("load_failed", request_id=r, ticket_id="t1") for r in ("r1", "r2")}
    assert joined == {("load_failed", "load-failed:t1")}
    assert messages.refusal_notification("load_failed", request_id="r3", ticket_id="t2") == (
        "load_failed", "load-failed:t2")
    assert {messages.refusal_notification("no_fit", request_id=r) for r in ("r1", "r2")} == {
        ("refused", "refusal:r1"), ("refused", "refusal:r2")}
    assert messages.refusal_notification("footprint_suspect", request_id="r1") == (
        "footprint_suspect", "refusal:r1")
    assert messages.refusal_notification("gate_down", request_id="r1") is None  # the failure notifier's alert says it
    with pytest.raises(ValueError, match="^load_failed's notification is its load's: it needs ticket_id"):
        messages.refusal_notification("load_failed", request_id="r1")
    with pytest.raises(ValueError, match="'no_room' isn't a refusal"):
        messages.refusal_notification("no_room", request_id="r1")
    # Its words name no key, so the requests that shared the load share the one alert.
    with pytest.raises(ValueError, match="^load_failed's words take no key_label"):
        say("load_failed", **FAILED_FIELDS, key_label="agent")


def test_an_off_notification_sends_nothing():
    off = replace(REGISTRY, notifications={**REGISTRY.notifications, "loaded": "off"})
    assert messages.notification("loaded", off, label="the coder", seconds=24) is None
    # A type that is off still checks its fields, so a caller's mistake shows while it is off too.
    with pytest.raises(ValueError, match="^loaded's words need seconds"):
        messages.notification("loaded", off, label="the coder")


def test_each_refusal_sends_exactly_one_type():
    assert messages.REFUSAL_NOTIFICATION == {"footprint_suspect": "footprint_suspect", "load_failed": "load_failed"}
    sent = {code: messages.REFUSAL_NOTIFICATION.get(code, "refused") for code in EVERY_CODE}
    assert sent == {**dict.fromkeys(EVERY_CODE, "refused"), "footprint_suspect": "footprint_suspect",
                    "load_failed": "load_failed"}
    # refused never words a refusal that has a type of its own, nor gate_down, which the failure notifier's alert says.
    for code, moment in (("footprint_suspect", SUSPECT), ("load_failed", LOAD_FAILED), ("gate_down", Moment(**DAN))):
        with pytest.raises(ValueError, match=f"^refused doesn't word {code}"):
            say("refused", code=code, moment=moment)


# Dan's phone gets every refusal in Dan's own words: what was refused, for whom, why, and Dan's step where there is one.
# The moments are Task 6's rows; agent's key words Dan's step for an agent's request.
FRONT_DAN = {"key_label": "pi on the Mac", "words": "dan"}
REFUSED_ROWS = [
    ("no_fit, agent, Dan's hold counted", NO_FIT_AGENT,
     "Refused the coder for agent: needs 41 GiB, 12 free for a load while make-room holds 70 GiB for you. On the "
     "Spark, `spark make-room --done` ends the hold."),
    # Ending the hold would leave 36 − 24 = 12 GiB, short of 41: its end isn't the step (the review's minor 10).
    ("no_fit, agent, Dan's job in the hold", NO_FIT_AGENT_CLAMPED,
     "Refused the coder for agent: needs 41 GiB, nothing free for a load while make-room holds 70 GiB for you; "
     "python3 (chendaniely) holds 70. On the Spark, `spark status` shows what's using memory."),
    ("no_fit, agent, no hold", replace(NO_FIT_DAN, **AGENT),
     "Refused the coder for agent: needs 41 GiB, 18 free for a load; python3 (chendaniely) holds 32. On the Spark, "
     "`spark status` shows what's using memory."),
    ("no_fit, two processes outside the stack",
     replace(NO_FIT_DAN, holders=[PYTHON, Holder("srv (agent)", 8.4, False), GEMMA]),
     "Refused the coder for pi on the Mac: needs 41 GiB, 18 free for a load; python3 (chendaniely) holds 32 and srv "
     "(agent) holds 8. Free space with `spark make-room 41G` on the Spark, then try again."),
    ("no_fit, only models hold memory", replace(NO_FIT_DAN, holders=[GEMMA]),
     "Refused the coder for pi on the Mac: needs 41 GiB, 18 free for a load. Free space with `spark make-room 41G` on "
     "the Spark, then try again."),
    ("no_fit, more than the box", replace(NO_FIT_DAN, needed_gib=500, holders=[]),
     "Refused the coder for pi on the Mac: needs more than 400 GiB, 18 free for a load. The Spark can never free that "
     "much."),
    ("loading", LOADING,
     "Refused the coder for pi on the Mac: the 30 s wait ran out while Gemma loads, since one model loads at a time. "
     "Try again in a minute."),
    ("loading, agent", replace(LOADING, **AGENT),
     "Refused the coder for agent: the 10 min wait ran out while Gemma loads, since one model loads at a time."),
    ("held_by_brake", HELD,
     "Refused the coder for pi on the Mac: new loads are paused since memory ran low at 03:12 (19.6 GiB available). "
     "They resume by themselves after 5 min above 28 GiB available, if what would reload fits; on the Spark, "
     "`make brake-release` resumes them now."),
    ("held_by_brake, waits for Dan, agent", replace(HELD, release_waits_for_dan=True, **AGENT),
     "Refused the coder for agent: new loads are paused since memory ran low at 03:12 (19.6 GiB available), until you "
     "release them: on the Spark, `make brake-release`."),
    ("not_downloaded", NOT_DOWNLOADED,
     "Refused the coder for pi on the Mac: it isn't downloaded yet. On the Spark, `make pull` fetches it (16 GiB)."),
    ("restarting", Moment(**CODER, **DAN),
     "Refused the coder for pi on the Mac: the 30 s wait ran out while the model service restarts for a configuration "
     "change. Try again in a minute."),
    ("restarting, agent", Moment(**CODER, **AGENT),
     "Refused the coder for agent: the 10 min wait ran out while the model service restarts for a configuration "
     "change."),
    ("llama_swap_down, agent", Moment(**CODER, **AGENT),
     "Refused the coder for agent: the model service isn't answering. On the Spark, `make doctor` shows what's "
     "wrong."),
    ("draining for make-room", DRAINING_DAN,
     "Refused the coder for pi on the Mac: it is being unloaded for make-room, and the 30 s wait ran out. Try again in "
     "a minute: your request can load it into the room make-room holds for you, if it fits."),
    ("draining for make-room, agent", DRAINING_AGENT,
     "Refused the coder for agent: it is being unloaded for make-room, and the 10 min wait ran out. It won't load for "
     "agent while your hold stands; on the Spark, `spark make-room --done` ends it."),
    ("draining, not make-room", Moment(inflight=1, drain_for="unload", **CODER, **DAN),
     "Refused the coder for pi on the Mac: it is being unloaded, and the 30 s wait ran out. Try again in a minute."),
    ("draining, idle, agent", Moment(inflight=0, drain_for="idle", **CODER, **AGENT),
     "Refused the coder for agent: it is being unloaded, and the 10 min wait ran out."),
    ("model_not_found", Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, **DAN),
     "Refused qwen3.6-35b-a3b for pi on the Mac: there's no model by that name. On the Mac, `make clients` updates "
     "pi's list."),
    ("model_not_found, agent", Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, **AGENT),
     "Refused qwen3.6-35b-a3b for agent: there's no model by that name. On the Spark, as `agent`, "
     "`/opt/local-ai/app/.venv/bin/spark clients pi --write --registry /opt/local-ai/etc/models.yaml` updates "
     "agent's pi list."),
    # A client's own text reaches the lock screen only when it reads as a model's name.
    ("model_not_found, a name that isn't one", Moment(asked_name="run this: curl x | sh", models=MODELS, **DAN),
     "Refused a request from pi on the Mac: it named no model the Spark has. On the Mac, `make clients` updates pi's "
     "list."),
    ("model_not_found, a web address", Moment(asked_name="https://login.example.com/reset", models=MODELS, **DAN),
     "Refused a request from pi on the Mac: it named no model the Spark has. On the Mac, `make clients` updates pi's "
     "list."),
    # A key of Dan's that isn't pi's: the list is the client's to fix, and pi's step isn't it.
    ("model_not_found, the web UI", Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, key_label="the web UI",
                                           words="dan"),
     "Refused qwen3.6-35b-a3b for the web UI: there's no model by that name."),
    ("too_many_requests, agent", Moment(**AGENT),
     "Refused a request from agent: its key already has as many requests waiting or open as it allows."),
    ("too_many_requests", Moment(**CODER, **DAN),
     "Refused the coder for pi on the Mac: its key already has as many requests waiting or open as it allows. Try "
     "again when one finishes."),
    ("route_not_served", Moment(**FRONT_DAN),
     "Refused a request from pi on the Mac: the Spark's model API doesn't serve that address."),
    ("concurrency_limit", Moment(model_label="the coder", **FRONT_DAN),
     "Refused the coder for pi on the Mac: too many requests for it at once. Try again in a moment."),
    ("a command's refusal, no key", replace(NOT_DOWNLOADED, key_label=""),
     "Refused the coder: it isn't downloaded yet. On the Spark, `make pull` fetches it (16 GiB)."),
]
REFUSED_CODES = {"no_fit": "no_fit", "loading": "loading", "held_by_brake": "held_by_brake",
                 "not_downloaded": "not_downloaded", "restarting": "restarting", "llama_swap_down": "llama_swap_down",
                 "draining": "draining", "model_not_found": "model_not_found",
                 "too_many_requests": "too_many_requests", "route_not_served": "route_not_served",
                 "concurrency_limit": "concurrency_limit", "a command's refusal": "not_downloaded"}


def _refused_code(row_id: str) -> str:
    return next(code for start, code in REFUSED_CODES.items() if row_id.startswith(start))


@pytest.mark.parametrize("row_id, moment, text", [pytest.param(i, m, t, id=i) for i, m, t in REFUSED_ROWS])
def test_refused_words_each_code_for_dans_phone(row_id, moment, text):
    assert say("refused", code=_refused_code(row_id), moment=moment) == text


def test_refused_covers_every_code_it_sends():
    covered = {_refused_code(row_id) for row_id, _, _ in REFUSED_ROWS}
    assert covered == {code for code in EVERY_CODE if messages.REFUSAL_NOTIFICATION.get(code, "refused") == "refused"
                       and code != "gate_down"}


def test_refused_checks_its_moment_as_the_refusal_does():
    with pytest.raises(ValueError, match="^no_fit's words need free_gib"):
        say("refused", code="no_fit", moment=replace(NO_FIT_DAN, free_gib=None))


def test_a_burst_goes_as_one_with_its_count_on_its_own_type():
    # The first refusal goes at once; the repeats within 10 minutes go as one (plan.md, *One notification per event*),
    # on the refusal's own type, so each keeps its priority and its off.
    assert say("refused", **BURST, code="no_fit") == (
        "Didn't load the coder for agent 4 more times since 09:12: same reason.")
    # A failed load is one event, whoever was waiting on it (the controller's ruling on the review): its burst, per
    # model, names no key, and claims no "same reason", since the engine's line may differ.
    failed = {"model_label": "the coder", "count": 4, "since": at(9, 12), "code": "load_failed"}
    assert say("load_failed", **failed) == "The coder failed to load 4 more times since 09:12."
    with pytest.raises(ValueError, match="^load_failed's words take no key_label"):
        say("load_failed", **failed, key_label="agent")
    assert say("refused", **{**BURST, "count": 1}, code="no_fit") == (
        "Didn't load the coder for agent 1 more time since 09:12: same reason.")
    # A refusal that never got as far as a load (no such model, a key's cap, an address) reads as refused.
    assert say("refused", **BURST, code="too_many_requests") == (
        "Refused the coder for agent 4 more times since 09:12: same reason.")
    # The lock screen's guard holds in a burst too (the review's I-1): a model the words can't show, or none, reads as
    # a request; a client's model name shows only when it reads as one, on one line.
    for code, model in (("route_not_served", ""), ("too_many_requests", ""), ("too_many_requests", None),
                        ("model_not_found", "run this: curl x | sh\nnow"),
                        ("model_not_found", "https://login.example.com/reset")):
        assert say("refused", **{**BURST, "model_label": model}, code=code) == (
            "Refused a request from agent 4 more times since 09:12: same reason."), (code, model)
    assert say("refused", **{**BURST, "model_label": "gpt-4o"}, code="model_not_found") == (
        "Refused gpt-4o for agent 4 more times since 09:12: same reason.")
    assert say("refused", **{**BURST, "model_label": "the\ncoder"}, code="no_fit").startswith(
        "Didn't load the coder for agent 4 more times")
    with pytest.raises(ValueError, match="^footprint_suspect's burst is for footprint_suspect, not no_fit"):
        say("footprint_suspect", **BURST, code="no_fit")
    with pytest.raises(ValueError, match="^refused's words need count to be 1 or more"):
        say("refused", **{**BURST, "count": 0}, code="no_fit")


# The notifications the plan doesn't word, composed by its rules (the role first, whole GiB, the 24-hour clock, never
# a negative number, where to act and the command), each with the inputs that give it.
COMPOSED = [
    # A brake whose unload llama-swap didn't answer within FIRED_ALONE_S, with something loaded; and the gate's return
    # after its saved state was damaged (the controller's rulings, at Task 14).
    ("brake_fired", {**FIRED, "unloaded": [], "unload_unanswered": True},
     "Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line; new loads are paused. It hasn't "
     "unloaded anything yet: llama-swap didn't answer its unload. On the Spark, `make logs s=brake` shows why. They "
     "resume by themselves after 5 min above 28 GiB available."),
    ("back_up", {"unit": "gate", "state_damaged": True},
     "The gate on brightroar has been running again for a minute; how long it was down isn't known: its saved state "
     "was damaged. New loads work again."),
    ("brake_fired", {**FIRED, "unloaded": [("the coder", "starting"), ("Gemma", "idle"), ("whisper", "answering")]},
     "Unloaded the coder (loading), Gemma (idle) and whisper (answering); new loads are paused."),
    ("brake_fired", {"at": at(3, 13), "unloaded": [("Gemma", "idle"), ("the embeddings", "idle"), ("whisper", "idle")],
                     "follow_up": True},
     "Brake, 03:13: also unloaded Gemma, the embeddings and whisper, all idle."),
    ("brake_fired", {"at": at(3, 13), "unloaded": [("Gemma", "idle")], "follow_up": True},
     "Brake, 03:13: also unloaded Gemma, which was idle."),
    ("brake_fired", {**FIRED, "unloaded": [("Gemma", None)]}, "Unloaded Gemma; new loads are paused."),
    # A brake with nothing of the stack's loaded (the memory is taken by something outside it) still pauses new loads,
    # and says so (the controller's ruling at Task 7's review, worded at Task 13).
    ("brake_fired", {**FIRED, "unloaded": []},
     "Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line. Nothing of the stack's was loaded, so "
     "there was nothing to unload; new loads are paused. They resume by themselves after 5 min above 28 GiB "
     "available."),
    ("brake_fired", {**FIRED, "unloaded": [], "by_brake": True},
     "Nothing of the stack's was loaded, so there was nothing to unload; new loads are paused. They resume once the "
     "gate is back and memory has stayed above 28 GiB available for 5 min."),
    # A drill's raised line is worded as itself, and the release time is the gate's.
    ("brake_fired", {**FIRED, "available_gib": 52.3, "line_gib": 56, "release_after_s": 600},
     "52.3 GiB available, under the 56 GiB line. Unloaded the coder, which was loading; new loads are paused. They "
     "resume by themselves after 10 min above 28 GiB available."),
    ("brake_down", {**DOWN, "result_words": "it stopped answering"},
     "The memory brake on brightroar stopped at 09:14 (it stopped answering; it is restarting within 2 s)."),
    ("llama_swap_down", {**DOWN, "result_words": "it ran out of memory"},
     "The model service on brightroar stopped at 09:14 (it ran out of memory; it is restarting). No model answers"),
    ("back_up", {"unit": "front", "down_s": 90},
     "The front on brightroar has been running again for a minute, after 1 min 30 s down. Requests go through again."),
    ("back_up", {"unit": "llama-swap", "down_s": 5},
     "The model service on brightroar has been running again for a minute, after 5 s down. Models answer again."),
    ("back_up", {"unit": "brake", "down_s": 2},
     "The memory brake on brightroar has been running again for a minute, after 2 s down. Memory is watched again."),
    ("back_up", {"unit": "gate", "down_s": -3}, "after 0 s down."),
    ("load_failed", {**FAILED_FIELDS, "engine_said": None, "deadline_s": 180},
     "The coder failed to load: it didn't finish within 3 min. On the Spark, `spark logs coder` shows the engine's "
     "last lines."),
    ("load_failed", {**FAILED_FIELDS, "engine_said": " \n"},
     "The coder failed to load: the engine stopped. On the Spark, `spark logs coder` shows the engine's last lines."),
    ("brake_released", {"at": at(3, 40), "available_gib": 64.9, "reloaded": []},
     "Brake released at 03:40, 64 GiB available. Nothing to reload."),
    ("room_hold_ended", {**HOLD_ENDED, "why": "time", "unused_gib": 9, "total_gib": 41, "reloading": ["Gemma"]},
     "make-room's hold for you ended (its time ran out), 9 of its 41 GiB unused. Reloading Gemma; the coder loads on "
     "its next request."),
    ("room_hold_ended", {**HOLD_ENDED, "why": "reboot", "unused_gib": 0.2, "reloading": [], "next_label": None},
     "make-room's hold for you ended (the Spark rebooted), all of it used. Nothing to reload."),
    ("room_hold_ended",
     {**HOLD_ENDED, "why": "used", "unused_gib": 0, "reloading": ["Gemma", "the embeddings"], "next_label": None},
     "make-room's hold for you ended: your own loads used it up. Reloading Gemma, then the embeddings."),
    ("resident_waiting", {"label": "the embeddings", "needed_gib": 7.5, "free_gib": 0.6, "after": "boot"},
     "The embeddings didn't fit at boot: it needs 8 GiB, and nothing is free for a load. It loads by itself once "
     "there's room."),
    ("resident_waiting", {"label": "Gemma", "needed_gib": 32, "free_gib": 9, "after": "brake"},
     "Gemma didn't fit after the brake's release:"),
    ("resident_waiting", {"label": "Gemma", "needed_gib": 32, "free_gib": 9, "after": "apply"},
     "Gemma didn't fit after make apply's restart:"),
    ("resident_waiting", {"label": "Gemma", "needed_gib": 32, "free_gib": 9, "after": "restart"},
     "Gemma didn't fit after the model service restarted:"),
    ("resident_waiting", {"label": "Gemma", "needed_gib": 32, "free_gib": 9, "after": "crash"},
     "Gemma didn't fit after it stopped outside the gate:"),
    ("apply_restarted", {"at": at(14, 2), "reloaded": [], "on_demand": ["the coder", "Qwen"]},
     "make apply restarted the model service at 14:02. The coder and Qwen load on their next request."),
    ("apply_restarted", {"at": at(14, 2)}, "make apply restarted the model service at 14:02."),
    ("load_started", {"label": "Gemma"}, "Loading Gemma…"),
    ("unloaded", {"label": "the coder", "why": "make-room"}, "Unloaded the coder for make-room."),
    ("unloaded", {"label": "the coder", "why": "unload"}, "Unloaded the coder, as `spark unload` asked."),
    ("waiting", {**WAITING, "free_gib": -3}, "It needs 41 GiB, and nothing is free for a load."),
    ("waiting", {**WAITING, "why": "brake", "needed_gib": None, "free_gib": None, "release_waits_for_dan": False,
                 "release_after_s": 300},
     "Waiting while new loads are paused: the coder for pi on the Mac, up to 30 s. They resume by themselves after "
     "5 min above 28 GiB available, if what would reload fits; on the Spark, `make brake-release` resumes them now."),
    ("waiting", {**WAITING, "why": "brake", "needed_gib": None, "free_gib": None, "release_waits_for_dan": True},
     "Waiting while new loads are paused: the coder for pi on the Mac, up to 30 s. They stay paused until you release "
     "them: on the Spark, `make brake-release`."),
    ("waiting", {**WAITING, "why": "slot", "needed_gib": None, "free_gib": None, "loading_label": "Gemma"},
     "Waiting its turn to load: the coder for pi on the Mac, up to 30 s. Gemma is loading, and one model loads at a "
     "time."),
    ("waiting", {**WAITING, "key_label": "agent", "why": "dan", "wait_s": 600, "needed_gib": None, "free_gib": None,
                 "command": "coder"},
     "Waiting for you: the coder for agent, up to 10 min. It was loading when the brake fired; on the Spark, "
     "`spark load coder` allows it again."),
    ("pin_ended", {"label": "Gemma", "at": at(18, 0)},
     "The pin on Gemma ended at 18:00; it stays loaded, since it's always loaded."),
    # Times can come as Unix seconds, as the gate keeps them: 21:14 UTC is 14:14 in the tests' zone.
    ("pin_ended", {"label": "the coder", "at": datetime(2026, 10, 7, 21, 14, tzinfo=timezone.utc).timestamp(),
                   "idle_min": 60}, "ended at 14:14;"),
]


@pytest.mark.parametrize("kind, given, text",
                         [pytest.param(k, g, t, id=f"{k}-{i}") for i, (k, g, t) in enumerate(COMPOSED)])
def test_each_composed_notification_reads_as_built(kind, given, text):
    assert text in say(kind, **given)


# What each notification type needs: a field left out, or None, an empty text or an empty list, is refused by name.
NOTIFICATION_NEEDS = {
    "brake_fired": (FIRED, ("at", "unloaded", "available_gib", "line_gib", "release_waits_for_dan",
                            "release_after_s")),
    "brake_needs_release": (NOTIFICATION_ROWS[2][1], ("fired_at",)),
    "gate_down": (DOWN, ("at",)),
    "back_up": ({"unit": "gate", "down_s": 12}, ("unit", "down_s")),
    "refused": ({"code": "no_fit", "moment": NO_FIT_DAN}, ("code", "moment")),
    "footprint_suspect": (SUSPECT_FIELDS, ("model_label", "key_label", "fired_at", "command")),
    "load_failed": (FAILED_FIELDS, ("model_label", "command")),
    "brake_released": (NOTIFICATION_ROWS[11][1], ("at", "available_gib")),
    "room_hold_ended": (HOLD_ENDED, ("why", "unused_gib", "total_gib")),
    "resident_waiting": (NOTIFICATION_ROWS[14][1], ("label", "needed_gib", "free_gib", "after")),
    "apply_restarted": (NOTIFICATION_ROWS[15][1], ("at",)),
    "load_started": (NOTIFICATION_ROWS[16][1], ("label",)),
    "loaded": (NOTIFICATION_ROWS[17][1], ("label", "seconds")),
    "unloaded": (NOTIFICATION_ROWS[18][1], ("label", "why", "idle_min")),
    "waiting": (WAITING, ("label", "key_label", "why", "wait_s", "needed_gib", "free_gib")),
    "pin_ended": (NOTIFICATION_ROWS[20][1], ("label", "at")),
    "memory_warning": (NOTIFICATION_ROWS[21][1], ("available_gib", "warn_gib", "brake_gib")),
}


@pytest.mark.parametrize("kind, name", [(k, n) for k, (_, names) in NOTIFICATION_NEEDS.items() for n in names])
def test_each_notification_refuses_a_missing_field_by_name(kind, name):
    given, _ = NOTIFICATION_NEEDS[kind]
    with pytest.raises(ValueError, match=rf"^{kind}'s words need {name}\b"):
        messages.notification(kind, REGISTRY, **{**given, name: None})
    with pytest.raises(ValueError, match=rf"^{kind}'s words need {name}\b"):
        messages.notification(kind, REGISTRY, **{k: v for k, v in given.items() if k != name})


def test_a_field_needed_only_in_one_form_is_needed_there():
    with pytest.raises(ValueError, match="^waiting's words need command"):
        say("waiting", **{**WAITING, "why": "dan"})
    with pytest.raises(ValueError, match="^waiting's words need loading_label"):
        say("waiting", **{**WAITING, "why": "slot"})
    with pytest.raises(ValueError, match="^waiting's words need release_waits_for_dan"):
        say("waiting", **{**WAITING, "why": "brake"})
    with pytest.raises(ValueError, match="^waiting's words need release_after_s"):
        say("waiting", **{**WAITING, "why": "brake", "release_waits_for_dan": False})
    assert say("waiting", **{**WAITING, "why": "brake", "release_waits_for_dan": True})  # no time when it waits for Dan
    assert say("brake_fired", **{**WITHIN_THE_HOUR, "release_after_s": None})  # the same for the brake's alert
    assert say("brake_fired", **NOTIFICATION_ROWS[1][1]).startswith("Brake, 03:13:")  # a follow-up has no reading
    with pytest.raises(ValueError, match="^brake_fired's words need unloaded"):  # a follow-up is always an unload
        say("brake_fired", **{**NOTIFICATION_ROWS[1][1], "unloaded": []})


def test_a_brake_after_a_damaged_start_never_names_an_automatic_release():
    # After the gate's saved state was damaged, the last automatic release is assumed, not real: the alert says why
    # the pause waits for Dan, never "the automatic release at …" (the controller's ruling at Task 13's re-review).
    text = say("brake_fired", **{**FIRED, "release_waits_for_dan": True, "release_assumed": True})
    assert text.endswith("new loads are paused. New loads stay paused until you release them: the gate's saved state "
                         "was damaged, so it can't tell when the last automatic release was. On the Spark, "
                         "`make brake-release` resumes them.")
    assert "automatic release at" not in text
    assert say("brake_fired", **WITHIN_THE_HOUR).endswith(DANS_RELEASE)  # a real release keeps its words


UNANSWERED_HEAD = ("Brake on brightroar at 03:12: 19.6 GiB available, under the 20 GiB line; new loads are paused. It "
                   "hasn't unloaded anything yet: llama-swap didn't answer its unload. On the Spark, `make logs s=brake` "
                   "shows why.")


def test_a_brake_whose_unload_went_unanswered_says_so_then_how_the_pause_ends():
    # The controller's ruling, at Task 14: no unload within FIRED_ALONE_S while something is loaded. Its words are
    # true whether the unload was slow, failed or refused, then the usual pause sentence.
    unanswered = {**FIRED, "unloaded": [], "unload_unanswered": True}
    assert say("brake_fired", **unanswered) == (
        f"{UNANSWERED_HEAD} They resume by themselves after 5 min above 28 GiB available.")
    assert say("brake_fired", **{**unanswered, "by_brake": True}) == (
        f"{UNANSWERED_HEAD} They resume once the gate is back and memory has stayed above 28 GiB available for 5 min.")
    assert say("brake_fired", **{**unanswered, "release_waits_for_dan": True, "released_at": at(3, 0)}) == (
        f"{UNANSWERED_HEAD} It fired within an hour of the automatic release at 03:00, so they stay paused until you "
        "release them: on the Spark, `make brake-release`.")
    assert say("brake_fired", **{**unanswered, "release_waits_for_dan": True, "release_assumed": True}).startswith(
        f"{UNANSWERED_HEAD} New loads stay paused until you release them: the gate's saved state was damaged")
    # It names no unload, so a brake that did unload, or a follow-up, can't take it.
    for given in ({**FIRED, "unload_unanswered": True}, {**NOTIFICATION_ROWS[1][1], "unload_unanswered": True}):
        with pytest.raises(ValueError, match="^brake_fired's unload_unanswered is for a first alert that unloaded "
                                             "nothing"):
            say("brake_fired", **given)
    # The command it names is the Makefile's: `make logs s=brake` reads local-ai-brake.service's journal.
    makefile = (Path(__file__).resolve().parents[2] / "Makefile").read_text()
    assert re.search(r"^logs: ## On the Spark: make logs s=llama-swap\|brake\|", makefile, re.MULTILINE)
    assert "journalctl -u local-ai-$(s).service" in makefile


def test_the_gates_return_after_a_damaged_state_says_its_downtime_isnt_known():
    assert say("back_up", unit="gate", state_damaged=True) == (
        "The gate on brightroar has been running again for a minute; how long it was down isn't known: its saved state "
        "was damaged. New loads work again.")
    # Only the gate keeps a saved state, and a downtime given is a downtime known.
    for given in ({"unit": "front", "state_damaged": True}, {"unit": "gate", "down_s": 12, "state_damaged": True}):
        with pytest.raises(ValueError, match="^back_up's state_damaged is the gate's alone, with no down_s"):
            say("back_up", **given)


def test_the_unloaded_whys_and_drain_for_are_gateprotos():
    # messages can't import gateproto (it stays light), so this ties the two lists together.
    from typing import get_args, get_type_hints

    from spark.gateproto import DRAIN_WHY
    drain_for = get_type_hints(Moment)["drain_for"]
    assert set(get_args(get_args(drain_for)[0])) == set(DRAIN_WHY)
    for why in DRAIN_WHY:
        assert say("unloaded", label="the coder", why=why, idle_min=60).startswith("Unloaded the coder")
    with pytest.raises(ValueError, match="^unloaded's why must be one of "):
        say("unloaded", label="the coder", why="brake")


def test_an_unload_whose_why_is_unknown_is_worded_neutrally():
    # A drain saved without its why (an older state file) reads as unknown: no words name `spark unload` or make-room
    # for it, and Task 15's abort of a start past its deadline says what it was (the controller's ruling at Task 13's
    # review).
    assert say("unloaded", label="the coder", why="unknown") == "Unloaded the coder."
    late = say("unloaded", label="the coder", why="late_start")
    assert late == "Unloaded the coder: its start ran past its deadline."
    for why in ("unknown", "late_start"):
        for words in (DAN, AGENT):
            moment = Moment(inflight=1, drain_for=why, **CODER, **words)
            for text in (refusal("draining", moment).message, say("refused", code="draining", moment=moment)):
                assert "is being unloaded" in text and "make-room" not in text and "spark unload" not in text
    assert messages.unloading("the coder", 0) == "Unloading the coder…"
    assert say("unloaded", label="the coder", why="unload")  # only an idle unload names its minutes


def test_a_field_a_type_doesnt_take_and_a_value_it_doesnt_know_are_refused():
    with pytest.raises(ValueError, match="^loaded's words take no idle_min"):
        say("loaded", label="the coder", seconds=24, idle_min=60)
    with pytest.raises(ValueError, match="^'brake_on' isn't a notification type"):
        say("brake_on", at=AT_0312)
    for kind, given, name in (("back_up", {"unit": "web", "down_s": 1}, "unit"),
                              ("unloaded", {"label": "the coder", "why": "brake"}, "why"),
                              ("brake_fired", {**FIRED, "unloaded": [("Gemma", "busy")]}, "state"),
                              ("resident_waiting", {**NOTIFICATION_ROWS[14][1], "after": "noon"}, "after"),
                              ("room_hold_ended", {**HOLD_ENDED, "why": "done"}, "why")):
        with pytest.raises(ValueError, match=rf"^{kind}'s {name} must be one of "):
            say(kind, **given)


def test_the_host_is_the_short_name_at_the_time(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "otherbox")
    assert say("gate_down", **DOWN).startswith("The gate on otherbox stopped at 09:14")


# make-room's list: the residents and the coder loaded, nothing else running, 9 GiB free for a load (plan.md). The
# plan's names: the coder is Qwen3.8-27B (Task 42), so the list's registry renames today's coder.
CODER_MODEL = next(m for m in REGISTRY.models.values() if m.label == "the coder")
PLAN_REGISTRY = replace(REGISTRY, models={**{n: m for n, m in REGISTRY.models.items() if m is not CODER_MODEL},
                                          "qwen3.8-27b": replace(CODER_MODEL, name="qwen3.8-27b")})
CANDIDATES = [
    Candidate("qwen3.8-27b", "the coder", 41, False, False, None, 0, None, 12),
    Candidate("gemma-4-26b-a4b", "Gemma", 32, True, False, None, 0, None, 1.5),
    Candidate("qwen3-embedding-0.6b", "the embeddings", 8, True, False, None, 0, None, 30),
    Candidate("whisper-large-v3-turbo", "whisper", 3, True, False, None, 0, None, 5),
]
ROOM_LIST = """\
To make 40 GiB free for a load (9 GiB now):
  1  the coder (qwen3.8-27b)      41 GiB  loads when asked · idle 12 min
  2  Gemma (gemma-4-26b-a4b)      32 GiB  always loaded · the web UI and photos use it
  3  the embeddings               8 GiB   always loaded
  4  whisper                      3 GiB   always loaded
Unloading 1 leaves 50 GiB free for a load. Unload 1? [y/N]"""


def test_the_make_room_list_follows_its_rule():
    assert messages.room_list(40, 9, CANDIDATES, ["qwen3.8-27b"], 50, registry=PLAN_REGISTRY) == ROOM_LIST
    # Bracketed names on the chat models only: the coder and Gemma.
    rows = ROOM_LIST.splitlines()[1:5]
    assert [("(" in row) for row in rows] == [True, True, False, False]


def test_the_make_room_list_marks_pins_sessions_and_requests_in_flight():
    # plan.md's rule 4: pinned models and those a session holds included, each marked, with each one's requests in
    # flight and how long they have run (Task 17's candidates).
    marked = [replace(CANDIDATES[0], pinned=True, session="agent's pi"),
              replace(CANDIDATES[1], inflight=1, oldest_s=180), *CANDIDATES[2:]]
    rows = messages.room_list(40, 9, marked, ["qwen3.8-27b"], 50, registry=PLAN_REGISTRY).splitlines()
    assert rows[1].endswith("41 GiB  loads when asked · pinned · session: agent's pi · idle 12 min")
    assert rows[2].endswith("32 GiB  always loaded · the web UI and photos use it · 1 request in flight for 3 min")
    two = messages.room_list(40, 9, [replace(CANDIDATES[0], inflight=2, oldest_s=75)], ["qwen3.8-27b"], 50,
                             registry=PLAN_REGISTRY).splitlines()[1]
    assert two.endswith("loads when asked · 2 requests in flight, the oldest for 1 min 15 s")  # in flight: not idle


def test_the_make_room_list_for_more_or_less_than_it_needs():
    both = messages.room_list(70, 9, CANDIDATES, ["qwen3.8-27b", "gemma-4-26b-a4b"], 82, registry=PLAN_REGISTRY)
    assert both.splitlines()[-1] == "Unloading 1 and 2 leaves 82 GiB free for a load. Unload 1 and 2? [y/N]"
    # Not enough even with everything: the list ends at its rows, and room_too_much asks the question.
    short = messages.room_list(100, 9, CANDIDATES, [c.model for c in CANDIDATES], 93, registry=PLAN_REGISTRY)
    assert short.splitlines()[-1].startswith("  4  whisper")
    # Enough already: nothing to unload, and the hold is offered.
    assert messages.room_list(5, 9, CANDIDATES, [], 9, registry=PLAN_REGISTRY).splitlines()[-1] == (
        "Nothing needs to unload: 9 GiB is free for a load now. Hold 5 GiB of it for you? [y/N]")
    # --all: everything, from nothing free for a load.
    every = messages.room_list(None, 0.4, CANDIDATES, [c.model for c in CANDIDATES], 84.4, registry=PLAN_REGISTRY)
    assert every.splitlines()[0] == "To unload everything (nothing free for a load now):"
    assert every.splitlines()[-1] == ("Unloading 1, 2, 3 and 4 leaves 84 GiB free for a load. Unload 1, 2, 3 and 4? "
                                      "[y/N]")
    with pytest.raises(ValueError, match="'gpt-oss-120b' isn't on make-room's list"):
        messages.room_list(40, 9, CANDIDATES, ["gpt-oss-120b"], 50, registry=PLAN_REGISTRY)


NOW = at(10, 0)  # when the commands below run
CONFIRMATIONS = [
    ("loaded", "loaded", ("the coder", 24, 60, "coder"), {},
     "Loaded the coder in 24 s. It unloads after 60 min idle; `spark pin coder` keeps it."),
    ("unloading", "unloading", ("the coder", 1), {}, "Unloading the coder once its 1 request in flight finishes…"),
    ("unloaded", "unloaded", ("the coder",), {}, "Unloaded the coder."),
    ("pinned", "pinned", ("the coder", at(18, 0), 24, "coder"), {"now": NOW},
     "The coder stays loaded until 18:00 (loaded it first, 24 s). `spark unpin coder` ends the pin."),
    ("unpinned", "unpinned", ("the coder", 60), {}, "The pin on the coder ended; it unloads after 60 min idle."),
    ("make-room 40G", "room_held", (["the coder"], 50, 40, None), {},
     "Unloaded the coder. 50 GiB is free for a load, and 40 GiB of it is held for you until `spark make-room --done` "
     "or a reboot; your own requests can load into it, agent's and automatic reloads can't."),
    ("make-room 70G, too much", "room_too_much", (70, 61), {},
     "Unloading everything leaves 61 GiB free for a load, not 70. Free 61 and hold it? [y/N]"),
    ("make-room --all", "room_all", (), {},
     "Unloaded everything. The whole box is held for you until `spark make-room --done` or a reboot; your own requests "
     "can load into it."),
    ("make-room --done", "room_done", (40, 40, [], "the coder"), {},
     "Hold ended, all 40 GiB of it unused. Nothing to reload: the coder loads on its next request."),
    ("make-room --done, reloading", "room_done", (40, 40, ["Gemma"], None), {},
     "Hold ended, all 40 GiB of it unused. Reloading Gemma."),
    ("make brake-release", "brake_released_by_dan", (["Gemma", "the embeddings"],), {},
     "New loads resume. Reloading Gemma, then the embeddings…"),
    ("make apply, waiting", "apply_waiting", ("the coder", 20, 60), {},
     "Waiting for a quiet moment: the coder answered 20 s ago, and it needs 60 s with nothing in flight. Ctrl-C leaves "
     "everything as it was; `make apply-now` restarts now."),
    ("make apply, no quiet minute", "apply_no_quiet", (2,), {},
     "No quiet minute in 15 minutes. Drain now, holding new requests while the 2 in flight finish? [y/N]"),
    ("make apply-now", "apply_now_confirm", (["pi on the Mac", "agent"],), {},
     "This restarts the model service now and cuts off the 2 requests in flight (pi on the Mac, agent). Continue? "
     "[y/N]"),
]


@pytest.mark.parametrize("function, args, kwargs, text",
                         [pytest.param(f, a, k, t, id=i) for i, f, a, k, t in CONFIRMATIONS])
def test_each_confirmation_reads_word_for_word(function, args, kwargs, text):
    assert getattr(messages, function)(*args, **kwargs) == text


def test_make_brake_release_says_so_when_the_gate_isnt_answering():
    assert messages.BRAKE_RELEASED_WITHOUT_GATE == (
        "The gate isn't answering, so the hold file was removed directly; nothing reloads until the gate is back.")


COMPOSED_CONFIRMATIONS = [
    ("loaded", ("Gemma", 20, None, "gemma"), {}, "Loaded Gemma in 20 s. It stays loaded, since it's always loaded."),
    ("loaded", ("the coder", None, 60, "coder"), {},
     "The coder is already loaded. It unloads after 60 min idle; `spark pin coder` keeps it."),
    ("unloading", ("the coder", 0), {}, "Unloading the coder…"),
    ("unloading", ("the coder", 2), {}, "Unloading the coder once its 2 requests in flight finish…"),
    ("pinned", ("the coder", None, None, "coder"), {"now": NOW},
     "The coder stays loaded, with no end set. `spark unpin coder` ends the pin."),
    # A pin's end more than a day away names its day.
    ("pinned", ("the coder", at(18, 0) + timedelta(days=2), None, "coder"), {"now": NOW},
     "The coder stays loaded until Fri 9 Oct, 18:00. `spark unpin coder` ends the pin."),
    ("pinned", ("the coder", at(8, 0) + timedelta(days=1), 24, "coder"), {"now": at(22, 0)},
     "The coder stays loaded until 08:00 (loaded it first, 24 s)."),
    ("unpinned", ("Gemma", None), {}, "The pin on Gemma ended; it stays loaded, since it's always loaded."),
    ("room_held", (["the coder", "Gemma"], 82, 70, at(18, 0)), {"now": NOW},
     "Unloaded the coder and Gemma. 82 GiB is free for a load, and 70 GiB of it is held for you until 18:00, "
     "`spark make-room --done` or a reboot;"),
    ("room_held", ([], 50, 40, None), {}, "Nothing needed to unload. 50 GiB is free for a load,"),
    ("room_too_much", (70, 0.4), {}, "Unloading everything leaves nothing free for a load, not 70."),
    ("room_done", (9, 41, ["Gemma"], "the coder"), {},
     "Hold ended, 9 of its 41 GiB unused. Reloading Gemma; the coder loads on its next request."),
    ("room_done", (0, 41, [], None), {}, "Hold ended, all of it used. Nothing to reload."),
    ("brake_released_by_dan", ([],), {}, "New loads resume. Nothing to reload."),
    ("apply_waiting", ("the coder", 0, 60), {}, "Waiting for a quiet moment: the coder is answering now, and"),
    ("apply_waiting", (None, 12, 60), {}, "Waiting for a quiet moment: the last request finished 12 s ago, and"),
    # A drill shortens apply's deadline (Task 32's --deadline-s): the words name the one it was given.
    ("apply_no_quiet", (1,), {"deadline_s": 120},
     "No quiet minute in 2 minutes. Drain now, holding new requests while the 1 in flight finishes? [y/N]"),
    ("apply_no_quiet", (0,), {}, "No quiet minute in 15 minutes. Drain now, holding new requests? [y/N]"),
    ("apply_now_confirm", (["agent"],), {},
     "This restarts the model service now and cuts off the 1 request in flight (agent). Continue? [y/N]"),
    ("apply_now_confirm", ([],), {}, "This restarts the model service now; nothing is in flight. Continue? [y/N]"),
]


@pytest.mark.parametrize("function, args, kwargs, text",
                         [pytest.param(f, a, k, t, id=f"{f}-{i}") for i, (f, a, k, t) in
                          enumerate(COMPOSED_CONFIRMATIONS)])
def test_each_composed_confirmation_reads_as_built(function, args, kwargs, text):
    assert text in getattr(messages, function)(*args, **kwargs)


def _every_text() -> list[tuple[str, str]]:
    """Every refusal, notification and confirmation these tests build, by what built it."""
    texts = [(f"refusal {code}", refusal(code, moment).message) for _, code, moment, _ in EVERY_ROW]
    for kind, given, _ in NOTIFICATION_ROWS + PLAN_VARIANTS + COMPOSED:
        texts.append((f"notification {kind}", say(kind, **given)))
    for row_id, moment, _ in REFUSED_ROWS:
        texts.append((f"notification refused, {row_id}", say("refused", code=_refused_code(row_id), moment=moment)))
    for _, function, args, kwargs, _ in CONFIRMATIONS:
        texts.append((f"confirmation {function}", getattr(messages, function)(*args, **kwargs)))
    for function, args, kwargs, _ in COMPOSED_CONFIRMATIONS:
        texts.append((f"confirmation {function}", getattr(messages, function)(*args, **kwargs)))
    texts.append(("confirmation room_list", messages.room_list(40, 9, CANDIDATES, ["qwen3.8-27b"], 50,
                                                               registry=PLAN_REGISTRY)))
    texts.append(("confirmation brake release without the gate", messages.BRAKE_RELEASED_WITHOUT_GATE))
    return texts


# "free" as a word: as an adjective only in "free for a load" (plan.md, *Two numbers, two words*); as a verb,
# make-room's action, only in the phrases the plan's and Task 6's own words use (the controller's ruling, 2026-10-07).
FREE_WORD = re.compile(r"\bfree[sd]?\b", re.IGNORECASE)  # "frees" and "freed" too (the review's minor 3)
FREE_ALLOWED = re.compile(r"free for a load|Free space with `spark make-room|Only Dan can free memory for it"
                          r"|The Spark can never free that much|Free \d+ and hold it\?")


def test_no_message_says_free_alone():
    checked = 0
    for source, text in _every_text():
        assert not re.search(FREE_WORD, FREE_ALLOWED.sub("", text)), (source, text)
        checked += 1
    assert checked == (len(EVERY_ROW) + len(NOTIFICATION_ROWS) + len(PLAN_VARIANTS) + len(COMPOSED) + len(REFUSED_ROWS)
                       + len(CONFIRMATIONS) + len(COMPOSED_CONFIRMATIONS) + 2)
    # The test can fail: "free" alone, and a verb phrase outside the allowance.
    for stray in ("18 GiB free now", "Free 61 GiB", "once memory frees", "18 GiB freed"):
        assert re.search(FREE_WORD, FREE_ALLOWED.sub("", stray)), stray


# Every model's registry name, today's and the plan's: refusals and notifications name models by their labels.
NAMES = {name for _, name in MODELS} | set(REGISTRY.models)


def test_models_are_named_by_label():
    checked = 0
    for source, text in _every_text():
        if source.startswith("confirmation") or "model_not_found" in source:
            continue  # make-room's list brackets the chat models' names; model_not_found lists them, and the asked one
        assert not any(name in text for name in NAMES), (source, text)
        checked += 1
    # Every refusal row but model_not_found's two, and every refused row but model_not_found's.
    refused = sum(1 for row_id, _, _ in REFUSED_ROWS if not row_id.startswith("model_not_found"))
    assert checked == len(EVERY_ROW) - 2 + len(NOTIFICATION_ROWS) + len(PLAN_VARIANTS) + len(COMPOSED) + refused
    assert any(name in say("refused", code="model_not_found", moment=REFUSED_ROWS[18][1]) for name in NAMES)


def test_agents_no_fit_names_the_holds_end_only_when_that_makes_room():
    # The hold's end is agent's step only when ending it would let the load fit: free for a load and the hold, 41 or
    # more here (the controller's ruling on the review's minor 10). Short of that, the no-hold words.
    holds_end = "The hold ends when Dan runs `spark make-room --done` on the Spark."
    no_hold = "Only Dan can free memory for it, on the Spark; try again after that."
    assert refusal("no_fit", replace(NO_FIT_AGENT, free_gib=Decimal(12), held_gib=29)).message.endswith(holds_end)
    assert refusal("no_fit", replace(NO_FIT_AGENT, free_gib=Decimal(12), held_gib=28.9)).message.endswith(no_hold)
    assert refusal("no_fit", NO_FIT_AGENT_CLAMPED).message.endswith(no_hold)
    # A group with Dan's words whose hold is counted: the same test, then Dan's own no-hold step.
    assert refusal("no_fit", replace(NO_FIT_AGENT_CLAMPED, words="dan")).message.endswith(
        "Free space with `spark make-room 41G` on the Spark, then try again.")


def test_model_not_found_names_pis_step_only_for_a_pi_key():
    # Dan's keys: pi's step for pi's key ("pi on the Mac", "pi in Orca"); for any other key, the web UI's, the list
    # alone, since `make clients` updates pi's list only (the controller's ruling on the review's minor 10).
    web_ui = Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, key_label="the web UI", words="dan")
    assert refusal("model_not_found", web_ui).message == MODEL_NOT_FOUND_TEXT.replace(
        " On the Mac, `make clients` updates pi's list.", "")
    orca = replace(web_ui, key_label="pi in Orca")
    assert refusal("model_not_found", orca).message == MODEL_NOT_FOUND_TEXT
    # The first word, not a prefix: a key labelled "pipelines" isn't pi's.
    pipelines = replace(web_ui, key_label="pipelines")
    assert refusal("model_not_found", pipelines).message == refusal("model_not_found", web_ui).message


def test_make_room_all_with_nothing_loaded_says_so():
    assert messages.room_list(None, 93, [], [], 93, registry=PLAN_REGISTRY) == (
        "Nothing is loaded, so there is nothing to unload.")
    assert messages.room_list(100, 93, [], [], 93, registry=PLAN_REGISTRY) == (
        "Nothing is loaded, so there is nothing to unload.")


def test_apply_now_names_each_asker_once_with_its_count():
    assert messages.apply_now_confirm(["pi on the Mac", "agent", "pi on the Mac"]) == (
        "This restarts the model service now and cuts off the 3 requests in flight (pi on the Mac ×2, agent). "
        "Continue? [y/N]")
