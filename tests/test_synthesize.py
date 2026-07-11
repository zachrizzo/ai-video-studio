"""Regression tests for cmd_synthesize resumability and failure semantics.

cmd_synthesize used to re-roll EVERY segment's audio on every invocation (the
head of the video/audio desync chain: new audio durations under skipped old
clips) and exited 0 even when TTS failed, writing fake estimated-duration
manifest entries that let downstream steps pretend audio existed. It also
picked its voice from whatever env/config happened to be set, so a resumed run
could switch voices mid-video.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import pipeline
from src.pipeline import cmd_synthesize


def _script(tmp_path: Path, n: int = 2) -> Path:
    segments = [
        {
            "segment_id": f"seg_{i:03d}",
            "section_title": "S",
            "narration_text": f"Narration number {i}.",
            "estimated_duration_seconds": 3.0,
            "animation_cues": [],
            "visual_engine": "html",
        }
        for i in range(1, n + 1)
    ]
    script = {
        "title": "Synth Test",
        "total_estimated_duration_seconds": 3.0 * n,
        "segments": segments,
    }
    path = tmp_path / "script.json"
    path.write_text(json.dumps(script))
    return path


def _last_json(capsys) -> dict:
    out = capsys.readouterr().out
    return json.loads([line for line in out.splitlines() if line.startswith("{")][-1])


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "PTV_VOICE_PROVIDER",
        "PTV_QWEN_TTS_SPEAKER",
        "PTV_QWEN_TTS_LANGUAGE",
        "PTV_VOICEBOX_PROFILE",
        "PTV_VOICEBOX_LANGUAGE",
        "PTV_AUDIO_FORCE",
        "PTV_ELEVENLABS_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_tts(monkeypatch):
    """Fake Qwen TTS + audio post-processing; records generate_speech calls."""
    calls: list[dict] = []

    def fake_generate(text, output_path, speaker, language, instruct, model_size):
        calls.append(
            {"text": text, "speaker": speaker, "language": language, "instruct": instruct}
        )
        Path(output_path).write_bytes(b"fake-wav")
        return {"success": True, "error": None}

    import src.studio.tts as tts_mod

    monkeypatch.setattr(tts_mod, "generate_speech", fake_generate)
    monkeypatch.setattr(pipeline, "_normalize_audio_file", lambda path, lufs: None)
    monkeypatch.setattr(pipeline, "_probe_media_duration", lambda path: 3.0)
    return calls


def test_success_writes_manifest_summary_and_voice(tmp_path, capsys, fake_tts):
    script = _script(tmp_path)
    out = tmp_path / "audio"
    cmd_synthesize(str(script), str(out))

    payload = _last_json(capsys)
    assert payload["synthesized"] == ["seg_001", "seg_002"]
    assert payload["skipped"] == []
    assert payload["failed"] == []

    manifest = json.loads((out / "audio_manifest.json").read_text())
    assert manifest["seg_001"]["duration_seconds"] == 3.0
    assert manifest["seg_001"]["qa_issues"] == []
    # Effective voice settings persist for resumes; default speaker is serena
    # (aligned across config, the tts tool, and presets).
    assert manifest["_voice"] == {
        "provider": "qwen",
        "speaker": "serena",
        "language": "english",
    }


def test_rerun_skips_existing_good_takes(tmp_path, capsys, fake_tts):
    script = _script(tmp_path)
    out = tmp_path / "audio"
    cmd_synthesize(str(script), str(out))
    del fake_tts[:]

    cmd_synthesize(str(script), str(out))

    payload = _last_json(capsys)
    assert payload["skipped"] == ["seg_001", "seg_002"]
    assert payload["synthesized"] == []
    assert fake_tts == [], "a re-run must never re-roll approved audio"


def test_audio_force_regenerates_everything(tmp_path, capsys, fake_tts, monkeypatch):
    script = _script(tmp_path)
    out = tmp_path / "audio"
    cmd_synthesize(str(script), str(out))
    del fake_tts[:]

    monkeypatch.setenv("PTV_AUDIO_FORCE", "1")
    cmd_synthesize(str(script), str(out))

    payload = _last_json(capsys)
    assert payload["skipped"] == []
    assert payload["synthesized"] == ["seg_001", "seg_002"]
    assert len(fake_tts) == 2


def test_missing_wav_is_not_a_reusable_take(tmp_path, capsys, fake_tts):
    script = _script(tmp_path, n=1)
    out = tmp_path / "audio"
    cmd_synthesize(str(script), str(out))
    (out / "audio_seg_001.wav").unlink()
    del fake_tts[:]

    cmd_synthesize(str(script), str(out))

    payload = _last_json(capsys)
    assert payload["synthesized"] == ["seg_001"]
    assert payload["skipped"] == []


def test_silence_placeholders_are_never_reused_as_takes(tmp_path, capsys, fake_tts):
    script = _script(tmp_path, n=1)
    out = tmp_path / "audio"
    out.mkdir()
    silent = out / "audio_seg_001.mp3"
    silent.write_bytes(b"silence")
    (out / "audio_manifest.json").write_text(
        json.dumps(
            {
                "seg_001": {
                    "audio_path": str(silent),
                    "duration_seconds": 3.0,
                    "silent": True,
                }
            }
        )
    )

    cmd_synthesize(str(script), str(out))

    payload = _last_json(capsys)
    assert payload["synthesized"] == ["seg_001"]
    assert payload["skipped"] == []


def test_failed_tts_exits_2_and_writes_honest_manifest(tmp_path, capsys, monkeypatch):
    import src.studio.tts as tts_mod

    monkeypatch.setattr(
        tts_mod,
        "generate_speech",
        lambda **kwargs: {"success": False, "error": "qwen exploded"},
    )

    script = _script(tmp_path, n=1)
    out = tmp_path / "audio"
    with pytest.raises(SystemExit) as exc_info:
        cmd_synthesize(str(script), str(out))
    assert exc_info.value.code == 2

    payload = _last_json(capsys)
    assert payload["failed"] == [{"segment_id": "seg_001", "error": "qwen exploded"}]

    entry = json.loads((out / "audio_manifest.json").read_text())["seg_001"]
    assert entry["failed"] is True
    assert entry["duration_seconds"] == 0, "no fake estimated duration for failed takes"
    assert "qwen exploded" in entry["error"]


def test_failed_manifest_entry_is_retried_not_skipped(tmp_path, capsys, fake_tts):
    script = _script(tmp_path, n=1)
    out = tmp_path / "audio"
    out.mkdir()
    wav = out / "audio_seg_001.wav"
    wav.write_bytes(b"partial-garbage")
    (out / "audio_manifest.json").write_text(
        json.dumps(
            {
                "seg_001": {
                    "audio_path": str(wav),
                    "duration_seconds": 0,
                    "failed": True,
                    "error": "TTS failed",
                    "qa_issues": ["TTS failed"],
                }
            }
        )
    )

    cmd_synthesize(str(script), str(out))

    payload = _last_json(capsys)
    assert payload["synthesized"] == ["seg_001"]
    assert payload["skipped"] == []


def test_qa_issue_after_retry_exits_2_but_keeps_real_take(tmp_path, capsys, fake_tts, monkeypatch):
    # Probed duration 30s vs 3s estimate = drift on both attempts.
    monkeypatch.setattr(pipeline, "_probe_media_duration", lambda path: 30.0)

    script = _script(tmp_path, n=1)
    out = tmp_path / "audio"
    with pytest.raises(SystemExit) as exc_info:
        cmd_synthesize(str(script), str(out))
    assert exc_info.value.code == 2

    payload = _last_json(capsys)
    assert payload["failed"] == []
    assert payload["qa_flagged"][0]["segment_id"] == "seg_001"
    assert len(fake_tts) == 2, "drift must trigger exactly one stricter retry"

    entry = json.loads((out / "audio_manifest.json").read_text())["seg_001"]
    assert entry["qa_issues"], "the drift issue must be visible in the manifest"
    assert entry["duration_seconds"] == 30.0


def test_resume_reuses_persisted_voice_when_env_unset(tmp_path, capsys, fake_tts, monkeypatch):
    script = _script(tmp_path, n=1)
    out = tmp_path / "audio"
    monkeypatch.setenv("PTV_QWEN_TTS_SPEAKER", "dylan")
    cmd_synthesize(str(script), str(out))
    assert fake_tts[0]["speaker"] == "dylan"

    # Resume with no explicit override: the persisted voice must beat the
    # config default (serena). Force so the segment is actually re-rolled.
    monkeypatch.delenv("PTV_QWEN_TTS_SPEAKER")
    monkeypatch.setenv("PTV_AUDIO_FORCE", "1")
    del fake_tts[:]
    cmd_synthesize(str(script), str(out))

    assert fake_tts[0]["speaker"] == "dylan"
    manifest = json.loads((out / "audio_manifest.json").read_text())
    assert manifest["_voice"]["speaker"] == "dylan"


def test_explicit_voice_change_regenerates_existing_takes(tmp_path, capsys, fake_tts, monkeypatch):
    script = _script(tmp_path, n=1)
    out = tmp_path / "audio"
    monkeypatch.setenv("PTV_QWEN_TTS_SPEAKER", "dylan")
    cmd_synthesize(str(script), str(out))
    del fake_tts[:]

    # Explicit new speaker: the old good take is stale — skipping it would
    # ship a video that switches voices mid-run.
    monkeypatch.setenv("PTV_QWEN_TTS_SPEAKER", "eric")
    cmd_synthesize(str(script), str(out))

    payload = _last_json(capsys)
    assert payload["skipped"] == []
    assert payload["synthesized"] == ["seg_001"]
    assert fake_tts[0]["speaker"] == "eric"
    manifest = json.loads((out / "audio_manifest.json").read_text())
    assert manifest["_voice"]["speaker"] == "eric"
