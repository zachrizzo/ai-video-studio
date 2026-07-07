"""Text-to-speech generation via Qwen3-TTS (local, Apple Silicon).

Uses the Qwen3-TTS installation at /Volumes/4TB-Z/programming/qwen-TTS/Qwen3-TTS.
Supports custom voices, voice cloning, and multiple languages.
"""

import subprocess
import tempfile
from pathlib import Path

_QWEN_TTS_DIR = Path("/Volumes/4TB-Z/programming/qwen-TTS/Qwen3-TTS")
_QWEN_PYTHON = _QWEN_TTS_DIR / ".venv" / "bin" / "python"

SPEAKERS = ['aiden', 'dylan', 'eric', 'ono_anna', 'ryan', 'serena', 'sohee', 'uncle_fu', 'vivian']
LANGUAGES = ['auto', 'chinese', 'english', 'french', 'german', 'italian', 'japanese', 'korean', 'portuguese', 'russian', 'spanish']

# Pre-built voice clones available
CLONED_VOICES = {
    "zachs_voice": str(_QWEN_TTS_DIR / "zachs_voice_17b_icl.pt"),
}


def generate_speech(
    text: str,
    output_path: Path,
    speaker: str = "serena",
    language: str = "auto",
    instruct: str | None = None,
    ref_audio: str | None = None,
    model_size: str = "0.6B",
) -> dict:
    """Generate speech from text using Qwen3-TTS.

    Returns {success, output_path, error}.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build a Python script to run in the Qwen TTS venv
    if ref_audio:
        # Voice clone mode
        script = f'''
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

model = Qwen3TTSModel.from_pretrained(
    {repr(f"Qwen/Qwen3-TTS-12Hz-{model_size}-Base")},
    device_map="mps",
    dtype=torch.float32,
)
wavs, sr = model.generate_voice_clone(
    text={repr(text)},
    ref_audio={repr(ref_audio)},
    ref_text="",
    language={repr(language)},
    x_vector_only_mode=True,
)
sf.write({repr(str(output_path))}, wavs[0], sr)
print("OK")
'''
    else:
        # Custom voice mode
        instruct_arg = f"instruct={repr(instruct)}," if instruct else ""
        script = f'''
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

model = Qwen3TTSModel.from_pretrained(
    {repr(f"Qwen/Qwen3-TTS-12Hz-{model_size}-CustomVoice")},
    device_map="mps",
    dtype=torch.float32,
)
wavs, sr = model.generate_custom_voice(
    text={repr(text)},
    language={repr(language)},
    speaker={repr(speaker)},
    {instruct_arg}
)
sf.write({repr(str(output_path))}, wavs[0], sr)
print("OK")
'''

    try:
        proc = subprocess.run(
            [str(_QWEN_PYTHON), "-c", script],
            capture_output=True, text=True, timeout=300,
            cwd=str(_QWEN_TTS_DIR),
        )
        if proc.returncode != 0 or not output_path.exists():
            tail = (proc.stderr or proc.stdout or "")[-500:]
            return {"success": False, "output_path": str(output_path),
                    "error": f"Qwen TTS failed: {tail}"}
        return {"success": True, "output_path": str(output_path), "error": None}
    except subprocess.TimeoutExpired:
        return {"success": False, "output_path": str(output_path),
                "error": "Qwen TTS timed out after 300s"}
    except Exception as e:
        return {"success": False, "output_path": str(output_path),
                "error": str(e)}
