"""Pipeline toolkit — individual commands that Claude Code orchestrates.

Each function is a standalone step that can be called via:
    uv run python -m src.pipeline <command> [args]

Commands:
    synthesize <script.json> <output_dir>  Synthesize voice for all segments
    silence <script.json> <output_dir>     Generate silent audio (estimated durations)
    render <scene_spec.json> <work_dir>    Validate and render a single scene
    imagegen <script.json> <run_dir> [ids] Generate FLUX stills for scene segments
    videogen <script.json> <run_dir> [ids] Turn scene stills into motion clips
    fallback <segment_id> <title> <desc> <duration> <work_dir>  Generate fallback visual
    composite <manifest.json> <output.mp4> Composite final video (audio optional)
    setup <base_dir>                       Create working directories
"""

import json
import sys
from pathlib import Path

from rich.console import Console

console = Console()


def cmd_synthesize(script_json: str, output_dir: str):
    """Synthesize voice audio for all segments in a script."""
    from .analysis.script_writer import load_script
    from .voice.synthesizer import VoiceSynthesizer
    from .config import PipelineConfig

    config = PipelineConfig()
    script = load_script(Path(script_json))
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

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

    # Save manifest with actual durations
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
                "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono",
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


def cmd_imagegen(script_json: str, run_dir: str, segment_ids: str = ""):
    """Generate FLUX still images for 'scene' segments. Writes run_dir/images/{seg}.png.

    segment_ids: optional comma-separated filter; default = all scene segments.
    Skips existing PNGs unless PTV_IMAGE_FORCE=1.
    """
    from .analysis.script_writer import load_script
    from .config import PipelineConfig
    from .imagegen.flux import generate_image

    config = PipelineConfig()
    script = load_script(Path(script_json))
    images_dir = Path(run_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    w, h = config.resolution

    only = {s.strip() for s in segment_ids.split(",") if s.strip()}
    generated, skipped, failed = [], [], []

    for seg in script.segments:
        if seg.visual_type != "scene" or not seg.image_prompt:
            continue
        if only and seg.segment_id not in only:
            continue
        out = images_dir / f"{seg.segment_id}.png"
        if out.exists() and out.stat().st_size > 0 and not config.image_force:
            skipped.append(seg.segment_id)
            console.print(f"[dim]Skip (exists): {seg.segment_id}[/dim]")
            continue
        result = generate_image(
            prompt=seg.image_prompt, output_path=out, segment_id=seg.segment_id,
            width=w, height=h, steps=config.image_steps, model=config.image_model,
            quantize=config.image_quantize, timeout=config.image_timeout_seconds,
            models_dir=config.models_dir,
        )
        if result.success:
            generated.append(seg.segment_id)
        else:
            failed.append({"segment_id": seg.segment_id, "error": result.error_message})
            console.print(f"[red]Failed: {seg.segment_id}: {result.error_message}[/red]")

    print(json.dumps({
        "generated": generated, "skipped": skipped, "failed": failed,
        "images_dir": str(images_dir),
    }))


def cmd_videogen(script_json: str, run_dir: str, segment_ids: str = ""):
    """Turn scene stills into motion clips at each segment's exact audio duration.

    Reads run_dir/audio/audio_manifest.json for durations. Writes run_dir/clips/{seg}.mp4
    using the configured provider (kenburns | comfyui). segment_ids optional filter.
    """
    from .analysis.script_writer import load_script
    from .config import PipelineConfig
    from .videogen.kenburns import kenburns_clip, DIRECTIONS

    config = PipelineConfig()
    script = load_script(Path(script_json))
    rd = Path(run_dir)
    images_dir = rd / "images"
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
        if only and seg.segment_id not in only:
            continue
        img = images_dir / f"{seg.segment_id}.png"
        if not img.exists():
            failed.append({"segment_id": seg.segment_id, "error": "no image (run imagegen first)"})
            continue

        out = clips_dir / f"{seg.segment_id}.mp4"
        if out.exists() and out.stat().st_size > 0 and not config.image_force:
            skipped.append(seg.segment_id)
            scene_index += 1
            continue

        # Duration: prefer the real audio duration; fall back to the estimate.
        entry = audio_manifest.get(seg.segment_id)
        duration = entry["duration_seconds"] if entry else seg.estimated_duration_seconds
        if not entry:
            console.print(f"[yellow]{seg.segment_id}: no audio manifest entry, using estimate {duration:.1f}s[/yellow]")

        direction = DIRECTIONS[scene_index % len(DIRECTIONS)]
        scene_index += 1

        if config.video_provider == "ltx":
            from .videogen.ltx import generate_ltx_clip
            result = generate_ltx_clip(
                img, out, duration, prompt=seg.image_prompt or seg.section_title,
                resolution=config.resolution, fps=config.frame_rate,
                model_id=config.ltx_model, steps=config.ltx_steps,
                gen_width=config.ltx_gen_width, gen_height=config.ltx_gen_height,
                clip_seconds=config.ltx_clip_seconds, models_dir=config.models_dir,
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
            generated.append(seg.segment_id)
        else:
            failed.append({"segment_id": seg.segment_id, "error": result["error_message"]})

    print(json.dumps({
        "generated": generated, "skipped": skipped, "failed": failed,
        "clips_dir": str(clips_dir),
    }))


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


COMMANDS = {
    "synthesize": cmd_synthesize,
    "silence": cmd_silence,
    "render": cmd_render,
    "imagegen": cmd_imagegen,
    "videogen": cmd_videogen,
    "motionclip": cmd_videogen,
    "fallback": cmd_fallback,
    "composite": cmd_composite,
    "setup": cmd_setup,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        console.print(f"[red]Usage: python -m src.pipeline <command> [args][/red]")
        console.print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    cmd = sys.argv[1]
    args = sys.argv[2:]
    COMMANDS[cmd](*args)


if __name__ == "__main__":
    main()
