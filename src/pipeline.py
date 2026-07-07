"""Pipeline toolkit — individual commands that Claude Code orchestrates.

Each function is a standalone step that can be called via:
    uv run python -m src.pipeline <command> [args]

Commands:
    synthesize <script.json> <output_dir>  Synthesize voice for all segments
    silence <script.json> <output_dir>     Generate silent audio (estimated durations)
    render <scene_spec.json> <work_dir>    Validate and render a single scene
    storyboard <script.json> <run_dir>     Write storyboard.json from script visual beats
    imagegen <script.json> <run_dir> [ids] Generate FLUX stills for scene visual beats
    videogen <script.json> <run_dir> [ids] Turn scene stills into motion clips
    manifest <script.json> <run_dir> [out] Build a composite manifest from run artifacts
    fallback <segment_id> <title> <desc> <duration> <work_dir>  Generate fallback visual
    composite <manifest.json> <output.mp4> Composite final video (audio optional)
    qa <run_dir> [strict]                  Run release QA and write qa_report.json
    setup <base_dir>                       Create working directories
"""

import json
import sys
from pathlib import Path

from rich.console import Console

console = Console()

# Cross-process lock so only ONE heavy generation (FLUX image or LTX video) runs
# at a time. On Apple MPS, concurrent generations cause memory pressure and noisy
# output. A second generation BLOCKS until the first releases the lock.
# The implementation lives in src/utils/locks.py so non-CLI modules
# (src/assets/generate.py) can share it without importing this module.
from .utils.locks import generation_lock as _generation_lock  # noqa: E402


def _probe_media_duration(path: Path) -> float | None:
    import subprocess

    probe = subprocess.run(
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
        capture_output=True,
        text=True,
        timeout=10,
    )
    try:
        return float(probe.stdout.strip()) if probe.stdout.strip() else None
    except ValueError:
        return None


def _duration_drift_issue(actual: float, expected: float, config) -> str | None:
    if expected <= 0:
        return None
    ratio = actual / expected
    overage = actual - expected
    if ratio <= config.qa_max_audio_duration_ratio:
        return None
    if overage <= config.qa_max_audio_duration_overage_seconds:
        return None
    return (
        f"duration drift: actual {actual:.1f}s vs estimated {expected:.1f}s "
        f"({ratio:.2f}x)"
    )


