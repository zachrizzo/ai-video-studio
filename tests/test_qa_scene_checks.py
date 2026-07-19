"""Tests for the frame-rendered-scene QA checks (duration drift + blank frames)."""

import json
import subprocess
from pathlib import Path
from typing import Callable

import pytest

from src.qa.run_qa import qa_run


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True, text=True)


def _make_run(
    tmp_path: Path,
    *,
    engine: str,
    audio_seconds: float,
    estimated_seconds: float,
    video_maker: Callable[[Path], None],
) -> Path:
    run_dir = tmp_path / "run"
    (run_dir / "audio").mkdir(parents=True)
    render_dir = run_dir / "scenes" / "seg_001_render"
    render_dir.mkdir(parents=True)

    script = {
        "title": "Scene QA",
        "subject": "Scene Subject",
        "canonical_name": "Scene Subject",
        "style_bible": "Minimal clean test style.",
        "release_acceptance_criteria": ["Renders are frame-accurate."],
        "storyboard_summary": "One diagram scene.",
        "total_estimated_duration_seconds": estimated_seconds,
        "segments": [
            {
                "segment_id": "seg_001",
                "section_title": "Intro",
                "narration_text": "Scene Subject begins here.",
                "estimated_duration_seconds": estimated_seconds,
                "animation_cues": [],
                "visual_engine": engine,
                "visual_type": "diagram",
            }
        ],
    }
    (run_dir / "script.json").write_text(json.dumps(script))

    audio_path = run_dir / "audio" / "audio_seg_001.wav"
    _ffmpeg(
        ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", str(audio_seconds), str(audio_path)]
    )
    (run_dir / "audio" / "audio_manifest.json").write_text(
        json.dumps(
            {"seg_001": {"audio_path": str(audio_path), "duration_seconds": audio_seconds}}
        )
    )

    video_maker(render_dir / f"seg_001_{engine}.mp4")
    return run_dir


def _colorful(seconds: float) -> Callable[[Path], None]:
    def maker(path: Path) -> None:
        _ffmpeg(
            [
                "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30",
                "-t", str(seconds), "-pix_fmt", "yuv420p", str(path),
            ]
        )
    return maker


def _black(seconds: float) -> Callable[[Path], None]:
    def maker(path: Path) -> None:
        _ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30",
                "-t", str(seconds), "-pix_fmt", "yuv420p", str(path),
            ]
        )
    return maker


def _near_uniform(seconds: float) -> Callable[[Path], None]:
    # A near-uniform frame with tiny grain (stands in for a #F0EEE6 paper
    # background). It must NOT be treated as blank.
    def maker(path: Path) -> None:
        _ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=30",
                "-t", str(seconds), "-vf", "noise=alls=20:allf=t+u",
                "-pix_fmt", "yuv420p", str(path),
            ]
        )
    return maker


@pytest.fixture(autouse=True)
def _no_asr(monkeypatch):
    # Keep the audio/ASR checks out of the way; we assert only on scene checks.
    monkeypatch.setenv("PTV_QA_ASR_COMMAND", "definitely-not-installed-whisper")


def test_correct_duration_non_blank_scene_passes(tmp_path: Path) -> None:
    run_dir = _make_run(
        tmp_path,
        engine="collage",
        audio_seconds=3.0,
        estimated_seconds=3.0,
        video_maker=_colorful(3.0),
    )
    report = qa_run(run_dir)
    ids = {c["id"] for c in report["checks"]}
    assert "scene.duration_drift" not in ids
    assert "scene.blank_frames" not in ids


def test_wrong_duration_collage_scene_errors(tmp_path: Path) -> None:
    # Video is 3s but the segment audio is 8s -> drift far above epsilon.
    run_dir = _make_run(
        tmp_path,
        engine="collage",
        audio_seconds=8.0,
        estimated_seconds=8.0,
        video_maker=_colorful(3.0),
    )
    report = qa_run(run_dir)
    drift = [c for c in report["checks"] if c["id"] == "scene.duration_drift"]
    assert drift, "expected a scene.duration_drift check"
    assert drift[0]["severity"] == "error"


def test_fully_black_scene_flags_blank_error(tmp_path: Path) -> None:
    run_dir = _make_run(
        tmp_path,
        engine="collage",
        audio_seconds=6.0,
        estimated_seconds=6.0,
        video_maker=_black(6.0),
    )
    report = qa_run(run_dir)
    blank = [c for c in report["checks"] if c["id"] == "scene.blank_frames"]
    assert blank, "expected a scene.blank_frames check"
    assert blank[0]["severity"] == "error"


