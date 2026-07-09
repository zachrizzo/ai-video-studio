"""Tests for word-level alignment and TimeRef resolution.

Covers the whisper alignment path (via a FAKE whisper shell script so no real
model is needed), the fail-loud semantics (missing whisper -> SystemExit), the
no-collage-work no-op, and resolve_time's at/at_frac/at_word behaviour with the
frozen "no estimated fallback" contract (missing alignment -> ValueError).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.alignment.align import _whisper_align, run_align
from src.collage.spec import TimeRef
from src.collage.timing import normalize_word, resolve_time


# ---------------------------------------------------------------------------
# resolve_time
# ---------------------------------------------------------------------------


def test_resolve_time_at_absolute_clamps() -> None:
    assert resolve_time(TimeRef(at=2.5), narration_text="", duration_seconds=10.0) == 2.5
    # Beyond duration -> clamped to duration.
    assert resolve_time(TimeRef(at=20.0), narration_text="", duration_seconds=10.0) == 10.0


def test_resolve_time_at_frac() -> None:
    t = resolve_time(TimeRef(at_frac=0.5), narration_text="", duration_seconds=8.0)
    assert t == 4.0


def test_resolve_time_offset_and_negative_clamp() -> None:
    # Positive offset.
    assert resolve_time(TimeRef(at=1.0, offset=2.0), narration_text="", duration_seconds=10.0) == 3.0
    # Negative offset clamps at 0.
    assert resolve_time(TimeRef(at=1.0, offset=-5.0), narration_text="", duration_seconds=10.0) == 0.0


def test_resolve_time_at_word_uses_alignment_start() -> None:
    words = [
        {"w": "Clouds", "start": 3.12, "end": 3.55},
        {"w": "drift", "start": 3.55, "end": 3.9},
        {"w": "clouds", "start": 6.0, "end": 6.4},
    ]
    t = resolve_time(
        TimeRef(at_word="clouds"),
        narration_text="Clouds drift clouds",
        duration_seconds=10.0,
        words=words,
    )
    assert t == 3.12


def test_resolve_time_at_word_occurrence_two() -> None:
    words = [
        {"w": "Clouds", "start": 3.12, "end": 3.55},
        {"w": "clouds", "start": 6.0, "end": 6.4},
    ]
    t = resolve_time(
        TimeRef(at_word="clouds", occurrence=2),
        narration_text="Clouds clouds",
        duration_seconds=10.0,
        words=words,
    )
    assert t == 6.0


def test_resolve_time_at_word_punctuation_and_case_normalized() -> None:
    words = [{"w": " Clouds,", "start": 3.12, "end": 3.55}]
    t = resolve_time(
        TimeRef(at_word="clouds"),
        narration_text="Clouds",
        duration_seconds=10.0,
        words=words,
    )
    assert t == 3.12


def test_resolve_time_at_word_without_alignment_raises() -> None:
    with pytest.raises(ValueError, match="alignment"):
        resolve_time(
            TimeRef(at_word="clouds"),
            narration_text="Clouds drift by",
            duration_seconds=10.0,
            words=None,
        )


def test_resolve_time_at_word_not_found_raises() -> None:
    words = [{"w": "clouds", "start": 3.12, "end": 3.55}]
    with pytest.raises(ValueError, match="not found"):
        resolve_time(
            TimeRef(at_word="mountains"),
            narration_text="Clouds drift by",
            duration_seconds=10.0,
            words=words,
        )


def test_resolve_time_at_word_occurrence_out_of_range_raises() -> None:
    words = [{"w": "clouds", "start": 3.12, "end": 3.55}]
    with pytest.raises(ValueError, match="occurrence 3"):
        resolve_time(
            TimeRef(at_word="clouds", occurrence=3),
            narration_text="clouds",
            duration_seconds=10.0,
            words=words,
        )


def test_normalize_word() -> None:
    assert normalize_word(" Clouds,") == "clouds"
    assert normalize_word("DON'T") == "don't"
    assert normalize_word("...") == ""


# ---------------------------------------------------------------------------
# run_align fixtures
# ---------------------------------------------------------------------------


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True, text=True)


def _collage_run(tmp_path: Path) -> tuple[Path, Path]:
    """Build a minimal run dir with one collage segment + a real tiny wav.

    Returns (script_path, run_dir).
    """
    run_dir = tmp_path / "run_align"
    audio_dir = run_dir / "audio"
    audio_dir.mkdir(parents=True)

    wav_path = audio_dir / "seg01.wav"
    _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "1", str(wav_path)])

    script = {
        "title": "Collage Test",
        "segments": [
            {
                "segment_id": "seg01",
                "visual_engine": "collage",
                "visual_type": "diagram",
                "narration_text": "Clouds drift over the valley",
                "estimated_duration_seconds": 1.0,
            }
        ],
    }
    script_path = run_dir / "script.json"
    script_path.write_text(json.dumps(script))

    manifest = {
        "seg01": {"audio_path": str(wav_path), "duration_seconds": 1.0, "qa_issues": []}
    }
    (audio_dir / "audio_manifest.json").write_text(json.dumps(manifest))
    return script_path, run_dir


def _command_for(script: Path) -> str:
    """A PTV_ALIGN_COMMAND string that runs ``script`` with this interpreter.

    Cross-platform: python scripts run everywhere (the original bash fixtures
    could not execute on Windows). Forward slashes because the command is
    parsed with POSIX shlex.split, which eats backslashes.
    """
    py = sys.executable.replace("\\", "/")
    return f'"{py}" "{script.as_posix()}"'


def _fake_whisper(tmp_path: Path) -> str:
    """A python script emulating whisper: writes a whisper-style JSON to --output_dir."""
    script = tmp_path / "fake_whisper.py"
    script.write_text(
        """import json, sys
