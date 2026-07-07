"""Local image generation via mflux (MLX, Apple Silicon).

Runs an mflux CLI as a subprocess so model memory is freed after each image
and the first-run model download is isolated. No server required.

Model notes (A/B validated 2026-07, mflux 0.18.0)
-------------------------------------------------
Default: ``z-image-turbo`` (Tongyi-MAI, Apache-2.0, ungated) at ``steps=8``,
``quantize=4``. Steps 6 occasionally malforms fine geometry (e.g. a cannon
muzzle bore); 8 resolves it cleanly; 10 adds only marginal detail for ~25%
more wall-clock, so 8 is the quality/speed sweet spot. quantize 4 is verified
working for z-image-turbo (peak MLX memory ~27 GB at 512x512). On archival
oil-painting prompts z-image-turbo clearly beats schnell (richer patina, wood
grain, believable light) and is far less prone to hallucinating text/signature
watermarks. Cost: it is slower than schnell (~1.6x at 1920x1088: 108s vs 68s).

Fallback: FLUX.1-``schnell`` at ``steps=4`` (faster, lower quality). Select it
without code changes via ``PTV_IMAGE_MODEL=schnell`` (config uses env_prefix
``PTV_``).

CLI entrypoint per model (regression note) — ``mflux-generate`` is the FLUX-ONLY
generator; it loads any ``--model`` through the FLUX weight definition. Passing a
non-FLUX model (z-image-turbo, ...) to it fails in ~1s with
``FileNotFoundError: No safetensors files found in .../text_encoder_2`` (a FLUX
component Z-Image lacks). This bit us once: the z-image-turbo default was DOA
because generate_image hardcoded ``mflux-generate``. Fix: ``_entrypoint_for()``
routes each model to the CLI matching its architecture — the FLUX family
(schnell/dev/krea-dev/dev-krea) uses ``mflux-generate``; others use their
dedicated ``mflux-generate-<model>`` script (e.g. ``mflux-generate-z-image-turbo``).
Mappings are explicit and verified against the installed console scripts; an
unmapped model fails hard rather than guessing an entrypoint.
"""

import os
import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from .models import ImageGenResult

console = Console()

# mflux ships one generic FLUX generator plus one dedicated console script per
# non-FLUX architecture. The generic ``mflux-generate`` loads ANY --model through
# the FLUX weight definition, so a non-FLUX model routed to it dies looking for
# FLUX-only components (e.g. text_encoder_2). Route by architecture instead.
# These mappings are verified against the scripts mflux 0.18.0 installs — keep
# them explicit; never string-template an unverified ``mflux-generate-<model>``.
_FLUX_FAMILY = frozenset({"schnell", "dev", "krea-dev", "dev-krea"})
_DEDICATED_ENTRYPOINTS = {
    "z-image": "mflux-generate-z-image",
    "z-image-turbo": "mflux-generate-z-image-turbo",
}


def _discover_entrypoints() -> list[str]:
    """Names of installed ``mflux-generate*`` console scripts (for error messages)."""
    base = shutil.which("mflux-generate")
    if not base:
        return []
    return sorted(p.name for p in Path(base).parent.glob("mflux-generate*") if p.is_file())


def _entrypoint_for(model: str) -> str:
    """Return the mflux CLI whose architecture matches ``model``.

    FLUX-family models use the generic ``mflux-generate``; others use their
    dedicated ``mflux-generate-<model>`` script. An unmapped model fails hard
    (ValueError) rather than guessing an entrypoint — mislabelled routing wastes
    a full model load and dies with a cryptic missing-weights error.
    """
    if model in _FLUX_FAMILY:
        return "mflux-generate"
    if model in _DEDICATED_ENTRYPOINTS:
        return _DEDICATED_ENTRYPOINTS[model]
    supported = sorted(_FLUX_FAMILY) + sorted(_DEDICATED_ENTRYPOINTS)
    discovered = _discover_entrypoints()
    raise ValueError(
        f"No mflux entrypoint mapping for image model {model!r}. "
        f"Supported models: {', '.join(supported)}. "
        f"Installed mflux generators: {', '.join(discovered) or '(none found)'}. "
        f"Add the model to _DEDICATED_ENTRYPOINTS, or set PTV_IMAGE_MODEL to a supported model."
    )


def generate_image(
    prompt: str,
    output_path: Path,
    segment_id: str = "",
    width: int = 1920,
    height: int = 1080,
    steps: int = 4,
    model: str = "schnell",
    quantize: int = 4,
    seed: int | None = None,
    timeout: int = 900,
    models_dir: str = "",
) -> ImageGenResult:
    """Generate a single still image with mflux and write it to output_path.

    Returns an ImageGenResult. Sets success=False (with error_message) on
    subprocess failure, timeout, or if the PNG is missing/empty afterward.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    entrypoint = _entrypoint_for(model)  # raises ValueError on an unmapped model
    if shutil.which(entrypoint) is None:
        discovered = _discover_entrypoints()
        raise RuntimeError(
            f"mflux entrypoint {entrypoint!r} (for model {model!r}) is not installed. "
            f"Installed mflux generators: {', '.join(discovered) or '(none found)'}. "
            f"Reinstall/upgrade mflux, or set PTV_IMAGE_MODEL to a supported model."
        )

    cmd = [
        entrypoint,
        "--model", model,
        "--prompt", prompt,
        "--width", str(width),
        "--height", str(height),
        "--steps", str(steps),
        "--quantize", str(quantize),
        "--output", str(output_path),
    ]
    if seed is not None:
        cmd += ["--seed", str(seed)]

    console.print(f"[blue]mflux {segment_id or output_path.stem}: {model} {width}x{height} ({steps} steps)[/blue]")
    console.print(f"  [dim]{prompt[:90]}{'…' if len(prompt) > 90 else ''}[/dim]")

    # Route the (multi-GB) HuggingFace hub download cache to an external drive if set.
    env = dict(os.environ)
    if models_dir:
        env["HF_HUB_CACHE"] = str(Path(models_dir).expanduser())

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return ImageGenResult(
            segment_id=segment_id, image_path=output_path, success=False,
            width=width, height=height, seed=seed,
            error_message=f"mflux timed out after {timeout}s (first run downloads a multi-GB model)",
        )

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        return ImageGenResult(
            segment_id=segment_id, image_path=output_path, success=False,
            width=width, height=height, seed=seed,
            error_message=f"mflux exited {proc.returncode}: {tail}",
        )

    # mflux appends a numeric suffix to --output (e.g. foo.png -> foo_1.png).
    # Move the produced file to the exact requested path.
    if not output_path.exists() or output_path.stat().st_size < 50_000:
        suffix = output_path.suffix
        stem = output_path.stem
        candidates = sorted(
            output_path.parent.glob(f"{stem}_*{suffix}"),
            key=lambda p: p.stat().st_mtime,
        )
        if candidates:
            produced = candidates[-1]
            produced.replace(output_path)

    # Verify a real image landed (mflux can exit 0 yet write nothing under OOM).
    if not output_path.exists() or output_path.stat().st_size < 50_000:
        return ImageGenResult(
            segment_id=segment_id, image_path=output_path, success=False,
            width=width, height=height, seed=seed,
            error_message="mflux exited 0 but produced no usable image (likely out of memory)",
        )

    console.print(f"[green]  -> {output_path} ({output_path.stat().st_size // 1024} KB)[/green]")
    return ImageGenResult(
        segment_id=segment_id, image_path=output_path, success=True,
        width=width, height=height, seed=seed,
    )
