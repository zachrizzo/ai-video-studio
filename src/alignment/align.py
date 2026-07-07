"""Word-level narration alignment -> <run_dir>/audio/alignment.json.

Phase 0 scaffold: implements the frozen CLI semantics (no-op exit 0 on runs
without collage work) and the estimated fallback. The alignment workstream
replaces ``_align_segment`` with the whisper ``--word_timestamps True`` path,
keeping the output format frozen in docs/collage/CONTRACTS.md:

    {"<segment_id>": {"duration_seconds": float,
                      "source": "whisper" | "estimated",
                      "words": [{"w": str, "start": float, "end": float}]}}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console

from ..collage.work import collage_segment_ids, print_skipped

console = Console()


def _load_audio_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "audio" / "audio_manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _estimated_words(narration_text: str, duration: float) -> list[dict[str, Any]]:
    tokens = [t for t in narration_text.split() if t.strip()]
    if not tokens or duration <= 0:
        return []
    step = duration / len(tokens)
    return [
        {"w": tok, "start": round(i * step, 3), "end": round((i + 1) * step, 3)}
        for i, tok in enumerate(tokens)
    ]


def _align_segment(
    segment: dict[str, Any],
    audio_entry: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    """Return one alignment.json entry. Scaffold: linear estimate only."""
    duration = float(
        audio_entry.get("duration_seconds") or segment.get("estimated_duration_seconds") or 0
    )
    return {
        "duration_seconds": duration,
        "source": "estimated",
        "words": _estimated_words(segment.get("narration_text", ""), duration),
    }


def run_align(script_path: Path, run_dir: Path) -> None:
    segment_ids = collage_segment_ids(script_path, run_dir)
    if not segment_ids:
        print_skipped("no collage segments in this run")
        return

    script = json.loads(script_path.read_text())
    segments = {s.get("segment_id"): s for s in script.get("segments", [])}
    manifest = _load_audio_manifest(run_dir)

    alignment: dict[str, Any] = {}
    for seg_id in segment_ids:
        segment = segments.get(seg_id, {"segment_id": seg_id})
        alignment[seg_id] = _align_segment(segment, manifest.get(seg_id, {}), run_dir)

    out_path = run_dir / "audio" / "alignment.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(alignment, indent=2))
    console.print(f"[green]Aligned {len(alignment)} segments -> {out_path}[/green]")
    print(json.dumps({"skipped": False, "segments": sorted(alignment)}))
