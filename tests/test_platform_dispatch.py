"""Tests for OS-aware backend routing (mflux/MLX on Apple Silicon, diffusers
elsewhere) and the cross-platform pieces that support it.

Nothing here loads a model or touches a GPU: backends are monkeypatched and
only the routing/mapping layers are exercised, so the suite runs identically
on macOS, Windows, and Linux.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.imagegen import generate_image, resolve_image_provider
from src.imagegen import torch_backend
from src.imagegen.models import ImageGenResult
from src.utils import hw
from src.utils.locks import file_lock, generation_lock
from src.videogen import ltx_torch


# ---------------------------------------------------------------------------
# hw.resolve_backend
# ---------------------------------------------------------------------------


def test_resolve_backend_explicit_passthrough() -> None:
    assert hw.resolve_backend("mlx") == "mlx"
    assert hw.resolve_backend("torch") == "torch"
    # Provider-style aliases normalize to backend names.
    assert hw.resolve_backend("mflux") == "mlx"
    assert hw.resolve_backend("diffusers") == "torch"


def test_resolve_backend_auto_follows_platform(monkeypatch) -> None:
    monkeypatch.setattr(hw, "is_apple_silicon", lambda: True)
    assert hw.resolve_backend("auto") == "mlx"
    monkeypatch.setattr(hw, "is_apple_silicon", lambda: False)
    assert hw.resolve_backend("auto") == "torch"


# ---------------------------------------------------------------------------
# imagegen provider resolution + dispatch
# ---------------------------------------------------------------------------


def test_resolve_image_provider_explicit() -> None:
    assert resolve_image_provider("mflux") == "mflux"
    assert resolve_image_provider("diffusers") == "diffusers"


def test_resolve_image_provider_none_fails() -> None:
    with pytest.raises(ValueError):
        resolve_image_provider("none")


def test_resolve_image_provider_auto(monkeypatch) -> None:
    monkeypatch.setattr(hw, "is_apple_silicon", lambda: True)
    assert resolve_image_provider("auto") == "mflux"
    monkeypatch.setattr(hw, "is_apple_silicon", lambda: False)
    assert resolve_image_provider("auto") == "diffusers"


def _fake_result(tag: str) -> ImageGenResult:
    return ImageGenResult(
        segment_id=tag, image_path=Path(f"{tag}.png"), success=True,
        width=1, height=1, seed=None,
    )


def test_generate_image_dispatches_to_torch_backend(monkeypatch, tmp_path) -> None:
    from src.imagegen import flux

    monkeypatch.setattr(
        torch_backend, "generate_image", lambda **kw: _fake_result("torch")
    )
    monkeypatch.setattr(flux, "generate_image", lambda **kw: _fake_result("mflux"))

    res = generate_image(
        prompt="x", output_path=tmp_path / "a.png", provider="diffusers"
    )
    assert res.segment_id == "torch"


def test_generate_image_dispatches_to_mflux_backend(monkeypatch, tmp_path) -> None:
    from src.imagegen import flux

    monkeypatch.setattr(
        torch_backend, "generate_image", lambda **kw: _fake_result("torch")
    )
    monkeypatch.setattr(flux, "generate_image", lambda **kw: _fake_result("mflux"))

    res = generate_image(prompt="x", output_path=tmp_path / "a.png", provider="mflux")
    assert res.segment_id == "mflux"


# ---------------------------------------------------------------------------
# torch_backend model alias -> HF repo mapping
# ---------------------------------------------------------------------------


def test_repo_for_known_aliases() -> None:
    assert torch_backend.repo_for("z-image-turbo") == "Tongyi-MAI/Z-Image-Turbo"
    assert torch_backend.repo_for("schnell") == "black-forest-labs/FLUX.1-schnell"


def test_repo_for_full_repo_id_passthrough() -> None:
    assert torch_backend.repo_for("some-org/some-model") == "some-org/some-model"


def test_repo_for_unmapped_model_fails_hard() -> None:
    with pytest.raises(ValueError) as excinfo:
        torch_backend.repo_for("totally-not-a-real-model")
    msg = str(excinfo.value)
    assert "totally-not-a-real-model" in msg
    assert "z-image-turbo" in msg


# ---------------------------------------------------------------------------
# videogen: LTX pipeline class routing + backend resolution
# ---------------------------------------------------------------------------


def test_ltx_pipeline_class_routing() -> None:
    import diffusers

    assert (
        ltx_torch._pipeline_class("diffusers/LTX-2.3-Diffusers")
        is diffusers.LTX2ImageToVideoPipeline
    )
    assert (
        ltx_torch._pipeline_class("Lightricks/LTX-Video")
        is diffusers.LTXImageToVideoPipeline
    )


def test_ltx_backend_resolution(monkeypatch) -> None:
    from src.videogen import ltx

    monkeypatch.setattr(hw, "is_apple_silicon", lambda: False)
    monkeypatch.delenv("PTV_LTX_BACKEND", raising=False)
    assert ltx._resolve_ltx_backend() == "torch"
    monkeypatch.setenv("PTV_LTX_BACKEND", "mlx")
    assert ltx._resolve_ltx_backend() == "mlx"


# ---------------------------------------------------------------------------
# locks: cross-platform acquire/release
# ---------------------------------------------------------------------------


def test_generation_lock_acquires_and_releases() -> None:
    ran = []
    with generation_lock():
        ran.append(1)
    # Re-acquirable after release (a stuck lock would hang here on POSIX and
    # raise on Windows).
    with generation_lock():
        ran.append(2)
    assert ran == [1, 2]


def test_file_lock_acquires_and_releases(tmp_path) -> None:
    lock_path = tmp_path / "x.lock"
    with file_lock(lock_path):
        pass
    with file_lock(lock_path):
        pass


# ---------------------------------------------------------------------------
# tts: runtime selection
# ---------------------------------------------------------------------------


def test_tts_runtime_off_mac(monkeypatch) -> None:
    import sys

    from src.studio import tts

    monkeypatch.setattr(hw, "is_apple_silicon", lambda: False)
    python_exe, cwd, device_expr, dtype_expr = tts._runtime()
    assert python_exe == sys.executable
    assert cwd is None
    assert "cuda" in device_expr


def test_tts_runtime_on_mac(monkeypatch) -> None:
    from src.studio import tts

    monkeypatch.setattr(hw, "is_apple_silicon", lambda: True)
    python_exe, cwd, device_expr, dtype_expr = tts._runtime()
    assert device_expr == '"mps"'
    assert cwd is not None
