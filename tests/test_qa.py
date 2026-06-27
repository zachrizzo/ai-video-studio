import json
import subprocess
from pathlib import Path

from src.pipeline import cmd_manifest, cmd_storyboard
from src.qa.run_qa import qa_run
from src.visuals.beats import ltx_motion_prompt, segment_visual_beats


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True, text=True)


def test_qa_flags_audio_duration_drift(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_test"
    (run_dir / "audio").mkdir(parents=True)
    (run_dir / "images").mkdir()
    (run_dir / "clips").mkdir()

    script = {
        "title": "Test Run",
        "subject": "Test Subject",
        "canonical_name": "Test Subject",
        "style_bible": "Minimal clean test style.",
        "release_acceptance_criteria": ["Narration matches script."],
        "total_estimated_duration_seconds": 2.0,
        "segments": [
            {
                "segment_id": "seg_001",
                "section_title": "Intro",
                "narration_text": "Test Subject begins here.",
                "estimated_duration_seconds": 2.0,
                "animation_cues": [],
                "visual_engine": "html",
                "visual_type": "scene",
                "image_prompt": "Minimal clean test style, a banner in frame.",
            }
        ],
    }
    (run_dir / "script.json").write_text(json.dumps(script))

    image_path = run_dir / "images" / "seg_001.png"
    audio_path = run_dir / "audio" / "audio_seg_001.wav"
    clip_path = run_dir / "clips" / "seg_001.mp4"

    _ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=1920x1080", "-frames:v", "1", str(image_path)])
    _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "8", str(audio_path)])
    _ffmpeg(["-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=30", "-t", "8", str(clip_path)])

    (run_dir / "audio" / "audio_manifest.json").write_text(
        json.dumps(
            {
                "seg_001": {
                    "audio_path": str(audio_path),
                    "duration_seconds": 8.0,
                }
            }
        )
    )

    report = qa_run(run_dir)

    assert report["status"] == "failed"
    assert any(check["id"] == "audio.duration_drift" for check in report["checks"])
    assert (run_dir / "qa_report.json").exists()


