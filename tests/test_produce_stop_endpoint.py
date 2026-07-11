"""Tests for POST /api/runs/{run_id}/produce/stop (src/studio/server.py)."""

from pathlib import Path

import pytest


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path))
    return tmp_path


def test_stop_unknown_run_is_404(runs_root: Path) -> None:
    from starlette.testclient import TestClient

    from src.studio.server import app

    client = TestClient(app)
    r = client.post("/api/runs/run_missing/produce/stop")
    assert r.status_code == 404


def test_stop_with_no_active_job_is_idempotent_noop(runs_root: Path) -> None:
    from starlette.testclient import TestClient

    from src.studio.server import app

    run_dir = runs_root / "run_abc123"
    run_dir.mkdir()
    (run_dir / "script.json").write_text('{"title": "t", "segments": []}')

    client = TestClient(app)
    r = client.post("/api/runs/run_abc123/produce/stop")
    assert r.status_code == 200
    assert r.json()["status"] == "idle"

    # Calling it again is still a no-op, not an error.
    r2 = client.post("/api/runs/run_abc123/produce/stop")
    assert r2.status_code == 200
    assert r2.json()["status"] == "idle"
