"""Studio configuration — durable storage paths and agent settings.

Everything resolves environment variables lazily at call time so tests can
monkeypatch them. Data lives under STUDIO_HOME (default ~/.video-studio);
a one-time best-effort migration copies data from the legacy /tmp location.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_LEGACY_DIR = Path("/tmp/video-studio-generations")


def studio_home() -> Path:
    """Root directory for durable studio data."""
    home = Path(os.environ.get("STUDIO_HOME", "") or "~/.video-studio").expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home


def generations_dir() -> Path:
    """Directory holding generation outputs, migrated from /tmp if present."""
    env = os.environ.get("STUDIO_GENERATIONS_DIR", "")
    gens = Path(env).expanduser() if env else studio_home() / "generations"
    empty = not gens.exists() or not any(gens.iterdir())
    if empty and _LEGACY_DIR.exists() and gens != _LEGACY_DIR:
        try:
            shutil.copytree(_LEGACY_DIR, gens, dirs_exist_ok=True)
            logger.info("Migrated generations from %s to %s", _LEGACY_DIR, gens)
        except Exception:
            logger.warning("Failed to migrate generations from %s", _LEGACY_DIR,
                           exc_info=True)
    gens.mkdir(parents=True, exist_ok=True)
    return gens


def presets_file() -> Path:
    """Path to the custom presets JSON, migrated from /tmp if present."""
    env = os.environ.get("STUDIO_PRESETS_FILE", "")
    target = Path(env).expanduser() if env else studio_home() / "presets.json"
    legacy = _LEGACY_DIR / "presets.json"
    if not target.exists() and legacy.exists() and target != legacy:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, target)
            logger.info("Migrated presets from %s to %s", legacy, target)
        except Exception:
            logger.warning("Failed to migrate presets from %s", legacy,
                           exc_info=True)
    return target


def agent_model() -> str | None:
    """Model override for the chat agent (STUDIO_AGENT_MODEL), or None."""
    return os.environ.get("STUDIO_AGENT_MODEL", "").strip() or None
