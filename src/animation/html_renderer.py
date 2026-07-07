import asyncio
from pathlib import Path
from rich.console import Console
from .models import SceneSpec, RenderResult

console = Console()


def render_html(
    spec: SceneSpec,
    work_dir: Path,
    resolution: tuple[int, int] = (1920, 1080),
    fps: int = 30,
    timeout: int = 120,
) -> RenderResult:
    """Render an HTML/collage scene to MP4 via the deterministic frame renderer.

    There is NO real-time recorder fallback: every scene must implement the
    ``window.seek`` contract (see docs/collage/CONTRACTS.md §1). Scenes that do
    not are returned as a failed RenderResult with an actionable message.
    """
    return asyncio.run(_render_html_async(spec, work_dir, resolution, fps, timeout))


async def _render_html_async(
    spec: SceneSpec,
    work_dir: Path,
    resolution: tuple[int, int],
    fps: int,
    timeout: int,
) -> RenderResult:
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        return RenderResult(
            segment_id=spec.segment_id,
            video_path=Path(""),
            actual_duration_seconds=0,
            visual_engine=spec.visual_engine,
            success=False,
            error_message="Playwright not installed. Run: playwright install chromium",
        )

    # html_renderer owns writing the built scene HTML into the render dir.
    html_file = work_dir / f"{spec.segment_id}.html"
    html_file.write_text(spec.code)

    from .frame_renderer import render_frames

    return await render_frames(spec, work_dir, html_file, resolution, fps, timeout)
