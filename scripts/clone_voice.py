"""Upload voice samples to ElevenLabs and create a cloned voice.

Reads samples from voice_samples/ directory and creates a voice clone.
Stores the resulting voice_id in .env.

Usage:
    uv run python scripts/clone_voice.py [--name "My Voice"]
"""

import sys
from pathlib import Path

VOICE_SAMPLES_DIR = Path(__file__).parent.parent / "voice_samples"
ENV_FILE = Path(__file__).parent.parent / ".env"


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Clone voice via ElevenLabs")
    parser.add_argument("--name", default="MyVoice", help="Name for the cloned voice")
    args = parser.parse_args()

    # Check for API key
    from dotenv import load_dotenv
    import os

    load_dotenv(ENV_FILE)
    api_key = os.getenv("PTV_ELEVENLABS_API_KEY")

    if not api_key:
        print("❌ PTV_ELEVENLABS_API_KEY not found in .env")
        print("Add your ElevenLabs API key to .env first.")
        sys.exit(1)

    # Find samples
    samples = list(VOICE_SAMPLES_DIR.glob("*.wav")) + list(VOICE_SAMPLES_DIR.glob("*.mp3"))
    if not samples:
        print(f"❌ No voice samples found in {VOICE_SAMPLES_DIR}")
        print("Record samples first: uv run python scripts/record_voice.py")
        sys.exit(1)

    print(f"📁 Found {len(samples)} voice samples")
    for s in samples:
        print(f"   {s.name}")

    # Clone voice
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=api_key)

    print(f"\n🔄 Cloning voice as '{args.name}'...")

    try:
        voice = client.clone(
            name=args.name,
            files=[str(s) for s in samples[:25]],
            description="Cloned voice for educational video narration",
        )
        voice_id = voice.voice_id
    except Exception as e:
        # Try alternative API
        print(f"  First method failed ({e}), trying alternative...")
        file_handles = [open(s, "rb") for s in samples[:25]]
        try:
            voice = client.voices.add(
                name=args.name,
                files=file_handles,
            )
            voice_id = voice.voice_id
        finally:
            for fh in file_handles:
                fh.close()

    print(f"✅ Voice cloned! ID: {voice_id}")

    # Update .env
    _update_env("PTV_VOICE_ID", voice_id)
    print(f"✅ Updated .env with PTV_VOICE_ID={voice_id}")
    print(f"\nYou're ready to generate videos!")


def _update_env(key: str, value: str):
    """Update or add a key in the .env file."""
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()
    else:
        lines = []

    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
