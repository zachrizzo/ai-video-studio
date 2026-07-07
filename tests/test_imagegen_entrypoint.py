"""Tests for mflux model -> CLI entrypoint routing in src.imagegen.flux.

Regression guard: ``mflux-generate`` is FLUX-only, so a non-FLUX model (e.g.
z-image-turbo) routed to it dies loading FLUX-only weights (text_encoder_2).
generate_image must dispatch each model to the architecture-matching CLI, and
must fail hard on an unmapped model rather than guessing an entrypoint. These
tests monkeypatch subprocess.run so nothing is actually generated.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.imagegen import flux
from src.imagegen.flux import _entrypoint_for, generate_image


# ---------------------------------------------------------------------------
# _entrypoint_for mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", ["schnell", "dev", "krea-dev", "dev-krea"])
def test_flux_family_uses_generic_entrypoint(model: str) -> None:
    assert _entrypoint_for(model) == "mflux-generate"


def test_zimage_models_use_dedicated_entrypoints() -> None:
    assert _entrypoint_for("z-image-turbo") == "mflux-generate-z-image-turbo"
    assert _entrypoint_for("z-image") == "mflux-generate-z-image"


def test_unmapped_model_fails_hard() -> None:
    with pytest.raises(ValueError) as excinfo:
        _entrypoint_for("totally-not-a-real-model")
    msg = str(excinfo.value)
    # Actionable: names the offending model and lists supported ones.
    assert "totally-not-a-real-model" in msg
    assert "z-image-turbo" in msg
    assert "schnell" in msg


# ---------------------------------------------------------------------------
# generate_image dispatches argv[0] per model
# ---------------------------------------------------------------------------


def _patch_run(monkeypatch, captured: dict) -> None:
    """Fake subprocess.run: record argv, emulate mflux writing the output PNG."""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out = Path(cmd[cmd.index("--output") + 1])
        out.write_bytes(b"\x89PNG\r\n" + b"0" * 60_000)  # >50 KB so it counts as real

        class _Completed:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Completed()

    monkeypatch.setattr(flux.subprocess, "run", fake_run)
    # Entrypoints exist in the venv; keep the which() guard from short-circuiting
    # even if PATH is unusual under the test runner.
    monkeypatch.setattr(flux.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_generate_image_zimage_turbo_argv(tmp_path, monkeypatch) -> None:
    captured: dict = {}
    _patch_run(monkeypatch, captured)

    res = generate_image(
        prompt="x", output_path=tmp_path / "a.png",
        width=512, height=512, steps=8, model="z-image-turbo", seed=1,
    )
    assert res.success
    assert captured["cmd"][0] == "mflux-generate-z-image-turbo"
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "z-image-turbo"


def test_generate_image_schnell_argv(tmp_path, monkeypatch) -> None:
    captured: dict = {}
    _patch_run(monkeypatch, captured)

    res = generate_image(
        prompt="x", output_path=tmp_path / "b.png",
        width=512, height=512, steps=4, model="schnell", seed=1,
    )
    assert res.success
    assert captured["cmd"][0] == "mflux-generate"


def test_generate_image_unmapped_model_raises(tmp_path, monkeypatch) -> None:
    captured: dict = {}
    _patch_run(monkeypatch, captured)
    with pytest.raises(ValueError):
        generate_image(
            prompt="x", output_path=tmp_path / "c.png", model="bogus-model",
        )
    assert "cmd" not in captured  # failed before spawning a subprocess
