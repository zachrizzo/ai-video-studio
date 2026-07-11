"""Typed SDK MCP tools exposing the video pipeline to the chat agent.

Pipeline steps run `uv run python -m src.pipeline <step> ...` as subprocesses
(same contract as src/pipeline.py); studio operations (runs, producer,
one-shot generations) call the backend modules directly. Every tool returns a
compact JSON payload in its content block and signals failure with is_error
instead of raising.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from src.studio.presets import split_resolution

# Repository root (two levels up from this file: src/studio/agent_tools.py)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT_TAIL_CHARS = 4000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_run_dir(run_id: Any) -> Path:
    """Reject traversal and resolve under the runs root (producer._safe_run_dir)."""
    from src.studio.producer import _safe_run_dir

    return _safe_run_dir(run_id)


def _ok(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}]}


def _err(message: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"error": message}
    if extra:
        data.update(extra)
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}],
        "is_error": True,
    }


async def _kill_process_group(proc: "asyncio.subprocess.Process") -> None:
    """SIGTERM the process group led by `proc`, escalating to SIGKILL if it
    hasn't exited within 5s. `start_new_session=True` on the child makes its
    pid equal its pgid, so this reaps the whole tree `uv` spawns (its python
    child, and that child's own mflux/ffmpeg/LTX children) — not just the
    immediate `uv` process. SIGTERM first: pipeline steps hold the
    cross-process generation lock and should release it cleanly if they can.
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
        return
    except asyncio.TimeoutError:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _run_pipeline(
    step: str,
    argv: list[str],
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Run one pipeline step; return (exit_code, tail of merged stdout+stderr)."""
    env = os.environ.copy()
    if env_overrides:
        env.update({k: v for k, v in env_overrides.items() if v})
    proc = await asyncio.create_subprocess_exec(
        "uv", "run", "python", "-m", "src.pipeline", step, *argv,
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        out, _ = await proc.communicate()
    except BaseException:
        # Cancellation (chat Stop) or any other error while awaiting the
        # subprocess must not orphan it — reap the whole process group before
        # letting the exception (e.g. CancelledError) propagate.
        await _kill_process_group(proc)
        raise
    text = out.decode("utf-8", errors="replace")
    return proc.returncode or 0, text[-_OUTPUT_TAIL_CHARS:]


def _step_result(step: str, code: int, output: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"step": step, "exit_code": code, "output": output}
    if extra:
        data.update(extra)
    result = _ok(data)
    if code != 0:
        result["is_error"] = True
    return result


def _script_path(run_dir: Path) -> Path:
    script = run_dir / "script.json"
    if not script.exists():
        raise FileNotFoundError(f"{script} not found — write script.json first")
    return script


# JSON Schema fragments (dict-style schemas mark every param required, so
# tools with optional params use explicit JSON Schema).
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_NUM = {"type": "number"}


def _schema(properties: dict[str, dict[str, Any]], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


# ---------------------------------------------------------------------------
# Pipeline-step tools (subprocess)
# ---------------------------------------------------------------------------


@tool("create_run", "Create a new run directory for a video (pipeline setup). "
      "Returns run_id and run_dir; write <run_dir>/script.json next.", {})
async def create_run_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio.runs import _runs_root

    # Must match the root every other tool/list_runs/get_run resolves against
    # (src.studio.runs._runs_root) — a stale hardcoded path here silently
    # creates runs the Studio UI's flow viewer can never find or display.
    code, out = await _run_pipeline("setup", [str(_runs_root())])
    return _step_result("setup", code, out)


def _simple_step(name: str, description: str, argv_builder):
    """Register a run_id-only pipeline step tool."""

    @tool(name, description, {"run_id": str})
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            run_dir = _resolve_run_dir(args.get("run_id"))
            argv = argv_builder(run_dir)
        except (ValueError, FileNotFoundError) as exc:
            return _err(str(exc))
        code, out = await _run_pipeline(name, argv)
        return _step_result(name, code, out)

    return handler


storyboard_tool = _simple_step(
    "storyboard",
    "Build <run_dir>/storyboard.json from the script's visual beats and flag weak pacing. "
    "Run before imagegen and fix warnings first.",
    lambda run_dir: [str(_script_path(run_dir)), str(run_dir)],
)

align_tool = _simple_step(
    "align",
    "Word-level narration alignment (whisper) -> audio/alignment.json. Required before "
    "sfx/collage at_word refs.",
    lambda run_dir: [str(_script_path(run_dir)), str(run_dir)],
)

sfx_tool = _simple_step(
    "sfx",
    "Mix declared sfx cues under segment narration (run after align; no-op without sfx).",
    lambda run_dir: [str(_script_path(run_dir)), str(run_dir)],
)

manifest_tool = _simple_step(
    "manifest",
    "Build <run_dir>/composite_manifest.json from script order and existing artifacts. "
    "Run after videogen, before composite.",
    lambda run_dir: [str(_script_path(run_dir)), str(run_dir)],
)


@tool("synthesize",
      "Synthesize narration for all script segments into <run_dir>/audio. Defaults to local "
      "Qwen3-TTS; pass voice_provider='voicebox' + voicebox_profile for Voicebox voices "
      "(the Voicebox app must be running; no silent fallback). qwen_model_size ('0.6B' "
      "default or '1.7B' for higher quality) applies to the Qwen path; language applies "
      "to whichever provider is active. Skips segments that already have a good take "
      "unless force=true; re-uses the run's persisted voice on resume unless voice "
      "params are passed explicitly. Fails (is_error) when any segment's TTS fails or "
      "its audio still carries QA issues — fix those before videogen/composite.",
      _schema({"run_id": _STR, "voice_provider": _STR, "speaker": _STR,
               "language": _STR, "voicebox_profile": _STR, "qwen_model_size": _STR,
               "force": _BOOL}, ["run_id"]))
async def synthesize_tool(args: dict[str, Any]) -> dict[str, Any]:
    try:
        run_dir = _resolve_run_dir(args.get("run_id"))
        script = _script_path(run_dir)
    except (ValueError, FileNotFoundError) as exc:
        return _err(str(exc))
    language = args.get("language", "")
    env = {
        "PTV_VOICE_PROVIDER": args.get("voice_provider", ""),
        "PTV_QWEN_TTS_SPEAKER": args.get("speaker", ""),
        # Both providers read their own language env var — set from the same
        # `language` arg so callers don't need to know which is active.
        "PTV_QWEN_TTS_LANGUAGE": language,
        "PTV_VOICEBOX_LANGUAGE": language,
        "PTV_VOICEBOX_PROFILE": args.get("voicebox_profile", ""),
        "PTV_QWEN_TTS_MODEL_SIZE": args.get("qwen_model_size", ""),
    }
    if args.get("force"):
        env["PTV_AUDIO_FORCE"] = "true"
    code, out = await _run_pipeline("synthesize", [str(script), str(run_dir / "audio")], env)
    return _step_result("synthesize", code, out)


def _filtered_step(name: str, description: str, force_env: str = ""):
    """Register a pipeline step taking run_id + optional segment_ids filter.

    force_env: when set, the tool grows an optional boolean `force` param that
    maps to that PTV_*_FORCE env var for the subprocess.
    """
    properties = {"run_id": _STR, "segment_ids": _STR}
    if force_env:
        properties["force"] = _BOOL

    @tool(name, description, _schema(properties, ["run_id"]))
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            run_dir = _resolve_run_dir(args.get("run_id"))
            script = _script_path(run_dir)
        except (ValueError, FileNotFoundError) as exc:
            return _err(str(exc))
        argv = [str(script), str(run_dir)]
        segment_ids = args.get("segment_ids", "")
        if segment_ids:
            argv.append(segment_ids)
        env = {force_env: "true"} if force_env and args.get("force") else None
        code, out = await _run_pipeline(name, argv, env)
        return _step_result(name, code, out)

    return handler


assets_tool = _filtered_step(
    "assets",
    "Generate CollageSpec assets (FLUX + optional cutouts) for collage segments. "
    "segment_ids: optional comma-separated filter.",
)

collage_tool = _filtered_step(
    "collage",
    "Build and render collage scenes from scenes/{id}.collage.json specs. "
    "segment_ids: optional comma-separated filter.",
)

@tool("imagegen",
      "Generate still images for scene segment visual beats. Defaults to "
      "z-image-turbo (model='z-image-turbo'); pass model='schnell' for the "
      "faster/lower-quality FLUX fallback. steps/quantize override the "
      "model's defaults (z-image-turbo: steps~8, quantize 4). Skips beats "
      "whose PNG already exists; to regenerate failing segments (e.g. in a "
      "QA fix loop) pass force=true with segment_ids. segment_ids: optional "
      "comma-separated segment/beat filter.",
      _schema({"run_id": _STR, "segment_ids": _STR, "model": _STR,
               "steps": _NUM, "quantize": _NUM, "force": _BOOL}, ["run_id"]))
async def imagegen_tool(args: dict[str, Any]) -> dict[str, Any]:
    try:
        run_dir = _resolve_run_dir(args.get("run_id"))
        script = _script_path(run_dir)
    except (ValueError, FileNotFoundError) as exc:
        return _err(str(exc))
    argv = [str(script), str(run_dir)]
    segment_ids = args.get("segment_ids", "")
    if segment_ids:
        argv.append(segment_ids)
    env = {
        "PTV_IMAGE_MODEL": args.get("model", ""),
        "PTV_IMAGE_STEPS": str(args["steps"]) if args.get("steps") is not None else "",
        "PTV_IMAGE_QUANTIZE": str(args["quantize"]) if args.get("quantize") is not None else "",
    }
    if args.get("force"):
        env["PTV_IMAGE_FORCE"] = "true"
    code, out = await _run_pipeline("imagegen", argv, env)
    return _step_result("imagegen", code, out)


@tool("videogen",
      "Turn scene stills into motion clips matching each segment's audio duration. "
      "Defaults to LTX-2.3 (video_provider='ltx'); 'kenburns' only for static/pan-only "
      "motion or fallback. steps/resolution ('WIDTHxHEIGHT')/clip_seconds/cfg_scale/"
      "stg_scale/prefer_extend tune LTX generation; fallback_to_kenburns (default true) "
      "and kenburns_zoom control the fallback used when LTX fails. Skips beats whose "
      "clip already exists; to actually redo clips (e.g. after audio changed or in a "
      "QA fix loop) pass force=true with segment_ids. segment_ids: optional "
      "comma-separated filter.",
      _schema({"run_id": _STR, "segment_ids": _STR, "video_provider": _STR,
               "steps": _NUM, "resolution": _STR, "clip_seconds": _NUM,
               "cfg_scale": _NUM, "stg_scale": _NUM, "prefer_extend": _BOOL,
               "fallback_to_kenburns": _BOOL, "kenburns_zoom": _NUM,
               "force": _BOOL}, ["run_id"]))
async def videogen_tool(args: dict[str, Any]) -> dict[str, Any]:
    try:
        run_dir = _resolve_run_dir(args.get("run_id"))
        script = _script_path(run_dir)
    except (ValueError, FileNotFoundError) as exc:
        return _err(str(exc))
    argv = [str(script), str(run_dir)]
    segment_ids = args.get("segment_ids", "")
    if segment_ids:
        argv.append(segment_ids)
    env = {"PTV_VIDEO_PROVIDER": args.get("video_provider", "")}
    if args.get("steps") is not None:
        env["PTV_LTX_STEPS"] = str(args["steps"])
    resolution = args.get("resolution", "")
    if resolution:
        split = split_resolution(resolution)
        if split is None:
            return _err(f"invalid resolution {resolution!r} — expected 'WIDTHxHEIGHT', e.g. '704x448'")
        env["PTV_LTX_GEN_WIDTH"], env["PTV_LTX_GEN_HEIGHT"] = split
    if args.get("clip_seconds") is not None:
        env["PTV_LTX_CLIP_SECONDS"] = str(args["clip_seconds"])
    if args.get("cfg_scale") is not None:
        env["PTV_LTX_CFG_SCALE"] = str(args["cfg_scale"])
    if args.get("stg_scale") is not None:
        env["PTV_LTX_STG_SCALE"] = str(args["stg_scale"])
    if "prefer_extend" in args:
        env["PTV_LTX_PREFER_EXTEND"] = "true" if args["prefer_extend"] else "false"
    if "fallback_to_kenburns" in args:
        env["PTV_VIDEO_FALLBACK_TO_KENBURNS"] = "true" if args["fallback_to_kenburns"] else "false"
    if args.get("kenburns_zoom") is not None:
        env["PTV_KENBURNS_ZOOM"] = str(args["kenburns_zoom"])
    if args.get("force"):
        env["PTV_VIDEO_FORCE"] = "true"
    code, out = await _run_pipeline("videogen", argv, env)
    return _step_result("videogen", code, out)


@tool("composite",
      "Composite the final video from <run_dir>/composite_manifest.json (run manifest first). "
      "output_name: optional file name inside the run dir (default final.mp4).",
      _schema({"run_id": _STR, "output_name": _STR}, ["run_id"]))
async def composite_tool(args: dict[str, Any]) -> dict[str, Any]:
    try:
        run_dir = _resolve_run_dir(args.get("run_id"))
    except ValueError as exc:
        return _err(str(exc))
    manifest = run_dir / "composite_manifest.json"
    if not manifest.exists():
        return _err(f"{manifest} not found — run the manifest tool first")
    output_name = args.get("output_name") or "final.mp4"
    if "/" in output_name or "\\" in output_name or ".." in output_name:
        return _err(f"invalid output_name: {output_name!r}")
    output = run_dir / output_name
    code, out = await _run_pipeline("composite", [str(manifest), str(output)])
    return _step_result("composite", code, out, {"output_path": str(output)})


@tool("qa",
      "Run release QA for a run and write qa_report.json. Always run after composite; "
      "fix failures and rerun before calling a video finished.",
      _schema({"run_id": _STR, "strict": _BOOL}, ["run_id"]))
async def qa_tool(args: dict[str, Any]) -> dict[str, Any]:
    try:
        run_dir = _resolve_run_dir(args.get("run_id"))
    except ValueError as exc:
        return _err(str(exc))
    argv = [str(run_dir)]
    if args.get("strict"):
        argv.append("strict")
    code, out = await _run_pipeline("qa", argv)
    # Attach the parsed report unconditionally: cmd_qa exits 0 on warnings, and
    # the truncated stdout tail can cut off the report's head — the agent must
    # always see the full structured result.
    extra: dict[str, Any] = {}
    report_path = run_dir / "qa_report.json"
    if report_path.exists():
        try:
            extra["qa_report"] = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return _step_result("qa", code, out, extra)


# ---------------------------------------------------------------------------
# Direct-call tools (runs / projects / producer)
# ---------------------------------------------------------------------------


@tool("list_runs", "List every video run (id, title, segment count, QA/production status).", {})
async def list_runs_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import runs

    return _ok(runs.list_runs())


@tool("get_run", "Full manifest for one run: segments with statuses, media URLs, "
      "storyboard frames, QA and production state.", {"run_id": str})
async def get_run_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import runs

    try:
        _resolve_run_dir(args.get("run_id"))
    except ValueError as exc:
        return _err(str(exc))
    manifest = runs.get_run(args["run_id"])
    if manifest is None:
        return _err(f"run not found: {args['run_id']}")
    return _ok(manifest)


@tool("list_projects", "List projects with their run ids and conversations.", {})
async def list_projects_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import projects

    return _ok(projects.list_projects())


@tool("capabilities", "Probe which local engines are available right now "
      "(voicebox, whisper, ffmpeg, mflux, ltx). Bypasses the cache.", {})
async def capabilities_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import capabilities

    return _ok(capabilities.probe(force=True))


@tool("produce_run",
      "Start (or resume) background production of a run that already has script.json. "
      "mode: 'full' (default), 'videos' (keep images, redo clips onward), or 'clips' "
      "(selected repair clips only). Existing clips are skipped, so mode 'videos' "
      "requires force_video=true to actually redo clips. preset_id: apply a saved "
      "style preset's voice/quality/video-provider settings to this production (snapshot "
      "persists across resumes of the same run). Poll production_status for progress.",
      _schema({"run_id": _STR, "mode": _STR, "force_video": _BOOL, "segment_ids": _STR,
               "preset_id": _STR},
              ["run_id"]))
async def produce_run_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import producer

    try:
        status = producer.start_run_production(
            args.get("run_id", ""),
            mode=args.get("mode") or "full",
            force_video=bool(args.get("force_video")),
            segment_ids=args.get("segment_ids") or "",
            preset_id=args.get("preset_id") or "",
        )
    except FileNotFoundError:
        return _err(f"run not found (or missing script.json): {args.get('run_id')}")
    except ValueError as exc:
        return _err(str(exc))
    return _ok(status)


@tool("production_status", "Current production job status for a run (step, progress, logs).",
      {"run_id": str})
async def production_status_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import producer

    try:
        status = producer.get_run_production_status(args.get("run_id", ""))
    except FileNotFoundError:
        return _err(f"run not found (or missing script.json): {args.get('run_id')}")
    except ValueError as exc:
        return _err(str(exc))
    return _ok(status)


@tool("stop_production", "Stop a background produce_run job for a run (idempotent — "
      "a no-op if nothing is currently running). This is separate from the chat's Stop "
      "button, which only interrupts in-flight tool calls in this conversation; use "
      "this tool when the user wants to halt a full background production.",
      {"run_id": str})
async def stop_production_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import producer

    try:
        # stop_run_production can block (killing the process group, joining
        # the producer thread) for several seconds — run it off the event
        # loop so it doesn't freeze this chat connection's turn processing.
        status = await asyncio.to_thread(producer.stop_run_production, args.get("run_id", ""))
    except FileNotFoundError:
        return _err(f"run not found (or missing script.json): {args.get('run_id')}")
    except ValueError as exc:
        return _err(str(exc))
    return _ok(status)


# ---------------------------------------------------------------------------
# One-shot generation tools (Generate tab backend)
# ---------------------------------------------------------------------------


@tool("generate_image", "Generate one standalone FLUX image (no run needed). "
      "Returns a gen_id to poll with generation_status.", {"prompt": str})
async def generate_image_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import generate

    gen_id = generate.start_image_generation(args["prompt"])
    return _ok({"gen_id": gen_id, "status": "generating"})


@tool("generate_video",
      "Generate one standalone LTX video clip (no run needed). Generates a FLUX image "
      "first unless image_path is given. Returns a gen_id to poll with generation_status.",
      _schema({"prompt": _STR, "image_path": _STR}, ["prompt"]))
async def generate_video_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import generate

    gen_id = generate.start_video_generation(args["prompt"], args.get("image_path") or None)
    return _ok({"gen_id": gen_id, "status": "generating"})


@tool("retake_video",
      "Regenerate a time window of an existing video with a new prompt (LTX retake). "
      "start_time/duration in seconds. Returns a gen_id.",
      _schema({"video_path": _STR, "start_time": _NUM, "duration": _NUM, "prompt": _STR},
              ["video_path", "start_time", "duration", "prompt"]))
async def retake_video_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import generate

    gen_id = generate.start_retake_video(
        video_uri=args["video_path"],
        start_time=float(args["start_time"]),
        duration=float(args["duration"]),
        prompt=args["prompt"],
    )
    return _ok({"gen_id": gen_id, "status": "generating"})


@tool("extend_video",
      "Extend an existing video with generated frames (LTX extend). mode: 'from_end' "
      "(default) or 'from_start'; duration: seconds to add. Returns a gen_id.",
      _schema({"video_path": _STR, "prompt": _STR, "mode": _STR, "duration": _NUM},
              ["video_path", "prompt"]))
async def extend_video_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import generate

    gen_id = generate.start_extend_video(
        video_uri=args["video_path"],
        prompt=args["prompt"],
        mode=args.get("mode") or "from_end",
        duration=float(args["duration"]) if args.get("duration") is not None else None,
    )
    return _ok({"gen_id": gen_id, "status": "generating"})


@tool("video_hdr", "Upscale a video to HDR via LTX cloud (needs PTV_LTX_API_KEY). "
      "Returns a gen_id.", {"video_uri": str})
async def video_hdr_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import generate

    gen_id = generate.start_video_hdr(args["video_uri"])
    return _ok({"gen_id": gen_id, "status": "generating"})


@tool("tts",
      "Synthesize one standalone speech clip (no run needed). Defaults to local Qwen3-TTS; "
      "provider='voicebox' + voicebox_profile uses the Voicebox app. Returns a gen_id.",
      _schema({"text": _STR, "speaker": _STR, "language": _STR, "provider": _STR,
               "voicebox_profile": _STR}, ["text"]))
async def tts_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import generate

    # Only pass what the caller supplied — generate.start_tts owns the one-shot
    # voice defaults, so they cannot drift from a second copy here.
    kwargs: dict[str, Any] = {}
    if args.get("speaker"):
        kwargs["speaker"] = args["speaker"]
    if args.get("language"):
        kwargs["language"] = args["language"]
    gen_id = generate.start_tts(
        text=args["text"],
        provider=args.get("provider") or None,
        voicebox_profile=args.get("voicebox_profile") or None,
        **kwargs,
    )
    return _ok({"gen_id": gen_id, "status": "generating"})


@tool("generation_status", "Status of a one-shot generation (from generate_*/tts tools).",
      {"gen_id": str})
async def generation_status_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import generate

    status = generate.get_generation(args["gen_id"])
    if status is None:
        return _err(f"generation not found: {args['gen_id']}")
    return _ok(status)


@tool("list_generations", "List the most recent one-shot generations.", {})
async def list_generations_tool(args: dict[str, Any]) -> dict[str, Any]:
    from src.studio import generate

    return _ok(generate.list_generations())


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

_TOOLS = [
    create_run_tool,
    storyboard_tool,
    synthesize_tool,
    align_tool,
    sfx_tool,
    assets_tool,
    collage_tool,
    imagegen_tool,
    videogen_tool,
    manifest_tool,
    composite_tool,
    qa_tool,
    list_runs_tool,
    get_run_tool,
    list_projects_tool,
    capabilities_tool,
    produce_run_tool,
    production_status_tool,
    stop_production_tool,
    generate_image_tool,
    generate_video_tool,
    retake_video_tool,
    extend_video_tool,
    video_hdr_tool,
    tts_tool,
    generation_status_tool,
    list_generations_tool,
]

STUDIO_TOOL_NAMES: list[str] = [t.name for t in _TOOLS]

# Raw async handlers by tool name, for tests and direct invocation.
TOOL_HANDLERS = {t.name: t.handler for t in _TOOLS}


def build_studio_server():
    """Create the in-process MCP server exposing the studio tools."""
    return create_sdk_mcp_server(name="studio", version="1.0.0", tools=_TOOLS)
