"""FastAPI server for the Video Studio backend.

Exposes:
- GET /api/runs
- GET /api/runs/{run_id}
- Static files at /media -> RUNS_ROOT  (with HTTP Range support for video)
- WebSocket at /ws/chat

Run via:
  uv run uvicorn src.studio.server:app --port 8787
  python -m src.studio.server
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from src.studio.runs import _runs_root, get_run, list_runs
from src.studio.producer import get_run_production_status, start_run_production
from src.studio.agent import handle_ws
from src.studio.presets import list_presets, get_preset, save_preset, delete_preset
from src.studio.style_packs import list_style_packs
from src.studio.generate import (
    get_generation, list_generations,
    start_image_generation, start_video_generation,
    start_text_to_video, start_image_to_video, start_audio_to_video,
    start_retake_video, start_extend_video, start_video_hdr, start_tts,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Video Studio API", version="0.1.0")

# CORS: allow localhost on any port (development)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8787",
        "http://localhost:8787",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Mount static media (must happen after app creation; path is resolved at
# request time so that STUDIO_RUNS_DIR env var changes are honoured).
# ---------------------------------------------------------------------------

def _mount_media() -> None:
    """Mount RUNS_ROOT as /media, creating it if necessary."""
    runs_root = _runs_root()
    runs_root.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/media",
        StaticFiles(directory=str(runs_root), html=False),
        name="media",
    )


_mount_media()

# Mount generations directory for serving generated files
_GENS_DIR = Path("/tmp/video-studio-generations")
_GENS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/generations",
    StaticFiles(directory=str(_GENS_DIR), html=False),
    name="generations",
)

# Mount uploads directory for serving uploaded files
_UPLOADS_DIR = _GENS_DIR / "uploads"
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount(
    "/uploads",
    StaticFiles(directory=str(_UPLOADS_DIR), html=False),
    name="uploads",
)


# ---------------------------------------------------------------------------
# Allowed upload extensions
# ---------------------------------------------------------------------------

ALLOWED_EXTENSIONS = {
    # images
    ".jpg", ".jpeg", ".png", ".webp",
    # video
    ".mp4", ".mov", ".webm",
    # audio
    ".mp3", ".wav", ".m4a", ".aac",
}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ImageGenRequest(BaseModel):
    prompt: str

class VideoGenRequest(BaseModel):
    prompt: str
    image_path: str | None = None

class TextToVideoRequest(BaseModel):
    prompt: str
    backend: str = "cloud"
    model: str = "ltx-2-3-pro"
    duration: int | None = None
    resolution: str | None = None
    fps: int = 25
    camera_motion: str | None = None
    generate_audio: bool = True

class ImageToVideoRequest(BaseModel):
    image_path: str
    prompt: str | None = None
    backend: str = "cloud"
    model: str = "ltx-2-3-pro"
    duration: int | None = None
    resolution: str | None = None
    fps: int = 25
    camera_motion: str | None = None
    generate_audio: bool = True
    first_frame: bool | None = None
    last_frame: bool | None = None

class AudioToVideoRequest(BaseModel):
    audio_uri: str
    image_uri: str | None = None
    prompt: str | None = None
    model: str = "ltx-2-3-pro"
    resolution: str | None = None
    guidance_scale: float | None = None
    backend: str = "local"

class RetakeRequest(BaseModel):
    video_uri: str
    start_time: float
    duration: float
    prompt: str
    model: str = "ltx-2-3-pro"
    resolution: str | None = None
    mode: str | None = None
    backend: str = "local"

class ExtendRequest(BaseModel):
    video_uri: str
    prompt: str
    model: str = "ltx-2-3-pro"
    mode: str = "from_end"
    duration: float | None = None
    context: float | None = None
    backend: str = "local"

class VideoHDRRequest(BaseModel):
    video_uri: str

class PresetSaveRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    style_prompt: str = ""
    video_length_minutes: int = 2
    voice_speaker: str = "serena"
    voice_language: str = "english"
    video_provider: str = "kenburns"
    narration_style: str = ""

class TTSRequest(BaseModel):
    text: str
    speaker: str = "serena"
    language: str = "auto"
    instruct: str | None = None
    ref_audio: str | None = None
    model_size: str = "0.6B"

class ProduceRunRequest(BaseModel):
    mode: str = "full"
    force_video: bool = False
    segment_ids: str = ""


# ---------------------------------------------------------------------------
# REST routes
# ---------------------------------------------------------------------------


@app.get("/api/runs")
async def api_list_runs() -> dict:
    """List all runs with summary metadata."""
    runs = list_runs()
    for run in runs:
        try:
            run["production"] = get_run_production_status(run["id"])
        except Exception:
            pass
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
async def api_get_run(run_id: str) -> dict:
    """Return full manifest for a single run."""
    manifest = get_run(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    try:
        manifest["production"] = get_run_production_status(run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return manifest


@app.get("/api/runs/{run_id}/production")
async def api_get_run_production(run_id: str) -> dict:
    """Return full-run production job status."""
    try:
        return get_run_production_status(run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/produce")
async def api_start_run_production(
    run_id: str,
    req: ProduceRunRequest | None = Body(default=None),
) -> dict:
    """Start or resume deterministic video production for a run."""
    try:
        options = req or ProduceRunRequest()
        return start_run_production(
            run_id,
            mode=options.mode,
            force_video=options.force_video,
            segment_ids=options.segment_ids,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# File upload endpoint
# ---------------------------------------------------------------------------


@app.post("/api/upload")
async def api_upload(file: UploadFile) -> dict:
    """Accept a file upload and save to the uploads directory."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type '{ext}' not allowed")
    file_id = uuid.uuid4().hex[:16]
    dest = _UPLOADS_DIR / f"{file_id}{ext}"
    content = await file.read()
    dest.write_bytes(content)
    return {"path": str(dest), "url": f"/uploads/{file_id}{ext}"}


