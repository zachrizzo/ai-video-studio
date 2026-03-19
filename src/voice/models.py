from pydantic import BaseModel
from pathlib import Path


class AudioSegment(BaseModel):
    segment_id: str
    audio_path: Path
    duration_seconds: float
    narration_text: str
