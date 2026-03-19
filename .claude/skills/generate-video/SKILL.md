---
name: generate-video
description: Generate a 3Blue1Brown-style educational video from a research paper PDF. YOU are the AI brain — you analyze the paper, write the narration script, generate Manim/HTML animation code, and orchestrate rendering + voice synthesis + compositing.
argument-hint: <path-to-paper.pdf>
---

# Generate Educational Video from Paper

You are the orchestrator. You do ALL the thinking — analyzing the paper, writing the script, generating animation code, fixing render errors. The Python toolkit handles the mechanical parts (PDF parsing, Manim rendering, voice synthesis, FFmpeg compositing).

## Prerequisites Check
1. Verify the PDF exists at the given path
2. `cd /Users/zachrizzo/Desktop/programming/video-generation`
3. For voice: check `.env` has `PTV_ELEVENLABS_API_KEY` and `PTV_VOICE_ID`. If not set, use `--no-voice` mode (silent audio with estimated durations).

## Step 1: Setup Working Directory
```bash
uv run python -m src.pipeline setup /tmp/paper-to-video
```
This returns JSON with `run_dir`, `audio_dir`, `video_dir`, `scenes_dir`. Save these paths.

## Step 2: Parse the Paper
```bash
uv run python -m src.pipeline parse "$ARGUMENTS" <run_dir>
```
Then read the generated `<run_dir>/paper.md` to understand the paper content.

## Step 3: Analyze the Paper (YOU do this)
Read the paper markdown. Then write a JSON file at `<run_dir>/analysis.json`:
```json
{
    "core_contribution": "One sentence",
    "target_audience_level": "beginner|intermediate|advanced",
    "key_concepts": [
        {"name": "...", "description": "...", "visual_engine": "manim|html", "importance": 1-5, "prerequisites": []}
    ],
    "paper_summary": "2-3 paragraphs",
    "suggested_video_title": "Catchy YouTube title"
}
```
For visual_engine classification:
- **manim**: equations, transforms, function graphs, geometric proofs, 3D surfaces
- **html**: architecture diagrams, flowcharts, timelines, comparisons, concept maps

## Step 4: Write the Narration Script (YOU do this)

### YouTube Length Requirements
- **Target: 10–15 minutes total** (600–900 seconds)
- **15–20 segments**, each **40–70 seconds** long
- Shorter segments (15–25s) are OK only for title cards, transitions, or simple stat reveals

### Script Structure (3B1B style)
1. **Hook** (30–45s) — Start with a striking question or surprising fact. No jargon yet.
2. **Problem** (60–90s) — Why does this matter? What was broken before?
3. **Intuition** (90–120s) — Build the idea from first principles, use analogies
4. **The Math** (60–90s) — Now introduce the formalism, grounded in the intuition
5. **How It Works** (90–120s) — Walk through the mechanism step by step
6. **Results & Proof** (60–90s) — Show the numbers, comparisons, ablations
7. **Why It Matters** (60s) — Real-world impact, what it enabled
8. **Recap** (30–45s) — One-sentence takeaway per concept

### Narration Style Rules — FAST, PUNCHY, HUMAN
- **Pace**: Write for ~3 words/second. Short sentences. No filler. Every word earns its place.
- Speak in second person: "You know how...", "Think about it like this—"
- Use ALL CAPS for emphasis on KEY words: "that's TEN THOUSAND times fewer"
- Use `...` for trailing off, `—` for punchy interruptions
- Contractions always. "It's", "can't", "they're" — never "it is", "cannot"
- Build intuition before equations. Never show a formula cold.
- One concrete analogy per abstract concept
- Vary energy: build up → peak → breathe → build again

### Audio Tags for Expressive Narration (ElevenLabs v3)

v3 interprets bracketed stage directions as vocal performance cues. Use them LIBERALLY — 4–8 per 60-second segment. They make the voice feel human, not robotic.

**Emotion & Tone:**
`[excited]` `[calm]` `[serious tone]` `[playfully]` `[awe]` `[nervous]` `[frustrated]` `[happily]` `[reflective]` `[dramatic]` `[conversational tone]` `[matter-of-fact]` `[deadpan]` `[cheerfully]` `[sarcastically]` `[wistful]`

