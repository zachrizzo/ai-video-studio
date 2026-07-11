"""Tests for the WS-agent helpers in src/studio/agent.py.

Only the pure/filesystem helpers are covered — driving handle_ws needs a live
Claude Agent SDK session. The key regression here: the end-of-turn run scan
must only surface runs the turn actually touched, so concurrent terminal work
can't hijack the viewer or mis-bind a chat.
"""

from pathlib import Path

from src.studio.agent import _extract_run_id, _select_changed_runs, _snapshot_run_scripts


# ---------------------------------------------------------------------------
# _extract_run_id
# ---------------------------------------------------------------------------


def test_extract_run_id_from_typed_field() -> None:
    assert _extract_run_id({"run_id": "run_abcdef12"}) == "run_abcdef12"


def test_extract_run_id_from_command_and_paths() -> None:
    assert _extract_run_id({"command": "ls /tmp/x/run_deadbeef/images"}) == "run_deadbeef"
    assert _extract_run_id({"file_path": "/x/run_0123ab/script.json"}) == "run_0123ab"


def test_extract_run_id_none_for_runless_input() -> None:
    # One-shot generate_video / tts inputs carry no run id; the hook must not
    # emit artifact frames for them (no stale-run fallback).
    assert _extract_run_id({"prompt": "a knight rides at dawn"}) is None
    assert _extract_run_id({}) is None


# ---------------------------------------------------------------------------
# _select_changed_runs
# ---------------------------------------------------------------------------


def test_select_changed_runs_requires_turn_activity() -> None:
    before = {"run_aaa111": 1.0, "run_bbb222": 1.0}
    after = {"run_aaa111": 2.0, "run_bbb222": 5.0, "run_ccc333": 9.0}

    # run_bbb222/run_ccc333 changed on disk but this turn never touched them
    # (e.g. a terminal pipeline run) — they must not steer the viewer.
    changed = _select_changed_runs(before, after, {"run_aaa111"})

    assert changed == [("run_aaa111", 2.0)]


def test_select_changed_runs_orders_newest_first() -> None:
    after = {"run_aaa111": 1.0, "run_bbb222": 3.0}

    changed = _select_changed_runs({}, after, {"run_aaa111", "run_bbb222"})

    assert changed == [("run_bbb222", 3.0), ("run_aaa111", 1.0)]


def test_select_changed_runs_empty_when_nothing_seen() -> None:
    assert _select_changed_runs({}, {"run_abc123": 1.0}, set()) == []


def test_select_changed_runs_ignores_unchanged_seen_runs() -> None:
    before = {"run_abc123": 5.0}
    after = {"run_abc123": 5.0}

    assert _select_changed_runs(before, after, {"run_abc123"}) == []


# ---------------------------------------------------------------------------
# _snapshot_run_scripts
# ---------------------------------------------------------------------------


def test_snapshot_run_scripts_uses_runs_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(tmp_path))
    run_dir = tmp_path / "run_abc123"
    run_dir.mkdir()
    (run_dir / "script.json").write_text("{}")
    (tmp_path / "not_a_run").mkdir()
    (tmp_path / "run_noscript").mkdir()

    snapshot = _snapshot_run_scripts()

    assert set(snapshot) == {"run_abc123"}
    assert snapshot["run_abc123"] > 0
