---
name: ai-news-video
description: Use when asked to make, produce, or refresh an AI news video or news-rundown episode for a date window — e.g. "make an AI news video for yesterday", "do this week's AI news", "another episode" — or to extend/redo a previous episode's stories, visuals, or narration.
---

# AI News Video

Produce a fact-checked, Vox-style AI news rundown (~30s/story, ~8 min for 15 stories): real screenshots + screen-recorded clips of the **primary sources**, generated benchmark charts, `zach 2` Voicebox narration, collage engine with the Vox finish. One episode = one studio run in its own project. YOU are the editor — research, story picking, and script voice are judgment; the scripts in `scripts/` are the mechanical layer.

**Delegate heavy phases to subagents** (research workflow, capture/produce passes) to preserve context — then refute-check their reports: re-run at least one verification yourself (a frame extract, a grep) before trusting "done".

## Inputs
- **Date window** (required): "yesterday" / "this week" / explicit range. Resolve to concrete dates first.
- **Story slate** (optional): if the user supplies stories, skip discovery but still fact-check every one.

## Phases (gate: no scripting until all briefs are locked)

**0 — Scaffold.** `projects.create_project(...)` + new `run_<hex8>` under the runs root with `scenes/ assets/shared/ audio/ research/`; `assign_run`. Runs auto-register once `script.json` exists (`src/studio/projects.py`, `src/studio/runs.py`).

**1 — Stories + research.** Discovery (if no slate): WebSearch the window, collect ~12–16 AI stories (drop pure-gadget). Then a Workflow: per story **2 independent research passes + a reconciler** (schema: verified claim, key numbers, confidence, ≥2 source URLs, best_screenshot_url, headline ≤8 words, stat_callout, source_tag). Load-bearing disagreement → escalate that story (targeted 2-verifier tiebreak, or full `deep-research`). Persist `research/NN_slug.json` + `summary.md`. Facts that only one pass found stay "reportedly".

**2 — Visuals: primary sources first.** For each story capture 2–3 stills (`scripts/capture.py`) preferring official blog/release/docs/product/GitHub/HF pages; news articles are the fallback. Record 5–7 slow-scroll webm clips of the richest pages (`scripts/record.py`) for native video beats. Generate 6–8 charts **from verified brief numbers only** (style: `scripts/charts_reference.py` — paper bg, ink text, accent bars, Helvetica, blank bottom band so the lower-third never covers labels). Lower-thirds + title cards: `scripts/lowerthirds_reference.py`. **View every asset** (contact sheet) — see Verification.

**3 — Script.** ~65–75 words/story (`zach 2` ≈ 2.0 w/s → ~30s), crisp anchor tone; intro teases 4–5 stories; outro states the week's pattern. Style contract:
- The narration is what an anchor says on air. It contains story facts, attributed hedges ("reportedly", "Bloomberg says", "per The Information"), and transitions ("Next:", "Meanwhile,", "And the big one:").
- Numbering lives ONLY in the on-screen chips ("07 / 15") — transitions replace spoken story numbers, so inserting a story never desyncs narration from chips.
- Editorial-process language stays out of the script: it describes the *story*, never the *research that produced it* (no "fact-checked", "confirmed vs reported", "cross-checked", "X confirmed it", no freshness disclaimers like "about ten days old"). On-screen labels follow the same contract.
- Segment fields per `ScriptSegment` (`src/analysis/models.py`); `visual_engine: "collage"`, `style_pack: "anthropic_docu"`. Reference: `scripts/script_reference.py`.

**4 — Scenes.** Mechanics in `scripts/scenes_reference.py` (adapt its STORIES list; the beat machinery is the reusable part): 5–6 beats/story (~5s each), open on the strongest primary visual, chart as full-bleed mid-beat (w 0.86, never punched-in), one video beat full-bleed (w 0.90, `rate = clip_duration / beat_window`), stills on de-zoomed FRAMINGS (punch-ins ≤ 1.10, headline band y ≈ 0.40), persistent lower-third + `NN / total` chip, Vox finish (`stutter_fps 12, lens, transition_in/out`). `at_frac` timing (`at_word` needs whisper installed). Validate every spec builds (`load_collage_spec` + `build_collage_html`) before rendering.

**5 — Audio.** Precheck Voicebox: `/health` must show `model_loaded: true` AND a short test generation must COMPLETE (the API streams "generating" heartbeats forever when the model isn't actually loaded). Then `PTV_VOICE_PROVIDER=voicebox PTV_VOICEBOX_PROFILE="zach 2" python -m src.pipeline synthesize <run>/script.json <run>/audio`. Re-roll a segment = delete its wav first (manifest skips existing takes). Voice unavailable → STOP and tell the user; never substitute a voice.

**6 — Produce.** `scripts/produce.py`: `plan` → `clean` → one `render` call per batch (≤3 segments, foreground, sequential) → `finish`. Never one long call; never a yielded background render.

**7 — Verify (refute, don't confirm).** ffprobe duration/streams; extract ~10 frames across the final including **two frames inside a video beat (they must differ — a static beat is a failure)**, a chart beat, a punch-in (must land on headline/content); contact-sheet and LOOK. Grep the final script for editorial-process phrases (step 3 list) — must be clean. Confirm the run shows in the studio UI. An empty/blank frame is never a pass.

## Trap table (each one broke a real episode)

| Trap | Rule |
|---|---|
| Background render dies when the driving agent yields | Foreground batches via `produce.py render`; poll nothing |
| Two renders racing one run dir (stale + new) | `pgrep -f "src.pipeline"` before launching; kill strays |
| Voicebox `/health` up but generations never complete | Precheck = health AND a completed test clip; else stop |
| Capture "ok" but it's a cookie wall / interstitial / error page | View every capture; consent iframes need the JS STRIP, not selector clicks |
| openai.com (and some SPAs) blank under headless | Use an alternate reputable source or archive.org; log the substitution |
| Spoken story numbers desync from chips when a story is inserted | Numbering only in chips; narration uses transitions |
| Meta/editor language leaks into narration | Step-3 style contract + the verification grep |
| Punch-ins land on empty page regions | Punch-in ≤1.10 anchored to the headline band |
| Data-driven view empty ≠ working | Frames inside video beats must differ; charts must be legible |
| Shell calls capped ~10 min; buffered logs look empty | Bounded batch calls; don't diagnose from an empty log |

## scripts/
`capture.py` (stills) · `record.py` (webm scrolls) · `produce.py` (plan/clean/render/finish) · references from episode 2026-07-18: `scenes_reference.py`, `charts_reference.py`, `lowerthirds_reference.py`, `script_reference.py` (adapt their episode-specific lists; paths in references are that episode's run).
