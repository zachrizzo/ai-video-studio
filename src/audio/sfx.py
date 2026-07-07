"""Procedural sound-effects library + narration mixing for the collage engine.

Sounds are synthesized deterministically with numpy/scipy (seeded per sound
name — same wav bytes every run, no downloads, works offline) and mixed under
the narration with ffmpeg. There is no sample library to install and no
network fetch: a missing/unknown sound name is a hard error listing the valid
names, per the engine's no-fallback rules.

Script schema (src/analysis/models.py):

    "sfx": [{"sound": "cannon_boom", "at_word": "cannon", "gain_db": -10}]

Timing refs reuse the collage TimeRef semantics (exactly one of
at / at_frac / at_word, optional offset) and are resolved against the same
``audio/alignment.json`` the collage engine uses — ``at_word`` therefore
REQUIRES `align` to have run first, like everywhere else.

Mixing happens in-place on ``audio/audio_<segment_id>.wav`` after
synthesize+align (word timings stay valid — narration timing is unchanged by
the mix). A sidecar ``audio/sfx_applied.json`` records which segments were
mixed so re-runs are idempotent instead of double-mixing.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SAMPLE_RATE = 48000


# ---------------------------------------------------------------------------
# Synthesis primitives
# ---------------------------------------------------------------------------

def _rng(name: str) -> np.random.Generator:
    """Deterministic per-sound RNG (stable across runs and machines)."""
    seed = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big")
    return np.random.default_rng(seed)


def _env_exp(n: int, decay: float) -> np.ndarray:
    """Exponential decay envelope over n samples; ``decay`` = seconds to -60dB."""
    t = np.arange(n) / SAMPLE_RATE
    return np.exp(-6.9078 * t / max(decay, 1e-3))


def _lowpass(x: np.ndarray, hz: float, order: int = 2) -> np.ndarray:
    from scipy.signal import butter, lfilter

    b, a = butter(order, hz / (SAMPLE_RATE / 2), btype="low")
    return lfilter(b, a, x)


def _highpass(x: np.ndarray, hz: float, order: int = 2) -> np.ndarray:
    from scipy.signal import butter, lfilter

    b, a = butter(order, hz / (SAMPLE_RATE / 2), btype="high")
    return lfilter(b, a, x)


def _echo(x: np.ndarray, delays_s: list[float], gains: list[float]) -> np.ndarray:
    out = np.copy(x)
    for d, g in zip(delays_s, gains, strict=True):
        k = int(d * SAMPLE_RATE)
        if k < len(x):
            out[k:] += g * x[: len(x) - k]
    return out


def _normalize(x: np.ndarray, peak: float = 0.89) -> np.ndarray:
    m = float(np.max(np.abs(x)) or 1.0)
    return (x / m * peak).astype(np.float64)


# ---------------------------------------------------------------------------
# The sound library
# ---------------------------------------------------------------------------

def _cannon_boom() -> np.ndarray:
    """A single heavy siege-cannon shot with a rolling echo tail."""
    rng = _rng("cannon_boom")
    n = int(3.2 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    # Sub-bass thump sweeping 70->28 Hz.
    freq = 70 * np.exp(-1.8 * t) + 28
    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
    thump = np.sin(phase) * _env_exp(n, 0.9)
    # Blast body: lowpassed noise burst.
    body = _lowpass(rng.standard_normal(n), 240) * _env_exp(n, 0.45) * 1.6
    # Muzzle crack: first 8 ms of bright noise.
    crack = _highpass(rng.standard_normal(n), 1200) * _env_exp(n, 0.02) * 0.8
    x = thump * 1.1 + body + crack
    x = _echo(x, [0.35, 0.82, 1.45], [0.38, 0.22, 0.12])
    return _normalize(x)


def _cannon_distant() -> np.ndarray:
    """Far-off bombardment rumble — softer, duller, longer."""
    rng = _rng("cannon_distant")
    n = int(4.0 * SAMPLE_RATE)
    body = _lowpass(rng.standard_normal(n), 120) * _env_exp(n, 1.2)
    x = _echo(body, [0.5, 1.1, 2.0], [0.5, 0.3, 0.18])
    return _normalize(x, peak=0.6)


def _musket_volley() -> np.ndarray:
    """A ragged volley of early gunfire — staggered sharp cracks."""
    rng = _rng("musket_volley")
    n = int(2.6 * SAMPLE_RATE)
    x = np.zeros(n)
    shots = 7
    times = np.sort(rng.uniform(0.0, 1.1, shots))
    for st in times:
        k = int(st * SAMPLE_RATE)
        m = int(0.28 * SAMPLE_RATE)
        if k + m > n:
            m = n - k
        crack = _highpass(rng.standard_normal(m), 900) * _env_exp(m, 0.05)
        thud = _lowpass(rng.standard_normal(m), 300) * _env_exp(m, 0.10) * 0.7
        x[k : k + m] += (crack + thud) * rng.uniform(0.6, 1.0)
    x = _echo(x, [0.23, 0.55], [0.3, 0.15])
    return _normalize(x)


def _war_drums() -> np.ndarray:
    """Slow martial drum pattern, ~4 s loopable."""
    rng = _rng("war_drums")
    n = int(4.2 * SAMPLE_RATE)
    x = np.zeros(n)
    beat_times = [0.0, 0.55, 1.1, 1.38, 1.65, 2.2, 2.75, 3.03, 3.3, 3.85]
    for i, bt in enumerate(beat_times):
        k = int(bt * SAMPLE_RATE)
        m = int(0.5 * SAMPLE_RATE)
        if k + m > n:
            m = n - k
        tt = np.arange(m) / SAMPLE_RATE
        f = 92 * np.exp(-9 * tt) + 55
        ph = np.cumsum(2 * np.pi * f / SAMPLE_RATE)
        hit = np.sin(ph) * _env_exp(m, 0.28)
        skin = _lowpass(rng.standard_normal(m), 700) * _env_exp(m, 0.05) * 0.35
        accent = 1.0 if i % 5 == 0 else 0.72
        x[k : k + m] += (hit + skin) * accent
    return _normalize(x, peak=0.8)


def _ocean_waves() -> np.ndarray:
    """Rolling sea swell, ~8 s loopable."""
    rng = _rng("ocean_waves")
    n = int(8.0 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    base = _lowpass(rng.standard_normal(n), 420)
    swell = 0.55 + 0.45 * np.sin(2 * np.pi * 0.16 * t + 1.1) * np.sin(2 * np.pi * 0.07 * t)
    hiss = _highpass(rng.standard_normal(n), 1500) * 0.10 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.16 * t))
    return _normalize(base * swell + hiss, peak=0.55)


def _fire_crackle() -> np.ndarray:
    """Burning city — low roar plus sparse pops, ~6 s loopable."""
    rng = _rng("fire_crackle")
    n = int(6.0 * SAMPLE_RATE)
    roar = _lowpass(rng.standard_normal(n), 300) * 0.5
    pops = np.zeros(n)
    for _ in range(90):
        k = int(rng.uniform(0, n - 400))
        m = int(rng.uniform(60, 380))
        pops[k : k + m] += _highpass(rng.standard_normal(m), 2500) * _env_exp(m, 0.004) * rng.uniform(0.2, 0.9)
    return _normalize(roar + pops, peak=0.6)


def _wind_howl() -> np.ndarray:
    """Low mournful wind, ~8 s loopable."""
    rng = _rng("wind_howl")
    n = int(8.0 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    noise = rng.standard_normal(n)
    sweep = 350 + 220 * np.sin(2 * np.pi * 0.09 * t)
    # Piecewise lowpass approximated by mixing two filtered copies.
    lo = _lowpass(noise, 300)
    hi = _lowpass(noise, 800)
    mix = lo * (1 - (sweep - 300) / 500) + hi * ((sweep - 300) / 500)
    amp = 0.6 + 0.4 * np.sin(2 * np.pi * 0.05 * t + 0.7)
    return _normalize(mix * amp, peak=0.5)


def _bell_toll() -> np.ndarray:
    """A single heavy church bell strike with long decay."""
    rng = _rng("bell_toll")
    n = int(5.0 * SAMPLE_RATE)
    t = np.arange(n) / SAMPLE_RATE
    x = np.zeros(n)
    # Inharmonic partials characteristic of large bells.
    for f, g, d in [(196, 1.0, 3.5), (392, 0.55, 2.6), (523, 0.4, 2.0), (660, 0.28, 1.5), (988, 0.18, 1.0)]:
        x += g * np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)) * _env_exp(n, d)
    strike = _highpass(rng.standard_normal(n), 800) * _env_exp(n, 0.015) * 0.5
    return _normalize(x + strike, peak=0.7)


SOUNDS: dict[str, callable] = {
    "cannon_boom": _cannon_boom,
    "cannon_distant": _cannon_distant,
    "musket_volley": _musket_volley,
    "war_drums": _war_drums,
    "ocean_waves": _ocean_waves,
    "fire_crackle": _fire_crackle,
    "wind_howl": _wind_howl,
    "bell_toll": _bell_toll,
}


def write_sound(name: str, out_path: Path) -> Path:
    """Synthesize ``name`` to a 48k mono 16-bit wav at out_path (cached)."""
    if name not in SOUNDS:
        raise ValueError(
            f"unknown sfx sound {name!r}; valid sounds: {', '.join(sorted(SOUNDS))}"
        )
    out_path = Path(out_path)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import wave

    x = SOUNDS[name]()
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return out_path


# ---------------------------------------------------------------------------
# Mixing
# ---------------------------------------------------------------------------

def mix_sfx_into_narration(
    narration_wav: Path,
    events: list[tuple[Path, float, float]],
    out_wav: Path,
) -> None:
    """Mix (sfx_wav, start_seconds, gain_db) events under a narration track.

    Keeps the narration's exact duration (word alignment stays valid) and
    limits the sum to avoid clipping. Raises on ffmpeg failure.
    """
    if not events:
        raise ValueError("mix_sfx_into_narration called with no events")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(narration_wav)]
    for wav, _, _ in events:
        cmd += ["-i", str(wav)]
    parts = []
    labels = []
    for i, (_, start, gain_db) in enumerate(events, start=1):
        ms = max(0, int(round(start * 1000)))
        parts.append(f"[{i}]adelay={ms}|{ms},volume={gain_db}dB[s{i}]")
        labels.append(f"[s{i}]")
    fc = (
        ";".join(parts)
        + f";[0]{''.join(labels)}amix=inputs={len(events) + 1}:duration=first:normalize=0,"
        + "alimiter=limit=0.97[out]"
    )
    cmd += ["-filter_complex", fc, "-map", "[out]", "-ar", str(SAMPLE_RATE), "-ac", "1", str(out_wav)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not Path(out_wav).exists():
        raise RuntimeError(f"sfx mix failed: {proc.stderr[-400:]}")


# ---------------------------------------------------------------------------
# Pipeline command
# ---------------------------------------------------------------------------

def run_sfx(script_path: Path, run_dir: Path) -> None:
    """Mix each segment's ``sfx`` cues into its narration wav (idempotent).

    Exits 0 printing {"skipped": true} when no segment declares sfx, so the
    Studio producer can include this step unconditionally (CONTRACTS §4).
    Any real failure (unknown sound, unresolvable at_word, ffmpeg error)
    exits non-zero with an actionable message — no silent fallback.
    """
    from ..analysis.script_writer import load_script
    from ..collage.timing import resolve_time

    run_dir = Path(run_dir)
    script = load_script(Path(script_path))
    with_sfx = [s for s in script.segments if s.sfx]
    if not with_sfx:
        print(json.dumps({"skipped": True, "reason": "no segments declare sfx"}))
        return

    audio_dir = run_dir / "audio"
    manifest_path = audio_dir / "audio_manifest.json"
    if not manifest_path.exists():
        print(json.dumps({"skipped": False, "errors": {"_": "audio/audio_manifest.json missing — run synthesize first"}}))
        sys.exit(2)
    manifest = json.loads(manifest_path.read_text())

    alignment_path = audio_dir / "alignment.json"
    alignment = json.loads(alignment_path.read_text()) if alignment_path.exists() else {}

    applied_path = audio_dir / "sfx_applied.json"
    applied: dict = json.loads(applied_path.read_text()) if applied_path.exists() else {}

    sfx_dir = run_dir / "sfx"
    mixed: list[str] = []
    skipped_done: list[str] = []
    errors: dict[str, str] = {}

    tmp = audio_dir / "_sfx_tmp.wav"
    for seg in with_sfx:
        sid = seg.segment_id
        if applied.get(sid):
            skipped_done.append(sid)
            continue
        entry = manifest.get(sid)
        if not entry:
            errors[sid] = "no audio manifest entry — run synthesize first"
            continue
        narration = Path(entry["audio_path"])
        if not narration.exists():
            errors[sid] = f"narration wav missing: {narration}"
            continue
        duration = float(entry.get("duration_seconds") or 0)
        words = (alignment.get(sid) or {}).get("words")
        try:
            events: list[tuple[Path, float, float]] = []
            for cue in seg.sfx:
                wav = write_sound(cue.sound, sfx_dir / f"{cue.sound}.wav")
                start = resolve_time(
                    cue,
                    narration_text=seg.narration_text,
                    duration_seconds=duration,
                    words=words,
                )
                events.append((wav, start, cue.gain_db))
            mix_sfx_into_narration(narration, events, tmp)
            tmp.replace(narration)
            applied[sid] = [
                {"sound": c.sound, "gain_db": c.gain_db} for c in seg.sfx
            ]
            mixed.append(sid)
        except (ValueError, RuntimeError) as exc:
            errors[sid] = str(exc)
        finally:
            tmp.unlink(missing_ok=True)

    applied_path.write_text(json.dumps(applied, indent=2))
    print(json.dumps({
        "skipped": False,
        "mixed": mixed,
        "already_applied": skipped_done,
        "errors": errors,
        "sounds_available": sorted(SOUNDS),
    }))
    if errors:
        sys.exit(2)
