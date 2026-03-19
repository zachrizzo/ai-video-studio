from pydantic import BaseModel
from pathlib import Path
from typing import Any, Literal


class SceneSpec(BaseModel):
    segment_id: str
    visual_engine: Literal["manim", "html"]
    code: str  # generated Python (manim) or HTML code
    target_duration_seconds: float
    narration_text: str  # for context
    description: str
    animation_cues: list[dict[str, Any]] = []  # timestamp-synced cues from script


class RenderResult(BaseModel):
    segment_id: str
    video_path: Path
    actual_duration_seconds: float
    visual_engine: Literal["manim", "html"]
    success: bool
    error_message: str | None = None
    attempts: int = 1
