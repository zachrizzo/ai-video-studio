"""Tests for projects — grouping runs and chat conversations (src/studio/projects.py)."""

import json
from pathlib import Path

import pytest

from src.studio import projects


@pytest.fixture()
def runs_root(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path))
    return tmp_path


def _make_run(root: Path, run_id: str, title: str) -> None:
    d = root / f"run_{run_id}"
    d.mkdir()
    (d / "script.json").write_text(json.dumps({"title": title, "segments": []}))


# ---------------------------------------------------------------------------
# registry basics
# ---------------------------------------------------------------------------


def test_default_project_always_exists(runs_root: Path) -> None:
    result = projects.list_projects()
    assert result[0]["id"] == projects.DEFAULT_PROJECT_ID
    assert result[0]["name"] == projects.DEFAULT_PROJECT_NAME


def test_create_rename_delete_project(runs_root: Path) -> None:
    p = projects.create_project("Rome Documentary")
    assert p["name"] == "Rome Documentary"
    assert projects.rename_project(p["id"], "Fall of Rome") is True
    listed = {x["id"]: x for x in projects.list_projects()}
    assert listed[p["id"]]["name"] == "Fall of Rome"
    assert projects.delete_project(p["id"]) is True
    assert p["id"] not in {x["id"] for x in projects.list_projects()}


def test_create_project_rejects_empty_name(runs_root: Path) -> None:
    with pytest.raises(ValueError):
        projects.create_project("   ")


def test_default_project_cannot_be_deleted(runs_root: Path) -> None:
    with pytest.raises(ValueError):
        projects.delete_project(projects.DEFAULT_PROJECT_ID)


# ---------------------------------------------------------------------------
# run assignment
# ---------------------------------------------------------------------------


def test_unassigned_runs_fall_back_to_default(runs_root: Path) -> None:
    _make_run(runs_root, "aaaa1111", "Video A")
    listed = {x["id"]: x for x in projects.list_projects()}
    assert "run_aaaa1111" in listed[projects.DEFAULT_PROJECT_ID]["run_ids"]


def test_assign_run_moves_it_between_projects(runs_root: Path) -> None:
    _make_run(runs_root, "aaaa1111", "Video A")
    p = projects.create_project("Epics")
    assert projects.assign_run("run_aaaa1111", p["id"]) is True
    listed = {x["id"]: x for x in projects.list_projects()}
    assert "run_aaaa1111" in listed[p["id"]]["run_ids"]
    assert "run_aaaa1111" not in listed[projects.DEFAULT_PROJECT_ID]["run_ids"]
    assert projects.project_for_run("run_aaaa1111") == p["id"]


def test_assign_run_to_unknown_project_fails(runs_root: Path) -> None:
    assert projects.assign_run("run_x", "nope") is False


def test_deleting_project_reassigns_runs_to_default(runs_root: Path) -> None:
    _make_run(runs_root, "aaaa1111", "Video A")
    p = projects.create_project("Doomed")
    projects.assign_run("run_aaaa1111", p["id"])
    projects.delete_project(p["id"])
    assert projects.project_for_run("run_aaaa1111") == projects.DEFAULT_PROJECT_ID


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------


def test_project_holds_multiple_conversations(runs_root: Path) -> None:
    p = projects.create_project("Epics")
    projects.upsert_conversation(p["id"], "c1", title="Storyboard chat")
    projects.upsert_conversation(p["id"], "c2", title="Sound design chat")
    listed = {x["id"]: x for x in projects.list_projects()}
    titles = {c["title"] for c in listed[p["id"]]["conversations"]}
    assert titles == {"Storyboard chat", "Sound design chat"}


def test_upsert_updates_title_and_session(runs_root: Path) -> None:
    p = projects.create_project("Epics")
    projects.upsert_conversation(p["id"], "c1", title="First")
    rec = projects.upsert_conversation(p["id"], "c1", title="Renamed", claude_session_id="sess_42")
    assert rec["title"] == "Renamed"
    assert rec["claude_session_id"] == "sess_42"
    listed = {x["id"]: x for x in projects.list_projects()}
    assert len(listed[p["id"]]["conversations"]) == 1


def test_upsert_conversation_unknown_project(runs_root: Path) -> None:
    assert projects.upsert_conversation("nope", "c1") is None


def test_delete_conversation(runs_root: Path) -> None:
    p = projects.create_project("Epics")
    projects.upsert_conversation(p["id"], "c1", title="Chat")
    assert projects.delete_conversation(p["id"], "c1") is True
    assert projects.delete_conversation(p["id"], "c1") is False


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


def test_projects_rest_endpoints(runs_root: Path) -> None:
    httpx = pytest.importorskip("httpx")
    from starlette.testclient import TestClient  # noqa: F401  (shipped with fastapi)
    from src.studio.server import app

    client = TestClient(app)

    _make_run(runs_root, "bbbb2222", "Video B")

    r = client.post("/api/projects", json={"name": "Epics"})
    assert r.status_code == 200
    pid = r.json()["id"]

    assert client.post(f"/api/projects/{pid}/runs", json={"run_id": "run_bbbb2222"}).status_code == 200
    assert client.post(
        f"/api/projects/{pid}/conversations",
        json={"id": "c1", "title": "Chat one"},
    ).status_code == 200

    data = client.get("/api/projects").json()["projects"]
    proj = next(p for p in data if p["id"] == pid)
    assert proj["run_ids"] == ["run_bbbb2222"]
    assert proj["conversations"][0]["title"] == "Chat one"

    runs = client.get("/api/runs").json()["runs"]
    assert next(r for r in runs if r["id"] == "run_bbbb2222")["project_id"] == pid

    assert client.patch(f"/api/projects/{pid}", json={"name": "Epics 2"}).status_code == 200
    assert client.delete(f"/api/projects/{pid}/conversations/c1").status_code == 200
    assert client.delete(f"/api/projects/{pid}").status_code == 200
