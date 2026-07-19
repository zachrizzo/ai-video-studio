"""Bounded production steps for an episode run. The DRIVING AGENT loops these —
each invocation stays well under a ~10-minute shell timeout. Do NOT wrap the
whole render in one long call or a yielded background job: those are the two
failure modes that killed real runs (timeout kill; orphaned background render).

Usage (cd <repo>; PYTHONPATH=$PWD):
  python produce.py plan   <run_dir> [batch_size]   # print segment batches as JSON
  python produce.py clean  <run_dir>                # clear stale renders + final.mp4
  python produce.py render <run_dir> <sid1,sid2,..> # render ONE batch (foreground)
  python produce.py finish <run_dir>                # verify all renders, composite, ffprobe

Typical loop: plan -> clean -> render (per batch, sequentially) -> finish.
Scenes containing `video` elements render slower (per-frame decode awaits) —
keep batches at ~3 (2 if several video beats).
"""
import json, shutil, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve()
while not (REPO / "src" / "pipeline.py").exists():
    if REPO.parent == REPO:
        sys.exit("could not locate repo root (src/pipeline.py)")
    REPO = REPO.parent


def order_of(run_dir: Path):
    return [s["segment_id"] for s in json.loads((run_dir / "script.json").read_text())["segments"]]


def pipeline(args):
    r = subprocess.run([sys.executable, "-m", "src.pipeline", *args],
                       cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-800:]); print(r.stderr[-800:])
        sys.exit(f"FAILED: {' '.join(args[:2])}")
    return r.stdout


def main():
    cmd, run_dir = sys.argv[1], Path(sys.argv[2])
    if cmd == "plan":
        batch = int(sys.argv[3]) if len(sys.argv) > 3 else 3
        order = order_of(run_dir)
        print(json.dumps([order[i:i + batch] for i in range(0, len(order), batch)]))
    elif cmd == "clean":
        for stale in run_dir.glob("scenes/*_render"):
            shutil.rmtree(stale)
        (run_dir / "final.mp4").unlink(missing_ok=True)
        print("cleared stale renders + final.mp4")
    elif cmd == "render":
        ids = sys.argv[3]
        pipeline(["collage", str(run_dir / "script.json"), str(run_dir), ids])
        done = [s for s in ids.split(",")
                if (run_dir / "scenes" / f"{s}_render" / f"{s}_collage.mp4").exists()]
        print(f"batch done: {done}")
        if len(done) != len(ids.split(",")):
            sys.exit(f"batch incomplete: wanted {ids}")
    elif cmd == "finish":
        order = order_of(run_dir)
        video_paths, audio_paths, missing = [], [], []
        for sid in order:
            v = run_dir / "scenes" / f"{sid}_render" / f"{sid}_collage.mp4"
            a = run_dir / "audio" / f"audio_{sid}.wav"
            (missing.append(str(v)) if not v.exists() else None)
            (missing.append(str(a)) if not a.exists() else None)
            video_paths.append(str(v)); audio_paths.append(str(a))
        if missing:
            sys.exit("MISSING ARTIFACTS:\n  " + "\n  ".join(missing))
        mpath = run_dir / "composite_manifest.json"
        mpath.write_text(json.dumps({"video_paths": video_paths, "audio_paths": audio_paths}, indent=2))
        pipeline(["composite", str(mpath), str(run_dir / "final.mp4"), "--speed", "1.0"])
        dur = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1",
                              str(run_dir / "final.mp4")], capture_output=True, text=True).stdout.strip()
        print(f"DONE -> {run_dir/'final.mp4'} ({dur}s, {len(order)} segments)")
    else:
        sys.exit(f"unknown command {cmd!r} (plan|clean|render|finish)")


if __name__ == "__main__":
    main()
