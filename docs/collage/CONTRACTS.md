# Collage Engine — Frozen Contracts

These interfaces are frozen after Phase 0. Workstreams extend implementations
but must not change signatures, formats, or semantics written here.

## 1. Deterministic scene contract (`window.seek`)

A frame-renderable HTML scene (all collage scenes; opt-in for hand-written
html scenes) must define, before `sceneReady` resolves:

```js
window.__SCENE__ = { duration: <seconds>, fps: <24|30|60> }
window.seek = (t) => { /* set ALL visual state for time t, synchronously */ }
window.sceneReady  // Promise; resolves when fonts+assets are loaded
```

Determinism rules (enforced by the validator's collage branch):

- Every visual property is a pure function of `t`. No animation may depend on
  wall-clock time: `Date.now(`, `performance.now(`, and unseeded
  `Math.random(` are banned strings in built collage HTML.
- Randomness comes from a seeded PRNG (mulberry32) keyed by spec-level seeds.
- `seek(t)` must be correct for arbitrary `t`, but MAY keep a monotonic
  fast path (renderer always steps forward: `t = frame / fps`).
- `sceneReady` must resolve only after `document.fonts.ready` AND
  `document.fonts.check(...)` passes for every bundled family
  (`fonts.ready` resolves even when a face failed to load), AND every
  `<img>` has decoded.
- No network: ALL `http(s)://` references are banned in collage HTML
  (no Google Fonts exception — fonts are base64-embedded). `data:` URIs and
  relative paths are allowed.
- Masks are CSS/SVG only. Never read pixels back from canvases containing
  `file://`-loaded images (tainted canvas throws).

The frame renderer (src/animation/frame_renderer.py) is the ONLY html/collage
render path — there is no real-time recorder fallback. A scene that does not
define `window.seek` fails with a clear error telling the author to implement
the seek contract. The frame renderer:

- waits for `sceneReady` at most 10 s, then steps `seek(frame / fps)` →
  screenshot → `ffmpeg -f image2pipe` → H.264 mp4 with EXACTLY
  `round(duration * fps)` frames.
- timeout scales with work: `max(config.render_timeout_seconds,
  frames * 0.5 + 60)` seconds.
- reports TRUE `actual_duration_seconds = frames / fps` in RenderResult.

## 2. File layout inside a run dir

```
<run_dir>/scenes/{segment_id}.collage.json      agent-authored CollageSpec
<run_dir>/assets/{segment_id}/{asset_id}.png    generated/manual assets (RGBA for cutouts)
<run_dir>/audio/audio_manifest.json             {seg: {audio_path, duration_seconds, qa_issues}}
<run_dir>/audio/alignment.json                  see §3
<run_dir>/scenes/{segment_id}_render/           created by fixer; built HTML +
                                                {segment_id}_collage.mp4 land here
```

Asset references inside built HTML are **relative to
`scenes/{segment_id}_render/`**, i.e. `../../assets/{segment_id}/{asset_id}.png`.
Fonts are base64-embedded (no file references).

## 3. `audio/alignment.json`

Keyed identically to `audio_manifest.json`:

```json
{
  "<segment_id>": {
    "duration_seconds": 12.84,
    "source": "whisper",
    "words": [ {"w": "Clouds", "start": 3.12, "end": 3.55}, ... ]
  }
}
```

- Written by `python -m src.pipeline align <script.json> <run_dir>`.
- There is NO estimated fallback. If the whisper CLI is unavailable, a wav is
  missing, or transcription fails, `align` prints an actionable error and
  exits non-zero. `source` is always `"whisper"`.
- Staleness: if `alignment.json` is older than a segment's wav, the builder
  fails that segment with an error telling the operator to re-run `align`
  (fix-loops must re-align regenerated audio).
- TimeRef consequence: a spec using `at_word` requires alignment for that
  segment; the builder raises a clear error when it is missing.

## 4. CLI exit semantics

`align`, `assets`, and `collage` commands **exit 0 and print
`{"skipped": true, ...}`** ONLY when the run has no collage work (no
`visual_engine: "collage"` segments and no `scenes/*.collage.json`) — this
scoping keeps legacy runs working through the Studio producer, which raises
on any non-zero exit. When there IS collage work and any part of it fails
(whisper missing, asset generation error, cutout rejected, spec invalid,
render failure), the command prints the errors and **exits non-zero** — no
silent degradation. Discovery helper:
`src.collage.work.collage_segment_ids(script_path, run_dir, only="")`.

Command argument shapes (registered in `src/pipeline.py` COMMANDS):

```
align   <script.json> <run_dir>
assets  <script.json> <run_dir> [segment_ids]     # comma-separated filter
collage <script.json> <run_dir> [segment_ids]
```

## 5. Cross-workstream function signatures

```python
# src/collage/timing.py  (alignment workstream owns internals)
resolve_time(ref: TimeRef, *, narration_text: str, duration_seconds: float,
             words: list[dict] | None = None) -> float
normalize_word(token: str) -> str

# src/collage/builder.py  (collage workstream)
build_collage_html(spec: CollageSpec, run_dir: Path,
                   narration_text: str, duration_seconds: float,
                   words: list[dict] | None) -> str   # complete HTML document

# src/studio/style_packs.py  (assets workstream owns internals)
load_style_pack(name: str, style_packs_dir: Path | None = None) -> StylePack
    # StylePack: name, dir, tokens (dict with "palette"), flux_prefix,
    # flux_suffix, fonts (list[Path] of woff2), css (str)
list_style_packs(style_packs_dir: Path | None = None) -> list[dict]
    # [{"id", "name", "description", "palette"}]

# src/assets/generate.py
asset_path(run_dir: Path, segment_id: str, asset_id: str) -> Path

# src/utils/locks.py
generation_lock()   # contextmanager; serializes FLUX/LTX/rembg
```

TimeRef resolution order (builder): load spec → override
`spec.duration_seconds` with the audio manifest's real duration → resolve all
TimeRefs → emit HTML. Color tokens `$palette.<name>` resolve against the style
pack; unknown tokens are builder errors listing valid names.

No-fallback rules (builder + timing):
- `resolve_time` raises ValueError when an `at_word` ref cannot be resolved
  (no alignment words, or the word/occurrence is absent) — the error names the
  segment, the word, and the fix (re-run align / correct the spec).
- A spec using `$palette.*` tokens requires a resolvable style pack
  (spec.style_pack or the script-level style_pack); none → builder error.
- Font families named in the pack's `type` tokens must exist as woff2 files in
  the pack's `fonts/` dir; missing families are builder errors (determinism
  requires bundled fonts — no system-stack substitution).
- Cutout extraction has no soft-mask fallback: rembg failure or an
  out-of-range alpha fraction is a hard per-asset error.

## 6. Preset fields (UI ⇄ server ⇄ agent)

Presets gain two optional fields, exactly these names, end to end
(PresetBar/ChatPanel/ws.ts payload → agent preset injection):

```
style_pack: string | null              # e.g. "anthropic_docu"
default_visual_engine: string | null   # "collage" | "html" | "manim"
```

## 7. QA additions

- Scene duration drift: `|actual - target| > config.qa_scene_duration_epsilon`
  on frame-rendered scenes = error.
- Blank frames: only STRICTLY-uniform frames count (luma stddev <
  `config.qa_min_luma_stddev`); error only after
  `config.qa_max_blank_seconds` consecutive seconds. Near-uniform paper
  (`#F0EEE6`) cold-opens must pass.
- `freezedetect` warnings on deliberate calm shots (≥2.5 s holds) are expected
  and stay warnings, never errors.
