"""Tests for GET /api/pipeline-steps (src/studio/server.py)."""

from pathlib import Path

import pytest


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path))
    return tmp_path


def test_pipeline_steps_endpoint_matches_producer(runs_root: Path) -> None:
    from starlette.testclient import TestClient  # noqa: F401  (shipped with fastapi)

    from src.studio.producer import _pipeline_steps
    from src.studio.server import app

    client = TestClient(app)

    r = client.get("/api/pipeline-steps")
    assert r.status_code == 200
    modes = r.json()["modes"]

    assert set(modes) == {"full", "videos", "clips"}
    for mode, steps in modes.items():
        expected = _pipeline_steps(Path("."), mode)
        assert [(s["id"], s["label"]) for s in steps] == [
            (step, label) for step, label, _ in expected
        ]
        assert len(steps) > 0

    # clips is the short mode; full covers everything.
    assert len(modes["clips"]) < len(modes["videos"]) < len(modes["full"])
