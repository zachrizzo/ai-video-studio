"""Tests for run discovery and media URL resolution in the Studio backend."""

import json
import os
import time
from pathlib import Path

import pytest

from src.studio import runs
from src.studio.runs import _runs_root, _scene_url, get_run, list_runs


def _touch_render(run_dir: Path, segment_id: str, suffix: str) -> Path:
    render_dir = run_dir / "scenes" / f"{segment_id}_render"
    render_dir.mkdir(parents=True, exist_ok=True)
    mp4 = render_dir / f"{segment_id}_{suffix}.mp4"
    mp4.write_bytes(b"fake")
    return mp4


def test_scene_url_finds_html_render(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()
    _touch_render(run_dir, "seg01_intro", "html")

    url = _scene_url(run_dir, "seg01_intro")

    assert url == "/media/run_abc/scenes/seg01_intro_render/seg01_intro_html.mp4"


def test_scene_url_finds_manim_render(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()
    _touch_render(run_dir, "seg02_proof", "manim")

    url = _scene_url(run_dir, "seg02_proof")

    assert url == "/media/run_abc/scenes/seg02_proof_render/seg02_proof_manim.mp4"


def test_scene_url_finds_collage_render(tmp_path: Path) -> None:
    """Regression test: the collage engine (src/collage/cmd.py) writes
    ``{segment_id}_collage.mp4``, so _scene_url must recognize that suffix
    too, or every collage/diagram segment shows up as "no clip yet" in the
    storyboard despite having rendered successfully.
    """
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()
    _touch_render(run_dir, "seg01_cold_open", "collage")

    url = _scene_url(run_dir, "seg01_cold_open")

    assert url == "/media/run_abc/scenes/seg01_cold_open_render/seg01_cold_open_collage.mp4"


def test_scene_url_none_when_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "run_abc"
    run_dir.mkdir()

    assert _scene_url(run_dir, "seg99_missing") is None


# ---------------------------------------------------------------------------
# runs root + ordering
# ---------------------------------------------------------------------------


def _make_run(root: Path, run_id: str, title: str, mtime: float | None = None) -> None:
    d = root / run_id
    d.mkdir(parents=True)
    script = d / "script.json"
    script.write_text(json.dumps({"title": title, "segments": []}))
    if mtime is not None:
        os.utime(script, (mtime, mtime))


def test_list_runs_sorted_by_script_mtime_newest_first(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path))
    now = time.time()
    # Lexicographic order (aaa < bbb < zzz) deliberately disagrees with
    # recency so the sort has to be by mtime, not by id.
    _make_run(tmp_path, "run_aaa111", "Oldest", mtime=now - 300)
    _make_run(tmp_path, "run_zzz999", "Middle", mtime=now - 200)
    _make_run(tmp_path, "run_bbb222", "Newest", mtime=now - 100)

    assert [r["id"] for r in list_runs()] == ["run_bbb222", "run_zzz999", "run_aaa111"]


def test_runs_root_env_override(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path / "custom"))
    assert _runs_root() == tmp_path / "custom"


def test_runs_root_defaults_to_studio_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("STUDIO_RUNS_DIR", raising=False)
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    # Keep the real /tmp legacy dir out of this test.
    monkeypatch.setattr(runs, "_LEGACY_RUNS_ROOT", tmp_path / "no-legacy")
    monkeypatch.setattr(runs, "_legacy_migration_done", False)

    assert _runs_root() == tmp_path / "home" / "runs"


def test_runs_root_migrates_legacy_runs(tmp_path: Path, monkeypatch) -> None:
    legacy = tmp_path / "legacy"
    _make_run(legacy, "run_old111", "Old run")
    monkeypatch.delenv("STUDIO_RUNS_DIR", raising=False)
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runs, "_LEGACY_RUNS_ROOT", legacy)
    monkeypatch.setattr(runs, "_legacy_migration_done", False)

    root = _runs_root()

    assert root == tmp_path / "home" / "runs"
    assert (root / "run_old111" / "script.json").exists()
    assert not (legacy / "run_old111").exists()


def test_runs_root_migration_skipped_when_target_populated(tmp_path: Path, monkeypatch) -> None:
    legacy = tmp_path / "legacy"
    _make_run(legacy, "run_old111", "Old run")
    home_runs = tmp_path / "home" / "runs"
    _make_run(home_runs, "run_new222", "Existing run")
    monkeypatch.delenv("STUDIO_RUNS_DIR", raising=False)
    monkeypatch.setenv("STUDIO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(runs, "_LEGACY_RUNS_ROOT", legacy)
    monkeypatch.setattr(runs, "_legacy_migration_done", False)

    root = _runs_root()

    # Never clobber a populated durable root; legacy data stays put.
    assert (root / "run_new222").exists()
    assert not (root / "run_old111").exists()
    assert (legacy / "run_old111" / "script.json").exists()


def test_runs_root_migration_not_triggered_by_env_override(tmp_path: Path, monkeypatch) -> None:
    legacy = tmp_path / "legacy"
    _make_run(legacy, "run_old111", "Old run")
    override = tmp_path / "override"
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(override))
    monkeypatch.setattr(runs, "_LEGACY_RUNS_ROOT", legacy)
    monkeypatch.setattr(runs, "_legacy_migration_done", False)

    assert _runs_root() == override
    assert (legacy / "run_old111" / "script.json").exists()


# ---------------------------------------------------------------------------
# get_run duration math: failed segments + the "_voice" meta key must not
# distort per-segment or total durations.
# ---------------------------------------------------------------------------


def test_get_run_duration_falls_back_for_failed_segment_and_totals_per_segment(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path))
    run_dir = tmp_path / "run_mix001"
    run_dir.mkdir()
    (run_dir / "audio").mkdir()

    script = {
        "title": "Mixed Durations",
        "segments": [
            {
                "segment_id": "seg_ok",
                "section_title": "Intro",
                "narration_text": "This one synthesized fine.",
                "estimated_duration_seconds": 4.0,
                "animation_cues": [],
            },
            {
                "segment_id": "seg_failed",
                "section_title": "Outro",
                "narration_text": "This one failed synthesis.",
                "estimated_duration_seconds": 6.0,
                "animation_cues": [],
            },
        ],
    }
    (run_dir / "script.json").write_text(json.dumps(script))

    audio_manifest = {
        "seg_ok": {"audio_path": "audio_seg_ok.wav", "duration_seconds": 4.2, "qa_issues": []},
        "seg_failed": {
            "audio_path": "audio_seg_failed.wav",
            "duration_seconds": 0,
            "failed": True,
            "error": "tts provider timeout",
            "qa_issues": [],
        },
        "_voice": {"provider": "qwen", "speaker": "serena", "language": "english"},
    }
    (run_dir / "audio" / "audio_manifest.json").write_text(json.dumps(audio_manifest))

    result = get_run("run_mix001")

    assert result is not None
    segments_by_id = {s["segment_id"]: s for s in result["segments"]}
    # Real manifest duration wins for the successful segment.
    assert segments_by_id["seg_ok"]["duration_seconds"] == pytest.approx(4.2)
    # Failed segment falls back to the script estimate instead of showing 0:00.
    assert segments_by_id["seg_failed"]["duration_seconds"] == pytest.approx(6.0)
    # Total is the sum of the (corrected) per-segment durations, not a raw
    # sum of audio_manifest.values() (which would include the "_voice" 0 and
    # the failed segment's zeroed duration).
    assert result["total_duration_seconds"] == pytest.approx(4.2 + 6.0)
