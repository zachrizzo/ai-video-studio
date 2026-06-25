import subprocess
import json
from pathlib import Path
from rich.console import Console

console = Console()


def resolve_segment_video(run_dir: Path, segment_id: str) -> Path | None:
    """Return the best available video for a segment, in priority order:

    1. clips/{id}.mp4          (AI/motion clip for "scene" segments)
    2. scenes/{id}_render/{id}_(html|manim).mp4   (rendered diagram animation)
    3. scenes/{id}_render/{id}_fallback.mp4       (fallback title card)
    else None.
    """
    run_dir = Path(run_dir)
    clip = run_dir / "clips" / f"{segment_id}.mp4"
    if clip.exists():
        return clip
    render_dir = run_dir / "scenes" / f"{segment_id}_render"
    for suffix in ("html", "manim"):
        scene = render_dir / f"{segment_id}_{suffix}.mp4"
        if scene.exists():
            return scene
    fallback = render_dir / f"{segment_id}_fallback.mp4"
    if fallback.exists():
        return fallback
    return None


class VideoCompositor:
    """Composites video segments and audio into a final YouTube-ready video."""

    def composite(
        self,
        video_paths: list[Path],
        audio_paths: list[Path],
        output_path: Path,
        resolution: tuple[int, int] = (1920, 1080),
    ) -> Path:
        """Merge video segments with corresponding audio into a single video.

        If audio_paths is empty, produces a silent video (video-only).
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)
        work_dir = output_path.parent / "compositing_temp"
        work_dir.mkdir(exist_ok=True)

        console.print("[blue]Starting video compositing...[/blue]")

        # Step 1: Normalize all videos to same resolution/framerate
        normalized_videos = []
        for i, vp in enumerate(video_paths):
            normalized = work_dir / f"norm_{i:03d}.mp4"
            self._normalize_video(vp, normalized, resolution)
            normalized_videos.append(normalized)

        # Step 2: Concatenate videos
        concat_video = work_dir / "concat_video.mp4"
        self._concat_videos(normalized_videos, concat_video)

        # Step 3: Handle audio (or skip if none)
        if audio_paths:
            concat_audio = work_dir / "concat_audio.mp3"
            self._concat_audio(audio_paths, concat_audio)
            self._merge_av(concat_video, concat_audio, output_path)
        else:
            # Video-only: just re-encode with YouTube settings
            self._encode_video_only(concat_video, output_path)

        console.print(f"[green]Final video: {output_path}[/green]")

        duration = self._get_duration(output_path)
        console.print(f"[green]  Duration: {duration:.1f}s ({duration/60:.1f} min)[/green]")

        return output_path

    def _normalize_video(self, input_path: Path, output_path: Path, resolution: tuple[int, int]) -> None:
        """Normalize video to consistent resolution, framerate, and pixel format."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-vf", f"scale={resolution[0]}:{resolution[1]}:force_original_aspect_ratio=decrease,pad={resolution[0]}:{resolution[1]}:(ow-iw)/2:(oh-ih)/2:color=#1a1a2e",
            "-r", "30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-an",  # strip audio from video segments
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            console.print(f"[red]Normalize failed for {input_path}: {result.stderr[-500:]}[/red]")
            raise RuntimeError(f"Video normalization failed: {result.stderr[-500:]}")

    def _concat_videos(self, video_paths: list[Path], output_path: Path) -> None:
        """Concatenate video files using FFmpeg concat demuxer."""
        concat_list = output_path.parent / "concat_list.txt"
        with open(concat_list, "w") as f:
            for vp in video_paths:
                f.write(f"file '{vp.absolute()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "copy",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"Video concatenation failed: {result.stderr[-500:]}")

        console.print(f"[dim]Concatenated {len(video_paths)} video segments[/dim]")

    def _concat_audio(self, audio_paths: list[Path], output_path: Path) -> None:
        """Concatenate audio files using FFmpeg."""
        concat_list = output_path.parent / "audio_concat_list.txt"
        with open(concat_list, "w") as f:
            for ap in audio_paths:
                f.write(f"file '{ap.absolute()}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c:a", "libmp3lame", "-q:a", "2",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"Audio concatenation failed: {result.stderr[-500:]}")

        console.print(f"[dim]Concatenated {len(audio_paths)} audio segments[/dim]")

    def _merge_av(self, video_path: Path, audio_path: Path, output_path: Path) -> None:
        """Merge video and audio with YouTube-optimized encoding."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-c:a", "aac", "-b:a", "384k", "-ar", "48000",
            "-shortest",
            "-movflags", "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"AV merge failed: {result.stderr[-500:]}")

    def _encode_video_only(self, video_path: Path, output_path: Path) -> None:
        """Re-encode video with YouTube-optimized settings, no audio."""
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-movflags", "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"Video encoding failed: {result.stderr[-500:]}")

    def _get_duration(self, video_path: Path) -> float:
        """Get video duration using ffprobe."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
                capture_output=True, text=True, timeout=10,
            )
            data = json.loads(result.stdout)
            return float(data["format"]["duration"])
        except Exception:
            return 0.0
