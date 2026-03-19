"""Voice sample recording helper for ElevenLabs voice cloning.

Records 2-3 minutes of speech samples from the user's microphone.
Provides reading prompts for diverse, high-quality voice samples.

Usage:
    uv run python scripts/record_voice.py
"""

import sys
import time
from pathlib import Path

VOICE_SAMPLES_DIR = Path(__file__).parent.parent / "voice_samples"

READING_PROMPTS = [
    # Diverse sentence types for good voice cloning
    "The quick brown fox jumps over the lazy dog. This sentence contains every letter of the alphabet, making it perfect for voice calibration.",
    "Imagine you're standing at the edge of a vast ocean. The waves crash against the rocks below, sending spray into the air. Each droplet catches the light differently.",
    "In mathematics, we often encounter situations where the obvious approach fails. That's when creative problem-solving becomes essential. Let me show you what I mean.",
    "The transformer architecture revolutionized natural language processing by introducing self-attention mechanisms. Unlike recurrent neural networks, transformers can process all positions in parallel.",
    "Here's the key insight that makes this algorithm work. Instead of computing the full matrix multiplication, we can decompose it into smaller, more manageable pieces.",
    "Let's take a step back and think about why this matters. When we scale these systems to billions of parameters, even small efficiency gains compound dramatically.",
    "The result is quite surprising, actually. Despite using significantly fewer computational resources, the model achieves comparable performance on all major benchmarks.",
    "Now, you might be wondering — how does this connect to what we discussed earlier? The answer lies in a beautiful mathematical relationship.",
]


def main():
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        print("Required packages not installed. Run:")
        print("  uv pip install sounddevice soundfile")
        sys.exit(1)

    VOICE_SAMPLES_DIR.mkdir(exist_ok=True)
    sample_rate = 44100

    print("\n🎤 Voice Sample Recording for ElevenLabs Cloning")
    print("=" * 50)
    print(f"\nSamples will be saved to: {VOICE_SAMPLES_DIR}")
    print(f"\nYou'll read {len(READING_PROMPTS)} prompts (~2-3 minutes total).")
    print("Tips for best quality:")
    print("  - Use a quiet environment")
    print("  - Speak naturally, at your normal pace")
    print("  - Keep a consistent distance from the microphone")
    print("  - Use your natural speaking voice (not a 'presenter' voice)")
    print("\nPress Enter to start, or Ctrl+C to quit.")
    input()

    for i, prompt in enumerate(READING_PROMPTS, 1):
        output_path = VOICE_SAMPLES_DIR / f"sample_{i:02d}.wav"

        print(f"\n--- Sample {i}/{len(READING_PROMPTS)} ---")
        print(f"\nRead this aloud:\n")
        print(f'  "{prompt}"')
        print(f"\nPress Enter when ready to record...")
        input()

        print("🔴 Recording... (Press Enter when done)")

        # Record until Enter is pressed
        frames = []
        recording = True

        def callback(indata, frame_count, time_info, status):
            if recording:
                frames.append(indata.copy())

        stream = sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            callback=callback,
        )
        stream.start()
        input()  # Wait for Enter
        recording = False
        stream.stop()
        stream.close()

        if frames:
            import numpy as np
            audio_data = np.concatenate(frames)
            sf.write(str(output_path), audio_data, sample_rate)
            duration = len(audio_data) / sample_rate
            print(f"✅ Saved: {output_path.name} ({duration:.1f}s)")
        else:
            print("⚠️  No audio captured, skipping.")

    print(f"\n{'=' * 50}")
    print(f"✅ All samples recorded!")
    print(f"📁 Samples: {VOICE_SAMPLES_DIR}")
    print(f"\nNext step: Clone your voice by running:")
    print(f"  uv run python scripts/clone_voice.py")


if __name__ == "__main__":
    main()
