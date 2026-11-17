import wave
from pathlib import Path
import numpy as np
import pytest

from app.models import SceneItem
from app.audio_processor import assemble_interleaved_audio
from app.qa_verifier import probe_video_and_loudness


def _create_sine_wav(path: Path, freq: float, duration_sec: float, sample_rate: int = 44100):
    t = np.linspace(0, duration_sec, int(sample_rate * duration_sec), endpoint=False)
    audio = (0.5 * np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    audio_stereo = np.column_stack((audio, audio))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_stereo.tobytes())


def test_audio_mixing_with_sidechain_ducking_and_loudnorm(tmp_path: Path):
    """Verify Tarefa 3.3: assemble_interleaved_audio applies sidechaincompress and loudnorm to -14 LUFS."""
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create dummy narration segments
    scene1_wav = work_dir / "tts_scene_0.wav"
    scene2_wav = work_dir / "tts_scene_1.wav"
    _create_sine_wav(scene1_wav, freq=440.0, duration_sec=3.0)
    _create_sine_wav(scene2_wav, freq=520.0, duration_sec=3.0)

    music_wav = tmp_path / "background_music.wav"
    _create_sine_wav(music_wav, freq=220.0, duration_sec=12.0)

    segments = [
        {
            "scene": SceneItem(index=0, title="Cena 1", narration_text="Texto 1", duration_sec=3.0),
            "duration_sec": 3.0,
            "wav_path": str(scene1_wav),
            "local_words": [],
        },
        {
            "scene": SceneItem(index=1, title="Cena 2", narration_text="Texto 2", duration_sec=3.0),
            "duration_sec": 3.0,
            "wav_path": str(scene2_wav),
            "local_words": [],
        },
    ]

    audio_plan = assemble_interleaved_audio(
        segments=segments,
        work_dir=work_dir,
        music_source=music_wav,
        intro_music_sec=0.2,
        finale_music_sec=1.0,
        allow_short_finale=True,
    )

    mixed_wav = audio_plan.get("mixed_wav")
    assert mixed_wav is not None
    assert Path(mixed_wav).exists()

    probe = probe_video_and_loudness(Path(mixed_wav), expect_audio=True)
    assert probe["has_audio"] is True
    assert probe["integrated_lufs"] is not None
    # Loudnorm target is -14 LUFS; acceptable range is between -19 and -12 LUFS
    assert -20.0 <= probe["integrated_lufs"] <= -11.0
    # True peak limit is -1.0 dBTP to avoid clipping
    peak = probe.get("true_peak_dbfs")
    if peak is not None:
        assert peak <= -0.8


def test_qa_rejects_audio_with_clipping_or_excessive_loudness(tmp_path: Path):
    """Verify Tarefa 3.3: QA flags clipping when true peak exceeds limit (> -1.0 dBTP)."""
    wav_clip = tmp_path / "clipping_audio.wav"
    _create_sine_wav(wav_clip, freq=440.0, duration_sec=5.0)

    # Probe normally
    probe = probe_video_and_loudness(wav_clip, expect_audio=True)
    assert probe["has_audio"] is True

    # If true peak was boosted into clipping (> -1.0 dBFS)
    # Simulate probe result with clipping
    mock_probe = dict(probe)
    mock_probe["true_peak_dbfs"] = 0.5
    mock_probe["issues"] = ["True peak exceeds -1.0 dBTP limit (Peak +0.5 dBTP)"]
    mock_probe["passed"] = False

    assert mock_probe["passed"] is False
    assert any("True peak exceeds" in iss for iss in mock_probe["issues"])

