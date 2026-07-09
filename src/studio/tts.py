"""Text-to-speech generation via Qwen3-TTS (local), OS-aware.

The synthesis always runs as a subprocess so model memory is freed after each
clip. Which interpreter runs it depends on the platform:

- Apple Silicon: the dedicated Qwen3-TTS checkout/venv (config PTV_QWEN_TTS_DIR),
  on MPS — the original setup.
- Windows/Linux: this project's own interpreter (the ``qwen-tts`` package is a
  dependency on non-mac platforms), on CUDA when available, else CPU.

Same models either way: Qwen/Qwen3-TTS-12Hz-<size>-{CustomVoice,Base}.
"""

import subprocess
import sys
from pathlib import Path

SPEAKERS = ['aiden', 'dylan', 'eric', 'ono_anna', 'ryan', 'serena', 'sohee', 'uncle_fu', 'vivian']
LANGUAGES = ['auto', 'chinese', 'english', 'french', 'german', 'italian', 'japanese', 'korean', 'portuguese', 'russian', 'spanish']


def _qwen_tts_dir() -> Path:
    from src.config import PipelineConfig

    return Path(PipelineConfig().qwen_tts_dir)


def _runtime() -> tuple[str, str | None, str, str]:
    """(python executable, cwd, device expression, dtype expression) per OS.

    device/dtype are Python expressions evaluated inside the subprocess, so
    CUDA detection happens in the process that actually loads the model.
    """
    from src.utils.hw import is_apple_silicon

    if is_apple_silicon():
        qwen_dir = _qwen_tts_dir()
        return (
            str(qwen_dir / ".venv" / "bin" / "python"),
            str(qwen_dir),
            '"mps"',
            "torch.float32",
        )
    return (
        sys.executable,
        None,
        '("cuda" if torch.cuda.is_available() else "cpu")',
        '(torch.bfloat16 if torch.cuda.is_available() else torch.float32)',
    )


# Pre-built voice clones available (paths only exist on the original mac setup).
CLONED_VOICES = {
    "zachs_voice": str(_qwen_tts_dir() / "zachs_voice_17b_icl.pt") if sys.platform == "darwin" else "",
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

    python_exe, cwd, device_expr, dtype_expr = _runtime()

    # Build a Python script to run in the TTS interpreter
    if ref_audio:
        # Voice clone mode
        script = f'''
import torch
import soundfile as sf
from qwen_tts import Qwen3TTSModel

model = Qwen3TTSModel.from_pretrained(
    {repr(f"Qwen/Qwen3-TTS-12Hz-{model_size}-Base")},
    device_map={device_expr},
    dtype={dtype_expr},
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
    device_map={device_expr},
    dtype={dtype_expr},
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
            [python_exe, "-c", script],
            capture_output=True, text=True, timeout=900,  # first run downloads the model
            cwd=cwd,
        )
        if proc.returncode != 0 or not output_path.exists():
            tail = (proc.stderr or proc.stdout or "")[-500:]
            return {"success": False, "output_path": str(output_path),
                    "error": f"Qwen TTS failed: {tail}"}
        return {"success": True, "output_path": str(output_path), "error": None}
    except subprocess.TimeoutExpired:
        return {"success": False, "output_path": str(output_path),
                "error": "Qwen TTS timed out after 900s"}
    except Exception as e:
        return {"success": False, "output_path": str(output_path),
                "error": str(e)}
