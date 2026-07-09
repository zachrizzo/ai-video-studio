"""Local image generation via diffusers + PyTorch (Windows/Linux, CUDA or CPU).

The cross-platform twin of ``flux.py`` (mflux/MLX, Apple Silicon): same models,
same call contract, different runtime. Model names map to the HuggingFace repos
that back the mflux aliases, so ``PTV_IMAGE_MODEL=z-image-turbo`` produces the
same generator on every OS.

Memory strategy — consumer GPUs (e.g. 8 GB laptop cards) can't hold these
models in bf16, so ``quantize`` maps to bitsandbytes on CUDA the way mflux's
``--quantize`` maps to MLX quantization: 4 -> NF4, 8 -> int8. Combined with
model CPU offload, z-image-turbo (6B) fits in ~4 GB VRAM. Without CUDA the
pipeline runs unquantized on CPU (slow but functional). The pipeline is cached
at module level so a run loads the model once; the cross-process generation
lock already serializes GPU work.
"""

import os
from pathlib import Path

from rich.console import Console

from .models import ImageGenResult

console = Console()

# mflux model alias -> HuggingFace diffusers repo. A name containing "/" is
# passed through as an explicit repo id. Unmapped aliases fail hard (mirrors
# flux._entrypoint_for) rather than guessing a repo and dying mid-download.
HF_REPOS = {
    "z-image-turbo": "Tongyi-MAI/Z-Image-Turbo",
    "z-image": "Tongyi-MAI/Z-Image",
    "schnell": "black-forest-labs/FLUX.1-schnell",  # gated: needs `hf auth login`
    "dev": "black-forest-labs/FLUX.1-dev",  # gated + non-commercial
}

_PIPE = None
_PIPE_KEY: tuple | None = None


def repo_for(model: str) -> str:
    """Resolve a model alias to its HuggingFace repo id."""
    if "/" in model:
        return model
    if model in HF_REPOS:
        return HF_REPOS[model]
    supported = sorted(HF_REPOS)
    raise ValueError(
        f"No HuggingFace repo mapping for image model {model!r}. "
        f"Supported models: {', '.join(supported)}, or pass a full repo id "
        f"(owner/name). Set PTV_IMAGE_MODEL to a supported model."
    )


def _quantization_config(quantize: int, device: str):
    """bitsandbytes pipeline quantization when it can actually be used."""
    if device != "cuda" or quantize not in (4, 8):
        return None
    try:
        import bitsandbytes  # noqa: F401
    except ImportError:
        console.print("[yellow]bitsandbytes not installed; loading unquantized (bf16)[/yellow]")
        return None
    import torch
    from diffusers.quantizers import PipelineQuantizationConfig

    if quantize == 4:
        kwargs = {
            "load_in_4bit": True,
            "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": torch.bfloat16,
        }
    else:
        kwargs = {"load_in_8bit": True}
    return PipelineQuantizationConfig(
        quant_backend="bitsandbytes_4bit" if quantize == 4 else "bitsandbytes_8bit",
        quant_kwargs=kwargs,
        components_to_quantize=["transformer", "text_encoder"],
    )


def _load_pipeline(repo: str, quantize: int, device: str, models_dir: str):
    global _PIPE, _PIPE_KEY
    key = (repo, quantize, device)
    if _PIPE is not None and _PIPE_KEY == key:
        return _PIPE

    import torch
    from diffusers import DiffusionPipeline

    kwargs: dict = {"torch_dtype": torch.bfloat16 if device == "cuda" else torch.float32}
    if models_dir:
        kwargs["cache_dir"] = str(Path(models_dir).expanduser())
    quant = _quantization_config(quantize, device)
    if quant is not None:
        kwargs["quantization_config"] = quant

    console.print(f"[blue]Loading {repo} (diffusers, {device}, q{quantize if quant else 'off'})…[/blue]")
    pipe = DiffusionPipeline.from_pretrained(repo, **kwargs)

    if device == "cuda":
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(device)
    # Tiled VAE decode keeps 1920x1088 within consumer VRAM.
    for attr in ("vae",):
        vae = getattr(pipe, attr, None)
        if vae is not None and hasattr(vae, "enable_tiling"):
            vae.enable_tiling()

    _PIPE, _PIPE_KEY = pipe, key
    return pipe


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
    timeout: int = 900,  # noqa: ARG001 — in-process generation; kept for call parity
    models_dir: str = "",
) -> ImageGenResult:
    """Generate a single still image with diffusers and write it to output_path.

    Same contract as ``flux.generate_image``: returns an ImageGenResult with
    success=False (and error_message) on any failure.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    repo = repo_for(model)  # raises ValueError on an unmapped model

    if models_dir:
        # Some hub paths read the env var rather than cache_dir.
        os.environ.setdefault("HF_HUB_CACHE", str(Path(models_dir).expanduser()))

    from ..utils.hw import torch_device

    device = torch_device()
    console.print(
        f"[blue]diffusers {segment_id or output_path.stem}: {model} "
        f"{width}x{height} ({steps} steps, {device})[/blue]"
    )
    console.print(f"  [dim]{prompt[:90]}{'…' if len(prompt) > 90 else ''}[/dim]")

    # Diffusion pipelines need dims divisible by 16 (config resolution is
    # 1920x1080): generate at the next multiple, then center-crop back.
    gen_w = ((width + 15) // 16) * 16
    gen_h = ((height + 15) // 16) * 16

    try:
        import torch

        pipe = _load_pipeline(repo, quantize, device, models_dir)
        generator = None
        if seed is not None:
            generator = torch.Generator("cpu").manual_seed(seed)
        result = pipe(
            prompt=prompt,
            width=gen_w,
            height=gen_h,
            num_inference_steps=steps,
            generator=generator,
        )
        image = result.images[0]
        if (gen_w, gen_h) != (width, height):
            left = (gen_w - width) // 2
            top = (gen_h - height) // 2
            image = image.crop((left, top, left + width, top + height))
        image.save(output_path)
    except Exception as e:  # noqa: BLE001 — report, don't crash the pipeline run
        return ImageGenResult(
            segment_id=segment_id, image_path=output_path, success=False,
            width=width, height=height, seed=seed,
            error_message=f"diffusers {type(e).__name__}: {e}",
        )

    # Verify a real image landed (parity with the mflux backend's OOM guard).
    if not output_path.exists() or output_path.stat().st_size < 50_000:
        return ImageGenResult(
            segment_id=segment_id, image_path=output_path, success=False,
            width=width, height=height, seed=seed,
            error_message="diffusers produced no usable image (likely out of memory)",
        )

    console.print(f"[green]  -> {output_path} ({output_path.stat().st_size // 1024} KB)[/green]")
    return ImageGenResult(
        segment_id=segment_id, image_path=output_path, success=True,
        width=width, height=height, seed=seed,
    )
