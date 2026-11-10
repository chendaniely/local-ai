"""vid2sub's parser, for test_speech.py: the speech service's answer has to pass it unchanged (the design's constraint 1).

MIT License, Copyright (c) 2026 Daniel Chen. ms, fill_untimed, attach_punctuation, _segment_for, parse_openai and
NO_LINE_END are verbatim copies from vid2sub at commit ac29e7b (src/vid2sub/parse/words.py and openai.py, and
subs/text.py), and are not to be edited here: if vid2sub's parser changes, copy it again. What's above them stands in for
vid2sub's own types, with the fields and the check these functions use (Word's times are never negative)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    start_ms: int
    end_ms: int
    prob: float | None = None
    timed: bool = True

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms < 0:
            raise ValueError("a time is negative")


@dataclass(frozen=True)
class Segment:
    text: str
    start_ms: int
    end_ms: int
    words: tuple[Word, ...] = ()
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None


@dataclass(frozen=True)
class ParsedChunk:
    segments: tuple[Segment, ...]
    language: str | None
    language_probs: dict[str, float]


class langs:
    """vid2sub's table of languages, as far as parse_openai reads it."""

    LANGS = ("en", "ja", "zh", "yue", "ko")
    _FULL = {"english": "en", "japanese": "ja", "chinese": "zh", "cantonese": "yue", "korean": "ko"}

    @staticmethod
    def from_whisper_full(name: str) -> str | None:
        return langs._FULL.get(name.strip().lower())


NO_LINE_END = frozenset("（「『【〔〈《〘〖〝‘“｟«([{")


def ms(seconds: Any) -> int | None:
    if seconds is None or isinstance(seconds, bool):
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return round(value * 1000)


def fill_untimed(raw: Sequence[Sequence[Any]], start_ms: int = 0) -> list[Word]:
    words: list[Word] = []
    prev_end = start_ms
    for text, start, end, prob in raw:
        if start is None or end is None or end < start:
            words.append(Word(text=text, start_ms=prev_end, end_ms=prev_end, prob=prob,
                              timed=False))
        else:
            words.append(Word(text=text, start_ms=start, end_ms=end, prob=prob))
            prev_end = end
    return words


def attach_punctuation(text: str, units: Sequence[tuple[str, int | None, int | None]], *,
                       start_ms: int = 0) -> list[Word] | None:
    lowered = text.lower()
    fold = len(lowered) == len(text)
    haystack = lowered if fold else text
    raw: list[list[Any]] = []
    pos = 0
    for utext, start, end in units:
        needle = utext.strip()
        if not needle:
            continue
        found = haystack.find(needle.lower() if fold else needle, pos)
        if found < 0:
            return None
        gap = text[pos:found]
        lead = ""
        if gap:
            space = next((i for i, ch in enumerate(gap) if ch.isspace()), None)
            if space is not None:
                closing, lead = gap[:space], " " + gap[space:].strip()
            else:
                k = len(gap)
                while k > 0 and gap[k - 1] in NO_LINE_END:
                    k -= 1
                closing, lead = gap[:k], gap[k:]
            if closing:
                if raw:
                    raw[-1][0] += closing
                else:
                    lead = closing + lead
        raw.append([lead + text[found:found + len(needle)], start, end, None])
        pos = found + len(needle)
    if not raw:
        return None
    raw[-1][0] += text[pos:].rstrip()
    return fill_untimed(raw, start_ms)


def _segment_for(mid: int, spans: list[tuple[int, int]]) -> int:
    best, best_gap = 0, None
    for i, (start, end) in enumerate(spans):
        if start <= mid < end:
            return i
        gap = start - mid if mid < start else mid - end
        if best_gap is None or gap < best_gap:
            best, best_gap = i, gap
    return best


def parse_openai(data: Any) -> ParsedChunk:
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ParseError("not an OpenAI verbose_json response")
    raw_lang = str(data.get("language") or "")
    language = langs.from_whisper_full(raw_lang) or (raw_lang if raw_lang in langs.LANGS else None)
    words = [w for w in data.get("words") or [] if isinstance(w, dict)]
    segs = data["segments"]
    for seg in segs:
        if not isinstance(seg, dict):
            raise ParseError("an OpenAI segment is not an object")
    spans = []
    for seg in segs:
        s_start = ms(seg.get("start")) or 0
        spans.append((s_start, max(ms(seg.get("end")) or s_start, s_start)))
    mine: list[list[tuple[str, int, int]]] = [[] for _ in segs]
    if segs:
        for w in words:
            ws, we = ms(w.get("start")), ms(w.get("end"))
            if ws is None or we is None:
                continue
            mine[_segment_for((ws + we) // 2, spans)].append((str(w.get("word", "")), ws, we))
    segments = []
    for seg, (s_start, s_end), units in zip(segs, spans, mine, strict=True):
        text = str(seg.get("text", ""))
        aligned = attach_punctuation(text, units, start_ms=s_start) if units else None
        if aligned and "".join(w.text for w in aligned) == text.rstrip():
            text, words_out = "".join(w.text for w in aligned), tuple(aligned)
        else:
            words_out = ()
        segments.append(Segment(
            text=text, start_ms=s_start, end_ms=s_end, words=words_out,
            avg_logprob=seg.get("avg_logprob"), no_speech_prob=seg.get("no_speech_prob"),
            compression_ratio=seg.get("compression_ratio")))
    # The engine reports only the language, not a distribution: 1.0 means "the engine said so",
    # not a measured probability.
    return ParsedChunk(tuple(segments), language, {language: 1.0} if language else {})
