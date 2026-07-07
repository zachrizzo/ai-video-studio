"""Ken Burns motion clips: turn a still image into a slow pan/zoom MP4.

Uses ffmpeg's zoompan filter. The clip is rendered at an EXACT target duration
(matched to the segment's narration audio) so it composites in sync.
"""

import subprocess
from pathlib import Path

from rich.console import Console

console = Console()

DIRECTIONS = ["in", "out", "pan_lr", "pan_tb"]


def _ffprobe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return None


def _zoompan_expr(direction: str, total_frames: int, zoom: float, w: int, h: int) -> str:
    """Build the zoompan parameters for a given motion direction."""
    z_in = f"min(zoom+{(zoom - 1) / max(total_frames, 1):.6f},{zoom})"
    z_out = f"if(eq(on,0),{zoom},max(zoom-{(zoom - 1) / max(total_frames, 1):.6f},1))"
    center_x = "iw/2-(iw/zoom/2)"
    center_y = "ih/2-(ih/zoom/2)"
    if direction == "in":
        return f"z='{z_in}':x='{center_x}':y='{center_y}'"
    if direction == "out":
        return f"z='{z_out}':x='{center_x}':y='{center_y}'"
    if direction == "pan_lr":
        # hold a gentle zoom and pan left→right
        return f"z='{zoom}':x='(iw-iw/zoom)*on/{max(total_frames - 1, 1)}':y='{center_y}'"
    # pan_tb: pan top→bottom
    return f"z='{zoom}':x='{center_x}':y='(ih-ih/zoom)*on/{max(total_frames - 1, 1)}'"


def kenburns_clip(
    image_path: Path,
    output_path: Path,
    duration_seconds: float,
    resolution: tuple[int, int] = (1920, 1080),
    fps: int = 30,
    zoom: float = 1.12,
    direction: str = "in",
    timeout: int = 180,
) -> dict:
    """Render a Ken Burns motion clip from a still at an exact duration.

    Returns {success, video_path, duration, error_message}.
    """
    image_path = Path(image_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = resolution
    total_frames = max(round(duration_seconds * fps), 1)

    # Pre-upscale 2x before zoompan so slow zooms have pixels to crop (anti-jitter),
    # run zoompan, then trim to the exact duration.
    vf = (
        f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
        f"crop={w * 2}:{h * 2},"
        f"zoompan={_zoompan_expr(direction, total_frames, zoom, w, h)}:"
        f"d={total_frames}:s={w}x{h}:fps={fps},"
        f"trim=duration={duration_seconds:.3f},setpts=PTS-STARTPTS"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-t", f"{duration_seconds:.3f}",
        "-vf", vf,
        "-r", str(fps),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-an",
        str(output_path),
    ]

    console.print(f"[blue]Ken Burns: {image_path.stem} -> {output_path.name} "
                  f"({duration_seconds:.1f}s, {direction})[/blue]")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"success": False, "video_path": str(output_path), "duration": None,
                "error_message": f"ffmpeg zoompan timed out after {timeout}s"}

    if proc.returncode != 0 or not output_path.exists():
        return {"success": False, "video_path": str(output_path), "duration": None,
                "error_message": f"ffmpeg exited {proc.returncode}: {proc.stderr[-400:]}"}

    actual = _ffprobe_duration(output_path)
    if actual is not None and abs(actual - duration_seconds) > 0.15:
        console.print(f"[yellow]  duration drift: wanted {duration_seconds:.2f}s, got {actual:.2f}s[/yellow]")

    actual_str = f"{actual:.2f}s" if actual is not None else "?s"
    console.print(f"[green]  -> {output_path} ({actual_str})[/green]")
    return {"success": True, "video_path": str(output_path), "duration": actual,
            "error_message": None}
