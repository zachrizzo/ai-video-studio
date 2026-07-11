"""Regression tests: imagegen/videogen must FAIL LOUDLY, not exit 0.

Both commands used to collect per-beat failures into a JSON list but always
exit 0. The studio chat agent's typed tools flag errors by exit code, so the
agent was told "success" even when zero clips were produced — it would then
manifest/composite an unfinished video ("it creates images but the video gen
doesn't actually make videos"). Videogen's silent Ken Burns fallback likewise
turned every failed LTX clip into a static pan with no visible signal.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.pipeline import cmd_imagegen, cmd_videogen


def _scene_script(run_dir: Path) -> Path:
    script = {
        "title": "Exit Code Test",
        "subject": "Exit Codes",
        "canonical_name": "Exit Codes",
        "style_bible": "Minimal clean test style.",
        "release_acceptance_criteria": ["Narration matches script."],
        "total_estimated_duration_seconds": 6.0,
        "segments": [
            {
                "segment_id": "seg_001",
                "section_title": "Intro",
                "narration_text": "Exit codes begin here.",
                "estimated_duration_seconds": 6.0,
                "animation_cues": [],
                "visual_engine": "html",
                "visual_type": "scene",
                "visual_beats": [
                    {"beat_id": "setup", "image_prompt": "Minimal clean frame one."},
                ],
            }
        ],
    }
    path = run_dir / "script.json"
    path.write_text(json.dumps(script))
    return path


def test_videogen_exits_nonzero_when_beats_fail(tmp_path: Path, capsys, monkeypatch) -> None:
    """A scene beat with no source image is a failure, not a quiet JSON note."""
    monkeypatch.setenv("PTV_VIDEO_PROVIDER", "kenburns")
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()
    script = _scene_script(run_dir)
    # No images/ dir and no audio manifest: the single beat must fail.

    with pytest.raises(SystemExit) as exc_info:
        cmd_videogen(str(script), str(run_dir))
    assert exc_info.value.code == 2

    out = capsys.readouterr().out
    payload = json.loads([line for line in out.splitlines() if line.startswith("{")][-1])
    assert payload["generated"] == []
    assert payload["failed"], "expected the missing-image beat in failed[]"


def test_videogen_reports_kenburns_fallbacks(tmp_path: Path, capsys, monkeypatch) -> None:
    """LTX failure + Ken Burns fallback must be visible in the result JSON."""
    monkeypatch.setenv("PTV_VIDEO_PROVIDER", "ltx")
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()
    script = _scene_script(run_dir)
    images_dir = run_dir / "images"
    images_dir.mkdir()
    # A single-beat segment's file stem is just the segment id (beats.py:128).
    (images_dir / "seg_001.png").write_bytes(b"fake-png")

    def fake_ltx(*args, **kwargs):
        return {"success": False, "error_message": "MPS out of memory"}

    def fake_kenburns(img, out, duration, **kwargs):
        Path(out).write_bytes(b"fake-clip")
        return {"success": True, "error_message": None}

    import src.videogen.ltx as ltx_mod
    import src.videogen.kenburns as kb_mod

    monkeypatch.setattr(ltx_mod, "generate_ltx_clip", fake_ltx)
    monkeypatch.setattr(kb_mod, "kenburns_clip", fake_kenburns)

    cmd_videogen(str(script), str(run_dir))

    out = capsys.readouterr().out
    payload = json.loads([line for line in out.splitlines() if line.startswith("{")][-1])
    assert payload["generated"] == ["seg_001"]
    assert payload["failed"] == []
    assert payload["fallbacks"] == [
        {"segment_id": "seg_001", "beat_id": "setup", "ltx_error": "MPS out of memory"}
    ]


def test_imagegen_exits_nonzero_when_beats_fail(tmp_path: Path, capsys, monkeypatch) -> None:
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()
    script = _scene_script(run_dir)

    class FailResult:
        success = False
        error_message = "mflux exploded"

    import src.imagegen.flux as flux_mod

    monkeypatch.setattr(flux_mod, "generate_image", lambda **kwargs: FailResult())

    with pytest.raises(SystemExit) as exc_info:
        cmd_imagegen(str(script), str(run_dir))
    assert exc_info.value.code == 2

    out = capsys.readouterr().out
    payload = json.loads([line for line in out.splitlines() if line.startswith("{")][-1])
    assert payload["failed"][0]["error"] == "mflux exploded"
