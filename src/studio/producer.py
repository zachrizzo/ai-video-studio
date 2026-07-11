"""Resumable production jobs for Studio flows."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.studio import presets
from src.studio.runs import _runs_root

STATUS_FILE = ".production_status.json"
PRESET_SNAPSHOT_FILE = ".production_preset.json"
MAX_LOG_LINE_CHARS = 2000

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATUS_LOCK = threading.Lock()
_ACTIVE: dict[str, "ProductionJob"] = {}


class ProductionStopped(Exception):
    """Raised by _run_command when a step's process was killed by a deliberate
    stop_run_production call, so _run_production reports "stopped" instead of
    treating the resulting nonzero exit code as a genuine failure."""


@dataclass
class ProductionJob:
    run_id: str
    thread: threading.Thread
    mode: str = "full"
    force_video: bool = False
    segment_ids: str = ""
    process: subprocess.Popen[str] | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)


def _now() -> float:
    return time.time()


def _safe_run_dir(run_id: str) -> Path:
    """Resolve run_id under the runs root, rejecting traversal.

    Canonical validator — agent_tools._resolve_run_dir delegates here so the
    producer and the chat tools can never disagree on what a valid run id is.
    """
    if (
        not isinstance(run_id, str)
        or "/" in run_id
        or "\\" in run_id
        or run_id in {"", ".", ".."}
    ):
        raise ValueError(f"invalid run id: {run_id!r}")

    root = _runs_root().resolve()
    run_dir = (root / run_id).resolve()
    if run_dir == root or root not in run_dir.parents:
        raise ValueError(f"invalid run id: {run_id!r}")
    return run_dir


def _status_path(run_dir: Path) -> Path:
    return run_dir / STATUS_FILE


def _load_status_file(run_dir: Path) -> dict[str, Any] | None:
    path = _status_path(run_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_status(run_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    data["updated_at"] = _now()
    logs = data.get("logs")
    if isinstance(logs, list) and len(logs) > 200:
        data["logs"] = logs[-200:]

    with _STATUS_LOCK:
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = _status_path(run_dir).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_status_path(run_dir))
    return data


def _default_status(run_id: str, run_dir: Path) -> dict[str, Any]:
    final_path = run_dir / "final.mp4"
    status = "done" if final_path.exists() else "idle"
    return {
        "run_id": run_id,
        "status": status,
        "step": None,
        "step_label": "Done" if status == "done" else "Ready",
        "progress": 100 if status == "done" else 0,
        "total_steps": 0,
        "started_at": None,
        "finished_at": final_path.stat().st_mtime if final_path.exists() else None,
        "updated_at": final_path.stat().st_mtime if final_path.exists() else _now(),
        "error": None,
        "logs": [],
    }


def get_run_production_status(run_id: str) -> dict[str, Any]:
    """Return the persisted production status for a run."""
    run_dir = _safe_run_dir(run_id)
    if not run_dir.is_dir() or not (run_dir / "script.json").exists():
        raise FileNotFoundError(run_id)

    status = _load_status_file(run_dir) or _default_status(run_id, run_dir)
    if status.get("status") == "running" and run_id not in _ACTIVE:
        status = {
            **status,
            "status": "stalled",
            "error": "The previous production job stopped before finishing.",
            "step_label": "Stalled",
        }
        _write_status(run_dir, status)
    return status


def _initial_status(
    run_id: str,
    step_count: int,
    mode: str = "full",
    force_video: bool = False,
    segment_ids: str = "",
) -> dict[str, Any]:
    started = _now()
    return {
        "run_id": run_id,
        "mode": mode,
        "force_video": force_video,
        "segment_ids": segment_ids,
        "status": "running",
        "step": None,
        "step_label": "Starting",
        "progress": 0,
        "total_steps": step_count,
        "started_at": started,
        "finished_at": None,
        "updated_at": started,
        "error": None,
        "logs": [],
    }


def _append_log(status: dict[str, Any], line: str) -> None:
    clean = line.rstrip()
    if not clean:
        return
    if len(clean) > MAX_LOG_LINE_CHARS:
        clean = f"{clean[:MAX_LOG_LINE_CHARS]} ... [truncated]"
    logs = status.setdefault("logs", [])
    if isinstance(logs, list):
        logs.append(clean)
        if len(logs) > 200:
            del logs[:-200]


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """SIGTERM the process group led by `proc`, escalating to SIGKILL if it
    hasn't exited within 5s. `start_new_session=True` on the child makes its
    pid equal its pgid, so this reaps the whole tree the pipeline CLI spawns
    (mflux/ffmpeg/LTX subprocesses), not just the immediate child. SIGTERM
    first: pipeline steps hold the cross-process generation lock and should
    release it cleanly if they can.
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def _run_command(
    *,
    run_dir: Path,
    job: ProductionJob,
    status: dict[str, Any],
    step: str,
    step_label: str,
    progress: int,
    total_steps: int,
    args: list[str],
    env: dict[str, str],
) -> None:
    status.update(
        {
            "status": "running",
            "step": step,
            "step_label": step_label,
            "progress": progress,
            "total_steps": total_steps,
            "error": None,
        }
    )
    _append_log(status, f"$ {' '.join(args)}")
    _write_status(run_dir, status)

    proc = subprocess.Popen(
        args,
        cwd=str(_PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    job.process = proc

    if job.stop_event.is_set():
        # Stop was requested in the window between the step loop's check and
        # this Popen call returning, so nothing was there yet for
        # stop_run_production to kill. Catch it here instead of letting the
        # step run unsupervised until the next loop iteration.
        _kill_process_group(proc)
        job.process = None
        raise ProductionStopped(f"{step_label} stopped before start")

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _append_log(status, line)
            _write_status(run_dir, status)

        return_code = proc.wait()
    except BaseException:
        _kill_process_group(proc)
        raise
    finally:
        job.process = None

    if return_code != 0:
        if job.stop_event.is_set():
            raise ProductionStopped(f"{step_label} stopped (exit code {return_code})")
        raise RuntimeError(f"{step_label} failed with exit code {return_code}")


def _pipeline_steps(
    run_dir: Path,
    mode: str = "full",
    segment_ids: str = "",
) -> list[tuple[str, str, list[str]]]:
    script_path = run_dir / "script.json"
    manifest_path = run_dir / "composite_manifest.json"
    final_path = run_dir / "final.mp4"
    base = [sys.executable, "-m", "src.pipeline"]
    storyboard = ("storyboard", "Building storyboard", base + ["storyboard", str(script_path), str(run_dir)])
    update_storyboard = (
        "storyboard",
        "Updating storyboard timing",
        base + ["storyboard", str(script_path), str(run_dir)],
    )
    synthesize = (
        "synthesize",
        "Synthesizing narration",
        base + ["synthesize", str(script_path), str(run_dir / "audio")],
    )
    imagegen_args = base + ["imagegen", str(script_path), str(run_dir)]
    videogen_args = base + ["videogen", str(script_path), str(run_dir)]
    assets_args = base + ["assets", str(script_path), str(run_dir)]
    collage_args = base + ["collage", str(script_path), str(run_dir)]
    if segment_ids:
        imagegen_args.append(segment_ids)
        videogen_args.append(segment_ids)
        assets_args.append(segment_ids)
        collage_args.append(segment_ids)
    imagegen = ("imagegen", "Generating storyboard images", imagegen_args)
    videogen = ("videogen", "Animating storyboard beats", videogen_args)
    align = ("align", "Aligning narration words", base + ["align", str(script_path), str(run_dir)])
    sfx = ("sfx", "Mixing sound effects", base + ["sfx", str(script_path), str(run_dir)])
    assets = ("assets", "Generating collage assets", assets_args)
    collage = ("collage", "Rendering collage scenes", collage_args)
    manifest = ("manifest", "Building final manifest", base + ["manifest", str(script_path), str(run_dir)])
    composite = ("composite", "Compositing final video", base + ["composite", str(manifest_path), str(final_path)])
    qa = ("qa", "Running QA", base + ["qa", str(run_dir)])

    if mode == "full":
        return [
            storyboard,
            synthesize,
            update_storyboard,
            align,
            sfx,
            imagegen,
            assets,
            videogen,
            collage,
            manifest,
            composite,
            qa,
        ]
    if mode == "videos":
        return [update_storyboard, videogen, collage, manifest, composite, qa]
    if mode == "clips":
        return [update_storyboard, videogen]
    raise ValueError(f"unsupported production mode: {mode}")


def _run_production(
    run_id: str,
    run_dir: Path,
    job_ref: dict[str, ProductionJob],
    mode: str,
    force_video: bool,
    segment_ids: str,
    speed: float | None,
    preset: dict[str, Any] | None,
) -> None:
    steps = _pipeline_steps(run_dir, mode, segment_ids)
    status = _initial_status(run_id, len(steps), mode, force_video, segment_ids)
    _write_status(run_dir, status)

    # Video provider / LTX settings come from PipelineConfig defaults (env or
    # .env overrides included) — do not re-pin them here, or the two defaults
    # can silently drift apart. A resolved preset's env overrides those
    # defaults; explicit per-call force/speed still win over the preset.
    env = os.environ.copy()
    if preset is not None:
        env.update(presets.preset_env(preset))
    if force_video:
        env["PTV_VIDEO_FORCE"] = "true"
    if speed is not None:
        env["PTV_VIDEO_SPEED"] = str(speed)

    try:
        job = job_ref["job"]
        stopped = False
        for index, (step, label, args) in enumerate(steps, start=1):
            if job.stop_event.is_set():
                # Covers the gap between steps, when job.process is None and
                # _run_command has no process to kill.
                stopped = True
                break
            _run_command(
                run_dir=run_dir,
                job=job,
                status=status,
                step=step,
                step_label=label,
                progress=round(((index - 1) / len(steps)) * 100),
                total_steps=len(steps),
                args=args,
                env=env,
            )

        if stopped:
            status.update(
                {
                    "status": "stopped",
                    "step_label": "Stopped",
                    "error": None,
                    "finished_at": _now(),
                }
            )
            _append_log(status, "Stopped by user request.")
            _write_status(run_dir, status)
        else:
            final_path = run_dir / "final.mp4"
            done_label = "Clips ready" if mode == "clips" else "Done"
            status.update(
                {
                    "status": "done",
                    "step": "done",
                    "step_label": done_label,
                    "progress": 100,
                    "finished_at": _now(),
                    "error": None,
                    "final_video_url": f"/media/{run_id}/final.mp4" if final_path.exists() else None,
                }
            )
            _write_status(run_dir, status)
    except ProductionStopped:
        status.update(
            {
                "status": "stopped",
                "step_label": "Stopped",
                "error": None,
                "finished_at": _now(),
            }
        )
        _append_log(status, "Stopped by user request.")
        _write_status(run_dir, status)
    except Exception as exc:  # noqa: BLE001
        status.update(
            {
                "status": "failed",
                "step_label": "Failed",
                "error": str(exc),
                "finished_at": _now(),
            }
        )
        _append_log(status, f"ERROR: {exc}")
        _write_status(run_dir, status)
    finally:
        _ACTIVE.pop(run_id, None)


def _resolve_production_preset(run_dir: Path, preset_id: str) -> dict[str, Any] | None:
    """Resolve the preset to use for this production call.

    A truthy preset_id is looked up and snapshotted to disk so a later resume
    (mode="videos"/"clips", or a subsequent call with no preset_id) keeps
    using the same preset. Otherwise, reuse a snapshot from a prior call if
    one exists; if neither is present, return None (today's behavior — no
    preset env at all).
    """
    snapshot_path = run_dir / PRESET_SNAPSHOT_FILE
    if preset_id:
        preset = presets.get_preset(preset_id)
        if preset is None:
            raise ValueError(f"unknown preset: {preset_id!r}")
        snapshot_path.write_text(json.dumps(preset, indent=2), encoding="utf-8")
        return preset
    if snapshot_path.exists():
        try:
            return json.loads(snapshot_path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def start_run_production(
    run_id: str,
    mode: str = "full",
    force_video: bool = False,
    segment_ids: str = "",
    speed: float | None = None,
    preset_id: str = "",
) -> dict[str, Any]:
    """Start or return the existing production job for a run."""
    run_dir = _safe_run_dir(run_id)
    if not run_dir.is_dir() or not (run_dir / "script.json").exists():
        raise FileNotFoundError(run_id)

    mode = mode.strip().lower() or "full"
    segment_ids = ",".join(item.strip() for item in segment_ids.split(",") if item.strip())
    # Validate before creating a thread/status file.
    steps = _pipeline_steps(run_dir, mode, segment_ids)

    # Must run before preset resolution: an already-running job's produce-again
    # call is a documented no-op, so it must not snapshot a new preset to disk
    # (which the running job's env never picked up) or raise on an unknown
    # preset_id it will never use.
    active = _ACTIVE.get(run_id)
    if active and active.thread.is_alive():
        return get_run_production_status(run_id)

    preset = _resolve_production_preset(run_dir, preset_id)

    job_ref: dict[str, ProductionJob] = {}
    thread = threading.Thread(
        target=_run_production,
        args=(run_id, run_dir, job_ref, mode, force_video, segment_ids, speed, preset),
        daemon=True,
        name=f"run-producer-{run_id}-{mode}",
    )
    job = ProductionJob(
        run_id=run_id,
        thread=thread,
        mode=mode,
        force_video=force_video,
        segment_ids=segment_ids,
    )
    job_ref["job"] = job
    _ACTIVE[run_id] = job
    _write_status(run_dir, _initial_status(run_id, len(steps), mode, force_video, segment_ids))
    thread.start()
    return get_run_production_status(run_id)


def stop_run_production(run_id: str) -> dict[str, Any]:
    """Stop an active production job for a run.

    Idempotent: calling this when nothing is running (already stopped/done/
    never started) is a no-op that just returns the current status, not an
    error — a stop button double-click or a retry must never raise.
    """
    run_dir = _safe_run_dir(run_id)
    if not run_dir.is_dir() or not (run_dir / "script.json").exists():
        raise FileNotFoundError(run_id)

    job = _ACTIVE.get(run_id)
    if job is None or not job.thread.is_alive():
        return get_run_production_status(run_id)

    job.stop_event.set()
    proc = job.process
    if proc is not None:
        _kill_process_group(proc)
    job.thread.join(timeout=15)
    return get_run_production_status(run_id)
