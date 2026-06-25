from pathlib import Path

from pydantic import BaseModel


class ImageGenResult(BaseModel):
    """Result of generating a single still image for a segment."""

    segment_id: str
    image_path: Path
    success: bool
    width: int = 1920
    height: int = 1080
    seed: int | None = None
    error_message: str | None = None
