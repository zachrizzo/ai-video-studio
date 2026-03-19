from pathlib import Path
from pydub import AudioSegment


def get_audio_duration(audio_path: Path) -> float:
    """Get the duration of an audio file in seconds."""
    audio = AudioSegment.from_file(str(audio_path))
    return len(audio) / 1000.0


def format_duration(seconds: float) -> str:
    """Format seconds as MM:SS."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"
