"""Deterministic, frame-stepped renderer for seek-contract HTML scenes.

Collage scenes (and any hand-written html scene that opts in) expose the
``window.seek(t)`` contract described in ``docs/collage/CONTRACTS.md`` §1. For
those scenes we do NOT screen-record in real time. Instead we step the scene
frame by frame (``seek(frame / fps)``), screenshot each frame, and pipe the
frames straight into ffmpeg. This produces a clip with EXACTLY
``round(duration * fps)`` frames, so the reported duration is frame-accurate
rather than a wall-clock approximation.

This is the ONLY html/collage render path — there is no real-time recorder
fallback. A scene that does not implement the seek contract (``window.seek`` +
``window.__SCENE__``) is returned as a FAILED RenderResult telling the author
to implement it.
"""

import asyncio
import glob
import os
import subprocess
from pathlib import Path

from rich.console import Console

from .models import SceneSpec, RenderResult

console = Console()


def _chromium_launch_kwargs() -> dict:
    """Resolve a concrete chromium binary under PLAYWRIGHT_BROWSERS_PATH.

    The pre-installed browser build may not match the exact revision the
    installed Playwright package expects, so we point launch() at whatever
    full chromium build is present (revision-agnostic) instead of relying on
    Playwright's version-pinned lookup. Falls back to Playwright's default
    resolution (which fails loudly with an actionable message) if none found.
    """
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if not root:
        return {}
    # Prefer the full chromium build over the headless shell; pick the highest
    # revision if several are installed.
    # macOS app-bundle name varies by Playwright/Chromium revision ("Chromium.app"
    # on older revisions, "Google Chrome for Testing.app" after the rebrand), and
    # the arch-specific directory can be chrome-mac, chrome-mac-x64, or
    # chrome-mac-arm64 -- glob broadly rather than hardcoding one combination.
    patterns = [
        os.path.join(root, "chromium-*", "chrome-linux", "chrome"),
        os.path.join(root, "chromium-*", "chrome-mac*", "*.app", "Contents", "MacOS", "*"),
    ]
    candidates = sorted(c for pattern in patterns for c in glob.glob(pattern))
    if candidates:
        return {"executable_path": candidates[-1]}
    return {}


# Resolve ``window.sceneReady`` but never wait longer than 10s (per contract).
_SCENE_READY_JS = """
() => {
  const ready = window.sceneReady || Promise.resolve();
  return Promise.race([
    Promise.resolve(ready),
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error('sceneReady timeout')), 10000)),
  ]);
}
"""


def _failed(spec: SceneSpec, message: str) -> RenderResult:
    return RenderResult(
        segment_id=spec.segment_id,
        video_path=Path(""),
        actual_duration_seconds=0,
        visual_engine=spec.visual_engine,
        success=False,
        error_message=message,
    )


def _probe_frame_count(path: Path) -> int | None:
    """Count encoded video packets (≈ frames) with ffprobe."""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_packets",
                "-show_entries",
                "stream=nb_read_packets",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return int(proc.stdout.strip())
    except Exception:
        return None


async def _step_frames(page, ff, n_frames: int, fps: int, segment_id: str) -> None:
    """Seek → screenshot → feed ffmpeg, one frame at a time."""
    for frame in range(n_frames):
        await page.evaluate(f"window.seek({frame / fps})")
        img = await page.screenshot(type="jpeg", quality=92)
        ff.stdin.write(img)
        if frame % 30 == 0:
            console.print(f"[dim]  {segment_id}: frame {frame}/{n_frames}[/dim]")
    # stdin is flushed and closed by ff.communicate() after the loop, which
    # signals EOF so ffmpeg finalizes the encode.
    ff.stdin.flush()


_SEEK_CONTRACT_ERROR = (
    "HTML scene must implement the deterministic seek contract "
    "(window.seek + window.__SCENE__); see docs/collage/CONTRACTS.md"
)


