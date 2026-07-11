"""Tests for the typed studio MCP tools (src/studio/agent_tools.py)."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from src.studio import agent_tools


def _call(name: str, args: dict) -> dict:
    return asyncio.run(agent_tools.TOOL_HANDLERS[name](args))


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def runs_root(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setenv("STUDIO_RUNS_DIR", str(root))
    return root


@pytest.fixture
def run_dir(runs_root):
    rd = runs_root / "run_abc123"
    rd.mkdir()
    (rd / "script.json").write_text('{"title": "t", "segments": []}')
    return rd


class FakeProcess:
    def __init__(self, returncode: int = 0, output: bytes = b"ok\n"):
        self.returncode = returncode
        self._output = output

    async def communicate(self):
        return self._output, None


@pytest.fixture
def fake_exec(monkeypatch):
    calls: list[dict] = []

    def install(returncode: int = 0, output: bytes = b"ok\n"):
        async def _exec(*argv, **kwargs):
            calls.append({"argv": argv, **kwargs})
            return FakeProcess(returncode, output)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
        return calls

    return install


# ---------------------------------------------------------------------------
# run_id validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", ["../x", "/etc/passwd", "a/b", "..", ".", "", "a\\b"])
def test_rejects_invalid_run_ids(runs_root, bad_id):
    result = _call("storyboard", {"run_id": bad_id})
    assert result.get("is_error") is True
    assert "invalid run id" in _payload(result)["error"]


def test_rejects_missing_script(runs_root):
    (runs_root / "run_noscript").mkdir()
    result = _call("storyboard", {"run_id": "run_noscript"})
    assert result.get("is_error") is True
    assert "script.json" in _payload(result)["error"]


# ---------------------------------------------------------------------------
# Pipeline subprocess tools: argv + env construction
# ---------------------------------------------------------------------------


def test_create_run_uses_the_real_runs_root(runs_root, fake_exec):
    """Regression: create_run once hardcoded '/tmp/paper-to-video' as the setup
    base dir, so chat-created runs landed somewhere list_runs/get_run (and the
    Studio UI's flow viewer) never looked — the chat would report success on a
    run the rest of the app could never find or display."""
    calls = fake_exec()
    result = _call("create_run", {})
    assert "is_error" not in result
    assert calls[0]["argv"][-1] == str(runs_root)


def test_synthesize_builds_argv_and_env(run_dir, fake_exec):
    calls = fake_exec()
    result = _call(
        "synthesize",
        {
            "run_id": "run_abc123",
            "voice_provider": "voicebox",
            "voicebox_profile": "Narrator",
            "speaker": "dylan",
            "language": "english",
        },
    )
    assert "is_error" not in result
    call = calls[0]
    assert call["argv"] == (
        "uv", "run", "python", "-m", "src.pipeline", "synthesize",
        str(run_dir / "script.json"), str(run_dir / "audio"),
    )
    assert call["cwd"] == str(agent_tools._REPO_ROOT)
    assert call["env"]["PTV_VOICE_PROVIDER"] == "voicebox"
    assert call["env"]["PTV_VOICEBOX_PROFILE"] == "Narrator"
    assert call["env"]["PTV_QWEN_TTS_SPEAKER"] == "dylan"
    assert call["env"]["PTV_QWEN_TTS_LANGUAGE"] == "english"
    payload = _payload(result)
    assert payload["exit_code"] == 0
    assert "ok" in payload["output"]


def test_synthesize_language_sets_both_provider_env_vars(run_dir, fake_exec):
    """A single `language` arg must reach whichever provider ends up active,
    without the caller needing to know which one that is."""
    calls = fake_exec()
    _call("synthesize", {"run_id": "run_abc123", "language": "spanish"})
    call = calls[0]
    assert call["env"]["PTV_QWEN_TTS_LANGUAGE"] == "spanish"
    assert call["env"]["PTV_VOICEBOX_LANGUAGE"] == "spanish"


def test_synthesize_qwen_model_size(run_dir, fake_exec):
    calls = fake_exec()
    _call("synthesize", {"run_id": "run_abc123", "qwen_model_size": "1.7B"})
    assert calls[0]["env"]["PTV_QWEN_TTS_MODEL_SIZE"] == "1.7B"


def test_imagegen_builds_argv_and_env(run_dir, fake_exec):
    calls = fake_exec()
    result = _call(
        "imagegen",
        {"run_id": "run_abc123", "segment_ids": "seg01", "model": "schnell",
         "steps": 6, "quantize": 8},
    )
    assert "is_error" not in result
    call = calls[0]
    assert call["argv"] == (
        "uv", "run", "python", "-m", "src.pipeline", "imagegen",
        str(run_dir / "script.json"), str(run_dir), "seg01",
    )
    assert call["env"]["PTV_IMAGE_MODEL"] == "schnell"
    assert call["env"]["PTV_IMAGE_STEPS"] == "6"
    assert call["env"]["PTV_IMAGE_QUANTIZE"] == "8"


def test_videogen_passes_segment_ids_and_provider(run_dir, fake_exec):
    calls = fake_exec()
    result = _call(
        "videogen",
        {"run_id": "run_abc123", "segment_ids": "seg01_b01,seg02", "video_provider": "ltx"},
    )
    assert "is_error" not in result
    call = calls[0]
    assert call["argv"] == (
        "uv", "run", "python", "-m", "src.pipeline", "videogen",
        str(run_dir / "script.json"), str(run_dir), "seg01_b01,seg02",
    )
    assert call["env"]["PTV_VIDEO_PROVIDER"] == "ltx"


def test_videogen_ltx_tuning_params(run_dir, fake_exec):
    calls = fake_exec()
    result = _call(
        "videogen",
        {
            "run_id": "run_abc123",
            "steps": 40,
            "resolution": "704x448",
            "clip_seconds": 4.0,
            "cfg_scale": 3.5,
            "stg_scale": 1.2,
            "prefer_extend": True,
            "fallback_to_kenburns": False,
            "kenburns_zoom": 1.2,
        },
    )
    assert "is_error" not in result
    env = calls[0]["env"]
    assert env["PTV_LTX_STEPS"] == "40"
    assert env["PTV_LTX_GEN_WIDTH"] == "704"
    assert env["PTV_LTX_GEN_HEIGHT"] == "448"
    assert env["PTV_LTX_CLIP_SECONDS"] == "4.0"
    assert env["PTV_LTX_CFG_SCALE"] == "3.5"
    assert env["PTV_LTX_STG_SCALE"] == "1.2"
    assert env["PTV_LTX_PREFER_EXTEND"] == "true"
    assert env["PTV_VIDEO_FALLBACK_TO_KENBURNS"] == "false"
    assert env["PTV_KENBURNS_ZOOM"] == "1.2"


def test_videogen_invalid_resolution_is_error(run_dir, fake_exec):
    fake_exec()
    result = _call("videogen", {"run_id": "run_abc123", "resolution": "bogus"})
    assert result.get("is_error") is True
    assert "invalid resolution" in _payload(result)["error"]


def test_composite_uses_manifest_and_default_output(run_dir, fake_exec):
    calls = fake_exec()
    (run_dir / "composite_manifest.json").write_text("{}")
    result = _call("composite", {"run_id": "run_abc123"})
    assert "is_error" not in result
    assert calls[0]["argv"] == (
        "uv", "run", "python", "-m", "src.pipeline", "composite",
        str(run_dir / "composite_manifest.json"), str(run_dir / "final.mp4"),
    )
    assert _payload(result)["output_path"] == str(run_dir / "final.mp4")


def test_composite_requires_manifest(run_dir, fake_exec):
    fake_exec()
    result = _call("composite", {"run_id": "run_abc123"})
    assert result.get("is_error") is True
    assert "manifest" in _payload(result)["error"]


def test_nonzero_exit_is_error(run_dir, fake_exec):
    fake_exec(returncode=2, output=b"boom\n")
    result = _call("imagegen", {"run_id": "run_abc123"})
    assert result.get("is_error") is True
    payload = _payload(result)
    assert payload["exit_code"] == 2
    assert "boom" in payload["output"]


def test_qa_failure_includes_report(run_dir, fake_exec):
    fake_exec(returncode=2, output=b"QA failed\n")
    (run_dir / "qa_report.json").write_text('{"status": "failed", "summary": {"errors": 1}}')
    result = _call("qa", {"run_id": "run_abc123"})
    assert result.get("is_error") is True
    assert _payload(result)["qa_report"]["status"] == "failed"


def test_qa_success_also_includes_report(run_dir, fake_exec):
    """cmd_qa exits 0 on warnings and the stdout tail can truncate the report's
    head, so the parsed report must be attached even on success."""
    fake_exec(returncode=0, output=b"QA warning\n")
    (run_dir / "qa_report.json").write_text('{"status": "warning", "summary": {"warnings": 2}}')
    result = _call("qa", {"run_id": "run_abc123"})
    assert "is_error" not in result
    assert _payload(result)["qa_report"]["status"] == "warning"


# ---------------------------------------------------------------------------
# Force params (QA fix loops must be able to actually regenerate media)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "env_var"),
    [
        ("imagegen", "PTV_IMAGE_FORCE"),
        ("videogen", "PTV_VIDEO_FORCE"),
        ("synthesize", "PTV_AUDIO_FORCE"),
    ],
)
def test_force_param_sets_force_env(run_dir, fake_exec, monkeypatch, tool_name, env_var):
    monkeypatch.delenv(env_var, raising=False)
    calls = fake_exec()
    result = _call(tool_name, {"run_id": "run_abc123", "force": True})
    assert "is_error" not in result
    assert calls[0]["env"][env_var] == "true"


@pytest.mark.parametrize(
    ("tool_name", "env_var"),
    [
        ("imagegen", "PTV_IMAGE_FORCE"),
        ("videogen", "PTV_VIDEO_FORCE"),
        ("synthesize", "PTV_AUDIO_FORCE"),
    ],
)
def test_no_force_param_leaves_env_unset(run_dir, fake_exec, monkeypatch, tool_name, env_var):
    monkeypatch.delenv(env_var, raising=False)
    calls = fake_exec()
    _call(tool_name, {"run_id": "run_abc123"})
    assert env_var not in calls[0]["env"]


def test_qa_strict_flag(run_dir, fake_exec):
    calls = fake_exec()
    _call("qa", {"run_id": "run_abc123", "strict": True})
    assert calls[0]["argv"][-1] == "strict"


# ---------------------------------------------------------------------------
# _run_pipeline cancellation: the whole process group must be reaped
# ---------------------------------------------------------------------------


def test_run_pipeline_cancel_reaps_process_group(monkeypatch, tmp_path):
    """Regression: a cancelled tool call (chat Stop) used to leave the whole
    process tree `uv` spawns (its python child, and that child's own
    mflux/ffmpeg/LTX children) running orphaned, holding the cross-process
    generation lock for as long as it took to finish on its own.

    start_new_session=True gives the spawned process its own pgid (equal to
    its pid); cancelling the awaiting communicate() must SIGTERM/SIGKILL that
    whole group, not just the immediate child. This test redirects
    _run_pipeline's subprocess_exec call to a real shell script that spawns
    its own child and sleeps — standing in for `uv` spawning `python` spawning
    mflux/ffmpeg — so the assertions exercise real process-group semantics.
    """
    pidfile = tmp_path / "child.pid"
    real_exec = asyncio.create_subprocess_exec
    procs: list[asyncio.subprocess.Process] = []

    async def _fake_exec(*_argv, **kwargs):
        proc = await real_exec(
            "sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait $!",
            **kwargs,
        )
        procs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    async def scenario() -> int:
        task = asyncio.ensure_future(agent_tools._run_pipeline("storyboard", []))
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text().strip():
                break
            await asyncio.sleep(0.05)
        assert pidfile.exists(), "child process never started"
        child_pid = int(pidfile.read_text().strip())

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return child_pid

    child_pid = asyncio.run(scenario())
    assert procs, "fake subprocess_exec was never called"
    pgid = procs[0].pid

    # Both the top-level process standing in for `uv` and its grandchild
    # standing in for mflux/ffmpeg/LTX must be gone — not just orphaned.
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    with pytest.raises(ProcessLookupError):
        os.killpg(pgid, 0)


# ---------------------------------------------------------------------------
# Direct-call tools
# ---------------------------------------------------------------------------


def test_produce_run_missing_run_is_error(runs_root):
    result = _call("produce_run", {"run_id": "run_missing"})
    assert result.get("is_error") is True
    assert "run_missing" in _payload(result)["error"]


def test_stop_production_missing_run_is_error(runs_root):
    result = _call("stop_production", {"run_id": "run_missing"})
    assert result.get("is_error") is True
    assert "run_missing" in _payload(result)["error"]


def test_stop_production_no_active_job_is_idempotent_noop(run_dir):
    """Stopping a run with no production running must succeed (not error) and
    just return the current status — a stop button double-click or an agent
    retry must never fail."""
    result = _call("stop_production", {"run_id": "run_abc123"})
    assert "is_error" not in result
    assert _payload(result)["status"] == "idle"


def test_get_run_missing_is_error(runs_root):
    result = _call("get_run", {"run_id": "run_missing"})
    assert result.get("is_error") is True


def test_list_runs_returns_runs(run_dir):
    payload = _payload(_call("list_runs", {}))
    assert [r["id"] for r in payload] == ["run_abc123"]


def test_tts_defaults_are_owned_by_generate(monkeypatch):
    """The tts tool must not hardcode its own speaker/language defaults (a
    second copy of generate.start_tts's) — omitted params are simply not
    passed, so generate.start_tts remains the single source of truth."""
    import src.studio.generate as generate_mod

    captured: dict = {}

    def fake_start_tts(**kwargs):
        captured.update(kwargs)
        return "gen000001"

    monkeypatch.setattr(generate_mod, "start_tts", fake_start_tts)
    result = _call("tts", {"text": "hello"})
    assert _payload(result)["gen_id"] == "gen000001"
    assert "speaker" not in captured
    assert "language" not in captured


def test_tts_passes_explicit_voice_params(monkeypatch):
    import src.studio.generate as generate_mod

    captured: dict = {}

    def fake_start_tts(**kwargs):
        captured.update(kwargs)
        return "gen000002"

    monkeypatch.setattr(generate_mod, "start_tts", fake_start_tts)
    _call("tts", {"text": "hello", "speaker": "dylan", "language": "english"})
    assert captured["speaker"] == "dylan"
    assert captured["language"] == "english"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_tool_names_match_registered_tools():
    assert agent_tools.STUDIO_TOOL_NAMES == [t.name for t in agent_tools._TOOLS]
    assert len(set(agent_tools.STUDIO_TOOL_NAMES)) == len(agent_tools.STUDIO_TOOL_NAMES)
    assert set(agent_tools.TOOL_HANDLERS) == set(agent_tools.STUDIO_TOOL_NAMES)


def test_build_studio_server_registers_all_tools():
    server = agent_tools.build_studio_server()
    assert server["type"] == "sdk"
    assert server["name"] == "studio"