def test_near_uniform_noisy_scene_is_not_blank(tmp_path: Path) -> None:
    run_dir = _make_run(
        tmp_path,
        engine="collage",
        audio_seconds=5.0,
        estimated_seconds=5.0,
        video_maker=_near_uniform(5.0),
    )
    report = qa_run(run_dir)
    ids = {c["id"] for c in report["checks"]}
    assert "scene.blank_frames" not in ids


def _corrupt(path: Path) -> None:
    path.write_bytes(b"this is not a decodable video container")


def test_unprobeable_scene_render_is_flagged_not_silently_passed(tmp_path: Path) -> None:
    """A corrupt/unreadable scene render must NOT silently pass the duration and
    blank-frame gates — both probes fail, so both must surface as explicit
    *_unverified errors instead of a value indistinguishable from a clean pass."""
    run_dir = _make_run(
        tmp_path,
        engine="collage",
        audio_seconds=3.0,
        estimated_seconds=3.0,
        video_maker=_corrupt,
    )
    report = qa_run(run_dir)
    ids = {c["id"] for c in report["checks"]}
    assert "scene.duration_unverified" in ids
    assert "scene.blank_frames_unverified" in ids
    # It must NOT be reported as a clean duration/blank result.
    assert "scene.duration_drift" not in ids
    assert "scene.blank_frames" not in ids
    unverified = [c for c in report["checks"] if c["id"].endswith("_unverified")]
    assert all(c["severity"] == "error" for c in unverified)


# ---------------------------------------------------------------------------
# narration voice: AI-tell detection (docs/script-voice.md)
# ---------------------------------------------------------------------------
def test_detect_ai_tells_flags_known_patterns() -> None:
    from src.qa.run_qa import detect_ai_tells

    segments = [
        # colon-reveal opener + labeled rhetoric + editorial-process language
        ("s1", "First up: a big model. The hook: it was built fast. Every claim fact-checked."),
        # tidy-kicker aphorism + "one message" summarizer
        ("s2", "Two stories, one message: the world is picking sides."),
        # explicit-nuance lecture + it's-not-X-it's-Y contrast
        ("s3", "But note what it's not: it's not a ban — it's a rule. An important nuance: robots."),
        # uniformity source (same-ish length as others) + zero questions overall
        ("s4", "Meanwhile, spending is up. Record spending, deep cuts, and an honest admission."),
    ]
    tells = detect_ai_tells(segments)
    joined = " ".join(t["message"] for t in tells).lower()
    ids = {t["id"] for t in tells}
    assert any("colon" in m or "reveal" in m for m in joined.split(". ")) or "voice.colon_reveal" in ids
    assert "voice.one_message" in ids or "one message" in joined
    assert "voice.labeled_rhetoric" in ids or "the hook" in joined
    assert "voice.editorial_process" in ids or "fact-checked" in joined
    assert "voice.explicit_nuance" in ids or "nuance" in joined
    # zero questions across the whole script is itself a tell
    assert "voice.no_questions" in ids


def test_detect_ai_tells_clean_script_is_quiet() -> None:
    from src.qa.run_qa import detect_ai_tells

    segments = [
        ("s1", "Google still hasn't shipped. And honestly? A free model just beat them to it."),
        ("s2", "So why would TikTok ban AI from its own shop? Trust. Shoppers spot the fakes."),
        ("s3", "Meta spent big, cut deep, and Zuckerberg says the agents aren't there yet. That's the whole story."),
    ]
    tells = detect_ai_tells(segments)
    assert tells == [], [t["id"] for t in tells]


def test_qa_run_surfaces_voice_warnings(tmp_path: Path) -> None:
    """qa_run should surface voice tells as WARNINGS (never errors)."""
    def black(video_path: Path) -> None:
        _ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=320x180:d=1.0", "-r", "30", str(video_path)])

    run_dir = _make_run(tmp_path, engine="collage", audio_seconds=1.0,
                        estimated_seconds=1.0, video_maker=black)
    script = json.loads((run_dir / "script.json").read_text())
    script["segments"][0]["narration_text"] = (
        "First up: a model. Two stories, one message: buy it. Every claim fact-checked."
    )
    (run_dir / "script.json").write_text(json.dumps(script))

    report = qa_run(run_dir)
    voice = [c for c in report["checks"] if c["id"].startswith("voice.")]
    assert voice, "expected voice.* checks in report"
    assert all(c["severity"] == "warning" for c in voice)
