"""True AI image-to-video via LTX-Video (diffusers + Apple MPS), headless.

AI video models produce short clips (~2-5s). Scene segments are much longer
(matched to narration), so we generate one real AI clip and extend it to the
exact target duration with a seamless boomerang loop via ffmpeg.
"""

import subprocess
import tempfile
from pathlib import Path

from rich.console import Console

console = Console()

# Module-level pipeline cache so a videogen run loads the model only once.
_PIPE = None
_PIPE_KEY: tuple | None = None


def _round_to(n: int, mult: int) -> int:
    return max(mult, (n // mult) * mult)


def _set_cache(models_dir: str) -> None:
    """Point the HF hub cache at an external drive. MUST run before importing
    diffusers/huggingface_hub, which read this env var at import time."""
    if models_dir:
        import os
        os.environ["HF_HUB_CACHE"] = str(Path(models_dir).expanduser())


def _load_pipe(model_id: str, models_dir: str):
    global _PIPE, _PIPE_KEY
    key = (model_id,)
    if _PIPE is not None and _PIPE_KEY == key:
        return _PIPE
    _set_cache(models_dir)
    import torch
    from diffusers import LTXImageToVideoPipeline

    console.print(f"[blue]Loading LTX pipeline: {model_id} (first run downloads weights)…[/blue]")
    pipe = LTXImageToVideoPipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16)
    pipe.to("mps")
    _PIPE = pipe
    _PIPE_KEY = key
    return pipe


def _extend_to_duration(clip: Path, output_path: Path, duration_seconds: float,
                        resolution: tuple[int, int], fps: int) -> dict:
    """Make a seamless boomerang from a short clip, then loop it to the exact
    target duration and upscale. Two passes: boomerang, then -stream_loop."""
    w, h = resolution
    with tempfile.TemporaryDirectory() as td:
        boom = Path(td) / "boom.mp4"
        # Pass 1: scale/crop to target, build forward+reversed boomerang.
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setpts=PTS-STARTPTS,fps={fps}"
        )
        fc = (
            f"[0:v]{vf},split[a][b];"
            f"[b]reverse[r];"
            f"[a][r]concat=n=2:v=1[out]"
        )
        p1 = subprocess.run(
            ["ffmpeg", "-y", "-i", str(clip), "-filter_complex", fc, "-map", "[out]",
             "-r", str(fps), "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-an", str(boom)],
            capture_output=True, text=True, timeout=300,
        )
        if p1.returncode != 0 or not boom.exists():
            return {"success": False, "error_message": f"boomerang failed: {p1.stderr[-400:]}"}

        # Pass 2: loop the boomerang to the exact duration.
        p2 = subprocess.run(
            ["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(boom),
             "-t", f"{duration_seconds:.3f}", "-r", str(fps),
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-an", str(output_path)],
            capture_output=True, text=True, timeout=300,
        )
        if p2.returncode != 0 or not output_path.exists():
            return {"success": False, "error_message": f"loop failed: {p2.stderr[-400:]}"}
    return {"success": True}


def generate_ltx_clip(
    image_path: Path,
    output_path: Path,
    duration_seconds: float,
    prompt: str = "",
    resolution: tuple[int, int] = (1920, 1080),
    fps: int = 30,
    model_id: str = "Lightricks/LTX-Video",
    steps: int = 25,
    gen_width: int = 768,
    gen_height: int = 512,
    clip_seconds: float = 4.0,
    models_dir: str = "",
) -> dict:
    """Generate a real AI image-to-video clip, extended to duration_seconds.

    Returns {success, video_path, duration, error_message}.
    """
    image_path = Path(image_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Set the cache location BEFORE any diffusers/hf import below.
    _set_cache(models_dir)

    try:
        import torch
        from diffusers.utils import export_to_video, load_image

        pipe = _load_pipe(model_id, models_dir)

        gw = _round_to(gen_width, 32)
        gh = _round_to(gen_height, 32)
        # LTX wants num_frames = 8k + 1
        gen_fps = 24
        raw = int(clip_seconds * gen_fps)
        num_frames = _round_to(raw, 8) + 1

        console.print(f"[blue]LTX i2v: {image_path.stem} {gw}x{gh} {num_frames}f {steps} steps[/blue]")
        image = load_image(str(image_path))
        motion_prompt = (prompt + ", subtle natural motion, cinematic").strip(", ")
        result = pipe(
            image=image,
            prompt=motion_prompt,
            negative_prompt="worst quality, blurry, distorted, jittery, static",
            width=gw, height=gh, num_frames=num_frames,
            num_inference_steps=steps,
            generator=torch.Generator(device="cpu").manual_seed(0),
        )
        frames = result.frames[0]

        with tempfile.TemporaryDirectory() as td:
            short = Path(td) / "ltx_short.mp4"
            export_to_video(frames, str(short), fps=gen_fps)
            ext = _extend_to_duration(short, output_path, duration_seconds, resolution, fps)
            if not ext["success"]:
                return {"success": False, "video_path": str(output_path),
                        "duration": None, "error_message": ext["error_message"]}

        from .kenburns import _ffprobe_duration
        actual = _ffprobe_duration(output_path)
        console.print(f"[green]  -> {output_path} ({actual:.2f}s, real AI motion)[/green]")
        return {"success": True, "video_path": str(output_path), "duration": actual,
                "error_message": None}

    except Exception as e:  # noqa: BLE001
        return {"success": False, "video_path": str(output_path), "duration": None,
                "error_message": f"{type(e).__name__}: {e}"}
