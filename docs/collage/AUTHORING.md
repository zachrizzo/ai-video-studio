# Authoring collage scenes

A **collage scene** is a declarative JSON file at
`scenes/{segment_id}.collage.json` inside a run directory. The builder compiles
it (plus a self-contained JS runtime) into one HTML file that renders
deterministically via the `window.seek(t)` contract. You never write HTML or
JS — you describe layers, labels, particles, camera moves and timing, and the
engine produces the Anthropic-documentary look: ivory paper, ink, terracotta
accent, slow eased motion, torn-paper labels, film grain and vignette.

The schema is frozen in `src/collage/spec.py` (`CollageSpec`). Unknown fields
fail loudly with a JSON path, so typos are caught immediately.

## Coordinate system

- Coordinates are **normalized 0–1** with the origin at the **top-left** of the
  16:9 frame. `x: 0.5, y: 0.5` is dead center.
- `width` on a layer is **normalized to the frame width**; height keeps the
  image's aspect ratio.
- `depth` is `0` (locked to the camera — no parallax) … `1` (full parallax).
  Background plates want a low depth (~0.1); foreground cutouts want a high one
  (~0.9). Parallax is `screen_offset = camera_offset * depth`.

## Timing (`TimeRef`)

Every time is a **TimeRef**: exactly one of

| field | meaning | when to use |
| --- | --- | --- |
| `at_word` | a narration word (resolved via `alignment.json`) | **preferred** — cues stay locked to the voiceover |
| `at_frac` | fraction `0–1` of the segment's real audio duration | good default when there is no obvious word |
| `at` | absolute seconds | **last resort** — breaks when narration length changes |

Optional `offset` (seconds, may be negative) is added after resolution;
`occurrence` (default 1) picks the *n*th occurrence of `at_word`.

**Alignment is required for `at_word`.** Before building any spec that uses
`at_word`, run:

```
uv run python -m src.pipeline align <script.json> <run_dir>
```

There is **no estimated fallback**. If alignment is missing, stale (older than
the segment's audio), or the word is absent, the build fails with an actionable
error — re-run `align` or fix the word. The real audio duration from
`audio/audio_manifest.json` overrides `duration_seconds` before any TimeRef is
resolved, so `at_frac` and the clamp are always against the true length.

## Camera

`camera` is a list of keyframes `{time, x, y, scale}` (camera center + zoom,
normalized). Times are interpolated with cubic in-out easing; before the first
and after the last keyframe the value is clamped. Keep moves slow — a push from
`scale 1.0` to `1.12` over the whole segment reads as calm and intentional.

## Film finish (the Vox look)

Three optional scene-level knobs push the render toward the Vox mixed-media
explainer aesthetic. All three stay pure functions of `t` (seek-contract safe),
and all are declared at the top level of the spec, not on elements.

- `stutter_fps` (`12` | `15` | `24` | omit) — **motion on twos.** The file still
  frame-renders at `fps`, but the time fed to every renderer snaps to
  `floor(t*stutter_fps)/stutter_fps`, so the whole composite plays on a coarse,
  hand-animated cadence. `12` is the classic Vox stutter. Omit it for a
  deliberately smooth, calm scene.
- `lens` (`true` | `false`, default `false`) — adds a **periphery lens
  character**: a subtle edge blur plus a faint red/cyan chromatic fringe masked
  to the frame border (the centre stays crisp). Constant in `t`.
- `transition_in` / `transition_out` — a **blur-masked camera push** at the
  scene's boundary. Each is `{seconds, blur_px, push}` (defaults `0.5`, `14`,
  `0.06`). `transition_in` peaks at `t=0` and clears over `seconds`;
  `transition_out` is clear until `duration-seconds` then peaks at the end.
  Scenes are hard-cut concatenated, so there is no true cross-dissolve — instead
  give scene A a `transition_out` and scene B a **matching** `transition_in`, and
  the cut lands between two blurred, pushed frames so it reads as a tracking
  transition. The blur masking the cut is what sells it.

