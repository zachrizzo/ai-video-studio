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
    **AI motion** (**LTX-Video** via `diffusers`, Apple MPS) or a fast Ken Burns clip.
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

### CLI (without the UI)

```bash
uv run python -m src.pipeline setup /tmp/video-runs
uv run python -m src.pipeline silence  <run_dir>/script.json <run_dir>/audio
uv run python -m src.pipeline imagegen <run_dir>/script.json <run_dir>
uv run python -m src.pipeline videogen <run_dir>/script.json <run_dir>
uv run python -m src.pipeline composite <run_dir>/composite_manifest.json output/video.mp4
```

## Models

- **Images:** FLUX.1-schnell (Apache-2.0, commercial-safe) via `mflux`. FLUX is gated on
  HuggingFace — run `hf auth login` and accept the model license once.
- **AI video:** LTX-Video via `diffusers` (`PTV_VIDEO_PROVIDER=ltx`), with a Ken Burns
  fallback (`kenburns`). Weights cache to `PTV_MODELS_DIR`.

## Notes

- Local generation is memory-bound on Apple MPS; a cross-process lock ensures only one
  image/video generation runs at a time.
- This is a research/hobby project. Respect each model's license for your use case.

Built with [Claude Code](https://claude.com/claude-code)
