"""Resumable production jobs for Studio flows."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.studio.runs import _runs_root

STATUS_FILE = ".production_status.json"
MAX_LOG_LINE_CHARS = 2000

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STATUS_LOCK = threading.Lock()
_ACTIVE: dict[str, "ProductionJob"] = {}


@dataclass
class ProductionJob:
    run_id: str
    thread: threading.Thread
    mode: str = "full"
    force_video: bool = False
    segment_ids: str = ""
    process: subprocess.Popen[str] | None = None


def _now() -> float:
    return time.time()


def _safe_run_dir(run_id: str) -> Path:
    if "/" in run_id or "\\" in run_id or run_id in {"", ".", ".."}:
        raise ValueError("invalid run id")

    root = _runs_root().resolve()
    run_dir = (root / run_id).resolve()
    if run_dir != root and root not in run_dir.parents:
        raise ValueError("invalid run id")
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
        # On Windows os.replace raises PermissionError while a concurrent
        # reader (the status GET endpoint) briefly holds the destination open.
        # Retry with a short backoff instead of killing the producer thread.
        for attempt in range(20):
            try:
                tmp.replace(_status_path(run_dir))
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
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
    )
    job.process = proc

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            _append_log(status, line)
            _write_status(run_dir, status)

        return_code = proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise
    finally:
        job.process = None

    if return_code != 0:
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
) -> None:
    steps = _pipeline_steps(run_dir, mode, segment_ids)
    status = _initial_status(run_id, len(steps), mode, force_video, segment_ids)

    try:
        _write_status(run_dir, status)

        env = os.environ.copy()
        env.setdefault("PTV_VIDEO_PROVIDER", "ltx")
        env.setdefault("PTV_LTX_PREFER_EXTEND", "false")
        if force_video:
            env["PTV_VIDEO_FORCE"] = "true"
        if speed is not None:
            env["PTV_VIDEO_SPEED"] = str(speed)

        job = job_ref["job"]
        for index, (step, label, args) in enumerate(steps, start=1):
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


def start_run_production(
    run_id: str,
    mode: str = "full",
    force_video: bool = False,
    segment_ids: str = "",
    speed: float | None = None,
) -> dict[str, Any]:
    """Start or return the existing production job for a run."""
    run_dir = _safe_run_dir(run_id)
    if not run_dir.is_dir() or not (run_dir / "script.json").exists():
        raise FileNotFoundError(run_id)

    mode = mode.strip().lower() or "full"
    segment_ids = ",".join(item.strip() for item in segment_ids.split(",") if item.strip())
    # Validate before creating a thread/status file.
    steps = _pipeline_steps(run_dir, mode, segment_ids)

    active = _ACTIVE.get(run_id)
    if active and active.thread.is_alive():
        return get_run_production_status(run_id)

    job_ref: dict[str, ProductionJob] = {}
    thread = threading.Thread(
        target=_run_production,
        args=(run_id, run_dir, job_ref, mode, force_video, segment_ids, speed),
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
