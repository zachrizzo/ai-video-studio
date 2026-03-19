import shutil
import uuid
from pathlib import Path
from rich.console import Console

console = Console()


class FileManager:
    """Manages temporary working directories for pipeline runs."""

    def __init__(self, base_temp_dir: Path):
        self.base_temp_dir = base_temp_dir
        self.run_id = uuid.uuid4().hex[:8]
        self.run_dir = base_temp_dir / f"run_{self.run_id}"

    def setup(self) -> Path:
        """Create the run directory and subdirectories."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "audio").mkdir(exist_ok=True)
        (self.run_dir / "video").mkdir(exist_ok=True)
        (self.run_dir / "scenes").mkdir(exist_ok=True)
        console.print(f"[dim]Working directory: {self.run_dir}[/dim]")
        return self.run_dir

    @property
    def audio_dir(self) -> Path:
        return self.run_dir / "audio"

    @property
    def video_dir(self) -> Path:
        return self.run_dir / "video"

    @property
    def scenes_dir(self) -> Path:
        return self.run_dir / "scenes"

    def cleanup(self) -> None:
        """Remove the run directory."""
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir)
            console.print(f"[dim]Cleaned up: {self.run_dir}[/dim]")
