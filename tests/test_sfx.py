"""Tests for the procedural SFX library + narration mixing (src/audio/sfx.py)."""

import json
import subprocess
import wave
from pathlib import Path

import pytest

from src.audio.sfx import SOUNDS, mix_sfx_into_narration, run_sfx, write_sound


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True, text=True)


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


def test_every_sound_synthesizes(tmp_path: Path) -> None:
    for name in SOUNDS:
        out = write_sound(name, tmp_path / f"{name}.wav")
        assert out.exists() and out.stat().st_size > 1000
        assert _wav_duration(out) > 0.5


def test_synthesis_is_deterministic(tmp_path: Path) -> None:
    a = write_sound("cannon_boom", tmp_path / "a.wav").read_bytes()
    b = write_sound("cannon_boom", tmp_path / "b.wav").read_bytes()
    assert a == b


def test_unknown_sound_is_hard_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown sfx sound"):
        write_sound("laser_blast", tmp_path / "x.wav")


# ---------------------------------------------------------------------------
# Mixing
# ---------------------------------------------------------------------------


def test_mix_keeps_narration_duration(tmp_path: Path) -> None:
    narration = tmp_path / "narration.wav"
    _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-ar", "48000", "-ac", "1", str(narration)])
    boom = write_sound("cannon_boom", tmp_path / "boom.wav")
    out = tmp_path / "mixed.wav"
    mix_sfx_into_narration(narration, [(boom, 1.0, -10.0), (boom, 3.0, -14.0)], out)
    assert abs(_wav_duration(out) - 4.0) < 0.05


# ---------------------------------------------------------------------------
# run_sfx command semantics
# ---------------------------------------------------------------------------


def _write_script(tmp_path: Path, sfx: list[dict]) -> Path:
    script = {
        "title": "t",
        "total_estimated_duration_seconds": 4,
        "segments": [
            {
                "segment_id": "seg01",
                "section_title": "s",
                "narration_text": "the cannon fired at the walls",
                "estimated_duration_seconds": 4,
                "animation_cues": [],
                "visual_engine": "collage",
                "visual_type": "diagram",
                "sfx": sfx,
            }
        ],
    }
    p = tmp_path / "script.json"
    p.write_text(json.dumps(script))
    return p


def _make_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    audio = run_dir / "audio"
    audio.mkdir(parents=True)
    wav = audio / "audio_seg01.wav"
    _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-ar", "48000", "-ac", "1", str(wav)])
    (audio / "audio_manifest.json").write_text(
        json.dumps({"seg01": {"audio_path": str(wav), "duration_seconds": 4.0}})
    )
    (audio / "alignment.json").write_text(
        json.dumps(
            {
                "seg01": {
                    "duration_seconds": 4.0,
                    "source": "whisper",
                    "words": [
                        {"w": " the", "start": 0.0, "end": 0.3},
                        {"w": " cannon", "start": 0.3, "end": 0.9},
                        {"w": " fired", "start": 0.9, "end": 1.4},
                    ],
                }
            }
        )
    )
    return run_dir


def test_run_sfx_noop_without_cues(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    script = _write_script(tmp_path, sfx=[])
    run_sfx(script, _make_run(tmp_path))
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["skipped"] is True


def test_run_sfx_mixes_and_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    script = _write_script(
        tmp_path,
        sfx=[
            {"sound": "cannon_boom", "at_word": "cannon", "gain_db": -10},
            {"sound": "ocean_waves", "at_frac": 0.0, "gain_db": -18},
        ],
    )
    run_dir = _make_run(tmp_path)
    wav = run_dir / "audio" / "audio_seg01.wav"

    run_sfx(script, run_dir)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["mixed"] == ["seg01"]
    assert abs(_wav_duration(wav) - 4.0) < 0.05
    first_bytes = wav.read_bytes()

    # Second run must not double-mix.
    run_sfx(script, run_dir)
    out2 = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out2["already_applied"] == ["seg01"]
    assert wav.read_bytes() == first_bytes


def test_run_sfx_preserves_narration_mtime(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Mixing must not trip the collage alignment-staleness (mtime) check."""
    script = _write_script(tmp_path, sfx=[{"sound": "cannon_boom", "at_frac": 0.2}])
    run_dir = _make_run(tmp_path)
    wav = run_dir / "audio" / "audio_seg01.wav"
    before = wav.stat().st_mtime

    run_sfx(script, run_dir)
    capsys.readouterr()

    assert wav.stat().st_mtime == pytest.approx(before, abs=0.01)


def test_run_sfx_unknown_sound_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    script = _write_script(tmp_path, sfx=[{"sound": "laser_blast", "at_frac": 0.5}])
    run_dir = _make_run(tmp_path)
    with pytest.raises(SystemExit):
        run_sfx(script, run_dir)
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "unknown sfx sound" in out["errors"]["seg01"]
