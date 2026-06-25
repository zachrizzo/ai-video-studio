"""Run discovery and manifest building for the Video Studio backend.

A "run" is any immediate subdirectory of RUNS_ROOT whose name starts with
``run_`` and which contains a ``script.json`` file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Default location; overridden by env var STUDIO_RUNS_DIR
RUNS_ROOT = Path(os.environ.get("STUDIO_RUNS_DIR", "/tmp/mongol-video"))


def _runs_root() -> Path:
    """Return the current runs root (respects live env-var changes)."""
    return Path(os.environ.get("STUDIO_RUNS_DIR", "/tmp/mongol-video"))


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    """Load JSON from *path*, return empty dict on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _scene_url(run_dir: Path, segment_id: str) -> str | None:
    """Return the media URL for a rendered scene mp4, or None if not present."""
    render_dir = run_dir / "scenes" / f"{segment_id}_render"
    for suffix in ("html", "manim"):
        mp4 = render_dir / f"{segment_id}_{suffix}.mp4"
        if mp4.exists():
            run_id = run_dir.name
            return f"/media/{run_id}/scenes/{segment_id}_render/{segment_id}_{suffix}.mp4"
    return None


def _audio_url(run_dir: Path, segment_id: str) -> str | None:
    """Return the media URL for a segment's audio file, or None."""
    # The audio files are named audio_{segment_id}.mp3
    mp3 = run_dir / "audio" / f"audio_{segment_id}.mp3"
    if mp3.exists():
        run_id = run_dir.name
        return f"/media/{run_id}/audio/audio_{segment_id}.mp3"
    return None


def _image_url(run_dir: Path, segment_id: str) -> str | None:
    """Return the media URL for a segment's still image, or None."""
    png = run_dir / "images" / f"{segment_id}.png"
    if png.exists():
        run_id = run_dir.name
        return f"/media/{run_id}/images/{segment_id}.png"
    return None


def _clip_url(run_dir: Path, segment_id: str) -> str | None:
    """Return the media URL for a composite clip, or None."""
    mp4 = run_dir / "clips" / f"{segment_id}.mp4"
    if mp4.exists():
        run_id = run_dir.name
        return f"/media/{run_id}/clips/{segment_id}.mp4"
    return None


def _segment_status(
    run_dir: Path,
    segment_id: str,
    scene_url: str | None,
    clip_url: str | None,
    audio_url: str | None,
) -> str:
    """Derive the status string for a segment.

    Rules (in priority order):
    1. ``failed``     – fallback mp4 marker exists.
    2. ``done``       – (scene_url or clip_url) AND audio_url present.
    3. ``generating`` – some but not all expected artifacts exist.
    4. ``pending``    – nothing exists yet.
    """
    fallback = (
        run_dir / "scenes" / f"{segment_id}_render" / f"{segment_id}_fallback.mp4"
    )
    if fallback.exists():
        return "failed"
    if (scene_url or clip_url) and audio_url:
        return "done"
    if scene_url or clip_url or audio_url:
        return "generating"
    return "pending"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_runs() -> list[dict[str, Any]]:
    """Return summary info for every valid run in RUNS_ROOT."""
    root = _runs_root()
    if not root.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not child.name.startswith("run_"):
            continue
        script_path = child / "script.json"
        if not script_path.exists():
            continue
        script = _load_json(script_path)
        run_id = child.name
        title = script.get("title", run_id)
        segments = script.get("segments", [])
        has_final = (child / "final.mp4").exists()
        final_url = f"/media/{run_id}/final.mp4" if has_final else None
        results.append(
            {
                "id": run_id,
                "title": title,
                "segment_count": len(segments),
                "has_final_video": has_final,
                "final_video_url": final_url,
            }
        )
    return results


def get_run(run_id: str) -> dict[str, Any] | None:
    """Return the full manifest for *run_id*, or None if not found."""
    root = _runs_root()
    run_dir = root / run_id
    script_path = run_dir / "script.json"
    if not run_dir.is_dir() or not script_path.exists():
        return None

    script = _load_json(script_path)

    # Audio manifest: keyed by segment_id → {audio_path, duration_seconds}
    audio_manifest = _load_json(run_dir / "audio" / "audio_manifest.json")

    title = script.get("title", run_id)
    has_final = (run_dir / "final.mp4").exists()
    final_video_url = f"/media/{run_id}/final.mp4" if has_final else None

    # Build segments
    segments: list[dict[str, Any]] = []
    for seg in script.get("segments", []):
        seg_id = seg.get("segment_id", "")
        section_title = seg.get("section_title", "")
        narration_text = seg.get("narration_text", "")

        # Flatten animation_cues to the contract shape
        cues = [
            {
                "timestamp_hint": c.get("timestamp_hint", ""),
                "description": c.get("description", ""),
            }
            for c in seg.get("animation_cues", [])
        ]

        scene_url = _scene_url(run_dir, seg_id)
        clip_url = _clip_url(run_dir, seg_id)
        audio_url = _audio_url(run_dir, seg_id)
        image_url = _image_url(run_dir, seg_id)
        status = _segment_status(run_dir, seg_id, scene_url, clip_url, audio_url)

        # Duration: prefer audio manifest, fall back to script estimate
        if seg_id in audio_manifest:
            duration_seconds = float(audio_manifest[seg_id].get("duration_seconds", 0))
        else:
            duration_seconds = float(seg.get("estimated_duration_seconds", 0))

        segments.append(
            {
                "segment_id": seg_id,
                "section_title": section_title,
                "narration_text": narration_text,
                "cues": cues,
                "status": status,
                "image_url": image_url,
                "clip_url": clip_url,
                "scene_url": scene_url,
                "audio_url": audio_url,
                "duration_seconds": duration_seconds,
            }
        )

    # Total duration: sum of audio manifest durations if available, else script estimate
    if audio_manifest:
        total_duration = sum(
            float(v.get("duration_seconds", 0)) for v in audio_manifest.values()
        )
    else:
        total_duration = float(script.get("total_estimated_duration_seconds", 0))
        if total_duration == 0:
            total_duration = sum(s["duration_seconds"] for s in segments)

    return {
        "id": run_id,
        "title": title,
        "final_video_url": final_video_url,
        "total_duration_seconds": total_duration,
        "segments": segments,
    }
