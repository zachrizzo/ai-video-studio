"""Image generation with an OS-aware backend.

``generate_image`` here is the entry point every caller should use. It routes
to the backend that runs on the current machine:

- ``mflux`` (MLX CLI) on Apple Silicon — the original backend in ``flux.py``.
- ``diffusers`` (PyTorch, CUDA when available) everywhere else —
  ``torch_backend.py``. Same models, same call contract.

``PTV_IMAGE_PROVIDER`` (auto | mflux | diffusers | none) overrides the choice.
"""

from pathlib import Path

from .models import ImageGenResult


def resolve_image_provider(requested: str = "auto") -> str:
    """Resolve an image provider request to "mflux" or "diffusers"."""
    if requested == "none":
        raise ValueError("image generation is disabled (PTV_IMAGE_PROVIDER=none)")
    if requested in ("mflux", "diffusers"):
        return requested
    from ..utils.hw import is_apple_silicon

    return "mflux" if is_apple_silicon() else "diffusers"


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
    provider: str | None = None,
) -> ImageGenResult:
    """Generate one still image with the platform-appropriate backend."""
    if provider is None:
        from ..config import PipelineConfig

        provider = PipelineConfig().image_provider
    backend = resolve_image_provider(provider)
    if backend == "mflux":
        from .flux import generate_image as impl
    else:
        from .torch_backend import generate_image as impl
    return impl(
        prompt=prompt, output_path=output_path, segment_id=segment_id,
        width=width, height=height, steps=steps, model=model,
        quantize=quantize, seed=seed, timeout=timeout, models_dir=models_dir,
    )
