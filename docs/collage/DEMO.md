# Demo — "Inside the mind of a language model" (Anthropic-documentary collage)

A 60–90s piece in the collage style: archival-painting parallax layers, torn-paper
labels pinned to spoken words, a node-graph + silhouette reveal, a split-screen
"experiment" panel, and a quiet typewriter outro — all deterministically
code-rendered in Anthropic's design language.

The full run uses FLUX (asset art), rembg (cutouts), whisper (word alignment),
and Qwen3-TTS (narration), so **run it on the Mac** where those models live
(mflux/LTX/Qwen are Apple-Silicon; rembg needs one online run to cache weights).
Everything else — the builder, the deterministic frame renderer, QA — is what the
Linux CI exercises.

## Prerequisites (Mac)

- `uv sync` in the repo; `whisper` CLI available (or set `PTV_ALIGN_COMMAND`).
- One online run so rembg can cache its `isnet-general-use` weights under
  `PTV_MODELS_DIR/rembg`.
- The `anthropic_docu` style pack ships in `style_packs/` with its bundled fonts.

## Option A — let the Studio agent produce it (recommended)

1. Start the studio server + UI, pick the **Anthropic Documentary** preset
   (it sets `style_pack: anthropic_docu`, `default_visual_engine: collage`,
   voice `eric`).
2. Ask: *"Make a 75-second documentary titled 'Inside the mind of a language
   model' in the collage style."*

The agent follows the brief in `src/studio/agent.py` (COLLAGE ENGINE section) and
`docs/collage/AUTHORING.md`: it writes `script.json` (with `style_pack`), one
`scenes/{id}.collage.json` per segment (using the 4 golden examples in
`docs/collage/examples/` as templates), then runs:

```
setup → script.json → storyboard → synthesize → align →
  (write collage specs) → assets → collage → manifest → composite → qa
```

## Option B — manual command sequence

Given a `script.json` with five collage segments (`visual_engine: "collage"`,
`visual_type: "diagram"`, top-level `"style_pack": "anthropic_docu"`) and a
`scenes/{id}.collage.json` per segment:

```bash
RUN=/tmp/paper-to-video/run_xxx          # from `setup`
S=$RUN/script.json
uv run python -m src.pipeline setup /tmp/paper-to-video
# ... write $S and the collage specs ...
uv run python -m src.pipeline storyboard  $S $RUN
uv run python -m src.pipeline synthesize  $S $RUN/audio
uv run python -m src.pipeline align       $S $RUN     # whisper word timings
uv run python -m src.pipeline assets      $S $RUN     # FLUX stills + rembg cutouts
uv run python -m src.pipeline collage     $S $RUN     # build + deterministic render
uv run python -m src.pipeline manifest    $S $RUN
uv run python -m src.pipeline composite   $RUN/composite_manifest.json output/mind.mp4
uv run python -m src.pipeline qa          $RUN
```

Each new command **exits non-zero with an actionable message** on real failure
(whisper missing, a cutout rejected, an `at_word` with no alignment, a missing
asset or font) — there are no silent fallbacks. `assets`/`align`/`collage` exit 0
printing `{"skipped": true}` only when a run has no collage work, so legacy
(non-collage) runs are unaffected.

## Suggested 5-segment arc

| # | Segment | Template example | Exercises |
|---|---------|------------------|-----------|
| 1 | Metaphor cold-open | `01_parallax_cold_open` | layers @ 3 depths, cutouts, torn labels pinned `at_word`, dust particles, slow camera push |
| 2 | The graph of concepts | `02_nodegraph_reveal` | node-graph edge reveal, `head_silhouette` mask iris |
| 3 | An experiment | `03_split_experiment` | split-screen panels, typewriter |
| 4 | A generated scene | (FLUX still → LTX) | a `visual_type: "scene"` segment under the same style pack, proving mixed engines compose |
| 5 | Closing thought | `04_outro_quote` | serif typewriter quote, biolume particles, camera drift |

Keep every shot ≥2.5s and the camera calm (max scale ~1.15) per the pack's motion
tokens. Prefer `at_word`/`at_frac` TimeRefs so labels land on the spoken word even
though real narration never matches the duration estimate.

## What "done" looks like

`qa` passes with no errors (duration drift within `qa_scene_duration_epsilon`, no
blank-frame runs; `freezedetect` warnings on deliberate calm holds are expected),
and the piece plays in the Studio FlowViewer. Re-running `composite` on an
unchanged legacy run remains byte-stable — the collage engine only adds new
optional steps.
