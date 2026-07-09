"""OS and accelerator detection shared by the generation backends.

The pipeline was originally built for Apple Silicon (mflux / MLX). On other
platforms the same models run through diffusers + PyTorch instead: Z-Image-Turbo
and FLUX via ``ZImagePipeline``/``FluxPipeline``, LTX-2.3 via
``LTX2ImageToVideoPipeline``. Backends resolve "auto" through these helpers so
every entry point picks the stack that actually works on the current machine.
"""

import platform
import shutil
import sys


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return sys.platform == "win32"


def is_apple_silicon() -> bool:
    return is_macos() and platform.machine() == "arm64"


def mlx_available() -> bool:
    """True when the MLX toolchain can actually run (Apple Silicon + mflux CLI)."""
    return is_apple_silicon() and shutil.which("mflux-generate") is not None


def torch_device() -> str:
    """Best available torch device: cuda > mps > cpu.

    Imports torch lazily — callers use this right before loading a model, so
    the import cost is paid exactly once where it is already unavoidable.
    """
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resolve_backend(requested: str = "auto") -> str:
    """Resolve an "auto" backend request to "mlx" or "torch".

    Explicit requests pass through so a mac user can force the torch path
    (or vice versa) via config/env without code changes.
    """
    if requested in ("mlx", "torch", "mflux", "diffusers"):
        return {"mflux": "mlx", "diffusers": "torch"}.get(requested, requested)
    return "mlx" if is_apple_silicon() else "torch"
