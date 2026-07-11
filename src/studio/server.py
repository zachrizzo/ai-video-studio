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

import asyncio
import logging
import os
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, UploadFile, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from src.studio import config
from src.studio.runs import _runs_root, get_run, list_runs
from src.studio.producer import (
    _pipeline_steps,
    get_run_production_status,
    start_run_production,
    stop_run_production,
)
from src.studio.agent import handle_ws
from src.studio.presets import list_presets, get_preset, save_preset, delete_preset
from src.studio.style_packs import list_style_packs, load_style_pack
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
# Mount static media (must happen after app creation). The directory is
# resolved once here from STUDIO_RUNS_DIR at import/startup time; changing
# the env var afterward requires a server restart to take effect.
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
_GENS_DIR = config.generations_dir()
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

MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB


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
    api_key: str | None = None

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
    api_key: str | None = None

class AudioToVideoRequest(BaseModel):
    audio_uri: str
    image_uri: str | None = None
    prompt: str | None = None
    model: str = "ltx-2-3-pro"
    resolution: str | None = None
    guidance_scale: float | None = None
    backend: str = "local"
    api_key: str | None = None

class RetakeRequest(BaseModel):
    video_uri: str
    start_time: float
    duration: float
    prompt: str
    model: str = "ltx-2-3-pro"
    resolution: str | None = None
    mode: str | None = None
    backend: str = "local"
    api_key: str | None = None

class ExtendRequest(BaseModel):
    video_uri: str
    prompt: str
    model: str = "ltx-2-3-pro"
    mode: str = "from_end"
    duration: float | None = None
    context: float | None = None
    backend: str = "local"
    api_key: str | None = None

class VideoHDRRequest(BaseModel):
    video_uri: str
    api_key: str | None = None

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
    # Optional fields that api_save_preset persists via req.model_dump(); declare
    # them so custom presets stop silently dropping these on save.
    style_pack: str | None = None
    default_visual_engine: str | None = None
    sfx_style: str | None = None
    tts_provider: str | None = None
    voicebox_profile: str | None = None
    # Generation-quality overrides (undefined = use the pipeline's own default).
    image_model: str | None = None
    image_steps: int | None = None
    image_quantize: int | None = None
    ltx_steps: int | None = None
    ltx_resolution: str | None = None
    ltx_clip_seconds: float | None = None
    ltx_cfg_scale: float | None = None
    ltx_stg_scale: float | None = None
    ltx_prefer_extend: bool | None = None
    video_fallback_to_kenburns: bool | None = None
    kenburns_zoom: float | None = None
    qwen_model_size: str | None = None

class TTSRequest(BaseModel):
    text: str
    speaker: str = "serena"
    language: str = "auto"
    instruct: str | None = None
    ref_audio: str | None = None
    model_size: str = "0.6B"
    # provider=None uses the configured default (PTV_VOICE_PROVIDER); "voicebox"
    # routes through the Voicebox app, "qwen"/anything else uses Qwen3-TTS.
    provider: str | None = None
    voicebox_profile: str | None = None

class ProduceRunRequest(BaseModel):
    mode: str = "full"
    force_video: bool = False
    segment_ids: str = ""
    speed: float | None = None


# ---------------------------------------------------------------------------
# REST routes
# ---------------------------------------------------------------------------


@app.get("/api/runs")
async def api_list_runs() -> dict:
    """List all runs with summary metadata (including their project)."""
    from src.studio import projects as projects_store

    runs = list_runs()
    for run in runs:
        try:
            run["production"] = get_run_production_status(run["id"])
        except Exception:
            pass
        run["project_id"] = projects_store.project_for_run(run["id"])
    return {"runs": runs}


# ---------------------------------------------------------------------------
# Projects — group runs + chat conversations
# ---------------------------------------------------------------------------


class ProjectCreateRequest(BaseModel):
    name: str


class ProjectRenameRequest(BaseModel):
    name: str


class ProjectRunAssignRequest(BaseModel):
    run_id: str


class ProjectConversationUpsertRequest(BaseModel):
    id: str
    title: str | None = None
    claude_session_id: str | None = None


@app.get("/api/projects")
async def api_list_projects() -> dict:
    """All projects with their run ids and conversation records."""
    from src.studio import projects as projects_store

    return {"projects": projects_store.list_projects()}


