"""Tests for the collage builder, validator branch, and cmd staleness logic.

No browser: these assert on the built HTML *string* only. Deterministic
rendering is covered by another workstream's test.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.animation.models import SceneSpec
from src.animation.validator import ValidationError, validate
from src.collage.builder import build_collage_html
from src.collage.spec import load_collage_spec

EXAMPLES = Path("docs/collage/examples")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def _make_png(path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 8), (217, 119, 87, 255)).save(path)


@pytest.fixture
def style_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    packs_dir = tmp_path / "style_packs"
    pack = packs_dir / "testpack"
    (pack / "fonts").mkdir(parents=True)
    (pack / "tokens.json").write_text(
        json.dumps(
            {
                "name": "Test Pack",
                "palette": {
                    "paper": "#F0EEE6",
                    "ink": "#1F1E1B",
                    "accent": "#D97757",
                    "muted": "#8A8778",
                },
                "type": {"serif": "TestSerif", "sans": "TestSans", "mono": "TestMono"},
            }
        )
    )
    for fam in ("TestSerif", "TestSans", "TestMono"):
        (pack / "fonts" / f"{fam}-Regular.woff2").write_bytes(b"woff2-fake-" + fam.encode())
    (pack / "runtime.css").write_text("/* testpack css */\n")
    monkeypatch.setenv("PTV_STYLE_PACKS_DIR", str(packs_dir))
    return "testpack"


# ---------------------------------------------------------------------------
# golden examples parse
# ---------------------------------------------------------------------------
def test_golden_examples_parse() -> None:
    files = sorted(EXAMPLES.glob("*.collage.json"))
    assert len(files) == 4, files
    for f in files:
        spec = load_collage_spec(f)
        assert spec.spec_version == 1
        assert spec.elements


# ---------------------------------------------------------------------------
# build a small scene
# ---------------------------------------------------------------------------
def _small_spec(run_dir: Path, style_pack: str) -> None:
    _make_png(run_dir / "assets" / "seg" / "bg.png")
    spec = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 10.0,
        "fps": 30,
        "style_pack": style_pack,
        "assets": [{"id": "bg", "generate": {"prompt": "x", "width": 16, "height": 16}}],
        "elements": [
            {
                "id": "bg_layer",
                "type": "layer",
                "asset_id": "bg",
                "x": 0.5,
                "y": 0.5,
                "width": 1.0,
                "enter": {"at_frac": 0.5},
            },
            {"id": "cap", "type": "typewriter", "text": "Hi", "x": 0.1, "y": 0.8, "font": "mono"},
            {"id": "tag", "type": "label", "text": "note", "x": 0.2, "y": 0.2},
        ],
    }
    (run_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec))


def test_build_small_scene(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    _small_spec(run_dir, style_pack)
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")

    html = build_collage_html(
        spec=spec, run_dir=run_dir, narration_text="Hi there", duration_seconds=10.0, words=None
    )

    # seek contract present
    assert "window.seek" in html
    assert "__SCENE__" in html
    # resolved time: at_frac 0.5 of 10s -> 5.0 lands in the compiled scene JSON
    assert '"enter": 5.0' in html
    # resolved palette, no unresolved tokens
    assert "#F0EEE6" in html
    assert "$palette." not in html
    # no network references at all
    assert "http://" not in html
    assert "https://" not in html
    # relative asset path per CONTRACTS §2
    assert "../../assets/seg/bg.png" in html
    # bundled fonts, base64-embedded, listed for sceneReady
    assert "@font-face" in html
    assert "TestMono" in html
    assert "data:font/woff2;base64," in html
    assert '"fonts":' in html or '"fonts": ' in html
    # pack css inlined
    assert "testpack css" in html

    # the built HTML passes the collage validator branch
    scene = SceneSpec(
        segment_id="seg",
        visual_engine="collage",
        code=html,
        target_duration_seconds=10.0,
        narration_text="Hi there",
        description="Collage scene seg",
    )
    validate(scene)  # must not raise


# ---------------------------------------------------------------------------
# error paths (fail loudly)
# ---------------------------------------------------------------------------
def test_missing_asset_raises(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "scenes").mkdir(parents=True)
    spec_json = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 8.0,
        "style_pack": style_pack,
        "assets": [{"id": "ghost", "generate": {"prompt": "x", "width": 16, "height": 16}}],
        "elements": [
            {"id": "l", "type": "layer", "asset_id": "ghost", "x": 0.5, "y": 0.5, "width": 1.0}
        ],
    }
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec_json))
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")

    with pytest.raises(ValueError, match="ghost"):
        build_collage_html(spec=spec, run_dir=run_dir, narration_text="", duration_seconds=8.0, words=None)


def test_unknown_palette_token_raises(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "scenes").mkdir(parents=True)
    spec_json = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 8.0,
        "style_pack": style_pack,
        "elements": [
            {"id": "tag", "type": "label", "text": "hi", "x": 0.2, "y": 0.2, "color": "$palette.bogus"}
        ],
    }
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec_json))
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")

    with pytest.raises(ValueError, match="bogus"):
        build_collage_html(spec=spec, run_dir=run_dir, narration_text="", duration_seconds=8.0, words=None)


def test_missing_style_pack_for_token_raises(tmp_path: Path) -> None:
    """A palette token with no resolvable pack is a hard error (no default palette)."""
    run_dir = tmp_path / "run"
    (run_dir / "scenes").mkdir(parents=True)
    spec_json = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 8.0,
        "style_pack": None,
        "background": "#101010",
        "elements": [
            {"id": "tag", "type": "label", "text": "hi", "x": 0.2, "y": 0.2, "color": "$palette.ink"}
        ],
    }
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec_json))
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")

    with pytest.raises(ValueError, match="style pack|style_pack"):
        build_collage_html(spec=spec, run_dir=run_dir, narration_text="", duration_seconds=8.0, words=None)


def test_missing_fonts_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pack whose fonts/ dir lacks the needed woff2 is a hard error."""
    packs_dir = tmp_path / "style_packs"
    pack = packs_dir / "nofonts"
    (pack / "fonts").mkdir(parents=True)  # empty — no woff2
    (pack / "tokens.json").write_text(
        json.dumps(
            {
                "palette": {"paper": "#F0EEE6", "ink": "#1F1E1B"},
                "type": {"sans": "MissingSans", "mono": "MissingMono"},
            }
        )
    )
    monkeypatch.setenv("PTV_STYLE_PACKS_DIR", str(packs_dir))

    run_dir = tmp_path / "run"
    (run_dir / "scenes").mkdir(parents=True)
    spec_json = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 8.0,
        "style_pack": "nofonts",
        "elements": [{"id": "tag", "type": "label", "text": "hi", "x": 0.2, "y": 0.2}],
    }
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec_json))
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")

    with pytest.raises(ValueError, match="woff2|font"):
        build_collage_html(spec=spec, run_dir=run_dir, narration_text="", duration_seconds=8.0, words=None)


