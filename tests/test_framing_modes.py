import subprocess
from pathlib import Path
import pytest

from app.config import get_ffmpeg_exe
from app.video_renderer import _render_scene_video_clip


def _generate_test_16_9_video(out_path: Path, duration_sec: float = 1.0):
    """Generate a clean synthetic 1280x720 16:9 video with color bars and moving counter for testing."""
    ffmpeg = get_ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration_sec}:size=1280x720:rate=30",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Falha ao gerar vídeo de teste: {res.stderr}"


def test_framing_modes_render_correctly_in_all_three_modes(tmp_path: Path):
    """Critério de Aceite Tarefa 3.1: a mesma cena renderiza corretamente nos três modos."""
    src_video = tmp_path / "source_16_9.mp4"
    _generate_test_16_9_video(src_video, duration_sec=1.2)

    modes = [
        ("crop_center", 0.5, "out_crop_center.mp4"),
        ("crop_focus", 0.25, "out_crop_focus.mp4"),
        ("blur_fill", 0.5, "out_blur_fill.mp4"),
    ]

    for mode, fx, out_name in modes:
        out_file = tmp_path / out_name
        res_path = _render_scene_video_clip(
            video_path=src_video,
            duration_sec=1.0,
            effect="zoom_in",
            out_mp4=out_file,
            accent_color="#FF2E93",
            seek_sec=0.0,
            framing_mode=mode,
            focus_x=fx,
        )

        assert res_path.exists(), f"Output for mode {mode} was not created"
        assert res_path.stat().st_size > 1000, f"Output for mode {mode} is too small: {res_path.stat().st_size} bytes"

        # Verify resolution using ffprobe or ffmpeg
        ffmpeg = get_ffmpeg_exe()
        probe_cmd = [ffmpeg, "-i", str(res_path)]
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
        assert "1080x1920" in probe_res.stderr, f"Resolution 1080x1920 not found for mode {mode} in: {probe_res.stderr}"
