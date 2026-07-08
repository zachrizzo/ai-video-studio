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
