"""Tests for one-shot generation start semantics (src/studio/generate.py).

Two regressions:
- start_* used to return before the background thread wrote status.json, so an
  immediate generation_status poll got "generation not found" and agents
  retried, duplicating heavy generations.
- The local FLUX/LTX branches bypassed the cross-process generation lock the
  pipeline takes, so a chat "make me an image" could run concurrently with a
  production run's generation on the same MPS device.

Also covers the Generate tab contract fixes: audio-to-video/retake/extend/
video-hdr previously 422'd on every request because the frontend sent field
names (audio_path/video_path) and string timecodes the backend's Pydantic
models don't accept; and the created_at/progress correctness fix
(_update_status merging instead of _write_status overwriting).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import pytest


@pytest.fixture
def generate(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("STUDIO_GENERATIONS_DIR", str(tmp_path / "gens"))
    from src.studio import generate as generate_mod

    return generate_mod


def test_start_image_generation_status_is_immediately_pollable(generate, monkeypatch):
    monkeypatch.setattr(generate, "_run_image_gen", lambda *args: None)
    gen_id = generate.start_image_generation("a red square")
    status = generate.get_generation(gen_id)
    assert status is not None, "status.json must exist before start_* returns"
    assert status["status"] == "generating"
    assert status["type"] == "image"


def test_start_video_generation_status_is_immediately_pollable(generate, monkeypatch):
    monkeypatch.setattr(generate, "_run_video_gen", lambda *args: None)
    gen_id = generate.start_video_generation("pan over hills")
    status = generate.get_generation(gen_id)
    assert status is not None and status["status"] == "generating"


def test_start_tts_status_is_immediately_pollable(generate, monkeypatch):
    monkeypatch.setattr(generate, "_run_tts", lambda *args: None)
    gen_id = generate.start_tts("hello there")
    status = generate.get_generation(gen_id)
    assert status is not None and status["status"] == "generating"


def test_local_image_gen_holds_the_generation_lock(generate, monkeypatch):
    held = {"now": False, "during_gen": None}

    @contextmanager
    def fake_lock():
        held["now"] = True
        try:
            yield
        finally:
            held["now"] = False

    monkeypatch.setattr(generate, "generation_lock", fake_lock)

    class Result:
        success = True
        error_message = None

    def fake_generate_image(**kwargs):
        held["during_gen"] = held["now"]
        Path(kwargs["output_path"]).write_bytes(b"png")
        return Result()

    import src.imagegen.flux as flux_mod

    monkeypatch.setattr(flux_mod, "generate_image", fake_generate_image)

    from src.config import PipelineConfig

    generate._run_image_gen("genlock000001", "a prompt", PipelineConfig())

    assert held["during_gen"] is True, "FLUX must run under the generation lock"
    assert held["now"] is False, "the lock must be released afterwards"
    assert generate.get_generation("genlock000001")["status"] == "done"


def test_one_shot_tts_speaker_default_matches_pipeline_config(generate):
    """The pipeline's qwen speaker default (PipelineConfig.qwen_tts_speaker) and
    the one-shot defaults (generate.start_tts / tts.generate_speech) must agree,
    or a run resumed through a different entry point switches voices — the exact
    drift bug this guards against."""
    import inspect

    from src.config import PipelineConfig
    from src.studio import tts

    config_default = PipelineConfig.model_fields["qwen_tts_speaker"].default
    start_default = inspect.signature(generate.start_tts).parameters["speaker"].default
    speech_default = inspect.signature(tts.generate_speech).parameters["speaker"].default
    assert start_default == config_default
    assert speech_default == config_default


# ---------------------------------------------------------------------------
# Generate tab contract fixes: the corrected frontend-shaped payloads for the
# 4 modes that previously 422'd on every request.
# ---------------------------------------------------------------------------


@pytest.fixture
def client(generate):
    """A TestClient against src.studio.server, sharing the generate fixture's
    STUDIO_HOME/STUDIO_GENERATIONS_DIR env so generation ids resolve."""
    from starlette.testclient import TestClient

    from src.studio import server

    return TestClient(server.app)


def test_audio_to_video_accepts_frontend_shaped_payload(client, monkeypatch):
    from src.studio import server

    captured: dict = {}

    def fake_start(**kwargs):
        captured.update(kwargs)
        return "genatv0000001"

    monkeypatch.setattr(server, "start_audio_to_video", fake_start)

    r = client.post("/api/generate/audio-to-video", json={
        "backend": "local",
        "audio_uri": "/tmp/some.wav",
        "image_uri": None,
        "prompt": "a scene matching the audio",
        "model": "ltx-2-3-pro",
        "resolution": "1280x720",
        "guidance_scale": 75,
        "api_key": "sekrit-key",
    })

    assert r.status_code == 200
    assert r.json() == {"id": "genatv0000001"}
    assert captured["audio_uri"] == "/tmp/some.wav"
    assert captured["backend"] == "local"
    assert captured["api_key"] == "sekrit-key"


def test_audio_to_video_old_audio_path_shape_still_422s(client):
    """Regression: the pre-fix frontend sent audio_path/image_path, which
    AudioToVideoRequest (audio_uri required) has never accepted. Documents
    why the audio_path->audio_uri rename in GeneratePanel.tsx was needed —
    this shape must keep 422ing so the fix isn't silently reverted."""
    r = client.post("/api/generate/audio-to-video", json={
        "backend": "local",
        "audio_path": "/tmp/some.wav",
        "image_path": None,
        "prompt": "a scene",
    })
    assert r.status_code == 422


