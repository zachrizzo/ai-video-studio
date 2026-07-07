"""Single-shot image and video generation for the Generate tab.

Runs generation in a background thread and stores results in a
generations directory. The frontend polls GET /api/generate/{id}
for status updates.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path

from src.config import PipelineConfig

logger = logging.getLogger(__name__)

_GENS_DIR = Path("/tmp/video-studio-generations")
_GENS_DIR.mkdir(parents=True, exist_ok=True)


def _gen_path(gen_id: str) -> Path:
    return _GENS_DIR / gen_id


def _write_status(gen_id: str, data: dict) -> None:
    p = _gen_path(gen_id)
    p.mkdir(parents=True, exist_ok=True)
    (p / "status.json").write_text(json.dumps(data))


def get_generation(gen_id: str) -> dict | None:
    status_file = _gen_path(gen_id) / "status.json"
    if not status_file.exists():
        return None
    return json.loads(status_file.read_text())


def list_generations() -> list[dict]:
    gens = []
    for d in _GENS_DIR.iterdir():
        sf = d / "status.json"
        if sf.exists():
            try:
                data = json.loads(sf.read_text())
                gens.append(data)
            except Exception:
                pass
    gens.sort(key=lambda data: data.get("created_at", ""), reverse=True)
    return gens[:50]  # last 50


# ---------------------------------------------------------------------------
# Delete / Stop
# ---------------------------------------------------------------------------

def delete_generation(gen_id: str) -> bool:
    """Delete a generation directory and all its files."""
    p = _gen_path(gen_id)
    if not p.exists():
        return False
    shutil.rmtree(p, ignore_errors=True)
    return True


def stop_generation(gen_id: str) -> bool:
    """Stop a running generation by killing its subprocess, then delete it."""
    import signal
    p = _gen_path(gen_id)
    if not p.exists():
        return False
    # Kill any mflux or ltx-2-mlx subprocesses for this gen
    import subprocess as sp
    try:
        # Find processes whose command line contains the gen_id directory
        result = sp.run(
            ["pgrep", "-f", gen_id],
            capture_output=True, text=True, timeout=5,
        )
        for pid_str in result.stdout.strip().split("\n"):
            if pid_str.strip():
                try:
                    __import__("os").kill(int(pid_str.strip()), signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass
    except Exception:
        pass
    # Also try to kill mflux/ltx processes spawned from the gen output dir
    try:
        result = sp.run(
            ["pgrep", "-f", str(p)],
            capture_output=True, text=True, timeout=5,
        )
        for pid_str in result.stdout.strip().split("\n"):
            if pid_str.strip():
                try:
                    __import__("os").kill(int(pid_str.strip()), signal.SIGTERM)
                except (ProcessLookupError, ValueError):
                    pass
    except Exception:
        pass
    shutil.rmtree(p, ignore_errors=True)
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_gen(gen_type: str) -> tuple[str, Path]:
    gen_id = uuid.uuid4().hex[:12]
    out_dir = _gen_path(gen_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    return gen_id, out_dir


def _save_upload(src_path: str | None, out_dir: Path, name: str) -> Path | None:
    """Copy an uploaded file into the generation directory."""
    if not src_path:
        return None
    src = Path(src_path)
    if not src.exists():
        return None
    dst = out_dir / f"{name}{src.suffix}"
    shutil.copy2(src, dst)
    return dst


_LTX_MLX_DIR = Path("/Volumes/4TB-Z/programming/ltx-2-mlx")


def _cfg() -> PipelineConfig:
    return PipelineConfig()


def _run_ltx_mlx(cmd_args: list[str], models_dir: str = "") -> dict:
    """Run an ltx-2-mlx CLI command as a subprocess. Returns {success, error}."""
    import subprocess as sp
    env = dict(__import__("os").environ)
    if models_dir:
        env["HF_HUB_CACHE"] = str(Path(models_dir).expanduser())
    try:
        proc = sp.run(
            ["uv", "run", "ltx-2-mlx"] + cmd_args,
            capture_output=True, text=True, timeout=3600,
            cwd=str(_LTX_MLX_DIR), env=env,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-500:]
            return {"success": False, "error": f"ltx-2-mlx exited {proc.returncode}: {tail}"}
        return {"success": True, "error": None}
    except sp.TimeoutExpired:
        return {"success": False, "error": "ltx-2-mlx timed out after 3600s"}


def _get_ltx_client():
    from src.studio.ltx_api import LTXClient
    cfg = _cfg()
    if not cfg.ltx_api_key:
        raise ValueError("PTV_LTX_API_KEY env var not set")
    return LTXClient(api_key=cfg.ltx_api_key)


# ---------------------------------------------------------------------------
# Original image generation (unchanged)
# ---------------------------------------------------------------------------

def _run_image_gen(gen_id: str, prompt: str, cfg: PipelineConfig) -> None:
    """Generate a single image in a background thread."""
    try:
        _write_status(gen_id, {
            "id": gen_id, "type": "image", "status": "generating",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "error": None,
        })
        out_dir = _gen_path(gen_id)
        out_file = out_dir / "output.png"

        from src.imagegen.flux import generate_image
        result = generate_image(
            prompt=prompt,
            output_path=out_file,
            model=cfg.image_model,
            steps=cfg.image_steps,
            quantize=cfg.image_quantize,
            models_dir=cfg.models_dir,
            timeout=cfg.image_timeout_seconds,
        )
        if result.success:
            _write_status(gen_id, {
                "id": gen_id, "type": "image", "status": "done",
                "prompt": prompt, "created_at": time.time(),
                "output_url": f"/generations/{gen_id}/output.png",
                "error": None,
            })
        else:
            _write_status(gen_id, {
                "id": gen_id, "type": "image", "status": "failed",
                "prompt": prompt, "created_at": time.time(),
                "output_url": None,
                "error": result.error_message or "Unknown error",
            })
    except Exception as e:
        _write_status(gen_id, {
            "id": gen_id, "type": "image", "status": "failed",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "error": str(e),
        })


# ---------------------------------------------------------------------------
# Original video generation (unchanged)
# ---------------------------------------------------------------------------

def _run_video_gen(gen_id: str, prompt: str, image_path: str | None,
                   cfg: PipelineConfig) -> None:
    """Generate a single video in a background thread."""
    try:
        _write_status(gen_id, {
            "id": gen_id, "type": "video", "status": "generating",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "thumbnail_url": None, "error": None,
        })
        out_dir = _gen_path(gen_id)
        out_video = out_dir / "output.mp4"

        # If no image provided, generate one first
        src_image = Path(image_path) if image_path else None
        if src_image is None or not src_image.exists():
            src_image = out_dir / "input.png"
            from src.imagegen.flux import generate_image
            img_result = generate_image(
                prompt=prompt,
                output_path=src_image,
                model=cfg.image_model,
                steps=cfg.image_steps,
                quantize=cfg.image_quantize,
                models_dir=cfg.models_dir,
                timeout=cfg.image_timeout_seconds,
            )
            if not img_result.success:
                _write_status(gen_id, {
                    "id": gen_id, "type": "video", "status": "failed",
                    "prompt": prompt, "created_at": time.time(),
                    "output_url": None, "thumbnail_url": None,
                    "error": f"Image gen failed: {img_result.error_message}",
                })
                return

        _write_status(gen_id, {
            "id": gen_id, "type": "video", "status": "generating",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None,
            "thumbnail_url": f"/generations/{gen_id}/input.png" if (out_dir / "input.png").exists() else None,
            "error": None,
        })

        from src.videogen.ltx import generate_ltx_clip
        result = generate_ltx_clip(
            image_path=src_image,
            output_path=out_video,
            duration_seconds=cfg.ltx_clip_seconds,
            prompt=prompt,
            resolution=cfg.resolution,
            fps=cfg.frame_rate,
            model_id=cfg.ltx_model,
            steps=cfg.ltx_steps,
            gen_width=cfg.ltx_gen_width,
            gen_height=cfg.ltx_gen_height,
            clip_seconds=cfg.ltx_clip_seconds,
            models_dir=cfg.models_dir,
            prefer_extend=cfg.ltx_prefer_extend,
            max_frames=cfg.ltx_max_frames,
            anchor_last_frame=cfg.ltx_anchor_last_frame,
            cfg_scale=cfg.ltx_cfg_scale,
            stg_scale=cfg.ltx_stg_scale,
        )
        if result.get("success"):
            _write_status(gen_id, {
                "id": gen_id, "type": "video", "status": "done",
                "prompt": prompt, "created_at": time.time(),
                "output_url": f"/generations/{gen_id}/output.mp4",
                "thumbnail_url": f"/generations/{gen_id}/input.png" if (out_dir / "input.png").exists() else None,
                "error": None,
            })
        else:
            _write_status(gen_id, {
                "id": gen_id, "type": "video", "status": "failed",
                "prompt": prompt, "created_at": time.time(),
                "output_url": None, "thumbnail_url": None,
                "error": result.get("error_message", "Unknown error"),
            })
    except Exception as e:
        _write_status(gen_id, {
            "id": gen_id, "type": "video", "status": "failed",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "thumbnail_url": None,
            "error": str(e),
        })


def start_image_generation(prompt: str) -> str:
    gen_id = uuid.uuid4().hex[:12]
    cfg = PipelineConfig()
    t = threading.Thread(target=_run_image_gen, args=(gen_id, prompt, cfg), daemon=True)
    t.start()
    return gen_id


def start_video_generation(prompt: str, image_path: str | None = None) -> str:
    gen_id = uuid.uuid4().hex[:12]
    cfg = PipelineConfig()
    t = threading.Thread(target=_run_video_gen, args=(gen_id, prompt, image_path, cfg), daemon=True)
    t.start()
    return gen_id


# ---------------------------------------------------------------------------
# New generation modes (cloud + local)
# ---------------------------------------------------------------------------

def _run_text_to_video(gen_id: str, prompt: str, backend: str,
                       model: str, duration: int | None, resolution: str | None,
                       fps: int, camera_motion: str | None,
                       generate_audio: bool) -> None:
    out_dir = _gen_path(gen_id)
    out_file = out_dir / "output.mp4"
    try:
        _write_status(gen_id, {
            "id": gen_id, "type": "text-to-video", "status": "generating",
            "prompt": prompt, "backend": backend, "created_at": time.time(),
            "output_url": None, "error": None,
        })

        if backend == "cloud":
            _write_status(gen_id, {
                "id": gen_id, "type": "text-to-video", "status": "generating",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 10, "progress_step": "Sending to LTX Cloud...",
            })
            client = _get_ltx_client()
            result = client.text_to_video(
                output_path=out_file, prompt=prompt, model=model,
                duration=duration, resolution=resolution, fps=fps,
                camera_motion=camera_motion, generate_audio=generate_audio,
            )
        else:
            # Local: generate image first, then use diffusers i2v pipeline
            cfg = _cfg()
            src_image = out_dir / "input.png"

            _write_status(gen_id, {
                "id": gen_id, "type": "text-to-video", "status": "generating",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 5, "progress_step": "Generating image with FLUX...",
            })

            from src.imagegen.flux import generate_image
            img_result = generate_image(
                prompt=prompt, output_path=src_image,
                model=cfg.image_model, steps=cfg.image_steps,
                quantize=cfg.image_quantize, models_dir=cfg.models_dir,
                timeout=cfg.image_timeout_seconds,
            )
            if not img_result.success:
                _write_status(gen_id, {
                    "id": gen_id, "type": "text-to-video", "status": "failed",
                    "prompt": prompt, "backend": backend, "created_at": time.time(),
                    "output_url": None,
                    "error": f"Image gen failed: {img_result.error_message}",
                    "progress": 0, "progress_step": "Failed",
                })
                return

            _write_status(gen_id, {
                "id": gen_id, "type": "text-to-video", "status": "generating",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 40, "progress_step": "Loading LTX-2.3 video model...",
            })

            from src.videogen.ltx import generate_ltx_clip

            _write_status(gen_id, {
                "id": gen_id, "type": "text-to-video", "status": "generating",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 55, "progress_step": "Generating video frames...",
            })

            result = generate_ltx_clip(
                image_path=src_image, output_path=out_file,
                duration_seconds=duration or cfg.ltx_clip_seconds,
                prompt=prompt, resolution=cfg.resolution, fps=fps or cfg.frame_rate,
                model_id=cfg.ltx_model, steps=cfg.ltx_steps,
                gen_width=cfg.ltx_gen_width, gen_height=cfg.ltx_gen_height,
                clip_seconds=cfg.ltx_clip_seconds, models_dir=cfg.models_dir,
                prefer_extend=cfg.ltx_prefer_extend,
                max_frames=cfg.ltx_max_frames,
                anchor_last_frame=cfg.ltx_anchor_last_frame,
                cfg_scale=cfg.ltx_cfg_scale,
                stg_scale=cfg.ltx_stg_scale,
            )
            result = {"success": result.get("success", False), "output_path": str(out_file),
                       "error": result.get("error_message")}

        if result["success"]:
            _write_status(gen_id, {
                "id": gen_id, "type": "text-to-video", "status": "done",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": f"/generations/{gen_id}/output.mp4",
                "error": None,
                "progress": 100, "progress_step": "Done",
            })
        else:
            _write_status(gen_id, {
                "id": gen_id, "type": "text-to-video", "status": "failed",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": None, "error": result.get("error", "Unknown error"),
                "progress": 0, "progress_step": "Failed",
            })
    except Exception as e:
        _write_status(gen_id, {
            "id": gen_id, "type": "text-to-video", "status": "failed",
            "prompt": prompt, "backend": backend, "created_at": time.time(),
            "output_url": None, "error": str(e),
            "progress": 0, "progress_step": "Failed",
        })


def _run_image_to_video(gen_id: str, image_path: str, prompt: str | None,
                        backend: str, model: str, duration: int | None,
                        resolution: str | None, fps: int,
                        camera_motion: str | None, generate_audio: bool,
                        first_frame: bool | None, last_frame: bool | None) -> None:
    out_dir = _gen_path(gen_id)
    out_file = out_dir / "output.mp4"
    try:
        saved_img = _save_upload(image_path, out_dir, "input_image")
        _write_status(gen_id, {
            "id": gen_id, "type": "image-to-video", "status": "generating",
            "prompt": prompt, "backend": backend, "created_at": time.time(),
            "output_url": None, "error": None,
            "progress": 5, "progress_step": "Starting...",
        })

        if backend == "cloud":
            _write_status(gen_id, {
                "id": gen_id, "type": "image-to-video", "status": "generating",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 10, "progress_step": "Sending to LTX Cloud...",
            })
            # For cloud, image_uri must be an HTTPS URL; assume caller provides one
            client = _get_ltx_client()
            result = client.image_to_video(
                output_path=out_file, image_uri=image_path, prompt=prompt,
                model=model, duration=duration, resolution=resolution,
                fps=fps, camera_motion=camera_motion,
                generate_audio=generate_audio, first_frame=first_frame,
                last_frame=last_frame,
            )
        else:
            # Local: use diffusers i2v pipeline
            cfg = _cfg()
            src = saved_img or Path(image_path)
            _write_status(gen_id, {
                "id": gen_id, "type": "image-to-video", "status": "generating",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 30, "progress_step": "Loading LTX-2.3 video model...",
            })
            from src.videogen.ltx import generate_ltx_clip
            _write_status(gen_id, {
                "id": gen_id, "type": "image-to-video", "status": "generating",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 50, "progress_step": "Generating video frames...",
            })
            lr = generate_ltx_clip(
                image_path=src, output_path=out_file,
                duration_seconds=duration or cfg.ltx_clip_seconds,
                prompt=prompt or "", resolution=cfg.resolution,
                fps=fps or cfg.frame_rate, model_id=cfg.ltx_model,
                steps=cfg.ltx_steps, gen_width=cfg.ltx_gen_width,
                gen_height=cfg.ltx_gen_height, clip_seconds=cfg.ltx_clip_seconds,
                models_dir=cfg.models_dir,
                prefer_extend=cfg.ltx_prefer_extend,
                max_frames=cfg.ltx_max_frames,
                anchor_last_frame=cfg.ltx_anchor_last_frame,
                cfg_scale=cfg.ltx_cfg_scale,
                stg_scale=cfg.ltx_stg_scale,
            )
            result = {"success": lr.get("success", False), "output_path": str(out_file),
                       "error": lr.get("error_message")}

        if result["success"]:
            _write_status(gen_id, {
                "id": gen_id, "type": "image-to-video", "status": "done",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": f"/generations/{gen_id}/output.mp4",
                "error": None,
                "progress": 100, "progress_step": "Done",
            })
        else:
            _write_status(gen_id, {
                "id": gen_id, "type": "image-to-video", "status": "failed",
                "prompt": prompt, "backend": backend, "created_at": time.time(),
                "output_url": None, "error": result.get("error", "Unknown error"),
                "progress": 0, "progress_step": "Failed",
            })
    except Exception as e:
        _write_status(gen_id, {
            "id": gen_id, "type": "image-to-video", "status": "failed",
            "prompt": prompt, "backend": backend, "created_at": time.time(),
            "output_url": None, "error": str(e),
            "progress": 0, "progress_step": "Failed",
        })


def _run_audio_to_video(gen_id: str, audio_uri: str, image_uri: str | None,
                        prompt: str | None, model: str,
                        resolution: str | None,
                        guidance_scale: float | None,
                        backend: str = "local") -> None:
    out_dir = _gen_path(gen_id)
    out_file = out_dir / "output.mp4"
    try:
        saved_audio = _save_upload(audio_uri, out_dir, "input_audio")
        saved_image = _save_upload(image_uri, out_dir, "input_image")
        _write_status(gen_id, {
            "id": gen_id, "type": "audio-to-video", "status": "generating",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "error": None,
            "progress": 10, "progress_step": "Starting audio-to-video...",
        })

        if backend == "local":
            cfg = _cfg()
            audio_path = saved_audio or Path(audio_uri)
            cmd = [
                "a2v",
                "--prompt", prompt or "Generate video matching the audio",
                "--output", str(out_file),
                "--audio", str(audio_path),
                "--frame-rate", "24",
            ]
            if saved_image:
                cmd += ["--image", str(saved_image)]
            _write_status(gen_id, {
                "id": gen_id, "type": "audio-to-video", "status": "generating",
                "prompt": prompt, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 30, "progress_step": "Generating video from audio...",
            })
            result = _run_ltx_mlx(cmd, cfg.models_dir)
        else:
            client = _get_ltx_client()
            result = client.audio_to_video(
                output_path=out_file, audio_uri=audio_uri, image_uri=image_uri,
                prompt=prompt, model=model, resolution=resolution,
                guidance_scale=guidance_scale,
            )

        ok = result["success"] and out_file.exists()
        _write_status(gen_id, {
            "id": gen_id, "type": "audio-to-video",
            "status": "done" if ok else "failed",
            "prompt": prompt, "created_at": time.time(),
            "output_url": f"/generations/{gen_id}/output.mp4" if ok else None,
            "error": result.get("error"),
            "progress": 100 if ok else 0,
            "progress_step": "Done" if ok else "Failed",
        })
    except Exception as e:
        _write_status(gen_id, {
            "id": gen_id, "type": "audio-to-video", "status": "failed",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "error": str(e),
            "progress": 0, "progress_step": "Failed",
        })


def _run_retake_video(gen_id: str, video_uri: str, start_time: float,
                      duration: float, prompt: str, model: str,
                      resolution: str | None, mode: str | None,
                      backend: str = "local") -> None:
    out_dir = _gen_path(gen_id)
    out_file = out_dir / "output.mp4"
    try:
        saved_video = _save_upload(video_uri, out_dir, "input_video")
        _write_status(gen_id, {
            "id": gen_id, "type": "retake", "status": "generating",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "error": None,
            "progress": 10, "progress_step": "Starting retake...",
        })

        if backend == "local":
            cfg = _cfg()
            video_path = saved_video or Path(video_uri)
            # Convert time to latent frame indices (24fps, 8 frames per latent)
            start_frame = max(0, int(start_time * 24 / 8))
            end_frame = start_frame + max(1, int(duration * 24 / 8))
            cmd = [
                "retake",
                "--prompt", prompt,
                "--output", str(out_file),
                "--video", str(video_path),
                "--start", str(start_frame),
                "--end", str(end_frame),
            ]
            _write_status(gen_id, {
                "id": gen_id, "type": "retake", "status": "generating",
                "prompt": prompt, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 30, "progress_step": "Regenerating video section...",
            })
            result = _run_ltx_mlx(cmd, cfg.models_dir)
        else:
            client = _get_ltx_client()
            result = client.retake(
                output_path=out_file, video_uri=video_uri,
                start_time=start_time, duration=duration, prompt=prompt,
                model=model, resolution=resolution, mode=mode,
            )

        ok = result["success"] and out_file.exists()
        _write_status(gen_id, {
            "id": gen_id, "type": "retake",
            "status": "done" if ok else "failed",
            "prompt": prompt, "created_at": time.time(),
            "output_url": f"/generations/{gen_id}/output.mp4" if ok else None,
            "error": result.get("error"),
            "progress": 100 if ok else 0,
            "progress_step": "Done" if ok else "Failed",
        })
    except Exception as e:
        _write_status(gen_id, {
            "id": gen_id, "type": "retake", "status": "failed",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "error": str(e),
            "progress": 0, "progress_step": "Failed",
        })


def _run_extend_video(gen_id: str, video_uri: str, prompt: str, model: str,
                      mode: str, duration: float | None,
                      context: float | None,
                      backend: str = "local") -> None:
    out_dir = _gen_path(gen_id)
    out_file = out_dir / "output.mp4"
    try:
        saved_video = _save_upload(video_uri, out_dir, "input_video")
        _write_status(gen_id, {
            "id": gen_id, "type": "extend", "status": "generating",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "error": None,
            "progress": 10, "progress_step": "Starting extend...",
        })

        if backend == "local":
            cfg = _cfg()
            video_path = saved_video or Path(video_uri)
            # Convert duration seconds to latent frames (24fps / 8 = 3 latent frames per second)
            extend_frames = max(1, int((duration or 5) * 3))
            direction = "after" if "end" in mode.lower() else "before"
            cmd = [
                "extend",
                "--prompt", prompt,
                "--output", str(out_file),
                "--video", str(video_path),
                "--extend-frames", str(extend_frames),
                "--direction", direction,
            ]
            _write_status(gen_id, {
                "id": gen_id, "type": "extend", "status": "generating",
                "prompt": prompt, "created_at": time.time(),
                "output_url": None, "error": None,
                "progress": 30, "progress_step": f"Extending video ({direction})...",
            })
            result = _run_ltx_mlx(cmd, cfg.models_dir)
        else:
            client = _get_ltx_client()
            result = client.extend(
                output_path=out_file, video_uri=video_uri, prompt=prompt,
                model=model, mode=mode, duration=duration, context=context,
            )

        ok = result["success"] and out_file.exists()
        _write_status(gen_id, {
            "id": gen_id, "type": "extend",
            "status": "done" if ok else "failed",
            "prompt": prompt, "created_at": time.time(),
            "output_url": f"/generations/{gen_id}/output.mp4" if ok else None,
            "error": result.get("error"),
            "progress": 100 if ok else 0,
            "progress_step": "Done" if ok else "Failed",
        })
    except Exception as e:
        _write_status(gen_id, {
            "id": gen_id, "type": "extend", "status": "failed",
            "prompt": prompt, "created_at": time.time(),
            "output_url": None, "error": str(e),
            "progress": 0, "progress_step": "Failed",
        })


def _run_video_hdr(gen_id: str, video_uri: str) -> None:
    out_dir = _gen_path(gen_id)
    out_file = out_dir / "output.mp4"
    try:
        _save_upload(video_uri, out_dir, "input_video")
        _write_status(gen_id, {
            "id": gen_id, "type": "video-hdr", "status": "generating",
            "created_at": time.time(),
            "output_url": None, "error": None,
        })
        client = _get_ltx_client()
        result = client.video_to_video_hdr(output_path=out_file, video_uri=video_uri)
        status = "done" if result["success"] else "failed"
        _write_status(gen_id, {
            "id": gen_id, "type": "video-hdr", "status": status,
            "created_at": time.time(),
            "output_url": f"/generations/{gen_id}/output.mp4" if result["success"] else None,
            "error": result.get("error"),
        })
    except Exception as e:
        _write_status(gen_id, {
            "id": gen_id, "type": "video-hdr", "status": "failed",
            "created_at": time.time(),
            "output_url": None, "error": str(e),
        })


# ---------------------------------------------------------------------------
# Public start functions (new modes)
# ---------------------------------------------------------------------------

def start_text_to_video(
    prompt: str,
    backend: str = "cloud",
    model: str = "ltx-2-3-pro",
    duration: int | None = None,
    resolution: str | None = None,
    fps: int = 25,
    camera_motion: str | None = None,
    generate_audio: bool = True,
) -> str:
    gen_id, _ = _new_gen("text-to-video")
    t = threading.Thread(
        target=_run_text_to_video,
        args=(gen_id, prompt, backend, model, duration, resolution, fps,
              camera_motion, generate_audio),
        daemon=True,
    )
    t.start()
    return gen_id


def start_image_to_video(
    image_path: str,
    prompt: str | None = None,
    backend: str = "cloud",
    model: str = "ltx-2-3-pro",
    duration: int | None = None,
    resolution: str | None = None,
    fps: int = 25,
    camera_motion: str | None = None,
    generate_audio: bool = True,
    first_frame: bool | None = None,
    last_frame: bool | None = None,
) -> str:
    gen_id, _ = _new_gen("image-to-video")
    t = threading.Thread(
        target=_run_image_to_video,
        args=(gen_id, image_path, prompt, backend, model, duration, resolution,
              fps, camera_motion, generate_audio, first_frame, last_frame),
        daemon=True,
    )
    t.start()
    return gen_id


def start_audio_to_video(
    audio_uri: str,
    image_uri: str | None = None,
    prompt: str | None = None,
    model: str = "ltx-2-3-pro",
    resolution: str | None = None,
    guidance_scale: float | None = None,
    backend: str = "local",
) -> str:
    gen_id, _ = _new_gen("audio-to-video")
    t = threading.Thread(
        target=_run_audio_to_video,
        args=(gen_id, audio_uri, image_uri, prompt, model, resolution,
              guidance_scale, backend),
        daemon=True,
    )
    t.start()
    return gen_id


def start_retake_video(
    video_uri: str,
    start_time: float,
    duration: float,
    prompt: str,
    model: str = "ltx-2-3-pro",
    resolution: str | None = None,
    mode: str | None = None,
    backend: str = "local",
) -> str:
    gen_id, _ = _new_gen("retake")
    t = threading.Thread(
        target=_run_retake_video,
        args=(gen_id, video_uri, start_time, duration, prompt, model,
              resolution, mode, backend),
        daemon=True,
    )
    t.start()
    return gen_id


def start_extend_video(
    video_uri: str,
    prompt: str,
    model: str = "ltx-2-3-pro",
    mode: str = "from_end",
    duration: float | None = None,
    context: float | None = None,
    backend: str = "local",
) -> str:
    gen_id, _ = _new_gen("extend")
    t = threading.Thread(
        target=_run_extend_video,
        args=(gen_id, video_uri, prompt, model, mode, duration, context, backend),
        daemon=True,
    )
    t.start()
    return gen_id


def start_video_hdr(video_uri: str) -> str:
    gen_id, _ = _new_gen("video-hdr")
    t = threading.Thread(
        target=_run_video_hdr,
        args=(gen_id, video_uri),
        daemon=True,
    )
    t.start()
    return gen_id


# ---------------------------------------------------------------------------
# Text-to-Speech
# ---------------------------------------------------------------------------

def _run_tts(gen_id: str, text: str, speaker: str, language: str,
             instruct: str | None, ref_audio: str | None,
             model_size: str, provider: str | None = None,
             voicebox_profile: str | None = None) -> None:
    out_dir = _gen_path(gen_id)
    out_file = out_dir / "output.wav"
    try:
        cfg = PipelineConfig()
        # provider=None means "use the configured default". Voicebox routes
        # through the Voicebox app (no fallback); anything else uses Qwen3-TTS.
        use_voicebox = provider == "voicebox" or (
            provider is None and cfg.voice_provider == "voicebox"
        )
        engine = "Voicebox" if use_voicebox else "Qwen3-TTS"
        _write_status(gen_id, {
            "id": gen_id, "type": "text-to-speech", "status": "generating",
            "prompt": text, "created_at": time.time(),
            "output_url": None, "error": None,
            "progress": 10, "progress_step": f"Loading {engine}...",
        })
        _write_status(gen_id, {
            "id": gen_id, "type": "text-to-speech", "status": "generating",
            "prompt": text, "created_at": time.time(),
            "output_url": None, "error": None,
            "progress": 30, "progress_step": "Generating speech...",
        })
        if use_voicebox:
            from src.studio.tts_voicebox import generate_speech_voicebox
            result = generate_speech_voicebox(
                text=text, output_path=out_file,
                profile=voicebox_profile or cfg.voicebox_profile,
                language=cfg.voicebox_language, url=cfg.voicebox_url,
            )
        else:
            from src.studio.tts import generate_speech
            result = generate_speech(
                text=text, output_path=out_file, speaker=speaker,
                language=language, instruct=instruct, ref_audio=ref_audio,
                model_size=model_size,
            )
        ok = result["success"] and out_file.exists()
        _write_status(gen_id, {
            "id": gen_id, "type": "text-to-speech",
            "status": "done" if ok else "failed",
            "prompt": text, "created_at": time.time(),
            "output_url": f"/generations/{gen_id}/output.wav" if ok else None,
            "error": result.get("error"),
            "progress": 100 if ok else 0,
            "progress_step": "Done" if ok else "Failed",
        })
    except Exception as e:
        _write_status(gen_id, {
            "id": gen_id, "type": "text-to-speech", "status": "failed",
            "prompt": text, "created_at": time.time(),
            "output_url": None, "error": str(e),
            "progress": 0, "progress_step": "Failed",
        })


def start_tts(
    text: str,
    speaker: str = "serena",
    language: str = "auto",
    instruct: str | None = None,
    ref_audio: str | None = None,
    model_size: str = "0.6B",
    provider: str | None = None,
    voicebox_profile: str | None = None,
) -> str:
    gen_id, _ = _new_gen("text-to-speech")
    t = threading.Thread(
        target=_run_tts,
        args=(gen_id, text, speaker, language, instruct, ref_audio,
              model_size, provider, voicebox_profile),
        daemon=True,
    )
    t.start()
    return gen_id
