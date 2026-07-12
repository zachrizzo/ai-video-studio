"""Run discovery and manifest building for the Video Studio backend.

A "run" is any immediate subdirectory of the runs root whose name starts with
``run_`` and which contains a ``script.json`` file. The root defaults to
``<studio_home>/runs`` (durable, ~/.video-studio) and can be overridden with
the STUDIO_RUNS_DIR env var; a one-time migration moves run data from the
legacy /tmp location, which macOS periodically wipes.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from src.studio import config

logger = logging.getLogger(__name__)

_LEGACY_RUNS_ROOT = Path("/tmp/mongol-video")
_legacy_migration_done = False

PRODUCTION_STATUS_FILE = ".production_status.json"


def _migrate_legacy_runs(root: Path) -> None:
    """One-time move of run data from the legacy /tmp root into *root*.

    Only fires for the default (durable) root — an explicit STUDIO_RUNS_DIR
    override never triggers it — and only when the new root is empty, so it
    can never clobber existing data.
    """
    global _legacy_migration_done
    if _legacy_migration_done:
        return
    _legacy_migration_done = True
    try:
        legacy = _LEGACY_RUNS_ROOT
        if legacy == root or not legacy.is_dir():
            return
        entries = list(legacy.iterdir())
        if not entries:
            return
        if root.exists() and any(root.iterdir()):
            return
        root.mkdir(parents=True, exist_ok=True)
        for child in entries:
            shutil.move(str(child), str(root / child.name))
        logger.info("Migrated runs from %s to %s", legacy, root)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to migrate runs from %s", _LEGACY_RUNS_ROOT, exc_info=True)


def _runs_root() -> Path:
    """Return the current runs root (respects live env-var changes)."""
    env = os.environ.get("STUDIO_RUNS_DIR", "")
    if env:
        return Path(env).expanduser()
    root = config.studio_home() / "runs"
    _migrate_legacy_runs(root)
    return root


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict[str, Any]:
    """Load JSON from *path*, return empty dict on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_production_status(run_dir: Path) -> dict[str, Any] | None:
    """Load persisted producer state for a run, if present."""
    status = _load_json(run_dir / PRODUCTION_STATUS_FILE)
    return status or None


def _scene_url(run_dir: Path, segment_id: str) -> str | None:
    """Return the media URL for a rendered scene mp4, or None if not present."""
    render_dir = run_dir / "scenes" / f"{segment_id}_render"
    for suffix in ("html", "manim", "collage"):
        mp4 = render_dir / f"{segment_id}_{suffix}.mp4"
        if mp4.exists():
            run_id = run_dir.name
            return f"/media/{run_id}/scenes/{segment_id}_render/{segment_id}_{suffix}.mp4"
    return None


def _audio_url(run_dir: Path, segment_id: str) -> str | None:
    """Return the media URL for a segment's audio file, or None."""
    for ext in (".mp3", ".wav", ".m4a"):
        audio = run_dir / "audio" / f"audio_{segment_id}{ext}"
        if audio.exists():
            run_id = run_dir.name
            return f"/media/{run_id}/audio/audio_{segment_id}{ext}"
    return None


def _visual_stems(seg: dict[str, Any]) -> list[str]:
    """Return expected generated visual file stems for a raw segment dict."""
    seg_id = seg.get("segment_id", "")
    if not seg_id or seg.get("visual_type") != "scene":
        return []
    beats = seg.get("visual_beats") or []
    if beats:
        if len(beats) == 1:
            return [seg_id]
        return [f"{seg_id}_b{i:02d}" for i in range(1, len(beats) + 1)]
    if seg.get("image_prompt"):
        return [seg_id]
    return []


