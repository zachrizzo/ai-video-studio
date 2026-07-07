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
from pathlib import Path

import pytest

from src.alignment.align import run_align
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


def _fake_whisper(tmp_path: Path) -> Path:
    """A shell script emulating whisper: writes a whisper-style JSON to --output_dir."""
    script = tmp_path / "fake_whisper.sh"
    script.write_text(
        """#!/usr/bin/env bash
set -e
out_dir=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output_dir) out_dir="$2"; shift 2 ;;
    *) shift ;;
  esac
done
cat > "$out_dir/seg01.json" <<'JSON'
{
  "text": "Clouds drift over the valley",
  "segments": [
    {"id": 0, "text": "Clouds drift over the valley", "words": [
      {"word": "Clouds", "start": 0.0, "end": 0.2},
      {"word": "drift", "start": 0.2, "end": 0.4},
      {"word": "over", "start": 0.4, "end": 0.6},
      {"word": "the", "start": 0.6, "end": 0.7},
      {"word": "valley", "start": 0.7, "end": 1.0}
    ]}
  ]
}
JSON
"""
    )
    script.chmod(0o755)
    return script


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
