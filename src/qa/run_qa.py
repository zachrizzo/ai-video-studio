"""Automated release QA for generated video runs.

The checks here deliberately focus on objective failures and high-risk patterns.
They do not replace human review, but they prevent the pipeline from treating
missing, garbled, inaudible, or obviously risky artifacts as production-ready.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.analysis.script_writer import load_script
from src.config import PipelineConfig
from src.visuals.beats import beat_clip_path, beat_image_path, segment_visual_beats


RISKY_MOTION_KEYWORDS = {
    "bird",
    "birds",
    "cape",
    "cloak",
    "fabric",
    "flag",
    "flags",
    "banner",
    "banners",
    "horse",
    "horses",
    "crowd",
    "crowds",
    "hands",
    "fingers",
    "flame",
    "smoke",
}

TEXT_RISK_KEYWORDS = {
    "text",
    "letters",
    "writing",
    "written",
    "sign",
    "signs",
    "label",
    "labels",
    "banner",
    "banners",
    "map",
}


@dataclass
class Check:
    id: str
    severity: str
    message: str
    segment_id: str | None = None
    path: str | None = None
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "severity": self.severity,
            "message": self.message,
        }
        if self.segment_id:
            data["segment_id"] = self.segment_id
        if self.path:
            data["path"] = self.path
        if self.details:
            data["details"] = self.details
        return data


def _run(
    cmd: list[str],
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _word_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


def _normalize_text(text: str) -> str:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return " ".join(words)


def _text_similarity(expected: str, actual: str) -> float:
    expected_norm = _normalize_text(expected)
    actual_norm = _normalize_text(actual)
    if not expected_norm or not actual_norm:
        return 0.0
    # autojunk MUST stay off: its "popular character" heuristic treats any
    # character occurring >1% of the time in a 200+ char string as junk and
    # ignores it, which on long character-level transcripts collapses the ratio
    # for near-identical text (e.g. 0.955 -> 0.015). That produced false
    # transcript_mismatch failures on perfectly good, faithful voiceover.
    return difflib.SequenceMatcher(None, expected_norm, actual_norm, autojunk=False).ratio()


def _ffprobe_duration(path: Path) -> float | None:
    if not path.exists():
        return None
    proc = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=20,
    )
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def _probe_loudness(path: Path) -> dict[str, float] | None:
    if not path.exists():
        return None
    proc = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-16:LRA=11:TP=-1.5:print_format=json",
            "-f",
            "null",
            "-",
        ],
        timeout=180,
    )
    text = proc.stderr or proc.stdout
    match = re.search(r"\{\s*\"input_i\".*?\}", text, flags=re.S)
    if proc.returncode != 0 or not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    out: dict[str, float] = {}
    for key in ("input_i", "input_tp", "input_lra", "input_thresh"):
        try:
            out[key] = float(raw[key])
        except (KeyError, TypeError, ValueError):
            pass
    return out


def _detect_video_events(path: Path, vf: str, pattern: str) -> list[dict[str, float]]:
    if not path.exists():
        return []
    proc = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            vf,
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=240,
    )
    events: list[dict[str, float]] = []
    for match in re.finditer(pattern, proc.stderr or proc.stdout):
        event: dict[str, float] = {}
        for key, value in match.groupdict().items():
            if value is not None:
                event[key] = float(value)
        events.append(event)
    return events


def _asr_available(command: str) -> bool:
    parts = shlex.split(command)
    while parts and "=" in parts[0] and not parts[0].startswith("-"):
        parts.pop(0)
    if not parts:
        return False
    exe = parts[0]
    return shutil.which(exe) is not None


def _transcribe_voicebox(audio_path: Path, config: PipelineConfig) -> tuple[str | None, str | None]:
    """Transcribe via the Voicebox app's POST /transcribe endpoint.

    Builds a multipart/form-data body by hand (stdlib urllib only, no new deps):
    a ``file`` part (audio/wav) plus a ``language`` field. Returns (text, None)
    on success. An unreachable server or a non-200 response returns the same
    (None, message) "unavailable" shape the CLI branch uses for a missing
    whisper binary, but with an actionable message.
    """
    url = config.voicebox_url.rstrip("/") + "/transcribe"
    unreachable = (
        f"Voicebox /transcribe unreachable at {config.voicebox_url} — launch the "
        f"Voicebox app (voicebox.sh) or set PTV_QA_ASR_PROVIDER=cli"
    )

    try:
        audio_bytes = audio_path.read_bytes()
    except OSError as exc:
        return None, f"could not read audio for Voicebox transcription: {exc}"

    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="language"\r\n\r\n',
            f"{config.voicebox_language}\r\n".encode(),
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; '
                f'filename="{audio_path.name}"\r\n'
            ).encode(),
            b"Content-Type: audio/wav\r\n\r\n",
            audio_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )

    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            if response.status != 200:
                return None, unreachable
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, unreachable

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        return None, f"Voicebox /transcribe returned invalid JSON: {exc}"
    text = data.get("text")
    if text is None:
        return None, "Voicebox /transcribe response had no 'text' field"
    return str(text).strip(), None


def _transcribe(audio_path: Path, config: PipelineConfig) -> tuple[str | None, str | None]:
    if config.qa_asr_provider == "voicebox":
        return _transcribe_voicebox(audio_path, config)

    command = os.environ.get("PTV_QA_ASR_COMMAND") or config.qa_asr_command
    if not _asr_available(command):
        return None, f"ASR command not found: {command}"

    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td)
        parts = shlex.split(command)
        env = os.environ.copy()
        while parts and "=" in parts[0] and not parts[0].startswith("-"):
            key, value = parts.pop(0).split("=", 1)
            env[key] = value
        args = parts + [
            str(audio_path),
            "--model",
            config.qa_asr_model,
            "--output_dir",
            str(out_dir),
            "--output_format",
            "txt",
            "--fp16",
            "False",
        ]
        try:
            proc = _run(args, timeout=900, env=env)
        except Exception as exc:  # noqa: BLE001
            return None, f"ASR failed: {exc}"
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-500:]
            return None, f"ASR exited {proc.returncode}: {tail}"
        txt_files = sorted(out_dir.glob("*.txt"))
        if not txt_files:
            return None, "ASR produced no transcript"
        return txt_files[0].read_text(errors="ignore").strip(), None


def _looks_like_gibberish_token(token: str) -> bool:
    if len(token) < 6 or not token.isalpha():
        return False
    vowels = sum(1 for ch in token.lower() if ch in "aeiou")
    vowel_ratio = vowels / len(token)
    return vowel_ratio < 0.18 or re.search(r"([bcdfghjklmnpqrstvwxyz])\1{2,}", token.lower()) is not None


def _run_ocr(image_path: Path) -> tuple[str | None, str | None]:
    if shutil.which("tesseract") is None:
        return None, "tesseract not installed"
    proc = _run(["tesseract", str(image_path), "stdout", "--psm", "6"], timeout=60)
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "")[-300:]
    return proc.stdout.strip(), None


def _load_audio_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "audio" / "audio_manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _segment_video_paths(run_dir: Path, segment: Any) -> list[Path]:
    if getattr(segment, "visual_type", None) == "scene":
        beats = segment_visual_beats(segment)
        if beats:
            return [beat_clip_path(run_dir, beat) for beat in beats]

    segment_id = getattr(segment, "segment_id", "")
    render_dir = run_dir / "scenes" / f"{segment_id}_render"
    candidates = [
        run_dir / "clips" / f"{segment_id}.mp4",
        # collage first: the deterministic frame-rendered artifact.
        render_dir / f"{segment_id}_collage.mp4",
        render_dir / f"{segment_id}_html.mp4",
        render_dir / f"{segment_id}_manim.mp4",
        render_dir / f"{segment_id}_fallback.mp4",
    ]
    path = next((path for path in candidates if path.exists()), None)
    return [path] if path else []


def _scene_render_video(run_dir: Path, segment_id: str) -> tuple[Path, str] | None:
    """Return (path, engine) for a segment's frame-rendered scene, if present.

    Priority mirrors the compositor: collage (the deterministic frame artifact)
    first, then legacy html/manim renders.
    """
    render_dir = run_dir / "scenes" / f"{segment_id}_render"
    for engine in ("collage", "html", "manim"):
        candidate = render_dir / f"{segment_id}_{engine}.mp4"
        if candidate.exists():
            return candidate, engine
    return None


def _luma_stddev_series(path: Path) -> list[tuple[float, float]]:
    """Per-frame (pts_time, luma spread) via ffmpeg signalstats.

    ffmpeg's ``signalstats`` filter (6.1) does not expose a per-frame luma
    standard deviation (``YSTD``); we use the equivalent peak-to-peak luma
    spread ``YMAX - YMIN`` as the flatness metric. A strictly-uniform frame
    yields 0; ANY content or grain yields a positive spread. Deliberately near-
    uniform paper backgrounds (e.g. #F0EEE6 with grain) carry a small amount of
    luma variance, so they land ABOVE the tiny threshold and are NOT treated as
    blank.
    """
    proc = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-vf",
            "signalstats,metadata=print",
            "-an",
            "-f",
            "null",
            "-",
        ],
        timeout=240,
    )
    text = (proc.stderr or "") + (proc.stdout or "")
    times = [float(m) for m in re.findall(r"pts_time:(\d+(?:\.\d+)?)", text)]
    ymins = [float(m) for m in re.findall(r"lavfi\.signalstats\.YMIN=(\d+(?:\.\d+)?)", text)]
    ymaxs = [float(m) for m in re.findall(r"lavfi\.signalstats\.YMAX=(\d+(?:\.\d+)?)", text)]
    n = min(len(times), len(ymins), len(ymaxs))
    return [(times[i], ymaxs[i] - ymins[i]) for i in range(n)]


def _max_blank_run_seconds(series: list[tuple[float, float]], threshold: float) -> float:
    """Longest run of CONSECUTIVE strictly-uniform (blank) frames, in seconds."""
    max_run = 0.0
    run_start: float | None = None
    for pts_time, spread in series:
        # Blank ONLY when strictly uniform (below the tiny threshold). Near-
        # uniform paper with grain has spread >= threshold and never counts.
        if spread < threshold:
            if run_start is None:
                run_start = pts_time
            max_run = max(max_run, pts_time - run_start)
        else:
            run_start = None
    return max_run


def _scene_render_checks(
    add,
    config: PipelineConfig,
    segment: Any,
    scene_video: Path,
    engine: str,
    audio_manifest: dict[str, Any],
) -> None:
    """Duration-drift + blank-frame checks for a frame-rendered scene."""
    seg_id = getattr(segment, "segment_id", "")

    # --- Scene duration drift (CONTRACTS §7) ---
    video_duration = _ffprobe_duration(scene_video)
    audio_entry = audio_manifest.get(seg_id) or {}
    target = float(audio_entry.get("duration_seconds") or 0) or float(
        getattr(segment, "estimated_duration_seconds", 0) or 0
    )
    if video_duration is not None and target > 0:
        drift = abs(video_duration - target)
        if drift > config.qa_scene_duration_epsilon:
            # Frame-rendered collage scenes are deterministic, so any drift is a
            # real bug (error). Legacy html/manim renders only warn so old runs
            # are not broken.
            severity = "error" if engine == "collage" else "warning"
            add(
                Check(
                    "scene.duration_drift",
                    severity,
                    "Frame-rendered scene duration drifts from the segment's audio duration.",
                    seg_id,
                    str(scene_video),
                    {
                        "engine": engine,
                        "video_seconds": video_duration,
                        "target_seconds": target,
                        "drift_seconds": drift,
                        "epsilon": config.qa_scene_duration_epsilon,
                    },
                )
            )

    # --- Blank-frame check (collage + html scene renders only) ---
    if engine in ("collage", "html"):
        series = _luma_stddev_series(scene_video)
        blank_run = _max_blank_run_seconds(series, config.qa_min_luma_stddev)
        if blank_run > config.qa_max_blank_seconds:
            add(
                Check(
                    "scene.blank_frames",
                    "error",
                    "Frame-rendered scene holds strictly-uniform (blank) frames for too long.",
                    seg_id,
                    str(scene_video),
                    {
                        "engine": engine,
                        "blank_seconds": blank_run,
                        "max_blank_seconds": config.qa_max_blank_seconds,
                        "luma_stddev_threshold": config.qa_min_luma_stddev,
                    },
                )
            )


def _status_for(checks: list[Check]) -> str:
    if any(c.severity == "error" for c in checks):
        return "failed"
    if any(c.severity == "warning" for c in checks):
        return "warning"
    return "passed"


def qa_run(run_dir: Path, *, strict: bool = False) -> dict[str, Any]:
    run_dir = Path(run_dir)
    config = PipelineConfig()
    script_path = run_dir / "script.json"
    checks: list[Check] = []
    segment_checks: dict[str, list[Check]] = {}

    def add(check: Check) -> None:
        checks.append(check)
        if check.segment_id:
            segment_checks.setdefault(check.segment_id, []).append(check)

    if not script_path.exists():
        add(Check("script.missing", "error", "Missing script.json", path=str(script_path)))
        return _report(run_dir, checks, segment_checks)

    try:
        script = load_script(script_path)
    except Exception as exc:  # noqa: BLE001
        add(Check("script.invalid", "error", f"script.json did not validate: {exc}", path=str(script_path)))
        return _report(run_dir, checks, segment_checks)

    asr_command = os.environ.get("PTV_QA_ASR_COMMAND") or config.qa_asr_command
    all_text = " ".join([script.title] + [s.narration_text for s in script.segments])
    if script.canonical_name and script.canonical_name.lower() not in all_text.lower():
        add(
            Check(
                "script.canonical_name_missing",
                "error",
                f"Canonical name '{script.canonical_name}' is not used in the title or narration.",
            )
        )
    if not script.style_bible:
        add(Check("script.style_bible_missing", "warning", "No style_bible set; continuity QA is weaker."))
    if not script.release_acceptance_criteria:
        add(
            Check(
                "script.acceptance_criteria_missing",
                "warning",
                "No release_acceptance_criteria set; agent has no explicit finish line.",
            )
        )
    if not script.storyboard_summary:
        add(
            Check(
                "storyboard.summary_missing",
                "warning",
                "No storyboard_summary set; visual pacing and continuity review is weaker.",
            )
        )

    audio_manifest = _load_audio_manifest(run_dir)
    if not audio_manifest:
        add(Check("audio.manifest_missing", "error", "Missing audio/audio_manifest.json"))

    for seg in script.segments:
        seg_id = seg.segment_id
        segment_checks.setdefault(seg_id, [])

        if seg.visual_type == "scene":
            beats = segment_visual_beats(seg)
            if not beats:
                add(
                    Check(
                        "segment.image_prompt_missing",
                        "error",
                        "Scene segment has no image_prompt or visual_beats prompt.",
                        seg_id,
                    )
                )
            if seg.estimated_duration_seconds > 6 and len(beats) < 2:
                add(
                    Check(
                        "storyboard.too_few_beats",
                        "warning",
                        "Scene segment is longer than 6 seconds but has fewer than 2 storyboard beats.",
                        seg_id,
                    )
                )
            for beat in beats:
                if not any([beat.description, beat.shot_type, beat.composition, beat.action]):
                    add(
                        Check(
                            "storyboard.frame_underdescribed",
                            "warning",
                            "Storyboard beat lacks description, shot type, composition, or action.",
                            seg_id,
                            details={"beat_id": beat.beat_id, "beat_index": beat.index},
                        )
                    )
                image_path = beat_image_path(run_dir, beat)
                if not image_path.exists():
                    add(
                        Check(
                            "image.missing",
                            "error",
                            "Missing generated still image.",
                            seg_id,
                            str(image_path),
                            {"beat_id": beat.beat_id, "beat_index": beat.index},
                        )
                    )
                    continue

                prompt_words = _word_set(beat.prompt)
                risky_motion = sorted(prompt_words & RISKY_MOTION_KEYWORDS)
                if risky_motion:
                    add(
                        Check(
                            "prompt.motion_risk",
                            "warning",
                            "Prompt contains high-risk generated motion elements; review or use safer layered/Ken Burns motion.",
                            seg_id,
                            str(image_path),
                            {
                                "beat_id": beat.beat_id,
                                "beat_index": beat.index,
                                "keywords": risky_motion,
                            },
                        )
                    )

                text_risk = sorted(prompt_words & TEXT_RISK_KEYWORDS)
                if text_risk:
                    ocr_text, ocr_error = _run_ocr(image_path)
                    if ocr_text:
                        bad_tokens = [
                            token
                            for token in re.findall(r"[A-Za-z]{4,}", ocr_text)
                            if _looks_like_gibberish_token(token)
                        ]
                        severity = "error" if bad_tokens else "warning"
                        add(
                            Check(
                                "image.ocr_text_detected",
                                severity,
                                "OCR detected text in an image from a text-risk prompt.",
                                seg_id,
                                str(image_path),
                                {
                                    "beat_id": beat.beat_id,
                                    "beat_index": beat.index,
                                    "text": ocr_text[:300],
                                    "gibberish_tokens": bad_tokens[:10],
                                    "keywords": text_risk,
                                },
                            )
                        )
                    elif ocr_error:
                        add(
                            Check(
                                "image.ocr_unavailable",
                                "warning",
                                "Image prompt asks for text-like elements, but OCR is unavailable for automated review.",
                                seg_id,
                                str(image_path),
                                {
                                    "beat_id": beat.beat_id,
                                    "beat_index": beat.index,
                                    "ocr_error": ocr_error[:200],
                                    "keywords": text_risk,
                                },
                            )
                        )

        video_paths = _segment_video_paths(run_dir, seg)
        missing_videos = [path for path in video_paths if not path.exists()]
        if not video_paths or missing_videos:
            for path in missing_videos or [None]:
                add(
                    Check(
                        "video.segment_missing",
                        "error",
                        "Missing segment video/clip.",
                        seg_id,
                        str(path) if path else None,
                    )
                )

        # Frame-rendered scene checks: only for diagram-type segments whose
        # resolved video is a scenes/{id}_render/{id}_(collage|html|manim).mp4.
        if seg.visual_type == "diagram":
            scene_render = _scene_render_video(run_dir, seg_id)
            if scene_render is not None:
                scene_video, engine = scene_render
                _scene_render_checks(add, config, seg, scene_video, engine, audio_manifest)

        audio_entry = audio_manifest.get(seg_id)
        if not audio_entry:
            add(Check("audio.segment_missing", "error", "Missing audio manifest entry.", seg_id))
            continue
        if audio_entry.get("failed"):
            add(
                Check(
                    "audio.synthesis_failed",
                    "error",
                    "Narration synthesis failed for this segment — re-run synthesize before compositing.",
                    seg_id,
                    details={"error": str(audio_entry.get("error") or "TTS failed")},
                )
            )
            continue

        audio_path = Path(audio_entry.get("audio_path", ""))
        if not audio_path.exists():
            add(Check("audio.file_missing", "error", "Missing audio file.", seg_id, str(audio_path)))
            continue

        actual_duration = _ffprobe_duration(audio_path) or float(audio_entry.get("duration_seconds", 0) or 0)
        estimated = float(seg.estimated_duration_seconds or 0)
        if estimated > 0:
            ratio = actual_duration / estimated if estimated else 1.0
            overage = actual_duration - estimated
            if (
                ratio > config.qa_max_audio_duration_ratio
                and overage > config.qa_max_audio_duration_overage_seconds
            ):
                add(
                    Check(
                        "audio.duration_drift",
                        "error",
                        "Audio duration is far longer than the script estimate; this often indicates TTS hallucination or repeated gibberish.",
                        seg_id,
                        str(audio_path),
                        {
                            "estimated_seconds": estimated,
                            "actual_seconds": actual_duration,
                            "ratio": ratio,
                        },
                    )
                )

        loudness = _probe_loudness(audio_path)
        if loudness and "input_i" in loudness:
            integrated = loudness["input_i"]
            if integrated < config.qa_min_lufs or integrated > config.qa_max_lufs:
                add(
                    Check(
                        "audio.segment_loudness",
                        "warning",
                        "Segment loudness is outside the target web playback range.",
                        seg_id,
                        str(audio_path),
                        {"input_i_lufs": integrated, "target_lufs": config.qa_target_lufs},
                    )
                )

        if config.qa_require_asr or strict or _asr_available(asr_command):
            transcript, error = _transcribe(audio_path, config)
            if transcript:
                similarity = _text_similarity(seg.narration_text, transcript)
                if similarity < config.qa_min_transcript_similarity:
                    add(
                        Check(
                            "audio.transcript_mismatch",
                            "error",
                            "ASR transcript does not match narration; voiceover may be garbled or missing text.",
                            seg_id,
                            str(audio_path),
                            {
                                "similarity": similarity,
                                "expected": seg.narration_text[:500],
                                "actual": transcript[:500],
                            },
                        )
                    )
            elif config.qa_require_asr or strict:
                add(Check("audio.asr_failed", "error", error or "ASR failed.", seg_id, str(audio_path)))
            elif error:
                add(Check("audio.asr_unavailable", "warning", error, seg_id, str(audio_path)))

    final_video = run_dir / "final.mp4"
    if not final_video.exists():
        add(Check("final.missing", "warning", "No final.mp4 found in run directory.", path=str(final_video)))
    else:
        # --- A/V sync: final duration must cover the full narration ---
        # The compositor's AV merge uses ffmpeg -shortest, which silently
        # truncates when the concatenated clips are shorter than the narration
        # (e.g. audio was re-rolled but existing clips were skipped). Compare
        # the composited duration against the audio manifest total, adjusted
        # for the playback speed cmd_composite applies (config.video_speed).
        narration_total = sum(
            float(entry.get("duration_seconds") or 0)
            for key, entry in audio_manifest.items()
            if not key.startswith("_") and isinstance(entry, dict) and not entry.get("failed")
        )
        final_duration = _ffprobe_duration(final_video)
        # Prefer the speed cmd_composite actually applied (persisted to
        # composite_meta.json). The --speed flag overrides config.video_speed,
        # so trusting the config default alone falsely flags a speed-adjusted
        # final as A/V-drifted.
        speed = float(config.video_speed or 1.0)
        try:
            meta = json.loads((run_dir / "composite_meta.json").read_text(encoding="utf-8"))
            meta_speed = float(meta.get("speed"))
            if meta_speed > 0:
                speed = meta_speed
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
        if narration_total > 0 and final_duration is not None and speed > 0:
            expected = narration_total / speed
            tolerance = max(1.0, expected * 0.015)
            drift = final_duration - expected
            if abs(drift) > tolerance:
                direction = "shorter" if drift < 0 else "longer"
                add(
                    Check(
                        "final.av_sync_drift",
                        "error",
                        (
                            f"Final video is {abs(drift):.1f}s {direction} than the narration "
                            f"({final_duration:.1f}s vs {expected:.1f}s expected) — video/audio "
                            "desync; regenerate clips for segments after the drift point "
                            "(videogen with force) and re-composite."
                        ),
                        path=str(final_video),
                        details={
                            "final_seconds": final_duration,
                            "expected_seconds": expected,
                            "narration_seconds": narration_total,
                            "video_speed": speed,
                            "drift_seconds": drift,
                            "tolerance_seconds": tolerance,
                        },
                    )
                )

        final_loudness = _probe_loudness(final_video)
        if final_loudness and "input_i" in final_loudness:
            integrated = final_loudness["input_i"]
            if integrated < config.qa_min_lufs or integrated > config.qa_max_lufs:
                add(
                    Check(
                        "final.loudness",
                        "error",
                        "Final video loudness is outside the target web playback range.",
                        path=str(final_video),
                        details={"input_i_lufs": integrated, "target_lufs": config.qa_target_lufs},
                    )
                )

        black_events = _detect_video_events(
            final_video,
            "blackdetect=d=0.5:pix_th=0.10",
            r"black_start:(?P<start>\d+(?:\.\d+)?) black_end:(?P<end>\d+(?:\.\d+)?) black_duration:(?P<duration>\d+(?:\.\d+)?)",
        )
        for event in black_events:
            add(
                Check(
                    "final.black_frames",
                    "warning",
                    "Final video contains a black-frame interval.",
                    path=str(final_video),
                    details=event,
                )
            )

        # NOTE: freezedetect warnings on deliberate calm holds (>=2.5s static
        # shots — a paused diagram, a held beat) are EXPECTED and must stay
        # warnings, never errors. They flag "is this intentional?" for a human,
        # not a defect.
        freeze_events = _detect_video_events(
            final_video,
            "freezedetect=n=-60dB:d=3",
            r"freeze_start: (?P<start>\d+(?:\.\d+)?).*?freeze_duration: (?P<duration>\d+(?:\.\d+)?)",
        )
        for event in freeze_events:
            add(
                Check(
                    "final.freeze",
                    "warning",
                    "Final video contains a long near-static interval.",
                    path=str(final_video),
                    details=event,
                )
            )

    return _report(run_dir, checks, segment_checks)


def _report(run_dir: Path, checks: list[Check], segment_checks: dict[str, list[Check]]) -> dict[str, Any]:
    error_count = sum(1 for c in checks if c.severity == "error")
    warning_count = sum(1 for c in checks if c.severity == "warning")
    info_count = sum(1 for c in checks if c.severity == "info")
    status = "failed" if error_count else "warning" if warning_count else "passed"
    report = {
        "status": status,
        "summary": {
            "errors": error_count,
            "warnings": warning_count,
            "info": info_count,
        },
        "checks": [c.as_dict() for c in checks],
        "segments": {
            seg_id: {
                "status": _status_for(items),
                "checks": [item.as_dict() for item in items],
            }
            for seg_id, items in segment_checks.items()
        },
    }
    (run_dir / "qa_report.json").write_text(json.dumps(report, indent=2))
    return report
