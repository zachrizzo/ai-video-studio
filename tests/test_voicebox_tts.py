"""Tests for the Voicebox TTS provider (src/studio/tts_voicebox.py).

A threading http.server fake stands in for the Voicebox app: it serves
/health, /profiles, POST /generate (async — returns "generating"), GET
/generate/{id}/status (an SSE stream closing on a terminal status), and GET
/audio/{id}, so the client can be exercised end-to-end without the real app
running. This mirrors the real app's behavior (verified 2026-07-09), which
differs from the committed docs/openapi.json in exactly these ways. No
third-party HTTP deps — the client is stdlib urllib only, and so is this fake.
"""

from __future__ import annotations

import io
import json
import math
import socket
import struct
import subprocess
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src.studio.tts_voicebox import (
    _LAUNCH_HINT,
    generate_speech_voicebox,
    resolve_profile,
    voicebox_health,
)

PROFILES = [
    {"id": "p1", "name": "Narrator", "default_engine": "kokoro"},
    {"id": "p2", "name": "Morgan", "default_engine": "chatterbox_turbo"},
]

# Requests captured by the fake POST /generate handler, so tests can assert
# what the client actually sent (in particular: did it send "engine"?).
GENERATE_CALLS: list[dict] = []


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", *args], check=True, capture_output=True, text=True)


def _sine_wav_bytes(seconds: float = 1.0, rate: int = 8000, freq: float = 220.0) -> bytes:
    """A tiny valid mono 16-bit PCM wav in memory (ffmpeg reads it by content)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for i in range(int(seconds * rate)):
            frames += struct.pack("<h", int(32767 * 0.2 * math.sin(2 * math.pi * freq * i / rate)))
        w.writeframes(frames)
    return buf.getvalue()


_AUDIO = _sine_wav_bytes()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output quiet
        pass

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (http.server API)
        if self.path == "/health":
            self._send_json({"status": "ok"})
        elif self.path == "/profiles":
            self._send_json(PROFILES)
        elif self.path.startswith("/generate/") and self.path.endswith("/status"):
            # Real app: SSE stream that closes once a terminal status is
            # reached. One "generating" event then "completed" is enough to
            # exercise _parse_sse_last_event without a real multi-second wait.
            gen_id = self.path.split("/")[2]
            body = (
                f'data: {{"id": "{gen_id}", "status": "generating"}}\n\n'
                f'data: {{"id": "{gen_id}", "status": "completed", "duration": 1.0}}\n\n'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/audio/"):
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(_AUDIO)))
            self.end_headers()
            self.wfile.write(_AUDIO)
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode())
        except json.JSONDecodeError:
            payload = {}
        if self.path == "/generate":
            GENERATE_CALLS.append(payload)
            # Real app: /generate only kicks the job off — status "generating",
            # no audio yet. The client must drain /generate/{id}/status.
            self._send_json({
                "id": "gen-abc123",
                "audio_path": "",
                "duration": 0.0,
                "profile_id": payload.get("profile_id"),
                "seed": payload.get("seed"),
                "engine": payload.get("engine"),
                "status": "generating",
            })
        else:
            self._send_json({"error": "not found"}, status=404)


@pytest.fixture(autouse=True)
def _clear_generate_calls():
    GENERATE_CALLS.clear()
    yield
    GENERATE_CALLS.clear()


@pytest.fixture
def voicebox_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _closed_url() -> str:
    """A localhost URL with nothing listening (bind, read the port, release it)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def test_health_true_when_up(voicebox_server) -> None:
    assert voicebox_health(voicebox_server) is True


def test_health_false_when_down() -> None:
    assert voicebox_health(_closed_url(), timeout=1.0) is False


# ---------------------------------------------------------------------------
# resolve_profile
# ---------------------------------------------------------------------------


def test_resolve_by_exact_id(voicebox_server) -> None:
    assert resolve_profile(voicebox_server, "p2") == "p2"


def test_resolve_by_case_insensitive_name(voicebox_server) -> None:
    assert resolve_profile(voicebox_server, "narrator") == "p1"


def test_resolve_miss_lists_available_names(voicebox_server) -> None:
    with pytest.raises(RuntimeError) as exc:
        resolve_profile(voicebox_server, "Ghost")
    msg = str(exc.value)
    assert "Narrator" in msg and "Morgan" in msg


# ---------------------------------------------------------------------------
# generate_speech_voicebox
# ---------------------------------------------------------------------------