```json
// A scene that stutters on twos, carries lens character, and blur-pushes out of
// its tail so the next scene can blur-push in and hide the cut:
{
  "spec_version": 1,
  "segment_id": "seg03",
  "duration_seconds": 12.0,
  "stutter_fps": 12,
  "lens": true,
  "transition_in": { "seconds": 0.5, "blur_px": 14, "push": 0.06 },
  "transition_out": { "seconds": 0.5, "blur_px": 14, "push": 0.06 },
  "elements": [ "..." ]
}
```

## Style tokens

Colours may be raw CSS (`"#D97757"`, `"rgba(0,0,0,0.4)"`) or a **palette token**
`"$palette.<name>"` resolved against the scene's style pack. The Anthropic pack
exposes `paper`, `ink`, `accent`, `muted`. An unknown token is a build error
listing the valid names.

Set the pack with `style_pack` on the spec (or inherit the script-level
`style_pack`). A spec that uses any `$palette.*` token — including the default
`background: "$palette.paper"` — **requires a resolvable pack**. Text elements
also require the pack's bundled fonts (serif/sans/mono); there is no
system-font substitution.

## Assets

Declare images under `assets`. Each asset has an `id` and exactly one of:

- `generate`: a local image request — `{prompt, width, height, cutout, seed}`.
  Width and height must be multiples of 16. Set `cutout: true` for subjects that
  need a transparent background (rembg) — for those, prompt a bold flat
  silhouette on a plain cream/white background so the matte is clean. Seeds
  default to a deterministic hash so rebuilds reproduce identical imagery. The
  model is a config choice (`PTV_IMAGE_MODEL`), defaulting to `z-image-turbo`
  (best archival/painterly fidelity); `schnell` is a faster, lower-fidelity
  fallback.
- `src`: a path (relative to the run dir) to an existing PNG you dropped in
  manually.

Generate assets with:

```
uv run python -m src.pipeline assets <script.json> <run_dir> [segment_ids]
```

Built HTML references assets relatively (`../../assets/{segment_id}/{id}.png`),
so the render is fully self-contained. A missing asset file is a build error
that lists every missing asset at once.

## Element cookbook

Every element has an `id`, an optional `enter` and `exit` TimeRef (fade + 12px
drift over 0.6s), and a `type`.

**layer** — a positioned image in the parallax stack.
```json
{ "id": "ridge", "type": "layer", "asset_id": "ridgeline",
  "x": 0.5, "y": 0.58, "width": 1.1, "depth": 0.5, "z": 1 }
```

Layers also carry SUBJECT MOTION — use it on every living subject so scenes
feel alive rather than a static painting the camera drifts over:

