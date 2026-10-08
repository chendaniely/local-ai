"""The refusals in Dan's words (website/design/plan.md, *What you see in Phase 2a*), each with its status and headers:
what pi and the web UI show when the gate or the front says no."""

import json
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from spark.messages import (CODES_409, CODES_503, FRONT_CODES, PI_RETRY_PATTERNS, RETRY_AFTER_S, UNKNOWN_KEY, Holder,
                            Moment, Refusal, refusal)

# The tests' own time zone, seven hours behind UTC: POSIX's TZ writes it "PDT+7", its sign the other way round.
TZ = "PDT+7"
ZONE = timezone(timedelta(hours=-7))


@pytest.fixture(autouse=True)
def local_zone(monkeypatch):
    """Every test here reads the clock in ZONE; the process gets its own zone back afterwards."""
    monkeypatch.setenv("TZ", TZ)
    time.tzset()
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
# Dan's job running in his hold: free for a load, 36 − 24 − 70, is below 0.
NO_FIT_AGENT_CLAMPED = replace(NO_FIT_AGENT, free_gib=Decimal(-58), available_gib=36,
                               holders=[Holder("python3 (chendaniely)", 70, True), Holder("the embeddings", 8, False)])
LOADING = Moment(loading_label="Gemma", **CODER, **DAN)
HELD = Moment(brake_at=AT_0312, brake_available_gib=19.6, release_waits_for_dan=False, **CODER, **DAN)
LOAD_FAILED = Moment(engine_said="failed to load model", **CODER, **DAN)
SUSPECT = Moment(brake_at=AT_0312, **CODER, **AGENT)
MODELS = [("the coder", "qwen3.8-27b"), ("Gemma", "gemma-4-26b-a4b"), ("the embeddings", "qwen3-embedding-0.6b"),
          ("whisper", "whisper-large-v3-turbo")]

