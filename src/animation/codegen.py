"""Visual code management.

Claude Code generates Manim/HTML code directly and saves it.
This module handles loading code into SceneSpec models.
"""

from pathlib import Path
from .models import SceneSpec


def load_scene_spec(json_path: Path) -> SceneSpec:
    """Load a scene spec from JSON."""
    import json
    data = json.loads(json_path.read_text())
    return SceneSpec(**data)


def save_scene_spec(spec: SceneSpec, json_path: Path) -> None:
    """Save a scene spec to JSON."""
    import json
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(spec.model_dump_json(indent=2))


def create_scene_spec(
    segment_id: str,
    visual_engine: str,
    code: str,
    target_duration_seconds: float,
    narration_text: str,
    description: str,
) -> SceneSpec:
    """Create a SceneSpec from Claude-generated code."""
    # Strip markdown fences if present
    clean_code = code.strip()
    if clean_code.startswith("```"):
        clean_code = "\n".join(clean_code.split("\n")[1:])
        if clean_code.endswith("```"):
            clean_code = clean_code[:-3]

    return SceneSpec(
        segment_id=segment_id,
        visual_engine=visual_engine,
        code=clean_code.strip(),
        target_duration_seconds=target_duration_seconds,
        narration_text=narration_text,
        description=description,
    )
