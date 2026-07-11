"""Tests for GET /api/style_packs/{name} (src/studio/server.py)."""

from __future__ import annotations


def test_style_pack_detail_returns_full_tokens() -> None:
    """The summary endpoint (/api/style_packs) only returns palette; the UI
    needs type/motion/texture/flux prompt too to show what a style pack
    actually is instead of just its name."""
    from starlette.testclient import TestClient

    from src.studio.server import app

    client = TestClient(app)

    r = client.get("/api/style_packs/anthropic_docu")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "anthropic_docu"
    assert data["palette"]["paper"] == "#F0EEE6"
    assert data["type"]["display"] == "Lora"
    assert data["motion"]["camera_max_scale"] == 1.15
    assert data["texture"]["grain_opacity"] == 0.05
    assert "oil painting" in data["flux_prefix"]


def test_style_pack_detail_unknown_name_is_404() -> None:
    from starlette.testclient import TestClient

    from src.studio.server import app

    client = TestClient(app)

    r = client.get("/api/style_packs/does_not_exist")
    assert r.status_code == 404
