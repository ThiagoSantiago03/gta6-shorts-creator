import math
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.models import SceneItem, MusicStoryProject
from app.tts_engine import synthesize_narration
from app.gaming_scriptwriter import enforce_script_duration_budget
from app.qa_verifier import run_project_qa


def test_enforce_script_duration_budget_preserves_sentence_integrity():
    """Verify that trimming middle scenes stays within budget and leaves complete sentences."""
    scenes = [
        {"index": 0, "title": "Gancho", "narration_text": "Rockstar confirma data histórica para GTA 6."},
        {"index": 1, "title": "Fato 1", "narration_text": "O desenvolvimento do jogo já dura mais de dez anos com investimento bilionário que surpreendeu o mercado financeiro internacional."},
        {"index": 2, "title": "Fato 2", "narration_text": "A cidade de Leonida trará sistemas dinâmicos de clima nunca antes vistos em outros jogos de mundo aberto da indústria."},
        {"index": 3, "title": "Fato 3", "narration_text": "A física de corpos e veículos promete alcançar um realismo sem precedentes para os consoles de nova geração."},
        {"index": 4, "title": "Fechamento", "narration_text": "Você pretende jogar no primeiro dia? Comenta aí!"},
    ]

    total_words_before = sum(len(s["narration_text"].split()) for s in scenes)
    assert total_words_before > 60

    budget = 45
    trimmed = enforce_script_duration_budget(scenes, max_words=budget)
    total_words_after = sum(len(s["narration_text"].split()) for s in trimmed)

    assert total_words_after <= budget + 5
    for sc in trimmed:
        text = sc["narration_text"].strip()
        assert text.endswith((".", "!", "?")), f"Scene {sc['index']} did not end cleanly: {text}"


def test_synthesize_narration_auto_accelerates_under_max_duration(tmp_path: Path):
    """Verify synthesize_narration auto-accelerates when initial rate exceeds max_duration_sec (Tarefa 2.2)."""
    scenes = [
        SceneItem(index=0, title="Gancho", narration_text="Rockstar confirma novidade importante para GTA 6."),
        SceneItem(index=1, title="Fato 1", narration_text="Os gráficos e a iluminação global foram completamente reformulados."),
        SceneItem(index=2, title="Fato 2", narration_text="A cidade de Leonida terá centenas de interiores totalmente exploráveis."),
        SceneItem(index=3, title="CTA", narration_text="Qual a sua expectativa para esse lançamento? Deixa seu comentário!"),
    ]

    # Mock synthesize_narration_segments to return long durations on initial rate,
    # and acceptable durations on fallback rate
    call_count = 0

    def mock_synth_segments(scenes, work_dir, tts_engine, voice_name, api_key_override=None, rate="+6%"):
        nonlocal call_count
        call_count += 1
        # If rate is default +6%, duration exceeds 58s (e.g. 60s total)
        # If rate is >= +12%, duration drops to 45s
        per_scene_dur = 15.0 if rate == "+6%" else 11.0
        segs = []
        for s in scenes:
            wav_file = work_dir / "tts" / f"scene_{s.index}.wav"
            wav_file.parent.mkdir(parents=True, exist_ok=True)
            # Create a minimal valid wav
            import wave
            with wave.open(str(wav_file), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(b"\x00" * 480)  # dummy bytes
            segs.append({
                "scene": s,
                "duration_sec": per_scene_dur,
                "wav_path": str(wav_file),
                "local_words": [],
            })
        return segs, "edge_tts"

    with patch("app.tts_engine.synthesize_narration_segments", side_effect=mock_synth_segments):
        with patch("app.tts_engine._concat_wavs", return_value=44.48):
            full_wav, spans, words, engine = synthesize_narration(
                scenes=scenes,
                work_dir=tmp_path,
                tts_engine="edge_tts",
                voice_name="pt-BR-AntonioNeural",
                rate="+6%",
                max_duration_sec=58.0,
            )

    # Initial rate (+6%) produced 4 * 15.0 + 3 * 0.16 = 60.48s > 58s.
    # It must have retried with higher rate
    assert call_count >= 2
    assert full_wav.exists() or full_wav.name == "narration_full.wav"


def test_qa_rejects_shorts_exceeding_58s(tmp_path: Path):
    """Verify run_project_qa strictly fails when rendered YouTube Short exceeds 58.0s (Tarefa 2.2)."""
    project = MusicStoryProject(
        id="test-proj-58s",
        artist="Rockstar Games",
        song_title="GTA 6 Shorts Test",
        niche="gaming_news",
        publish_intent="editorial",
        max_duration_sec=58.0,
        scenes=[
            SceneItem(index=0, title="Cena 1", narration_text="Texto da cena 1."),
        ],
    )
    work_dir = tmp_path / "work"
    out_dir = tmp_path / "out"
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    dummy_short = out_dir / "short_youtube_gta6.mp4"
    dummy_short.write_bytes(b"dummy mp4 content")
    project.outputs = {"youtube_short": str(dummy_short)}

    # Mock probe_video_and_loudness to return 59.2s (exceeds 58.0s)
    mock_probe = {
        "width": 1080,
        "height": 1920,
        "duration_sec": 59.2,
        "fps": 30.0,
        "has_h264": True,
        "has_yuv420p": True,
        "has_audio": True,
        "audio_codec": "aac",
        "audio_sample_rate": 44100,
        "audio_channels": 2,
        "integrated_lufs": -14.2,
        "true_peak_db": -1.5,
        "issues": [],
        "passed": True,
    }

    with patch("app.qa_verifier.probe_video_and_loudness", return_value=mock_probe):
        with patch("app.qa_verifier.build_timeline_contact_sheet"):
            qa_res = run_project_qa(project=project, work_dir=work_dir, out_dir=out_dir)

    assert qa_res["qa_passed"] is False
    yt_issues = qa_res["probes"]["youtube_short"]["issues"]
    assert any("teto de Shorts" in iss or "Exceeds max Shorts duration" in iss for iss in yt_issues)
