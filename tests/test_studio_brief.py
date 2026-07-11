"""_STUDIO_BRIEF must stay in sync with the code it describes.

The brief is interpolated from the real sources of truth (producer step order,
sfx SOUNDS registry, PipelineConfig voicebox_url); these tests guard against
someone reverting it to hardcoded prose that can silently drift.
"""

from __future__ import annotations

from pathlib import Path

from src.audio.sfx import SOUNDS
from src.config import PipelineConfig
from src.studio.agent import _STUDIO_BRIEF
from src.studio.producer import _pipeline_steps


def test_brief_lists_full_pipeline_step_order():
    names = [name for name, _label, _args in _pipeline_steps(Path("run"), "full")]
    rendered = " → ".join(
        f"{name} (again)" if name in names[:i] else name for i, name in enumerate(names)
    )
    assert rendered in _STUDIO_BRIEF


def test_brief_mentions_every_sfx_sound():
    for name in SOUNDS:
        assert name in _STUDIO_BRIEF, f"sfx sound {name!r} missing from _STUDIO_BRIEF"


def test_brief_mentions_configured_voicebox_url():
    assert PipelineConfig.model_fields["voicebox_url"].default in _STUDIO_BRIEF


def test_brief_has_no_unexpanded_placeholders():
    assert "%STEP_ORDER%" not in _STUDIO_BRIEF
    assert "%SFX_SOUNDS%" not in _STUDIO_BRIEF
    assert "%VOICEBOX_URL%" not in _STUDIO_BRIEF