def test_generate_writes_48k_mono_wav(voicebox_server, tmp_path: Path) -> None:
    out = tmp_path / "seg.wav"
    result = generate_speech_voicebox(
        text="Hello world", output_path=out, profile="Narrator", url=voicebox_server,
    )
    assert result["success"] is True, result["error"]
    assert out.exists() and out.stat().st_size > 1000
    with wave.open(str(out), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnchannels() == 1
    assert result["duration"] == pytest.approx(1.0, abs=0.2)


def test_generate_derives_engine_from_profile_default(voicebox_server, tmp_path: Path) -> None:
    """The server does NOT infer engine from profile_id — omitting it silently
    defaults to "qwen" server-side — so the client must send the profile's
    own default_engine explicitly (regression for the real-app bug found
    2026-07-09: Kokoro-configured profiles were silently generated as qwen)."""
    out = tmp_path / "seg.wav"
    result = generate_speech_voicebox(
        text="Hello world", output_path=out, profile="Narrator", url=voicebox_server,
    )
    assert result["success"] is True, result["error"]
    assert len(GENERATE_CALLS) == 1
    assert GENERATE_CALLS[0]["engine"] == "kokoro"  # p1's default_engine


def test_generate_explicit_engine_overrides_profile_default(voicebox_server, tmp_path: Path) -> None:
    out = tmp_path / "seg.wav"
    result = generate_speech_voicebox(
        text="Hello world", output_path=out, profile="Narrator", engine="chatterbox_turbo",
        url=voicebox_server,
    )
    assert result["success"] is True, result["error"]
    assert GENERATE_CALLS[0]["engine"] == "chatterbox_turbo"


def test_generate_waits_for_async_completion(voicebox_server, tmp_path: Path) -> None:
    """/generate returns "generating" immediately; the client must drain
    /generate/{id}/status (SSE) before /audio/{id} — this is the regression
    for the real-app bug where fetching audio immediately 500s."""
    out = tmp_path / "seg.wav"
    result = generate_speech_voicebox(
        text="Hello world", output_path=out, profile="Narrator", url=voicebox_server,
    )
    assert result["success"] is True, result["error"]
    assert result["duration"] == pytest.approx(1.0, abs=0.2)


def test_generate_failed_status_is_a_hard_error(tmp_path: Path, monkeypatch) -> None:
    """A "failed" terminal status from /generate/{id}/status must surface the
    server's own error message, not silently proceed to /audio/{id}."""
    class _FailHandler(_Handler):
        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self._send_json({"status": "ok"})
            elif self.path == "/profiles":
                self._send_json(PROFILES)
            elif self.path.startswith("/generate/") and self.path.endswith("/status"):
                gen_id = self.path.split("/")[2]
                body = (
                    f'data: {{"id": "{gen_id}", "status": "failed", '
                    f'"error": "No samples found for profile"}}\n\n'
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self._send_json({"error": "not found"}, status=404)

    server = HTTPServer(("127.0.0.1", 0), _FailHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        result = generate_speech_voicebox(
            text="Hello world", output_path=tmp_path / "seg.wav", profile="Narrator",
            url=f"http://{host}:{port}",
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["success"] is False
    assert "No samples found for profile" in result["error"]
    assert not (tmp_path / "seg.wav").exists()


def test_generate_server_down_returns_launch_hint(tmp_path: Path) -> None:
    result = generate_speech_voicebox(
        text="hi", output_path=tmp_path / "x.wav", profile="Narrator",
        url=_closed_url(), timeout=1.0,
    )
    assert result["success"] is False
    assert _LAUNCH_HINT in result["error"]
    assert not (tmp_path / "x.wav").exists()


# ---------------------------------------------------------------------------
# cmd_synthesize dispatch
# ---------------------------------------------------------------------------


def _write_min_script(tmp_path: Path) -> Path:
    script = {
        "title": "t",
        "total_estimated_duration_seconds": 2.0,
        "segments": [
            {
                "segment_id": "seg01",
                "section_title": "s",
                "narration_text": "Hello from the narrator.",
                "estimated_duration_seconds": 2.0,
                "animation_cues": [],
                "visual_engine": "collage",
            }
        ],
    }
    p = tmp_path / "script.json"
    p.write_text(json.dumps(script))
    return p


def test_cmd_synthesize_voicebox_dispatch(voicebox_server, tmp_path: Path, monkeypatch) -> None:
    import src.studio.tts_voicebox as tts_voicebox
    from src import pipeline

    calls: list[dict] = []

    def fake_generate(text, output_path, profile, language="en", seed=None,
                      url=None, timeout=300.0):
        calls.append({"profile": profile, "seed": seed, "language": language})
        # Write a real 48k mono wav so loudnorm + duration probe both succeed.
        _ffmpeg(["-f", "lavfi", "-i", "sine=frequency=220:duration=2",
                 "-ar", "48000", "-ac", "1", str(output_path)])
        return {"success": True, "output_path": str(output_path), "error": None, "duration": 2.0}

    # cmd_synthesize does `from .studio.tts_voicebox import generate_speech_voicebox`
    # at call time, so patching the module attribute is enough. resolve_profile
    # stays real and runs against the fake server.
    monkeypatch.setattr(tts_voicebox, "generate_speech_voicebox", fake_generate)
    monkeypatch.setenv("PTV_VOICE_PROVIDER", "voicebox")
    monkeypatch.setenv("PTV_VOICEBOX_URL", voicebox_server)
    # Pin the profile explicitly: the fake server only lists Narrator/Morgan,
    # and this test is about dispatch/resolution, not the config default.
    monkeypatch.setenv("PTV_VOICEBOX_PROFILE", "Narrator")

    script_path = _write_min_script(tmp_path)
    out_dir = tmp_path / "audio"
    pipeline.cmd_synthesize(str(script_path), str(out_dir))

    manifest = json.loads((out_dir / "audio_manifest.json").read_text())
    assert "seg01" in manifest
    assert manifest["seg01"]["qa_issues"] == []
    # Resolved "Narrator" -> "p1" once, before the loop; first attempt seed=None.
    assert len(calls) == 1
    assert calls[0]["profile"] == "p1"
    assert calls[0]["seed"] is None
    assert calls[0]["language"] == "en"


# ---------------------------------------------------------------------------
# _normalize_language — full names from presets/tool args must become ISO
# codes before reaching /generate (Voicebox 422s on "english").
# ---------------------------------------------------------------------------


def test_normalize_language_maps_full_names_and_codes() -> None:
    from src.studio.tts_voicebox import _normalize_language

    assert _normalize_language("english") == "en"
    assert _normalize_language("English ") == "en"
    assert _normalize_language("en") == "en"
    assert _normalize_language("zh") == "zh"
    assert _normalize_language("") == "en"
    assert _normalize_language("klingon") == "en"  # unknown full name -> safe default
