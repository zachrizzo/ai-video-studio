"""Voicebox TTS provider — REST client for the Voicebox app (voicebox.sh).

Voicebox (github.com/jamiepine/voicebox) is a local-first, open-source voice
studio exposing a REST API on http://127.0.0.1:17493 while the app is running.
Voice + engine selection (Qwen3-TTS, Chatterbox Turbo/Multilingual, Kokoro,
LuxTTS, TADA, ...) lives on a *profile* created in the Voicebox UI; this
client only references profiles by name or id.

API surface used (committed spec: repo docs/openapi.json — NOTE it may lag the
live app; the live spec is served at {url}/docs while running. The committed
spec still shows Qwen-era request constraints like language ``^(en|zh)$``;
send only fields we need and parse responses tolerantly):

    GET  /health                     -> 200 when up
    GET  /profiles                   -> list of {id, name, ...}
    POST /generate                   -> {"profile_id", "text" (1..5000),
                                         "language", "seed"?}
                                      -> {id, audio_path, duration, ...}
                                         (synchronous)
    GET  /audio/{generation_id}      -> the audio bytes

NO-FALLBACK RULE (docs/collage/CONTRACTS.md spirit): when the server is
unreachable or a profile cannot be resolved, functions return/raise hard,
actionable errors ("Launch the Voicebox app; expected at {url}") — callers
must never silently fall back to another TTS provider.

FROZEN INTERFACE (Phase 0) — implementations must keep these signatures:
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:17493"

_LAUNCH_HINT = (
    "Launch the Voicebox app (download: https://voicebox.sh, or `docker compose "
    "up` from github.com/jamiepine/voicebox) and leave it running, or point "
    "PTV_VOICEBOX_URL at the right host."
)


def voicebox_health(url: str = DEFAULT_URL, timeout: float = 3.0) -> bool:
    """True when a Voicebox server answers GET /health at ``url``."""
    raise NotImplementedError  # Subagent A

def list_profiles(url: str = DEFAULT_URL, timeout: float = 5.0) -> list[dict]:
    """GET /profiles. Raises RuntimeError with _LAUNCH_HINT when unreachable."""
    raise NotImplementedError  # Subagent A

def resolve_profile(url: str, name_or_id: str) -> str:
    """Resolve a profile NAME (case-insensitive) or id -> profile_id.

    Mirrors Voicebox's own /speak precedence. Raises RuntimeError listing the
    available profile names when no match exists.
    """
    raise NotImplementedError  # Subagent A

def generate_speech_voicebox(
    text: str,
    output_path: Path,
    profile: str,
    language: str = "en",
    seed: int | None = None,
    url: str = DEFAULT_URL,
    timeout: float = 300.0,
) -> dict:
    """Generate speech via Voicebox and write a 48 kHz mono wav to output_path.

    POST /generate (synchronous), then GET /audio/{id}; transcode to 48k mono
    wav with ffmpeg when the returned audio isn't already wav.

    Returns {"success": bool, "output_path": str, "error": str | None,
             "duration": float | None} — the same success/error shape as
    src/studio/tts.py:generate_speech so cmd_synthesize can dispatch on
    provider without reshaping results. Never raises for server/profile
    problems; returns success=False with an actionable error instead.
    """
    raise NotImplementedError  # Subagent A
