"""AI-News scenes v5: adds NATIVE VIDEO beats (recorded slow-scroll clips of the
primary sources) + a de-zoom fix on the still punch-ins.

Changes vs v4:
- FRAMINGS punch-ins de-zoomed (widths ~1.08-1.10, gentle scale, biased to the
  headline band) so crops land on the hero/headline, not empty page. Wide beats
  ~0.9-0.96. Charts stay full-bleed ~0.86, never punched-in.
- 7 stories with a recorded clip get ONE mid-segment beat swapped from a still to
  a `video` element: full-bleed width ~0.9, no punch-in, rate = clipdur/window so
  the recorded scroll fills the beat. Persistent lower-third + chip + Vox finish.

Rewrites the 15 story scenes. Intro/outro kept."""
import json, subprocess
from pathlib import Path

RUN = Path("/Users/zachrizzo/.video-studio/runs/run_1eb772e9")
SHARED = RUN / "assets" / "shared"
FINISH = dict(stutter_fps=12, lens=True,
              transition_in={"seconds": 0.45, "blur_px": 12, "push": 0.05},
              transition_out={"seconds": 0.45, "blur_px": 12, "push": 0.05})

def frac(f): return {"at_frac": round(f, 4)}
def write(seg, spec): (RUN/"scenes"/f"{seg}.collage.json").write_text(
    json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

# De-zoomed still framings: (w, y, s0, s1, x_pan). Punch-ins gentle + headline-biased.
FRAMINGS = [
    (0.94, 0.40, 1.00, 1.05,  0.012),   # wide, slow zoom-in
    (1.10, 0.40, 1.00, 1.06, -0.015),   # GENTLE punch-in on the headline band
    (0.92, 0.42, 1.05, 1.00, -0.012),   # wide, zoom-out
    (1.08, 0.43, 1.06, 1.00,  0.015),   # GENTLE punch-in on the headline band
    (0.96, 0.40, 1.02, 1.08,  0.015),   # wide, zoom-in
]
CHART_W, CHART_Y, CHART_S0, CHART_S1 = 0.86, 0.40, 1.00, 1.02
VID_W, VID_Y = 0.90, 0.40

def is_chart(n): return n.startswith("chart_")
def is_clip(n): return n.startswith("clip")

def clip_dur(name):
    p = SHARED/f"{name}.webm"
    out = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",str(p)],capture_output=True,text=True).stdout.strip()
    return float(out)

# (seg_id, display_num, lt_file_num, [ordered visual base-names]) — clipNN = video beat
STORIES = [
 ("s01_gemini",  1, 1, ["shot01", "shot01b", "chart_gemini", "shot01c", "shot01"]),
 ("s01b_kimi",   2, 15, ["shot15", "clip01", "chart_kimi", "shot15d", "shot15e"]),
 ("s02_codex",   3, 2, ["shot02d", "clip02", "shot02", "shot02c", "shot02d"]),
 ("s03_bonsai",  4, 3, ["shot03", "chart_bonsai", "clip03", "chart_bonsai_size", "shot03e", "shot03b"]),
 ("s04_chips",   5, 4, ["shot04d", "clip04", "shot04b", "shot04c", "shot04d"]),
 ("s05_hyundai", 6, 5, ["shot05", "shot05d", "shot05b", "shot05c", "shot05"]),
 ("s06_tiktok",  7, 6, ["shot06", "shot06b", "shot06c", "shot06", "shot06b"]),
 ("s07_race",    8, 7, ["shot07", "shot07d", "shot07b", "shot07c", "shot07"]),
 ("s08_voice",   9, 8, ["shot08e", "shot08", "chart_voice", "shot08b", "shot08c"]),
 ("s09_muse",   10, 9, ["shot09", "shot09b", "shot09c", "shot09", "shot09b"]),
 ("s10_meta",   11, 10, ["shot10", "chart_meta", "shot10b", "shot10c", "shot10"]),
 ("s11_ant",    12, 11, ["shot11", "chart_ant", "shot11c", "chart_ant", "shot11"]),
 ("s12_antidoom", 13, 12, ["shot12", "chart_antidoom", "clip12", "shot12d", "shot12b"]),
 ("s13_hemispheric", 14, 13, ["shot13d", "clip13", "shot13b", "shot13c", "shot13d"]),
 ("s14_nobel",  15, 14, ["shot14d", "clip14", "shot14b", "shot14d", "shot14"]),
]

def exists(name):
    return (SHARED/f"{name}.webm").exists() if is_clip(name) else (SHARED/f"{name}.png").exists()

def story_scene(seg, display, lt_num, seq, dur):
    seq = [a for a in seq if exists(a)]
    if not seq:
        raise SystemExit(f"{seg}: no visuals exist!")
    lt = f"lt{lt_num:02d}"
    K = len(seq)
    used = list(dict.fromkeys(seq + [lt]))
    assets = []
    for a in used:
        ext = "webm" if is_clip(a) else "png"
        assets.append({"id": a, "role": "subject", "src": f"assets/shared/{a}.{ext}"})
    els = [{"id": "dust", "type": "particles", "style": "dust", "count": 26,
            "color": "$palette.muted", "depth": 0.7}]
    photo_i = 0
    for i, a in enumerate(seq):
        w0, w1 = i/K, (i+1)/K
        base = {"id": f"b{i}", "asset_id": a, "x": 0.5, "depth": 0.0, "z": 2+i,
                "enter": frac(max(0.0, w0-0.02))}
        if i < K-1:
            base["exit"] = frac(min(1.0, w1+0.01))
        if is_clip(a):
            window = max(0.5, (w1-w0)*dur)
            rate = round(clip_dur(a)/window, 3)
            base.update({"type": "video", "y": VID_Y, "width": VID_W,
                         "start": frac(w0), "clip_start": 0.0, "rate": rate})
        elif is_chart(a):
            base.update({"type": "layer", "y": CHART_Y, "width": CHART_W,
                         "move": [{"time": frac(w0), "scale": CHART_S0},
                                  {"time": frac(min(1.0, w1)), "scale": CHART_S1}]})
        else:
            fw, fy, s0, s1, pan = FRAMINGS[photo_i % len(FRAMINGS)]; photo_i += 1
            base.update({"type": "layer", "y": fy, "width": fw,
                         "move": [{"time": frac(w0), "x": 0.5, "scale": s0},
                                  {"time": frac(min(1.0, w1)), "x": 0.5+pan, "scale": s1}]})
        els.append(base)
    els += [
        {"id": "num", "type": "label", "text": f"{display:02d} / 15", "style": "mono",
         "x": 0.06, "y": 0.08, "color": "$palette.accent", "enter": frac(0.02)},
        {"id": "lt", "type": "layer", "asset_id": lt, "x": 0.5, "y": 0.86,
         "width": 0.94, "depth": 0.0, "z": 20, "enter": frac(0.1)},
    ]
    write(seg, {"spec_version": 1, "segment_id": seg, "duration_seconds": float(dur),
        "fps": 30, "style_pack": "anthropic_docu", "background": "$palette.paper",
        "camera": [{"time": frac(0.0), "x": 0.5, "y": 0.5, "scale": 1.0},
                   {"time": frac(1.0), "x": 0.5, "y": 0.5, "scale": 1.0}],
        "assets": assets, "elements": els, **FINISH})

def patch_intro_count():
    p = RUN/"scenes"/"s00_intro.collage.json"
    txt = p.read_text()
    if "14 stories - fact-checked" in txt:
        p.write_text(txt.replace("14 stories - fact-checked", "15 stories - fact-checked"))
        print("patched intro count label -> 15")

manifest = json.loads((RUN/"audio"/"audio_manifest.json").read_text())
script = {s["segment_id"]: s for s in json.loads((RUN/"script.json").read_text())["segments"]}
for seg, display, lt_num, seq in STORIES:
    dur = manifest.get(seg, {}).get("duration_seconds") or script[seg]["estimated_duration_seconds"]
    story_scene(seg, display, lt_num, seq, dur)
patch_intro_count()
print("rewrote", len(STORIES), "story scenes (v5: video beats + de-zoom)")
for seg, display, lt_num, seq in STORIES:
    clips = [a for a in seq if is_clip(a) and exists(a)]
    if clips:
        print(f"  {seg}: video beat={clips[0]} ({clip_dur(clips[0]):.1f}s)")
