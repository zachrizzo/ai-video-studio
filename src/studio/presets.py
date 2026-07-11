"""Video generation presets — stored as JSON on disk."""

import json

from src.studio import config

DEFAULT_PRESETS = {
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
        "voicebox_profile": "Narrator",
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
        "voicebox_profile": "Narrator",
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
        "voicebox_profile": "Narrator",
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
    "historical_epic": {
        "name": "Historical Epic",
        "description": "Archival collage documentary for history — battles, fleets, empires — with period sound effects",
        "style_prompt": "19th century oil painting, muted archival palette, soft natural light, aged canvas texture, cream and terracotta tones, dramatic historical scene",
        "video_length_minutes": 6,
        "voice_speaker": "eric",
        "voice_language": "english",
        "video_provider": "ltx",
        "narration_style": "Grave, cinematic documentary narrator. Long deliberate pauses. History told like a slow-burning epic — intimate, ominous, humane.",
        "tts_provider": "voicebox",
        "voicebox_profile": "Narrator",
        "style_pack": "anthropic_docu",
        "default_visual_engine": "collage",
        "sfx_style": (
            "Layer battle sfx under narration: cannon_boom/musket_volley pinned at_word "
            "(-8..-12 dB), war_drums/ocean_waves/fire_crackle/wind_howl/bell_toll as "
            "at_frac 0 ambience (-16..-20 dB). Most segments should carry at least one cue."
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
        "voicebox_profile": "Narrator",
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
