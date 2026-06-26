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
    from diffusers import LTXConditionPipeline

    console.print(f"[blue]Loading LTX pipeline: {model_id} (first run downloads weights)…[/blue]")
    pipe = LTXConditionPipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16)
    pipe.to("mps")
    pipe.vae.enable_tiling()
    _PIPE = pipe
    _PIPE_KEY = key
    return pipe


def _finalize_trim(clip: Path, output_path: Path, duration_seconds: float,
                   resolution: tuple[int, int], fps: int) -> dict:
    """Scale/crop a clip to the target res and trim to the exact duration.
    Used when the AI clip is already long enough — preserves continuous motion
    (no boomerang), which looks far less static."""
    w, h = resolution
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},fps={fps},trim=duration={duration_seconds:.3f},setpts=PTS-STARTPTS"
    )
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(clip), "-vf", vf, "-r", str(fps),
         "-c:v", "libx264", "-preset", "fast", "-crf", "18",
         "-pix_fmt", "yuv420p", "-an", str(output_path)],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0 or not output_path.exists():
        return {"success": False, "error_message": f"finalize failed: {proc.stderr[-400:]}"}
    return {"success": True}


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
        from diffusers.pipelines.ltx.pipeline_ltx_condition import LTXVideoCondition

        pipe = _load_pipe(model_id, models_dir)

        gw = _round_to(gen_width, 32)
        gh = _round_to(gen_height, 32)
        gen_fps = 24
        # Generate enough frames to cover the whole target duration when feasible
        # (continuous motion). Cap to stay stable on MPS; beyond it we loop.
        MAX_FRAMES = 161  # ~6.7s at 24fps
        want = max(int(duration_seconds * gen_fps), int(clip_seconds * gen_fps))
        num_frames = min(_round_to(want, 8) + 1, MAX_FRAMES)
        covers = (num_frames / gen_fps) >= duration_seconds - 0.05

        console.print(f"[blue]LTX i2v: {image_path.stem} {gw}x{gh} {num_frames}f {steps} steps"
                      f" ({'direct' if covers else 'loop'})[/blue]")
        image = load_image(str(image_path))
        # Image conditioning on the first frame; strength<1.0 frees the model to
        # add motion instead of freezing on the still.
        condition = LTXVideoCondition(image=image, frame_index=0, strength=1.0)
        motion_prompt = (prompt + ", dynamic motion, cinematic").strip(", ")
        negative = "static, frozen, still image, no motion, worst quality, inconsistent motion, blurry, jittery, distorted"
        common = dict(
            conditions=[condition], prompt=motion_prompt, negative_prompt=negative,
            width=gw, height=gh, num_frames=num_frames,
            decode_timestep=0.05, decode_noise_scale=0.025, image_cond_noise_scale=0.0,
            generator=torch.Generator(device="cpu").manual_seed(0),
        )
        if "distilled" in model_id:
            # Distilled: guidance-distilled (guidance 1.0) with the documented
            # custom timesteps; keeps the clip stable + moving.
            result = pipe(
                **common, guidance_scale=1.0, guidance_rescale=0.7,
                timesteps=[1000, 993, 987, 981, 975, 909, 725, 0.03],
            )
        else:
            result = pipe(
                **common, guidance_scale=5.0, guidance_rescale=0.7,
                num_inference_steps=steps,
            )
        frames = result.frames[0]

        with tempfile.TemporaryDirectory() as td:
            short = Path(td) / "ltx_short.mp4"
            export_to_video(frames, str(short), fps=gen_fps)
            # Continuous motion when the clip covers the duration; otherwise loop.
            if covers:
                ext = _finalize_trim(short, output_path, duration_seconds, resolution, fps)
            else:
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
