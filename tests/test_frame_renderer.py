"""Tests for the deterministic, frame-stepped scene renderer."""

import json
import subprocess
from pathlib import Path

from src.animation.html_renderer import render_html
from src.animation.models import SceneSpec


# A tiny seek-contract scene: a box whose x-position is a pure function of t.
# 2 seconds @ 12 fps at 320x180. sceneReady resolves after document.fonts.ready.
SEEK_HTML = """<!DOCTYPE html>
<html>
<head><style>
  * { margin: 0; padding: 0; }
  body { width: 320px; height: 180px; background: #202030; overflow: hidden; }
  #box { position: absolute; top: 70px; left: 0; width: 40px; height: 40px; background: #e04040; }
</style></head>
<body>
  <div id="box"></div>
  <script>
    window.__SCENE__ = { duration: 2, fps: 12 };
    window.seek = function (t) {
      // Pure function of t: no wall-clock, deterministic for any seek.
      document.getElementById("box").style.transform = "translateX(" + (t * 120) + "px)";
    };
    window.seek(0);
    window.sceneReady = document.fonts.ready;
  </script>
</body>
</html>
"""

# A legacy-style scene that does NOT implement the seek contract.
NO_SEEK_HTML = """<!DOCTYPE html>
<html>
<head><style>body { width: 320px; height: 180px; background: #101010; }</style></head>
<body><h1 style="color:white">no seek here</h1></body>
</html>
"""


def _ffprobe_duration(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    return float(proc.stdout.strip())


def test_frame_renderer_produces_frame_accurate_collage(tmp_path: Path) -> None:
    work_dir = tmp_path / "seg_001_render"
    work_dir.mkdir(parents=True)

    spec = SceneSpec(
        segment_id="seg_001",
        visual_engine="collage",
        code=SEEK_HTML,
        target_duration_seconds=2.0,
        narration_text="A box slides across the frame.",
        description="Frame-accuracy smoke test.",
    )

    result = render_html(spec, work_dir, (320, 180), 12, 120)

    assert result.success, result.error_message
    # Output suffix follows the visual engine: collage -> {id}_collage.mp4.
    assert result.video_path.name == "seg_001_collage.mp4"
    assert result.video_path.exists()
    # TRUE duration is frames / fps = round(2 * 12) / 12 = 2.0 exactly.
    assert abs(result.actual_duration_seconds - 2.0) < 1e-9
    assert abs(_ffprobe_duration(result.video_path) - 2.0) <= 0.05


def test_frame_renderer_rejects_scene_without_seek(tmp_path: Path) -> None:
    work_dir = tmp_path / "seg_002_render"
    work_dir.mkdir(parents=True)

    spec = SceneSpec(
        segment_id="seg_002",
        visual_engine="html",
        code=NO_SEEK_HTML,
        target_duration_seconds=1.0,
        narration_text="A scene with no seek contract.",
        description="Should fail loudly, no legacy fallback.",
    )

    result = render_html(spec, work_dir, (320, 180), 12, 60)

    assert not result.success
    assert result.error_message and "seek contract" in result.error_message
    # No output file is produced for a non-conforming scene.
    assert not (work_dir / "seg_002_html.mp4").exists()
