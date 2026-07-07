"""TimeRef resolution against real narration timing.

Frozen signature (docs/collage/CONTRACTS.md): the builder calls
``resolve_time(ref, narration_text=..., duration_seconds=..., words=...)``
where ``duration_seconds`` is the REAL audio duration (audio-manifest override
already applied) and ``words`` is the segment's entry from alignment.json
(``[{"w": str, "start": float, "end": float}]``) or None when unavailable.

This module ships with the linear-estimate implementation so builds never
block on whisper. The alignment workstream replaces the word lookup with
real timestamps while keeping this exact signature and the estimate as the
per-cue fallback.
"""

from __future__ import annotations

import re
from typing import Any

_WORD_RE = re.compile(r"[a-z0-9']+")


def normalize_word(token: str) -> str:
    """Lowercase and strip punctuation/whitespace from a (whisper) word token."""
    match = _WORD_RE.search(token.lower())
    return match.group(0) if match else ""


def _script_words(narration_text: str) -> list[str]:
    return [w for w in (normalize_word(t) for t in narration_text.split()) if w]


def _estimate_word_time(word: str, occurrence: int, narration_text: str, duration: float) -> float:
    """Linear estimate: position of the word among script words × duration."""
    words = _script_words(narration_text)
    if not words:
        return 0.0
    target = normalize_word(word)
    seen = 0
    for i, w in enumerate(words):
        if w == target:
            seen += 1
            if seen == occurrence:
                return duration * i / len(words)
    # Word not in narration at all: fall back to mid-scene rather than raising —
    # the builder surfaces a warning, never a hard failure.
    return duration * 0.5


def resolve_time(
    ref: Any,
    *,
    narration_text: str,
    duration_seconds: float,
    words: list[dict[str, Any]] | None = None,
) -> float:
    """Resolve a TimeRef to absolute scene seconds, clamped to [0, duration]."""
    if ref.at is not None:
        t = ref.at
    elif ref.at_frac is not None:
        t = ref.at_frac * duration_seconds
    else:
        t = _resolve_word(ref.at_word, ref.occurrence, narration_text, duration_seconds, words)
    t += ref.offset
    return max(0.0, min(t, duration_seconds))


def _resolve_word(
    word: str,
    occurrence: int,
    narration_text: str,
    duration: float,
    words: list[dict[str, Any]] | None,
) -> float:
    if words:
        target = normalize_word(word)
        seen = 0
        for entry in words:
            if normalize_word(str(entry.get("w", ""))) == target:
                seen += 1
                if seen == occurrence:
                    try:
                        return float(entry["start"])
                    except (KeyError, TypeError, ValueError):
                        break
    # Missing word (or no alignment): linear estimate for this cue only.
    return _estimate_word_time(word, occurrence, narration_text, duration)
