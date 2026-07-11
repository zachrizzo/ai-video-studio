"""Tests for the resumable production driver (src/studio/producer.py)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time

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
        "run_abc123", run_dir, {"job": job}, "clips", False, "", None, None
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


# ---------------------------------------------------------------------------
# stop_run_production — real subprocess, real process group
# ---------------------------------------------------------------------------


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def test_stop_run_production_stops_running_job(run_dir, monkeypatch, tmp_path):
    """stop_run_production must: report status "stopped" (not "failed"),
    remove the job from _ACTIVE, actually kill the running subprocess *and*
    its own child (standing in for the pipeline CLI's mflux/ffmpeg/LTX
    subprocess), be idempotent on a second call, and leave start_run_production
    able to start a fresh job afterward (the resume path stays untouched)."""
    pidfile = tmp_path / "child.pid"
    slow_step = ("slow_step", "Running slow step",
                 ["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait $!"])
    monkeypatch.setattr(producer, "_pipeline_steps", lambda *a, **k: [slow_step])

    status = producer.start_run_production("run_abc123")
    assert status["status"] == "running"

    assert _wait_until(lambda: pidfile.exists() and bool(pidfile.read_text().strip())), (
        "step subprocess never started"
    )
    child_pid = int(pidfile.read_text().strip())

    job = producer._ACTIVE["run_abc123"]
    assert _wait_until(lambda: job.process is not None), "job.process never set"
    proc_pid = job.process.pid

    stopped_status = producer.stop_run_production("run_abc123")
    assert stopped_status["status"] == "stopped"
    assert stopped_status["error"] is None
    assert "run_abc123" not in producer._ACTIVE

    # Both the step's own process and its grandchild must be gone — not
    # merely orphaned.
    with pytest.raises(ProcessLookupError):
        os.kill(proc_pid, 0)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)

    # Idempotent: stopping an already-stopped run is a no-op, not an error.
    again = producer.stop_run_production("run_abc123")
    assert again["status"] == "stopped"

    # Resume path untouched: production can be started again afterward.
    monkeypatch.setattr(producer, "_pipeline_steps", lambda *a, **k: [("noop", "No-op", ["true"])])
    restart_status = producer.start_run_production("run_abc123")
    assert restart_status["status"] == "running"
    assert _wait_until(lambda: producer.get_run_production_status("run_abc123")["status"] != "running")
    final_status = producer.get_run_production_status("run_abc123")
    assert final_status["status"] == "done"


def test_stop_run_production_no_active_job_is_idempotent_noop(run_dir):
    status = producer.stop_run_production("run_abc123")
    assert status["status"] == "idle"
    assert "run_abc123" not in producer._ACTIVE


def test_run_command_kills_process_immediately_if_stop_requested_during_spawn(
    run_dir, monkeypatch
):
    """Regression for the step-startup race: stop_run_production can be called
    in the window between the step loop's stop_event check and _run_command's
    job.process assignment, while Popen is still spawning. Since job.process
    is None during that window, stop_run_production has nothing to kill, so
    _run_command must re-check stop_event itself right after job.process is
    set and kill the just-spawned process immediately rather than proceeding
    to read its stdout / wait on it. Setting stop_event before calling
    _run_command deterministically hits this exact path without racing real
    threads."""
    killed: list[subprocess.Popen] = []
    real_kill = producer._kill_process_group

    def spy_kill(proc):
        killed.append(proc)
        real_kill(proc)

    monkeypatch.setattr(producer, "_kill_process_group", spy_kill)

    job = producer.ProductionJob(
        run_id="run_abc123", thread=threading.Thread(target=lambda: None)
    )
    job.stop_event.set()

    with pytest.raises(producer.ProductionStopped):
        producer._run_command(
            run_dir=run_dir,
            job=job,
            status={},
            step="slow_step",
            step_label="Running slow step",
            progress=0,
            total_steps=1,
            args=["sleep", "30"],
            env=os.environ.copy(),
        )

    assert job.process is None
    assert len(killed) == 1
    proc = killed[0]
    assert _wait_until(lambda: proc.poll() is not None), "spawned process was not killed"


# ---------------------------------------------------------------------------
# preset_id — snapshot, env propagation, resume, unknown preset
# ---------------------------------------------------------------------------


def test_start_run_production_with_preset_writes_snapshot_and_applies_env(run_dir, monkeypatch):
    fake_preset = {
        "id": "cinematic_documentary",
        "builtin": True,
        "tts_provider": "voicebox",
        "voicebox_profile": "Eric",
        "video_provider": "ltx",
    }
    monkeypatch.setattr(
        producer.presets, "get_preset",
        lambda pid: fake_preset if pid == "cinematic_documentary" else None,
    )
    monkeypatch.setattr(producer, "_pipeline_steps", lambda *a, **k: [("noop", "No-op", ["true"])])
    envs: list[dict] = []
    monkeypatch.setattr(producer, "_run_command", lambda *, env, **kwargs: envs.append(env))

    status = producer.start_run_production("run_abc123", preset_id="cinematic_documentary")
    assert status["status"] == "running"
    assert _wait_until(lambda: producer.get_run_production_status("run_abc123")["status"] != "running")

    snapshot = run_dir / producer.PRESET_SNAPSHOT_FILE
    assert snapshot.exists()
    assert json.loads(snapshot.read_text())["id"] == "cinematic_documentary"

    assert len(envs) == 1
    assert envs[0]["PTV_VOICE_PROVIDER"] == "voicebox"
    assert envs[0]["PTV_VOICEBOX_PROFILE"] == "Eric"
    assert envs[0]["PTV_VIDEO_PROVIDER"] == "ltx"


def test_start_run_production_reuses_snapshot_without_preset_id(run_dir, monkeypatch):
    fake_preset = {"id": "cinematic_documentary", "builtin": True, "tts_provider": "voicebox"}
    monkeypatch.setattr(
        producer.presets, "get_preset",
        lambda pid: fake_preset if pid == "cinematic_documentary" else None,
    )
    monkeypatch.setattr(producer, "_pipeline_steps", lambda *a, **k: [("noop", "No-op", ["true"])])
    envs: list[dict] = []
    monkeypatch.setattr(producer, "_run_command", lambda *, env, **kwargs: envs.append(env))

    producer.start_run_production("run_abc123", preset_id="cinematic_documentary")
    assert _wait_until(lambda: producer.get_run_production_status("run_abc123")["status"] != "running")
    assert len(envs) == 1 and envs[0]["PTV_VOICE_PROVIDER"] == "voicebox"

    # A later call with no preset_id reuses the snapshot from the first call.
    producer.start_run_production("run_abc123")
    assert _wait_until(lambda: len(envs) == 2)
    assert envs[1]["PTV_VOICE_PROVIDER"] == "voicebox"


def test_start_run_production_unknown_preset_id_raises_value_error(run_dir, monkeypatch):
    monkeypatch.setattr(producer.presets, "get_preset", lambda pid: None)
    with pytest.raises(ValueError, match="unknown preset"):
        producer.start_run_production("run_abc123", preset_id="does_not_exist")
    assert not (run_dir / producer.PRESET_SNAPSHOT_FILE).exists()


def test_start_run_production_no_preset_no_snapshot_is_unchanged(run_dir, monkeypatch):
    """No preset_id and no prior snapshot = today's behavior exactly: no
    preset env at all, no snapshot file written."""
    monkeypatch.delenv("PTV_VOICE_PROVIDER", raising=False)
    monkeypatch.setattr(producer, "_pipeline_steps", lambda *a, **k: [("noop", "No-op", ["true"])])
    envs: list[dict] = []
    monkeypatch.setattr(producer, "_run_command", lambda *, env, **kwargs: envs.append(env))

    producer.start_run_production("run_abc123")
    assert _wait_until(lambda: producer.get_run_production_status("run_abc123")["status"] != "running")
    assert not (run_dir / producer.PRESET_SNAPSHOT_FILE).exists()
    assert envs and "PTV_VOICE_PROVIDER" not in envs[0]


def _start_fake_active_job(run_id: str) -> producer.ProductionJob:
    """Register a still-alive job in _ACTIVE without touching disk, so the
    already-active no-op branch of start_run_production is exercised without
    a real subprocess or thread race."""
    ready = threading.Event()
    stop = threading.Event()

    def alive_forever():
        ready.set()
        stop.wait()

    thread = threading.Thread(target=alive_forever, daemon=True)
    job = producer.ProductionJob(run_id=run_id, thread=thread)
    thread.start()
    ready.wait(timeout=5)
    producer._ACTIVE[run_id] = job
    job.stop_event = stop  # reuse as our own stop switch to end the thread later
    return job


def test_start_run_production_already_active_does_not_touch_preset_snapshot(
    run_dir, monkeypatch
):
    """Regression: calling start_run_production with a preset_id while a job
    for this run is already active must be a true no-op — it must not
    overwrite (or create) .production_preset.json, since the already-running
    job's env was already built from whatever preset existed when its own
    thread started."""
    fake_preset = {"id": "cinematic_documentary", "builtin": True, "tts_provider": "voicebox"}
    monkeypatch.setattr(
        producer.presets, "get_preset",
        lambda pid: fake_preset if pid == "cinematic_documentary" else None,
    )

    snapshot_path = run_dir / producer.PRESET_SNAPSHOT_FILE
    assert not snapshot_path.exists()

    job = _start_fake_active_job("run_abc123")
    try:
        status = producer.start_run_production("run_abc123", preset_id="cinematic_documentary")
        assert status["status"] != "failed"
        assert not snapshot_path.exists(), "no-op call must not write a preset snapshot"
    finally:
        job.stop_event.set()
        job.thread.join(timeout=5)
        producer._ACTIVE.pop("run_abc123", None)


def test_start_run_production_already_active_unknown_preset_id_does_not_raise(
    run_dir, monkeypatch
):
    """Regression: an unknown preset_id passed alongside an already-active job
    must not raise — the call never reaches preset resolution because it's a
    no-op that just returns the existing job's status."""
    monkeypatch.setattr(producer.presets, "get_preset", lambda pid: None)

    job = _start_fake_active_job("run_abc123")
    try:
        status = producer.start_run_production("run_abc123", preset_id="does_not_exist")
        assert status["status"] != "failed"
        assert not (run_dir / producer.PRESET_SNAPSHOT_FILE).exists()
    finally:
        job.stop_event.set()
        job.thread.join(timeout=5)
        producer._ACTIVE.pop("run_abc123", None)
