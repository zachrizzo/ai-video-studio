"""Tests for one-shot generation start semantics (src/studio/generate.py).

Two regressions:
- start_* used to return before the background thread wrote status.json, so an
  immediate generation_status poll got "generation not found" and agents
  retried, duplicating heavy generations.
- The local FLUX/LTX branches bypassed the cross-process generation lock the
  pipeline takes, so a chat "make me an image" could run concurrently with a
  production run's generation on the same MPS device.
"""

from __future__ import annotations

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
