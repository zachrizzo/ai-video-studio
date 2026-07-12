import json
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from src.config import PipelineConfig
from src.pipeline import cmd_manifest, cmd_storyboard
from src.qa.run_qa import _transcribe, qa_run
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


def test_storyboard_warns_on_implausibly_short_duration_estimate(tmp_path: Path) -> None:
    """Regression: a segment's estimated_duration_seconds must be plausible for
    its narration word count. An estimate too short for the text wastes a
    synthesize+QA cycle discovering the same problem this warning catches for
    free at the preproduction gate (real incident: a 51-word segment estimated
    at 15s repeatedly failed synthesize's duration-drift QA check because the
    actual narration needs ~25-30s to speak)."""
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()
    script = {
        "title": "Duration Estimate Test",
        "subject": "Subject",
        "canonical_name": "Subject",
        "style_bible": "Minimal clean test style.",
        "storyboard_summary": "One segment with a bad duration estimate.",
        "release_acceptance_criteria": ["Narration matches script."],
        "total_estimated_duration_seconds": 15.0,
        "segments": [
            {
                "segment_id": "seg_001",
                "section_title": "Intro",
                # 51 words — at ~2.2 words/sec this needs ~23s, not 15s.
                "narration_text": (
                    "In 1769, on a small island in the Mediterranean, a boy is born who "
                    "will conquer a continent. Within thirty years he will crown himself "
                    "Emperor of the French. This is the story of Napoleon Bonaparte, told "
                    "through the battles that made him a legend, and the ones that "
                    "destroyed him."
                ),
                "estimated_duration_seconds": 15.0,
                "animation_cues": [],
                "visual_engine": "html",
                "visual_type": "diagram",
            }
        ],
    }
    (run_dir / "script.json").write_text(json.dumps(script))

    cmd_storyboard(str(run_dir / "script.json"), str(run_dir))

    storyboard = json.loads((run_dir / "storyboard.json").read_text())
    assert len(storyboard["warnings"]) == 1
    warning = storyboard["warnings"][0]
    assert warning["segment_id"] == "seg_001"
    assert "too short" in warning["warning"]
    assert "51 words" in warning["warning"]


def test_storyboard_no_warning_for_plausible_duration_estimate(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_test"
    run_dir.mkdir()
    script = {
        "title": "Duration Estimate Test",
        "subject": "Subject",
        "canonical_name": "Subject",
        "style_bible": "Minimal clean test style.",
        "storyboard_summary": "One segment with a reasonable duration estimate.",
        "release_acceptance_criteria": ["Narration matches script."],
        "total_estimated_duration_seconds": 25.0,
        "segments": [
            {
                "segment_id": "seg_001",
                "section_title": "Intro",
                "narration_text": (
                    "In 1769, on a small island in the Mediterranean, a boy is born who "
                    "will conquer a continent. Within thirty years he will crown himself "
                    "Emperor of the French. This is the story of Napoleon Bonaparte, told "
                    "through the battles that made him a legend, and the ones that "
                    "destroyed him."
                ),
                "estimated_duration_seconds": 25.0,
                "animation_cues": [],
                "visual_engine": "html",
                "visual_type": "diagram",
            }
        ],
    }
    (run_dir / "script.json").write_text(json.dumps(script))

    cmd_storyboard(str(run_dir / "script.json"), str(run_dir))

    storyboard = json.loads((run_dir / "storyboard.json").read_text())
    assert storyboard["warnings"] == []


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


class _VoiceboxTranscribeHandler(BaseHTTPRequestHandler):
    """Fake Voicebox /transcribe endpoint: returns a fixed transcript."""

    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # drain the multipart body
        body = json.dumps({"text": "hello world", "duration": 1.0}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence the test server
        pass


def test_transcribe_voicebox_returns_server_text(tmp_path: Path, monkeypatch) -> None:
    server = HTTPServer(("127.0.0.1", 0), _VoiceboxTranscribeHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("PTV_QA_ASR_PROVIDER", "voicebox")
        monkeypatch.setenv("PTV_VOICEBOX_URL", f"http://127.0.0.1:{port}")

        audio_path = tmp_path / "seg.wav"
        audio_path.write_bytes(b"RIFF0000WAVEfake-audio-bytes")

        config = PipelineConfig()
        transcript, error = _transcribe(audio_path, config)

        assert error is None
        assert transcript == "hello world"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_transcribe_voicebox_unreachable_is_actionable(tmp_path: Path, monkeypatch) -> None:
    # Bind an ephemeral port then release it so nothing is listening there.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()

    monkeypatch.setenv("PTV_QA_ASR_PROVIDER", "voicebox")
    monkeypatch.setenv("PTV_VOICEBOX_URL", f"http://127.0.0.1:{dead_port}")

    audio_path = tmp_path / "seg.wav"
    audio_path.write_bytes(b"fake-audio-bytes")

    config = PipelineConfig()
    transcript, error = _transcribe(audio_path, config)

    assert transcript is None
    assert error is not None
    assert "Voicebox /transcribe unreachable" in error
    assert "voicebox.sh" in error
    assert "PTV_QA_ASR_PROVIDER=cli" in error


def test_text_similarity_no_autojunk_collapse() -> None:
    """difflib's autojunk heuristic treats frequent characters as junk on long
    strings, collapsing near-identical transcripts to ~0.0 similarity. QA must
    keep autojunk off so faithful voiceover doesn't fail transcript checks."""
    from src.qa.run_qa import _text_similarity

    expected = (
        "They say it began with two brothers and a she-wolf. In seven fifty "
        "three before Christ, on seven hills above the river Tiber, a "
        "settlement took root. Legend gave it Romulus, who killed his brother "
        "Remus to rule alone, and gave the young city his name. Rome. A "
        "village of shepherds that would one day command the whole known world."
    )
    actual = expected.replace("seven fifty three", "753").replace(". ", ".\n")
    assert _text_similarity(expected, actual) > 0.9
