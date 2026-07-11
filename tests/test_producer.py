"""Tests for the resumable production driver (src/studio/producer.py)."""

from __future__ import annotations

import threading

import pytest

from src.studio import producer


@pytest.fixture
def runs_root(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(root))
    return root


@pytest.fixture
def run_dir(runs_root):
    rd = runs_root / "run_abc123"
    rd.mkdir()
    (rd / "script.json").write_text('{"title": "t", "segments": []}')
    return rd


# ---------------------------------------------------------------------------
# _safe_run_dir — canonical run-id validator (agent_tools delegates here)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", ["../x", "/etc/passwd", "a/b", "..", ".", "", "a\\b", None, 7])
def test_safe_run_dir_rejects_invalid_ids(runs_root, bad_id):
    with pytest.raises(ValueError, match="invalid run id"):
        producer._safe_run_dir(bad_id)


def test_safe_run_dir_resolves_under_runs_root(runs_root, run_dir):
    assert producer._safe_run_dir("run_abc123") == run_dir.resolve()


# ---------------------------------------------------------------------------
# _pipeline_steps — the single source of truth for step order per mode
# ---------------------------------------------------------------------------


def test_pipeline_steps_full_order(tmp_path):
    names = [name for name, _label, _args in producer._pipeline_steps(tmp_path, "full")]
    assert names == [
        "storyboard",
        "synthesize",
        "storyboard",
        "align",
        "sfx",
        "imagegen",
        "assets",
        "videogen",
        "collage",
        "manifest",
        "composite",
        "qa",
    ]


def test_pipeline_steps_videos_and_clips_order(tmp_path):
    videos = [name for name, _l, _a in producer._pipeline_steps(tmp_path, "videos")]
    assert videos == ["storyboard", "videogen", "collage", "manifest", "composite", "qa"]
    clips = [name for name, _l, _a in producer._pipeline_steps(tmp_path, "clips")]
    assert clips == ["storyboard", "videogen"]


def test_pipeline_steps_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError, match="unsupported production mode"):
        producer._pipeline_steps(tmp_path, "everything")


# ---------------------------------------------------------------------------
# _run_production env — must not re-pin PipelineConfig defaults
# ---------------------------------------------------------------------------


def _capture_production_envs(monkeypatch, run_dir) -> list[dict]:
    envs: list[dict] = []

    def fake_run_command(*, env, **kwargs):
        envs.append(env)

    monkeypatch.setattr(producer, "_run_command", fake_run_command)
    job = producer.ProductionJob(
        run_id="run_abc123", thread=threading.Thread(target=lambda: None)
    )
    producer._run_production(
        "run_abc123", run_dir, {"job": job}, "clips", False, "", None
    )
    return envs


def test_production_env_does_not_pin_video_provider(run_dir, monkeypatch):
    """Regression: the producer used to setdefault PTV_VIDEO_PROVIDER=ltx and
    PTV_LTX_PREFER_EXTEND=false, a second copy of the PipelineConfig defaults
    that also silently overrode .env values in the subprocess."""
    monkeypatch.delenv("PTV_VIDEO_PROVIDER", raising=False)
    monkeypatch.delenv("PTV_LTX_PREFER_EXTEND", raising=False)
    envs = _capture_production_envs(monkeypatch, run_dir)
    assert envs, "no pipeline steps ran"
    for env in envs:
        assert "PTV_VIDEO_PROVIDER" not in env
        assert "PTV_LTX_PREFER_EXTEND" not in env


def test_production_env_passes_through_explicit_provider(run_dir, monkeypatch):
    monkeypatch.setenv("PTV_VIDEO_PROVIDER", "kenburns")
    envs = _capture_production_envs(monkeypatch, run_dir)
    assert envs and all(env["PTV_VIDEO_PROVIDER"] == "kenburns" for env in envs)