NO_FIT_DAN_TEXT = (
    "The coder didn't load: it needs 41 GiB, and 18 GiB is free for a load (48 GiB available, less the 24 GiB reserve "
    "and the 6 GiB the loaded models may still grow into). Using memory now: python3 (chendaniely) 32 GiB, Gemma "
    "27 GiB. Free space with `spark make-room 41G` on the Spark, then try again."
)
NO_FIT_CLAMPED_TEXT = (
    "The coder didn't load: it needs 41 GiB, and nothing is free for a load while make-room holds 70 GiB for Dan "
    "(36 GiB available, less the 24 GiB reserve). Using memory now: a process of Dan's, 70 GiB, the embeddings 8 GiB. "
    "On the Spark, `spark make-room --done` ends the hold."
)
LOADING_TEXT = (
    "The coder didn't start in time: it was waiting its turn while Gemma loads, since one model loads at a time, and "
    "your 30 s ran out. Try again in a minute."
)
LOAD_FAILED_TEXT = (
    'The coder started loading but failed: the engine stopped with "failed to load model". On the Spark, '
    "`spark status` shows the engine's last lines."
)
LOAD_FAILED_UNQUOTED_TEXT = (
    "The coder started loading but failed: the engine stopped. On the Spark, `spark status` shows the engine's last "
    "lines."
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

# plan.md's refusal table, each row with the inputs phase-2a.md's Task 6 gives it: (id, code, moment, the text).
ROWS = [
    ("no_fit, Dan", "no_fit", NO_FIT_DAN, NO_FIT_DAN_TEXT),
    ("no_fit, agent", "no_fit", NO_FIT_AGENT,
     "The coder didn't load: it needs 41 GiB, and 12 GiB is free for a load (106 GiB available, less the 24 GiB "
     "reserve and the 70 GiB make-room holds for Dan). Using memory now: the embeddings 8 GiB, whisper 3 GiB. On the "
     "Spark, `spark make-room --done` ends the hold."),
    ("no_fit, agent, Dan's job in the hold", "no_fit", NO_FIT_AGENT_CLAMPED, NO_FIT_CLAMPED_TEXT),
    ("loading", "loading", LOADING, LOADING_TEXT),
    ("held_by_brake", "held_by_brake", HELD,
     "Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads are paused. They resume "
     "by themselves after 5 minutes above 28 GiB available, if what would reload fits; on the Spark, "
     "`make brake-release` resumes them now."),
    ("held_by_brake, waits for Dan", "held_by_brake", replace(HELD, release_waits_for_dan=True),
     "Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads stay paused until you "
     "release them: on the Spark, `make brake-release`."),
    ("gate_down", "gate_down", Moment(), GATE_DOWN_TEXT),
    ("load_failed", "load_failed", LOAD_FAILED, LOAD_FAILED_TEXT),
    ("load_failed, deadline", "load_failed", replace(LOAD_FAILED, engine_said=None, deadline_s=180),
     "The coder started loading but didn't finish within 180 s. On the Spark, `spark status` shows the engine's last "
     "lines."),
    ("not_downloaded", "not_downloaded", Moment(download_gib=16, **CODER, **DAN),
     "The coder isn't downloaded yet. On the Spark, `make pull` fetches it (16 GiB)."),
    ("restarting", "restarting", Moment(**CODER, **DAN), RESTARTING_TEXT),
    ("llama_swap_down", "llama_swap_down", Moment(**CODER, **DAN),
     "The model service on the Spark isn't answering, and your 30 s ran out. Your phone has the alert; on the Spark, "
     "`make doctor` shows what's wrong."),
    ("draining, Dan", "draining", Moment(inflight=1, drain_for="make-room", **CODER, **DAN),
     "The coder is being unloaded for make-room once its 1 request in flight finishes, and your 30 s ran out. Try "
     "again in a minute: your request can load it again, into the room make-room holds for you, if it fits."),
    ("draining, agent", "draining", Moment(inflight=1, drain_for="make-room", **CODER, **AGENT),
     "The coder is being unloaded for make-room once its 1 request in flight finishes, and your 10 minutes ran out. "
     "It won't load for agent while make-room's hold stands."),
    ("footprint_suspect", "footprint_suspect", SUSPECT,
     "Not loading the coder for agent: it was loading when the brake fired at 03:12, so only Dan can load it again: "
     "`spark load coder` on the Spark, or a request of his from pi on the Mac or the web UI, which loads it if it "
     "fits."),
    ("model_not_found", "model_not_found", Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, **DAN),
     MODEL_NOT_FOUND_TEXT),
    # agent's pi is on the Spark, and agent updates its list there (the controller's ruling, at Task 6).
    ("model_not_found, agent", "model_not_found", Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, **AGENT),
     MODEL_NOT_FOUND_TEXT.replace("On the Mac, `make clients` updates pi's list.",
                                  "On the Spark, as `agent`: pull its clone and run `spark clients pi --write` to "
                                  "update pi's list.")),
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


@pytest.mark.parametrize("code, moment", [pytest.param(c, m, id=i) for i, c, m, _ in ROWS if c in CODES_409])
def test_pi_wont_retry_a_refusal(code, moment):
    assert not pi_retries(pi_shows(409, code, moment))
    assert pi_retries(pi_shows(503, code, moment))  # so the test can fail


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
    # The ceiling binds: the gate's figure, min(48 − 24 − 6, ceiling − committed), is 10, below the breakdown's 18. The
    # words show the gate's figure and never work out one of their own; the breakdown is the plan's, as it was.
    ceiling = refusal("no_fit", replace(NO_FIT_DAN, free_gib=Decimal(10))).message
    assert ceiling == NO_FIT_DAN_TEXT.replace("and 18 GiB is free for a load", "and 10 GiB is free for a load")
    # Rounded down, as admission's figure is: 17.99 never reads as 18.
    assert "and 17 GiB is free for a load" in refusal("no_fit", replace(NO_FIT_DAN, free_gib=Decimal("17.99"))).message
    with pytest.raises(ValueError, match="free_gib"):
        refusal("no_fit", replace(NO_FIT_DAN, free_gib=None))


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


def test_dans_own_hold_is_never_counted_against_him():
    # make-room's hold is Dan's to load into (rule 4): a hold standing changes nothing in his key's no_fit.
    assert refusal("no_fit", replace(NO_FIT_DAN, held_gib=41, hold_counted=False)).message == NO_FIT_DAN_TEXT


