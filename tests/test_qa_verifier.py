import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from app.qa_verifier import probe_video_and_loudness

def make_ffmpeg_proc_mock(duration_str="00:00:45.00", res_str="1080x1920", codec_str="h264, yuv420p", has_audio=True):
    mock = MagicMock()
    mock.returncode = 0
    audio_part = "Stream #0:1(und): Audio: aac (LC)" if has_audio else ""
    mock.stderr = f"""
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'test.mp4':
  Duration: {duration_str}, start: 0.000000, bitrate: 12000 kb/s
  Stream #0:0[0x1](und): Video: {codec_str}, {res_str} [SAR 1:1 DAR 9:16], 30 fps
  {audio_part}
"""
    return mock

def make_ebur128_proc_mock(integrated="-14.0", peak="-1.5"):
    mock = MagicMock()
    mock.returncode = 0
    mock.stderr = f"""
[Parsed_ebur128_0 @ 00000216503c5ec0] Summary:
    Integrated loudness:
        I: {integrated} LUFS
        Threshold: -24.5 LUFS
    Loudness range:
        LRA: 7.2 LU
    True peak:
        Peak: {peak} dBFS
"""
    return mock

def test_qa_ideal_video_passes(tmp_path):
    fake_video = tmp_path / "ideal.mp4"
    fake_video.touch()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            make_ffmpeg_proc_mock("00:00:45.00", "1080x1920"),
            make_ebur128_proc_mock(integrated="-14.0", peak="-1.5")
        ]
        result = probe_video_and_loudness(fake_video)
        assert result["passed"] is True
        assert len(result["issues"]) == 0
        assert result["integrated_lufs"] == -14.0
        assert result["true_peak_dbfs"] == -1.5

def test_qa_rejects_exceeding_duration(tmp_path):
    fake_video = tmp_path / "long.mp4"
    fake_video.touch()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            make_ffmpeg_proc_mock("00:00:59.20", "1080x1920"),
            make_ebur128_proc_mock(integrated="-14.0", peak="-1.5")
        ]
        result = probe_video_and_loudness(fake_video)
        assert result["passed"] is False
        assert any("58.0s" in issue for issue in result["issues"])

def test_qa_rejects_too_quiet_audio(tmp_path):
    fake_video = tmp_path / "quiet.mp4"
    fake_video.touch()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            make_ffmpeg_proc_mock("00:00:30.00", "1080x1920"),
            make_ebur128_proc_mock(integrated="-27.5", peak="-12.0")
        ]
        result = probe_video_and_loudness(fake_video)
        assert result["passed"] is False
        assert any("too quiet" in issue.lower() for issue in result["issues"])

def test_qa_rejects_too_loud_audio(tmp_path):
    fake_video = tmp_path / "loud.mp4"
    fake_video.touch()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            make_ffmpeg_proc_mock("00:00:30.00", "1080x1920"),
            make_ebur128_proc_mock(integrated="-8.5", peak="-0.5")
        ]
        result = probe_video_and_loudness(fake_video)
        assert result["passed"] is False
        assert any("too loud" in issue.lower() for issue in result["issues"])

def test_qa_rejects_true_peak_above_limit(tmp_path):
    fake_video = tmp_path / "clipping.mp4"
    fake_video.touch()

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            make_ffmpeg_proc_mock("00:00:30.00", "1080x1920"),
            make_ebur128_proc_mock(integrated="-14.0", peak="-0.2")
        ]
        result = probe_video_and_loudness(fake_video)
        assert result["passed"] is False
        assert any("true peak" in issue.lower() for issue in result["issues"])
