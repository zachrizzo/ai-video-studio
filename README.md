# Video Studio

A local, AI-powered educational/explainer video generator with a chat-driven web UI.
You talk to **Claude Code** in the app; it orchestrates a Python pipeline that turns a
topic into a finished video — script, voice, animated diagrams, AI-generated scene
photos, AI motion clips, and a final composite — all running on your own machine.

## What it does

- **Chat → video.** A two-panel web app: chat with Claude Code on the left (via the
  Claude Agent SDK), watch a live "flow viewer" on the right show each segment's
  script, image, clip, and the final video as they're produced.
- **Hybrid visuals:**
  - **Diagram segments** → animated HTML/Manim (maps, timelines, stat reveals).
  - **Scene segments** → a generated still (**FLUX** via `mflux`) turned into real
    **AI motion** with **LTX-2.3** (`ltx-2-mlx`, Apple Silicon), with Ken Burns kept
    as fallback.
  - Longer scene segments can define multiple `visual_beats`, producing several
    ordered stills/clips inside one narrated section so the final cut changes shots
    every few seconds.
- **Storyboard-first planning:** `storyboard.json` records the intended shot type,
  composition, action, camera motion, continuity notes, and duration for each visual
  beat before generation starts.
- **Voice** via ElevenLabs (or silent tracks for testing).
- **Word-synced compositing** with FFmpeg into a YouTube-ready MP4.
- **Runs locally** on Apple Silicon; large model weights cache to a configurable dir
  (e.g. an external drive) and generations are serialized to stay within memory.

## Architecture

```
studio-ui/ (React + Vite)  --ws/http-->  src/studio/ (FastAPI + Claude Agent SDK)
                                              | runs pipeline commands
                                              v
        src/ pipeline:  analysis -> script -> voice -> visuals -> composite
        |- imagegen/    FLUX stills (mflux)
        |- videogen/    LTX AI motion (diffusers) + Ken Burns (ffmpeg)
        |- animation/   Manim + HTML renderers
        |- voice/       ElevenLabs synthesis
        \- compositing/ FFmpeg final cut
```

## Quick start

Requirements: macOS (Apple Silicon recommended), Python 3.12, [`uv`](https://docs.astral.sh/uv/), `ffmpeg`, Node.js.

```bash
# 1. install python deps
uv sync

# 2. configure — see .env.example
cp .env.example .env   # add PTV_ELEVENLABS_API_KEY, PTV_VOICE_ID, PTV_MODELS_DIR, etc.

# 3. backend (serves API + chat)
STUDIO_RUNS_DIR=/tmp/video-runs uv run uvicorn src.studio.server:app --port 8787

# 4. frontend
cd studio-ui && npm install && npm run dev   # http://localhost:5173
```

Then open the app and ask it to make a video.

### Configuration

Backend environment variables (all optional):

- `STUDIO_HOME` — root for durable studio data (default `~/.video-studio`).
- `STUDIO_GENERATIONS_DIR` — where one-shot generations (Generate tab, chat
  quick clips) are stored (default `$STUDIO_HOME/generations`).
- `STUDIO_PRESETS_FILE` — custom presets JSON (default `$STUDIO_HOME/presets.json`).
- `STUDIO_AGENT_MODEL` — model for the chat agent (default: `claude-sonnet-5`,
  set to override).
- `STUDIO_RUNS_DIR` — where video runs live and what `/media` serves
  (default `/tmp/mongol-video`).

Chat transcripts are persisted server-side under `<STUDIO_RUNS_DIR>/chats/`
(one JSONL file per conversation), so conversations survive a cleared browser
and can be reloaded from any machine pointing at the same runs directory.

### Voice with Voicebox (optional, recommended)

The default voice is bundled Qwen3-TTS (local, no setup). For higher-quality
narration, use the [Voicebox](https://voicebox.sh) voice studio
([source](https://github.com/jamiepine/voicebox)):

1. Install and launch Voicebox — it must stay **running** during synthesis; it listens
   at `http://127.0.0.1:17493`. There is no silent fallback: if the app is unreachable,
   the pipeline reports it rather than switching providers.
2. In Voicebox, create a **profile** (a profile bundles a voice + engine). A
   **Chatterbox Turbo** profile wins blind tests for English narration; for other
   languages use a **Chatterbox Multilingual** profile or fall back to the Qwen voices.
3. Name the profile `Narrator` (the default), or point at any profile with
   `PTV_VOICEBOX_PROFILE=<profile name>`.
4. Select the provider with `PTV_VOICE_PROVIDER=voicebox` (env or `.env`). The
   `Anthropic Documentary` and `Historical Epic` presets already request Voicebox with
   the `Narrator` profile.

Word-level narration alignment now uses whisper **large-v3-turbo** (near large-v3
accuracy at ~4x speed), and image generation defaults to **z-image-turbo**.

### CLI (without the UI)

```bash
uv run python -m src.pipeline setup /tmp/video-runs
uv run python -m src.pipeline silence  <run_dir>/script.json <run_dir>/audio
uv run python -m src.pipeline storyboard <run_dir>/script.json <run_dir>
uv run python -m src.pipeline imagegen <run_dir>/script.json <run_dir>
uv run python -m src.pipeline videogen <run_dir>/script.json <run_dir>
uv run python -m src.pipeline manifest <run_dir>/script.json <run_dir>
uv run python -m src.pipeline composite <run_dir>/composite_manifest.json output/video.mp4
uv run python -m src.pipeline qa <run_dir>
```

The QA step writes `<run_dir>/qa_report.json` and fails on release blockers such as
missing artifacts, large TTS duration drift, bad final loudness, and high-risk visual
prompts that need review.

## Models

- **Images:** FLUX.1-schnell (Apache-2.0, commercial-safe) via `mflux`. FLUX is gated on
  HuggingFace — run `hf auth login` and accept the model license once.
- **AI video:** LTX-2.3 via `ltx-2-mlx` (`PTV_VIDEO_PROVIDER=ltx`), with a Ken Burns
  fallback (`kenburns`). Each storyboard beat's `action` and `camera_motion` are
  converted into an LTX motion prompt; when a clip is shorter than the beat, the
  pipeline tries LTX extension before falling back to a loop. Weights cache to
  `PTV_MODELS_DIR`.

## Notes

- Local generation is memory-bound on Apple MPS; a cross-process lock ensures only one
  image/video generation runs at a time.
- This is a research/hobby project. Respect each model's license for your use case.

Built with [Claude Code](https://claude.com/claude-code)