**Pacing & Delivery:**
`[rushed]` `[slows down]` `[deliberate]` `[rapid-fire]` `[drawn out]` `[hesitates]` `[whispers]` `[quietly]` `[loudly]`

**Human Sounds (these make it feel REAL):**
`[breathes]` `[breathes deeply]` `[sighs]` `[soft chuckle]` `[laughs]` `[clears throat]` `[gasps]` `[gulps]` `[inhales deeply]`

**Punctuation tricks (not tags, but v3 interprets them):**
- `...` = trailing off, reflective pause
- `—` = sharp interruption/pivot
- ALL CAPS = louder emphasis on that word
- `!` = energy boost

**Example of GOOD narration (notice the rhythm and tags):**
```
"[conversational tone] So here's the thing about GPT-3. It has a hundred and seventy-five BILLION parameters. [breathes] That's... a lot. And if you want to fine-tune it? [soft chuckle] You'd need to retrain every. single. one. [pause] [serious tone] The cost? Astronomical. We're talking millions of dollars per task. [breathes deeply] But then— [excited] a team at Microsoft figured out a trick so elegant it almost feels like cheating."
```

**Pacing patterns for engagement:**
- **Build-up**: `[conversational tone]` → steady → `[rushed]` → `[excited]` PEAK! → `[breathes]` → reset
- **Reveal**: `[slows down]` ... `[pause]` ... `[whispers]` the key insight ... `[excited]` AND THAT CHANGES EVERYTHING!
- **Grounding**: After emotional peaks, use `[breathes]` or `[matter-of-fact]` to reset before the next build

### Animation Cues — WORD-LEVEL SYNC IS MANDATORY
Each animation_cue corresponds to a **specific word or phrase in the narration**. When the narrator says that word, the visual changes. This creates the feeling that visuals respond to the voice.

**How to calculate timestamps:** Count words from the start of narration text at ~3 words/second. When the narrator says "175 billion" (words 5-6, ~2s in), the number "175,000,000,000" should appear on screen at that exact moment.

**Every cue must specify `trigger_words`** — the exact narration words that trigger this visual change.

**Visual density rule:** Something must change on screen every 4–8 seconds. For a 30s segment, you need at least 5–6 cues. Visuals should PROGRESSIVELY REVEAL — don't show everything at once. Build information piece by piece as the narrator explains it.

```json
{
    "title": "Video title",
    "total_estimated_duration_seconds": 720,
    "segments": [
        {
            "segment_id": "seg_001",
            "section_title": "The Hook",
            "narration_text": "GPT-3 has 175 billion parameters. Fine-tuning means retraining every single one. That's prohibitively expensive — most companies can't afford it. In 2021, Microsoft researchers found a clever shortcut called LoRA, and it changed how the entire industry builds AI.",
            "estimated_duration_seconds": 18,
            "animation_cues": [
                {"timestamp_hint": "0s",  "description": "Show '175,000,000,000' counting up", "visual_engine": "html", "math_content": null},
                {"timestamp_hint": "5s",  "description": "All weight cells light up red — retraining everything", "visual_engine": "html", "math_content": null},
                {"timestamp_hint": "10s", "description": "Red X or price tag: $$$", "visual_engine": "html", "math_content": null},
                {"timestamp_hint": "14s", "description": "LoRA title fades in", "visual_engine": "html", "math_content": null}
            ],
            "visual_engine": "html",
            "transition_type": "fade"
        }
    ]
}
```

**Timestamp rules:**
- `"0s"` = first frame of segment
- `"12s"` = 12 seconds into this segment
- Every 8–15 seconds, something NEW must appear or change on screen
- The last cue must land at least 3s before segment end (allow viewer to absorb)
- For Manim segments, timestamp_hints map directly to `self.wait()` durations between animations
- For HTML segments, timestamp_hints become `animation-delay` CSS values

## Step 5: Generate Audio
**With voice** (ElevenLabs configured):
```bash
uv run python -m src.pipeline synthesize <run_dir>/script.json <audio_dir>
```

**Without voice** (testing / no API key):
```bash
uv run python -m src.pipeline silence <run_dir>/script.json <audio_dir>
```
Both produce `<audio_dir>/audio_manifest.json` with durations per segment.

