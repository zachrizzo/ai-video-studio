"""WebSocket chat handler using the Claude Agent SDK.

Each WebSocket connection maintains its own session.  Messages are streamed
back as JSON frames following the contract defined in the API spec.

If the Claude Agent SDK is not available or not authenticated, an error event
is sent and the connection is kept alive so the REST endpoints remain usable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Matches a run id (run_<8+ hex>) inside a command string or file path so the
# artifact hook can tell the viewer which run was just touched.
_RUN_ID_RE = re.compile(r"run_[0-9a-f]{6,}")


def _extract_run_id(tool_input: dict[str, Any]) -> str | None:
    for key in ("command", "file_path", "path"):
        val = tool_input.get(key)
        if isinstance(val, str):
            m = _RUN_ID_RE.search(val)
            if m:
                return m.group(0)
    return None

# Repository root (two levels up from this file: src/studio/agent.py → repo root)
_REPO_ROOT = str(Path(__file__).resolve().parents[2])


# ---------------------------------------------------------------------------
# Helper: summarise a tool call input to ~120 chars
# ---------------------------------------------------------------------------


def _tool_summary(name: str, tool_input: dict[str, Any]) -> str:
    """Return a short human-readable description of a tool invocation."""
    # Try common fields in priority order
    for key in ("command", "file_path", "path", "pattern", "query", "description"):
        if key in tool_input:
            val = str(tool_input[key])
            prefix = f"{name}: {val}"
            return prefix[:120]
    # Fall back to compact JSON of the input
    try:
        raw = json.dumps(tool_input, ensure_ascii=False)
    except Exception:
        raw = str(tool_input)
    return raw[:120]


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

_ARTIFACT_TOOLS = frozenset({"Write", "Edit", "Bash", "MultiEdit"})


async def handle_ws(websocket: WebSocket) -> None:
    """Accept a WebSocket connection and drive a Claude Agent SDK session."""
    await websocket.accept()

    # Per-connection state
    session_id: str | None = None
    active_run_id: str | None = None
    # Queue used to bridge sync hook callbacks → async ws.send_json
    artifact_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _send(msg: dict[str, Any]) -> None:
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Attempt to import the SDK – errors are caught so the server stays up.
    # -----------------------------------------------------------------------
    try:
        from claude_agent_sdk import (  # type: ignore[import]
            ClaudeAgentOptions,
            HookMatcher,
            PostToolUseHookInput,
            HookContext,
            AssistantMessage,
            SystemMessage,
            ResultMessage,
            StreamEvent,
            TextBlock,
            ToolUseBlock,
            ToolResultBlock,
            query,
        )

        sdk_available = True
    except Exception as exc:  # noqa: BLE001
        sdk_available = False
        sdk_error = str(exc)
        logger.warning("Claude Agent SDK not available: %s", exc)

    if not sdk_available:
        await _send(
            {
                "type": "error",
                "message": f"Claude Agent SDK not available: {sdk_error}",
            }
        )
        # Keep the socket open so the frontend can still use REST endpoints
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            return
        return

    # -----------------------------------------------------------------------
    # PostToolUse hook – runs synchronously inside the SDK; bridges to async
    # via artifact_queue.
    # -----------------------------------------------------------------------
    async def _post_tool_use_hook(
        hook_input: PostToolUseHookInput,
        _transcript: str | None,
        _ctx: HookContext,
    ) -> dict[str, Any]:
        nonlocal active_run_id
        tool_name: str = hook_input.get("tool_name", "")
        if tool_name in _ARTIFACT_TOOLS:
            # Prefer a run id parsed from the tool input (catches brand-new runs
            # created mid-conversation); fall back to the viewed run.
            run_id = _extract_run_id(hook_input.get("tool_input", {}) or {})
            if run_id:
                active_run_id = run_id
            await artifact_queue.put(active_run_id or "unknown")
        return {}

    # -----------------------------------------------------------------------
    # Background task: drain artifact_queue → ws send_json
    # -----------------------------------------------------------------------
    async def _drain_artifacts() -> None:
        while True:
            run_id = await artifact_queue.get()
            if run_id is None:
                break
            await _send({"type": "artifact_updated", "run_id": run_id})

    drain_task = asyncio.create_task(_drain_artifacts())

    # -----------------------------------------------------------------------
    # Main message loop
    # -----------------------------------------------------------------------
    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send({"type": "error", "message": "Invalid JSON"})
                continue

            if msg.get("type") != "user_message":
                continue

            user_text: str = msg.get("text", "")
            active_run_id = msg.get("run_id") or active_run_id

            # Build options
            options = ClaudeAgentOptions(
                cwd=_REPO_ROOT,
                permission_mode="acceptEdits",
                allowed_tools=[
                    "Read",
                    "Write",
                    "Edit",
                    "Bash",
                    "Glob",
                    "Grep",
                    "Task",
                    "Skill",
                ],
                setting_sources=["project"],
                skills="all",
                include_partial_messages=True,
                resume=session_id,
                hooks={
                    "PostToolUse": [
                        HookMatcher(hooks=[_post_tool_use_hook])
                    ]
                },
            )

            try:
                async for event in query(prompt=user_text, options=options):
                    # ---- SystemMessage: capture session_id ----
                    if isinstance(event, SystemMessage):
                        new_sid = event.data.get("session_id")
                        if new_sid and not session_id:
                            session_id = new_sid
                            await _send({"type": "session", "session_id": session_id})

                    # ---- StreamEvent: partial content deltas ----
                    elif isinstance(event, StreamEvent):
                        ev = event.event
                        ev_type = ev.get("type", "")

                        if ev_type == "content_block_delta":
                            delta = ev.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    await _send({"type": "assistant_text", "text": text})
                        # Tool-use events are emitted from AssistantMessage below
                        # (with full summaries) to avoid duplicate activity lines.

                    # ---- AssistantMessage: completed turn ----
                    elif isinstance(event, AssistantMessage):
                        if session_id is None and event.session_id:
                            session_id = event.session_id
                            await _send({"type": "session", "session_id": session_id})

                        for block in event.content:
                            # TextBlock is skipped: the live text already arrived via
                            # StreamEvent text_delta; re-sending it would duplicate.
                            if isinstance(block, ToolUseBlock):
                                summary = _tool_summary(block.name, block.input)
                                await _send(
                                    {
                                        "type": "tool_use",
                                        "name": block.name,
                                        "summary": summary,
                                    }
                                )
                            elif isinstance(block, ToolResultBlock):
                                ok = not bool(
                                    block.is_error if hasattr(block, "is_error") else False
                                )
                                # tool_name not stored on ToolResultBlock; use empty string
                                await _send(
                                    {"type": "tool_result", "name": "", "ok": ok}
                                )

                    # ---- ResultMessage: query finished ----
                    elif isinstance(event, ResultMessage):
                        if session_id is None:
                            session_id = event.session_id
                            await _send({"type": "session", "session_id": session_id})

            except Exception as exc:  # noqa: BLE001
                logger.exception("Error during Claude query: %s", exc)
                await _send({"type": "error", "message": str(exc)})
                continue

            await _send({"type": "done"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected WS error: %s", exc)
        await _send({"type": "error", "message": str(exc)})
    finally:
        # Signal the drain task to stop
        await artifact_queue.put(None)
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
