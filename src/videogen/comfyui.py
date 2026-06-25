"""ComfyUI client for true image-to-video (LTX) — the high-quality video engine.

Optional: only used when config.video_provider == "comfyui" and a ComfyUI server
is reachable. Falls back to Ken Burns when unavailable so a run never breaks.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from rich.console import Console

from .kenburns import kenburns_clip, _ffprobe_duration

console = Console()


class ComfyUIClient:
    """Minimal HTTP client for a local ComfyUI server."""

    def __init__(self, base_url: str = "http://127.0.0.1:8188"):
        self.base_url = base_url.rstrip("/")

    def is_available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/system_stats", timeout=3) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def submit_workflow(self, workflow: dict, client_id: str = "video-studio") -> str:
        data = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/prompt", data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["prompt_id"]

    def poll_until_done(self, prompt_id: str, timeout: int = 900) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{self.base_url}/history/{prompt_id}", timeout=10) as r:
                    history = json.loads(r.read())
                if prompt_id in history:
                    return history[prompt_id]
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(2)
        raise TimeoutError(f"ComfyUI prompt {prompt_id} did not finish within {timeout}s")

    def fetch_output(self, filename: str, subfolder: str, type_: str, dest: Path) -> Path:
        params = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": type_})
        with urllib.request.urlopen(f"{self.base_url}/view?{params}", timeout=120) as r:
            dest.write_bytes(r.read())
        return dest


def _build_ltx_workflow(image_path: Path, frames: int, w: int, h: int, model: str) -> dict:
    """Build an LTX image-to-video workflow graph.

    NOTE: ComfyUI workflow node IDs/class_types depend on the installed custom
    nodes and model files. This is a template to be adapted to the user's
    ComfyUI install before the comfyui provider is used in production.
    """
    raise NotImplementedError(
        "LTX workflow graph must be finalized against the local ComfyUI install "
        "(node class_types + model filenames). Use the kenburns provider until then."
    )


def image_to_video(
    image_path: Path,
    output_path: Path,
    duration_seconds: float,
    resolution: tuple[int, int] = (1920, 1080),
    fps: int = 30,
    model: str = "ltx",
    base_url: str = "http://127.0.0.1:8188",
    fallback_to_kenburns: bool = True,
    direction: str = "in",
    timeout: int = 900,
) -> dict:
    """Generate an image-to-video clip via ComfyUI/LTX.

    Falls back to Ken Burns if the server is unavailable, the workflow is not yet
    finalized, or generation fails (when fallback_to_kenburns is True).
    """
    image_path = Path(image_path)
    output_path = Path(output_path)
    client = ComfyUIClient(base_url)

    def _fallback(reason: str) -> dict:
        if not fallback_to_kenburns:
            return {"success": False, "video_path": str(output_path), "duration": None,
                    "error_message": reason}
        console.print(f"[yellow]ComfyUI unavailable ({reason}); using Ken Burns fallback[/yellow]")
        return kenburns_clip(image_path, output_path, duration_seconds,
                             resolution=resolution, fps=fps, direction=direction)

    if not client.is_available():
        return _fallback("server not reachable")

    try:
        frames = max(round(duration_seconds * fps), 1)
        workflow = _build_ltx_workflow(image_path, frames, resolution[0], resolution[1], model)
        prompt_id = client.submit_workflow(workflow)
        result = client.poll_until_done(prompt_id, timeout=timeout)
        # Locate the produced video in the history outputs and download it.
        for node_out in result.get("outputs", {}).values():
            for key in ("gifs", "videos", "images"):
                for item in node_out.get(key, []):
                    client.fetch_output(item["filename"], item.get("subfolder", ""),
                                        item.get("type", "output"), output_path)
                    return {"success": True, "video_path": str(output_path),
                            "duration": _ffprobe_duration(output_path), "error_message": None}
        return _fallback("no video in ComfyUI output")
    except NotImplementedError as e:
        return _fallback(str(e))
    except Exception as e:  # noqa: BLE001 - any failure should degrade gracefully
        return _fallback(f"{type(e).__name__}: {e}")