@app.post("/api/projects")
async def api_create_project(req: ProjectCreateRequest) -> dict:
    from src.studio import projects as projects_store

    try:
        return projects_store.create_project(req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/projects/{project_id}")
async def api_rename_project(project_id: str, req: ProjectRenameRequest) -> dict:
    from src.studio import projects as projects_store

    try:
        if not projects_store.rename_project(project_id, req.name):
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.delete("/api/projects/{project_id}")
async def api_delete_project(project_id: str) -> dict:
    from src.studio import projects as projects_store

    try:
        if not projects_store.delete_project(project_id):
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/projects/{project_id}/runs")
async def api_assign_run_to_project(project_id: str, req: ProjectRunAssignRequest) -> dict:
    from src.studio import projects as projects_store

    if not projects_store.assign_run(req.run_id, project_id):
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return {"ok": True}


@app.post("/api/projects/{project_id}/conversations")
async def api_upsert_project_conversation(
    project_id: str, req: ProjectConversationUpsertRequest
) -> dict:
    from src.studio import projects as projects_store

    record = projects_store.upsert_conversation(
        project_id, req.id, title=req.title, claude_session_id=req.claude_session_id
    )
    if record is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")
    return record


@app.delete("/api/projects/{project_id}/conversations/{conversation_id}")
async def api_delete_project_conversation(project_id: str, conversation_id: str) -> dict:
    from src.studio import projects as projects_store

    if not projects_store.delete_conversation(project_id, conversation_id):
        raise HTTPException(status_code=404, detail="conversation not found in project")
    return {"ok": True}


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
            speed=options.speed,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/{run_id}/produce/stop")
async def api_stop_run_production(run_id: str) -> dict:
    """Stop background production for a run (idempotent no-op if not running).

    stop_run_production can block for several seconds killing the process
    group and joining the producer thread — run it off the event loop so it
    doesn't freeze WS streaming or other concurrent requests.
    """
    try:
        return await asyncio.to_thread(stop_run_production, run_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/pipeline-steps")
async def api_pipeline_steps() -> dict:
    """Ordered production step ids/labels per mode, straight from the producer.

    The UI stepper derives its display from this instead of hardcoding a copy
    of producer._pipeline_steps.
    """
    # _pipeline_steps only builds command args from run_dir (no filesystem
    # access), so a placeholder path is fine for listing ids/labels.
    placeholder = Path(".")
    return {
        "modes": {
            mode: [
                {"id": step, "label": label}
                for step, label, _ in _pipeline_steps(placeholder, mode)
            ]
            for mode in ("full", "videos", "clips")
        }
    }


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
    size = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_SIZE:
                    raise HTTPException(status_code=413, detail="File too large")
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
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
    """Delete a generation and its files. Also kills any running subprocess.

    Runs off the event loop: this does a blocking pgrep + shutil.rmtree that
    would otherwise freeze WS streaming and other concurrent requests.
    """
    from src.studio.generate import delete_generation
    ok = await asyncio.to_thread(delete_generation, gen_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Generation '{gen_id}' not found")
    return {"deleted": True}


@app.post("/api/generate/{gen_id}/stop")
async def api_stop_generation(gen_id: str) -> dict:
    """Stop a running generation and delete it.

    Runs off the event loop: this does two blocking pgrep calls (5s timeout
    each) plus shutil.rmtree that would otherwise freeze WS streaming and
    other concurrent requests.
    """
    from src.studio.generate import stop_generation
    ok = await asyncio.to_thread(stop_generation, gen_id)
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
        api_key=req.api_key,
    )
    return {"id": gen_id}


@app.post("/api/generate/image-to-video")
async def api_image_to_video(req: ImageToVideoRequest) -> dict:
    gen_id = start_image_to_video(
        image_path=req.image_path, prompt=req.prompt, backend=req.backend,
        model=req.model, duration=req.duration, resolution=req.resolution,
        fps=req.fps, camera_motion=req.camera_motion,
        generate_audio=req.generate_audio, first_frame=req.first_frame,
        last_frame=req.last_frame, api_key=req.api_key,
    )
    return {"id": gen_id}


@app.post("/api/generate/audio-to-video")
async def api_audio_to_video(req: AudioToVideoRequest) -> dict:
    gen_id = start_audio_to_video(
        audio_uri=req.audio_uri, image_uri=req.image_uri,
        prompt=req.prompt, model=req.model, resolution=req.resolution,
        guidance_scale=req.guidance_scale, backend=req.backend,
        api_key=req.api_key,
    )
    return {"id": gen_id}


@app.post("/api/generate/retake")
async def api_retake(req: RetakeRequest) -> dict:
    gen_id = start_retake_video(
        video_uri=req.video_uri, start_time=req.start_time,
        duration=req.duration, prompt=req.prompt, model=req.model,
        resolution=req.resolution, mode=req.mode, backend=req.backend,
        api_key=req.api_key,
    )
    return {"id": gen_id}


@app.post("/api/generate/extend")
async def api_extend(req: ExtendRequest) -> dict:
    gen_id = start_extend_video(
        video_uri=req.video_uri, prompt=req.prompt, model=req.model,
        mode=req.mode, duration=req.duration, context=req.context,
        backend=req.backend, api_key=req.api_key,
    )
    return {"id": gen_id}


@app.post("/api/generate/video-hdr")
async def api_video_hdr(req: VideoHDRRequest) -> dict:
    gen_id = start_video_hdr(video_uri=req.video_uri, api_key=req.api_key)
    return {"id": gen_id}


@app.post("/api/generate/tts")
async def api_tts(req: TTSRequest) -> dict:
    gen_id = start_tts(
        text=req.text, speaker=req.speaker, language=req.language,
        instruct=req.instruct, ref_audio=req.ref_audio,
        model_size=req.model_size, provider=req.provider,
        voicebox_profile=req.voicebox_profile,
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


@app.get("/api/style_packs/{name}")
async def api_get_style_pack(name: str) -> dict:
    """Full detail for one style pack — the tokens (palette/type/motion/
    texture) and FLUX prompt prefix/suffix that /api/style_packs' summary
    (palette only) leaves out, so the preset UI can show what a style pack
    actually is instead of just its name."""
    try:
        pack = load_style_pack(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "id": pack.name,
        "name": pack.tokens.get("name", pack.name),
        "description": pack.tokens.get("description", ""),
        "palette": pack.tokens.get("palette", {}),
        "type": pack.tokens.get("type", {}),
        "motion": pack.tokens.get("motion", {}),
        "texture": pack.tokens.get("texture", {}),
        "flux_prefix": pack.flux_prefix,
        "flux_suffix": pack.flux_suffix,
        "fonts": [f.name for f in pack.fonts],
    }


# ---------------------------------------------------------------------------
# Voicebox profiles
# ---------------------------------------------------------------------------


@app.get("/api/voicebox/profiles")
async def api_list_voicebox_profiles() -> dict:
    """List real Voicebox profiles (name/id/default_engine) so the preset UI
    can offer a real dropdown instead of a free-text field the user has to
    get exactly right. Voicebox being unreachable is a normal, expected state
    (the app may not be running) — return an empty list with a message
    rather than a 500, so the UI can show "type a name" as a graceful
    fallback instead of an error."""
    from src.config import PipelineConfig
    from src.studio.tts_voicebox import list_profiles

    cfg = PipelineConfig()
    try:
        profiles = list_profiles(cfg.voicebox_url)
    except RuntimeError as exc:
        return {"profiles": [], "available": False, "message": str(exc)}
    return {
        "profiles": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "default_engine": p.get("default_engine"),
            }
            for p in profiles
        ],
        "available": True,
        "message": None,
    }


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@app.get("/api/capabilities")
async def api_capabilities() -> dict:
    """Which local engines (voicebox, whisper, ffmpeg, mflux, ltx) are available."""
    from src.studio import capabilities

    return capabilities.probe()


# ---------------------------------------------------------------------------
# Chat transcripts
# ---------------------------------------------------------------------------


@app.get("/api/conversations/{conversation_id}/messages")
async def api_conversation_messages(conversation_id: str) -> dict:
    """Server-side transcript for one conversation (empty list is a valid 200)."""
    from src.studio import transcripts

    if not transcripts.is_valid_conversation_id(conversation_id):
        raise HTTPException(status_code=404, detail="invalid conversation id")
    return {"messages": transcripts.load_messages(conversation_id)}


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

    host = os.environ.get("STUDIO_HOST", "127.0.0.1")
    uvicorn.run("src.studio.server:app", host=host, port=8787, reload=False)
