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


# ---------------------------------------------------------------------------
# Vox finishing techniques: stutter (motion on twos), lens, transitions
# ---------------------------------------------------------------------------
def _finishing_spec(
    run_dir: Path,
    style_pack: str,
    *,
    duration: float = 2.0,
    stutter_fps=None,
    lens=None,
    transition_in=None,
    transition_out=None,
    move=None,
) -> None:
    """Write a one-layer collage spec, opting into whichever finishing knobs
    the caller passes. The layer moves across the scene so stutter is
    observable in its position."""
    _make_png(run_dir / "assets" / "seg" / "bg.png")
    layer = {
        "id": "bg_layer",
        "type": "layer",
        "asset_id": "bg",
        "x": 0.2,
        "y": 0.5,
        "width": 0.4,
        "depth": 0.0,
    }
    if move is not None:
        layer["move"] = move
    spec = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": duration,
        "fps": 24,
        "style_pack": style_pack,
        "assets": [{"id": "bg", "generate": {"prompt": "x", "width": 16, "height": 16}}],
        "elements": [layer],
    }
    if stutter_fps is not None:
        spec["stutter_fps"] = stutter_fps
    if lens is not None:
        spec["lens"] = lens
    if transition_in is not None:
        spec["transition_in"] = transition_in
    if transition_out is not None:
        spec["transition_out"] = transition_out
    (run_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec))


def _build_finishing(run_dir: Path, duration: float = 2.0) -> str:
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")
    return build_collage_html(
        spec=spec, run_dir=run_dir, narration_text="", duration_seconds=duration, words=None
    )


