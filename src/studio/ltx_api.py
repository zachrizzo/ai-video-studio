"""Python client for the LTX Video cloud API.

Provides methods for each endpoint:
  - text_to_video
  - image_to_video
  - audio_to_video
  - retake
  - extend
  - video_to_video_hdr

Each method sends a POST request and saves the returned media to output_path.
Returns dict with {success, output_path, error}.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.ltx.video"
TIMEOUT = 300.0


class LTXClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
        }

    def _post(self, endpoint: str, payload: dict, output_path: Path) -> dict:
        """POST JSON to an endpoint, save the response body as a file."""
        url = f"{BASE_URL}{endpoint}"
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.post(url, json=payload, headers=self._headers())
                if resp.status_code != 200:
                    body = resp.text[:500]
                    return {
                        "success": False,
                        "output_path": str(output_path),
                        "error": f"HTTP {resp.status_code}: {body}",
                    }
                content_type = resp.headers.get("content-type", "")
                if "application/json" in content_type:
                    data = resp.json()
                    if data.get("error"):
                        return {
                            "success": False,
                            "output_path": str(output_path),
                            "error": data["error"],
                        }
                output_path.write_bytes(resp.content)
                return {
                    "success": True,
                    "output_path": str(output_path),
                    "error": None,
                }
        except httpx.TimeoutException:
            return {
                "success": False,
                "output_path": str(output_path),
                "error": "Request timed out",
            }
        except Exception as e:
            return {
                "success": False,
                "output_path": str(output_path),
                "error": f"{type(e).__name__}: {e}",
            }

    def text_to_video(
        self,
        output_path: Path,
        prompt: str,
        model: str = "ltx-2-3-pro",
        duration: int | None = None,
        resolution: str | None = None,
        fps: int = 25,
        camera_motion: str | None = None,
        generate_audio: bool = True,
    ) -> dict:
        payload: dict = {"prompt": prompt, "model": model, "fps": fps, "generate_audio": generate_audio}
        if duration is not None:
            payload["duration"] = duration
        if resolution is not None:
            payload["resolution"] = resolution
        if camera_motion is not None:
            payload["camera_motion"] = camera_motion
        return self._post("/v1/text-to-video", payload, output_path)

    def image_to_video(
        self,
        output_path: Path,
        image_uri: str,
        prompt: str | None = None,
        model: str = "ltx-2-3-pro",
        duration: int | None = None,
        resolution: str | None = None,
        fps: int = 25,
        camera_motion: str | None = None,
        generate_audio: bool = True,
        first_frame: bool | None = None,
        last_frame: bool | None = None,
    ) -> dict:
        payload: dict = {"image_uri": image_uri, "model": model, "fps": fps, "generate_audio": generate_audio}
        if prompt is not None:
            payload["prompt"] = prompt
        if duration is not None:
            payload["duration"] = duration
        if resolution is not None:
            payload["resolution"] = resolution
        if camera_motion is not None:
            payload["camera_motion"] = camera_motion
        if first_frame is not None:
            payload["first_frame"] = first_frame
        if last_frame is not None:
            payload["last_frame"] = last_frame
        return self._post("/v1/image-to-video", payload, output_path)

    def audio_to_video(
        self,
        output_path: Path,
        audio_uri: str,
        image_uri: str | None = None,
        prompt: str | None = None,
        model: str = "ltx-2-3-pro",
        resolution: str | None = None,
        guidance_scale: float | None = None,
    ) -> dict:
        payload: dict = {"audio_uri": audio_uri, "model": model}
        if image_uri is not None:
            payload["image_uri"] = image_uri
        if prompt is not None:
            payload["prompt"] = prompt
        if resolution is not None:
            payload["resolution"] = resolution
        if guidance_scale is not None:
            payload["guidance_scale"] = guidance_scale
        return self._post("/v1/audio-to-video", payload, output_path)

    def retake(
        self,
        output_path: Path,
        video_uri: str,
        start_time: float,
        duration: float,
        prompt: str,
        model: str = "ltx-2-3-pro",
        resolution: str | None = None,
        mode: str | None = None,
    ) -> dict:
        payload: dict = {
            "video_uri": video_uri,
            "start_time": start_time,
            "duration": duration,
            "prompt": prompt,
            "model": model,
        }
        if resolution is not None:
            payload["resolution"] = resolution
        if mode is not None:
            payload["mode"] = mode
        return self._post("/v1/retake", payload, output_path)

    def extend(
        self,
        output_path: Path,
        video_uri: str,
        prompt: str,
        model: str = "ltx-2-3-pro",
        mode: str = "from_end",
        duration: float | None = None,
        context: float | None = None,
    ) -> dict:
        payload: dict = {
            "video_uri": video_uri,
            "prompt": prompt,
            "model": model,
            "mode": mode,
        }
        if duration is not None:
            payload["duration"] = duration
        if context is not None:
            payload["context"] = context
        return self._post("/v1/extend", payload, output_path)

    def video_to_video_hdr(
        self,
        output_path: Path,
        video_uri: str,
    ) -> dict:
        payload: dict = {"video_uri": video_uri}
        return self._post("/v1/video-to-video-hdr", payload, output_path)
