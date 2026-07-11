"""Runtime capability probing for the local media engines.

The chat agent embeds `summary_line()` in its system prompt each turn so it
stops confidently instructing users about engines that are not actually
available on this machine. Results are cached for a short TTL because the
probe runs on every turn.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import shlex
import shutil
import socket
import time

logger = logging.getLogger(__name__)

_VOICEBOX_ADDR = ("127.0.0.1", 17493)
_CACHE_TTL_SECONDS = 60.0

_cache: dict[str, bool] | None = None
_cache_at: float = 0.0


def _check_voicebox() -> bool:
    """The Voicebox app has no cheap health route; a TCP connect is enough."""
    try:
        with socket.create_connection(_VOICEBOX_ADDR, timeout=0.5):
            return True
    except Exception:  # noqa: BLE001
        return False


def _check_which(name: str) -> bool:
    try:
        return shutil.which(name) is not None
    except Exception:  # noqa: BLE001
        return False


def _check_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001
        return False


_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _check_align_command() -> bool:
    """Probe the executable actually configured for alignment/QA transcription.

    align_command (e.g. "PYENV_VERSION=3.11.13 whisper") may prefix the real
    executable with shell env-var assignments; checking the literal string
    "whisper" would misreport availability whenever this is overridden via
    PTV_ALIGN_COMMAND.
    """
    try:
        from src.config import PipelineConfig

        tokens = shlex.split(PipelineConfig().align_command)
        for token in tokens:
            if not _ENV_ASSIGNMENT_RE.match(token):
                return _check_which(token)
        return False
    except Exception:  # noqa: BLE001
        return False


def _check_ltx() -> bool:
    """LTX runs via the ltx-2-mlx CLI checked out at a fixed path; reuse the
    pipeline's own constant so this stays in sync with src/videogen/ltx.py."""
    try:
        from src.videogen.ltx import _LTX_MLX_DIR

        return _LTX_MLX_DIR.is_dir()
    except Exception:  # noqa: BLE001
        return False


def probe(force: bool = False) -> dict[str, bool]:
    """Return {engine: available} for the local generation stack."""
    global _cache, _cache_at
    now = time.monotonic()
    if not force and _cache is not None and now - _cache_at < _CACHE_TTL_SECONDS:
        return _cache
    caps = {
        "voicebox": _check_voicebox(),
        "whisper": _check_align_command(),
        "ffmpeg": _check_which("ffmpeg"),
        "mflux": _check_module("mflux"),
        "ltx": _check_ltx(),
    }
    _cache = caps
    _cache_at = now
    return caps


def summary_line() -> str:
    """One-line summary for the agent's system prompt, e.g.
    `[capabilities] voicebox=up whisper=ok ffmpeg=ok mflux=ok ltx=ok`."""
    caps = probe()
    parts = []
    for name, available in caps.items():
        if name == "voicebox":
            parts.append(f"voicebox={'up' if available else 'down'}")
        else:
            parts.append(f"{name}={'ok' if available else 'missing'}")
    return "[capabilities] " + " ".join(parts)
