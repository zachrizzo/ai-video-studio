"""Local FLUX image generation via mflux (MLX, Apple Silicon).

Runs `mflux-generate` as a subprocess so model memory is freed after each image
and the first-run model download is isolated. No server required.
"""

import os
import subprocess
from pathlib import Path

from rich.console import Console

from .models import ImageGenResult

console = Console()


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

    cmd = [
        "mflux-generate",
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

    console.print(f"[blue]FLUX {segment_id or output_path.stem}: {model} {width}x{height} ({steps} steps)[/blue]")
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
