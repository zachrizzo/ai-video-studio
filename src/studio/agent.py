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
import os
import re
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


def _snapshot_run_scripts() -> dict[str, float]:
    """Return script.json mtimes keyed by run id for changed-run detection."""
    root = Path(os.environ.get("STUDIO_RUNS_DIR", "/tmp/mongol-video"))
    if not root.is_dir():
        return {}
    mtimes: dict[str, float] = {}
    for child in root.iterdir():
        if not child.is_dir() or not child.name.startswith("run_"):
            continue
        script = child / "script.json"
        if script.exists():
            try:
                mtimes[child.name] = script.stat().st_mtime
            except OSError:
                continue
    return mtimes

# Repository root (two levels up from this file: src/studio/agent.py → repo root)
_REPO_ROOT = str(Path(__file__).resolve().parents[2])

# Briefing appended to Claude Code's default system prompt so the chat agent
# knows this project's REAL local generation capabilities.
_STUDIO_BRIEF = """
You are the brain of a local "Video Studio" app on this machine. You CAN generate
real media locally — never tell the user you have no video/image model. Available:

- IMAGES: FLUX.1 (via mflux) — `uv run python -m src.pipeline imagegen <script.json> <run_dir> [ids]`
- AI VIDEO: real image-to-video via LTX-2.3 (ltx-2-mlx on Apple Silicon) —
  `uv run python -m src.pipeline videogen <script.json> <run_dir> [ids]`
  Use PTV_VIDEO_PROVIDER=ltx for generated scene clips by default. The pipeline
  turns each storyboard beat's action/camera_motion into an LTX motion prompt and
  anchors both the first frame and a soft final keyframe from the source image for
  better identity/coherence. Use Ken
  Burns only when the user explicitly asks for static/pan-only motion or when LTX
  fails and fallback is needed.
- VOICE: `uv run python -m src.pipeline synthesize <script.json> <run_dir>/audio`
  This auto-uses Qwen3-TTS (local, no API key needed). DO NOT use 'silence' — always use 'synthesize'.
  Set env vars PTV_QWEN_TTS_SPEAKER and PTV_QWEN_TTS_LANGUAGE to control voice.
  VISUALS for diagrams:
  write HTML/Manim scene specs and `render`. Final cut: `composite`.
- STORYBOARD: `uv run python -m src.pipeline storyboard <script.json> <run_dir>`
  writes `<run_dir>/storyboard.json` from the script's visual beats and flags weak pacing.

Segments have `visual_type`: "scene" (a FLUX photo → LTX motion clip) or "diagram"
(HTML/Manim animation). Scene segments need an `image_prompt`, or preferably an
ordered `visual_beats` list for pacing.

PRODUCTION CONTRACT — act like a producer, not a one-shot prompt bot:
1. Before generating, write a structured `<run_dir>/script.json` that includes:
   subject, canonical_name, audience, style_bible, narration_style,
   historical_constraints, visual_continuity_rules, forbidden_visuals,
   storyboard_summary, storyboard_rules, negative_prompt, pronunciation_dictionary,
   release_acceptance_criteria.
   Each segment should include visual_intent, visual_constraints,
   negative_prompt, production_notes, acceptance_criteria, and for scene segments
   `visual_beats`.
   Visual beats are ordered mini-shots inside one narrated section:
   [{"beat_id":"b01","description":"...","shot_type":"wide",
   "composition":"...","action":"...","camera_motion":"slow push-in",
   "continuity_notes":["..."],"asset_notes":["..."],"image_prompt":"...","weight":1.0}].
   Use enough visual beats to keep each beat near 2.5-3.5 seconds. Scene segments
   longer than ~6 seconds usually need 2-4+ visual beats. Use one beat only for very
   short segments or when the section is a rendered diagram.
2. Treat the storyboard as a preproduction gate. Before imagegen, run:
   `uv run python -m src.pipeline storyboard <run_dir>/script.json <run_dir>`
   Inspect `<run_dir>/storyboard.json`. If it warns about too few beats or weak
   pacing, revise script.json before generating media.
3. Make the style bible concrete. If the user selected a preset, apply that style to
   every image_prompt and continuity rule. If the user asked for a specific historical
   subject, the script and visuals must use the correct canonical name and era.
4. Prefer robust visuals over fragile AI motion. Avoid asking AI video to render
   readable text, banners with words, hands, detailed fingers, capes, flags, birds,
   horses, huge crowds, flames, or smoke unless those artifacts are essential. Use
   diagrams, stills, overlays, or post-composited text for these. For scene beats,
   write `action` and `camera_motion` so LTX-2.3 brings the still image to life
   instead of merely panning across it.
5. Never rely on generated text inside images. If text is needed, render it in
   HTML/Manim or composite it manually after generation.
6. After synthesize/imagegen/videogen, run:
   `uv run python -m src.pipeline manifest <run_dir>/script.json <run_dir>`
   then composite with the generated `<run_dir>/composite_manifest.json`.
7. After synthesize/imagegen/videogen/manifest/composite, ALWAYS run:
   `uv run python -m src.pipeline qa <run_dir>`
   If QA fails, inspect `<run_dir>/qa_report.json`, regenerate or revise the failing
   segments, composite again, and rerun QA. Do not call a video finished unless QA
   passes, or you explicitly tell the user what failed and ask for override.
8. For voice failures, regenerate the affected audio segment first. Audio duration
   far longer than the script estimate usually means hallucinated speech.
9. When editing an existing run, preserve good approved artifacts and regenerate only
   the failed or requested segments.
10. When the user asks whether an existing flow is still making a video, or asks to
   continue/resume/finish an existing flow that already has script.json, use the
   Studio producer instead of improvising a second production path:
   `curl -X POST http://127.0.0.1:8787/api/runs/<run_id>/produce`
   Then inspect `http://127.0.0.1:8787/api/runs/<run_id>/production` for status.
11. When an existing run already has good images and the user wants better LTX clips
    or to finish video without redoing photos, use video-only production:
    `curl -X POST http://127.0.0.1:8787/api/runs/<run_id>/produce -H 'Content-Type: application/json' -d '{"mode":"videos","force_video":true}'`
    This preserves `<run_dir>/images`. For a selected repair/test clip, use:
    `curl -X POST http://127.0.0.1:8787/api/runs/<run_id>/produce -H 'Content-Type: application/json' -d '{"mode":"clips","force_video":true,"segment_ids":"seg01_b01"}'`

QUICK CLIP RECIPE — when the user asks for a short standalone clip of something
(e.g. "make a 5s video of someone dancing"), do NOT refuse and do NOT require a PDF.
Do this:
1. `uv run python -m src.pipeline setup /tmp/paper-to-video`  (run lands in the viewer dir)
2. Write `<run_dir>/script.json` with ONE segment and the production fields above:
   visual_type "scene",
   estimated_duration_seconds ~5, a vivid `image_prompt` that DESCRIBES MOTION
   (e.g. "a person dancing energetically, moving to the beat, dynamic, photorealistic"),
   visual_engine "html", empty animation_cues, plus title/total_estimated_duration_seconds.
3. `uv run python -m src.pipeline storyboard <run_dir>/script.json <run_dir>`
4. `uv run python -m src.pipeline synthesize <run_dir>/script.json <run_dir>/audio`
5. `uv run python -m src.pipeline imagegen <run_dir>/script.json <run_dir>`
6. `uv run python -m src.pipeline videogen <run_dir>/script.json <run_dir>`
7. `uv run python -m src.pipeline manifest <run_dir>/script.json <run_dir>` then
   `uv run python -m src.pipeline composite <run_dir>/composite_manifest.json output/<name>.mp4`
8. `uv run python -m src.pipeline qa <run_dir>` and fix failures before presenting it.
The result auto-appears in the flow viewer. Always use `uv run`. For longer/full videos,
use the `generate-video` skill which orchestrates the same steps across many segments.
"""


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
    session_ids_by_conversation: dict[str, str] = {}
    active_run_ids_by_conversation: dict[str, str] = {}
    active_conversation_id = "default"
    active_run_id: str | None = None
    # Queue used to bridge sync hook callbacks → async ws.send_json
    artifact_queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

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
                active_run_ids_by_conversation[active_conversation_id] = run_id
            await artifact_queue.put((active_run_id or "unknown", active_conversation_id))
        return {}

    # -----------------------------------------------------------------------
    # Background task: drain artifact_queue → ws send_json
    # -----------------------------------------------------------------------
    async def _drain_artifacts() -> None:
        while True:
            item = await artifact_queue.get()
            if item is None:
                break
            run_id, conversation_id = item
            await _send(
                {
                    "type": "artifact_updated",
                    "run_id": run_id,
                    "conversation_id": conversation_id,
                }
            )

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

            conversation_id = str(msg.get("conversation_id") or "default")
            active_conversation_id = conversation_id
            runs_before_query = _snapshot_run_scripts()
            user_text: str = msg.get("text", "")
            requested_session_id = msg.get("session_id")
            if not isinstance(requested_session_id, str) or not requested_session_id:
                requested_session_id = session_ids_by_conversation.get(conversation_id)
            msg_run_id = msg.get("run_id")
            if isinstance(msg_run_id, str) and msg_run_id:
                active_run_ids_by_conversation[conversation_id] = msg_run_id
            active_run_id = active_run_ids_by_conversation.get(conversation_id)

            # Inject preset context so the agent knows the user's chosen style
            preset = msg.get("preset")
            if preset:
                preset_ctx = (
                    f"\n\n[ACTIVE PRESET: {preset.get('name', '?')}]\n"
                    f"- Image style: {preset.get('style_prompt', '')}\n"
                    f"- Narration style: {preset.get('narration_style', '')}\n"
                    f"- Target length: {preset.get('video_length_minutes', '?')} minutes\n"
                    f"- Voice: speaker={preset.get('voice_speaker', 'serena')}, language={preset.get('voice_language', 'english')}\n"
                    f"- Video motion: {preset.get('video_provider', 'kenburns')}\n"
                    f"IMPORTANT: Use these settings when generating the video. "
                    f"Prefer PTV_VIDEO_PROVIDER=ltx for scene clips so LTX-2.3 animates the storyboard action; use Ken Burns only if explicitly requested or as fallback. "
                    f"Put the image style and narration style into script.json as style_bible and narration_style. "
                    f"Prefix ALL segment image_prompt and visual_beats image_prompt values with the style prompt above. "
                    f"For scene segments longer than ~6 seconds, write 2-4 visual_beats with storyboard fields "
                    f"(description, shot_type, composition, action, camera_motion) so LTX can bring each still's action to life and the final video changes visuals every 3-6 seconds. "
                    f"Use the 'synthesize' command (Qwen3-TTS local) for voice, NOT 'silence'. "
                    f"Set PTV_QWEN_TTS_SPEAKER={preset.get('voice_speaker', 'serena')} and "
                    f"PTV_QWEN_TTS_LANGUAGE={preset.get('voice_language', 'english')} before running synthesize. "
                    f"Before imagegen, run `uv run python -m src.pipeline storyboard <run_dir>/script.json <run_dir>` and fix storyboard warnings. "
                    f"After videogen, run `uv run python -m src.pipeline manifest <run_dir>/script.json <run_dir>` before compositing. "
                    f"After compositing, run `uv run python -m src.pipeline qa <run_dir>` and fix failures.\n"
                )
                user_text = user_text + preset_ctx

            # Build options
            options = ClaudeAgentOptions(
                cwd=_REPO_ROOT,
                system_prompt={
                    "type": "preset",
                    "preset": "claude_code",
                    "append": _STUDIO_BRIEF,
                },
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
                resume=requested_session_id,
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
                        if new_sid:
                            session_ids_by_conversation[conversation_id] = new_sid
                            await _send(
                                {
                                    "type": "session",
                                    "session_id": new_sid,
                                    "conversation_id": conversation_id,
                                }
                            )

                    # ---- StreamEvent: partial content deltas ----
                    elif isinstance(event, StreamEvent):
                        ev = event.event
                        ev_type = ev.get("type", "")

                        if ev_type == "content_block_delta":
                            delta = ev.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    await _send(
                                        {
                                            "type": "assistant_text",
                                            "text": text,
                                            "conversation_id": conversation_id,
                                        }
                                    )
                        # Tool-use events are emitted from AssistantMessage below
                        # (with full summaries) to avoid duplicate activity lines.

                    # ---- AssistantMessage: completed turn ----
                    elif isinstance(event, AssistantMessage):
                        if event.session_id:
                            session_ids_by_conversation[conversation_id] = event.session_id
                            await _send(
                                {
                                    "type": "session",
                                    "session_id": event.session_id,
                                    "conversation_id": conversation_id,
                                }
                            )

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
                                        "conversation_id": conversation_id,
                                    }
                                )
                            elif isinstance(block, ToolResultBlock):
                                ok = not bool(
                                    block.is_error if hasattr(block, "is_error") else False
                                )
                                # tool_name not stored on ToolResultBlock; use empty string
                                await _send(
                                    {
                                        "type": "tool_result",
                                        "name": "",
                                        "ok": ok,
                                        "conversation_id": conversation_id,
                                    }
                                )

                    # ---- ResultMessage: query finished ----
                    elif isinstance(event, ResultMessage):
                        if event.session_id:
                            session_ids_by_conversation[conversation_id] = event.session_id
                            await _send(
                                {
                                    "type": "session",
                                    "session_id": event.session_id,
                                    "conversation_id": conversation_id,
                                }
                            )

            except Exception as exc:  # noqa: BLE001
                logger.exception("Error during Claude query: %s", exc)
                await _send(
                    {
                        "type": "error",
                        "message": str(exc),
                        "conversation_id": conversation_id,
                    }
                )
                continue

            runs_after_query = _snapshot_run_scripts()
            changed_runs = [
                (run_id, mtime)
                for run_id, mtime in runs_after_query.items()
                if mtime > runs_before_query.get(run_id, 0)
            ]
            if changed_runs:
                changed_runs.sort(key=lambda item: item[1], reverse=True)
                changed_run_id = changed_runs[0][0]
                active_run_id = changed_run_id
                active_run_ids_by_conversation[conversation_id] = changed_run_id
                await _send(
                    {
                        "type": "artifact_updated",
                        "run_id": changed_run_id,
                        "conversation_id": conversation_id,
                    }
                )

            await _send(
                {
                    "type": "done",
                    "conversation_id": conversation_id,
                    "run_id": active_run_ids_by_conversation.get(conversation_id),
                }
            )

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected WS error: %s", exc)
        await _send(
            {
                "type": "error",
                "message": str(exc),
                "conversation_id": active_conversation_id,
            }
        )
    finally:
        # Signal the drain task to stop
        await artifact_queue.put(None)
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
