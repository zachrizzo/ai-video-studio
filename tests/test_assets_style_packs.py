"""Workstream C — asset pipeline, style packs, presets, cutout degradation."""

import json
from pathlib import Path

import pytest
from PIL import Image

from src.assets.generate import asset_path, run_assets
from src.imagegen.cutout import alpha_fraction, extract_cutout
from src.studio.presets import list_presets
from src.studio.style_packs import list_style_packs, load_style_pack

REPO_ROOT = Path(__file__).resolve().parent.parent
STYLE_PACKS_DIR = REPO_ROOT / "style_packs"


# ---------------------------------------------------------------------------
# Style packs
# ---------------------------------------------------------------------------


def test_load_anthropic_docu_pack() -> None:
    pack = load_style_pack("anthropic_docu", STYLE_PACKS_DIR)
    assert pack.palette["paper"] == "#F0EEE6"
    assert pack.flux_prefix.strip()  # nonempty prefix
    assert pack.flux_suffix.strip()  # nonempty suffix
    # Required fonts are bundled (builder errors without them).
    names = {p.name for p in pack.fonts}
    assert any("Lora" in n for n in names)
    assert any("Inter" in n for n in names)
    assert any("IBMPlexMono" in n or "IBM" in n for n in names)


def test_list_style_packs_includes_anthropic() -> None:
    ids = {p["id"] for p in list_style_packs(STYLE_PACKS_DIR)}
    assert "anthropic_docu" in ids


def test_unknown_style_pack_raises_listing_available() -> None:
    with pytest.raises(FileNotFoundError) as exc:
        load_style_pack("does_not_exist", STYLE_PACKS_DIR)
    assert "anthropic_docu" in str(exc.value)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


def test_anthropic_documentary_preset_present() -> None:
    presets = {p["id"]: p for p in list_presets()}
    assert "anthropic_documentary" in presets
    preset = presets["anthropic_documentary"]
    assert preset["style_pack"] == "anthropic_docu"
    assert preset["default_visual_engine"] == "collage"


# ---------------------------------------------------------------------------
# Cutout — pure gate + structured (never-raising) failure
# ---------------------------------------------------------------------------


def test_alpha_fraction_pure() -> None:
    opaque = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
    transparent = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    assert alpha_fraction(opaque) == 1.0
    assert alpha_fraction(transparent) == 0.0
    # Non-RGBA images have no alpha channel.
    assert alpha_fraction(Image.new("RGB", (10, 10))) == 0.0


def test_alpha_gate_rejects_all_opaque() -> None:
    # An all-opaque matte (fraction 1.0) exceeds the default alpha_max (0.95),
    # so the gate must reject it — rembg removed nothing.
    opaque = Image.new("RGBA", (10, 10), (0, 0, 0, 255))
    assert alpha_fraction(opaque) > 0.95


def test_extract_cutout_structured_failure(tmp_path: Path) -> None:
    # rembg model weights cannot download in this offline container, so
    # extract_cutout must return a structured failure (never raise, never
    # silently degrade).
    raw = tmp_path / "raw.png"
    Image.new("RGB", (64, 64), (200, 120, 90)).save(raw)
    out = tmp_path / "cut.png"

    result = extract_cutout(raw, out, model="isnet-general-use", models_dir=str(tmp_path))
    assert result["success"] is False
    assert result["method"] == "rembg"
    assert result["error"]
    # Actionable message: points at the missing model / needed network access,
    # or the alpha gate if the model somehow loaded.
    err = result["error"].lower()
    assert any(w in err for w in ("model", "network", "online", "download", "alpha", "rembg"))
    # No degraded file was written on failure.
    assert not out.exists()


# ---------------------------------------------------------------------------
# run_assets — skip existing without importing mflux
# ---------------------------------------------------------------------------


def _minimal_spec(seg_id: str) -> dict:
    return {
        "spec_version": 1,
        "segment_id": seg_id,
        "duration_seconds": 5.0,
        "assets": [
            {
                "id": "hero",
                "role": "subject",
                "generate": {"prompt": "a lighthouse", "width": 512, "height": 512},
            }
        ],
        "elements": [
            {
                "id": "hero_layer",
                "type": "layer",
                "asset_id": "hero",
                "x": 0.5,
                "y": 0.5,
                "width": 0.6,
            }
        ],
    }


def test_run_assets_skips_existing_without_generating(tmp_path: Path, monkeypatch, capsys) -> None:
    seg_id = "seg_001"
    run_dir = tmp_path / "run"
    (run_dir / "scenes").mkdir(parents=True)
    spec_path = run_dir / "scenes" / f"{seg_id}.collage.json"
    spec_path.write_text(json.dumps(_minimal_spec(seg_id)))

    # The asset already exists (manual override) — must be skipped, and
    # generate_image must never be imported/called.
    target = asset_path(run_dir, seg_id, "hero")
    target.parent.mkdir(parents=True)
    Image.new("RGBA", (16, 16), (0, 0, 0, 255)).save(target)

    def _boom(*args, **kwargs):
        raise AssertionError("generate_image must not be called when the asset exists")

    monkeypatch.setattr("src.imagegen.flux.generate_image", _boom)

    script_path = run_dir / "script.json"  # absent is fine; scene file drives discovery

    run_assets(script_path, run_dir)  # must not raise or sys.exit

    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["skipped"] is False
    assert f"{seg_id}/hero" in out["skipped_existing"]
    assert out["generated"] == []
    assert out["errors"] == {}
