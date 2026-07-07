import math
from pathlib import Path

import pytest

from src.compositing import compositor as compositor_mod
from src.compositing.compositor import VideoCompositor, atempo_chain


def _factors(chain: str) -> list[float]:
    return [float(part.split("=")[1]) for part in chain.split(",")]


@pytest.mark.parametrize("speed", [1.0, 1.5, 2.0, 3.0, 0.25])
def test_atempo_chain_factors_multiply_back(speed: float) -> None:
    factors = _factors(atempo_chain(speed))
    assert all(0.5 <= f <= 2.0 for f in factors)
    assert math.prod(factors) == pytest.approx(speed)


class _FakeResult:
    returncode = 0
    stderr = ""


def _capture_run(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return _FakeResult()

    monkeypatch.setattr(compositor_mod.subprocess, "run", fake_run)
    return calls


def test_merge_av_default_speed_command_unchanged(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)
    VideoCompositor()._merge_av(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"))
    assert calls[0] == [
        "ffmpeg", "-y",
        "-i", "v.mp4",
        "-i", "a.mp3",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
        "-c:a", "aac", "-b:a", "384k", "-ar", "48000",
        "-shortest",
        "-movflags", "+faststart",
        "out.mp4",
    ]


def test_merge_av_speed_adds_retiming(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)
    VideoCompositor()._merge_av(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"), speed=1.5)
    cmd = calls[0]
    assert cmd[cmd.index("-filter:v") + 1] == "setpts=PTS/1.5"
    assert cmd[cmd.index("-af") + 1] == "atempo=1.5,loudnorm=I=-16:LRA=11:TP=-1.5"
    assert cmd[cmd.index("-r") + 1] == "30"


def test_encode_video_only_speed_adds_setpts(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)
    VideoCompositor()._encode_video_only(Path("v.mp4"), Path("out.mp4"), speed=2.0)
    cmd = calls[0]
    assert cmd[cmd.index("-filter:v") + 1] == "setpts=PTS/2"
    assert cmd[cmd.index("-r") + 1] == "30"