def test_agent_names_dans_processes_only_as_a_process_of_dans():
    message = refusal("no_fit", replace(NO_FIT_DAN, **AGENT)).message
    assert "Using memory now: a process of Dan's, 32 GiB, Gemma 27 GiB." in message
    assert "python3" not in message and "chendaniely" not in message


def test_the_wording_follows_the_groups_words():
    # agent's words at the examples' moment, with no hold of Dan's to end: only Dan can make room.
    assert refusal("no_fit", replace(NO_FIT_DAN, words="agent", names_processes=True)).message == (
        NO_FIT_DAN_TEXT.replace("Free space with `spark make-room 41G` on the Spark, then try again.",
                                "Dan can free space with `spark make-room 41G` on the Spark; then try again."))
    assert refusal("no_fit", replace(NO_FIT_DAN, words="dan", names_processes=False)).message == (
        NO_FIT_DAN_TEXT.replace("python3 (chendaniely) 32 GiB", "a process of Dan's, 32 GiB"))
    # agent's words beside Dan's hold, with Dan's processes named because the group's names_processes says so.
    held = refusal("no_fit", replace(NO_FIT_AGENT, holders=[PYTHON, GEMMA], names_processes=True)).message
    assert held.endswith("Using memory now: python3 (chendaniely) 32 GiB, Gemma 27 GiB. On the Spark, "
                         "`spark make-room --done` ends the hold.")
    # A hold that waits for Dan: agent can't release it, so agent's words say who can.
    waits = replace(HELD, release_waits_for_dan=True, **AGENT)
    assert refusal("held_by_brake", waits).message == (
        "Not loading the coder now: memory ran low at 03:12 (19.6 GiB available), and new loads stay paused until "
        "Dan releases them: on the Spark, `make brake-release`.")


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
    # An owed growth that rounds to 0 is left out of the breakdown, never shown as "0 GiB".
    small = refusal("no_fit", replace(NO_FIT_DAN, free_gib=17.7, owed_gib=0.3)).message
    assert "and 17 GiB is free for a load (48 GiB available, less the 24 GiB reserve)." in small


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


def test_waits_read_in_seconds_under_a_minute_and_in_minutes_from_one():
    for seconds, words in ((30, "30 s"), (59, "59 s"), (60, "1 minute"), (90, "2 minutes"), (600, "10 minutes")):
        assert f"and your {words} ran out." in refusal("restarting", replace(MOMENT["restarting"],
                                                                             wait_s=seconds)).message


def test_a_label_starting_with_the_is_capitalised_only_at_a_sentences_start():
    web_ui = refusal("too_many_requests", Moment(key_label="the web UI")).message
    assert web_ui.startswith("The web UI already has as many requests")
    assert refusal("not_downloaded", Moment(model_label="whisper", download_gib=2)).message.startswith(
        "whisper isn't downloaded yet.")
    assert "Not loading Gemma for the web UI:" in refusal(
        "footprint_suspect", replace(SUSPECT, model_label="Gemma", key_label="the web UI")).message


def test_requests_in_flight_read_as_words():
    two = refusal("draining", replace(MOMENT["draining"], inflight=2)).message
    assert two.startswith("The coder is being unloaded for make-room once its 2 requests in flight finish, and")
    none = refusal("draining", replace(MOMENT["draining"], inflight=0, drain_for="idle")).message
    assert none == "The coder is being unloaded, and your 30 s ran out. Try again in a minute."


def test_the_front_builds_its_own_refusals_with_defaults():
    assert refusal("restarting", Moment(model_label="the coder", wait_s=30)).message == RESTARTING_TEXT
    assert refusal("draining", Moment(model_label="the coder", wait_s=30, inflight=1, drain_for="unload")).message == (
        DRAINING_UNLOAD_TEXT)
    assert refusal("model_not_found", Moment(asked_name="qwen3.6-35b-a3b", models=MODELS, words="dan")).message == (
        MODEL_NOT_FOUND_TEXT)
    assert refusal("concurrency_limit", Moment(model_label="the coder")).message == CONCURRENCY_TEXT
    assert refusal("gate_down", Moment()).message == GATE_DOWN_TEXT


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
