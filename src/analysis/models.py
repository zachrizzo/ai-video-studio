from pydantic import BaseModel
from typing import Literal


class ConceptClassification(BaseModel):
    name: str
    description: str
    visual_engine: Literal["manim", "html"]
    importance: int  # 1-5, 5 being most important
    prerequisites: list[str] = []


class PaperAnalysis(BaseModel):
    core_contribution: str
    target_audience_level: str  # "beginner", "intermediate", "advanced"
    key_concepts: list[ConceptClassification]
    paper_summary: str
    suggested_video_title: str


class AnimationCue(BaseModel):
    timestamp_hint: str  # "at start", "after 3 seconds", "with narration"
    description: str
    visual_engine: Literal["manim", "html"]
    math_content: str | None = None  # LaTeX if applicable


class ScriptSegment(BaseModel):
    segment_id: str
    section_title: str
    narration_text: str
    estimated_duration_seconds: float
    animation_cues: list[AnimationCue]
    visual_engine: Literal["manim", "html"]
    transition_type: str = "fade"  # "fade", "slide", "none"


class VideoScript(BaseModel):
    title: str
    total_estimated_duration_seconds: float
    segments: list[ScriptSegment]
