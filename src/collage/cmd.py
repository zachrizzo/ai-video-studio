"""The `collage` pipeline command — build spec -> HTML -> deterministic render.

Real path (CONTRACTS.md §4, no silent degradation):

1. load scenes/{segment_id}.collage.json (CollageSpec); pydantic errors surface
   as clear per-segment failures, never tracebacks.
2. override duration_seconds from audio/audio_manifest.json, then resolve
   TimeRefs (src.collage.timing.resolve_time) against audio/alignment.json.
   A spec that uses ``at_word`` requires a FRESH alignment entry for the
   segment: missing or stale (older mtime than the segment's wav) alignment is
   a HARD per-segment error telling the operator to re-run `align`.
3. build one self-contained HTML file (src.collage.builder).
4. render via src.animation.fixer.validate_and_render with
   visual_engine="collage" -> scenes/{id}_render/{id}_collage.mp4.

Exit semantics: `{"skipped": true}` + exit 0 ONLY when the run has no collage
work. When there IS work and any segment fails, print the per-segment results
JSON and exit non-zero.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

from ..animation.models import SceneSpec
from .spec import (
    CameraKeyframe,
    CollageSpec,
    MaskElement,
    NodeGraphElement,
    TimeRef,
    load_collage_spec,
)
from .work import collage_segment_ids, print_skipped

console = Console()


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _all_timerefs(spec: CollageSpec) -> list[TimeRef]:
    refs: list[TimeRef] = []
    for kf in spec.camera:
        if isinstance(kf, CameraKeyframe):
            refs.append(kf.time)
    for el in spec.elements:
        if el.enter is not None:
            refs.append(el.enter)
        if el.exit is not None:
            refs.append(el.exit)
        if isinstance(el, MaskElement):
            refs.append(el.reveal)
        if isinstance(el, NodeGraphElement):
            refs.append(el.reveal)
    return refs


def _uses_at_word(spec: CollageSpec) -> bool:
    return any(ref.at_word is not None for ref in _all_timerefs(spec))


def _wav_path(run_dir: Path, audio_entry: dict[str, Any]) -> Path | None:
    raw = audio_entry.get("audio_path")
    if not raw:
        return None
    p = Path(raw)
    if p.exists():
        return p
    alt = run_dir / raw
    if alt.exists():
        return alt
    return None


def run_collage(script_path: Path, run_dir: Path, segment_ids: str = "") -> None:
    run_dir = Path(run_dir)
    seg_ids = collage_segment_ids(script_path, run_dir, only=segment_ids)
    if not seg_ids:
        print_skipped("no collage segments in this run")
        return

    from ..config import PipelineConfig

    config = PipelineConfig()

    script = _load_json(script_path)
    segments = {s.get("segment_id"): s for s in script.get("segments", [])}
    script_style_pack = script.get("style_pack")

    manifest = _load_json(run_dir / "audio" / "audio_manifest.json")
    alignment_path = run_dir / "audio" / "alignment.json"
    alignment = _load_json(alignment_path)
    alignment_mtime = alignment_path.stat().st_mtime if alignment_path.exists() else None

    from .builder import build_collage_html

    results: dict[str, dict] = {}

    for seg_id in seg_ids:
        try:
            video_path = _build_and_render(
                seg_id,
                run_dir,
                config,
                segments.get(seg_id, {}),
                script_style_pack,
                manifest.get(seg_id, {}),
                alignment.get(seg_id),
                alignment_mtime,
                script_path,
                build_collage_html,
            )
            results[seg_id] = {"success": True, "video_path": str(video_path), "error": None}
            console.print(f"[green]Collage rendered {seg_id} -> {video_path}[/green]")
        except Exception as exc:  # noqa: BLE001 — every failure becomes a per-segment result
            results[seg_id] = {"success": False, "video_path": None, "error": str(exc)}
            console.print(f"[red]Collage failed {seg_id}: {exc}[/red]")

    print(json.dumps({"skipped": False, "results": results}))

    if any(not r["success"] for r in results.values()):
        sys.exit(1)


def _build_and_render(
    seg_id: str,
    run_dir: Path,
    config,
    segment: dict[str, Any],
    script_style_pack: str | None,
    audio_entry: dict[str, Any],
    alignment_entry: dict[str, Any] | None,
    alignment_mtime: float | None,
    script_path: Path,
    build_collage_html,
) -> Path:
    spec_path = run_dir / "scenes" / f"{seg_id}.collage.json"
    if not spec_path.exists():
        raise ValueError(f"missing spec file: {spec_path}")

    try:
        spec = load_collage_spec(spec_path)
    except Exception as exc:  # pydantic ValidationError et al.
        raise ValueError(f"invalid CollageSpec {spec_path.name}: {exc}") from exc

    # Real audio duration overrides the spec estimate BEFORE resolving TimeRefs.
    duration = float(audio_entry.get("duration_seconds") or spec.duration_seconds)

    # Style-pack fallthrough: spec.style_pack, else the script-level pack.
    if spec.style_pack is None:
        spec.style_pack = script_style_pack

    narration_text = segment.get("narration_text", "")

    words: list[dict] | None = None
    if _uses_at_word(spec):
        if alignment_entry is None:
            raise ValueError(
                f"alignment is missing for {seg_id}; this spec uses `at_word`. "
                f"Re-run: uv run python -m src.pipeline align {script_path} {run_dir}"
            )
        wav = _wav_path(run_dir, audio_entry)
        if wav is None:
            raise ValueError(
                f"alignment freshness cannot be verified for {seg_id} (wav audio file not found); "
                f"this spec uses `at_word`. Re-run: uv run python -m src.pipeline align {script_path} {run_dir}"
            )
        if alignment_mtime is not None and alignment_mtime < wav.stat().st_mtime:
            raise ValueError(
                f"alignment is stale for {seg_id} (audio is newer than alignment.json); "
                f"re-run: uv run python -m src.pipeline align {script_path} {run_dir}"
            )
        words = alignment_entry.get("words") or []

    html = build_collage_html(
        spec=spec,
        run_dir=run_dir,
        narration_text=narration_text,
        duration_seconds=duration,
        words=words,
    )

    scene_spec = SceneSpec(
        segment_id=seg_id,
        visual_engine="collage",
        code=html,
        target_duration_seconds=duration,
        narration_text=narration_text,
        description=f"Collage scene {seg_id}",
    )

    from ..animation.fixer import validate_and_render

    result = validate_and_render(
        scene_spec,
        config.manim_quality_flag,
        work_dir=run_dir / "scenes",
        render_timeout=config.render_timeout_seconds,
        resolution=config.resolution,
        fps=spec.fps,
    )
    if not result.success:
        raise ValueError(result.error_message or "render failed")
    return Path(result.video_path)