def _normalize_audio_file(path: Path, target_lufs: float = -16.0) -> str | None:
    import shutil
    import subprocess
    import tempfile

    path = Path(path)
    if not path.exists():
        return "audio file missing"

    suffix = path.suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(path),
                "-af",
                f"loudnorm=I={target_lufs}:LRA=11:TP=-1.5",
                "-ar",
                "48000",
                "-ac",
                "1",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
            return f"audio normalization failed: {(proc.stderr or proc.stdout)[-300:]}"
        shutil.move(str(tmp_path), str(path))
        return None
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def cmd_synthesize(script_json: str, output_dir: str):
    """Synthesize voice audio for all segments in a script.

    Uses Qwen3-TTS (local) when no ElevenLabs key is set, or when
    voice_provider is explicitly set to 'qwen'.
    """
    from .analysis.script_writer import load_script
    from .config import PipelineConfig

    config = PipelineConfig()
    script = load_script(Path(script_json))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    use_qwen = config.voice_provider == "qwen" or not config.elevenlabs_api_key

    if use_qwen:
        console.print("[blue]Using Qwen3-TTS (local) for voice synthesis[/blue]")
        from .studio.tts import generate_speech
        manifest = {}
        for seg in script.segments:
            audio_path = out / f"audio_{seg.segment_id}.wav"
            console.print(f"  {seg.segment_id}: {seg.narration_text[:60]}…")
            qa_issues: list[str] = []
            result = {"success": False, "error": "not attempted"}
            duration = seg.estimated_duration_seconds
            retry_instruction = (
                "Read the text exactly once in clear English. Do not translate, improvise, "
                "repeat, add words, or continue after the final sentence."
            )
            for attempt, instruct in enumerate((None, retry_instruction), start=1):
                result = generate_speech(
                    text=seg.narration_text,
                    output_path=audio_path,
                    speaker=config.qwen_tts_speaker,
                    language=config.qwen_tts_language,
                    instruct=instruct,
                    model_size=config.qwen_tts_model_size,
                )
                if not result["success"]:
                    break

                norm_error = _normalize_audio_file(audio_path, config.qa_target_lufs)
                if norm_error:
                    qa_issues.append(norm_error)

                probed = _probe_media_duration(audio_path)
                duration = probed if probed is not None else seg.estimated_duration_seconds
                drift = _duration_drift_issue(
                    duration,
                    seg.estimated_duration_seconds,
                    config,
                )
                if drift and attempt == 1:
                    console.print(f"    [yellow]{drift}; retrying with stricter TTS instruction[/yellow]")
                    continue
                if drift:
                    qa_issues.append(drift)
                break

            if result["success"]:
                manifest[seg.segment_id] = {
                    "audio_path": str(audio_path),
                    "duration_seconds": duration,
                    "qa_issues": qa_issues,
                }
                issue_note = " [yellow](QA issue)[/yellow]" if qa_issues else ""
                console.print(f"    [green]-> {audio_path.name} ({duration:.1f}s)[/green]{issue_note}")
            else:
                console.print(f"    [red]FAILED: {result.get('error', '?')}[/red]")
                manifest[seg.segment_id] = {
                    "audio_path": str(audio_path),
                    "duration_seconds": seg.estimated_duration_seconds,
                    "qa_issues": [result.get("error", "TTS failed")],
                }

        manifest_path = out / "audio_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        total = sum(v["duration_seconds"] for v in manifest.values())
        console.print(f"[green]Synthesized {len(manifest)} segments ({total:.1f}s total) via Qwen3-TTS[/green]")
        console.print(f"  Manifest: {manifest_path}")

    else:
        console.print("[blue]Using ElevenLabs for voice synthesis[/blue]")
        from .voice.synthesizer import VoiceSynthesizer
        synth = VoiceSynthesizer(
            api_key=config.elevenlabs_api_key,
            voice_id=config.voice_id,
            stability=config.voice_stability,
            similarity_boost=config.voice_similarity_boost,
            style=config.voice_style,
            model_id=config.elevenlabs_model,
            use_speaker_boost=config.voice_use_speaker_boost,
        )

        audio_segments = synth.synthesize_all(script.segments, out)

        manifest = {
            seg.segment_id: {
                "audio_path": str(seg.audio_path),
                "duration_seconds": seg.duration_seconds,
            }
            for seg in audio_segments
        }
        manifest_path = out / "audio_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        total = sum(s.duration_seconds for s in audio_segments)
        console.print(f"[green]Synthesized {len(audio_segments)} segments ({total:.1f}s total)[/green]")
        console.print(f"  Manifest: {manifest_path}")


def cmd_silence(script_json: str, output_dir: str):
    """Generate silent audio files matching script segment durations. No API needed."""
    import subprocess
    from .analysis.script_writer import load_script

    script = load_script(Path(script_json))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    manifest = {}
    for seg in script.segments:
        duration = seg.estimated_duration_seconds
        audio_path = out / f"audio_{seg.segment_id}.mp3"

        # Generate silent audio with ffmpeg
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", str(duration),
                "-q:a", "9",
                str(audio_path),
            ],
            capture_output=True, timeout=30,
        )

        manifest[seg.segment_id] = {
            "audio_path": str(audio_path),
            "duration_seconds": duration,
        }
        console.print(f"  [dim]Silent: {seg.segment_id} ({duration:.1f}s)[/dim]")

    manifest_path = out / "audio_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    total = sum(s.estimated_duration_seconds for s in script.segments)
    console.print(f"[green]Generated {len(script.segments)} silent tracks ({total:.1f}s total)[/green]")
    console.print(f"  Manifest: {manifest_path}")


