"""Regression: PresetSaveRequest must declare every field a custom preset can
carry, or api_save_preset's req.model_dump() silently drops unknown ones on
save — the exact bug class hit earlier with style_pack/tts_provider/etc."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def presets_file(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "presets.json"
    monkeypatch.setenv("STUDIO_PRESETS_FILE", str(path))
    return path


def test_saved_preset_keeps_generation_settings(presets_file: Path) -> None:
    from starlette.testclient import TestClient

    from src.studio.server import app

    client = TestClient(app)

    body = {
        "id": "custom_test",
        "name": "Custom Test",
        "style_prompt": "test",
        "narration_style": "test",
        "image_model": "schnell",
        "image_steps": 6,
        "image_quantize": 8,
        "ltx_steps": 40,
        "ltx_resolution": "704x448",
        "ltx_clip_seconds": 4.0,
        "ltx_cfg_scale": 3.5,
        "ltx_stg_scale": 1.2,
        "ltx_prefer_extend": True,
        "video_fallback_to_kenburns": False,
        "kenburns_zoom": 1.2,
        "qwen_model_size": "1.7B",
    }
    r = client.post("/api/presets", json=body)
    assert r.status_code == 200

    r = client.get("/api/presets/custom_test")
    assert r.status_code == 200
    saved = r.json()
    for key, value in body.items():
        if key == "id":
            continue
        assert saved[key] == value, f"{key} was dropped or changed on save"


def test_partial_save_of_builtin_preset_preserves_untouched_fields(presets_file: Path) -> None:
    """Regression: editing a single field on a built-in preset used to
    replace the whole custom entry with a full PresetSaveRequest dump, which
    silently reset every other field to that request model's hardcoded
    defaults (video_length_minutes=2, video_provider="kenburns", etc.)
    instead of leaving the built-in preset's actual values in place."""
    from starlette.testclient import TestClient

    from src.studio.server import app

    client = TestClient(app)

    # cinematic_documentary's built-in values: video_length_minutes=3,
    # voice_speaker="eric", video_provider="ltx" — none of which match
    # PresetSaveRequest's hardcoded defaults (2, "serena", "kenburns").
    body = {
        "id": "cinematic_documentary",
        "name": "Cinematic Documentary",
        "voice_speaker": "dylan",
    }
    r = client.post("/api/presets", json=body)
    assert r.status_code == 200

    r = client.get("/api/presets/cinematic_documentary")
    assert r.status_code == 200
    saved = r.json()
    assert saved["voice_speaker"] == "dylan"
    assert saved["video_length_minutes"] == 3
    assert saved["video_provider"] == "ltx"
    assert saved["voice_language"] == "english"
    assert saved["tts_provider"] == "voicebox"
    assert saved["voicebox_profile"] == "Eric"
