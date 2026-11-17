from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from app.config import get_safe_zones, verify_ffmpeg_installation
from app.models import MusicStoryProject, SceneItem
from app.qa_verifier import run_project_qa, evaluate_objective_qa_criteria


def _make_probe_success(dur: float = 45.0, res: str = "1080x1920", fps: float = 30.0, lufs: float = -14.0, peak: float = -1.5):
    return {
        "exists": True,
        "size_mb": 15.0,
        "duration_sec": dur,
        "resolution": res,
        "fps": fps,
        "has_audio": True,
        "integrated_lufs": lufs,
        "true_peak_dbfs": peak,
        "issues": [],
        "passed": True,
    }


def test_qa_objective_criteria_pronto_when_all_pass(tmp_path: Path):
    """Projeto com técnica, narrativa, visual e editorial válidos vira 'Pronto'."""
    pdir = tmp_path / "proj"
    pdir.mkdir()
    odir = tmp_path / "out"
    odir.mkdir()
    vfile = odir / "video.mp4"
    vfile.touch()

    project = MusicStoryProject(
        id="proj_ideal",
        title="GTA 6 Novidades Oficiais",
        publish_intent="commercial",
        confidence_status="confirmado",
        fonte_primaria="Rockstar Games",
        max_duration_sec=58.0,
        outputs={"youtube_short": str(vfile)},
        scenes=[
            SceneItem(index=1, narration_text="A Rockstar confirmou novidades para GTA 6.", duration_sec=15.0),
            SceneItem(index=2, narration_text="Os detalhes técnicos mostram gráficos impressionantes.", duration_sec=15.0),
            SceneItem(index=3, narration_text="O que você mais quer ver no jogo? Comente aqui!", duration_sec=15.0, is_cta=True),
        ],
        used_media=[
            {
                "filename": "trailer1.mp4",
                "role": "video_clip",
                "license_type": "promotional_fair_use",
                "authorization_status": "commercial_ok",
                "commercial_use_allowed": True,
                "risk_level": "baixo",
            }
        ]
    )

    with patch("app.qa_verifier.probe_video_and_loudness", return_value=_make_probe_success()), \
         patch("app.qa_verifier.score_project_slideshow_risk", return_value={"verdict": "strong", "score": 1.0}), \
         patch("app.qa_verifier.write_packed_timeline", return_value=tmp_path / "timeline.txt"), \
         patch("app.qa_verifier.build_timeline_contact_sheet", return_value=tmp_path / "csheet.png"):

        report = run_project_qa(project, pdir, odir)

    assert report["qa_passed"] is True
    assert report["is_ready_for_publication"] is True
    assert report["requires_manual_confirmation"] is False
    assert report["verdict"] == "Pronto"

    obj = report["objective_criteria"]
    assert obj["categories"]["tecnica"] == "aprovado"
    assert obj["categories"]["narrativa"] == "aprovado"
    assert obj["categories"]["visual"] == "aprovado"
    assert obj["categories"]["editorial"] == "aprovado"


def test_qa_objective_criteria_rejects_mandatory_technical_failure(tmp_path: Path):
    """Falha técnica obrigatória (ex: duração > 58s ou resolução incorreta) reprova o QA."""
    pdir = tmp_path / "proj"
    pdir.mkdir()
    odir = tmp_path / "out"
    odir.mkdir()
    vfile = odir / "video.mp4"
    vfile.touch()

    project = MusicStoryProject(
        id="proj_fail_tech",
        title="GTA 6 Longo",
        publish_intent="editorial",
        confidence_status="confirmado",
        fonte_primaria="Rockstar Games",
        outputs={"youtube_short": str(vfile)},
        scenes=[
            SceneItem(index=1, narration_text="Cena 1", duration_sec=20.0),
            SceneItem(index=2, narration_text="Cena 2", duration_sec=20.0),
            SceneItem(index=3, narration_text="Cena 3? Comente!", duration_sec=22.0),
        ]
    )

    probe_over_duration = _make_probe_success(dur=62.0, res="1080x1920")
    probe_over_duration["passed"] = False
    probe_over_duration["issues"].append("Exceeds max Shorts duration 58.0s")

    with patch("app.qa_verifier.probe_video_and_loudness", return_value=probe_over_duration), \
         patch("app.qa_verifier.score_project_slideshow_risk", return_value={"verdict": "strong", "score": 1.0}), \
         patch("app.qa_verifier.write_packed_timeline", return_value=tmp_path / "timeline.txt"), \
         patch("app.qa_verifier.build_timeline_contact_sheet", return_value=tmp_path / "csheet.png"):

        report = run_project_qa(project, pdir, odir)

    assert report["qa_passed"] is False
    assert report["is_ready_for_publication"] is False
    assert report["verdict"] == "Reprovado no QA"
    assert report["objective_criteria"]["categories"]["tecnica"] == "reprovado"


def test_qa_objective_criteria_flags_manual_confirmation_when_editorial_has_warning(tmp_path: Path):
    """Técnica e narrativa aprovadas, mas pauta não verificada gera 'Pronto com Ressalvas'."""
    pdir = tmp_path / "proj"
    pdir.mkdir()
    odir = tmp_path / "out"
    odir.mkdir()
    vfile = odir / "video.mp4"
    vfile.touch()

    project = MusicStoryProject(
        id="proj_rumor",
        title="GTA 6 Suposto Leak",
        publish_intent="editorial",
        confidence_status="nao_verificado",
        fonte_primaria="",
        outputs={"youtube_short": str(vfile)},
        scenes=[
            SceneItem(index=1, narration_text="Cena 1 segundo rumores.", duration_sec=14.0),
            SceneItem(index=2, narration_text="Cena 2 de acordo com boatos.", duration_sec=14.0),
            SceneItem(index=3, narration_text="O que você acha? Comente!", duration_sec=14.0, is_cta=True),
        ],
        used_media=[
            {
                "filename": "trailer.mp4",
                "role": "video_clip",
                "license_type": "promotional_fair_use",
                "authorization_status": "editorial_ok",
                "commercial_use_allowed": False,
                "risk_level": "baixo",
            }
        ]
    )

    with patch("app.qa_verifier.probe_video_and_loudness", return_value=_make_probe_success()), \
         patch("app.qa_verifier.score_project_slideshow_risk", return_value={"verdict": "strong", "score": 1.0}), \
         patch("app.qa_verifier.write_packed_timeline", return_value=tmp_path / "timeline.txt"), \
         patch("app.qa_verifier.build_timeline_contact_sheet", return_value=tmp_path / "csheet.png"):

        report = run_project_qa(project, pdir, odir)

    assert report["is_ready_for_publication"] is True
    assert report["requires_manual_confirmation"] is True
    assert "Requer Confirmação Editorial" in report["verdict"]
    assert report["objective_criteria"]["categories"]["editorial"] == "reprovado"


def test_safe_zones_configuration():
    """Valida que as safe zones respeitam os valores mínimos recomendados para celular."""
    sz = get_safe_zones()
    assert sz["top"] >= 250
    assert sz["bottom"] >= 320
    assert sz["left"] >= 60
    assert sz["right"] >= 60


def test_ffmpeg_installation_verification():
    """Valida que o utilitário detecta a presença e filtros essenciais do FFmpeg."""
    info = verify_ffmpeg_installation()
    assert info["ok"] is True
    assert info["path"]
    assert "version" in info
    assert len(info["missing_filters"]) == 0
