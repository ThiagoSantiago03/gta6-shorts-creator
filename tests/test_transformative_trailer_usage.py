from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from app.models import SceneItem, MusicStoryProject
from app.gaming_media import (
    auto_blend_gaming_clips_and_images,
    record_project_used_media,
    match_best_trailer_cut,
)
from app.qa_verifier import run_project_qa


def test_match_best_trailer_cut_selects_thematic_cut_and_records_reason():
    """Verify Tarefa 1.2: trailer cuts are matched to what the scene narrates and record a reason."""
    scene_police = SceneItem(
        index=1,
        title="Polícia Implacável",
        narration_text="A polícia de Vice City e os oficiais armados perseguem suspeitos em alta velocidade.",
        duration_sec=5.2,
    )
    available_cuts = [
        {"clip_name": "GTA Trailer 1.mp4", "clip_path": "inputs/gta6/GTA Trailer 1.mp4", "start_sec": 2.7, "label": "Lucia Prisao"},
        {"clip_name": "GTA Trailer 1.mp4", "clip_path": "inputs/gta6/GTA Trailer 1.mp4", "start_sec": 21.7, "label": "Perseguicao Policial"},
        {"clip_name": "GTA Trailer 1.mp4", "clip_path": "inputs/gta6/GTA Trailer 1.mp4", "start_sec": 10.9, "label": "Lancha Pantano"},
    ]

    best_cut, reason = match_best_trailer_cut(
        scene=scene_police,
        available_cuts=available_cuts,
        used_keys=set(),
        max_continuous_sec=6.0,
    )

    assert best_cut is not None
    # 21.7s is the police pursuit cut
    assert best_cut["start_sec"] == 21.7
    assert "polícia" in reason.lower() or "perseguição" in reason.lower() or "perseguicao" in reason.lower()


def test_max_continuous_trailer_duration_enforced_per_scene():
    """Verify Tarefa 1.2: trailer cuts do not exceed max_continuous_trailer_sec (<= 6.0s)."""
    scene_long = SceneItem(
        index=2,
        title="Vida Noturna",
        narration_text="As festas e baladas em coberturas de Vice City mostram o luxo da cidade.",
        duration_sec=9.5,  # scene duration is 9.5s, higher than 6.0s
    )
    available_cuts = [
        {"clip_name": "GTA Trailer 1.mp4", "clip_path": "inputs/gta6/GTA Trailer 1.mp4", "start_sec": 26.0, "label": "Boate"},
    ]

    best_cut, reason = match_best_trailer_cut(
        scene=scene_long,
        available_cuts=available_cuts,
        used_keys=set(),
        max_continuous_sec=6.0,
    )

    assert best_cut is not None
    cut_dur = float(best_cut.get("end_sec", 0.0)) - float(best_cut.get("start_sec", 0.0))
    assert cut_dur <= 6.0


def test_qa_rejects_single_continuous_trailer_without_cuts(tmp_path: Path):
    """Verify Tarefa 1.2: QA rejects when an uncut continuous trailer is used across all scenes."""
    project = MusicStoryProject(
        id="test-trailer-qa",
        artist="Rockstar Games",
        song_title="GTA 6 Shorts Test",
        niche="gaming_news",
        publish_intent="editorial",
        max_continuous_trailer_sec=6.0,
        scenes=[
            SceneItem(
                index=0,
                title="Cena 1",
                narration_text="Texto 1",
                video_path=str(tmp_path / "trailer.mp4"),
                video_start_sec=0.0,
                video_end_sec=5.0,
                clip_reason="Corte 1",
                asset_type="video",
            ),
            SceneItem(
                index=1,
                title="Cena 2",
                narration_text="Texto 2",
                video_path=str(tmp_path / "trailer.mp4"),
                video_start_sec=0.0,  # Exact same cut repeated continuously
                video_end_sec=5.0,
                clip_reason="Corte 1",
                asset_type="video",
            ),
        ],
    )
    # Create the dummy file so it exists
    (tmp_path / "trailer.mp4").write_bytes(b"dummy")

    work_dir = tmp_path / "work"
    out_dir = tmp_path / "out"
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    dummy_short = out_dir / "short_youtube_gta6.mp4"
    dummy_short.write_bytes(b"dummy")
    project.outputs = {"youtube_short": str(dummy_short)}

    mock_probe = {
        "width": 1080, "height": 1920, "duration_sec": 10.0, "fps": 30.0,
        "has_h264": True, "has_yuv420p": True, "has_audio": True,
        "integrated_lufs": -14.0, "true_peak_db": -1.5, "issues": [], "passed": True,
    }

    with patch("app.qa_verifier.probe_video_and_loudness", return_value=mock_probe):
        with patch("app.qa_verifier.build_timeline_contact_sheet"):
            qa_res = run_project_qa(project=project, work_dir=work_dir, out_dir=out_dir)

    assert qa_res["qa_passed"] is False
    assert any("não-transformativo" in str(iss) or "fundo contínuo" in str(iss) for iss in qa_res.get("transformative_trailer_issues", []))


def test_record_project_used_media_attaches_clip_details_and_reasons(tmp_path: Path):
    """Verify Tarefa 1.2: media records contain start/end timestamps and reasons per scene."""
    clip_file = tmp_path / "GTA Trailer 1.mp4"
    clip_file.write_bytes(b"dummy")

    project = MusicStoryProject(
        id="test-details",
        artist="Rockstar Games",
        song_title="GTA 6 Test",
        scenes=[
            SceneItem(
                index=0,
                title="Cena 1",
                narration_text="Texto 1",
                video_path=str(clip_file),
                video_start_sec=2.7,
                video_end_sec=6.7,
                clip_reason="Corte temático de abertura",
                asset_type="video",
            )
        ]
    )

    used = record_project_used_media(project)
    trailer_item = next((u for u in used if u["filename"] == "GTA Trailer 1.mp4"), None)
    assert trailer_item is not None
    assert "clip_details" in trailer_item
    assert len(trailer_item["clip_details"]) == 1
    detail = trailer_item["clip_details"][0]
    assert detail["scene_index"] == 0
    assert detail["start_sec"] == 2.7
    assert detail["end_sec"] == 6.7
    assert detail["reason"] == "Corte temático de abertura"