def test_retake_accepts_frontend_shaped_payload(client, monkeypatch):
    from src.studio import server

    captured: dict = {}

    def fake_start(**kwargs):
        captured.update(kwargs)
        return "genrtk0000001"

    monkeypatch.setattr(server, "start_retake_video", fake_start)

    r = client.post("/api/generate/retake", json={
        "backend": "local",
        "video_uri": "/tmp/vid.mp4",
        "start_time": 2.0,
        "duration": 3.0,
        "prompt": "replace this section",
        "model": "ltx-2-3-pro",
        "resolution": "1280x720",
        "mode": "replace_audio_and_video",
    })

    assert r.status_code == 200
    assert r.json() == {"id": "genrtk0000001"}
    assert captured["video_uri"] == "/tmp/vid.mp4"
    assert captured["start_time"] == 2.0
    assert captured["duration"] == 3.0
    assert captured["mode"] == "replace_audio_and_video"


def test_retake_old_video_path_shape_still_422s(client):
    """Regression: the pre-fix frontend also sent start_time/duration as
    "00:00.00" strings into float fields, on top of the video_path rename."""
    r = client.post("/api/generate/retake", json={
        "backend": "local",
        "video_path": "/tmp/vid.mp4",
        "start_time": "00:02.00",
        "duration": "00:03.00",
        "prompt": "replace this section",
    })
    assert r.status_code == 422


def test_extend_accepts_frontend_shaped_payload(client, monkeypatch):
    from src.studio import server

    captured: dict = {}

    def fake_start(**kwargs):
        captured.update(kwargs)
        return "genext0000001"

    monkeypatch.setattr(server, "start_extend_video", fake_start)

    r = client.post("/api/generate/extend", json={
        "backend": "local",
        "video_uri": "/tmp/vid.mp4",
        "prompt": "continue the scene",
        "model": "ltx-2-3-pro",
        "mode": "from_end",
        "duration": 5.0,
        "context": None,
    })

    assert r.status_code == 200
    assert r.json() == {"id": "genext0000001"}
    assert captured["video_uri"] == "/tmp/vid.mp4"
    assert captured["mode"] == "from_end"
    assert captured["duration"] == 5.0


def test_extend_old_video_path_shape_still_422s(client):
    r = client.post("/api/generate/extend", json={
        "backend": "local",
        "video_path": "/tmp/vid.mp4",
        "prompt": "continue the scene",
        "duration": "00:05.00",
    })
    assert r.status_code == 422


def test_video_hdr_accepts_frontend_shaped_payload(client, monkeypatch):
    from src.studio import server

    captured: dict = {}

    def fake_start(**kwargs):
        captured.update(kwargs)
        return "genhdr0000001"

    monkeypatch.setattr(server, "start_video_hdr", fake_start)

    r = client.post("/api/generate/video-hdr", json={
        "video_uri": "/tmp/vid.mp4",
        "api_key": "sekrit-key",
    })

    assert r.status_code == 200
    assert r.json() == {"id": "genhdr0000001"}
    assert captured["video_uri"] == "/tmp/vid.mp4"
    assert captured["api_key"] == "sekrit-key"