# ---------------------------------------------------------------------------
# Generate endpoints (single-shot image/video) -- original
# ---------------------------------------------------------------------------


@app.post("/api/generate/image")
async def api_generate_image(req: ImageGenRequest) -> dict:
    gen_id = start_image_generation(req.prompt)
    return {"id": gen_id}


@app.post("/api/generate/video")
async def api_generate_video(req: VideoGenRequest) -> dict:
    gen_id = start_video_generation(req.prompt, req.image_path)
    return {"id": gen_id}


@app.get("/api/generate/{gen_id}")
async def api_get_generation(gen_id: str) -> dict:
    gen = get_generation(gen_id)
    if gen is None:
        raise HTTPException(status_code=404, detail=f"Generation '{gen_id}' not found")
    return gen


@app.get("/api/generations")
async def api_list_generations() -> dict:
    return {"generations": list_generations()}


@app.delete("/api/generate/{gen_id}")
async def api_delete_generation(gen_id: str) -> dict:
    """Delete a generation and its files. Also kills any running subprocess."""
    from src.studio.generate import delete_generation
    ok = delete_generation(gen_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Generation '{gen_id}' not found")
    return {"deleted": True}


@app.post("/api/generate/{gen_id}/stop")
async def api_stop_generation(gen_id: str) -> dict:
    """Stop a running generation and delete it."""
    from src.studio.generate import stop_generation
    ok = stop_generation(gen_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Generation '{gen_id}' not found")
    return {"stopped": True}


# ---------------------------------------------------------------------------
# New generation endpoints (cloud + local)
# ---------------------------------------------------------------------------


@app.post("/api/generate/text-to-video")
async def api_text_to_video(req: TextToVideoRequest) -> dict:
    gen_id = start_text_to_video(
        prompt=req.prompt, backend=req.backend, model=req.model,
        duration=req.duration, resolution=req.resolution, fps=req.fps,
        camera_motion=req.camera_motion, generate_audio=req.generate_audio,
    )
    return {"id": gen_id}


@app.post("/api/generate/image-to-video")
async def api_image_to_video(req: ImageToVideoRequest) -> dict:
    gen_id = start_image_to_video(
        image_path=req.image_path, prompt=req.prompt, backend=req.backend,
        model=req.model, duration=req.duration, resolution=req.resolution,
        fps=req.fps, camera_motion=req.camera_motion,
        generate_audio=req.generate_audio, first_frame=req.first_frame,
        last_frame=req.last_frame,
    )
    return {"id": gen_id}


@app.post("/api/generate/audio-to-video")
async def api_audio_to_video(req: AudioToVideoRequest) -> dict:
    gen_id = start_audio_to_video(
        audio_uri=req.audio_uri, image_uri=req.image_uri,
        prompt=req.prompt, model=req.model, resolution=req.resolution,
        guidance_scale=req.guidance_scale, backend=req.backend,
    )
    return {"id": gen_id}


@app.post("/api/generate/retake")
async def api_retake(req: RetakeRequest) -> dict:
    gen_id = start_retake_video(
        video_uri=req.video_uri, start_time=req.start_time,
        duration=req.duration, prompt=req.prompt, model=req.model,
        resolution=req.resolution, mode=req.mode, backend=req.backend,
    )
    return {"id": gen_id}


@app.post("/api/generate/extend")
async def api_extend(req: ExtendRequest) -> dict:
    gen_id = start_extend_video(
        video_uri=req.video_uri, prompt=req.prompt, model=req.model,
        mode=req.mode, duration=req.duration, context=req.context,
        backend=req.backend,
    )
    return {"id": gen_id}


@app.post("/api/generate/video-hdr")
async def api_video_hdr(req: VideoHDRRequest) -> dict:
    gen_id = start_video_hdr(video_uri=req.video_uri)
    return {"id": gen_id}


@app.post("/api/generate/tts")
async def api_tts(req: TTSRequest) -> dict:
    gen_id = start_tts(
        text=req.text, speaker=req.speaker, language=req.language,
        instruct=req.instruct, ref_audio=req.ref_audio,
        model_size=req.model_size,
    )
    return {"id": gen_id}


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


@app.get("/api/presets")
async def api_list_presets() -> dict:
    return {"presets": list_presets()}


@app.get("/api/presets/{preset_id}")
async def api_get_preset(preset_id: str) -> dict:
    p = get_preset(preset_id)
    if not p:
        raise HTTPException(status_code=404, detail="Preset not found")
    return p


@app.post("/api/presets")
async def api_save_preset(req: PresetSaveRequest) -> dict:
    data = req.model_dump(exclude={"id"})
    return save_preset(req.id, data)


@app.delete("/api/presets/{preset_id}")
async def api_delete_preset(preset_id: str) -> dict:
    ok = delete_preset(preset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Preset not found or is built-in")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Style packs
# ---------------------------------------------------------------------------


@app.get("/api/style_packs")
async def api_list_style_packs() -> dict:
    return {"style_packs": list_style_packs()}


# ---------------------------------------------------------------------------
# WebSocket chat
# ---------------------------------------------------------------------------


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    """WebSocket endpoint for chat with the Claude Agent SDK."""
    await handle_ws(websocket)


# ---------------------------------------------------------------------------
# Entry-point for `python -m src.studio.server`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.studio.server:app", host="0.0.0.0", port=8787, reload=False)
