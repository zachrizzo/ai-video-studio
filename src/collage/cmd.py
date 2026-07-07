"""The `collage` pipeline command — build spec -> HTML -> deterministic render.

Phase 0 scaffold: implements the frozen CLI semantics (no-op exit 0 on runs
without collage work). The collage workstream replaces ``_build_and_render``
with the real path:

1. load scenes/{segment_id}.collage.json (CollageSpec)
2. override duration_seconds from audio/audio_manifest.json, THEN resolve
   TimeRefs (src.collage.timing.resolve_time) against audio/alignment.json
   (stale alignment — older mtime than the segment wav — degrades to the
   estimated fallback with a warning)
3. build one self-contained HTML file (src.collage.builder)
4. render via src.animation.fixer.validate_and_render with
   visual_engine="collage" -> scenes/{id}_render/{id}_collage.mp4
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from .work import collage_segment_ids, print_skipped

console = Console()


def run_collage(script_path: Path, run_dir: Path, segment_ids: str = "") -> None:
    seg_ids = collage_segment_ids(script_path, run_dir, only=segment_ids)
    if not seg_ids:
        print_skipped("no collage segments in this run")
        return

    results: dict[str, dict] = {}
    for seg_id in seg_ids:
        spec_path = run_dir / "scenes" / f"{seg_id}.collage.json"
        if not spec_path.exists():
            results[seg_id] = {"success": False, "error": f"missing spec: {spec_path}"}
            continue
        results[seg_id] = {"success": False, "error": "collage builder not yet implemented"}

    console.print(f"[yellow]Collage rendering not yet implemented for: {seg_ids}[/yellow]")
    print(json.dumps({"skipped": False, "results": results}))
