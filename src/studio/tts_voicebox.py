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

import json
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:17493"

_LAUNCH_HINT = (
    "Launch the Voicebox app (download: https://voicebox.sh, or `docker compose "
    "up` from github.com/jamiepine/voicebox) and leave it running, or point "
    "PTV_VOICEBOX_URL at the right host."
)

# Errors that mean "server didn't answer the way we need" — every network /
# decode failure is folded into a hard, actionable error rather than a stack
# trace, so callers can surface the launch hint instead of crashing.
_NET_ERRORS = (urllib.error.URLError, OSError, ValueError)


# ---------------------------------------------------------------------------
# Tiny stdlib HTTP helpers (no third-party deps by design)
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def _get_json(url: str, timeout: float) -> object:
    return json.loads(_http_get(url, timeout).decode("utf-8"))


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _coerce_profiles(data: object) -> list[dict]:
    """Pull a profile list out of a tolerant /profiles response.

    Accepts a bare list, or a wrapper dict ({"profiles": [...]} / {"data": [...]}),
    and drops anything that isn't a dict so unknown shapes degrade gracefully.
    """
    if isinstance(data, dict):
        data = data.get("profiles", data.get("data", []))
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict)]


def _coerce_duration(value: object) -> float | None:
    try:
        seconds = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _ffprobe_duration(path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = proc.stdout.strip()
    try:
        return float(out) if out else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public API (frozen signatures)
# ---------------------------------------------------------------------------

def voicebox_health(url: str = DEFAULT_URL, timeout: float = 3.0) -> bool:
    """True when a Voicebox server answers GET /health at ``url``."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except _NET_ERRORS:
        return False


def list_profiles(url: str = DEFAULT_URL, timeout: float = 5.0) -> list[dict]:
    """GET /profiles. Raises RuntimeError with _LAUNCH_HINT when unreachable."""
    try:
        data = _get_json(url.rstrip("/") + "/profiles", timeout)
    except _NET_ERRORS as exc:
        raise RuntimeError(f"Voicebox not reachable at {url} ({exc}). {_LAUNCH_HINT}") from exc
    return _coerce_profiles(data)


def resolve_profile(url: str, name_or_id: str) -> str:
    """Resolve a profile NAME (case-insensitive) or id -> profile_id.

    Mirrors Voicebox's own /speak precedence. Raises RuntimeError listing the
    available profile names when no match exists.
    """
    profiles = list_profiles(url)
    wanted = name_or_id.strip()
    # id match wins over name (Voicebox /speak precedence), then case-insensitive name.
    for profile in profiles:
        if str(profile.get("id", "")) == wanted:
            return str(profile["id"])
    for profile in profiles:
        if str(profile.get("name", "")).strip().lower() == wanted.lower():
            return str(profile.get("id", ""))
    names = ", ".join(str(p.get("name", "?")) for p in profiles) or "(none)"
    raise RuntimeError(
        f"Voicebox profile {name_or_id!r} not found. Available profiles: {names}. "
        "Create it in the Voicebox app or set PTV_VOICEBOX_PROFILE to one above."
    )


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
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    base = url.rstrip("/")

    def fail(error: str) -> dict:
        return {"success": False, "output_path": str(output_path),
                "error": error, "duration": None}

    # Resolve name/id -> profile_id up front. resolve_profile already raises a
    # hard error listing available names (bad profile) or the launch hint
    # (server down); fold either into a success=False result. No fallback.
    try:
        profile_id = resolve_profile(url, profile)
    except RuntimeError as exc:
        return fail(str(exc))

    payload: dict = {"profile_id": profile_id, "text": text, "language": language}
    if seed is not None:
        payload["seed"] = seed

    try:
        gen = _post_json(base + "/generate", payload, timeout)
    except _NET_ERRORS as exc:
        return fail(f"Voicebox /generate failed at {url} ({exc}). {_LAUNCH_HINT}")

    gen_id = gen.get("id") or gen.get("generation_id")
    if not gen_id:
        return fail(f"Voicebox /generate returned no generation id: {gen!r}")

    try:
        audio_bytes = _http_get(f"{base}/audio/{gen_id}", timeout)
    except _NET_ERRORS as exc:
        return fail(f"Voicebox /audio/{gen_id} fetch failed ({exc}). {_LAUNCH_HINT}")
    if not audio_bytes:
        return fail(f"Voicebox returned empty audio for generation {gen_id}.")

    # The returned container/codec is profile-dependent (wav/mp3/flac/...), so
    # ALWAYS transcode to the pipeline's canonical 48 kHz mono wav. ffmpeg
    # sniffs the real format from content, so the temp suffix is irrelevant.
    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(tmp_path), "-ar", "48000", "-ac", "1", str(output_path)],
            capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            tail = (proc.stderr or proc.stdout or "")[-300:]
            return fail(f"ffmpeg failed to transcode Voicebox audio: {tail}")
    except subprocess.TimeoutExpired:
        return fail("ffmpeg timed out transcoding Voicebox audio.")
    finally:
        tmp_path.unlink(missing_ok=True)

    duration = _coerce_duration(gen.get("duration"))
    if duration is None:
        duration = _ffprobe_duration(output_path)

    return {"success": True, "output_path": str(output_path),
            "error": None, "duration": duration}