async def render_frames(
    spec: SceneSpec,
    work_dir: Path,
    html_file: Path,
    resolution: tuple[int, int],
    fps: int,
    render_timeout: int,
) -> RenderResult:
    """Deterministically frame-render ``html_file`` to MP4.

    Always returns a ``RenderResult``. A scene that does not implement the seek
    contract (``window.seek`` + ``window.__SCENE__``) fails loudly with an
    actionable error — there is no fallback path.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return _failed(
            spec, "Playwright not installed. Run: playwright install chromium"
        )

    output_path = work_dir / f"{spec.segment_id}_{spec.visual_engine}.mp4"
    ff: subprocess.Popen | None = None
    n_frames = 0
    eff_fps = fps

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(**_chromium_launch_kwargs())
            context = await browser.new_context(
                viewport={"width": resolution[0], "height": resolution[1]},
            )
            page = await context.new_page()
            await page.goto(f"file://{html_file.absolute()}")

            # Contract probe: fail loudly if the scene does not implement both
            # window.seek and window.__SCENE__. There is no fallback path.
            has_contract = await page.evaluate(
                "() => typeof window.seek === 'function' "
                "&& typeof window.__SCENE__ === 'object' && window.__SCENE__ !== null"
            )
            if not has_contract:
                await browser.close()
                return _failed(spec, _SEEK_CONTRACT_ERROR)

            console.print(
                f"[blue]Frame-rendering scene: {spec.segment_id} "
                f"({spec.visual_engine})[/blue]"
            )

            # Wait for fonts/assets (bounded at 10s inside the JS).
            try:
                await page.evaluate(_SCENE_READY_JS)
            except Exception as exc:  # noqa: BLE001
                await browser.close()
                return _failed(spec, f"sceneReady did not resolve within 10s: {exc}")

            scene = await page.evaluate("() => window.__SCENE__ || {}") or {}
            # __SCENE__.duration wins; fall back to the spec target duration.
            duration = float(scene.get("duration") or spec.target_duration_seconds or 0)
            # The caller-supplied fps wins; fall back to __SCENE__.fps.
            eff_fps = int(fps or scene.get("fps") or 30)
            if duration <= 0 or eff_fps <= 0:
                await browser.close()
                return _failed(
                    spec,
                    f"invalid scene timing (duration={duration}, fps={eff_fps})",
                )

            n_frames = int(round(duration * eff_fps))
            if n_frames <= 0:
                await browser.close()
                return _failed(spec, f"scene resolves to {n_frames} frames")

            # Timeout scales with the amount of work (CONTRACTS §1).
            budget = max(render_timeout, n_frames * 0.5 + 60)

            ff = subprocess.Popen(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "image2pipe",
                    # The screenshots piped to stdin are concatenated JPEGs;
                    # declare the input codec so ffmpeg does not try (and fail)
                    # to autodetect it from the stream.
                    "-c:v",
                    "mjpeg",
                    "-framerate",
                    str(eff_fps),
                    "-i",
                    "-",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "fast",
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    str(eff_fps),
                    str(output_path),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            try:
                await asyncio.wait_for(
                    _step_frames(page, ff, n_frames, eff_fps, spec.segment_id),
                    timeout=budget,
                )
            except asyncio.TimeoutError:
                ff.kill()
                await browser.close()
                return _failed(
                    spec, f"frame render exceeded {budget:.0f}s budget"
                )

            await browser.close()
    except Exception as exc:  # noqa: BLE001
        if ff is not None and ff.poll() is None:
            ff.kill()
        return _failed(spec, str(exc))

    # Finalize ffmpeg: communicate() closes stdin (EOF) and drains the pipes.
    try:
        _, stderr = ff.communicate(timeout=max(60, n_frames * 0.5))
    except subprocess.TimeoutExpired:
        ff.kill()
        return _failed(spec, "ffmpeg did not finish encoding within the timeout")

    if ff.returncode != 0:
        tail = (stderr or b"").decode(errors="ignore")[-500:]
        return _failed(spec, f"ffmpeg encoding failed: {tail}")
    if not output_path.exists():
        return _failed(spec, "ffmpeg produced no output file")

    # TRUE duration is frames / fps, not a wall-clock estimate.
    actual_duration = n_frames / eff_fps

    # Verify the encoded frame count matches the deterministic expectation.
    error_message: str | None = None
    probed = _probe_frame_count(output_path)
    if probed is not None and probed != n_frames:
        error_message = (
            f"frame count mismatch: expected {n_frames}, encoded {probed}"
        )

    if error_message:
        console.print(f"[red]Scene {spec.segment_id}: {error_message}[/red]")
    else:
        console.print(
            f"[green]Frame-rendered scene: {spec.segment_id} "
            f"({n_frames} frames @ {eff_fps}fps = {actual_duration:.2f}s)[/green]"
        )

    return RenderResult(
        segment_id=spec.segment_id,
        video_path=output_path,
        actual_duration_seconds=actual_duration,
        visual_engine=spec.visual_engine,
        success=error_message is None,
        error_message=error_message,
    )
