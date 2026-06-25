# Video Studio App — Design Spec

**Date:** 2026-06-24
**Status:** Approved design, pending implementation plan

## One-line

A local desktop-style web app where you chat with Claude Code (running in the
background via the Claude Agent SDK) to produce educational YouTube videos, with
a live "flow viewer" that shows the script, generated images, animated clips, and
final video for each segment as they are produced.

## Background & motivation

We already have a working paper/topic → video pipeline (script → ElevenLabs voice
→ Manim/HTML visuals → FFmpeg composite). It is driven entirely from the terminal
by Claude. Two problems:

1. **No visibility** — you cannot see a segment's output without opening files, and
   cannot fix one without Claude re-running CLI commands.
2. **The brain is locked in the terminal** — all orchestration happens through a
   chat the user does not directly control.

The app solves both: the chat *is* Claude Code (same orchestrator that produced the
Mongol video), and the flow viewer is a live window into everything it generates.

This is NOT a from-scratch video tool. It is a **video-studio skin over Claude Code**,
backed by the existing Python pipeline.

## Goals

- Chat with Claude Code in-app; it can run the existing `generate-video` skill and
  pipeline commands without per-command permission prompts.
- See, per segment and in real time: narration script + cues, the generated image,
  the animated clip (or rendered HTML scene), audio, and a status badge.
- Play the final composited video in-app.
- Foundation that later supports: regenerating a single segment, and local
  image/video generation (FLUX + Wan/LTX).

## Non-goals (for the MVP)

- Not a timeline editor (no drag-to-trim, no manual keyframing).
- Not self-serve/no-Claude generation — Claude Code is always the brain.
- Not multi-user, not cloud-hosted. Local, single user, on the M5 Pro.
- Local image/video gen is a **fast-follow**, not part of the first shell.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Frontend — React + Vite (browser, localhost)            │
│  ┌──────────────┐   ┌──────────────────────────────────┐ │
│  │ Chat panel   │   │ Flow viewer panel                │ │
│  │ - messages   │   │ - run selector + final video     │ │
│  │ - streaming  │   │ - segment cards (script/img/clip)│ │
│  │   tool events│   │ - live status updates            │ │
│  └──────────────┘   └──────────────────────────────────┘ │
└───────────────┬───────────────────────────┬──────────────┘
                │ WebSocket (chat + events)  │ HTTP (run data, media)
                ▼                            ▼
┌──────────────────────────────────────────────────────────┐
│  Backend — FastAPI (Python)                              │
│  - /ws/chat       : stream Claude Agent SDK query()      │
│  - /api/runs      : list runs                            │
│  - /api/runs/{id} : run manifest (segments + paths)      │
│  - /media/...     : serve images / clips / audio / mp4   │
│  - Agent session mgmt (resume=session_id)               │
│  - PostToolUse hook → push "artifact updated" events     │
└───────────────────────────┬──────────────────────────────┘
                            │ claude-agent-sdk query()
                            ▼
