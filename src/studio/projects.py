"""Projects — group runs (videos/storyboards) and chat conversations.

A project is the unit of creative work: one project owns any number of runs
and any number of chat conversations. The registry is a single JSON file in
the runs root (same durable store `list_runs` scans), so projects survive
restarts and follow the runs directory wherever STUDIO_RUNS_DIR points.

Registry shape (projects.json):

    {
      "projects": {
        "<project_id>": {
          "id": "...",
          "name": "...",
          "created_at": 1700000000.0,
          "conversations": {
            "<conversation_id>": {
              "id": "...", "title": "...",
              "claude_session_id": "..." | null,
              "created_at": ..., "updated_at": ...
            }
          }
        }
      },
      "run_assignments": {"run_<id>": "<project_id>"}
    }

Chat transcripts themselves stay client-side (as before); the registry holds
the durable grouping + enough metadata (title, Claude session id) to resume a
chat from any browser.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .runs import _runs_root

DEFAULT_PROJECT_ID = "default"
DEFAULT_PROJECT_NAME = "My Videos"


def _registry_path() -> Path:
    return _runs_root() / "projects.json"


def _load() -> dict:
    path = _registry_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                data.setdefault("projects", {})
                data.setdefault("run_assignments", {})
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"projects": {}, "run_assignments": {}}


def _save(data: dict) -> None:
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _ensure_default(data: dict) -> dict:
    if DEFAULT_PROJECT_ID not in data["projects"]:
        data["projects"][DEFAULT_PROJECT_ID] = {
            "id": DEFAULT_PROJECT_ID,
            "name": DEFAULT_PROJECT_NAME,
            "created_at": time.time(),
            "conversations": {},
        }
    return data


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def list_projects() -> list[dict]:
    """All projects, each with its run ids, newest first (default always first).

    Runs that exist on disk but have no assignment are reported under the
    default project so nothing ever disappears from the UI.
    """
    from .runs import list_runs

    data = _ensure_default(_load())
    assignments = data["run_assignments"]
    runs = list_runs()

    by_project: dict[str, list[str]] = {}
    for run in runs:
        pid = assignments.get(run["id"], DEFAULT_PROJECT_ID)
        if pid not in data["projects"]:
            pid = DEFAULT_PROJECT_ID
        by_project.setdefault(pid, []).append(run["id"])

    result = []
    for pid, proj in data["projects"].items():
        convos = sorted(
            proj.get("conversations", {}).values(),
            key=lambda c: c.get("updated_at", c.get("created_at", 0)),
            reverse=True,
        )
        result.append({
            "id": pid,
            "name": proj.get("name", pid),
            "created_at": proj.get("created_at", 0),
            "run_ids": by_project.get(pid, []),
            "conversations": convos,
        })
    result.sort(key=lambda p: (p["id"] != DEFAULT_PROJECT_ID, -p["created_at"]))
    return result


def create_project(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("project name must not be empty")
    data = _ensure_default(_load())
    pid = uuid.uuid4().hex[:8]
    data["projects"][pid] = {
        "id": pid,
        "name": name,
        "created_at": time.time(),
        "conversations": {},
    }
    _save(data)
    return {"id": pid, "name": name, "created_at": data["projects"][pid]["created_at"],
            "run_ids": [], "conversations": []}


def rename_project(project_id: str, name: str) -> bool:
    name = (name or "").strip()
    if not name:
        raise ValueError("project name must not be empty")
    data = _ensure_default(_load())
    if project_id not in data["projects"]:
        return False
    data["projects"][project_id]["name"] = name
    _save(data)
    return True


def delete_project(project_id: str) -> bool:
    """Delete a project. Its runs fall back to the default project."""
    if project_id == DEFAULT_PROJECT_ID:
        raise ValueError("the default project cannot be deleted")
    data = _ensure_default(_load())
    if project_id not in data["projects"]:
        return False
    del data["projects"][project_id]
    for run_id, pid in list(data["run_assignments"].items()):
        if pid == project_id:
            data["run_assignments"][run_id] = DEFAULT_PROJECT_ID
    _save(data)
    return True


# ---------------------------------------------------------------------------
# Run assignment
# ---------------------------------------------------------------------------

def assign_run(run_id: str, project_id: str) -> bool:
    data = _ensure_default(_load())
    if project_id not in data["projects"]:
        return False
    data["run_assignments"][run_id] = project_id
    _save(data)
    return True


def project_for_run(run_id: str) -> str:
    data = _load()
    pid = data.get("run_assignments", {}).get(run_id, DEFAULT_PROJECT_ID)
    return pid if pid in data.get("projects", {}) or pid == DEFAULT_PROJECT_ID else DEFAULT_PROJECT_ID


# ---------------------------------------------------------------------------
# Conversations (metadata only — transcripts stay client-side)
# ---------------------------------------------------------------------------

def upsert_conversation(
    project_id: str,
    conversation_id: str,
    title: str | None = None,
    claude_session_id: str | None = None,
) -> dict | None:
    """Create/update a conversation record inside a project.

    Moving a conversation between projects is intentionally unsupported; a
    conversation id is upserted into whichever project it already lives in.
    """
    data = _ensure_default(_load())
    if project_id not in data["projects"]:
        return None
    # If the conversation already exists in ANY project, update it there.
    owner_pid = project_id
    for pid, proj in data["projects"].items():
        if conversation_id in proj.get("conversations", {}):
            owner_pid = pid
            break
    convos = data["projects"][owner_pid].setdefault("conversations", {})
    now = time.time()
    record = convos.get(conversation_id) or {
        "id": conversation_id,
        "title": title or "New chat",
        "claude_session_id": None,
        "created_at": now,
    }
    if title:
        record["title"] = title
    if claude_session_id:
        record["claude_session_id"] = claude_session_id
    record["updated_at"] = now
    convos[conversation_id] = record
    _save(data)
    return record


def delete_conversation(project_id: str, conversation_id: str) -> bool:
    data = _ensure_default(_load())
    proj = data["projects"].get(project_id)
    if not proj or conversation_id not in proj.get("conversations", {}):
        return False
    del proj["conversations"][conversation_id]
    _save(data)
    return True
