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
    except subprocess.TimeoutExpired:
        return "audio normalization failed: ffmpeg timed out"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"audio normalization failed: {exc}"
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def _reusable_take(entry) -> bool:
    """True when an existing audio manifest entry is a good, reusable take:
    it succeeded, carries no qa_issues, and its normalized wav is still on disk.
    """
    if not isinstance(entry, dict) or entry.get("failed") or entry.get("silent"):
        return False
    # Only entries written by a real synthesis pass carry a qa_issues list;
    # cmd_silence placeholders (and hand-written entries) lack it and must
    # never be reused as narration.
    if entry.get("qa_issues") != []:
        return False
    if float(entry.get("duration_seconds") or 0) <= 0:
        return False
    audio_path = entry.get("audio_path")
    if not audio_path:
        return False
    path = Path(audio_path)
    return path.exists() and path.stat().st_size > 0


def cmd_synthesize(script_json: str, output_dir: str):
    """Synthesize voice audio for all segments in a script.

    Dispatches on config.voice_provider: "voicebox" (Voicebox app REST API,
    no fallback — hard-fails if the app/profile is unavailable), "qwen"
    (bundled Qwen3-TTS, local), or "elevenlabs" (cloud). As a legacy
    convenience, an empty ElevenLabs key falls back to Qwen — but only when
    ElevenLabs was the chosen provider; "voicebox" never silently falls back.

    Segments that already have a good take in audio_manifest.json (no
    qa_issues, wav on disk) are SKIPPED unless PTV_AUDIO_FORCE=1, so re-runs
    never re-roll approved narration out from under already-generated clips.
    The effective voice settings persist in the manifest under "_voice" and are
    reused on resume unless explicitly overridden via env, so a resumed run
    never switches voices. Exits 2 when any segment fails or still carries
    qa_issues after its retry; failed segments get duration_seconds 0 and
    "failed": true instead of a fake estimated duration.
    """
    import os

    from .analysis.script_writer import load_script
    from .config import PipelineConfig

    config = PipelineConfig()
    script = load_script(Path(script_json))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    manifest_path = out / "audio_manifest.json"
    existing: dict = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
    persisted_voice = existing.get("_voice")
    if not isinstance(persisted_voice, dict):
        persisted_voice = {}

    def voice_setting(env_key: str, persisted_key: str, config_value):
        # An env var set for THIS invocation is an explicit override (pydantic
        # already folded it into config_value); otherwise the voice persisted
        # in the manifest wins so resumes never switch voices mid-run.
        if os.environ.get(env_key):
            return config_value
        return persisted_voice.get(persisted_key) or config_value

    provider = voice_setting("PTV_VOICE_PROVIDER", "provider", config.voice_provider)
    if provider == "elevenlabs" and not config.elevenlabs_api_key:
        provider = "qwen"

    if provider == "voicebox":
        profile = voice_setting("PTV_VOICEBOX_PROFILE", "profile", config.voicebox_profile)
        language = voice_setting("PTV_VOICEBOX_LANGUAGE", "language", config.voicebox_language)
        voice_meta = {"provider": "voicebox", "profile": profile, "language": language}
        console.print(f"[blue]Using Voicebox ({profile}) for voice synthesis[/blue]")
        from .studio.tts_voicebox import generate_speech_voicebox, resolve_profile

        # Resolve the profile ONCE, before the loop. A miss (or a down server)
        # is a hard failure with an actionable error — no fallback provider.
        try:
            profile_id = resolve_profile(config.voicebox_url, profile)
        except RuntimeError as exc:
            console.print(f"[red]Voicebox unavailable: {exc}[/red]")
            sys.exit(1)

        # Qwen re-rolls a drifting take with a stricter instruction; Voicebox
        # has no instruct knob, so re-roll the sampler seed instead. The first
        # take is server-chosen (seed=None); the retry pins a deterministic seed.
        attempts = (None, 1)
        retry_note = "retrying with a fresh Voicebox seed"

        def take(seg, audio_path, seed):
            return generate_speech_voicebox(
                text=seg.narration_text,
                output_path=audio_path,
                profile=profile_id,
                language=language,
                seed=seed,
                url=config.voicebox_url,
            )

    elif provider == "qwen":
        speaker = voice_setting("PTV_QWEN_TTS_SPEAKER", "speaker", config.qwen_tts_speaker)
        language = voice_setting("PTV_QWEN_TTS_LANGUAGE", "language", config.qwen_tts_language)
        voice_meta = {"provider": "qwen", "speaker": speaker, "language": language}
        console.print(f"[blue]Using Qwen3-TTS (local, speaker={speaker}) for voice synthesis[/blue]")
        from .studio.tts import generate_speech

        retry_instruction = (
            "Read the text exactly once in clear English. Do not translate, improvise, "
            "repeat, add words, or continue after the final sentence."
        )
        attempts = (None, retry_instruction)
        retry_note = "retrying with stricter TTS instruction"

        def take(seg, audio_path, instruct):
            return generate_speech(
                text=seg.narration_text,
                output_path=audio_path,
                speaker=speaker,
                language=language,
                instruct=instruct,
                model_size=config.qwen_tts_model_size,
            )

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

        # ElevenLabs raises on any API failure, so the process already exits
        # non-zero; no per-segment retry/skip loop here.
        audio_segments = synth.synthesize_all(script.segments, out)

        manifest = {
            seg.segment_id: {
                "audio_path": str(seg.audio_path),
                "duration_seconds": seg.duration_seconds,
            }
            for seg in audio_segments
        }
        manifest["_voice"] = {
            "provider": "elevenlabs",
            "voice_id": config.voice_id,
            "model": config.elevenlabs_model,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2))

        total = sum(s.duration_seconds for s in audio_segments)
        console.print(f"[green]Synthesized {len(audio_segments)} segments ({total:.1f}s total)[/green]")
        console.print(f"  Manifest: {manifest_path}")
        print(json.dumps({
            "synthesized": [s.segment_id for s in audio_segments],
            "skipped": [], "failed": [], "qa_flagged": [],
            "voice": manifest["_voice"], "manifest_path": str(manifest_path),
        }))
        return

    # An explicit voice change makes every existing take stale: reusing them
    # would ship a video that switches voices mid-run.
    voice_changed = bool(persisted_voice) and persisted_voice != voice_meta
    if voice_changed:
        console.print(
            f"[yellow]Voice changed ({persisted_voice} -> {voice_meta}); "
            f"regenerating all segments[/yellow]"
        )

    manifest = {}
    synthesized, skipped, failed, flagged = [], [], [], []
    for seg in script.segments:
        audio_path = out / f"audio_{seg.segment_id}.wav"
        prior = existing.get(seg.segment_id)
        if not config.audio_force and not voice_changed and _reusable_take(prior):
            manifest[seg.segment_id] = prior
            skipped.append(seg.segment_id)
            console.print(
                f"  [dim]Skip (good take exists): {seg.segment_id} "
                f"({float(prior['duration_seconds']):.1f}s)[/dim]"
            )
            continue

        console.print(f"  {seg.segment_id}: {seg.narration_text[:60]}…")
        qa_issues: list[str] = []
        result = {"success": False, "error": "not attempted"}
        duration = seg.estimated_duration_seconds
        for attempt, attempt_param in enumerate(attempts, start=1):
            qa_issues = []
            result = take(seg, audio_path, attempt_param)
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
                console.print(f"    [yellow]{drift}; {retry_note}[/yellow]")
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
            synthesized.append(seg.segment_id)
            if qa_issues:
                flagged.append({"segment_id": seg.segment_id, "qa_issues": qa_issues})
            issue_note = " [yellow](QA issue)[/yellow]" if qa_issues else ""
            console.print(f"    [green]-> {audio_path.name} ({duration:.1f}s)[/green]{issue_note}")
        else:
            error = result.get("error") or "TTS failed"
            console.print(f"    [red]FAILED: {error}[/red]")
            # duration_seconds stays 0 and "failed" is set so downstream steps
            # (videogen/manifest) can't treat this segment as having real audio.
            manifest[seg.segment_id] = {
                "audio_path": str(audio_path),
                "duration_seconds": 0,
                "failed": True,
                "error": error,
                "qa_issues": [error],
            }
            failed.append({"segment_id": seg.segment_id, "error": error})

    manifest["_voice"] = voice_meta
    manifest_path.write_text(json.dumps(manifest, indent=2))
    total = sum(
        float(entry.get("duration_seconds") or 0)
        for key, entry in manifest.items()
        if not key.startswith("_")
    )
    engine = "Voicebox" if provider == "voicebox" else "Qwen3-TTS"
    console.print(
        f"[green]Synthesized {len(synthesized)} segments, skipped {len(skipped)} "
        f"({total:.1f}s total) via {engine}[/green]"
    )
    console.print(f"  Manifest: {manifest_path}")
    print(json.dumps({
        "synthesized": synthesized, "skipped": skipped, "failed": failed,
        "qa_flagged": flagged, "voice": voice_meta,
        "manifest_path": str(manifest_path),
    }))
    if failed or flagged:
        # Exit non-zero (like imagegen/videogen) so callers can't mistake a
        # run with failed or QA-flagged narration for success.
        console.print(
            f"[red]synthesize: {len(failed)} segment(s) failed, "
            f"{len(flagged)} with QA issues[/red]"
        )
        sys.exit(2)


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
            # Placeholder track — cmd_synthesize must never reuse it as a take.
            "silent": True,
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


