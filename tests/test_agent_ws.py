"""Tests for the WS-agent helpers in src/studio/agent.py.

Most of this file covers the pure/filesystem helpers, since driving handle_ws
end-to-end needs a live Claude Agent SDK session. The key regression there:
the end-of-turn run scan must only surface runs the turn actually touched, so
concurrent terminal work can't hijack the viewer or mis-bind a chat.

The transcript-ordering regression test below (A10) is the exception: it
drives handle_ws with a fake websocket and a fake ClaudeSDKClient (monkeypatched
onto the claude_agent_sdk module, which handle_ws's local import re-reads at
call time), since the bug is specifically about event ORDER within the
appended transcript file — the pure helpers don't touch that file at all.
"""

import asyncio
import json
from pathlib import Path

import claude_agent_sdk
import pytest
from claude_agent_sdk.types import ResultMessage, StreamEvent
from fastapi import WebSocketDisconnect

from src.studio.agent import (
    _ARTIFACT_TOOLS,
    _MUTATING_STUDIO_TOOLS,
    _extract_run_id,
    _is_run_binding_tool,
    _record_tool_run_id,
    _reported_run_id,
    _select_changed_runs,
    _snapshot_run_scripts,
    handle_ws,
)
from src.studio.agent_tools import STUDIO_TOOL_NAMES


# ---------------------------------------------------------------------------
# _extract_run_id
# ---------------------------------------------------------------------------


def test_extract_run_id_from_typed_field() -> None:
    assert _extract_run_id({"run_id": "run_abcdef12"}) == "run_abcdef12"


def test_extract_run_id_from_command_and_paths() -> None:
    assert _extract_run_id({"command": "ls /tmp/x/run_deadbeef/images"}) == "run_deadbeef"
    assert _extract_run_id({"file_path": "/x/run_0123ab/script.json"}) == "run_0123ab"


def test_extract_run_id_none_for_runless_input() -> None:
    # One-shot generate_video / tts inputs carry no run id; the hook must not
    # emit artifact frames for them (no stale-run fallback).
    assert _extract_run_id({"prompt": "a knight rides at dawn"}) is None
    assert _extract_run_id({}) is None


# ---------------------------------------------------------------------------
# _select_changed_runs
# ---------------------------------------------------------------------------


def test_select_changed_runs_requires_turn_activity() -> None:
    before = {"run_aaa111": 1.0, "run_bbb222": 1.0}
    after = {"run_aaa111": 2.0, "run_bbb222": 5.0, "run_ccc333": 9.0}

    # run_bbb222/run_ccc333 changed on disk but this turn never touched them
    # (e.g. a terminal pipeline run) — they must not steer the viewer.
    changed = _select_changed_runs(before, after, {"run_aaa111"})

    assert changed == [("run_aaa111", 2.0)]


def test_select_changed_runs_orders_newest_first() -> None:
    after = {"run_aaa111": 1.0, "run_bbb222": 3.0}

    changed = _select_changed_runs({}, after, {"run_aaa111", "run_bbb222"})

    assert changed == [("run_bbb222", 3.0), ("run_aaa111", 1.0)]


def test_select_changed_runs_empty_when_nothing_seen() -> None:
    assert _select_changed_runs({}, {"run_abc123": 1.0}, set()) == []


def test_select_changed_runs_ignores_unchanged_seen_runs() -> None:
    before = {"run_abc123": 5.0}
    after = {"run_abc123": 5.0}

    assert _select_changed_runs(before, after, {"run_abc123"}) == []


# ---------------------------------------------------------------------------
# _snapshot_run_scripts
# ---------------------------------------------------------------------------


def test_snapshot_run_scripts_uses_runs_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path))
    run_dir = tmp_path / "run_abc123"
    run_dir.mkdir()
    (run_dir / "script.json").write_text("{}")
    (tmp_path / "not_a_run").mkdir()
    (tmp_path / "run_noscript").mkdir()

    snapshot = _snapshot_run_scripts()

    assert set(snapshot) == {"run_abc123"}
    assert snapshot["run_abc123"] > 0