def test_stutter_fps_compiles_into_scene(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    _finishing_spec(run_dir, style_pack, stutter_fps=12)
    html = _build_finishing(run_dir)
    assert '"stutterFps": 12' in html
    # runtime carries the on-twos quantization
    assert "stutterFps" in html and "Math.floor" in html


def test_stutter_fps_defaults_off(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    _finishing_spec(run_dir, style_pack)
    html = _build_finishing(run_dir)
    assert '"stutterFps": null' in html


def test_lens_compiles_into_scene(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    _finishing_spec(run_dir, style_pack, lens=True)
    html = _build_finishing(run_dir)
    assert '"lens": true' in html
    assert "collage-lens" in html


def test_lens_defaults_off(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    _finishing_spec(run_dir, style_pack)
    html = _build_finishing(run_dir)
    assert '"lens": false' in html


def test_transitions_compile_into_scene(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    _finishing_spec(
        run_dir,
        style_pack,
        transition_in={"seconds": 0.5, "blur_px": 14, "push": 0.06},
        transition_out={"seconds": 0.4},
    )
    html = _build_finishing(run_dir)
    assert '"transitionIn":' in html
    assert '"blurPx": 14' in html
    # transition_out uses defaults for the omitted fields
    assert '"transitionOut":' in html
    assert '"seconds": 0.4' in html


def test_transitions_default_null(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    _finishing_spec(run_dir, style_pack)
    html = _build_finishing(run_dir)
    assert '"transitionIn": null' in html
    assert '"transitionOut": null' in html


# ---- behavioral (headless Playwright over the seek contract) --------------
def _probe_seek(html_file: Path, script: str):
    """Load a built collage HTML headless, await sceneReady, then run a JS
    function body that may call window.seek and returns a JSON value."""
    import asyncio

    from playwright.async_api import async_playwright

    from src.animation.frame_renderer import _SCENE_READY_JS, _chromium_launch_kwargs

    async def _run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(**_chromium_launch_kwargs())
            context = await browser.new_context(viewport={"width": 640, "height": 360})
            page = await context.new_page()
            await page.goto(f"file://{html_file.absolute()}")
            await page.evaluate(_SCENE_READY_JS)
            result = await page.evaluate("() => {" + script + "}")
            await browser.close()
            return result

    return asyncio.run(_run())


def _write_html(run_dir: Path, html: str) -> Path:
    work = run_dir / "scenes" / "seg_render"
    work.mkdir(parents=True, exist_ok=True)
    html_file = work / "seg.html"
    html_file.write_text(html)
    return html_file


def test_stutter_quantizes_motion(tmp_path: Path, style_pack: str) -> None:
    """A moving layer holds its pose for a whole 1/stutter_fps frame, then
    jumps — proving seek time is quantized on twos, not sampled continuously."""
    run_dir = tmp_path / "run"
    _finishing_spec(
        run_dir,
        style_pack,
        stutter_fps=12,
        move=[
            {"time": {"at_frac": 0.0}, "x": 0.2},
            {"time": {"at_frac": 1.0}, "x": 0.8},
        ],
    )
    html_file = _write_html(run_dir, _build_finishing(run_dir))
    # Sample around mid-scene where the eased motion is steepest, so one
    # stutter bucket of movement is clearly visible.
    r = _probe_seek(
        html_file,
        "const L = t => { window.seek(t);"
        " return document.querySelector('.collage-layer').getBoundingClientRect().left; };"
        " return { a: L(1.00), b: L(1.04), c: L(1.12) };",
    )
    # 1.00 and 1.04 fall in the same 1/12s bucket (floor(t*12)==12) -> identical
    assert abs(r["a"] - r["b"]) < 1e-6, r
    # 1.12 crosses into the next bucket (floor(1.12*12)==13) -> moved
    assert abs(r["c"] - r["a"]) > 0.5, r


def test_lens_overlay_present_only_when_enabled(tmp_path: Path, style_pack: str) -> None:
    run_dir = tmp_path / "run"
    _finishing_spec(run_dir, style_pack, lens=True)
    html_file = _write_html(run_dir, _build_finishing(run_dir))
    present = _probe_seek(
        html_file, "window.seek(0); return !!document.querySelector('.collage-lens');"
    )
    assert present is True

    run_dir2 = tmp_path / "run2"
    _finishing_spec(run_dir2, style_pack)  # lens off
    html_file2 = _write_html(run_dir2, _build_finishing(run_dir2))
    absent = _probe_seek(
        html_file2, "window.seek(0); return !!document.querySelector('.collage-lens');"
    )
    assert absent is False


def test_transition_in_blur_peaks_at_boundary(tmp_path: Path, style_pack: str) -> None:
    """The frame blur is heavy at the scene's opening (t≈0) and clears by
    mid-scene — the blur that masks the cut into this scene."""
    run_dir = tmp_path / "run"
    _finishing_spec(
        run_dir, style_pack, duration=2.0, transition_in={"seconds": 0.5, "blur_px": 14}
    )
    html_file = _write_html(run_dir, _build_finishing(run_dir, duration=2.0))
    r = _probe_seek(
        html_file,
        "const blur = t => { window.seek(t);"
        " const f = getComputedStyle(document.querySelector('.collage-frame')).filter;"
        " const m = /blur\\(([0-9.]+)px\\)/.exec(f); return m ? parseFloat(m[1]) : 0; };"
        " return { boundary: blur(0.0), mid: blur(1.0) };",
    )
    assert r["boundary"] > 8.0, r
    assert r["mid"] < 1.0, r


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


# ---------------------------------------------------------------------------
# native video layer (recorded screen-capture clips as layers)
# ---------------------------------------------------------------------------
def _make_test_video(path: Path, *, duration: float = 2.0) -> None:
    """Render a short test clip with a moving pattern + on-screen timer so
    every frame differs over time (ffmpeg lavfi testsrc)."""
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size=320x180:rate=30",
            "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _video_spec(
    run_dir: Path, style_pack: str, *, src: str = "assets/shared/clip.webm", duration: float = 1.0
) -> dict:
    spec = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": duration,
        "fps": 24,
        "style_pack": style_pack,
        "assets": [{"id": "clip", "src": src}],
        "elements": [
            {
                "id": "screencap",
                "type": "video",
                "asset_id": "clip",
                "x": 0.5, "y": 0.5, "width": 0.8,
                "clip_start": 0.0,
                "rate": 1.0,
            },
        ],
    }
    (run_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (run_dir / "scenes" / "seg.collage.json").write_text(json.dumps(spec))
    return spec


def test_video_layer_compiles(tmp_path: Path, style_pack: str) -> None:
    """A spec with a video element + video asset compiles; the built HTML
    carries the video url, the timing fields, and the runtime buildVideoLayer."""
    run_dir = tmp_path / "run"
    # asset only needs to EXIST for the file check; contents are irrelevant here
    (run_dir / "assets" / "shared").mkdir(parents=True)
    (run_dir / "assets" / "shared" / "clip.webm").write_bytes(b"not-a-real-webm")
    _video_spec(run_dir, style_pack)
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")

    html = build_collage_html(
        spec=spec, run_dir=run_dir, narration_text="", duration_seconds=1.0, words=None
    )

    # relative, self-contained video URL (CONTRACTS §2)
    assert "../../assets/shared/clip.webm" in html
    assert "http://" not in html and "https://" not in html
    # video timing contract fields present in the compiled scene JSON
    assert '"videoUrl":' in html
    assert '"clip_start":' in html
    assert '"rate":' in html
    assert '"start":' in html
    # runtime carries the video-layer builder + the async seek machinery
    assert "buildVideoLayer" in html
    # still passes the collage validator (seek contract, no network, deterministic)
    scene = SceneSpec(
        segment_id="seg",
        visual_engine="collage",
        code=html,
        target_duration_seconds=1.0,
        narration_text="",
        description="video layer",
    )
    validate(scene)  # must not raise


def test_video_layer_rejects_unknown_field() -> None:
    """extra='forbid' — an unknown field on a video element fails loudly."""
    from pydantic import ValidationError as PydanticValidationError

    from src.collage.spec import CollageSpec

    spec_json = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 1.0,
        "assets": [{"id": "clip", "src": "assets/shared/clip.webm"}],
        "elements": [
            {
                "id": "v", "type": "video", "asset_id": "clip",
                "x": 0.5, "y": 0.5, "width": 0.8,
                "loop": True,  # not a real field
            }
        ],
    }
    with pytest.raises(PydanticValidationError, match="loop"):
        CollageSpec.model_validate(spec_json)


def test_video_element_requires_video_asset(tmp_path: Path) -> None:
    """A video element must reference an asset whose src is a video file."""
    from pydantic import ValidationError as PydanticValidationError

    from src.collage.spec import CollageSpec

    spec_json = {
        "spec_version": 1,
        "segment_id": "seg",
        "duration_seconds": 1.0,
        # image asset (generate) — not a video
        "assets": [{"id": "pic", "generate": {"prompt": "x", "width": 16, "height": 16}}],
        "elements": [
            {"id": "v", "type": "video", "asset_id": "pic",
             "x": 0.5, "y": 0.5, "width": 0.8}
        ],
    }
    with pytest.raises(PydanticValidationError, match="video"):
        CollageSpec.model_validate(spec_json)


def test_video_defaults(tmp_path: Path) -> None:
    """Video pose defaults: depth 0.0, scale 1.0, rate 1.0, clip_start 0.0."""
    from src.collage.spec import CollageSpec, VideoLayerElement

    spec = CollageSpec.model_validate(
        {
            "spec_version": 1,
            "segment_id": "seg",
            "duration_seconds": 1.0,
            "assets": [{"id": "clip", "src": "assets/shared/clip.mp4"}],
            "elements": [
                {"id": "v", "type": "video", "asset_id": "clip",
                 "x": 0.5, "y": 0.5, "width": 0.8}
            ],
        }
    )
    el = spec.elements[0]
    assert isinstance(el, VideoLayerElement)
    assert el.depth == 0.0
    assert el.scale == 1.0
    assert el.rate == 1.0
    assert el.clip_start == 0.0
    assert el.start is None


def test_video_layer_renders_and_advances(tmp_path: Path, style_pack: str) -> None:
    """End-to-end proof of frame-accurate video playback:

    1. A scene with a video layer builds AND renders through the real headless
       frame renderer (result.success + mp4 with the expected frame count).
    2. Driving the seek contract to two different times decodes two DIFFERENT
       source frames — the video advances frame-accurately, it is not frozen on
       frame 0. Proven both by `video.currentTime` advancing to the expected
       clamped value AND by the rendered pixels differing.
    """
    import asyncio
    import hashlib

    from playwright.async_api import async_playwright

    from src.animation.frame_renderer import _SCENE_READY_JS, _chromium_launch_kwargs
    from src.animation.html_renderer import render_html
    from src.animation.models import SceneSpec as RenderSceneSpec

    run_dir = tmp_path / "run"
    _make_test_video(run_dir / "assets" / "shared" / "clip.webm", duration=2.0)
    # 2.0s scene so seek(1.5) is un-clamped (seek clamps t to [0, duration]).
    _video_spec(run_dir, style_pack, duration=2.0)
    spec = load_collage_spec(run_dir / "scenes" / "seg.collage.json")
    html = build_collage_html(
        spec=spec, run_dir=run_dir, narration_text="", duration_seconds=2.0, words=None
    )

    work_dir = run_dir / "scenes" / "seg_render"
    work_dir.mkdir(parents=True, exist_ok=True)

    # (1) end-to-end render
    scene = RenderSceneSpec(
        segment_id="seg",
        visual_engine="collage",
        code=html,
        target_duration_seconds=2.0,
        narration_text="",
        description="video layer render smoke",
    )
    result = render_html(scene, work_dir, (320, 180), 24, 120)
    assert result.success, result.error_message
    assert result.video_path.exists()
    # 2.0s @ 24fps -> exactly 48 frames (render_frames already asserts this,
    # but be explicit that the mp4 is well-formed).
    from src.animation.frame_renderer import _probe_frame_count

    assert _probe_frame_count(result.video_path) == 48

    # (2a) frame-accuracy through the REAL render path: the frame renderer drives
    # `await page.evaluate("window.seek(frame/fps)")` (bare expression). If the
    # returned Promise were not awaited, every clip frame would freeze on frame 0
    # and — since the pose is static and the grain is constant in t — all output
    # frames would be identical. Two frames far apart in the OUTPUT mp4 differing
    # proves the video advanced frame-accurately.
    import subprocess

    def _frame_png(video: Path, ts: float) -> bytes:
        out = tmp_path / f"probe_{ts}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(ts),
             "-i", str(video), "-frames:v", "1", str(out)],
            check=True, capture_output=True,
        )
        return out.read_bytes()

    assert _frame_png(result.video_path, 0.1) != _frame_png(result.video_path, 1.5)

    # (2b) frame-accuracy: seek to two times, read currentTime + a pixel signature
    html_file = work_dir / "seg.html"

    async def _probe():
        async with async_playwright() as p:
            browser = await p.chromium.launch(**_chromium_launch_kwargs())
            context = await browser.new_context(viewport={"width": 320, "height": 180})
            page = await context.new_page()
            await page.goto(f"file://{html_file.absolute()}")
            await page.evaluate(_SCENE_READY_JS)
            # seek RETURNS A PROMISE when a scene has video — await it so the
            # target frame is decoded before we read state / screenshot.
            ct0 = await page.evaluate(
                "async () => { await window.seek(0.1);"
                " return document.querySelector('video').currentTime; }"
            )
            shot0 = await page.screenshot(type="png")
            ct1 = await page.evaluate(
                "async () => { await window.seek(1.5);"
                " return document.querySelector('video').currentTime; }"
            )
            shot1 = await page.screenshot(type="png")
            await browser.close()
            return ct0, ct1, shot0, shot1

    ct0, ct1, shot0, shot1 = asyncio.run(_probe())

    # currentTime is a pure function of t: clip_start(0) + max(0, t-0)*rate(1)
    assert abs(ct0 - 0.1) < 0.06, ct0
    assert abs(ct1 - 1.5) < 0.06, ct1
    assert ct1 > ct0
    # ...and the decoded pixels actually changed (not stuck on frame 0)
    assert hashlib.sha256(shot0).digest() != hashlib.sha256(shot1).digest()
