"""Tests for presets.preset_env — the preset -> PTV_* env var mapping used
by deterministic production. Every emitted env var name must match an actual
PipelineConfig field, so a future rename of a config field breaks this test
loudly instead of silently doing nothing at production time."""

from __future__ import annotations

from src.config import PipelineConfig
from src.studio.presets import preset_env, split_resolution


def test_preset_env_keys_round_trip_against_pipeline_config() -> None:
    # Instantiating (ignoring any local .env) is a sanity check that the
    # config itself still loads; the field-name check below is what matters.
    cfg = PipelineConfig(_env_file=None)
    fields = set(type(cfg).model_fields.keys())
    field_by_env_name = {f"PTV_{name.upper()}": name for name in fields}

    full_preset = {
        "tts_provider": "voicebox",
        "voicebox_profile": "Narrator",
        "voice_speaker": "eric",
        "voice_language": "english",
        "qwen_model_size": "1.7B",
        "image_model": "schnell",
        "image_steps": 6,
        "image_quantize": 8,
        "video_provider": "ltx",
        "ltx_steps": 40,
        "ltx_resolution": "704x448",
        "ltx_clip_seconds": 4.0,
        "ltx_cfg_scale": 3.5,
        "ltx_stg_scale": 1.2,
        "ltx_prefer_extend": True,
        "video_fallback_to_kenburns": False,
        "kenburns_zoom": 1.2,
    }
    env = preset_env(full_preset)
    assert env, "preset_env produced nothing for a fully-populated preset"
    for env_var in env:
        assert env_var in field_by_env_name, (
            f"{env_var} does not map to any PipelineConfig field — "
            "check for a renamed/removed config field"
        )


def test_preset_env_splits_resolution() -> None:
    env = preset_env({"ltx_resolution": "704x448"})
    assert env["PTV_LTX_GEN_WIDTH"] == "704"
    assert env["PTV_LTX_GEN_HEIGHT"] == "448"


def test_preset_env_skips_malformed_resolution_without_raising() -> None:
    env = preset_env({"ltx_resolution": "not-a-resolution"})
    assert "PTV_LTX_GEN_WIDTH" not in env
    assert "PTV_LTX_GEN_HEIGHT" not in env


def test_split_resolution_helper() -> None:
    assert split_resolution("704x448") == ("704", "448")
    assert split_resolution("704X448") == ("704", "448")
    assert split_resolution("bogus") is None
    assert split_resolution("704xabc") is None


def test_preset_env_empty_for_preset_with_no_optional_fields() -> None:
    preset = {
        "id": "x",
        "name": "X",
        "builtin": True,
        "style_prompt": "",
        "narration_style": "",
        "video_length_minutes": 2,
    }
    assert preset_env(preset) == {}


def test_preset_env_ignores_none_values() -> None:
    env = preset_env({"tts_provider": None, "image_steps": None, "ltx_resolution": None})
    assert env == {}


def test_preset_env_never_maps_voice_language_to_voicebox_language() -> None:
    """Deliberate omission: presets store voice_language as a full word
    ("english") but PTV_VOICEBOX_LANGUAGE wants an ISO code ("en"). Mapping
    one to the other would silently break Voicebox synthesis, so voice_language
    only ever maps to PTV_QWEN_TTS_LANGUAGE."""
    env = preset_env({"voice_language": "english"})
    assert "PTV_VOICEBOX_LANGUAGE" not in env
    assert env["PTV_QWEN_TTS_LANGUAGE"] == "english"


def test_preset_env_bool_fields_stringify_as_true_false() -> None:
    env = preset_env({"ltx_prefer_extend": False, "video_fallback_to_kenburns": True})
    assert env["PTV_LTX_PREFER_EXTEND"] == "false"
    assert env["PTV_VIDEO_FALLBACK_TO_KENBURNS"] == "true"
