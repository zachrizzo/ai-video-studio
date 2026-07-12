"""Tests for the final-video A/V sync QA gate (final.av_sync_drift).

The compositor's AV merge uses ffmpeg -shortest, which silently truncates the
final video when the concatenated clips are shorter than the narration (e.g.
audio was re-rolled but existing clips were skipped). QA must catch that
duration mismatch as an error instead of passing a desynced video.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.qa.run_qa import qa_run


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True, text=True)


@pytest.fixture(autouse=True)
def _no_asr(monkeypatch):
    # Keep the ASR checks out of the way; we assert only on the sync check.
    monkeypatch.setenv("PTV_QA_ASR_COMMAND", "definitely-not-installed-whisper")
    monkeypatch.delenv("PTV_VIDEO_SPEED", raising=False)


def _make_run(tmp_path: Path, *, narration_seconds: float, final_seconds: float) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "audio").mkdir(parents=True)

    script = {
        "title": "AV Sync",
        "subject": "Sync Subject",
        "canonical_name": "Sync Subject",
        "style_bible": "Minimal clean test style.",
        "release_acceptance_criteria": ["Audio and video stay in sync."],
        "storyboard_summary": "One diagram scene.",
        "total_estimated_duration_seconds": narration_seconds,
        "segments": [
            {
                "segment_id": "seg_001",
                "section_title": "Intro",
                "narration_text": "Sync Subject begins here.",
                "estimated_duration_seconds": narration_seconds,
                "animation_cues": [],
                "visual_engine": "html",
                "visual_type": "diagram",
            }
        ],
    }
    (run_dir / "script.json").write_text(json.dumps(script))

    audio_path = run_dir / "audio" / "audio_seg_001.wav"
    _ffmpeg(
        ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(narration_seconds), str(audio_path)]
    )
    (run_dir / "audio" / "audio_manifest.json").write_text(
        json.dumps(
            {
                "seg_001": {
                    "audio_path": str(audio_path),
                    "duration_seconds": narration_seconds,
                    "qa_issues": [],
                },
                # The voice meta key must not break the duration sum.
                "_voice": {"provider": "qwen", "speaker": "serena", "language": "english"},
            }
        )
    )

    _ffmpeg(
        [
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30",
            "-t", str(final_seconds), "-pix_fmt", "yuv420p",
            str(run_dir / "final.mp4"),
        ]
    )
    return run_dir


def test_truncated_final_video_is_an_av_sync_error(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, narration_seconds=8.0, final_seconds=3.0)
    report = qa_run(run_dir)
    drift = [c for c in report["checks"] if c["id"] == "final.av_sync_drift"]
    assert drift, "expected a final.av_sync_drift check"
    assert drift[0]["severity"] == "error"
    assert "shorter" in drift[0]["message"]
    assert drift[0]["details"]["expected_seconds"] == pytest.approx(8.0, abs=0.2)


def test_matching_final_video_passes_av_sync(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, narration_seconds=5.0, final_seconds=5.0)
    report = qa_run(run_dir)
    assert "final.av_sync_drift" not in {c["id"] for c in report["checks"]}


def test_av_sync_expectation_respects_video_speed(tmp_path: Path, monkeypatch) -> None:
    # At 2x playback an 8s narration correctly yields a ~4s final video.
    monkeypatch.setenv("PTV_VIDEO_SPEED", "2.0")
    run_dir = _make_run(tmp_path, narration_seconds=8.0, final_seconds=4.0)
    report = qa_run(run_dir)
    assert "final.av_sync_drift" not in {c["id"] for c in report["checks"]}


def test_av_sync_prefers_composite_meta_speed(tmp_path: Path) -> None:
    """cmd_composite persists the speed it actually applied (which may come
    from --speed, overriding config) to composite_meta.json; the drift check
    must trust that over the config default, or a deliberately retimed final
    (e.g. 1.25x) is falsely flagged as A/V drift."""
    import json as _json

    run_dir = _make_run(tmp_path, narration_seconds=10.0, final_seconds=8.0)
    (run_dir / "composite_meta.json").write_text(_json.dumps({"speed": 1.25}))
    report = qa_run(run_dir)
    assert "final.av_sync_drift" not in {c["id"] for c in report["checks"]}
