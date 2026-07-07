"""CollageSpec asset generation — FLUX stills + optional rembg cutouts.

Phase 0 scaffold: implements the frozen CLI semantics (no-op exit 0 on runs
without collage work) and the frozen storage layout
(docs/collage/CONTRACTS.md):

    <run_dir>/assets/{segment_id}/{asset_id}.png   (RGBA for cutouts)

The assets workstream fills in generation: for each CollageAsset with
``generate``, prompt = style-pack flux_prefix + prompt + flux_suffix
(+ cutout suffix when cutout=true), rendered via src.imagegen.flux.generate_image
under src.utils.locks.generation_lock, then rembg + alpha gate + soft-mask
fallback for cutouts. Existing files are always skipped (manual override).
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from ..collage.work import collage_segment_ids, print_skipped

console = Console()


def asset_path(run_dir: Path, segment_id: str, asset_id: str) -> Path:
    """Frozen storage location for a collage asset."""
    return run_dir / "assets" / segment_id / f"{asset_id}.png"


def run_assets(script_path: Path, run_dir: Path, segment_ids: str = "") -> None:
    seg_ids = collage_segment_ids(script_path, run_dir, only=segment_ids)
    if not seg_ids:
        print_skipped("no collage segments in this run")
        return

    # Scaffold: report what would be generated; the assets workstream replaces
    # this loop with real FLUX + rembg work.
    pending: list[str] = []
    for seg_id in seg_ids:
        spec_path = run_dir / "scenes" / f"{seg_id}.collage.json"
        if not spec_path.exists():
            continue
        from ..collage.spec import load_collage_spec

        spec = load_collage_spec(spec_path)
        for asset in spec.assets:
            if asset.generate is None:
                continue
            if not asset_path(run_dir, seg_id, asset.id).exists():
                pending.append(f"{seg_id}/{asset.id}")

    if pending:
        console.print(f"[yellow]Asset generation not yet implemented; pending: {pending}[/yellow]")
    print(json.dumps({"skipped": False, "pending": pending}))