def _image_urls(run_dir: Path, seg: dict[str, Any]) -> list[str]:
    """Return media URLs for a segment's generated beat stills.

    Collage segments keep their art under assets/<segment_id>/ instead of
    images/ — surface those too so the viewer's IMAGES column isn't empty for
    the (default) collage engine.
    """
    run_id = run_dir.name
    urls: list[str] = []
    for stem in _visual_stems(seg):
        png = run_dir / "images" / f"{stem}.png"
        if png.exists():
            urls.append(f"/media/{run_id}/images/{stem}.png")
    seg_id = seg.get("segment_id", "")
    legacy = run_dir / "images" / f"{seg_id}.png"
    if not urls and legacy.exists():
        urls.append(f"/media/{run_id}/images/{seg_id}.png")
    if not urls:
        assets_dir = run_dir / "assets" / seg_id
        if assets_dir.is_dir():
            for png in sorted(assets_dir.glob("*.png")):
                urls.append(f"/media/{run_id}/assets/{seg_id}/{png.name}")
    return urls


def _clip_urls(run_dir: Path, seg: dict[str, Any]) -> list[str]:
    """Return media URLs for a segment's generated beat clips."""
    run_id = run_dir.name
    urls: list[str] = []
    for stem in _visual_stems(seg):
        mp4 = run_dir / "clips" / f"{stem}.mp4"
        if mp4.exists():
            urls.append(f"/media/{run_id}/clips/{stem}.mp4")
    seg_id = seg.get("segment_id", "")
    legacy = run_dir / "clips" / f"{seg_id}.mp4"
    if not urls and legacy.exists():
        urls.append(f"/media/{run_id}/clips/{seg_id}.mp4")
    return urls


def _script_storyboard(seg: dict[str, Any]) -> list[dict[str, Any]]:
    beats = seg.get("visual_beats") or []
    if not beats:
        return []
    stems = _visual_stems(seg)
    frames: list[dict[str, Any]] = []
    for index, beat in enumerate(beats):
        stem = stems[index] if index < len(stems) else f"{seg.get('segment_id', '')}_b{index + 1:02d}"
        frames.append(
            {
                "frame_id": stem,
                "beat_id": beat.get("beat_id") or f"b{index + 1:02d}",
                "description": beat.get("description"),
                "shot_type": beat.get("shot_type"),
                "composition": beat.get("composition"),
                "action": beat.get("action"),
                "camera_motion": beat.get("camera_motion"),
                "transition": beat.get("transition"),
                "duration_seconds": beat.get("duration_seconds"),
                "continuity_notes": beat.get("continuity_notes") or [],
                "asset_notes": beat.get("asset_notes") or [],
            }
        )
    return frames


