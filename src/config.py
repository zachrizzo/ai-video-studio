from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import Literal


class PipelineConfig(BaseSettings):
    # API Keys
    elevenlabs_api_key: str = ""
    ltx_api_key: str = ""

    # Voice Settings
    voice_id: str | None = None
    voice_stability: float = 0.5
    voice_similarity_boost: float = 0.75
    voice_style: float = 0.0
    elevenlabs_model: str = "eleven_v3"
    voice_use_speaker_boost: bool = True
    voice_speed: float = 1.2  # 0.7-1.2, >1.0 = faster pacing

    # Local TTS — "qwen" (bundled Qwen3-TTS), "voicebox" (Voicebox app REST API,
    # https://voicebox.sh — voice/engine live on a profile created in the app;
    # recommend a Chatterbox Turbo profile for English narration), or
    # "elevenlabs" (cloud, needs API key).
    voice_provider: Literal["elevenlabs", "qwen", "voicebox"] = "qwen"
    qwen_tts_speaker: str = "dylan"
    qwen_tts_language: str = "english"
    qwen_tts_model_size: str = "0.6B"
    # Voicebox (jamiepine/voicebox) — the app must be RUNNING during synthesis;
    # there is no fallback. voicebox_profile is a profile NAME (case-insensitive)
    # or id from the app's profile list.
    voicebox_url: str = "http://127.0.0.1:17493"
    voicebox_profile: str = "Narrator"
    voicebox_language: str = "en"

    # Video Settings
    video_quality: Literal["low", "medium", "high", "4k"] = "high"
    frame_rate: int = 30
    background_color: str = "#1a1a2e"
    video_speed: float = Field(1.0, ge=0.25, le=4.0)  # playback speed of the final video

    # Local image generation (via mflux)
    # image_model: any mflux base model. Default z-image-turbo (Tongyi, Apache
    # 2.0, ungated) — beats FLUX.1-schnell on quality in 2026 comparisons and is
    # already cached in models_dir. FLUX "schnell" remains a faster fallback
    # (PTV_IMAGE_MODEL=schnell); "dev" is gated + non-commercial. Other ungated
    # options: "z-image", "qwen", "flux2-klein-4b".
    image_provider: Literal["mflux", "none"] = "mflux"
    image_model: str = "z-image-turbo"
    image_steps: int = 8  # z-image-turbo ~6-10; schnell ~4-8; dev ~20-25
    # Where large model weights are cached (HuggingFace hub cache). Set to an
    # external drive to keep multi-GB models off the internal disk. Empty = default
    # (~/.cache/huggingface). The HF auth token stays in the default location.
    models_dir: str = "/Volumes/4TB-Z/models"
    image_quantize: int = 4  # mflux -q (4 or 8); 4 = lowest memory
    image_force: bool = False  # regenerate even if a PNG already exists
    video_force: bool = False  # regenerate MP4 clips even if they already exist
    image_timeout_seconds: int = 900  # first run downloads a multi-GB model

    # Local video / motion generation
    #   ltx      = real LTX-2.3 image-to-video motion on Apple Silicon
    #   kenburns = ffmpeg pan/zoom fallback (fast, no model)
    #   comfyui  = AI image-to-video via a ComfyUI server
    video_provider: Literal["kenburns", "ltx", "comfyui"] = "ltx"
    kenburns_zoom: float = 1.12
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_model: str = "ltx"
    video_fallback_to_kenburns: bool = True  # if ai video fails, fall back to kenburns
    # LTX (MLX) settings. Keep generation near the model's native 24fps and use
    # image keyframes to anchor identity/composition across each short shot.
    ltx_model: str = "diffusers/LTX-2.3-Diffusers"
    ltx_steps: int = 30
    ltx_gen_width: int = 704
    ltx_gen_height: int = 448
    ltx_clip_seconds: float = 3.0
    ltx_max_frames: int = 73
    ltx_cfg_scale: float = 3.0
    ltx_stg_scale: float = 1.0
    ltx_anchor_last_frame: bool = True
    ltx_prefer_extend: bool = False

    # Collage engine — cutout extraction (rembg)
    cutout_model: str = "isnet-general-use"
    # A cutout is accepted when the fraction of pixels with alpha > 0.5 falls
    # inside [min, max]; outside that range rembg either removed everything or
    # nothing, and the soft-radial-mask fallback is used instead.
    cutout_alpha_min: float = 0.05
    cutout_alpha_max: float = 0.95

    # Collage engine — style packs
    style_packs_dir: Path = Path("style_packs")

    # Word-level narration alignment (whisper CLI, same shape as qa_asr_command).
    # large-v3-turbo: near large-v3 accuracy at ~4x speed (needs openai-whisper
    # >= 20240930; ~1.6 GB one-time weight download).
    align_command: str = "PYENV_VERSION=3.11.13 whisper"
    align_model: str = "large-v3-turbo"
    captions_burn_in: bool = False  # stretch: burn word-level .ass captions

    # Release QA gates
    qa_target_lufs: float = -16.0
    qa_min_lufs: float = -20.0
    qa_max_lufs: float = -14.0
    qa_max_audio_duration_ratio: float = 1.45
    qa_max_audio_duration_overage_seconds: float = 4.0
    qa_min_transcript_similarity: float = 0.72
    qa_require_asr: bool = False
    qa_asr_command: str = "PYENV_VERSION=3.11.13 whisper"
    qa_asr_model: str = "large-v3-turbo"
    # "voicebox" routes QA transcription through the Voicebox app's /transcribe
    # endpoint (plain text; word timestamps are NOT available there, which is
    # why alignment above stays on the local whisper CLI).
    qa_asr_provider: Literal["cli", "voicebox"] = "cli"
    # Frame-rendered scenes must land within this many seconds of target.
    qa_scene_duration_epsilon: float = 0.25
    # Blank-frame gate: a frame is "blank" only when strictly uniform
    # (luma stddev below this); near-uniform paper backgrounds must pass.
    qa_min_luma_stddev: float = 1.0
    qa_max_blank_seconds: float = 4.0

    # Pipeline Settings
    max_render_attempts: int = 5
    render_timeout_seconds: int = 120
    target_video_duration_minutes: int = 10

    # Paths
    output_dir: Path = Path("output")
    temp_dir: Path = Path("/tmp/paper-to-video")
    voice_samples_dir: Path = Path("voice_samples")

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PTV_")

    @property
    def manim_quality_flag(self) -> str:
        return {"low": "-ql", "medium": "-qm", "high": "-qh", "4k": "-qk"}[
            self.video_quality
        ]

    @property
    def resolution(self) -> tuple[int, int]:
        if self.video_quality == "4k":
            return (3840, 2160)
        return (1920, 1080)