# ---------------------------------------------------------------------------
# Run-binding semantics: only mutation binds
#
# A read-only question about a run (get_run, production_status, ...) must
# never snap the FlowViewer or bind a chat conversation to that run — only a
# tool call that actually mutates a run's on-disk state may do that. These
# tests drive the same pure helpers the PostToolUse hook and end-of-turn
# `done` frame call, since exercising the hook itself requires a live Claude
# Agent SDK session.
# ---------------------------------------------------------------------------


def test_mutating_studio_tools_is_subset_of_registered_tool_names() -> None:
    # A future rename of a studio tool must not silently drop it out of the
    # mutating set (which would make a real mutation stop binding) nor leave
    # a stale name in it (which would make a read-only tool wrongly bind).
    registered = {f"mcp__studio__{name}" for name in STUDIO_TOOL_NAMES}
    assert _MUTATING_STUDIO_TOOLS <= registered


def test_mutating_studio_tools_excludes_read_only_and_one_shot_tools() -> None:
    read_only_or_one_shot = {
        "mcp__studio__list_runs",
        "mcp__studio__get_run",
        "mcp__studio__list_projects",
        "mcp__studio__capabilities",
        "mcp__studio__production_status",
        "mcp__studio__generate_image",
        "mcp__studio__generate_video",
        "mcp__studio__retake_video",
        "mcp__studio__extend_video",
        "mcp__studio__video_hdr",
        "mcp__studio__tts",
        "mcp__studio__generation_status",
        "mcp__studio__list_generations",
    }
    assert not (_MUTATING_STUDIO_TOOLS & read_only_or_one_shot)


def test_is_run_binding_tool_true_for_mutating_and_artifact_tools() -> None:
    assert _is_run_binding_tool("mcp__studio__imagegen") is True
    assert _is_run_binding_tool("mcp__studio__create_run") is True
    for name in _ARTIFACT_TOOLS:
        assert _is_run_binding_tool(name) is True


def test_is_run_binding_tool_false_for_read_only_studio_tools() -> None:
    assert _is_run_binding_tool("mcp__studio__get_run") is False
    assert _is_run_binding_tool("mcp__studio__production_status") is False
    assert _is_run_binding_tool("mcp__studio__list_runs") is False


def test_record_tool_run_id_ignores_read_only_tool_call() -> None:
    # A get_run/production_status-shaped call carries a run_id but must not
    # bind the conversation or count as "seen" for this turn.
    active_run_ids: dict[str, str] = {}
    seen: set[str] = set()

    result = _record_tool_run_id(
        "mcp__studio__get_run",
        {"run_id": "run_abcdef12"},
        "conv-1",
        active_run_ids,
        seen,
    )

    assert result is None
    assert active_run_ids == {}
    assert seen == set()


def test_record_tool_run_id_binds_on_mutating_tool_call() -> None:
    # An imagegen/create_run-shaped mutating call DOES bind the conversation
    # and mark the run as seen this turn.
    active_run_ids: dict[str, str] = {}
    seen: set[str] = set()

    result = _record_tool_run_id(
        "mcp__studio__imagegen",
        {"run_id": "run_abcdef12"},
        "conv-1",
        active_run_ids,
        seen,
    )

    assert result == "run_abcdef12"
    assert active_run_ids == {"conv-1": "run_abcdef12"}
    assert seen == {"run_abcdef12"}


def test_record_tool_run_id_none_when_no_run_id_parseable() -> None:
    active_run_ids: dict[str, str] = {}
    seen: set[str] = set()

    result = _record_tool_run_id(
        "mcp__studio__imagegen",
        {"prompt": "a knight rides at dawn"},
        "conv-1",
        active_run_ids,
        seen,
    )

    assert result is None
    assert active_run_ids == {}
    assert seen == set()


def test_reported_run_id_null_when_turn_touched_no_run() -> None:
    # Even if the conversation has a stale/previously-bound run id (e.g. from
    # a prior turn), a turn that touched no run this time must report None —
    # this is what makes removing the client run_id pre-seeding effective:
    # a read-only turn on a client-supplied run_id must not echo it back.
    active_run_ids = {"conv-1": "run_stale0001"}
    seen: set[str] = set()

    assert _reported_run_id("conv-1", active_run_ids, seen) is None


def test_reported_run_id_returns_run_when_seen_this_turn() -> None:
    active_run_ids = {"conv-1": "run_abcdef12"}
    seen = {"run_abcdef12"}

    assert _reported_run_id("conv-1", active_run_ids, seen) == "run_abcdef12"


