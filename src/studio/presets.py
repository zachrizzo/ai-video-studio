"""Video generation presets — stored as JSON on disk."""

import json

from src.studio import config


def split_resolution(resolution: str) -> tuple[str, str] | None:
    """Split a "WIDTHxHEIGHT" string into normalized (width_str, height_str),
    or None if malformed. Shared by agent_tools.videogen_tool and preset_env
    so the two can't drift apart on how a resolution string maps to LTX env
    vars.
    """
    try:
        width_str, height_str = resolution.lower().split("x", 1)
        return str(int(width_str)), str(int(height_str))
    except ValueError:
        return None

DEFAULT_PRESETS = {
    # First preset is the default the UI selects on load — the collage
    # documentary style is the house default (scripted, word-synced motion).
    "historical_epic": {
        "name": "Historical Epic",
        "description": "Archival collage documentary for history — battles, fleets, empires — with period sound effects",
        "style_prompt": "19th century oil painting, muted archival palette, soft natural light, aged canvas texture, cream and terracotta tones, dramatic historical scene",
        "video_length_minutes": 6,
        "voice_speaker": "eric",
        "voice_language": "english",
        "video_provider": "ltx",
        "narration_style": "Brisk, gripping documentary narrator. Tight pacing — no dead air, short punchy sentences driving forward, momentum like a thriller. Vivid and cinematic, never slow.",
        "tts_provider": "voicebox",
        "voicebox_profile": "Eric",
        "style_pack": "anthropic_docu",
        "default_visual_engine": "collage",
        "video_speed": 1.25,
        "visual_pacing": (
            "Constant purposeful motion — the camera never stops drifting; "
            "depth layers (background/midground/foreground) move at different "
            "rates; something new enters, exits, or transforms every 2-4 "
            "seconds; dates, names, arrows, and map changes stamp in on the "
            "exact narrated word. No composition holds unchanged longer than "
            "4 seconds; 4-8 timed element events per scene minimum."
        ),
        "sfx_style": (
            "Layer battle sfx under narration: cannon_boom/musket_volley pinned at_word "
            "(-8..-12 dB), war_drums/ocean_waves/fire_crackle/wind_howl/bell_toll as "
            "at_frac 0 ambience (-16..-20 dB). Most segments should carry at least one cue."
        ),
    },
    "stick_figure_history": {
        "name": "Stick Figure History",
        "description": "OverSimplified-style stick figure illustrations with comedic narration",
        "style_prompt": "Simple stick figure illustration, minimalist 2D style, muted earth tones, comedic oversimplified history style, flat colors, clean lines",
        "video_length_minutes": 1,
        "voice_speaker": "dylan",
        "voice_language": "english",
        "video_provider": "ltx",
        "narration_style": "Fast-paced, witty, sarcastic humor. Short punchy sentences. Use modern slang to describe historical events. Make jokes.",
        "tts_provider": "voicebox",
        "voicebox_profile": "Eric",
    },
    "cinematic_documentary": {
        "name": "Cinematic Documentary",
        "description": "Photorealistic cinematic shots with dramatic narration",
        "style_prompt": "Photorealistic cinematic still, dramatic lighting, 8k quality, film grain, wide angle shot",
        "video_length_minutes": 3,
        "voice_speaker": "eric",
        "voice_language": "english",
        "video_provider": "ltx",
        "narration_style": "Deep, authoritative documentary narrator voice. Formal but engaging. Build tension and drama. Like David Attenborough meets Ken Burns.",
        "tts_provider": "voicebox",
        "voicebox_profile": "Eric",
    },
    "educational_explainer": {
        "name": "Educational Explainer",
        "description": "Clean diagrams and illustrations for learning",
        "style_prompt": "Clean educational illustration, infographic style, bright colors, white background, labeled diagrams, modern flat design",
        "video_length_minutes": 5,
        "voice_speaker": "serena",
        "voice_language": "english",
        "video_provider": "ltx",
        "narration_style": "Friendly, clear teacher voice. Break complex topics into simple parts. Use analogies. Pause for emphasis on key points.",
        "tts_provider": "voicebox",
        "voicebox_profile": "Eric",
    },
    "anime_style": {
        "name": "Anime Style",
        "description": "Anime-inspired illustrations with energetic narration",
        "style_prompt": "Anime illustration style, vibrant colors, dramatic poses, manga-inspired, detailed characters, dynamic composition, cel shading",
        "video_length_minutes": 2,
        "voice_speaker": "vivian",
        "voice_language": "english",
        "video_provider": "ltx",
        "narration_style": "Energetic and dramatic. Use exclamations. Build hype. Describe action scenes vividly.",
        "tts_provider": "voicebox",
        "voicebox_profile": "Eric",
    },
    "anthropic_documentary": {
        "name": "Anthropic Documentary",
        "description": "Mixed-media collage in Anthropic's design language — archival paintings, torn-paper labels, calm parallax",
        "style_prompt": "19th century oil painting, muted archival palette, soft natural light, aged canvas texture, cream and terracotta tones",
        "video_length_minutes": 2,
        "voice_speaker": "eric",
        "voice_language": "english",
        "video_provider": "ltx",
        "narration_style": "Calm, thoughtful documentary narrator. Measured pacing with deliberate pauses. Curious and precise, like a nature documentary about a mind.",
        "tts_provider": "voicebox",
        "voicebox_profile": "Eric",
        "style_pack": "anthropic_docu",
        "default_visual_engine": "collage",
        "sfx_style": (
            "Subtle procedural sfx under narration: ambience (ocean_waves, wind_howl, "
            "fire_crackle, war_drums) at_frac 0 at -16..-20 dB; hits (cannon_boom, "
            "musket_volley, bell_toll) pinned at_word at -8..-12 dB."
        ),
    },
    "dark_horror": {
        "name": "Dark & Horror",
        "description": "Dark atmospheric scenes with eerie narration",
        "style_prompt": "Dark atmospheric digital painting, horror style, muted desaturated colors, fog, shadows, ominous mood, gothic architecture",
        "video_length_minutes": 2,
        "voice_speaker": "aiden",
        "voice_language": "english",
        "video_provider": "ltx",
        "narration_style": "Slow, ominous whisper. Build dread. Use pauses. Describe sounds and sensations. Creepy atmosphere.",
        "tts_provider": "voicebox",
        "voicebox_profile": "Eric",
    },
}


