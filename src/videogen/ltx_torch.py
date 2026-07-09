"""LTX image-to-video via diffusers + PyTorch (Windows/Linux, CUDA or CPU).

The cross-platform twin of the ltx-2-mlx CLI backend in ``ltx.py``: it produces
the same short raw clip from a still + motion prompt; all trimming, extension
and looping to the narrated duration stays in ``ltx.py`` (pure ffmpeg, portable).

Model routing: repos named LTX-2* load ``LTX2ImageToVideoPipeline``; older
LTX-Video repos load ``LTXImageToVideoPipeline``. On CUDA the transformer and
text encoder are quantized to NF4 via bitsandbytes when available so LTX fits
consumer VRAM, with model CPU offload for the rest. The loaded pipeline is
cached at module level; the cross-process generation lock serializes GPU work.
"""

import os
from pathlib import Path

from rich.console import Console

console = Console()

_PIPE = None
_PIPE_KEY: tuple | None = None


def _pipeline_class(model_id: str):
    import diffusers

    if "ltx-2" in model_id.lower():
        return diffusers.LTX2ImageToVideoPipeline
    return diffusers.LTXImageToVideoPipeline


def _quantization_config():
    """NF4 quantization for the heavy components when bitsandbytes is present."""
    try:
        import bitsandbytes  # noqa: F401
    except ImportError:
        console.print("[yellow]bitsandbytes not installed; loading LTX unquantized (bf16)[/yellow]")
        return None
    import torch
    from diffusers.quantizers import PipelineQuantizationConfig

    return PipelineQuantizationConfig(
        quant_backend="bitsandbytes_4bit",
        quant_kwargs={
            "load_in_4bit": True,
            "bnb_4bit_quant_type": "nf4",
            "bnb_4bit_compute_dtype": torch.bfloat16,
        },
        components_to_quantize=["transformer", "text_encoder"],
    )


def _load_pipeline(model_id: str, device: str, models_dir: str):
    global _PIPE, _PIPE_KEY
    key = (model_id, device)
    if _PIPE is not None and _PIPE_KEY == key:
        return _PIPE

    import torch

    cls = _pipeline_class(model_id)
    kwargs: dict = {"torch_dtype": torch.bfloat16 if device == "cuda" else torch.float32}
    if models_dir:
        kwargs["cache_dir"] = str(Path(models_dir).expanduser())
    if device == "cuda":
        quant = _quantization_config()
        if quant is not None:
            kwargs["quantization_config"] = quant

    console.print(f"[blue]Loading {model_id} ({cls.__name__}, {device})…[/blue]")
    pipe = cls.from_pretrained(model_id, **kwargs)

    if device == "cuda":
        pipe.enable_model_cpu_offload()
    else:
        pipe.to(device)
    vae = getattr(pipe, "vae", None)
    if vae is not None and hasattr(vae, "enable_tiling"):
        vae.enable_tiling()

    _PIPE, _PIPE_KEY = pipe, key
    return pipe


def generate_short_clip(
    image_path: Path,
    output_path: Path,
    prompt: str,
    width: int,
    height: int,
    num_frames: int,
    frame_rate: int,
    steps: int,
    cfg_scale: float,
    stg_scale: float,
    model_id: str = "diffusers/LTX-2.3-Diffusers",
    models_dir: str = "",
) -> dict:
    """Generate the short raw AI clip. Returns {success, error_message}."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if models_dir:
        os.environ.setdefault("HF_HUB_CACHE", str(Path(models_dir).expanduser()))

    from ..utils.hw import torch_device

    device = torch_device()
    try:
        from diffusers.utils import export_to_video, load_image

        pipe = _load_pipeline(model_id, device, models_dir)
        image = load_image(str(image_path))

        call_kwargs = dict(
            image=image,
            prompt=prompt,
            width=width,
            height=height,
            num_frames=num_frames,
            num_inference_steps=steps,
            guidance_scale=cfg_scale,
        )
        # frame_rate / stg_scale exist on LTX-2 pipelines but not LTX-Video 0.9.
        import inspect

        accepted = inspect.signature(pipe.__call__).parameters
        if "frame_rate" in accepted:
            call_kwargs["frame_rate"] = frame_rate
        if "stg_scale" in accepted:
            call_kwargs["stg_scale"] = stg_scale

        result = pipe(**call_kwargs)
        export_to_video(result.frames[0], str(output_path), fps=frame_rate)
    except Exception as e:  # noqa: BLE001 — report, don't crash the pipeline run
        return {"success": False, "error_message": f"ltx diffusers {type(e).__name__}: {e}"}

    if not output_path.exists() or output_path.stat().st_size == 0:
        return {"success": False, "error_message": "ltx diffusers produced no clip"}
    return {"success": True, "error_message": None}
