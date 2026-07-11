"""Tests for GET /api/voicebox/profiles (src/studio/server.py)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
        elif self.path == "/profiles":
            import json
            body = json.dumps([
                {"id": "p1", "name": "Zach", "default_engine": "qwen"},
                {"id": "p2", "name": "Narrator", "default_engine": "kokoro"},
            ]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


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


def test_lists_real_profiles_when_voicebox_is_up(voicebox_server, monkeypatch) -> None:
    from starlette.testclient import TestClient

    from src.studio.server import app

    monkeypatch.setenv("PTV_VOICEBOX_URL", voicebox_server)
    client = TestClient(app)

    r = client.get("/api/voicebox/profiles")
    assert r.status_code == 200
    data = r.json()
    assert data["available"] is True
    assert data["message"] is None
    names = [p["name"] for p in data["profiles"]]
    assert names == ["Zach", "Narrator"]
    assert data["profiles"][1]["default_engine"] == "kokoro"


def test_gracefully_reports_unavailable_when_voicebox_is_down(monkeypatch) -> None:
    from starlette.testclient import TestClient

    from src.studio.server import app

    monkeypatch.setenv("PTV_VOICEBOX_URL", "http://127.0.0.1:1")  # nothing listens here
    client = TestClient(app)

    r = client.get("/api/voicebox/profiles")
    assert r.status_code == 200  # not a 500 — Voicebox being down is expected, not an error
    data = r.json()
    assert data["available"] is False
    assert data["profiles"] == []
    assert "Launch the Voicebox app" in data["message"]