def _load_all() -> dict:
    presets_file = config.presets_file()
    if presets_file.exists():
        try:
            return json.loads(presets_file.read_text())
        except Exception:
            pass
    return {}


def _save_all(data: dict) -> None:
    presets_file = config.presets_file()
    presets_file.parent.mkdir(parents=True, exist_ok=True)
    presets_file.write_text(json.dumps(data, indent=2))


def list_presets() -> list[dict]:
    custom = _load_all()
    result = []
    for key, preset in DEFAULT_PRESETS.items():
        if key in custom:
            result.append({"id": key, "builtin": False, **preset, **custom[key]})
        else:
            result.append({"id": key, "builtin": True, **preset})
    for key, preset in custom.items():
        if key in DEFAULT_PRESETS:
            continue
        result.append({"id": key, "builtin": False, **preset})
    return result


def get_preset(preset_id: str) -> dict | None:
    custom = _load_all()
    if preset_id in custom:
        base = DEFAULT_PRESETS.get(preset_id, {})
        return {"id": preset_id, "builtin": False, **base, **custom[preset_id]}
    if preset_id in DEFAULT_PRESETS:
        return {"id": preset_id, "builtin": True, **DEFAULT_PRESETS[preset_id]}
    return None


def save_preset(preset_id: str, data: dict) -> dict:
    custom = _load_all()
    custom[preset_id] = data
    _save_all(custom)
    return {"id": preset_id, "builtin": False, **data}


def delete_preset(preset_id: str) -> bool:
    custom = _load_all()
    if preset_id in custom:
        del custom[preset_id]
        _save_all(custom)
        return True
    return False


# Preset key -> PTV_* env var, for values that just need str()/bool-string
# conversion. voice_language deliberately has no entry: presets store a full
# word ("english") while PTV_VOICEBOX_LANGUAGE wants an ISO code ("en"), so
# mapping it there would silently break Voicebox synthesis. ltx_resolution is
# handled separately (splits into two env vars via split_resolution).
_ENV_STR_KEYS = {
    "tts_provider": "PTV_VOICE_PROVIDER",
    "voicebox_profile": "PTV_VOICEBOX_PROFILE",
    "voice_speaker": "PTV_QWEN_TTS_SPEAKER",
    "voice_language": "PTV_QWEN_TTS_LANGUAGE",
    "qwen_model_size": "PTV_QWEN_TTS_MODEL_SIZE",
    "image_model": "PTV_IMAGE_MODEL",
    "video_provider": "PTV_VIDEO_PROVIDER",
}
# int-typed PipelineConfig fields: coerce through int() so a hand-edited
# preset's "40.0" can't reach the pipeline subprocess and fail its int parse
# (mirrors agent_tools.py's imagegen_tool/videogen_tool treatment of the same
# knobs).
_ENV_INT_KEYS = {
    "image_steps": "PTV_IMAGE_STEPS",
    "image_quantize": "PTV_IMAGE_QUANTIZE",
    "ltx_steps": "PTV_LTX_STEPS",
}
_ENV_FLOAT_KEYS = {
    "ltx_clip_seconds": "PTV_LTX_CLIP_SECONDS",
    "ltx_cfg_scale": "PTV_LTX_CFG_SCALE",
    "ltx_stg_scale": "PTV_LTX_STG_SCALE",
    "kenburns_zoom": "PTV_KENBURNS_ZOOM",
    # Final composite playback speed (composite reads PTV_VIDEO_SPEED when no
    # explicit --speed/produce speed is passed). Explicit produce speed wins.
    "video_speed": "PTV_VIDEO_SPEED",
}
_ENV_BOOL_KEYS = {
    "ltx_prefer_extend": "PTV_LTX_PREFER_EXTEND",
    "video_fallback_to_kenburns": "PTV_VIDEO_FALLBACK_TO_KENBURNS",
}


def preset_env(preset: dict) -> dict[str, str]:
    """Map a resolved preset dict (as returned by get_preset/list_presets) to
    the PTV_* env vars that steer production. Keys absent or None in the
    preset are skipped so they fall through to the pipeline's own defaults.
    """
    env: dict[str, str] = {}
    for key, var in _ENV_STR_KEYS.items():
        value = preset.get(key)
        if value is not None:
            env[var] = str(value)
    for key, var in _ENV_INT_KEYS.items():
        value = preset.get(key)
        if value is not None:
            env[var] = str(int(value))
    for key, var in _ENV_FLOAT_KEYS.items():
        value = preset.get(key)
        if value is not None:
            env[var] = str(value)
    for key, var in _ENV_BOOL_KEYS.items():
        value = preset.get(key)
        if value is not None:
            env[var] = "true" if value else "false"
    resolution = preset.get("ltx_resolution")
    if resolution:
        split = split_resolution(resolution)
        if split is not None:
            width_str, height_str = split
            env["PTV_LTX_GEN_WIDTH"] = width_str
            env["PTV_LTX_GEN_HEIGHT"] = height_str
    return env
