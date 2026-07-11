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

from src.studio import capabilities, config, transcripts
from src.studio.runs import _runs_root

logger = logging.getLogger(__name__)

# Matches a run id (run_<8+ hex>) inside a command string or file path so the
# artifact hook can tell the viewer which run was just touched.
_RUN_ID_RE = re.compile(r"run_[0-9a-f]{6,}")


def _extract_run_id(tool_input: dict[str, Any]) -> str | None:
    for key in ("run_id", "command", "file_path", "path"):
        val = tool_input.get(key)
        if isinstance(val, str):
            m = _RUN_ID_RE.search(val)
            if m:
                return m.group(0)
    return None


def _snapshot_run_scripts() -> dict[str, float]:
    """Return script.json mtimes keyed by run id for changed-run detection."""
    root = _runs_root()
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


def _select_changed_runs(
    before: dict[str, float],
    after: dict[str, float],
    seen_run_ids: set[str],
) -> list[tuple[str, float]]:
    """Runs whose script.json changed during a turn, newest first.

    Only runs the turn actually touched (their id appeared in a tool input)
    are eligible — concurrent terminal work or another tab touching an
    unrelated run must not hijack the viewer's selection.
    """
    changed = [
        (run_id, mtime)
        for run_id, mtime in after.items()
        if mtime > before.get(run_id, 0) and run_id in seen_run_ids
    ]
    changed.sort(key=lambda item: item[1], reverse=True)
    return changed


# ---------------------------------------------------------------------------
# Detached turns: a dropped socket no longer kills the in-flight agent turn.
# The turn keeps running (it already streams into the server transcript); a
# reconnecting client claims it with a "resume" frame, and a watchdog
# interrupts it after a grace window so no CLI subprocess runs unattended
# forever.
# ---------------------------------------------------------------------------

_DETACHED_TURNS: dict[str, dict[str, Any]] = {}


def _turn_grace_seconds() -> float:
    try:
        return float(os.environ.get("STUDIO_TURN_GRACE_SECONDS", "120"))
    except ValueError:
        return 120.0


def _detach_turn(conversation_id: str, turn: dict[str, Any]) -> None:
    """Register a still-running turn whose socket just dropped."""

    async def _watchdog() -> None:
        task = turn["task"]
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=_turn_grace_seconds())
        except asyncio.TimeoutError:
            holder = turn["holder"]
            holder["stopped"] = True
            transcripts.append_event(
                conversation_id,
                {
                    "type": "error",
                    "message": (
                        "Interrupted: the browser disconnected mid-turn and did "
                        "not reconnect in time."
                    ),
                },
            )
            client = holder.get("client")
            if client is not None:
                try:
                    await client.interrupt()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("grace-window interrupt failed: %s", exc)
            try:
                await asyncio.wait_for(task, timeout=15)
            except Exception:  # noqa: BLE001
                task.cancel()
                try:
                    await task
                except (Exception, asyncio.CancelledError):  # noqa: BLE001
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("detached-turn watchdog failed")
        finally:
            if _DETACHED_TURNS.get(conversation_id) is turn:
                _DETACHED_TURNS.pop(conversation_id, None)

    turn["watchdog"] = asyncio.create_task(_watchdog())
    _DETACHED_TURNS[conversation_id] = turn


async def _interrupt_detached_turn(turn: dict[str, Any]) -> None:
    """Stop a detached turn before starting a new one on its conversation."""
    watchdog = turn.get("watchdog")
    if watchdog is not None:
        watchdog.cancel()
    holder = turn["holder"]
    holder["stopped"] = True
    client = holder.get("client")
    if client is not None:
        try:
            await client.interrupt()
        except Exception as exc:  # noqa: BLE001
            logger.warning("interrupt of detached turn failed: %s", exc)
    try:
        await asyncio.wait_for(turn["task"], timeout=15)
    except Exception:  # noqa: BLE001
        turn["task"].cancel()
        try:
            await turn["task"]
        except (Exception, asyncio.CancelledError):  # noqa: BLE001
            pass


# Repository root (two levels up from this file: src/studio/agent.py → repo root)
_REPO_ROOT = str(Path(__file__).resolve().parents[2])