def _segment_status(
    run_dir: Path,
    seg: dict[str, Any],
    scene_url: str | None,
    image_urls: list[str],
    clip_urls: list[str],
    audio_url: str | None,
    qa_segment: dict[str, Any] | None = None,
) -> str:
    """Derive the status string for a segment.

    Rules (in priority order):
    1. ``failed``     – fallback mp4 marker exists.
    2. ``qa_failed``  – artifacts exist but segment QA has errors.
    3. ``needs_review`` – artifacts exist but segment QA has warnings.
    4. ``approved``   – artifacts exist and segment QA passed.
    5. ``done``       – artifacts exist but no QA report exists yet.
    6. ``generating`` – some but not all expected artifacts exist.
    7. ``pending``    – nothing exists yet.
    """
    segment_id = seg.get("segment_id", "")
    fallback = (
        run_dir / "scenes" / f"{segment_id}_render" / f"{segment_id}_fallback.mp4"
    )
    if fallback.exists():
        return "failed"

    if seg.get("visual_type") == "scene":
        expected_visuals = len(_visual_stems(seg)) or 1
        visual_ready = len(clip_urls) >= expected_visuals
        visual_partial = bool(image_urls or clip_urls)
    else:
        visual_ready = bool(scene_url)
        visual_partial = bool(scene_url)

    if visual_ready and audio_url:
        if qa_segment:
            qa_status = qa_segment.get("status")
            if qa_status == "failed":
                return "qa_failed"
            if qa_status == "warning":
                return "needs_review"
            if qa_status == "passed":
                return "approved"
        return "done"
    if visual_partial or audio_url:
        return "generating"
    return "pending"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_runs() -> list[dict[str, Any]]:
    """Return summary info for every valid run, most recently touched first."""
    root = _runs_root()
    if not root.is_dir():
        return []

    entries: list[tuple[float, dict[str, Any]]] = []
    for child in root.iterdir():
        if not child.is_dir() or not child.name.startswith("run_"):
            continue
        script_path = child / "script.json"
        if not script_path.exists():
            continue
        try:
            mtime = script_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        script = _load_json(script_path)
        run_id = child.name
        title = script.get("title", run_id)
        segments = script.get("segments", [])
        has_final = (child / "final.mp4").exists()
        final_url = f"/media/{run_id}/final.mp4" if has_final else None
        qa_report = _load_json(child / "qa_report.json")
        production = _load_production_status(child)
        entries.append(
            (
                mtime,
                {
                    "id": run_id,
                    "title": title,
                    "segment_count": len(segments),
                    "has_final_video": has_final,
                    "final_video_url": final_url,
                    "qa_status": qa_report.get("status"),
                    "production": production,
                },
            )
        )
    # Newest first so the viewer's default selection is the latest work,
    # not whichever random hex id sorts lowest.
    entries.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in entries]


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
    qa_report = _load_json(run_dir / "qa_report.json")
    qa_segments = qa_report.get("segments", {}) if isinstance(qa_report, dict) else {}
    storyboard_report = _load_json(run_dir / "storyboard.json")
    storyboard_segments = {
        item.get("segment_id"): item.get("frames", [])
        for item in storyboard_report.get("segments", [])
        if isinstance(item, dict)
    }

    title = script.get("title", run_id)
    has_final = (run_dir / "final.mp4").exists()
    final_video_url = f"/media/{run_id}/final.mp4" if has_final else None
    production = _load_production_status(run_dir)

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
        clip_urls = _clip_urls(run_dir, seg)
        audio_url = _audio_url(run_dir, seg_id)
        image_urls = _image_urls(run_dir, seg)
        clip_url = clip_urls[0] if clip_urls else None
        image_url = image_urls[0] if image_urls else None
        qa_segment = qa_segments.get(seg_id) if isinstance(qa_segments, dict) else None
        storyboard = storyboard_segments.get(seg_id) or _script_storyboard(seg)
        status = _segment_status(
            run_dir,
            seg,
            scene_url,
            image_urls,
            clip_urls,
            audio_url,
            qa_segment,
        )

        # Duration: prefer audio manifest, fall back to script estimate. A
        # failed segment's manifest entry has duration_seconds deliberately
        # zeroed out, so it must fall back too rather than display as 0:00.
        manifest_entry = audio_manifest.get(seg_id)
        manifest_duration = float(manifest_entry.get("duration_seconds", 0)) if manifest_entry else 0.0
        if manifest_entry and not manifest_entry.get("failed") and manifest_duration > 0:
            duration_seconds = manifest_duration
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
                "image_urls": image_urls,
                "clip_url": clip_url,
                "clip_urls": clip_urls,
                "scene_url": scene_url,
                "audio_url": audio_url,
                "duration_seconds": duration_seconds,
                "visual_count": len(_visual_stems(seg)) or (1 if scene_url else len(clip_urls)),
                "storyboard": storyboard,
                "qa": qa_segment,
            }
        )

    # Total duration: sum of the (already-correct) per-segment durations, so
    # this can't diverge from what the UI shows per segment; only fall back
    # to the script estimate when there are no segments to sum at all.
    if segments:
        total_duration = sum(s["duration_seconds"] for s in segments)
    else:
        total_duration = float(script.get("total_estimated_duration_seconds", 0))

    return {
        "id": run_id,
        "title": title,
        "final_video_url": final_video_url,
        "total_duration_seconds": total_duration,
        "qa": qa_report or None,
        "production": production,
        "segments": segments,
    }
