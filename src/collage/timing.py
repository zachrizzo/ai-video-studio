"""TimeRef resolution against real narration timing.

Frozen signature (docs/collage/CONTRACTS.md §5): the builder calls
``resolve_time(ref, narration_text=..., duration_seconds=..., words=...)``
where ``duration_seconds`` is the REAL audio duration (audio-manifest override
already applied) and ``words`` is the segment's entry from alignment.json
(``[{"w": str, "start": float, "end": float}]``) or None when unavailable.

There is NO linear-estimate fallback (§3, §4): an ``at_word`` TimeRef requires
real alignment. If ``words`` is None or the word/occurrence is not found,
``resolve_time`` raises ``ValueError`` with an actionable message. ``at`` and
``at_frac`` refs never need alignment.
"""

from __future__ import annotations

import re
from typing import Any

_WORD_RE = re.compile(r"[a-z0-9']+")


def normalize_word(token: str) -> str:
    """Lowercase and strip punctuation/whitespace from a (whisper) word token."""
    match = _WORD_RE.search(token.lower())
    return match.group(0) if match else ""


def resolve_time(
    ref: Any,
    *,
    narration_text: str,
    duration_seconds: float,
    words: list[dict[str, Any]] | None = None,
) -> float:
    """Resolve a TimeRef to absolute scene seconds, clamped to [0, duration].

    Raises ValueError for an ``at_word`` ref when alignment is missing or the
    word/occurrence cannot be found.
    """
    if ref.at is not None:
        t = ref.at
    elif ref.at_frac is not None:
        t = ref.at_frac * duration_seconds
    else:
        t = _resolve_word(ref.at_word, ref.occurrence, words)
    t += ref.offset
    return max(0.0, min(t, duration_seconds))


def _resolve_word(
    word: str,
    occurrence: int,
    words: list[dict[str, Any]] | None,
) -> float:
    if not words:
        raise ValueError(
            f"TimeRef at_word={word!r} needs word-level alignment, but none is "
            f"available for this segment. Run `python -m src.pipeline align "
            f"<script.json> <run_dir>` (whisper) before building collage scenes."
        )
    target = normalize_word(word)
    seen = 0
    for entry in words:
        if normalize_word(str(entry.get("w", ""))) == target:
            seen += 1
            if seen == occurrence:
                try:
                    return float(entry["start"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(
                        f"TimeRef at_word={word!r} (occurrence {occurrence}) matched an "
                        f"alignment entry with no usable start time: {entry!r}"
                    ) from exc
    raise ValueError(
        f"TimeRef at_word={word!r} (occurrence {occurrence}) not found in the "
        f"aligned narration. Correct the spec's word/occurrence or re-run `align` "
        f"if the audio changed."
    )
