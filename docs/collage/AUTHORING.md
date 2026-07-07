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
