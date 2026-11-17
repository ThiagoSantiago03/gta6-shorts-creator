import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from app.video_renderer import _render_scene_clip, FPS
import math

EFFECTS = ["zoom_out", "pan_left", "pan_right", "ken_burns_duotone", "vintage_grain", "default"]

@pytest.mark.parametrize("effect", EFFECTS)
def test_render_scene_clip_uses_exact_n_frames(effect, tmp_path):
    fake_img = tmp_path / "scene.png"
    fake_img.touch()
    out_mp4 = tmp_path / "out.mp4"
    duration = 3.5
    expected_n_frames = max(15, int(math.ceil(duration * FPS)))

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        _render_scene_clip(fake_img, duration, effect, out_mp4, "#00F0FF")

        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        vf_index = cmd.index("-vf")
        vf_arg = cmd[vf_index + 1]

        # Verifica se d={expected_n_frames} está presente e d=99999 foi removido
        assert f":d={expected_n_frames}:" in vf_arg
        assert "99999" not in vf_arg