# Rough documentary-narration speaking pace used only to sanity-check a
# script's estimated_duration_seconds before synthesize ever runs (empirical
# baseline: ~110-130 wpm observed across Qwen3-TTS takes in this pipeline —
# deliberately conservative so we warn on clearly-wrong estimates, not merely
# fast ones).
_NARRATION_WORDS_PER_SECOND = 2.2


def _implausible_duration_warning(seg, config) -> dict[str, str] | None:
    """Flag a segment whose estimated_duration_seconds can't plausibly fit its
    narration_text, so a bad guess is caught at the preproduction gate instead
    of surfacing later as a synthesize QA failure (duration drift) after a
    wasted TTS generation."""
    text = (seg.narration_text or "").strip()
    estimated = float(seg.estimated_duration_seconds or 0)
    if not text or estimated <= 0:
        return None
    word_count = len(text.split())
    expected = word_count / _NARRATION_WORDS_PER_SECOND
    if expected <= 0 or expected / estimated <= config.qa_max_audio_duration_ratio:
        return None
    return {
        "segment_id": seg.segment_id,
        "warning": (
            f"estimated_duration_seconds ({estimated:.1f}s) looks too short for "
            f"~{word_count} words of narration (~{expected:.1f}s at a typical "
            f"narration pace) — synthesize will likely produce audio that trips "
            f"the duration-drift QA check. Raise the estimate before running synthesize."
        ),
    }


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
        duration_warning = _implausible_duration_warning(seg, config)
        if duration_warning:
            warnings.append(duration_warning)

        audio_entry = audio_manifest.get(seg.segment_id)
        # Failed synthesis entries carry duration 0 — fall back to the estimate
        # rather than storyboarding a zero-length segment.
        entry_duration = (
            float(audio_entry.get("duration_seconds") or 0) if audio_entry else 0.0
        )
        segment_duration = (
            entry_duration
            if audio_entry and not audio_entry.get("failed") and entry_duration > 0
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
    if failed:
        # Exit non-zero (like cmd_manifest) so callers — especially the studio
        # chat agent's imagegen tool, which flags errors by exit code — can't
        # mistake a partially/fully failed image pass for success.
        console.print(f"[red]imagegen: {len(failed)} beat(s) failed[/red]")
        sys.exit(2)


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
    generated, skipped, failed, fallbacks = [], [], [], []

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
        # Entries from a failed synthesis ("failed": true, duration 0) count as
        # missing — never size clips from a fake duration.
        entry = audio_manifest.get(seg.segment_id)
        entry_duration = float(entry.get("duration_seconds") or 0) if entry else 0.0
        usable_entry = bool(entry) and not entry.get("failed") and entry_duration > 0
        segment_duration = entry_duration if usable_entry else seg.estimated_duration_seconds
        if not usable_entry:
            console.print(
                f"[yellow]{seg.segment_id}: no usable audio manifest entry, "
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
                    ltx_error = result["error_message"]
                    result = kenburns_clip(
                        img, out, duration, resolution=config.resolution, fps=config.frame_rate,
                        zoom=config.kenburns_zoom, direction=direction,
                    )
                    if result["success"]:
                        fallbacks.append({
                            "segment_id": seg.segment_id,
                            "beat_id": beat.beat_id,
                            "ltx_error": ltx_error,
                        })
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
        "fallbacks": fallbacks, "clips_dir": str(clips_dir),
    }))
    if fallbacks:
        # Fallback pans are allowed (config.video_fallback_to_kenburns) but must
        # be LOUD: a run where every "motion" clip is secretly a Ken Burns pan
        # reads as "the video generator didn't actually make videos".
        console.print(
            f"[yellow]videogen: {len(fallbacks)} clip(s) are Ken Burns fallbacks, "
            f"not real AI motion — first LTX error: {fallbacks[0]['ltx_error']}[/yellow]"
        )
    if failed:
        # Exit non-zero (like cmd_manifest) so callers — especially the studio
        # chat agent's videogen tool, which flags errors by exit code — can't
        # mistake missing clips for success and composite an unfinished video.
        console.print(f"[red]videogen: {len(failed)} beat(s) failed[/red]")
        sys.exit(2)


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
        raw_audio = (audio_entry or {}).get("audio_path")
        audio_path = Path(raw_audio) if raw_audio else None
        # "failed" entries come from a failed synthesis; even if a partial wav
        # exists on disk it must not be composited as real narration.
        if (
            not audio_entry
            or audio_entry.get("failed")
            or not audio_path
            or not audio_path.exists()
        ):
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


