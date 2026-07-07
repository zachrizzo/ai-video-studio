"""CollageSpec asset generation — FLUX stills + optional rembg cutouts.

Implements the frozen CLI semantics (no-op exit 0 on runs without collage work,
docs/collage/CONTRACTS.md §4) and storage layout (§2):

    <run_dir>/assets/{segment_id}/{asset_id}.png   (RGBA for cutouts)

For each CollageAsset with ``generate``:

    prompt = style_pack.flux_prefix + generate.prompt + style_pack.flux_suffix
             (+ a cutout suffix when generate.cutout)

is rendered via ``src.imagegen.flux.generate_image`` under
``src.utils.locks.generation_lock``; cutouts then go through
``src.imagegen.cutout.extract_cutout`` (rembg + alpha gate — no fallback: a
rejected cutout is a hard per-asset error). Existing asset files are always
skipped (manual override) unless config.image_force.

Prints a JSON summary the fix-loops read, then exits non-zero if any asset
failed. The ``{"skipped": true}`` exit-0 path is only for runs with no collage
work.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rich.console import Console

from ..collage.work import collage_segment_ids, print_skipped

console = Console()

_CUTOUT_PROMPT_SUFFIX = "isolated on plain solid cream background, full figure, centered, no cropping"


def asset_path(run_dir: Path, segment_id: str, asset_id: str) -> Path:
    """Frozen storage location for a collage asset."""
    return run_dir / "assets" / segment_id / f"{asset_id}.png"


def _script_style_pack(script_path: Path) -> str | None:
    """Top-level ``style_pack`` from the script JSON, if any."""
    if not script_path.exists():
        return None
    try:
        script = json.loads(script_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    pack = script.get("style_pack")
    return pack or None


def _build_prompt(pack, base_prompt: str, cutout: bool) -> str:
    parts = [pack.flux_prefix if pack else "", base_prompt, pack.flux_suffix if pack else ""]
    prompt = ", ".join(p.strip() for p in parts if p and p.strip())
    if cutout:
        prompt = f"{prompt}, {_CUTOUT_PROMPT_SUFFIX}"
    return prompt


def run_assets(script_path: Path, run_dir: Path, segment_ids: str = "") -> None:
    seg_ids = collage_segment_ids(script_path, run_dir, only=segment_ids)
    if not seg_ids:
        print_skipped("no collage segments in this run")
        return

    # Deferred/local imports keep mflux (Apple-only) and rembg out of import time.
    from ..collage.spec import default_asset_seed, load_collage_spec
    from ..config import PipelineConfig
    from ..imagegen.cutout import extract_cutout
    from ..imagegen.flux import generate_image
    from ..studio.style_packs import load_style_pack
    from ..utils.locks import generation_lock

    config = PipelineConfig()
    run_dir = Path(run_dir)
    script_style_pack = _script_style_pack(Path(script_path))

    generated: list[str] = []
    skipped_existing: list[str] = []
    cutouts: dict[str, str] = {}
    errors: dict[str, str] = {}

    for seg_id in seg_ids:
        spec_path = run_dir / "scenes" / f"{seg_id}.collage.json"
        if not spec_path.exists():
            continue
        try:
            spec = load_collage_spec(spec_path)
        except Exception as exc:
            # Surface pydantic/validation errors as clean console output, not a traceback.
            console.print(f"[red]Invalid collage spec {spec_path.name}: {exc}[/red]")
            errors[seg_id] = f"spec load failed: {exc}"
            continue

        # Resolve the style pack once per segment: spec.style_pack, else the
        # script-level style_pack, else no pack.
        pack = None
        pack_name = spec.style_pack or script_style_pack
        if pack_name:
            try:
                pack = load_style_pack(pack_name, config.style_packs_dir)
            except FileNotFoundError as exc:
                console.print(f"[red]{seg_id}: {exc}[/red]")
                errors[f"{seg_id}/style_pack"] = str(exc)

        for asset in spec.assets:
            if asset.generate is None:
                continue
            key = f"{seg_id}/{asset.id}"
            target = asset_path(run_dir, seg_id, asset.id)
            if target.exists() and target.stat().st_size > 0 and not config.image_force:
                skipped_existing.append(key)
                console.print(f"[dim]Skip (exists): {key}[/dim]")
                continue

            gen = asset.generate
            prompt = _build_prompt(pack, gen.prompt, gen.cutout)
            seed = gen.seed if gen.seed is not None else default_asset_seed(seg_id, asset.id)

            # Cutouts generate to a temp raw PNG first, then get matted into target.
            raw_path = target.parent / f"{asset.id}_raw.png" if gen.cutout else target

            with generation_lock():  # never run two generations at once (MPS/GPU)
                result = generate_image(
                    prompt=prompt,
                    output_path=raw_path,
                    segment_id=key,
                    width=gen.width,
                    height=gen.height,
                    steps=config.image_steps,
                    model=config.image_model,
                    quantize=config.image_quantize,
                    seed=seed,
                    timeout=config.image_timeout_seconds,
                    models_dir=config.models_dir,
                )

            if not result.success:
                errors[key] = result.error_message or "image generation failed"
                console.print(f"[red]Failed: {key}: {errors[key]}[/red]")
                continue

            if gen.cutout:
                cut = extract_cutout(
                    raw_path,
                    target,
                    model=config.cutout_model,
                    alpha_min=config.cutout_alpha_min,
                    alpha_max=config.cutout_alpha_max,
                    models_dir=config.models_dir,
                )
                cutouts[key] = cut["method"]
                if cut.get("error"):
                    console.print(f"[yellow]  cutout note {key}: {cut['error']}[/yellow]")
                # Tidy the raw still now that the RGBA layer exists.
                try:
                    if raw_path.exists() and raw_path != target:
                        raw_path.unlink()
                except OSError:
                    pass
                if not cut["success"]:
                    errors[key] = cut.get("error") or "cutout failed"
                    continue

            generated.append(key)
            console.print(f"[green]Generated: {key}[/green]")

    print(json.dumps({
        "skipped": False,
        "generated": generated,
        "skipped_existing": skipped_existing,
        "cutouts": cutouts,
        "errors": errors,
    }))

    # Collage work existed and part of it failed — exit non-zero, no silent
    # degradation (CONTRACTS.md §4). Fix-loops read the JSON above.
    if errors:
        sys.exit(1)