def cmd_render(scene_spec_json: str, work_dir: str):
    """Validate and render a single scene from a spec JSON file."""
    from .animation.codegen import load_scene_spec
    from .animation.fixer import validate_and_render
    from .config import PipelineConfig

    config = PipelineConfig()
    spec = load_scene_spec(Path(scene_spec_json))
    wd = Path(work_dir)
    wd.mkdir(parents=True, exist_ok=True)

    result = validate_and_render(
        spec=spec,
        quality_flag=config.manim_quality_flag,
        work_dir=wd,
        render_timeout=config.render_timeout_seconds,
        resolution=config.resolution,
        fps=config.frame_rate,
    )

    # Save result
    result_path = wd / f"{spec.segment_id}_result.json"
    result_path.write_text(result.model_dump_json(indent=2))

    if result.success:
        console.print(f"[green]Rendered: {spec.segment_id} -> {result.video_path}[/green]")
    else:
        console.print(f"[red]Failed: {spec.segment_id}: {result.error_message}[/red]")

    # Print result as JSON for Claude to parse
    print(json.dumps({
        "success": result.success,
        "video_path": str(result.video_path),
        "error_message": result.error_message,
        "duration": result.actual_duration_seconds,
    }))


def cmd_fallback(segment_id: str, title: str, description: str, duration: str, work_dir: str):
    """Generate a fallback title card visual."""
    from .animation.fixer import generate_fallback

    result = generate_fallback(
        segment_id=segment_id,
        title=title,
        description=description,
        duration=float(duration),
        work_dir=Path(work_dir),
    )

    print(json.dumps({
        "success": result.success,
        "video_path": str(result.video_path),
        "error_message": result.error_message,
    }))


