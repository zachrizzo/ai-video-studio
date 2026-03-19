import subprocess
import re
import os
from pathlib import Path
from rich.console import Console
from .models import SceneSpec, RenderResult

# LaTeX (MacTeX) installs to /Library/TeX/texbin on macOS.
# Manim's MathTex requires pdflatex/latex to be on PATH.
_LATEX_BIN = "/Library/TeX/texbin"
_HOMEBREW_BIN = "/opt/homebrew/bin"

def _build_env() -> dict:
    """Return an env dict with LaTeX and Homebrew on PATH."""
    env = os.environ.copy()
    extra = ":".join(p for p in [_LATEX_BIN, _HOMEBREW_BIN] if p not in env.get("PATH", ""))
    if extra:
        env["PATH"] = extra + ":" + env.get("PATH", "")
    return env

console = Console()

def get_scene_class_name(code: str) -> str:
    """Extract the Scene subclass name from code."""
    import ast
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name in ("Scene", "ThreeDScene", "MovingCameraScene"):
                    return node.name
    raise ValueError("No Scene subclass found in code")

def render_manim(
    spec: SceneSpec,
    quality_flag: str,
    work_dir: Path,
    timeout: int = 120,
) -> RenderResult:
    """Render a Manim scene to MP4 via subprocess."""
    # Write code to file
    scene_file = work_dir / f"{spec.segment_id}.py"
    scene_file.write_text(spec.code)

    scene_name = get_scene_class_name(spec.code)
    media_dir = work_dir / "media"
    media_dir.mkdir(exist_ok=True)

    cmd = [
        "python", "-m", "manim", "render",
        quality_flag,
        "--disable_caching",
        "--media_dir", str(media_dir),
        str(scene_file),
        scene_name,
    ]

    console.print(f"[blue]Rendering Manim scene: {scene_name}[/blue]")
    console.print(f"[dim]Command: {' '.join(cmd)}[/dim]")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(work_dir),
            env=_build_env(),
        )

        if result.returncode != 0:
            return RenderResult(
                segment_id=spec.segment_id,
                video_path=Path(""),
                actual_duration_seconds=0,
                visual_engine="manim",
                success=False,
                error_message=result.stderr[-2000:] if result.stderr else "Unknown render error",
            )

        # Find the output video file — prefer the final combined file over partials
        video_files = list(media_dir.rglob("*.mp4"))
        if not video_files:
            return RenderResult(
                segment_id=spec.segment_id,
                video_path=Path(""),
                actual_duration_seconds=0,
                visual_engine="manim",
                success=False,
                error_message="Render completed but no MP4 file found",
            )

        # Filter out partial_movie_files — the final render is the one NOT in that dir
        final_files = [f for f in video_files if "partial_movie_files" not in str(f)]
        video_path = final_files[0] if final_files else video_files[-1]
        duration = _get_video_duration(video_path)

        console.print(f"[green]Rendered {scene_name}: {duration:.1f}s[/green]")

        return RenderResult(
            segment_id=spec.segment_id,
            video_path=video_path,
            actual_duration_seconds=duration,
            visual_engine="manim",
            success=True,
        )

    except subprocess.TimeoutExpired:
        return RenderResult(
            segment_id=spec.segment_id,
            video_path=Path(""),
            actual_duration_seconds=0,
            visual_engine="manim",
            success=False,
            error_message=f"Render timed out after {timeout} seconds",
        )


def _get_video_duration(video_path: Path) -> float:
    """Get video duration using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
            capture_output=True, text=True, timeout=10,
        )
        import json
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return 0.0
