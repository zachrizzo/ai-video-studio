"""Tests for runtime capability probing (src/studio/capabilities.py)."""

import socket

import pytest

from src.studio import capabilities


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(capabilities, "_cache", None)
    monkeypatch.setattr(capabilities, "_cache_at", 0.0)


def _patch_checks(
    monkeypatch,
    *,
    voicebox: bool,
    which: set[str],
    modules: set[str],
    ltx: bool,
) -> None:
    def fake_connect(addr, timeout=None):
        if voicebox:
            return _FakeConn()
        raise socket.timeout("down")

    monkeypatch.setattr(capabilities.socket, "create_connection", fake_connect)
    monkeypatch.setattr(
        capabilities.shutil, "which", lambda name: f"/usr/bin/{name}" if name in which else None
    )
    monkeypatch.setattr(
        capabilities.importlib.util,
        "find_spec",
        lambda name: object() if name in modules else None,
    )
    monkeypatch.setattr(capabilities, "_check_ltx", lambda: ltx)


def test_probe_all_up(monkeypatch) -> None:
    _patch_checks(monkeypatch, voicebox=True, which={"whisper", "ffmpeg"},
                  modules={"mflux"}, ltx=True)
    assert capabilities.probe(force=True) == {
        "voicebox": True, "whisper": True, "ffmpeg": True, "mflux": True, "ltx": True,
    }


def test_probe_all_down(monkeypatch) -> None:
    _patch_checks(monkeypatch, voicebox=False, which=set(), modules=set(), ltx=False)
    assert capabilities.probe(force=True) == {
        "voicebox": False, "whisper": False, "ffmpeg": False, "mflux": False, "ltx": False,
    }


def test_probe_check_failure_is_nonfatal(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(capabilities.socket, "create_connection", explode)
    monkeypatch.setattr(capabilities.shutil, "which", explode)
    monkeypatch.setattr(capabilities.importlib.util, "find_spec", explode)
    monkeypatch.setattr(capabilities, "_check_ltx", lambda: False)
    assert capabilities.probe(force=True) == {
        "voicebox": False, "whisper": False, "ffmpeg": False, "mflux": False, "ltx": False,
    }


def test_probe_caches_within_ttl(monkeypatch) -> None:
    _patch_checks(monkeypatch, voicebox=True, which={"whisper", "ffmpeg"},
                  modules={"mflux"}, ltx=True)
    first = capabilities.probe(force=True)

    # Everything goes down, but the cached probe is still returned...
    _patch_checks(monkeypatch, voicebox=False, which=set(), modules=set(), ltx=False)
    assert capabilities.probe() == first

    # ...until the TTL expires or force is passed.
    assert capabilities.probe(force=True)["voicebox"] is False


def test_probe_reprobes_after_ttl(monkeypatch) -> None:
    _patch_checks(monkeypatch, voicebox=True, which={"whisper", "ffmpeg"},
                  modules={"mflux"}, ltx=True)
    capabilities.probe(force=True)

    _patch_checks(monkeypatch, voicebox=False, which=set(), modules=set(), ltx=False)
    monkeypatch.setattr(
        capabilities.time, "monotonic",
        lambda: capabilities._cache_at + capabilities._CACHE_TTL_SECONDS + 1,
    )
    assert capabilities.probe()["voicebox"] is False


def test_check_align_command_skips_env_assignments(monkeypatch) -> None:
    """Regression: probing must resolve the CONFIGURED aligner, not a hardcoded
    "whisper" — align_command may prefix the real executable with shell
    env-var assignments (e.g. pyenv pinning)."""
    from src.config import PipelineConfig

    monkeypatch.setenv("PTV_ALIGN_COMMAND", "FOO=1 my-fake-whisper")
    seen: list[str] = []

    def fake_which(name: str) -> str | None:
        seen.append(name)
        return "/usr/bin/my-fake-whisper" if name == "my-fake-whisper" else None

    monkeypatch.setattr(capabilities.shutil, "which", fake_which)
    assert PipelineConfig().align_command == "FOO=1 my-fake-whisper"
    assert capabilities._check_align_command() is True
    assert seen == ["my-fake-whisper"]


def test_check_align_command_all_assignments_is_false(monkeypatch) -> None:
    monkeypatch.setenv("PTV_ALIGN_COMMAND", "FOO=1 BAR=2")
    monkeypatch.setattr(capabilities.shutil, "which", lambda name: "/usr/bin/whatever")
    assert capabilities._check_align_command() is False


def test_check_align_command_missing_executable_is_false(monkeypatch) -> None:
    monkeypatch.setenv("PTV_ALIGN_COMMAND", "FOO=1 my-fake-whisper")
    monkeypatch.setattr(capabilities.shutil, "which", lambda name: None)
    assert capabilities._check_align_command() is False


def test_probe_whisper_key_uses_align_command(monkeypatch) -> None:
    """The `whisper` key in probe()'s dict must reflect the configured
    align_command, not a literal 'whisper' lookup."""
    monkeypatch.setenv("PTV_ALIGN_COMMAND", "FOO=1 my-fake-whisper")
    _patch_checks(monkeypatch, voicebox=True, which={"ffmpeg"}, modules={"mflux"}, ltx=True)
    monkeypatch.setattr(
        capabilities.shutil, "which",
        lambda name: "/usr/bin/x" if name in {"ffmpeg", "my-fake-whisper"} else None,
    )
    assert capabilities.probe(force=True)["whisper"] is True


def test_summary_line_format(monkeypatch) -> None:
    monkeypatch.setattr(
        capabilities, "probe",
        lambda force=False: {
            "voicebox": True, "whisper": True, "ffmpeg": True, "mflux": True, "ltx": True,
        },
    )
    assert capabilities.summary_line() == (
        "[capabilities] voicebox=up whisper=ok ffmpeg=ok mflux=ok ltx=ok"
    )

    monkeypatch.setattr(
        capabilities, "probe",
        lambda force=False: {
            "voicebox": False, "whisper": False, "ffmpeg": True, "mflux": True, "ltx": False,
        },
    )
    assert capabilities.summary_line() == (
        "[capabilities] voicebox=down whisper=missing ffmpeg=ok mflux=ok ltx=missing"
    )