args = sys.argv[1:]
out_dir = args[args.index("--output_dir") + 1]
payload = {
    "text": "Clouds drift over the valley",
    "segments": [
        {"id": 0, "text": "Clouds drift over the valley", "words": [
            {"word": "Clouds", "start": 0.0, "end": 0.2},
            {"word": "drift", "start": 0.2, "end": 0.4},
            {"word": "over", "start": 0.4, "end": 0.6},
            {"word": "the", "start": 0.6, "end": 0.7},
            {"word": "valley", "start": 0.7, "end": 1.0},
        ]}
    ],
}
with open(f"{out_dir}/seg01.json", "w") as f:
    json.dump(payload, f)
"""
    )
    return _command_for(script)


def _fake_whisper_bad_model(tmp_path: Path) -> str:
    """A whisper stand-in that rejects the model like argparse and exits 1."""
    script = tmp_path / "fake_whisper_bad_model.py"
    script.write_text(
        """import sys
sys.stderr.write("error: argument --model: 'bogus-model' is not one of the available models\\n")
sys.exit(1)
"""
    )
    return _command_for(script)


# ---------------------------------------------------------------------------
# run_align
# ---------------------------------------------------------------------------


def test_run_align_whisper_success(tmp_path: Path, monkeypatch) -> None:
    script_path, run_dir = _collage_run(tmp_path)
    fake = _fake_whisper(tmp_path)
    monkeypatch.setenv("PTV_ALIGN_COMMAND", str(fake))

    run_align(script_path, run_dir)

    alignment_path = run_dir / "audio" / "alignment.json"
    assert alignment_path.exists()
    data = json.loads(alignment_path.read_text())
    assert "seg01" in data
    entry = data["seg01"]
    assert entry["source"] == "whisper"
    assert entry["duration_seconds"] == 1.0
    assert [w["w"] for w in entry["words"]] == ["Clouds", "drift", "over", "the", "valley"]
    assert entry["words"][0]["start"] == 0.0


def test_run_align_missing_whisper_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    script_path, run_dir = _collage_run(tmp_path)
    monkeypatch.setenv("PTV_ALIGN_COMMAND", "/nonexistent/definitely-not-whisper-xyz")

    with pytest.raises(SystemExit) as exc_info:
        run_align(script_path, run_dir)
    assert exc_info.value.code != 0
    assert not (run_dir / "audio" / "alignment.json").exists()


def test_run_align_includes_sfx_at_word_scene_segments(tmp_path: Path, monkeypatch) -> None:
    """A scene (non-collage) segment with an at_word sfx cue must get aligned too."""
    script_path, run_dir = _collage_run(tmp_path)
    script = json.loads(script_path.read_text())
    script["segments"][0]["visual_engine"] = "html"
    script["segments"][0]["visual_type"] = "scene"
    script["segments"][0]["sfx"] = [{"sound": "cannon_boom", "at_word": "valley"}]
    script_path.write_text(json.dumps(script))

    fake = _fake_whisper(tmp_path)
    monkeypatch.setenv("PTV_ALIGN_COMMAND", str(fake))

    run_align(script_path, run_dir)

    data = json.loads((run_dir / "audio" / "alignment.json").read_text())
    assert "seg01" in data
    assert data["seg01"]["source"] == "whisper"


def test_whisper_align_bad_model_suggests_upgrade(tmp_path: Path) -> None:
    """A model whisper does not recognise yields an actionable upgrade hint."""
    _, run_dir = _collage_run(tmp_path)
    wav_path = run_dir / "audio" / "seg01.wav"
    fake = _fake_whisper_bad_model(tmp_path)

    words, error = _whisper_align(wav_path, str(fake), "bogus-model")

    assert words is None
    assert error is not None
    assert "pip install -U openai-whisper" in error
    assert "bogus-model" in error


def test_run_align_no_collage_work_prints_skipped(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "run_plain"
    run_dir.mkdir()
    script = {
        "title": "Plain",
        "segments": [
            {"segment_id": "seg01", "visual_type": "scene", "narration_text": "hi"}
        ],
    }
    script_path = run_dir / "script.json"
    script_path.write_text(json.dumps(script))

    run_align(script_path, run_dir)

    out = capsys.readouterr().out
    assert json.loads(out.strip())["skipped"] is True
    assert not (run_dir / "audio" / "alignment.json").exists()
