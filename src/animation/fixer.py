"""Render and retry logic for visual scenes.

Claude Code handles the code generation and fixing directly.
This module provides the render attempt logic and fallback generation.
"""

from pathlib import Path
from rich.console import Console
from .models import SceneSpec, RenderResult
from .validator import validate, ValidationError
from .manim_renderer import render_manim
from .html_renderer import render_html

console = Console()

# The fallback title card implements the deterministic seek contract
# (docs/collage/CONTRACTS.md §1) so it renders through the frame renderer like
# every other scene: opacities are pure functions of t (no CSS keyframes).
FALLBACK_HTML_TEMPLATE = '''<!DOCTYPE html>
<html>
<head>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        width: 1920px; height: 1080px;
        background: #1a1a2e;
        display: flex; align-items: center; justify-content: center;
        flex-direction: column; gap: 40px;
        font-family: 'Helvetica Neue', sans-serif;
        overflow: hidden;
    }}
    h1 {{ color: white; font-size: 64px; opacity: 0; }}
    p {{
        color: #aaa; font-size: 32px; max-width: 1200px; text-align: center;
        opacity: 0;
    }}
</style>
</head>
<body>
    <h1 id="title">{title}</h1>
    <p id="desc">{description}</p>
<script>
    window.__SCENE__ = {{ duration: {duration}, fps: 30 }};
    function clamp01(x) {{ return Math.max(0, Math.min(1, x)); }}
    // Title fades in over the first 2s; the description fades in over 2s
    // starting at t=1s. Pure function of t — correct for arbitrary seeks.
    window.seek = function (t) {{
        document.getElementById("title").style.opacity = clamp01(t / 2);
        document.getElementById("desc").style.opacity = clamp01((t - 1) / 2);
    }};
    window.seek(0);
    window.sceneReady = document.fonts.ready;
</script>
</body>
</html>'''


def validate_and_render(
    spec: SceneSpec,
    quality_flag: str,
    work_dir: Path,
    render_timeout: int = 120,
    resolution: tuple[int, int] = (1920, 1080),
    fps: int = 30,
) -> RenderResult:
    """Validate and render a single scene spec. Returns result with error details if failed."""

    # Validate
    try:
        validate(spec)
    except ValidationError as e:
        return RenderResult(
            segment_id=spec.segment_id,
            video_path=Path(""),
            actual_duration_seconds=0,
            visual_engine=spec.visual_engine,
            success=False,
            error_message=f"Validation error: {e}",
        )

    # Render
    attempt_dir = work_dir / f"{spec.segment_id}_render"
    attempt_dir.mkdir(parents=True, exist_ok=True)

    if spec.visual_engine == "manim":
        return render_manim(spec, quality_flag, attempt_dir, render_timeout)
    else:
        # Both "html" and "collage" go through render_html: the collage builder
        # emits seek-contract HTML written to {segment_id}.html in attempt_dir,
        # and render_html routes it to the deterministic frame renderer. The
        # output suffix follows spec.visual_engine (collage -> {id}_collage.mp4).
        return render_html(spec, attempt_dir, resolution, fps, render_timeout)


def generate_fallback(
    segment_id: str,
    title: str,
    description: str,
    duration: float,
    work_dir: Path,
    resolution: tuple[int, int] = (1920, 1080),
) -> RenderResult:
    """Generate a simple fallback title card visual."""
    fallback_dir = work_dir / f"{segment_id}_fallback"
    fallback_dir.mkdir(parents=True, exist_ok=True)

    html_code = FALLBACK_HTML_TEMPLATE.format(
        title=title.replace('"', '&quot;'),
        description=description[:200].replace('"', '&quot;'),
        duration=duration,
    )

    spec = SceneSpec(
        segment_id=segment_id,
        visual_engine="html",
        code=html_code,
        target_duration_seconds=duration,
        narration_text=description,
        description=f"Fallback for {title}",
    )

    result = render_html(spec, fallback_dir, resolution)
    result.attempts = -1
    return result
