import asyncio
import subprocess
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
    """Render HTML visualization to MP4 using Playwright screen recording."""
    return asyncio.run(_render_html_async(spec, work_dir, resolution, fps, timeout))

async def _render_html_async(
    spec: SceneSpec,
    work_dir: Path,
    resolution: tuple[int, int],
    fps: int,
    timeout: int,
) -> RenderResult:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return RenderResult(
            segment_id=spec.segment_id,
            video_path=Path(""),
            actual_duration_seconds=0,
            visual_engine="html",
            success=False,
            error_message="Playwright not installed. Run: playwright install chromium",
        )

    html_file = work_dir / f"{spec.segment_id}.html"
    html_file.write_text(spec.code)

    video_dir = work_dir / "html_videos"
    video_dir.mkdir(exist_ok=True)

    output_path = work_dir / f"{spec.segment_id}_html.mp4"

    console.print(f"[blue]Recording HTML visual: {spec.segment_id}[/blue]")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            context = await browser.new_context(
                viewport={"width": resolution[0], "height": resolution[1]},
                record_video_dir=str(video_dir),
                record_video_size={"width": resolution[0], "height": resolution[1]},
            )

            page = await context.new_page()
            await page.goto(f"file://{html_file.absolute()}")

            # Wait for the animation duration
            duration_ms = int(spec.target_duration_seconds * 1000)
            await page.wait_for_timeout(duration_ms + 500)  # small buffer

            await context.close()
            await browser.close()

        # Find the recorded video
        recorded_files = list(video_dir.glob("*.webm"))
        if not recorded_files:
            return RenderResult(
                segment_id=spec.segment_id,
                video_path=Path(""),
                actual_duration_seconds=0,
                visual_engine="html",
                success=False,
                error_message="Recording completed but no video file found",
            )

        # Convert webm to mp4 and trim to exact duration
        recorded = recorded_files[-1]
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(recorded),
                "-t", str(spec.target_duration_seconds),
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-pix_fmt", "yuv420p",
                str(output_path),
            ],
            capture_output=True, text=True, timeout=60,
        )

        if output_path.exists():
            console.print(f"[green]Recorded HTML visual: {spec.segment_id} ({spec.target_duration_seconds:.1f}s)[/green]")
            return RenderResult(
                segment_id=spec.segment_id,
                video_path=output_path,
                actual_duration_seconds=spec.target_duration_seconds,
                visual_engine="html",
                success=True,
            )
        else:
            return RenderResult(
                segment_id=spec.segment_id,
                video_path=Path(""),
                actual_duration_seconds=0,
                visual_engine="html",
                success=False,
                error_message="FFmpeg conversion from webm to mp4 failed",
            )

    except Exception as e:
        return RenderResult(
            segment_id=spec.segment_id,
            video_path=Path(""),
            actual_duration_seconds=0,
            visual_engine="html",
            success=False,
            error_message=str(e),
        )
