# Collage engine: three Vox-signature finishing techniques

**Date:** 2026-07-17
**Status:** approved, implementing

## Goal

Close the three remaining gaps between the collage engine's output and the Vox
mixed-media explainer look: (1) 12fps "motion on twos" stutter, (2) lens
character / chromatic aberration, (3) blur-masked transitions between scenes.
Each is a small, opt-in scene-level knob that stays inside the frozen
`window.seek(t)` contract (pure function of `t`, no wall-clock, no randomness
beyond the seeded PRNG). All three default **on** for the documentary house
style so generated videos actually use them.

## Non-goals

- True full-frame per-pixel RGB-split aberration (SVG `feDisplacementMap` on the
  whole 1080p DOM every frame). The reference AE technique is itself edge-masked;
  a static periphery overlay is faithful and avoids per-frame filter cost.
- Real overlapping cross-dissolve at the compositor (ffmpeg `xfade`). Scenes are
  hard-cut concatenated while narration audio is concatenated separately and
  merged after; overlapping video would steal duration and desync the voiceover,
  and would require rewriting the concat step into a filter_complex chain. The
  blur-masked hard cut (Approach A below) gives the Vox tracking-transition read
  without that risk.

## 1. Motion on twos — `stutter_fps`

- **Spec:** `CollageSpec.stutter_fps: Literal[12, 15, 24] | None = None`
  (`None` = smooth). 12 is the canonical Vox cadence.
- **Builder:** pass `stutterFps` into the compiled `window.__COLLAGE__` scene.
- **Runtime:** in `seek(t)`, when `stutterFps` is set, quantize the time fed to
  every renderer to `tq = Math.floor(t * stutterFps) / stutterFps`. The file is
  still frame-rendered at the real `fps`; the whole collage (camera, subject
  motion, oscillation, fades) snaps to the on-twos cadence. Determinism holds:
  `tq` is a pure function of `t`.
- **Test:** a moving layer's `left` is identical at two times inside the same
  1/`stutterFps` bucket and differs across a bucket boundary.

## 2. Lens character — `lens`

- **Spec:** `CollageSpec.lens: bool = False`.
- **Runtime/CSS:** when on, `finish()` appends one static `.collage-lens`
  overlay, masked to the periphery (radial mask: transparent center → opaque
  edge) carrying `backdrop-filter: blur(3px)` plus two faint offset red/cyan
  radial-gradient fringes. Constant in `t`; no per-frame cost beyond one
  composited overlay.
- **Test:** `.collage-lens` present when `lens:true`, absent when `false`.

## 3. Blur-peaked transitions — `transition_in` / `transition_out`

Approach A (in-scene envelopes). Because scene A ends blurred+pushed and scene B
begins blurred+pushed, the hard concat cut lands between two blurred,
motion-matched frames and reads as a Vox tracking transition — the blur masks
the cut.

- **Spec:** `transition_in`, `transition_out: Transition | None`, where
  `Transition = {seconds: float>0 (default 0.5), blur_px: float>=0 (default 14),
  push: float (default 0.06)}`. `push` is the extra camera scale added at the
  peak (in) / (out).
- **Runtime:** a global blur on `.collage-frame` and an extra stage scale, both
  ramping with the existing `easeInOut`:
  - `transition_in`: peaks at `t = 0`, resolves to 0 over `seconds`.
  - `transition_out`: 0 until `duration - seconds`, peaks at `t = duration`.
  Applied as a pure function of `t` inside `seek`.
- **Test:** frame blur is higher near the boundary (t≈0 with `transition_in`)
  than mid-scene.

## Defaults & wiring

- `src/studio/presets.py`: documentary preset sets `stutter_fps=12`, `lens=True`,
  and standard `transition_in`/`transition_out` on authored scenes (via the
  agent house-rules text and/or preset env), so the agent emits them.
- `src/studio/agent.py`: house-rules prose instructs the agent to add motion on
  twos, lens, and boundary transitions.
- `docs/collage/AUTHORING.md`: cookbook entries for all three.

## Tests

Extend `tests/test_collage_builder.py`:
- String-level: scene JSON carries `stutterFps`, `lens`, `transitionIn/Out`;
  runtime carries the quantization, lens, and transition code.
- Behavioral (Playwright, same harness as the existing e2e test): stutter
  quantization bucket check; transition boundary blur peak; lens overlay
  presence. Plus the full-suite regression run.