┌──────────────────────────────────────────────────────────┐
│  Claude Code (background brain)                          │
│  - skills="all", setting_sources=["project"]            │
│  - permission_mode="acceptEdits"                        │
│  - allowed_tools includes Bash(uv run *)                │
│  - runs the existing src/ pipeline + generate-video skill│
└───────────────────────────┬──────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────┐
│  Existing Python pipeline (src/) → run_dir artifacts      │
│  script.json · audio/ · scenes/ · images/ · clips/ · mp4  │
└──────────────────────────────────────────────────────────┘
```

## Components

### 1. Backend (`src/studio/`)
- `server.py` — FastAPI app; HTTP routes + WebSocket.
- `agent.py` — wraps `claude_agent_sdk.query()`. Maintains `session_id` per chat
  for multi-turn (`resume=session_id`). Streams `StreamEvent`s (text deltas,
  tool-use start/stop) over the WebSocket. Options:
  - `skills="all"`, `setting_sources=["project"]`
  - `permission_mode="acceptEdits"`
  - `allowed_tools=["Read","Write","Edit","Bash(uv run *)","Bash(ls *)", ...]`
  - `cwd=` project root
  - `hooks={"PostToolUse": [...]}` → on Write/Bash, emit a WS event so the viewer
    refreshes the affected run.
- `runs.py` — reads a run_dir into a manifest: list of segments, each with
  `{segment_id, section_title, narration_text, cues, image_path?, clip_path?,
  scene_path?, audio_path, status}`, plus `final_video_path`. Status derived from
  which files exist.

### 2. Frontend (`studio-ui/`, React + Vite)
- `ChatPanel` — message list; renders streamed assistant text and a compact,
  collapsible "tool activity" line per tool call (e.g. "Rendering seg_003…").
  Input box posts to the WS.
- `FlowViewer` — run selector, final-video `<video>` player, and a vertical list
  of `SegmentCard`s.
- `SegmentCard` — three columns: **Script** (narration + cues), **Image** (FLUX
  still, or placeholder), **Clip** (clip/scene `<video>`, plus audio play button).
  Status badge: pending / generating / done / failed.
- State: WS for chat + live events; REST for run/segment data; on a "file ready"
  event for the active run, refetch that run's manifest.

### 3. Run/segment data model
A run = the existing run_dir. Manifest is computed on demand from files present, so
the viewer works with runs that already exist (no migration). Segment status:
- `pending` — in script.json, no audio/scene yet
- `generating` — partial artifacts present (heuristic / hook signal)
- `done` — scene/clip + audio present
- `failed` — render fallback marker present

## Data flow (happy path)
1. User types "make a video about the fall of Rome" in chat.
2. Backend `query()` starts/resumes a Claude Code session; streams events to chat.
3. Claude runs the `generate-video` skill → writes `script.json`, audio, scenes,
   composites final mp4 — each Write/Bash triggers the PostToolUse hook.
4. Hook pushes "artifact updated (run X)" over WS; frontend refetches run X manifest.
5. Segment cards fill in and flip to ✅; final-video player appears when mp4 exists.

## MVP build order
0. **Codebase cleanup** (see section below) — delete dead PDF-ingestion path,
   `templates/`, and unused deps; verify the pipeline still runs. Isolated commit.
1. Backend shell: FastAPI + `runs.py` manifest + `/media` static serving. Verify
   against the existing Mongol run (`/tmp/mongol-video/run_2e98e40a`).
2. Frontend shell: React+Vite two-panel layout; FlowViewer reads a run over REST
   and plays the final video + shows all 16 segment cards (script + scene + audio).
3. Agent chat: `/ws/chat` streaming `query()`; ChatPanel renders text + tool events.
4. Live updates: PostToolUse hook → WS event → viewer refresh.
5. Fast-follow: per-segment "regenerate" button (chat-driven command).
6. Fast-follow: local image/video gen modules (`src/imagegen/` FLUX via ComfyUI,
   `src/videogen/` Wan/LTX) populate the image/clip columns.

## Codebase cleanup (part of this work)

The project pivoted from "paper PDF → video" to "topic → video." The PDF-ingestion
path and unused scaffolding are now dead weight. Remove it as part of this effort so
the Studio app is built on a lean pipeline. Verified unused as of 2026-06-24:

**Delete (confirmed dead):**
- `src/ingestion/` — `parser.py` (615 lines, PyMuPDF PDF parsing), `models.py`
  (PaperContent/Section/Equation/Figure/Table), `__init__.py`. The topic-driven flow
  never calls `parse`; Claude writes `analysis.json` + `script.json` directly.
- `src/pipeline.py` — remove `cmd_parse`, its `"parse"` entry in the command table,
  and its line in the usage/help text.
- `templates/` — entire dir (`html/*.html`, `manim/*.py`, stale `__pycache__`).
  Referenced nowhere in `src/`; visual code is authored inline by Claude.
- `pyproject.toml` — drop `pymupdf` and `pdfplumber` deps (only used by the parser).

**Audit, then delete if unused:**
- `src/analysis/analyzer.py` and `src/analysis/script_writer.py` (21 lines each) —
  likely vestigial; analysis + scriptwriting are done by Claude, not these modules.
  Confirm no imports, then remove (keep `src/analysis/models.py` if the dataclasses
  are still used by the pipeline).
- `src/voice/synthesizer.py` — remove the now-unused `speed` constructor param /
  attribute left over after the ElevenLabs API change.

**Keep (still relevant):**
- `scripts/clone_voice.py`, `scripts/record_voice.py` — support the own-voice path
  (relevant given ElevenLabs paid-voice + demonetization concerns).
- All of `src/animation/`, `src/voice/`, `src/compositing/`, `src/utils/`,
  `src/config.py` — the live pipeline.

**Method:** remove in a dedicated commit BEFORE building the Studio backend, run the
existing pipeline end-to-end once (silence mode is fine) to confirm nothing broke,
then proceed. This keeps the deletion isolated and easy to revert if something was
load-bearing.

## Tech choices
- **Backend:** FastAPI + `claude-agent-sdk` (Python ≥3.12, already the project's
  Python). Reuses existing `src/` pipeline untouched.
- **Frontend:** React + Vite (chosen). Dev: Vite dev server proxying to FastAPI.
- **Transport:** WebSocket for chat + live events; HTTP for run data + media.
- **Local models (fast-follow):** ComfyUI (Metal backend) as a local server; FLUX.1
  dev for images, LTX-2.3 (fast) / Wan 2.2 (quality) for image→video. All on-device.

## Risks & mitigations
- **Agent SDK permission scope** — too-broad auto-approve is risky. Mitigate with a
  tight `allowed_tools` allowlist + a PreToolUse hook that only approves known
  pipeline commands; everything else still prompts.
- **Long generation blocks the WS** — generation can take minutes. Run the agent
  query in a background task; stream progress; never block the event loop.
- **Viewer/agent file races** — read manifests defensively (files may be mid-write);
  treat missing/partial files as `generating`.
- **Local model setup cost** — keep image/video gen behind a feature flag so the
  chat+viewer shell is usable immediately without ComfyUI installed.

## Success criteria (MVP)
- Open the app, select the existing Mongol run, play the final video, and browse all
  16 segments with script + scene + audio.
- Type a request in chat; watch Claude Code stream its work; see new segments appear
  in the viewer live; play the resulting video — all without touching the terminal.
