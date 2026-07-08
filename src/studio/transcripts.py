"""Server-side chat transcripts.

Each conversation appends JSON-line events to
``<runs_root>/chats/{conversation_id}.jsonl``; ``load_messages`` collapses
them back into the UI's ChatMessage shape. Conversation ids are
client-generated, so they are validated defensively — persistence is skipped
(never raised) for anything suspicious, and every write is best-effort so a
transcript failure can never break a chat turn.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from src.studio.runs import _runs_root

logger = logging.getLogger(__name__)

_CONVERSATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def is_valid_conversation_id(conversation_id: Any) -> bool:
    return isinstance(conversation_id, str) and bool(_CONVERSATION_ID_RE.match(conversation_id))


def _transcript_path(conversation_id: str) -> Path | None:
    if not is_valid_conversation_id(conversation_id):
        return None
    return _runs_root() / "chats" / f"{conversation_id}.jsonl"


def append_event(conversation_id: str, event: dict[str, Any]) -> None:
    """Append one transcript event (adds a ``ts`` timestamp); best-effort."""
    try:
        path = _transcript_path(conversation_id)
        if path is None:
            logger.warning(
                "skipping transcript for invalid conversation id: %r", conversation_id
            )
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {**event, "ts": time.time()}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        logger.exception("failed to append transcript event")


def load_messages(conversation_id: str) -> list[dict[str, Any]]:
    """Collapse a transcript into the UI's ChatMessage shape:
    ``[{role, text, tools: [{name, summary, status}]}]``.

    Tool events attach to the current assistant message; because the full
    assistant text is only persisted at turn end, tools arriving first create
    an assistant message with empty text that the text event later fills in.
    Returns [] for missing or invalid conversations.
    """
    path = _transcript_path(conversation_id)
    if path is None or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    messages: list[dict[str, Any]] = []
    current_assistant: dict[str, Any] | None = None
    tools_by_id: dict[str, dict[str, Any]] = {}

    def _open_assistant() -> dict[str, Any]:
        nonlocal current_assistant
        if current_assistant is None:
            current_assistant = {"role": "assistant", "text": "", "tools": []}
            messages.append(current_assistant)
        return current_assistant

    def _close_assistant() -> None:
        nonlocal current_assistant
        current_assistant = None
        tools_by_id.clear()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        role = event.get("role")
        etype = event.get("type")
        if role == "user":
            _close_assistant()
            messages.append(
                {"role": "user", "text": str(event.get("text") or ""), "tools": []}
            )
        elif role == "assistant":
            msg = _open_assistant()
            msg["text"] = str(event.get("text") or "")
            _close_assistant()
        elif etype == "tool_use":
            tool = {
                "name": str(event.get("name") or ""),
                "summary": str(event.get("summary") or ""),
                "status": "done",
            }
            _open_assistant()["tools"].append(tool)
            tool_id = event.get("id")
            if isinstance(tool_id, str):
                tools_by_id[tool_id] = tool
        elif etype == "tool_result":
            tool = tools_by_id.get(event.get("id"))
            if tool is not None:
                tool["status"] = "done" if event.get("ok") else "failed"
        elif etype == "error":
            _close_assistant()
            messages.append(
                {"role": "error", "text": str(event.get("message") or ""), "tools": []}
            )

    return messages
