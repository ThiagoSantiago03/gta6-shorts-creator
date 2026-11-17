from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from app.config import get_ffmpeg_exe
from app.models import MusicStoryProject, SceneItem, WordTiming
from app.qa_verifier import probe_video_and_loudness
from app.video_renderer import render_gaming_youtube_short


def _make_synthetic_video(path: Path, duration: float = 5.0, color: str = "blue") -> Path:
    ffmpeg = get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi",
            "-i", f"testsrc=duration={duration}:size=1280x720:rate=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _make_synthetic_speech_wav(path: Path, duration: float = 2.5, freq: int = 320) -> Path:
    ffmpeg = get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi",
            "-i", f"sine=frequency={freq}:sample_rate=44100:duration={duration}",
            "-ac", "2", "-ar", "44100", "-af", "volume=-12dB",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_render_gaming_youtube_short_characterization(tmp_path: Path):
    """
    T13.1 — Teste de caracterização de `render_gaming_youtube_short`:
    Renderiza um projeto mínimo com mídia sintética via FFmpeg e valida metadados reais
    do MP4 gerado (resolução 1080x1920, ~30 fps, H.264/yuv420p, áudio AAC, duração <= 58s,
    artefatos de entrega e checklist técnico/narrativo do QA).
    """
    work_dir = tmp_path / "work"
    out_dir = tmp_path / "out"
    media_dir = tmp_path / "media"
    work_dir.mkdir()
    out_dir.mkdir()
    media_dir.mkdir()

    clip1 = _make_synthetic_video(media_dir / "clip1.mp4", duration=5.0)
    clip2 = _make_synthetic_video(media_dir / "clip2.mp4", duration=5.0)
    wav1 = _make_synthetic_speech_wav(work_dir / "seg_01.wav", duration=2.4, freq=300)
    wav2 = _make_synthetic_speech_wav(work_dir / "seg_02.wav", duration=2.4, freq=420)

    project = MusicStoryProject(
        id="char-test-gta6",
        niche="gaming_news",
        publish_intent="editorial",
        confidence_status="confirmado",
        status_confiabilidade="confirmado",
        fonte_primaria="Rockstar Games",
        title="GTA 6 Teste de Caracterização",
        song_title="GTA 6 Teste de Caracterização",
        artist="GTA 6",
        max_duration_sec=58.0,
        speech_rate="+14%",
        youtube_title="GTA 6 Novidade Confirmada #shorts",
        youtube_description="Descrição de teste.",
        youtube_tags=["GTA 6", "Shorts"],
        scenes=[
            SceneItem(
                index=1,
                title="Gancho",
                narration_text="A Rockstar revelou detalhes importantes sobre GTA 6 hoje.",
                video_path=str(clip1),
                video_start_sec=0.0,
                video_end_sec=3.0,
                asset_type="video",
                framing_mode="crop_center",
            ),
            SceneItem(
                index=2,
                title="Fechamento",
                narration_text="E você, qual novidade de Vice City mais espera? Comente aqui!",
                video_path=str(clip2),
                video_start_sec=0.5,
                video_end_sec=3.5,
                asset_type="video",
                framing_mode="blur_fill",
                is_cta=True,
            ),
        ],
    )

    fake_segments = [
        {
            "scene": project.scenes[0],
            "scene_index": 1,
            "wav_path": wav1,
            "duration_sec": 2.4,
            "local_words": [
                WordTiming(word="Rockstar", start=0.1, end=0.8),
                WordTiming(word="GTA", start=0.9, end=1.5),
                WordTiming(word="6", start=1.6, end=2.2),
            ],
            "music_break_after_sec": 0.0,
        },
        {
            "scene": project.scenes[1],
            "scene_index": 2,
            "wav_path": wav2,
            "duration_sec": 2.4,
            "local_words": [
                WordTiming(word="Vice", start=0.1, end=0.8),
                WordTiming(word="City", start=0.9, end=1.5),
                WordTiming(word="Comente", start=1.6, end=2.2),
            ],
            "music_break_after_sec": 0.0,
        },
    ]

    with patch("app.video_renderer.prepare_gaming_project_visuals", return_value=None), \
         patch("app.video_renderer.synthesize_narration_segments", return_value=(fake_segments, "edge_tts")), \
         patch("app.video_renderer.auto_select_gaming_soundtrack", return_value=(None, 0.0)), \
         patch("app.video_renderer.find_headless_browser", return_value=None), \
         patch("app.thumbnail_engine.find_headless_browser", return_value=None):
        rendered = render_gaming_youtube_short(project, work_dir, out_dir)

    out_mp4 = Path(rendered.outputs["youtube_short"])
    assert out_mp4.exists()
    assert out_mp4.stat().st_size > 10_000
    assert Path(rendered.outputs["highlight_cover"]).exists()
    assert Path(rendered.outputs["caption_txt"]).exists()

    probe = probe_video_and_loudness(out_mp4, expect_audio=True)
    assert probe["exists"] is True
    assert probe["resolution"] == "1080x1920"
    assert 24.0 <= probe["fps"] <= 31.0
    assert probe["has_audio"] is True
    assert "aac" in (probe.get("audio_codec") or "")
    assert 3.0 <= probe["duration_sec"] <= 58.0
    assert not any("codec" in iss.lower() or "pix_fmt" in iss.lower() for iss in probe["issues"])

    cats = rendered.qa_report["objective_criteria"]["categories"]
    assert cats["tecnica"] == "aprovado"
    assert cats["narrativa"] == "aprovado"