def cmd_storyboard(script_json: str, run_dir: str):
    """Write a storyboard manifest from the script's scene beats and diagram cues."""
    from .analysis.script_writer import load_script
    from .config import PipelineConfig
    from .visuals.beats import (
        beat_clip_path,
        beat_image_path,
        segment_visual_beats,
        split_beat_durations,
    )

    config = PipelineConfig()
    script = load_script(Path(script_json))
    rd = Path(run_dir)
    storyboard_path = rd / "storyboard.json"
    audio_manifest_path = rd / "audio" / "audio_manifest.json"
    audio_manifest = (
        json.loads(audio_manifest_path.read_text()) if audio_manifest_path.exists() else {}
    )

    warnings: list[dict[str, str]] = []
    segments: list[dict[str, object]] = []
    total_frames = 0

    for seg in script.segments:
        audio_entry = audio_manifest.get(seg.segment_id)
        segment_duration = (
            float(audio_entry.get("duration_seconds", 0))
            if audio_entry
            else float(seg.estimated_duration_seconds or 0)
        )

        if seg.visual_type == "scene":
            beats = segment_visual_beats(seg)
            if not beats:
                warnings.append(
                    {"segment_id": seg.segment_id, "warning": "scene has no storyboard beats"}
                )
            if segment_duration > 6 and len(beats) < 2:
                warnings.append(
                    {
                        "segment_id": seg.segment_id,
                        "warning": "scene longer than 6s should usually have 2+ storyboard beats",
                    }
                )
            beat_durations = split_beat_durations(segment_duration, beats)
            max_ltx_duration = min(
                float(config.ltx_clip_seconds),
                max((int(config.ltx_max_frames) - 1) / 24, 0.1),
            )
            frames = []
            for beat, duration in zip(beats, beat_durations, strict=True):
                if duration > max_ltx_duration + 0.5:
                    warnings.append(
                        {
                            "segment_id": seg.segment_id,
                            "warning": (
                                f"{beat.file_stem} is {duration:.1f}s; use more visual_beats "
                                f"or explicit beat durations near {max_ltx_duration:.1f}s for LTX"
                            ),
                        }
                    )
                total_frames += 1
                frames.append(
                    {
                        "frame_id": beat.file_stem,
                        "segment_id": beat.segment_id,
                        "beat_id": beat.beat_id,
                        "duration_seconds": round(duration, 2),
                        "description": beat.description,
                        "shot_type": beat.shot_type,
                        "composition": beat.composition,
                        "action": beat.action,
                        "camera_motion": beat.camera_motion,
                        "transition": beat.transition,
                        "continuity_notes": beat.continuity_notes or [],
                        "asset_notes": beat.asset_notes or [],
                        "image_prompt": beat.prompt,
                        "image_path": str(beat_image_path(rd, beat)),
                        "clip_path": str(beat_clip_path(rd, beat)),
                    }
                )
        else:
            frames = [
                {
                    "frame_id": f"{seg.segment_id}_cue_{idx:02d}",
                    "segment_id": seg.segment_id,
                    "beat_id": f"cue_{idx:02d}",
                    "duration_seconds": None,
                    "description": cue.description,
                    "shot_type": "diagram",
                    "composition": cue.math_content,
                    "action": cue.timestamp_hint,
                    "camera_motion": None,
                    "transition": seg.transition_type,
                    "continuity_notes": [],
                    "asset_notes": [],
                    "image_prompt": None,
                    "image_path": None,
                    "clip_path": None,
                }
                for idx, cue in enumerate(seg.animation_cues, start=1)
            ]
            total_frames += len(frames)

        segments.append(
            {
                "segment_id": seg.segment_id,
                "section_title": seg.section_title,
                "visual_type": seg.visual_type,
                "duration_seconds": round(segment_duration, 2),
                "frames": frames,
            }
        )

    storyboard = {
        "title": script.title,
        "subject": script.subject,
        "style_bible": script.style_bible,
        "storyboard_summary": script.storyboard_summary,
        "storyboard_rules": script.storyboard_rules,
        "segments": segments,
        "summary": {
            "segment_count": len(script.segments),
            "frame_count": total_frames,
            "warnings": len(warnings),
        },
        "warnings": warnings,
    }
    storyboard_path.write_text(json.dumps(storyboard, indent=2))

    status = "warning" if warnings else "passed"
    color = "yellow" if warnings else "green"
    console.print(
        f"[{color}]Storyboard {status}: {total_frames} frames, "
        f"{len(warnings)} warnings[/{color}]"
    )
    console.print(f"  Storyboard: {storyboard_path}")
    print(json.dumps({"status": status, "storyboard_path": str(storyboard_path), "warnings": warnings}))


