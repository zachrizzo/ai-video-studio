"""Paper analysis utilities.

Claude Code generates the analysis directly and saves it as JSON.
This module validates and loads that output.
"""

import json
from pathlib import Path
from .models import PaperAnalysis


def load_analysis(json_path: Path) -> PaperAnalysis:
    """Load a paper analysis from a JSON file written by Claude."""
    data = json.loads(json_path.read_text())
    return PaperAnalysis(**data)


def save_analysis(analysis: PaperAnalysis, json_path: Path) -> None:
    """Save a paper analysis to JSON."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(analysis.model_dump_json(indent=2))
