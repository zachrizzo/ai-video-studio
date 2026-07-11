"""Helpers for ordered visual beats inside a narrated segment."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SegmentVisualBeat:
    segment_id: str
    index: int
    total: int
    file_stem: str
    beat_id: str
    prompt: str
    description: str | None = None
    shot_type: str | None = None
    composition: str | None = None
    action: str | None = None
    camera_motion: str | None = None
    transition: str | None = None
    continuity_notes: list[str] | None = None
    asset_notes: list[str] | None = None
    weight: float = 1.0
    duration_seconds: float | None = None
    negative_prompt: str | None = None

    @property
    def label(self) -> str:
        return self.segment_id if self.total == 1 else f"{self.segment_id}:b{self.index:02d}"


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _clean_id(value: str | None, fallback: str) -> str:
    raw = (value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_-]+", "_", raw).strip("_")
    return raw or fallback


def _as_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if str(item).strip()]


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _compose_prompt(segment: Any, beat: Any, prompt: str) -> str:
    parts = [prompt.strip()]
    storyboard_details = [
        _field(beat, "shot_type"),
        _field(beat, "composition"),
        _field(beat, "action"),
    ]
    storyboard_details = [str(item).strip() for item in storyboard_details if str(item or "").strip()]
    if storyboard_details:
        parts.append("Storyboard: " + "; ".join(storyboard_details))

    constraints = _as_list(_field(segment, "visual_constraints")) + _as_list(
        _field(beat, "visual_constraints")
    )
    if constraints:
        parts.append("Visual constraints: " + "; ".join(constraints))

    negative_prompt = _field(beat, "negative_prompt") or _field(segment, "negative_prompt")
    if negative_prompt:
        parts.append("Avoid: " + str(negative_prompt).strip())

    return ". ".join(part for part in parts if part)


def segment_visual_beats(segment: Any) -> list[SegmentVisualBeat]:
    """Return resolved visual beats for a scene segment.

    Old scripts without ``visual_beats`` get a single beat using ``image_prompt``
    and the legacy ``{segment_id}.png/mp4`` filenames.
    """
    segment_id = str(_field(segment, "segment_id", "")).strip()
    if not segment_id or _field(segment, "visual_type") != "scene":
        return []

    raw_beats = list(_field(segment, "visual_beats", []) or [])
    if not raw_beats:
        prompt = _field(segment, "image_prompt")
        if not prompt:
            return []
        return [
            SegmentVisualBeat(
                segment_id=segment_id,
                index=1,
                total=1,
                file_stem=segment_id,
                beat_id=segment_id,
                prompt=_compose_prompt(segment, {}, str(prompt)),
                description=_field(segment, "visual_intent"),
                shot_type=None,
                composition=None,
                action=None,
                camera_motion=None,
                transition=None,
                continuity_notes=[],
                asset_notes=[],
                negative_prompt=_field(segment, "negative_prompt"),
            )
        ]

    resolved: list[SegmentVisualBeat] = []
    total = len(raw_beats)
    for idx, beat in enumerate(raw_beats, start=1):
        fallback_id = f"b{idx:02d}"
        beat_id = _clean_id(_field(beat, "beat_id"), fallback_id)
        file_stem = segment_id if total == 1 else f"{segment_id}_b{idx:02d}"
        prompt = (
            _field(beat, "image_prompt")
            or _field(segment, "image_prompt")
            or _field(beat, "description")
            or _field(segment, "visual_intent")
            or _field(segment, "section_title")
        )
        resolved.append(
            SegmentVisualBeat(
                segment_id=segment_id,
                index=idx,
                total=total,
                file_stem=file_stem,
                beat_id=beat_id,
                prompt=_compose_prompt(segment, beat, str(prompt or "")),
                description=_field(beat, "description"),
                shot_type=_field(beat, "shot_type"),
                composition=_field(beat, "composition"),
                action=_field(beat, "action"),
                camera_motion=_field(beat, "camera_motion"),
                transition=_field(beat, "transition"),
                continuity_notes=_as_list(_field(beat, "continuity_notes")),
                asset_notes=_as_list(_field(beat, "asset_notes")),
                weight=_positive_float(_field(beat, "weight")) or 1.0,
                duration_seconds=_positive_float(_field(beat, "duration_seconds")),
                negative_prompt=(
                    _field(beat, "negative_prompt") or _field(segment, "negative_prompt")
                ),
            )
        )
    return resolved


def beat_image_path(run_dir: Path, beat: SegmentVisualBeat) -> Path:
    return Path(run_dir) / "images" / f"{beat.file_stem}.png"


def beat_clip_path(run_dir: Path, beat: SegmentVisualBeat) -> Path:
    return Path(run_dir) / "clips" / f"{beat.file_stem}.mp4"


def beat_matches_filter(beat: SegmentVisualBeat, only: set[str]) -> bool:
    if not only:
        return True
    return bool(
        {
            beat.segment_id,
            beat.file_stem,
            beat.beat_id,
            beat.label,
            f"{beat.segment_id}:{beat.beat_id}",
            f"{beat.segment_id}:b{beat.index:02d}",
        }
        & only
    )


def split_beat_durations(total_seconds: float, beats: list[SegmentVisualBeat]) -> list[float]:
    if not beats:
        return []
    total = max(float(total_seconds or 0), 0.1)
    durations = [beat.duration_seconds for beat in beats]
    explicit_total = sum(duration or 0 for duration in durations)
    missing_indexes = [i for i, duration in enumerate(durations) if not duration]

    if explicit_total > 0:
        allocated = [duration or 0 for duration in durations]
        if missing_indexes:
            remaining = total - explicit_total
            if remaining > 0:
                weight_total = sum(max(beats[i].weight, 0.1) for i in missing_indexes) or 1
                for i in missing_indexes:
                    allocated[i] = remaining * (max(beats[i].weight, 0.1) / weight_total)
            else:
                avg_explicit = explicit_total / max(len(beats) - len(missing_indexes), 1)
                for i in missing_indexes:
                    allocated[i] = avg_explicit * max(beats[i].weight, 0.1)
    else:
        weight_total = sum(max(beat.weight, 0.1) for beat in beats) or 1
        allocated = [total * (max(beat.weight, 0.1) / weight_total) for beat in beats]

    if len(allocated) == 1:
        allocated[0] = total
        return allocated

    allocated_total = sum(allocated)
    if allocated_total > 0 and abs(allocated_total - total) > 1e-9:
        allocated = [value * (total / allocated_total) for value in allocated]
    allocated = [max(0.1, value) for value in allocated]

    # Flooring above can push the sum back over `total` when total is small
    # relative to the beat count. Recover as much of that overshoot as
    # possible by shrinking whichever beats aren't already pinned to the
    # floor, proportionally, iterating until stable.
    for _ in range(len(allocated)):
        excess = sum(allocated) - total
        if excess <= 1e-9:
            break
        reducible = [i for i, value in enumerate(allocated) if value > 0.1 + 1e-9]
        if not reducible:
            break  # every beat is already at the floor; total is too small to honor exactly
        reducible_total = sum(allocated[i] for i in reducible)
        for i in reducible:
            share = excess * (allocated[i] / reducible_total)
            allocated[i] = max(0.1, allocated[i] - share)
    return allocated


def ltx_motion_prompt(beat: SegmentVisualBeat) -> str:
    """Build a short action-focused image-to-video prompt for LTX 2.3.

    LTX is more coherent when the video prompt describes only the motion and
    constraints for the supplied still. The full FLUX image prompt can contain
    long negative clauses and subject details that encourage visual drift.
    """
    shot = beat.shot_type or "stable shot"
    composition = beat.composition or "the exact layout of the reference image"
    action = beat.action or "dynamic motion that brings the subject to life"
    camera = beat.camera_motion or "smooth, expressive camera movement"

    parts = [
        "Animate the provided still image into a living shot.",
        f"Keep it as a {shot} with {composition}.",
        f"Bring this action to life: {action}.",
        f"Camera: {camera}.",
        "Preserve the original character identity, pose language, line thickness, color palette, and layout.",
        "Avoid frozen subjects: the subject visibly moves through the described action while the drawing style stays consistent.",
        "The frame stays simple and uncluttered, matching the source image.",
    ]
    if beat.description:
        parts.append(f"Story beat: {beat.description}.")
    if beat.transition:
        parts.append(f"Plan for transition: {beat.transition}.")
    if beat.continuity_notes:
        parts.append("Continuity: " + "; ".join(beat.continuity_notes) + ".")
    if beat.asset_notes:
        parts.append("Asset notes: " + "; ".join(beat.asset_notes) + ".")
    return " ".join(part for part in parts if part)