def cmd_imagegen(script_json: str, run_dir: str, segment_ids: str = ""):
    """Generate FLUX still images for 'scene' segment visual beats.

    segment_ids: optional comma-separated segment/beat filter; default = all scene segments.
    Skips existing PNGs unless PTV_IMAGE_FORCE=1.
    """
    from .analysis.script_writer import load_script
    from .config import PipelineConfig
    from .imagegen.flux import generate_image
    from .visuals.beats import beat_image_path, beat_matches_filter, segment_visual_beats

    config = PipelineConfig()
    script = load_script(Path(script_json))
    images_dir = Path(run_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    w, h = config.resolution

    only = {s.strip() for s in segment_ids.split(",") if s.strip()}
    generated, skipped, failed = [], [], []

    for seg in script.segments:
        if seg.visual_type != "scene":
            continue
        beats = segment_visual_beats(seg)
        if not beats:
            failed.append(
                {"segment_id": seg.segment_id, "error": "scene has no image prompt or visual beats"}
            )
            continue
        for beat in beats:
            if not beat_matches_filter(beat, only):
                continue
            out = beat_image_path(Path(run_dir), beat)
            if out.exists() and out.stat().st_size > 0 and not config.image_force:
                skipped.append(beat.file_stem)
                console.print(f"[dim]Skip (exists): {beat.label}[/dim]")
                continue
            if not beat.prompt.strip():
                failed.append(
                    {
                        "segment_id": seg.segment_id,
                        "beat_id": beat.beat_id,
                        "error": "empty image prompt",
                    }
                )
                continue
            with _generation_lock():  # never run two generations at once (MPS)
                result = generate_image(
                    prompt=beat.prompt, output_path=out, segment_id=beat.file_stem,
                    width=w, height=h, steps=config.image_steps, model=config.image_model,
                    quantize=config.image_quantize, timeout=config.image_timeout_seconds,
                    models_dir=config.models_dir,
                )
            if result.success:
                generated.append(beat.file_stem)
            else:
                failed.append({
                    "segment_id": seg.segment_id,
                    "beat_id": beat.beat_id,
                    "error": result.error_message,
                })
                console.print(f"[red]Failed: {beat.label}: {result.error_message}[/red]")

    print(json.dumps({
        "generated": generated, "skipped": skipped, "failed": failed,
        "images_dir": str(images_dir),
    }))


def cmd_videogen(script_json: str, run_dir: str, segment_ids: str = ""):
    """Turn scene stills into motion clips that sum to each segment's audio duration.

    Reads run_dir/audio/audio_manifest.json for durations. Writes run_dir/clips/{beat}.mp4
    using the configured provider (kenburns | comfyui | ltx). segment_ids optional filter.
    """
    from .analysis.script_writer import load_script
    from .config import PipelineConfig
    from .videogen.kenburns import kenburns_clip, DIRECTIONS
    from .visuals.beats import (
        beat_clip_path,
        beat_image_path,
        beat_matches_filter,
        ltx_motion_prompt,
        segment_visual_beats,
        split_beat_durations,
    )

    config = PipelineConfig()
    script = load_script(Path(script_json))
    rd = Path(run_dir)
    clips_dir = rd / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = rd / "audio" / "audio_manifest.json"
    audio_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    only = {s.strip() for s in segment_ids.split(",") if s.strip()}
    generated, skipped, failed = [], [], []

    scene_index = 0
    for seg in script.segments:
        if seg.visual_type != "scene":
            continue
        beats = segment_visual_beats(seg)
        if not beats:
            failed.append(
                {"segment_id": seg.segment_id, "error": "scene has no image prompt or visual beats"}
            )
            continue

        # Duration: prefer the real audio duration; fall back to the estimate.
        entry = audio_manifest.get(seg.segment_id)
        segment_duration = entry["duration_seconds"] if entry else seg.estimated_duration_seconds
        if not entry:
            console.print(
                f"[yellow]{seg.segment_id}: no audio manifest entry, "
                f"using estimate {segment_duration:.1f}s[/yellow]"
            )
        beat_durations = split_beat_durations(float(segment_duration), beats)

        for beat, duration in zip(beats, beat_durations, strict=True):
            direction = DIRECTIONS[scene_index % len(DIRECTIONS)]
            scene_index += 1
            if not beat_matches_filter(beat, only):
                continue

            img = beat_image_path(rd, beat)
            if not img.exists():
                failed.append({
                    "segment_id": seg.segment_id,
                    "beat_id": beat.beat_id,
                    "error": "no image (run imagegen first)",
                })
                continue

            out = beat_clip_path(rd, beat)
            if (
                out.exists()
                and out.stat().st_size > 0
                and not (config.video_force or config.image_force)
            ):
                skipped.append(beat.file_stem)
                continue

            if config.video_provider == "ltx":
                from .videogen.ltx import generate_ltx_clip
                with _generation_lock():  # never run two generations at once (MPS)
                    result = generate_ltx_clip(
                        img, out, duration, prompt=ltx_motion_prompt(beat),
                        resolution=config.resolution, fps=config.frame_rate,
                        model_id=config.ltx_model, steps=config.ltx_steps,
                        gen_width=config.ltx_gen_width, gen_height=config.ltx_gen_height,
                        clip_seconds=config.ltx_clip_seconds, models_dir=config.models_dir,
                        prefer_extend=config.ltx_prefer_extend,
                        max_frames=config.ltx_max_frames,
                        anchor_last_frame=config.ltx_anchor_last_frame,
                        cfg_scale=config.ltx_cfg_scale,
                        stg_scale=config.ltx_stg_scale,
                    )
                if not result["success"] and config.video_fallback_to_kenburns:
                    console.print(f"[yellow]LTX failed ({result['error_message']}); Ken Burns fallback[/yellow]")
                    result = kenburns_clip(
                        img, out, duration, resolution=config.resolution, fps=config.frame_rate,
                        zoom=config.kenburns_zoom, direction=direction,
                    )
            elif config.video_provider == "comfyui":
                from .videogen.comfyui import image_to_video
                result = image_to_video(
                    img, out, duration, resolution=config.resolution, fps=config.frame_rate,
                    model=config.comfyui_model, base_url=config.comfyui_url,
                    fallback_to_kenburns=config.video_fallback_to_kenburns, direction=direction,
                )
            else:
                result = kenburns_clip(
                    img, out, duration, resolution=config.resolution, fps=config.frame_rate,
                    zoom=config.kenburns_zoom, direction=direction,
                )

            if result["success"]:
                generated.append(beat.file_stem)
            else:
                failed.append({
                    "segment_id": seg.segment_id,
                    "beat_id": beat.beat_id,
                    "error": result["error_message"],
                })

    print(json.dumps({
        "generated": generated, "skipped": skipped, "failed": failed,
        "clips_dir": str(clips_dir),
    }))


def cmd_manifest(script_json: str, run_dir: str, output_path: str = ""):
    """Build a composite manifest from script order and existing run artifacts."""
    from .analysis.script_writer import load_script
    from .compositing.compositor import resolve_segment_videos

    script = load_script(Path(script_json))
    rd = Path(run_dir)
    manifest_path = Path(output_path) if output_path else rd / "composite_manifest.json"
    audio_manifest_path = rd / "audio" / "audio_manifest.json"
    audio_manifest = (
        json.loads(audio_manifest_path.read_text()) if audio_manifest_path.exists() else {}
    )

    video_paths: list[str] = []
    audio_paths: list[str] = []
    segments: list[dict[str, object]] = []
    missing: list[dict[str, str]] = []

    for seg in script.segments:
        seg_videos = resolve_segment_videos(rd, seg)
        if not seg_videos:
            missing.append({"segment_id": seg.segment_id, "artifact": "video"})
        else:
            video_paths.extend(str(path) for path in seg_videos)

        audio_entry = audio_manifest.get(seg.segment_id)
        audio_path = Path(audio_entry.get("audio_path", "")) if audio_entry else None
        if not audio_path or not audio_path.exists():
            missing.append({"segment_id": seg.segment_id, "artifact": "audio"})
            segment_audio = None
        else:
            audio_paths.append(str(audio_path))
            segment_audio = str(audio_path)

        segments.append(
            {
                "segment_id": seg.segment_id,
                "video_paths": [str(path) for path in seg_videos],
                "audio_path": segment_audio,
            }
        )

    manifest = {
        "video_paths": video_paths,
        "audio_paths": audio_paths,
        "segments": segments,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    result = {
        "manifest_path": str(manifest_path),
        "video_count": len(video_paths),
        "audio_count": len(audio_paths),
        "missing": missing,
    }
    if missing:
        console.print(f"[red]Manifest has {len(missing)} missing artifacts: {missing}[/red]")
        print(json.dumps(result))
        sys.exit(2)

    console.print(
        f"[green]Manifest: {manifest_path} ({len(video_paths)} video clips, "
        f"{len(audio_paths)} audio tracks)[/green]"
    )
    print(json.dumps(result))


def cmd_composite(manifest_json: str, output_path: str):
    """Composite video and audio segments into a final video. Audio is optional."""
    from .compositing.compositor import VideoCompositor
    from .config import PipelineConfig

    config = PipelineConfig()
    manifest = json.loads(Path(manifest_json).read_text())

    video_paths = [Path(v) for v in manifest["video_paths"]]
    audio_paths = [Path(a) for a in manifest.get("audio_paths", [])]

    compositor = VideoCompositor()
    final = compositor.composite(
        video_paths=video_paths,
        audio_paths=audio_paths,
        output_path=Path(output_path),
        resolution=config.resolution,
    )

    console.print(f"[green]Final video: {final}[/green]")

    # Also drop a copy at <run_dir>/final.mp4 so the Studio viewer shows it.
    # The manifest lives in the run dir, so its parent is the run dir.
    import shutil
    run_dir = Path(manifest_json).resolve().parent
    run_final = run_dir / "final.mp4"
    try:
        if Path(final).resolve() != run_final.resolve():
            shutil.copy(final, run_final)
            console.print(f"[green]Viewer copy: {run_final}[/green]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Could not write viewer copy: {exc}[/yellow]")


def cmd_qa(run_dir: str, strict: str = ""):
    """Run automated release QA for a generated run."""
    from .qa.run_qa import qa_run

    report = qa_run(Path(run_dir), strict=strict.lower() in {"1", "true", "strict", "--strict"})
    status = report["status"]
    summary = report["summary"]
    color = "green" if status == "passed" else "red" if status == "failed" else "yellow"
    console.print(
        f"[{color}]QA {status}: {summary['errors']} errors, "
        f"{summary['warnings']} warnings[/{color}]"
    )
    console.print(f"  Report: {Path(run_dir) / 'qa_report.json'}")
    print(json.dumps(report))
    if status == "failed":
        sys.exit(2)


def cmd_setup(base_dir: str):
    """Create working directories for a pipeline run.

    If STUDIO_RUNS_DIR is set (e.g. when driven by the Studio app), runs are
    created there so the flow viewer picks them up — overriding base_dir.
    """
    import os
    from .utils.file_manager import FileManager

    base = os.environ.get("STUDIO_RUNS_DIR") or base_dir
    fm = FileManager(Path(base))
    run_dir = fm.setup()

    print(json.dumps({
        "run_dir": str(run_dir),
        "run_id": fm.run_id,
        "audio_dir": str(fm.audio_dir),
        "video_dir": str(fm.video_dir),
        "scenes_dir": str(fm.scenes_dir),
    }))


def cmd_align(script_json: str, run_dir: str):
    """Word-level narration alignment (whisper) -> <run_dir>/audio/alignment.json.

    Exits 0 printing {"skipped": true} when the run has no collage work, so the
    Studio producer can include this step unconditionally.
    """
    from .alignment.align import run_align

    run_align(Path(script_json), Path(run_dir))


def cmd_assets(script_json: str, run_dir: str, segment_ids: str = ""):
    """Generate CollageSpec assets (FLUX + optional rembg cutouts).

    Exits 0 printing {"skipped": true} when the run has no collage work.
    """
    from .assets.generate import run_assets

    run_assets(Path(script_json), Path(run_dir), segment_ids)


def cmd_collage(script_json: str, run_dir: str, segment_ids: str = ""):
    """Build and render collage scenes from scenes/{id}.collage.json specs.

    Exits 0 printing {"skipped": true} when the run has no collage work.
    """
    from .collage.cmd import run_collage

    run_collage(Path(script_json), Path(run_dir), segment_ids)


COMMANDS = {
    "synthesize": cmd_synthesize,
    "silence": cmd_silence,
    "render": cmd_render,
    "storyboard": cmd_storyboard,
    "imagegen": cmd_imagegen,
    "videogen": cmd_videogen,
    "motionclip": cmd_videogen,
    "manifest": cmd_manifest,
    "fallback": cmd_fallback,
    "composite": cmd_composite,
    "qa": cmd_qa,
    "setup": cmd_setup,
    "align": cmd_align,
    "assets": cmd_assets,
    "collage": cmd_collage,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        console.print("[red]Usage: python -m src.pipeline <command> [args][/red]")
        console.print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]
    COMMANDS[cmd](*args)


if __name__ == "__main__":
    main()
