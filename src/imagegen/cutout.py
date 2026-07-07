"""Cutout extraction — rembg RGBA subject isolation.

``extract_cutout`` turns a FLUX still into an RGBA layer for the collage
parallax stack. It lazy-imports rembg (heavy, and Apple/Linux-CPU only) so that
importing this module never pulls in onnxruntime.

There is NO silent degradation (CONTRACTS.md §4): ANY failure — rembg import
error, model download failure (the ~170MB weights download lazily on first use
and can be blocked by an offline proxy), or an alpha gate that rejects a mask
covering too little / too much of the frame — returns a STRUCTURED failure with
an actionable error message. ``extract_cutout`` never raises, but it never
produces a degraded cutout either; the caller fails the run.

Model weights are kept under ``{models_dir}/rembg`` via ``U2NET_HOME`` so they
land on the same external drive as the FLUX/HF caches.
"""

from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console

console = Console()


def alpha_fraction(img) -> float:
    """Fraction of pixels whose alpha exceeds 128 (mid-point of 0..255).

    Pure function of an RGBA PIL image — the testable core of the alpha gate.
    Returns 0.0 for an image without an alpha channel.
    """
    if img.mode != "RGBA":
        return 0.0
    alpha = img.getchannel("A")
    total = alpha.width * alpha.height
    if total == 0:
        return 0.0
    # Histogram of the alpha channel; buckets 129..255 are "opaque enough".
    hist = alpha.histogram()
    opaque = sum(hist[129:256])
    return opaque / total


def extract_cutout(
    image_path: Path,
    output_path: Path,
    *,
    model: str = "isnet-general-use",
    alpha_min: float = 0.05,
    alpha_max: float = 0.95,
    models_dir: str = "",
    feather_px: int = 2,
) -> dict:
    """Extract an RGBA cutout from ``image_path`` into ``output_path``.

    Uses rembg (``model``) to matte the subject, gates the resulting alpha
    coverage to ``[alpha_min, alpha_max]``, and feathers the alpha edge with a
    small gaussian blur on SUCCESS.

    Never raises, but never silently degrades. Returns::

        {"success": bool, "method": "rembg",
         "alpha_fraction": float|None, "error": str|None}

    On failure ``success`` is False and ``error`` is an actionable message
    (the observed alpha fraction, or that the rembg model needs an online
    download). No file is written on failure.
    """
    image_path = Path(image_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Keep the ~170MB rembg weights on the big drive alongside the model caches.
    if models_dir:
        os.environ["U2NET_HOME"] = str(Path(models_dir).expanduser() / "rembg")

    try:
        from PIL import Image, ImageFilter
        from rembg import new_session, remove
    except Exception as exc:
        msg = (
            f"rembg import failed ({exc}); install rembg + onnxruntime "
            f"(`uv sync --extra dev`)"
        )
        console.print(f"[red]{msg}[/red]")
        return {"success": False, "method": "rembg", "alpha_fraction": None, "error": msg}

    try:
        session = new_session(model)
        src = Image.open(image_path).convert("RGBA")
        result = remove(src, session=session)
    except Exception as exc:
        msg = (
            f"rembg matting failed for model {model!r} ({exc}); the model weights "
            f"(~170MB) download lazily on first use and need online access — run "
            f"once with network to {os.environ.get('U2NET_HOME', '~/.u2net')}"
        )
        console.print(f"[red]{msg}[/red]")
        return {"success": False, "method": "rembg", "alpha_fraction": None, "error": msg}

    if result.mode != "RGBA":
        result = result.convert("RGBA")

    frac = alpha_fraction(result)
    if not (alpha_min <= frac <= alpha_max):
        msg = (
            f"cutout alpha gate rejected: fraction {frac:.3f} outside "
            f"[{alpha_min}, {alpha_max}] — rembg removed "
            + ("nearly everything" if frac < alpha_min else "nearly nothing")
            + f" for model {model!r}; adjust the prompt (plain background) or the gate"
        )
        console.print(f"[red]{msg}[/red]")
        return {"success": False, "method": "rembg", "alpha_fraction": frac, "error": msg}

    # Feather the alpha edge only (1-2px gaussian blur on the alpha channel).
    if feather_px and feather_px > 0:
        alpha = result.getchannel("A").filter(ImageFilter.GaussianBlur(feather_px))
        result.putalpha(alpha)

    result.save(output_path)
    console.print(f"[green]  cutout -> {output_path.name} (alpha {frac:.3f})[/green]")
    return {"success": True, "method": "rembg", "alpha_fraction": frac, "error": None}
