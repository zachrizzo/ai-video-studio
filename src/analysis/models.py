from pydantic import BaseModel
from typing import Literal


class ConceptClassification(BaseModel):
    name: str
    description: str
    visual_engine: Literal["manim", "html", "collage"]
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
    visual_engine: Literal["manim", "html", "collage"]
    math_content: str | None = None  # LaTeX if applicable


class SfxCue(BaseModel):
    """A sound effect mixed under a segment's narration.

    Timing works like the collage TimeRef: exactly one of at / at_frac /
    at_word (at_word needs `align` to have run). Sounds come from the
    procedural library in src/audio/sfx.py (cannon_boom, musket_volley,
    war_drums, ocean_waves, fire_crackle, wind_howl, bell_toll, ...).
    """

    sound: str
    at: float | None = None
    at_frac: float | None = None
    at_word: str | None = None
    occurrence: int = 1
    offset: float = 0.0
    gain_db: float = -12.0


class VisualBeat(BaseModel):
    beat_id: str | None = None
    description: str | None = None
    shot_type: str | None = None
    composition: str | None = None
    action: str | None = None
    camera_motion: str | None = None
    transition: str | None = None
    continuity_notes: list[str] = []
    asset_notes: list[str] = []
    image_prompt: str | None = None
    duration_seconds: float | None = None
    weight: float = 1.0
    visual_constraints: list[str] = []
    negative_prompt: str | None = None
    production_notes: list[str] = []
    acceptance_criteria: list[str] = []


class ScriptSegment(BaseModel):
    segment_id: str
    section_title: str
    narration_text: str
    estimated_duration_seconds: float
    animation_cues: list[AnimationCue]
    # "collage" renders a declarative CollageSpec (scenes/{segment_id}.collage.json)
    # through the deterministic frame renderer; see docs/collage/CONTRACTS.md.
    visual_engine: Literal["manim", "html", "collage"]
    transition_type: str = "fade"  # "fade", "slide", "none"
    # Routing for the visual: "diagram" keeps the existing HTML/Manim animation
    # path (maps, timelines, stat reveals); "scene" generates a FLUX still that
    # becomes a motion clip. Defaults to "diagram" so existing scripts are unchanged.
    visual_type: Literal["scene", "diagram"] = "diagram"
    image_prompt: str | None = None  # FLUX prompt, required for "scene" segments
    visual_intent: str | None = None
    visual_constraints: list[str] = []
    negative_prompt: str | None = None
    production_notes: list[str] = []
    acceptance_criteria: list[str] = []
    # Optional ordered mini-shots inside a scene segment. When present, the
    # pipeline creates images/clips named {segment_id}_b01, {segment_id}_b02...
    # and allocates the segment narration duration across them.
    visual_beats: list[VisualBeat] = []
    # Optional sound effects mixed under this segment's narration by the
    # `sfx` pipeline command (after synthesize + align).
    sfx: list[SfxCue] = []


class VideoScript(BaseModel):
    title: str
    total_estimated_duration_seconds: float
    segments: list[ScriptSegment]
    subject: str | None = None
    canonical_name: str | None = None
    audience: str | None = None
    style_bible: str | None = None
    narration_style: str | None = None
    historical_constraints: list[str] = []
    visual_continuity_rules: list[str] = []
    forbidden_visuals: list[str] = []
    storyboard_summary: str | None = None
    storyboard_rules: list[str] = []
    negative_prompt: str | None = None
    pronunciation_dictionary: dict[str, str] = {}
    release_acceptance_criteria: list[str] = []
    # Name of a directory under style_packs/ whose tokens/fonts/flux prefixes
    # style every collage scene and FLUX prompt in this script.
    style_pack: str | None = None
