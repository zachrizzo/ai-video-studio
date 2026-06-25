"""FastAPI server for the Video Studio backend.

Exposes:
- GET /api/runs
- GET /api/runs/{run_id}
- Static files at /media → RUNS_ROOT  (with HTTP Range support for video)
- WebSocket at /ws/chat

Run via:
  uv run uvicorn src.studio.server:app --port 8787
  python -m src.studio.server
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from src.studio.runs import _runs_root, get_run, list_runs
from src.studio.agent import handle_ws

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


# ---------------------------------------------------------------------------
# REST routes
# ---------------------------------------------------------------------------


@app.get("/api/runs")
async def api_list_runs() -> dict:
    """List all runs with summary metadata."""
    return {"runs": list_runs()}


@app.get("/api/runs/{run_id}")
async def api_get_run(run_id: str) -> dict:
    """Return full manifest for a single run."""
    manifest = get_run(run_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return manifest


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