## Step 6: Generate & Render Visuals (YOU write the code)

For each segment, read its actual audio duration from the manifest. Then:

1. **Write the visual code** using the animation_cues timestamps to synchronize visuals to narration
2. **Save a scene spec JSON** at `<scenes_dir>/<segment_id>.json`:
```json
{
    "segment_id": "seg_001",
    "visual_engine": "html",
    "code": "...",
    "target_duration_seconds": 18.0,
    "narration_text": "GPT-3 has 175 billion parameters...",
    "description": "The Hook",
    "animation_cues": [
        {"timestamp_hint": "0s",  "description": "Counter counting up"},
        {"timestamp_hint": "5s",  "description": "Weight grid lights red"},
        {"timestamp_hint": "10s", "description": "Price tag appears"},
        {"timestamp_hint": "14s", "description": "LoRA title fades in"}
    ]
}
```
3. **Render it**:
```bash
uv run python -m src.pipeline render <scenes_dir>/seg_001.json <scenes_dir>
```
4. If it failed, read the error, fix the code, re-render. Up to 5 attempts.
5. If all fail, use fallback:
```bash
uv run python -m src.pipeline fallback seg_001 "Title" "Description" 42.5 <scenes_dir>
```

### How to Use Timestamps When Writing Visual Code

**For HTML segments:**
Convert each `timestamp_hint` directly to a CSS `animation-delay`:
```css
/* cue at "0s" */
.counter        { opacity: 0; animation: fadeIn 0.5s ease 0s   forwards; }
/* cue at "5s" */
.weight-grid    { opacity: 0; animation: fadeIn 0.5s ease 5s   forwards; }
/* cue at "10s" */
.price-tag      { opacity: 0; animation: fadeIn 0.5s ease 10s  forwards; }
/* cue at "14s" */
.lora-title     { opacity: 0; animation: fadeIn 0.8s ease 14s  forwards; }
```

**For Manim segments:**
Convert timestamps to `self.wait()` durations between animations:
```python
# target_duration = 45s, cues at 0s, 8s, 18s, 30s, 40s
self.play(Write(title), run_time=1.5)       # cue at 0s
self.wait(6.5)                               # gap: 8s - 0s - 1.5s = 6.5s
self.play(Write(equation), run_time=2.5)     # cue at 8s
self.wait(7.5)                               # gap: 18s - 8s - 2.5s = 7.5s
self.play(FadeIn(labels), run_time=1.2)      # cue at 18s
self.wait(10.8)                              # gap: 30s - 18s - 1.2s = 10.8s
self.play(Create(highlight), run_time=0.8)   # cue at 30s
self.wait(9.2)                               # gap: 40s - 30s - 0.8s = 9.2s
self.play(FadeIn(note), run_time=0.8)        # cue at 40s
self.wait(4.2)                               # tail: 45s - 40s - 0.8s = 4.2s
self.play(FadeOut(*self.mobjects), run_time=1.0)  # always end with fadeout
```
**Always verify: sum of all run_times + waits = target_duration_seconds**

### Visual Density Rule
Every segment must have **meaningful visual change every 8–15 seconds**. If a segment is 60 seconds long, it needs at least 4–6 distinct visual events. Dead screen time (nothing changing) kills engagement.

### Manim Code Guidelines
Reference the prompt at `.claude/skills/generate-video/prompts/manim_codegen.md`

### HTML Code Guidelines
Reference the prompt at `.claude/skills/generate-video/prompts/html_codegen.md`

## Step 7: Composite Final Video
Create a composite manifest at `<run_dir>/composite_manifest.json`:
```json
{
    "video_paths": ["path/to/seg_001.mp4", "path/to/seg_002.mp4"],
    "audio_paths": ["path/to/audio_seg_001.mp3", "path/to/audio_seg_002.mp3"]
}
```
For **video-only** (no audio), omit `audio_paths` or set to `[]`.

```bash
uv run python -m src.pipeline composite <run_dir>/composite_manifest.json output/<title>.mp4
```

## Step 8: Report Results
Tell the user:
- Output video path and duration
- Number of segments rendered vs fallbacks
- Any issues encountered

## Parallelization
Render multiple segments in parallel — each segment is independent.
