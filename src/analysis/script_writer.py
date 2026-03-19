"""Video script utilities.

Claude Code generates the narration script directly and saves it as JSON.
This module validates and loads that output.
"""

import json
from pathlib import Path
from .models import VideoScript


def load_script(json_path: Path) -> VideoScript:
    """Load a video script from a JSON file written by Claude."""
    data = json.loads(json_path.read_text())
    return VideoScript(**data)


def save_script(script: VideoScript, json_path: Path) -> None:
    """Save a video script to JSON."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(script.model_dump_json(indent=2))