# Values interpolated into _STUDIO_BRIEF below so the brief can never drift
# from the code that actually runs: the step order comes from the producer's
# _pipeline_steps (the real execution order), the sfx sound names from the
# sfx engine's SOUNDS registry, and the Voicebox URL from PipelineConfig.
from src.audio.sfx import SOUNDS as _SFX_SOUNDS  # noqa: E402
from src.config import PipelineConfig as _PipelineConfig  # noqa: E402
from src.studio.producer import _pipeline_steps as _producer_pipeline_steps  # noqa: E402

_FULL_STEP_NAMES = [name for name, _label, _args in _producer_pipeline_steps(Path("run"), "full")]
_STEP_ORDER = " → ".join(
    f"{name} (again)" if name in _FULL_STEP_NAMES[:i] else name
    for i, name in enumerate(_FULL_STEP_NAMES)
)
_VOICEBOX_URL = _PipelineConfig.model_fields["voicebox_url"].default

# Briefing appended to Claude Code's default system prompt so the chat agent
# knows this project's REAL local generation capabilities.
_STUDIO_BRIEF = """
You are the brain of a local "Video Studio" app on this machine. You CAN generate
real media locally — never tell the user you have no video/image model. Available
locally: FLUX.1 images, LTX-2.3 image-to-video (Apple Silicon), Qwen3-TTS and
Voicebox voice, procedural sound effects, and a deterministic collage engine.
A [capabilities] line below reports which local engines are currently available
(the capabilities tool re-probes on demand); if one is down or missing, tell the
user how to enable it instead of pretending it works.

Use the typed studio tools (mcp__studio__*) for ALL pipeline operations — do not
shell out to the pipeline CLI or curl the REST API. Recommended order for a video
(the same order produce_run executes):
  create_run → Write <run_dir>/script.json → %STEP_ORDER%
Storyboard MUST be re-run after synthesize: the first pass uses script estimates,
and the re-run updates beat timing from the real audio durations so clips are
sized to the narration.
(assets and collage only do work for collage segments — author
scenes/{segment_id}.collage.json specs before assets; runs without collage
segments skip both steps automatically).
- Skip-if-exists + force: synthesize skips segments that already have a good
  take, imagegen skips existing PNGs, videogen skips existing clips. Re-running
  them without force=true changes NOTHING — a QA fix loop that omits force just
  recomposites the identical video. To regenerate failing segments pass
  force=true (plus segment_ids to limit scope); produce_run mode "videos"
  likewise needs force_video=true to actually redo clips.
- synthesize fails (is_error) when any segment's TTS fails or its audio still
  carries QA issues after the retry; failed segments are marked "failed" with
  duration 0 in the audio manifest, so downstream steps cannot pretend their
  audio exists. Fix and re-run synthesize before continuing. The effective
  voice persists in the manifest and is reused on resume unless you explicitly
  pass voice params, so resumed runs never switch voices.
- produce_run(run_id, mode, force_video, segment_ids) runs the full resumable
  production in the background; poll production_status(run_id). Use it when the
  user asks to continue/resume/finish a run that already has script.json. mode
  "videos" preserves images and redoes clips onward; "clips" + segment_ids repairs
  selected clips only.
- One-shot tools for quick standalone media (no run needed): generate_image,
  generate_video, retake_video, extend_video, video_hdr, tts. Poll
  generation_status(gen_id); list_generations shows recent ones. Never refuse a
  quick clip request or require a PDF — use generate_video or a one-segment run.
- Discovery: list_runs, get_run(run_id), list_projects.
- Voice: synthesize defaults to local Qwen3-TTS (speaker/language params;
  qwen_model_size="1.7B" for higher quality than the default "0.6B"). For
  Voicebox voices pass voice_provider="voicebox" + voicebox_profile=<name>; the
  Voicebox app (voicebox.sh) must be RUNNING at %VOICEBOX_URL% and there is no
  silent fallback — if unreachable, tell the user to launch it rather than
  switching providers. Never generate silent audio.
- Images: imagegen defaults to model="z-image-turbo" (steps~8, quantize 4);
  pass model="schnell" for a faster/lower-fidelity FLUX fallback, or override
  steps/quantize directly. Only change these when the user asks for a
  different look or speed/quality tradeoff, not by default.
- Video: videogen defaults to LTX-2.3 (video_provider="ltx"), which turns each
  storyboard beat's action/camera_motion into a motion prompt. Use Ken Burns only
  when the user explicitly wants static/pan-only motion or as an LTX fallback.
  Tune LTX with steps/resolution ("WIDTHxHEIGHT")/clip_seconds/cfg_scale/
  stg_scale/prefer_extend when the user wants higher fidelity, a specific
  frame size, or longer/shorter clips; fallback_to_kenburns/kenburns_zoom
  control what happens when LTX fails — leave these at their defaults unless
  asked.

Bash stays available for ffprobe/file inspection and the `render` step: HTML/Manim
diagram scenes still render via `uv run python -m src.pipeline render
<scene_spec.json> <work_dir>`. The work_dir MUST be `<run_dir>/scenes` so the
output lands at `scenes/{segment_id}_render/…` — the composite manifest, QA,
and the Studio UI all look there and will report the render missing otherwise.
HTML scenes must implement the deterministic seek
contract (`window.seek(t)` + `window.__SCENE__`, see docs/collage/CONTRACTS.md).

EVERY segment REQUIRES `visual_engine` ("manim", "html", or "collage") —
script.json fails validation without it, including "scene" segments (where it
is unused by the clip path; use "html" there).
Segments have `visual_type`: "scene" (a FLUX photo → LTX motion clip; needs an
`image_prompt`, or preferably an ordered `visual_beats` list) or "diagram"
(HTML/Manim). For documentary/explainer segments where designed motion beats AI
video, set `visual_engine` to "collage" (keep `visual_type` "diagram") and author
`<run_dir>/scenes/{segment_id}.collage.json` per docs/collage/AUTHORING.md (golden
examples in docs/collage/examples/). Prefer `at_word`/`at_frac` TimeRefs over
absolute seconds; `at_word` refs REQUIRE align to have run first (whisper must be
installed — there is no estimated fallback). Segments may declare `sfx` cues mixed
under narration: `"sfx": [{"sound": "cannon_boom", "at_word": "cannon",
"gain_db": -10}]` — sounds are procedurally synthesized (%SFX_SOUNDS%);
keep gains subtle (-18..-8 dB); at_word cues need align first.

PRODUCTION CONTRACT — act like a producer, not a one-shot prompt bot:
1. Before generating, write a structured `<run_dir>/script.json` that includes:
   subject, canonical_name, audience, style_bible, narration_style,
   historical_constraints, visual_continuity_rules, forbidden_visuals,
   storyboard_summary, storyboard_rules, negative_prompt,
   pronunciation_dictionary, release_acceptance_criteria. Each segment should
   include visual_intent, visual_constraints, negative_prompt, production_notes,
   acceptance_criteria, and for scene segments `visual_beats` — ordered mini-shots:
   [{"beat_id":"b01","description":"...","shot_type":"wide","composition":"...",
   "action":"...","camera_motion":"slow push-in","continuity_notes":["..."],
   "asset_notes":["..."],"image_prompt":"...","weight":1.0}]. Keep each beat near
   2.5-3.5 seconds; scene segments longer than ~6 seconds usually need 2-4+ beats.
   Estimate each segment's `estimated_duration_seconds` from its narration word
   count at ~2.2 words/second (documentary narration pace) — do not guess a
   round number. A too-short estimate wastes a synthesize+QA cycle discovering
   the same thing the storyboard tool would have caught for free.
2. Treat the storyboard as a preproduction gate: run the storyboard tool before
   imagegen and revise script.json if it warns about too few beats, weak
   pacing, or an implausibly short estimated_duration_seconds for a segment's
   narration length — fix the estimate there rather than letting synthesize
   discover it.
3. Make the style bible concrete. Apply any selected preset style to every
   image_prompt and continuity rule; use correct canonical names and eras.
4. Prefer robust visuals over fragile AI motion. Avoid asking AI video to render
   readable text, banners with words, hands, detailed fingers, capes, flags,
   birds, horses, huge crowds, flames, or smoke unless essential — use diagrams,
   stills, overlays, or post-composited text instead. Never rely on generated
   text inside images; render text in HTML/Manim or composite it afterward. For
   scene beats, write `action` and `camera_motion` so LTX-2.3 brings the still
   image to life instead of merely panning across it.
5. After videogen run manifest, then composite, then ALWAYS qa. If QA fails,
   inspect the report (the qa tool always returns it), regenerate the failing
   segments WITH force=true (skip-if-exists means a forceless re-run is a
   no-op), composite again, and rerun QA. QA also cross-checks the final
   video's duration against the total narration (A/V sync): a drift error
   means the clips no longer cover the audio — regenerate the affected clips
   with force and re-composite. Do not call a video finished unless
   QA passes, or you explicitly tell the user what failed and ask for override.
6. For voice failures, regenerate the affected audio segment first. Audio much
   longer than the script estimate usually means either the estimate was too
   short for the narration's real word count (check the storyboard tool's
   warnings — fix estimated_duration_seconds and re-synthesize with force) or,
   if the estimate already looked reasonable, hallucinated speech.
7. When editing an existing run, preserve good approved artifacts and regenerate
   only the failed or requested segments.
""".replace("%STEP_ORDER%", _STEP_ORDER) \
   .replace("%SFX_SOUNDS%", ", ".join(sorted(_SFX_SOUNDS))) \
   .replace("%VOICEBOX_URL%", _VOICEBOX_URL)


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


