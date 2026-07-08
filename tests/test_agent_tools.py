"""Tests for the typed studio MCP tools (src/studio/agent_tools.py)."""

from __future__ import annotations

import asyncio
import json

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


def test_qa_strict_flag(run_dir, fake_exec):
    calls = fake_exec()
    _call("qa", {"run_id": "run_abc123", "strict": True})
    assert calls[0]["argv"][-1] == "strict"


# ---------------------------------------------------------------------------
# Direct-call tools
# ---------------------------------------------------------------------------


def test_produce_run_missing_run_is_error(runs_root):
    result = _call("produce_run", {"run_id": "run_missing"})
    assert result.get("is_error") is True
    assert "run_missing" in _payload(result)["error"]


def test_get_run_missing_is_error(runs_root):
    result = _call("get_run", {"run_id": "run_missing"})
    assert result.get("is_error") is True


def test_list_runs_returns_runs(run_dir):
    payload = _payload(_call("list_runs", {}))
    assert [r["id"] for r in payload] == ["run_abc123"]


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