- `move`: keyframed pose path. Each key has a `time` (TimeRef) plus any of
  `x`/`y`/`scale`/`rotate`; omitted fields inherit the previous key (the first
  key inherits the layer's base pose). Positions may run off-frame
  (-0.5..1.5) so subjects can march or sail INTO and OUT OF shot. Keys are
  interpolated with the same cubic easing as the camera. Author keys in
  chronological order.
- `oscillate`: a continuous wobble on one axis —
  `{axis: x|y|rotate|scale, amplitude, period, phase}`. Amplitude is
  normalized frame units for x/y (keep it small: 0.004-0.02), degrees for
  rotate (2-6 reads well), scale delta for scale. `phase` (0-1) offsets the
  cycle — give side-by-side wave strips different phases so they undulate
  independently. Labels attached to a moving layer follow it automatically.

Recipes:

```json
// A column of soldiers marching across the frame with a step-bob:
{ "id": "column", "type": "layer", "asset_id": "legion_column",
  "x": 0.0, "y": 0.62, "width": 0.5, "depth": 0.35, "z": 2,
  "move": [ { "time": { "at_frac": 0.0 }, "x": -0.2 },
            { "time": { "at_frac": 0.95 }, "x": 1.2 } ],
  "oscillate": { "axis": "y", "amplitude": 0.006, "period": 0.7 } }
```
```json
// A ship rocking as it sails in on the word "fleet":
{ "id": "ship", "type": "layer", "asset_id": "warship_cutout",
  "x": 1.0, "y": 0.55, "width": 0.35, "depth": 0.4, "z": 2,
  "enter": { "at_word": "fleet", "offset": -0.3 },
  "move": [ { "time": { "at_word": "fleet" }, "x": 1.25 },
            { "time": { "at_frac": 1.0 }, "x": 0.35 } ],
  "oscillate": { "axis": "rotate", "amplitude": 3.5, "period": 3.2 } }
```
```json
// Ocean: two wave strips undulating out of phase behind/in front of the ship:
{ "id": "wave_back", "type": "layer", "asset_id": "wave_strip",
  "x": 0.45, "y": 0.8, "width": 1.25, "depth": 0.35, "z": 1,
  "oscillate": { "axis": "y", "amplitude": 0.006, "period": 3.4, "phase": 0.45 } }
```

**label** — a torn-paper word chip, optionally pinned to another element.
```json
{ "id": "label_field", "type": "label", "text": "the field",
  "attach": "grass_layer", "x": -0.15, "y": -0.1,
  "enter": { "at_word": "field" } }
```
`attach` positions the label at the target's current center plus `(x, y)` with a
thin pin line; omit `attach` to place it absolutely at `(x, y)`. `style` is
`torn` (default), `plain`, or `mono`.

**mask** — an animated reveal of a target element.
```json
{ "id": "reveal", "type": "mask", "target": "portrait",
  "shape": "head_silhouette", "reveal": { "at_frac": 0.05 }, "duration": 1.6 }
```
`shape` is `circle`, `rect`, or `head_silhouette` (a classical profile bust).

**particles** — a deterministic canvas field (closed-form motion).
```json
{ "id": "dust", "type": "particles", "style": "dust", "count": 80,
  "color": "$palette.muted", "area": {"x":0,"y":0,"w":1,"h":1}, "depth": 0.6 }
```
`style` is `dust` (drifting specks), `biolume` (soft pulsing glows), or
`sparks` (short streaks).

**split** — 2–3 experiment panels side by side.
```json
{ "id": "experiment", "type": "split", "direction": "horizontal", "gap": 0.06,
  "panels": [ {"asset_id": "trial_a", "label": "control"},
              {"asset_id": "trial_b", "label": "treated"} ] }
```
Panels enter staggered 0.3s apart.

**typewriter** — text typed on at `speed_cps` with a trailing block cursor.
```json
{ "id": "caption", "type": "typewriter", "text": "Two beakers, one difference.",
  "x": 0.12, "y": 0.82, "speed_cps": 16, "font": "mono" }
```
`font` is `serif`, `sans`, or `mono`.

**nodegraph** — nodes fade in, then edges draw sequentially.
```json
{ "id": "graph", "type": "nodegraph", "reveal": { "at_word": "connected" },
  "nodes": [ {"id":"a","label":"mind","x":0.6,"y":0.3} ],
  "edges": [ ["a","b"] ] }
```

## Common errors

| Error | Fix |
| --- | --- |
| `TimeRef needs exactly one of at/at_word/at_frac` | supply exactly one time field |
| `unknown palette token '$palette.foo'` | use a token the pack defines, or a raw CSS colour |
| `colour token needs a style pack but none is set` | set `style_pack` on the spec or script |
| `style pack ... missing woff2 fonts for families [...]` | the pack is missing bundled fonts — pick a pack that ships them |
| `missing asset files` | run the `assets` command, or fix the `src` path |
| `alignment is missing/stale for <id>` | re-run the `align` command |
| `layer 'x': unknown asset_id` | declare the asset under `assets` |

## Golden examples

Working, schema-valid specs live in `docs/collage/examples/`:

- `01_parallax_cold_open.collage.json` — three parallax layers, pinned torn
  labels at words, dust, and a slow camera push.
- `02_nodegraph_reveal.collage.json` — a head-silhouette mask reveal over a
  portrait plus a sequentially revealed node graph.
- `03_split_experiment.collage.json` — split panels with a typewriter caption.
- `04_outro_quote.collage.json` — a serif typewriter quote over biolume
  particles with a settling camera drift.
