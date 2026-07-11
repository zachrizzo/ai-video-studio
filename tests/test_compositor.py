import math
import shutil
import subprocess
from pathlib import Path

import pytest

from src.compositing import compositor as compositor_mod
from src.compositing.compositor import VideoCompositor, atempo_chain

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


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
    # _FakeResult has no .stdout, so the duration probes _merge_av now issues
    # before building the merge command fail (return 0.0) and don't affect it.
    calls = _capture_run(monkeypatch)
    VideoCompositor()._merge_av(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"))
    cmd = calls[-1]
    assert cmd == [
        "ffmpeg", "-y",
        "-i", "v.mp4",
        "-i", "a.mp3",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-af", "loudnorm=I=-16:LRA=11:TP=-1.5,apad",
        "-c:a", "aac", "-b:a", "384k", "-ar", "48000",
        "-shortest",
        "-movflags", "+faststart",
        "out.mp4",
    ]


def test_merge_av_speed_adds_retiming(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)
    VideoCompositor()._merge_av(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"), speed=1.5)
    cmd = calls[-1]
    assert cmd[cmd.index("-filter:v") + 1] == "setpts=PTS/1.5"
    assert cmd[cmd.index("-af") + 1] == "atempo=1.5,loudnorm=I=-16:LRA=11:TP=-1.5,apad"
    assert cmd[cmd.index("-r") + 1] == "30"


def test_merge_av_pads_video_when_shorter_than_audio(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)

    def fake_duration(self, path):
        return 3.0 if "v." in str(path) else 5.4

    monkeypatch.setattr(VideoCompositor, "_get_duration", fake_duration)
    VideoCompositor()._merge_av(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"))
    cmd = calls[-1]
    assert cmd[cmd.index("-filter:v") + 1] == "tpad=stop_mode=clone:stop_duration=2.50"
    assert cmd[cmd.index("-af") + 1] == "loudnorm=I=-16:LRA=11:TP=-1.5,apad"


def test_merge_av_scales_pad_by_speed(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)

    def fake_duration(self, path):
        return 4.0 if "v." in str(path) else 8.0

    monkeypatch.setattr(VideoCompositor, "_get_duration", fake_duration)
    VideoCompositor()._merge_av(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"), speed=2.0)
    cmd = calls[-1]
    # raw gap=4.0, retimed by /speed=2.0 -> 2.0, plus the 0.1s buffer -> 2.10
    assert cmd[cmd.index("-filter:v") + 1] == "setpts=PTS/2,tpad=stop_mode=clone:stop_duration=2.10"


def test_merge_av_no_pad_when_video_probe_fails(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)

    def fake_duration(self, path):
        return 0.0 if "v." in str(path) else 5.0

    monkeypatch.setattr(VideoCompositor, "_get_duration", fake_duration)
    VideoCompositor()._merge_av(Path("v.mp4"), Path("a.mp3"), Path("out.mp4"))
    cmd = calls[-1]
    assert "-filter:v" not in cmd
    assert cmd[cmd.index("-af") + 1] == "loudnorm=I=-16:LRA=11:TP=-1.5,apad"


def test_encode_video_only_speed_adds_setpts(monkeypatch) -> None:
    calls = _capture_run(monkeypatch)
    VideoCompositor()._encode_video_only(Path("v.mp4"), Path("out.mp4"), speed=2.0)
    cmd = calls[0]
    assert cmd[cmd.index("-filter:v") + 1] == "setpts=PTS/2"
    assert cmd[cmd.index("-r") + 1] == "30"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_merge_av_real_video_shorter_than_audio_pads_not_truncates(tmp_path: Path) -> None:
    """A video shorter than its narration must not get truncated to the
    video's length -- the narration must be heard in full."""
    video_path = tmp_path / "v.mp4"
    audio_path = tmp_path / "a.mp3"
    out_path = tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_path)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", "5", str(audio_path)],
        check=True, capture_output=True,
    )

    compositor = VideoCompositor()
    compositor._merge_av(video_path, audio_path, out_path)
    result_duration = compositor._get_duration(out_path)

    assert result_duration == pytest.approx(5.0, abs=0.3), (
        f"expected ~5s (narration length), got {result_duration:.2f}s -- truncated?"
    )


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_merge_av_real_video_much_longer_than_audio_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A video much longer than its narration must still trigger the AV
    mismatch warning -- the check must be symmetric, not just audio > video.

    Audio is 3s (not shorter) because ffmpeg's loudnorm filter produces NaN
    output on pure digital silence (anullsrc) shorter than ~3s, which is an
    unrelated ffmpeg quirk, not the behavior under test here.
    """
    video_path = tmp_path / "v.mp4"
    audio_path = tmp_path / "a.mp3"
    out_path = tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=15",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_path)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", "3", str(audio_path)],
        check=True, capture_output=True,
    )

    compositor = VideoCompositor()
    compositor._merge_av(video_path, audio_path, out_path)
    out = capsys.readouterr().out

    assert "AV duration mismatch" in out


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_merge_av_real_audio_shorter_than_video_pads_not_truncates(tmp_path: Path) -> None:
    """A narration shorter than its video must not truncate the video down to
    the narration's length -- the visual content must play in full."""
    video_path = tmp_path / "v.mp4"
    audio_path = tmp_path / "a.mp3"
    out_path = tmp_path / "out.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_path)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", "3", str(audio_path)],
        check=True, capture_output=True,
    )

    compositor = VideoCompositor()
    compositor._merge_av(video_path, audio_path, out_path)
    result_duration = compositor._get_duration(out_path)

    assert result_duration == pytest.approx(5.0, abs=0.3), (
        f"expected ~5s (video length), got {result_duration:.2f}s -- truncated?"
    )