def test_video_hdr_old_video_path_shape_still_422s(client):
    r = client.post("/api/generate/video-hdr", json={"video_path": "/tmp/vid.mp4"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# api_key threading (1d): LTXClient must receive the request's api_key, not
# just the server-side PTV_LTX_API_KEY env var (previously always ignored).
# ---------------------------------------------------------------------------


def test_get_ltx_client_prefers_explicit_api_key_over_env(generate, monkeypatch):
    monkeypatch.delenv("PTV_LTX_API_KEY", raising=False)

    captured: dict = {}

    class FakeClient:
        def __init__(self, api_key):
            captured["api_key"] = api_key

    import src.studio.ltx_api as ltx_api_mod

    monkeypatch.setattr(ltx_api_mod, "LTXClient", FakeClient)

    generate._get_ltx_client(api_key="from-request")

    assert captured["api_key"] == "from-request"


def test_get_ltx_client_raises_without_any_key(generate, monkeypatch):
    monkeypatch.delenv("PTV_LTX_API_KEY", raising=False)
    with pytest.raises(ValueError):
        generate._get_ltx_client(api_key=None)


# ---------------------------------------------------------------------------
# Progress milestones (P7) for the "classic" one-shot workers.
# ---------------------------------------------------------------------------


def test_run_image_gen_reports_progress_milestones(generate, monkeypatch):
    class Result:
        success = True
        error_message = None

    seen_mid_progress = {}

    def fake_generate_image(**kwargs):
        mid = generate.get_generation("genimgprog01")
        seen_mid_progress["progress"] = mid["progress"]
        seen_mid_progress["progress_step"] = mid["progress_step"]
        Path(kwargs["output_path"]).write_bytes(b"png")
        return Result()

    import src.imagegen.flux as flux_mod

    monkeypatch.setattr(flux_mod, "generate_image", fake_generate_image)

    from src.config import PipelineConfig

    generate._run_image_gen("genimgprog01", "a prompt", PipelineConfig())

    assert 0 < seen_mid_progress["progress"] < 100
    assert seen_mid_progress["progress_step"]

    final = generate.get_generation("genimgprog01")
    assert final["status"] == "done"
    assert final["progress"] == 100
    assert final["progress_step"] == "Done"


def test_run_video_gen_reports_failure_progress(generate, monkeypatch):
    class FailResult:
        success = False
        error_message = "flux boom"

    import src.imagegen.flux as flux_mod

    monkeypatch.setattr(flux_mod, "generate_image", lambda **kwargs: FailResult())

    from src.config import PipelineConfig

    generate._run_video_gen("genvidprog01", "a prompt", None, PipelineConfig())

    status = generate.get_generation("genvidprog01")
    assert status["status"] == "failed"
    assert status["progress"] == 0
    assert status["progress_step"] == "Failed"
    assert "flux boom" in status["error"]


def test_run_video_hdr_reports_progress_milestones(generate, monkeypatch):
    class FakeClient:
        def __init__(self, api_key):
            pass

        def video_to_video_hdr(self, output_path, video_uri):
            Path(output_path).write_bytes(b"mp4")
            return {"success": True, "error": None}

    import src.studio.ltx_api as ltx_api_mod

    monkeypatch.setattr(ltx_api_mod, "LTXClient", FakeClient)

    generate._run_video_hdr("genhdrprog01", "/tmp/vid.mp4", api_key="k")

    status = generate.get_generation("genhdrprog01")
    assert status["status"] == "done"
    assert status["progress"] == 100
    assert status["progress_step"] == "Done"


# ---------------------------------------------------------------------------
# created_at stability (P7): _update_status must merge, not overwrite, so the
# UI's elapsed-time timer (based on created_at) doesn't reset on every poll.
# ---------------------------------------------------------------------------


def test_update_status_preserves_created_at_across_calls(generate):
    generate._write_initial_status("gencreatedat1", "text-to-video", "a prompt")
    created_at = generate.get_generation("gencreatedat1")["created_at"]

    time.sleep(0.01)
    generate._update_status("gencreatedat1", status="generating", progress=50,
                             progress_step="halfway")
    assert generate.get_generation("gencreatedat1")["created_at"] == created_at

    time.sleep(0.01)
    generate._update_status("gencreatedat1", status="done", progress=100,
                             progress_step="Done")
    assert generate.get_generation("gencreatedat1")["created_at"] == created_at


def test_update_status_seeds_created_at_when_no_prior_status(generate):
    """_run_image_gen (and friends) can be invoked directly without a prior
    _write_initial_status call (see test_local_image_gen_holds_the_generation_lock
    above) — _update_status must not crash and must still produce a
    created_at."""
    generate._update_status("gennoinit0001", status="generating", progress=5)
    status = generate.get_generation("gennoinit0001")
    assert status is not None
    assert status["status"] == "generating"
    assert isinstance(status["created_at"], float)
