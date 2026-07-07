"""Word-level narration alignment -> <run_dir>/audio/alignment.json.

Implements the frozen CLI semantics (no-op exit 0 ONLY on runs without
collage work) plus the whisper alignment path. There is NO estimated
fallback (docs/collage/CONTRACTS.md §3, §4): if the whisper CLI is
unavailable, a wav is missing, or transcription fails for any collage
segment, ``align`` prints actionable errors and exits non-zero. ``source``
is always ``"whisper"``.

Output format (frozen §3):

    {"<segment_id>": {"duration_seconds": float,
                      "source": "whisper",
                      "words": [{"w": str, "start": float, "end": float}]}}
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from rich.console import Console

from ..collage.work import collage_segment_ids, print_skipped
from ..config import PipelineConfig

console = Console()


def _load_audio_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "audio" / "audio_manifest.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _whisper_available(command: str) -> bool:
    """Mirror run_qa._asr_available: leading VAR=value pairs are env vars."""
    parts = shlex.split(command)
    while parts and "=" in parts[0] and not parts[0].startswith("-"):
        parts.pop(0)
    if not parts:
        return False
    return shutil.which(parts[0]) is not None


def _parse_whisper_words(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten whisper JSON segments[].words[] into the frozen word format."""
    words: list[dict[str, Any]] = []
    for segment in data.get("segments", []) or []:
        for word in segment.get("words", []) or []:
            token = word.get("word")
            if token is None:
                continue
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            words.append({"w": str(token), "start": start, "end": end})
    return words


def _whisper_align(
    wav_path: Path,
    command: str,
    model: str,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Run whisper on ``wav_path``; return (words, error).

    Mirrors run_qa._transcribe's shelling pattern: leading VAR=value pairs
    become env vars, the remaining tokens are the executable + flags.
    """
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td)
        parts = shlex.split(command)
        env = os.environ.copy()
        while parts and "=" in parts[0] and not parts[0].startswith("-"):
            key, value = parts.pop(0).split("=", 1)
            env[key] = value
        args = parts + [
            str(wav_path),
            "--model",
            model,
            "--output_dir",
            str(out_dir),
            "--output_format",
            "json",
            "--word_timestamps",
            "True",
            "--fp16",
            "False",
        ]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=900,
                env=env,
            )
        except Exception as exc:  # noqa: BLE001
            return None, f"whisper invocation failed: {exc}"
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-500:]
            return None, f"whisper exited {proc.returncode}: {tail}"
        json_files = sorted(out_dir.glob("*.json"))
        if not json_files:
            return None, "whisper produced no JSON output"
        try:
            data = json.loads(json_files[0].read_text(errors="ignore"))
        except json.JSONDecodeError as exc:
            return None, f"whisper JSON parse failed: {exc}"
        words = _parse_whisper_words(data)
        if not words:
            return None, "whisper produced no word timestamps"
        return words, None


def _sfx_at_word_segment_ids(script_path: Path) -> list[str]:
    """Segments whose sfx cues use at_word — they need word alignment too."""
    if not script_path.exists():
        return []
    try:
        script = json.loads(script_path.read_text())
    except json.JSONDecodeError:
        return []
    ids: list[str] = []
    for seg in script.get("segments", []):
        if any(c.get("at_word") for c in seg.get("sfx", []) or []):
            seg_id = seg.get("segment_id", "")
            if seg_id:
                ids.append(seg_id)
    return ids


def run_align(script_path: Path, run_dir: Path) -> None:
    segment_ids = collage_segment_ids(script_path, run_dir)
    for seg_id in _sfx_at_word_segment_ids(script_path):
        if seg_id not in segment_ids:
            segment_ids.append(seg_id)
    if not segment_ids:
        print_skipped("no collage segments in this run")
        return

    config = PipelineConfig()
    whisper_command = os.environ.get("PTV_ALIGN_COMMAND") or config.align_command
    if not _whisper_available(whisper_command):
        console.print(
            f"[red]whisper CLI not found for alignment command: {whisper_command!r}[/red]\n"
            f"[red]Alignment is REQUIRED for collage segments ({', '.join(segment_ids)}).[/red]\n"
            f"[red]Install whisper, or set PTV_ALIGN_COMMAND / config.align_command to a "
            f"working whisper invocation (leading VAR=value pairs are treated as env vars).[/red]"
        )
        sys.exit(1)

    manifest = _load_audio_manifest(run_dir)

    alignment: dict[str, Any] = {}
    errors: list[str] = []
    for seg_id in segment_ids:
        audio_entry = manifest.get(seg_id, {})

        audio_path = audio_entry.get("audio_path")
        if not audio_path:
            errors.append(f"{seg_id}: no audio_path in audio_manifest.json (run synthesize first)")
            continue
        wav_path = Path(audio_path)
        if not wav_path.is_absolute():
            wav_path = (run_dir / wav_path).resolve()
        if not wav_path.exists():
            errors.append(f"{seg_id}: wav not found at {wav_path}")
            continue

        words, err = _whisper_align(wav_path, whisper_command, config.align_model)
        if err or not words:
            errors.append(f"{seg_id}: {err or 'whisper produced no words'}")
            continue

        duration = float(audio_entry.get("duration_seconds") or 0) or words[-1]["end"]
        alignment[seg_id] = {
            "duration_seconds": duration,
            "source": "whisper",
            "words": words,
        }

    if errors:
        console.print("[red]Alignment failed for one or more collage segments:[/red]")
        for line in errors:
            console.print(f"[red]  - {line}[/red]")
        console.print(
            "[red]No estimated fallback exists. Fix the audio/whisper setup and re-run "
            "`align` before building collage scenes.[/red]"
        )
        sys.exit(1)

    out_path = run_dir / "audio" / "alignment.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(alignment, indent=2))
    console.print(f"[green]Aligned {len(alignment)} segments -> {out_path}[/green]")
    print(json.dumps({"skipped": False, "segments": sorted(alignment)}))