def test_manifest_orders_visual_beat_clips_with_segment_audio(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_test"
    (run_dir / "audio").mkdir(parents=True)
    (run_dir / "clips").mkdir()

    script = {
        "title": "Beat Test",
        "subject": "Beat Subject",
        "canonical_name": "Beat Subject",
        "style_bible": "Minimal clean test style.",
        "release_acceptance_criteria": ["Narration matches script."],
        "total_estimated_duration_seconds": 6.0,
        "segments": [
            {
                "segment_id": "seg_001",
                "section_title": "Intro",
                "narration_text": "Beat Subject begins here.",
                "estimated_duration_seconds": 6.0,
                "animation_cues": [],
                "visual_engine": "html",
                "visual_type": "scene",
                "visual_beats": [
                    {"beat_id": "setup", "image_prompt": "Minimal clean frame one."},
                    {"beat_id": "payoff", "image_prompt": "Minimal clean frame two."},
                ],
            }
        ],
    }
    (run_dir / "script.json").write_text(json.dumps(script))

    audio_path = run_dir / "audio" / "audio_seg_001.wav"
    clip_1 = run_dir / "clips" / "seg_001_b01.mp4"
    clip_2 = run_dir / "clips" / "seg_001_b02.mp4"
    audio_path.write_bytes(b"fake-audio")
    clip_1.write_bytes(b"fake-clip-1")
    clip_2.write_bytes(b"fake-clip-2")
    (run_dir / "audio" / "audio_manifest.json").write_text(
        json.dumps(
            {
                "seg_001": {
                    "audio_path": str(audio_path),
                    "duration_seconds": 6.0,
                }
            }
        )
    )

    cmd_manifest(str(run_dir / "script.json"), str(run_dir))

    manifest = json.loads((run_dir / "composite_manifest.json").read_text())
    assert manifest["video_paths"] == [str(clip_1), str(clip_2)]
    assert manifest["audio_paths"] == [str(audio_path)]
    assert manifest["segments"][0]["video_paths"] == [str(clip_1), str(clip_2)]


def test_storyboard_command_writes_visual_beat_frames(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()

    script = {
        "title": "Storyboard Test",
        "subject": "Storyboard Subject",
        "canonical_name": "Storyboard Subject",
        "style_bible": "Minimal clean test style.",
        "storyboard_summary": "Two simple planned frames.",
        "release_acceptance_criteria": ["Storyboard is complete."],
        "total_estimated_duration_seconds": 6.0,
        "segments": [
            {
                "segment_id": "seg_001",
                "section_title": "Intro",
                "narration_text": "Storyboard Subject begins here.",
                "estimated_duration_seconds": 6.0,
                "animation_cues": [],
                "visual_engine": "html",
                "visual_type": "scene",
                "visual_beats": [
                    {
                        "beat_id": "setup",
                        "description": "Establish the location.",
                        "shot_type": "wide",
                        "composition": "Simple subject on the left, open space on the right.",
                        "action": "Subject notices the problem.",
                        "camera_motion": "slow push-in",
                        "image_prompt": "Minimal clean frame one.",
                    },
                    {
                        "beat_id": "payoff",
                        "description": "Show the consequence.",
                        "shot_type": "medium",
                        "composition": "Subject centered with one clear prop.",
                        "action": "Subject reacts.",
                        "camera_motion": "slow pull-out",
                        "image_prompt": "Minimal clean frame two.",
                    },
                ],
            }
        ],
    }
    (run_dir / "script.json").write_text(json.dumps(script))

    cmd_storyboard(str(run_dir / "script.json"), str(run_dir))

    storyboard = json.loads((run_dir / "storyboard.json").read_text())
    frames = storyboard["segments"][0]["frames"]
    assert storyboard["summary"]["frame_count"] == 2
    assert storyboard["warnings"] == []
    assert frames[0]["frame_id"] == "seg_001_b01"
    assert frames[0]["shot_type"] == "wide"
    assert frames[1]["camera_motion"] == "slow pull-out"


def test_ltx_motion_prompt_uses_storyboard_action() -> None:
    segment = {
        "segment_id": "seg_001",
        "visual_type": "scene",
        "visual_beats": [
            {
                "beat_id": "setup",
                "description": "A character notices the problem.",
                "shot_type": "medium reaction shot",
                "composition": "Character centered with one clear prop.",
                "action": "The character leans back in surprise as the prop moves closer.",
                "camera_motion": "slow push-in",
                "transition": "hard cut",
                "continuity_notes": ["Keep the same character silhouette."],
                "image_prompt": "Minimal clean frame.",
            }
        ],
    }

    beat = segment_visual_beats(segment)[0]
    prompt = ltx_motion_prompt(beat)

    assert "Bring this action to life" in prompt
    assert "leans back in surprise" in prompt
    assert "slow push-in" in prompt
    assert "Avoid frozen subjects" in prompt


def test_qa_checks_visual_beat_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PTV_QA_ASR_COMMAND", "definitely-not-installed-whisper")

    run_dir = tmp_path / "run_test"
    (run_dir / "audio").mkdir(parents=True)
    (run_dir / "images").mkdir()
    (run_dir / "clips").mkdir()

    script = {
        "title": "Beat QA",
        "subject": "Beat Subject",
        "canonical_name": "Beat Subject",
        "style_bible": "Minimal clean test style.",
        "release_acceptance_criteria": ["Narration matches script."],
        "total_estimated_duration_seconds": 4.0,
        "segments": [
            {
                "segment_id": "seg_001",
                "section_title": "Intro",
                "narration_text": "Beat Subject begins here.",
                "estimated_duration_seconds": 4.0,
                "animation_cues": [],
                "visual_engine": "html",
                "visual_type": "scene",
                "visual_beats": [
                    {"beat_id": "setup", "image_prompt": "Minimal clean frame one."},
                    {"beat_id": "payoff", "image_prompt": "Minimal clean frame two."},
                ],
            }
        ],
    }
    (run_dir / "script.json").write_text(json.dumps(script))

    image_1 = run_dir / "images" / "seg_001_b01.png"
    image_2 = run_dir / "images" / "seg_001_b02.png"
    clip_1 = run_dir / "clips" / "seg_001_b01.mp4"
    clip_2 = run_dir / "clips" / "seg_001_b02.mp4"
    audio_path = run_dir / "audio" / "audio_seg_001.wav"

    _ffmpeg(["-f", "lavfi", "-i", "color=c=blue:s=1920x1080", "-frames:v", "1", str(image_1)])
    _ffmpeg(["-f", "lavfi", "-i", "color=c=red:s=1920x1080", "-frames:v", "1", str(image_2)])
    _ffmpeg(["-f", "lavfi", "-i", "color=c=blue:s=1920x1080:r=30", "-t", "2", str(clip_1)])
    _ffmpeg(["-f", "lavfi", "-i", "color=c=red:s=1920x1080:r=30", "-t", "2", str(clip_2)])
    _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "4", str(audio_path)])

    (run_dir / "audio" / "audio_manifest.json").write_text(
        json.dumps(
            {
                "seg_001": {
                    "audio_path": str(audio_path),
                    "duration_seconds": 4.0,
                }
            }
        )
    )

    report = qa_run(run_dir)
    check_ids = {check["id"] for check in report["checks"]}

    assert "image.missing" not in check_ids
    assert "video.segment_missing" not in check_ids
