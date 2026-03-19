"""Pipeline toolkit — individual commands that Claude Code orchestrates.

Each function is a standalone step that can be called via:
    uv run python -m src.pipeline <command> [args]

Commands:
    parse <pdf_path> <output_dir>         Parse a PDF and save structured content
    synthesize <script.json> <output_dir>  Synthesize voice for all segments
    silence <script.json> <output_dir>     Generate silent audio (estimated durations)
    render <scene_spec.json> <work_dir>    Validate and render a single scene
    fallback <segment_id> <title> <desc> <duration> <work_dir>  Generate fallback visual
    composite <manifest.json> <output.mp4> Composite final video (audio optional)
    setup <base_dir>                       Create working directories
"""

import json
import sys
from pathlib import Path

from rich.console import Console

console = Console()


def cmd_parse(pdf_path: str, output_dir: str):
    """Parse a PDF and save structured content as JSON."""
    from .ingestion.parser import parse_paper

    pdf = Path(pdf_path)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    paper = parse_paper(pdf, output_dir=out / "figures")

    # Save as JSON
    paper_json = out / "paper.json"
    paper_json.write_text(paper.model_dump_json(indent=2))

    console.print(f"[green]Parsed: {paper.title}[/green]")
    console.print(f"  Sections: {len(paper.sections)}")
    console.print(f"  Saved to: {paper_json}")

    # Also save raw markdown for Claude to read
    markdown_path = out / "paper.md"
    markdown_path.write_text(paper.raw_text)
    console.print(f"  Markdown: {markdown_path}")


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
        speed=config.voice_speed,
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


def cmd_setup(base_dir: str):
    """Create working directories for a pipeline run."""
    from .utils.file_manager import FileManager

    fm = FileManager(Path(base_dir))
    run_dir = fm.setup()

    print(json.dumps({
        "run_dir": str(run_dir),
        "run_id": fm.run_id,
        "audio_dir": str(fm.audio_dir),
        "video_dir": str(fm.video_dir),
        "scenes_dir": str(fm.scenes_dir),
    }))


COMMANDS = {
    "parse": cmd_parse,
    "synthesize": cmd_synthesize,
    "silence": cmd_silence,
    "render": cmd_render,
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