def cmd_composite(manifest_json: str, output_path: str, *args):
    """Composite video and audio segments into a final video. Audio is optional.

    Usage: composite <manifest.json> <output.mp4> [--speed <0.25-4.0>]
    """
    from .compositing.compositor import VideoCompositor
    from .config import PipelineConfig

    config = PipelineConfig()
    speed = config.video_speed
    if "--speed" in args:
        speed = float(args[args.index("--speed") + 1])

    manifest = json.loads(Path(manifest_json).read_text())

    video_paths = [Path(v) for v in manifest["video_paths"]]
    audio_paths = [Path(a) for a in manifest.get("audio_paths", [])]

    compositor = VideoCompositor()
    final = compositor.composite(
        video_paths=video_paths,
        audio_paths=audio_paths,
        output_path=Path(output_path),
        resolution=config.resolution,
        speed=speed,
    )

    console.print(f"[green]Final video: {final}[/green]")

    # Also drop a copy at <run_dir>/final.mp4 so the Studio viewer shows it.
    # The manifest lives in the run dir, so its parent is the run dir.
    import shutil
    run_dir = Path(manifest_json).resolve().parent
    # Record the playback speed actually applied. The --speed flag overrides
    # config.video_speed, so QA's A/V-sync check must read the real speed from
    # here rather than assuming the config default (which would falsely flag a
    # speed-adjusted final as drifted).
    try:
        (run_dir / "composite_meta.json").write_text(
            json.dumps({"speed": speed}), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Could not write composite_meta.json: {exc}[/yellow]")
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


def cmd_sfx(script_json: str, run_dir: str):
    """Mix procedural sound effects under segment narration (after align).

    Exits 0 printing {"skipped": true} when no segment declares sfx.
    """
    from .audio.sfx import run_sfx

    run_sfx(Path(script_json), Path(run_dir))


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
    "sfx": cmd_sfx,
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