# ---------------------------------------------------------------------------
# validator determinism ban
# ---------------------------------------------------------------------------
def test_validator_bans_math_random() -> None:
    code = (
        "<!doctype html><html><body><script>"
        "window.__SCENE__={duration:1,fps:30};"
        "window.seek=function(t){var x=Math.random();};"
        "</script></body></html>"
    )
    scene = SceneSpec(
        segment_id="x",
        visual_engine="collage",
        code=code,
        target_duration_seconds=1.0,
        narration_text="",
        description="d",
    )
    with pytest.raises(ValidationError, match="Math.random"):
        validate(scene)


def test_validator_requires_seek() -> None:
    code = "<!doctype html><html><body><script>window.__SCENE__={};</script></body></html>"
    scene = SceneSpec(
        segment_id="x",
        visual_engine="collage",
        code=code,
        target_duration_seconds=1.0,
        narration_text="",
        description="d",
    )
    with pytest.raises(ValidationError, match="window.seek"):
        validate(scene)


# ---------------------------------------------------------------------------
# cmd staleness / missing-alignment logic (no render)
# ---------------------------------------------------------------------------
def _at_word_run(tmp_path: Path) -> tuple[Path, Path]:
    run_dir = tmp_path / "run"
    (run_dir / "scenes").mkdir(parents=True)
    (run_dir / "audio").mkdir(parents=True)
    spec_json = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 10.0,
        "elements": [
            {
                "id": "cap",
                "type": "typewriter",
                "text": "hi",
                "x": 0.1,
                "y": 0.8,
                "font": "mono",
                "enter": {"at_word": "clouds"},
            }
        ],
    }
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec_json))
    script = {
        "style_pack": "testpack",
        "segments": [
            {"segment_id": "seg", "visual_engine": "collage", "narration_text": "clouds drift by"}
        ],
    }
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script))
    return script_path, run_dir


