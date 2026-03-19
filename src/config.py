from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Literal


class PipelineConfig(BaseSettings):
    # API Keys
    elevenlabs_api_key: str = ""

    # Voice Settings
    voice_id: str | None = None
    voice_stability: float = 0.5
    voice_similarity_boost: float = 0.75
    voice_style: float = 0.0
    elevenlabs_model: str = "eleven_v3"
    voice_use_speaker_boost: bool = True
    voice_speed: float = 1.1  # 0.7-1.2, >1.0 = faster pacing

    # Video Settings
    video_quality: Literal["low", "medium", "high", "4k"] = "high"
    frame_rate: int = 30
    background_color: str = "#1a1a2e"

    # Pipeline Settings
    max_render_attempts: int = 5
    render_timeout_seconds: int = 120
    target_video_duration_minutes: int = 10

    # Paths
    output_dir: Path = Path("output")
    temp_dir: Path = Path("/tmp/paper-to-video")
    voice_samples_dir: Path = Path("voice_samples")

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PTV_")

    @property
    def manim_quality_flag(self) -> str:
        return {"low": "-ql", "medium": "-qm", "high": "-qh", "4k": "-qk"}[
            self.video_quality
        ]

    @property
    def resolution(self) -> tuple[int, int]:
        if self.video_quality == "4k":
            return (3840, 2160)
        return (1920, 1080)