def _stringify_tool_result(content: Any) -> str:
    """Flatten a ToolResultBlock.content payload to plain text."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


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
    project_ids_by_conversation: dict[str, str] = {}
    active_conversation_id = "default"
    # Run ids seen in this turn's tool inputs; the end-of-turn artifact scan
    # only trusts runs the turn actually touched.
    seen_run_ids_this_turn: set[str] = set()
    # The current turn's holder, so the PostToolUse hook can stream artifact
    # frames through whichever socket currently owns the turn.
    hook_state: dict[str, Any] = {"holder": None}
    # Once a send fails the socket is gone; stop trying (avoids log spam from
    # a turn task that keeps streaming after the client disconnected).
    conn_state = {"closed": False}

    async def _send(msg: dict[str, Any]) -> None:
        if conn_state["closed"]:
            return
        try:
            await websocket.send_json(msg)
        except Exception as exc:  # noqa: BLE001
            conn_state["closed"] = True
            logger.warning("ws send failed: %s", exc)

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
            UserMessage,
            ClaudeSDKClient,
        )

        from src.studio.agent_tools import STUDIO_TOOL_NAMES, build_studio_server

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
    # PostToolUse hook – emits artifact_updated frames through the turn's
    # holder so a reconnected socket that claimed the turn still gets them.
    # -----------------------------------------------------------------------
    async def _post_tool_use_hook(
        hook_input: PostToolUseHookInput,
        _transcript: str | None,
        _ctx: HookContext,
    ) -> dict[str, Any]:
        tool_name: str = hook_input.get("tool_name", "")
        if tool_name in _ARTIFACT_TOOLS or tool_name.startswith("mcp__studio__"):
            # Only a run id parsed from the tool input counts — falling back to
            # the previously-viewed run made run-less tools (one-shot
            # generate_video, generation_status, plain Bash) snap the viewer
            # to an unrelated run and mis-bind new chats.
            run_id = _extract_run_id(hook_input.get("tool_input", {}) or {})
            if run_id:
                active_run_ids_by_conversation[active_conversation_id] = run_id
                seen_run_ids_this_turn.add(run_id)
                holder = hook_state.get("holder")
                if holder is not None:
                    await holder["send"](
                        {
                            "type": "artifact_updated",
                            "run_id": run_id,
                            "conversation_id": active_conversation_id,
                        }
                    )
        return {}

    # In-process MCP server exposing the typed studio tools (stateless; one
    # instance per connection is fine).
    studio_server = build_studio_server()
    studio_tool_names = [f"mcp__studio__{name}" for name in STUDIO_TOOL_NAMES]

    # -----------------------------------------------------------------------
    # Turn task: owns the full ClaudeSDKClient lifecycle for one agent turn.
    # Runs as its own asyncio task so the WS receive loop stays free to handle
    # stop frames; `holder` exposes the live client for interrupt() and carries
    # the `stopped` flag into the done frame.
    # -----------------------------------------------------------------------
    async def _run_turn(
        conversation_id: str,
        user_text: str,
        options: Any,
        holder: dict[str, Any],
    ) -> None:
        async def _emit(msg: dict[str, Any]) -> None:
            # Route through the holder so a reconnected socket that claimed
            # this turn receives the rest of the stream.
            await holder["send"](msg)

        runs_before_query = _snapshot_run_scripts()
        tool_names_by_id: dict[str, str] = {}
        assistant_chunks: list[str] = []
        client = ClaudeSDKClient(options)
        holder["client"] = client
        error_sent = False
        try:
            await client.connect()
            await client.query(user_text)
            async for event in client.receive_response():
                # ---- SystemMessage: capture session_id ----
                if isinstance(event, SystemMessage):
                    new_sid = event.data.get("session_id")
                    if new_sid and new_sid != session_ids_by_conversation.get(conversation_id):
                        session_ids_by_conversation[conversation_id] = new_sid
                        # Persist the session id on the project's conversation
                        # record so the chat can resume from any browser.
                        convo_project = project_ids_by_conversation.get(conversation_id)
                        if convo_project:
                            try:
                                from src.studio import projects as projects_store

                                projects_store.upsert_conversation(
                                    convo_project,
                                    conversation_id,
                                    claude_session_id=new_sid,
                                )
                            except Exception:  # noqa: BLE001
                                logger.exception("failed to persist conversation session")
                        await _emit(
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
                                assistant_chunks.append(text)
                                await _emit(
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
                    if event.session_id and event.session_id != session_ids_by_conversation.get(
                        conversation_id
                    ):
                        session_ids_by_conversation[conversation_id] = event.session_id
                        await _emit(
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
                            tool_names_by_id[block.id] = block.name
                            summary = _tool_summary(block.name, block.input)
                            transcripts.append_event(
                                conversation_id,
                                {
                                    "type": "tool_use",
                                    "id": block.id,
                                    "name": block.name,
                                    "summary": summary,
                                },
                            )
                            await _emit(
                                {
                                    "type": "tool_use",
                                    "id": block.id,
                                    "name": block.name,
                                    "summary": summary,
                                    "conversation_id": conversation_id,
                                }
                            )

                # ---- UserMessage: carries ToolResultBlocks back from tools ----
                elif isinstance(event, UserMessage):
                    content = event.content
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, ToolResultBlock):
                                continue
                            frame: dict[str, Any] = {
                                "type": "tool_result",
                                "id": block.tool_use_id,
                                "name": tool_names_by_id.get(block.tool_use_id, ""),
                                "ok": not bool(block.is_error),
                                "conversation_id": conversation_id,
                            }
                            if block.is_error:
                                frame["error"] = _stringify_tool_result(block.content)[:200]
                            transcript_event = {
                                "type": "tool_result",
                                "id": block.tool_use_id,
                                "ok": frame["ok"],
                            }
                            if "error" in frame:
                                transcript_event["error"] = frame["error"]
                            transcripts.append_event(conversation_id, transcript_event)
                            await _emit(frame)

                # ---- ResultMessage: query finished ----
                elif isinstance(event, ResultMessage):
                    if event.session_id and event.session_id != session_ids_by_conversation.get(
                        conversation_id
                    ):
                        session_ids_by_conversation[conversation_id] = event.session_id
                        await _emit(
                            {
                                "type": "session",
                                "session_id": event.session_id,
                                "conversation_id": conversation_id,
                            }
                        )
                    # Surface turn failures instead of silently ending. A
                    # user-initiated stop also ends in an error result; skip it.
                    if event.is_error and not holder.get("stopped"):
                        message = event.result or f"agent turn failed ({event.subtype})"
                        transcripts.append_event(
                            conversation_id, {"type": "error", "message": message}
                        )
                        await _emit(
                            {
                                "type": "error",
                                "message": message,
                                "conversation_id": conversation_id,
                            }
                        )
                        error_sent = True

        except Exception as exc:  # noqa: BLE001
            logger.exception("Error during Claude turn: %s", exc)
            transcripts.append_event(
                conversation_id, {"type": "error", "message": str(exc)}
            )
            await _emit(
                {
                    "type": "error",
                    "message": str(exc),
                    "conversation_id": conversation_id,
                }
            )
            error_sent = True
        finally:
            holder["client"] = None
            if assistant_chunks:
                transcripts.append_event(
                    conversation_id,
                    {"role": "assistant", "text": "".join(assistant_chunks)},
                )
            try:
                await client.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("client disconnect failed: %s", exc)

        if error_sent:
            return

        runs_after_query = _snapshot_run_scripts()
        # Only runs this turn actually touched may steer the viewer; anything
        # else that changed concurrently (terminal work, another tab) is
        # ignored rather than hijacking the selection.
        changed_runs = _select_changed_runs(
            runs_before_query, runs_after_query, seen_run_ids_this_turn
        )
        if changed_runs:
            changed_run_id = changed_runs[0][0]
            active_run_ids_by_conversation[conversation_id] = changed_run_id
            # Runs created/touched by this chat belong to the chat's project.
            convo_project = project_ids_by_conversation.get(conversation_id)
            if convo_project:
                try:
                    from src.studio import projects as projects_store

                    for new_run_id, _ in changed_runs:
                        if new_run_id not in runs_before_query:
                            projects_store.assign_run(new_run_id, convo_project)
                except Exception:  # noqa: BLE001
                    logger.exception("failed to assign run to project")
            await _emit(
                {
                    "type": "artifact_updated",
                    "run_id": changed_run_id,
                    "conversation_id": conversation_id,
                }
            )

        await _emit(
            {
                "type": "done",
                "conversation_id": conversation_id,
                "run_id": active_run_ids_by_conversation.get(conversation_id),
                "stopped": bool(holder.get("stopped")),
            }
        )

    # -----------------------------------------------------------------------
    # Main message loop — never blocks on a turn; turns run as tasks.
    # -----------------------------------------------------------------------
    current_turn: dict[str, Any] | None = None

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

            msg_type = msg.get("type")
            turn_running = current_turn is not None and not current_turn["task"].done()

            if msg_type == "stop":
                if turn_running:
                    current_turn["holder"]["stopped"] = True
                    turn_client = current_turn["holder"].get("client")
                    if turn_client is not None:
                        try:
                            await turn_client.interrupt()
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("interrupt failed: %s", exc)
                continue

            if msg_type == "resume":
                # A reconnecting client asks to reclaim a turn that kept
                # running after its previous socket dropped.
                convo_id = str(msg.get("conversation_id") or "")
                detached = _DETACHED_TURNS.get(convo_id)
                if detached is not None and not detached["task"].done() and not turn_running:
                    _DETACHED_TURNS.pop(convo_id, None)
                    watchdog = detached.get("watchdog")
                    if watchdog is not None:
                        watchdog.cancel()
                    # Re-route the turn's stream to this socket.
                    detached["holder"]["send"] = _send
                    current_turn = {
                        "task": detached["task"],
                        "holder": detached["holder"],
                        "conversation_id": convo_id,
                    }
                    await _send({"type": "resumed", "conversation_id": convo_id})
                else:
                    # Nothing to reclaim — the turn already finished (or was
                    # never detached). Tell the client so it can settle from
                    # the server transcript.
                    await _send(
                        {
                            "type": "done",
                            "conversation_id": convo_id,
                            "run_id": None,
                            "stopped": False,
                        }
                    )
                continue

            if msg_type != "user_message":
                continue

            conversation_id = str(msg.get("conversation_id") or "default")
            if turn_running:
                await _send(
                    {
                        "type": "error",
                        "message": "a turn is already in progress",
                        "conversation_id": conversation_id,
                    }
                )
                continue

            # A detached turn (from a dropped socket) may still be running on
            # this conversation; the user's new message supersedes it.
            detached = _DETACHED_TURNS.pop(conversation_id, None)
            if detached is not None and not detached["task"].done():
                await _interrupt_detached_turn(detached)

            active_conversation_id = conversation_id
            user_text: str = msg.get("text", "")
            # Persist the user's original text before project/preset context is
            # appended below, so transcripts match what they actually typed.
            transcripts.append_event(conversation_id, {"role": "user", "text": user_text})
            requested_session_id = msg.get("session_id")
            if not isinstance(requested_session_id, str) or not requested_session_id:
                requested_session_id = session_ids_by_conversation.get(conversation_id)
            msg_run_id = msg.get("run_id")
            if isinstance(msg_run_id, str) and msg_run_id:
                active_run_ids_by_conversation[conversation_id] = msg_run_id

            # Track the conversation's project and inject project context so
            # every chat in a project shares awareness of its videos.
            msg_project_id = msg.get("project_id")
            if isinstance(msg_project_id, str) and msg_project_id:
                project_ids_by_conversation[conversation_id] = msg_project_id
            project_id = project_ids_by_conversation.get(conversation_id)
            if project_id:
                try:
                    from src.studio import projects as projects_store

                    project = next(
                        (p for p in projects_store.list_projects() if p["id"] == project_id),
                        None,
                    )
                except Exception:  # noqa: BLE001
                    project = None
                if project:
                    from src.studio.runs import get_run

                    run_lines = []
                    for rid in project["run_ids"][:12]:
                        try:
                            manifest = get_run(rid) or {}
                            title = manifest.get("title", rid)
                        except Exception:  # noqa: BLE001
                            title = rid
                        run_lines.append(f"  - {rid}: {title}")
                    other_chats = len(project.get("conversations", []))
                    project_ctx = (
                        f"\n\n[ACTIVE PROJECT: {project['name']} (id {project_id})]\n"
                        f"- This chat belongs to the project above; the project has "
                        f"{other_chats} chat(s) and {len(project['run_ids'])} video run(s).\n"
                    )
                    if run_lines:
                        project_ctx += (
                            "- Existing video runs in this project:\n" + "\n".join(run_lines) + "\n"
                            "When the user refers to 'the video', 'the storyboard', or asks for "
                            "edits without naming a run, prefer this project's runs (the active "
                            "run first). New videos you create in this chat belong to this "
                            "project automatically.\n"
                        )
                    user_text = user_text + project_ctx

            # Inject preset context so the agent knows the user's chosen style
            preset = msg.get("preset")
            if preset:
                style_pack = preset.get("style_pack")
                default_visual_engine = preset.get("default_visual_engine")
                collage_lines = ""
                if style_pack:
                    collage_lines += f"- Style pack: {style_pack}\n"
                if default_visual_engine:
                    collage_lines += f"- Default visual engine: {default_visual_engine}\n"
                if style_pack:
                    collage_lines += (
                        f"Set style_pack in script.json to '{style_pack}'. "
                        f"For collage segments follow docs/collage/AUTHORING.md and run the "
                        f"align/assets/collage tools between synthesize and manifest.\n"
                    )
                if default_visual_engine == "collage":
                    collage_lines += (
                        "Default segments to visual_engine 'collage' unless a segment "
                        "clearly needs manim math or an AI-motion scene.\n"
                    )
                sfx_style = preset.get("sfx_style")
                if sfx_style:
                    collage_lines += (
                        f"- Sound effects: {sfx_style} Declare `sfx` cues on segments in "
                        f"script.json and run the sfx tool after align.\n"
                    )
                # Voice: default is local Qwen3-TTS, but a preset can pin the
                # Voicebox voice studio (open-source app at 127.0.0.1:17493).
                if preset.get("tts_provider") == "voicebox":
                    voicebox_profile = preset.get("voicebox_profile", "Narrator")
                    voice_lines = (
                        f"Use the synthesize tool for voice, never silent audio. "
                        f"This preset uses the Voicebox voice studio: call mcp__studio__synthesize "
                        f"with voice_provider='voicebox' and voicebox_profile='{voicebox_profile}'. "
                        f"If synthesize fails because Voicebox is unreachable, tell the user to launch the "
                        f"Voicebox app (voicebox.sh) so it is listening at 127.0.0.1:17493 — do NOT silently "
                        f"switch to another TTS provider. "
                    )
                else:
                    voice_lines = (
                        f"Use the synthesize tool (Qwen3-TTS local) for voice, never silent audio: "
                        f"call mcp__studio__synthesize with speaker='{preset.get('voice_speaker', 'serena')}' "
                        f"and language='{preset.get('voice_language', 'english')}'. "
                    )
                if preset.get("qwen_model_size"):
                    voice_lines += (
                        f"Pass qwen_model_size='{preset['qwen_model_size']}' to synthesize. "
                    )

                # Generation-quality overrides: only present when the user has
                # explicitly customized them (Customize modal), so absence means
                # "use the tool's own defaults" — never invent values here.
                gen_params: list[str] = []
                if preset.get("image_model"):
                    gen_params.append(f"model='{preset['image_model']}'")
                if preset.get("image_steps") is not None:
                    gen_params.append(f"steps={preset['image_steps']}")
                if preset.get("image_quantize") is not None:
                    gen_params.append(f"quantize={preset['image_quantize']}")
                generation_lines = ""
                if gen_params:
                    generation_lines += (
                        f"- Image generation: call imagegen with {', '.join(gen_params)}.\n"
                    )
                ltx_params: list[str] = []
                if preset.get("ltx_steps") is not None:
                    ltx_params.append(f"steps={preset['ltx_steps']}")
                if preset.get("ltx_resolution"):
                    ltx_params.append(f"resolution='{preset['ltx_resolution']}'")
                if preset.get("ltx_clip_seconds") is not None:
                    ltx_params.append(f"clip_seconds={preset['ltx_clip_seconds']}")
                if preset.get("ltx_cfg_scale") is not None:
                    ltx_params.append(f"cfg_scale={preset['ltx_cfg_scale']}")
                if preset.get("ltx_stg_scale") is not None:
                    ltx_params.append(f"stg_scale={preset['ltx_stg_scale']}")
                if preset.get("ltx_prefer_extend") is not None:
                    ltx_params.append(f"prefer_extend={preset['ltx_prefer_extend']}")
                if preset.get("video_fallback_to_kenburns") is not None:
                    ltx_params.append(f"fallback_to_kenburns={preset['video_fallback_to_kenburns']}")
                if preset.get("kenburns_zoom") is not None:
                    ltx_params.append(f"kenburns_zoom={preset['kenburns_zoom']}")
                if ltx_params:
                    generation_lines += (
                        f"- Video motion tuning: call videogen with {', '.join(ltx_params)}.\n"
                    )

                preset_ctx = (
                    f"\n\n[ACTIVE PRESET: {preset.get('name', '?')}]\n"
                    f"- Image style: {preset.get('style_prompt', '')}\n"
                    f"- Narration style: {preset.get('narration_style', '')}\n"
                    f"- Target length: {preset.get('video_length_minutes', '?')} minutes\n"
                    f"- Voice: speaker={preset.get('voice_speaker', 'serena')}, language={preset.get('voice_language', 'english')}\n"
                    f"- Video motion: {preset.get('video_provider', 'ltx')}\n"
                    f"{collage_lines}"
                    f"{generation_lines}"
                    f"IMPORTANT: Use these settings when generating the video. "
                    f"Call the videogen tool with video_provider='ltx' so LTX-2.3 animates the storyboard action; use Ken Burns only if explicitly requested or as fallback. "
                    f"Put the image style and narration style into script.json as style_bible and narration_style. "
                    f"Prefix ALL segment image_prompt and visual_beats image_prompt values with the style prompt above. "
                    f"For scene segments longer than ~6 seconds, write 2-4 visual_beats with storyboard fields "
                    f"(description, shot_type, composition, action, camera_motion) so LTX can bring each still's action to life and the final video changes visuals every 3-6 seconds. "
                    f"{voice_lines}"
                    f"Before imagegen, run the storyboard tool and fix its warnings. "
                    f"After videogen, run the manifest tool before compositing. "
                    f"After compositing, run the qa tool and fix failures.\n"
                )
                user_text = user_text + preset_ctx

            # Build options
            options = ClaudeAgentOptions(
                cwd=_REPO_ROOT,
                model=config.agent_model(),
                system_prompt={
                    "type": "preset",
                    "preset": "claude_code",
                    "append": _STUDIO_BRIEF + "\n" + capabilities.summary_line(),
                },
                # bypassPermissions: this app has no interactive prompt UI for the
                # agent's own tool calls, so any tool not pre-approved would hang
                # forever waiting on a permission nobody can grant (this is what
                # broke WebSearch above). allowed_tools is kept as documentation
                # of the expected tool surface, but bypassPermissions is what
                # actually lets every one of them (and any future built-in tool)
                # run without getting stuck.
                permission_mode="bypassPermissions",
                mcp_servers={"studio": studio_server},
                allowed_tools=[
                    "Read",
                    "Write",
                    "Edit",
                    "Bash",
                    "Glob",
                    "Grep",
                    "Task",
                    "Skill",
                    "WebSearch",
                    "WebFetch",
                    "NotebookEdit",
                    "TodoWrite",
                    *studio_tool_names,
                ],
                setting_sources=["project"],
                skills="all",
                include_partial_messages=True,
                resume=requested_session_id,
                stderr=lambda line: logger.debug("agent stderr: %s", line),
                hooks={
                    "PostToolUse": [
                        HookMatcher(hooks=[_post_tool_use_hook])
                    ]
                },
            )

            seen_run_ids_this_turn.clear()
            holder: dict[str, Any] = {"client": None, "stopped": False, "send": _send}
            hook_state["holder"] = holder
            task = asyncio.create_task(
                _run_turn(conversation_id, user_text, options, holder)
            )
            current_turn = {
                "task": task,
                "holder": holder,
                "conversation_id": conversation_id,
            }

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
        conn_state["closed"] = True
        # Don't kill an in-flight turn just because the socket dropped — it
        # already streams into the server transcript and a chat-driven
        # production would die half-finished. Detach it instead: a
        # reconnecting client can claim it, and the watchdog interrupts it
        # after a grace window so no CLI subprocess runs unattended forever.
        if current_turn is not None and not current_turn["task"].done():
            _detach_turn(
                current_turn["conversation_id"],
                {"task": current_turn["task"], "holder": current_turn["holder"]},
            )