def test_cmd_stale_alignment_hard_error(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from src.collage.cmd import run_collage

    script_path, run_dir = _at_word_run(tmp_path)
    wav = run_dir / "audio" / "seg.wav"
    wav.write_bytes(b"RIFF....")
    (run_dir / "audio" / "audio_manifest.json").write_text(
        json.dumps({"seg": {"duration_seconds": 10.0, "audio_path": str(wav)}})
    )
    alignment = run_dir / "audio" / "alignment.json"
    alignment.write_text(json.dumps({"seg": {"words": [{"w": "clouds", "start": 1.0, "end": 1.2}]}}))
    # make alignment OLDER than the wav
    wav_mtime = wav.stat().st_mtime
    os.utime(alignment, (wav_mtime - 500, wav_mtime - 500))

    with pytest.raises(SystemExit) as exc:
        run_collage(script_path, run_dir)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    payload = json.loads([ln for ln in out.splitlines() if ln.strip().startswith("{")][-1])
    assert payload["results"]["seg"]["success"] is False
    assert "stale" in payload["results"]["seg"]["error"]


def test_cmd_missing_alignment_hard_error(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from src.collage.cmd import run_collage

    script_path, run_dir = _at_word_run(tmp_path)
    (run_dir / "audio" / "audio_manifest.json").write_text(
        json.dumps({"seg": {"duration_seconds": 10.0}})
    )
    # no alignment.json at all

    with pytest.raises(SystemExit) as exc:
        run_collage(script_path, run_dir)
    assert exc.value.code == 1

    out = capsys.readouterr().out
    payload = json.loads([ln for ln in out.splitlines() if ln.strip().startswith("{")][-1])
    assert payload["results"]["seg"]["success"] is False
    assert "alignment is missing" in payload["results"]["seg"]["error"]


def test_cmd_no_collage_work_skips(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from src.collage.cmd import run_collage

    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps({"segments": [{"segment_id": "a", "visual_engine": "html"}]}))
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    run_collage(script_path, run_dir)  # must NOT raise SystemExit
    out = capsys.readouterr().out
    payload = json.loads([ln for ln in out.splitlines() if ln.strip().startswith("{")][-1])
    assert payload["skipped"] is True


# ---------------------------------------------------------------------------
# subject motion: move keyframes + oscillation
# ---------------------------------------------------------------------------
def test_layer_move_and_oscillate_compile(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    _make_png(run_dir / "assets" / "seg" / "ship.png")
    spec_dict = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 10.0,
        "fps": 30,
        "style_pack": style_pack,
        "assets": [{"id": "ship", "generate": {"prompt": "x", "width": 16, "height": 16}}],
        "elements": [
            {
                "id": "ship_layer",
                "type": "layer",
                "asset_id": "ship",
                "x": 0.9,
                "y": 0.5,
                "width": 0.4,
                # keys authored with sparse fields: y inherits base, then the
                # second key inherits the first key's y — fill-forward.
                "move": [
                    {"time": {"at_frac": 0.0}, "x": 1.2},
                    {"time": {"at_frac": 0.8}, "x": 0.3, "rotate": 5.0},
                ],
                "oscillate": {"axis": "rotate", "amplitude": 3.0, "period": 3.0},
            },
        ],
    }
    (run_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec_dict))
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")

    html = build_collage_html(
        spec=spec, run_dir=run_dir, narration_text="", duration_seconds=10.0, words=None
    )

    # move keys resolved to seconds with fill-forward pose fields
    assert '"move":' in html
    assert '"t": 0.0' in html and '"t": 8.0' in html
    assert '"rotate": 5.0' in html  # second key's explicit rotate
    # oscillation passes through
    assert '"oscillate":' in html and '"axis": "rotate"' in html
    # runtime motion machinery present
    assert "poseAt" in html


def test_oscillation_amplitude_limit_rejected() -> None:
    from src.collage.spec import Oscillation

    with pytest.raises(ValueError, match="amplitude"):
        Oscillation(axis="y", amplitude=0.9, period=2.0)


def test_moving_scene_renders_end_to_end(tmp_path: Path, style_pack: str) -> None:
    """A spec using move+oscillate must build AND render through the real
    headless renderer — proving the new runtime pose machinery executes under
    the deterministic seek contract, not just that it serializes."""
    from src.animation.html_renderer import render_html
    from src.animation.models import SceneSpec as RenderSceneSpec

    run_dir = tmp_path / "run"
    _make_png(run_dir / "assets" / "seg" / "ship.png")
    spec_dict = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 1.0,
        "fps": 24,
        "style_pack": style_pack,
        "assets": [{"id": "ship", "generate": {"prompt": "x", "width": 16, "height": 16}}],
        "elements": [
            {
                "id": "ship_layer",
                "type": "layer",
                "asset_id": "ship",
                "x": 0.9, "y": 0.5, "width": 0.4,
                "move": [
                    {"time": {"at_frac": 0.0}, "x": 1.1},
                    {"time": {"at_frac": 1.0}, "x": 0.2},
                ],
                "oscillate": {"axis": "rotate", "amplitude": 3.0, "period": 0.8},
            },
        ],
    }
    (run_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec_dict))
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")
    html = build_collage_html(
        spec=spec, run_dir=run_dir, narration_text="", duration_seconds=1.0, words=None
    )

    work_dir = run_dir / "scenes" / "seg_render"
    work_dir.mkdir(parents=True, exist_ok=True)
    scene = RenderSceneSpec(
        segment_id="seg",
        visual_engine="collage",
        code=html,
        target_duration_seconds=1.0,
        narration_text="",
        description="subject-motion render smoke",
    )
    result = render_html(scene, work_dir, (320, 180), 24, 120)
    assert result.success, result.error_message
    assert result.video_path.exists()