# ---------------------------------------------------------------------------
# A10: streamed assistant text must be flushed to the transcript BEFORE an
# error event from the same turn — the transcript is append-only, so file
# order is display order on reload, and the text was generated first.
# ---------------------------------------------------------------------------


class _FakeWebSocket:
    """Feeds one user_message frame, then blocks briefly (giving the turn task
    time to finish) before raising a disconnect to end handle_ws's loop."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = list(messages)
        self.sent: list[dict] = []

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        if self._messages:
            return self._messages.pop(0)
        await asyncio.sleep(0.2)
        raise WebSocketDisconnect

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


def _make_fake_client_cls(*, raise_exception: bool = False, captured_options: list | None = None):
    """A fake ClaudeSDKClient that streams one text delta, then either raises
    (exercising the `except Exception` site) or yields an is_error
    ResultMessage (exercising the ResultMessage site) — the two flush sites
    the fix touches. Also records the constructed ClaudeAgentOptions (A11)."""

    class _FakeClient:
        def __init__(self, options) -> None:
            self.options = options
            if captured_options is not None:
                captured_options.append(options)

        async def connect(self) -> None:
            pass

        async def query(self, text: str) -> None:
            pass

        async def receive_response(self):
            yield StreamEvent(
                uuid="e1",
                session_id="sess-1",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "partial answer"},
                },
                parent_tool_use_id=None,
            )
            if raise_exception:
                raise RuntimeError("boom")
            yield ResultMessage(
                subtype="error_max_turns",
                duration_ms=1,
                duration_api_ms=1,
                is_error=True,
                num_turns=1,
                session_id="sess-1",
                stop_reason=None,
                total_cost_usd=None,
                usage=None,
                result="turn failed",
                structured_output=None,
                model_usage=None,
                permission_denials=None,
                deferred_tool_use=None,
                errors=None,
                api_error_status=None,
                uuid="u1",
            )

        async def disconnect(self) -> None:
            pass

        async def interrupt(self) -> None:
            pass

    return _FakeClient


def _run_one_turn(
    tmp_path, monkeypatch, *, raise_exception: bool, captured_options: list | None = None
) -> list[dict]:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(
        claude_agent_sdk,
        "ClaudeSDKClient",
        _make_fake_client_cls(raise_exception=raise_exception, captured_options=captured_options),
    )
    ws = _FakeWebSocket([json.dumps({
        "type": "user_message",
        "text": "hello",
        "conversation_id": "conv1",
    })])
    asyncio.run(handle_ws(ws))
    path = tmp_path / "chats" / "conv1.jsonl"
    assert path.exists(), "transcript file was never written"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("raise_exception", [False, True])
def test_assistant_text_flushed_before_error_event(tmp_path, monkeypatch, raise_exception) -> None:
    events = _run_one_turn(tmp_path, monkeypatch, raise_exception=raise_exception)

    assert events[0]["role"] == "user"
    assistant_idx = next(i for i, e in enumerate(events) if e.get("role") == "assistant")
    error_idx = next(i for i, e in enumerate(events) if e.get("type") == "error")
    assert assistant_idx < error_idx, (
        "assistant text must be appended to the transcript before the error "
        "event — otherwise reload renders the error above the text that "
        "chronologically preceded it"
    )
    assert events[assistant_idx]["text"] == "partial answer"
    # Exactly one assistant-text event: the `finally` block's own flush must
    # have been a no-op (assistant_chunks already cleared by the fix).
    assert sum(1 for e in events if e.get("role") == "assistant") == 1


# ---------------------------------------------------------------------------
# A11: ClaudeAgentOptions must carry a max_turns circuit breaker.
# ---------------------------------------------------------------------------


def test_options_default_max_turns(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("STUDIO_AGENT_MAX_TURNS", raising=False)
    captured: list = []
    _run_one_turn(tmp_path, monkeypatch, raise_exception=True, captured_options=captured)
    assert captured[0].max_turns == 150


def test_options_max_turns_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_AGENT_MAX_TURNS", "42")
    captured: list = []
    _run_one_turn(tmp_path, monkeypatch, raise_exception=True, captured_options=captured)
    assert captured[0].max_turns == 42
