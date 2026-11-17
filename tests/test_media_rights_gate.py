import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from app.models import MusicStoryProject, SceneItem
from app.qa_verifier import run_project_qa
from app.gaming_media import record_project_used_media

def make_probe_success():
    return {
        "exists": True,
        "size_mb": 15.0,
        "duration_sec": 45.0,
        "resolution": "1080x1920",
        "has_audio": True,
        "integrated_lufs": -14.0,
        "true_peak_dbfs": -1.5,
        "issues": [],
        "passed": True,
    }

def test_media_rights_gate_blocks_commercial_project_with_unknown_media(tmp_path):
    """Projeto comercial usando mídia desconhecida deve ser BLOQUEADO e reprovado no QA."""
    pdir = tmp_path / "proj_test"
    pdir.mkdir()
    odir = tmp_path / "out_test"
    odir.mkdir()
    vfile = odir / "single_video.mp4"
    vfile.touch()

    project = MusicStoryProject(
        id="proj_test",
        title="Teste Gate Direitos",
        publish_intent="commercial",
        audio_file="inputs/gta6/Miami Night Highway.mp3",
        outputs={"youtube_short": str(vfile)},
        scenes=[
            SceneItem(
                index=1,
                narration_text="Texto da cena",
                video_path="inputs/gta6/ReelGTARealista.mp4",
            )
        ]
    )

    with patch("app.qa_verifier.probe_video_and_loudness", return_value=make_probe_success()), \
         patch("app.qa_verifier.score_project_slideshow_risk", return_value={"verdict": "strong", "score": 1.0}), \
         patch("app.qa_verifier.write_packed_timeline", return_value=tmp_path / "timeline.txt"), \
         patch("app.qa_verifier.build_timeline_contact_sheet", return_value=tmp_path / "csheet.png"):

        report = run_project_qa(project, pdir, odir)
        assert report["qa_passed"] is False
        assert report["media_audit"]["status"] == "blocked"
        assert len(report["media_audit"]["blocking_reasons"]) >= 1
        assert any("ReelGTARealista.mp4" in r for r in report["media_audit"]["blocking_reasons"])

def test_media_rights_gate_allows_commercial_when_all_media_licensed(tmp_path):
    """Projeto comercial com mídia 100% autorizada para uso comercial passa no gate de direitos."""
    pdir = tmp_path / "proj_licensed"
    pdir.mkdir()
    odir = tmp_path / "out_licensed"
    odir.mkdir()
    vfile = odir / "single_video.mp4"
    vfile.touch()

    project = MusicStoryProject(
        id="proj_licensed",
        title="Teste Licenciado",
        publish_intent="commercial",
        audio_file="inputs/audio_licenciado.mp3",
        outputs={"youtube_short": str(vfile)},
        scenes=[
            SceneItem(
                index=1,
                narration_text="Texto da cena",
                image_path="inputs/imagem_cc0.png",
            )
        ],
        used_media=[
            {
                "media_id": "audio_cc0",
                "filename": "audio_licenciado.mp3",
                "role": "soundtrack",
                "source_url": "https://freemusicarchive.org/cc0",
                "copyright_owner": "Artista CC0",
                "license_type": "CC0",
                "authorization_status": "licensed",
                "commercial_use_allowed": True,
                "attribution_required": False,
                "risk_level": "baixo",
                "scenes_used": [1],
            },
            {
                "media_id": "img_cc0",
                "filename": "imagem_cc0.png",
                "role": "scene_image",
                "source_url": "https://commons.wikimedia.org/wiki/File:Test.png",
                "copyright_owner": "Wikimedia User",
                "license_type": "CC0",
                "authorization_status": "licensed",
                "commercial_use_allowed": True,
                "attribution_required": False,
                "risk_level": "baixo",
                "scenes_used": [1],
            }
        ]
    )

    with patch("app.qa_verifier.probe_video_and_loudness", return_value=make_probe_success()), \
         patch("app.qa_verifier.score_project_slideshow_risk", return_value={"verdict": "strong", "score": 1.0}), \
         patch("app.qa_verifier.write_packed_timeline", return_value=tmp_path / "timeline.txt"), \
         patch("app.qa_verifier.build_timeline_contact_sheet", return_value=tmp_path / "csheet.png"):

        report = run_project_qa(project, pdir, odir)
        assert report["qa_passed"] is True
        assert report["media_audit"]["status"] == "ok"
        assert len(report["media_audit"]["blocking_reasons"]) == 0

def test_media_rights_gate_editorial_mode_allows_promotional_fair_use(tmp_path):
    """Em modo editorial (jornalístico/análise), trailers promocionais oficiais com Fair Use são aceitos."""
    pdir = tmp_path / "proj_editorial"
    pdir.mkdir()
    odir = tmp_path / "out_editorial"
    odir.mkdir()
    vfile = odir / "single_video.mp4"
    vfile.touch()

    project = MusicStoryProject(
        id="proj_editorial",
        title="Análise Editorial GTA 6",
        publish_intent="editorial",
        audio_file="inputs/audio_licenciado.mp3",
        outputs={"youtube_short": str(vfile)},
        scenes=[
            SceneItem(
                index=1,
                narration_text="Texto de comentário crítico e jornalístico",
                video_path="inputs/gta6/GTA Trailer 1.mp4",
            )
        ],
        used_media=[
            {
                "media_id": "gta_trailer_1",
                "filename": "GTA Trailer 1.mp4",
                "role": "scene_video",
                "source_url": "https://www.youtube.com/watch?v=QdBZY2fkU-0",
                "copyright_owner": "Rockstar Games / Take-Two Interactive",
                "license_type": "promotional_fair_use",
                "authorization_status": "official_permission",
                "commercial_use_allowed": False,
                "attribution_required": True,
                "risk_level": "medio",
                "scenes_used": [1],
            }
        ]
    )

    with patch("app.qa_verifier.probe_video_and_loudness", return_value=make_probe_success()), \
         patch("app.qa_verifier.score_project_slideshow_risk", return_value={"verdict": "strong", "score": 1.0}), \
         patch("app.qa_verifier.write_packed_timeline", return_value=tmp_path / "timeline.txt"), \
         patch("app.qa_verifier.build_timeline_contact_sheet", return_value=tmp_path / "csheet.png"):

        report = run_project_qa(project, pdir, odir)
        assert report["qa_passed"] is True
        assert report["media_audit"]["status"] != "blocked"
        assert len(report["media_audit"]["blocking_reasons"]) == 0

def test_media_rights_gate_editorial_mode_still_blocks_unknown_media(tmp_path):
    """Mesmo em modo editorial, mídia de autor desconhecido/não verificado continua BLOQUEADA."""
    pdir = tmp_path / "proj_ed_unknown"
    pdir.mkdir()
    odir = tmp_path / "out_ed_unknown"
    odir.mkdir()
    vfile = odir / "single_video.mp4"
    vfile.touch()

    project = MusicStoryProject(
        id="proj_ed_unknown",
        title="Análise com vídeo misterioso",
        publish_intent="editorial",
        outputs={"youtube_short": str(vfile)},
        scenes=[
            SceneItem(
                index=1,
                narration_text="Comentário",
                video_path="inputs/gta6/ReelGTARealista.mp4",
            )
        ],
        used_media=[
            {
                "media_id": "reel_gta_realista",
                "filename": "ReelGTARealista.mp4",
                "role": "scene_video",
                "source_url": "desconhecido",
                "copyright_owner": "Desconhecido",
                "license_type": "desconhecido",
                "authorization_status": "unknown",
                "commercial_use_allowed": False,
                "attribution_required": True,
                "risk_level": "alto",
                "scenes_used": [1],
            }
        ]
    )

    with patch("app.qa_verifier.probe_video_and_loudness", return_value=make_probe_success()), \
         patch("app.qa_verifier.score_project_slideshow_risk", return_value={"verdict": "strong", "score": 1.0}), \
         patch("app.qa_verifier.write_packed_timeline", return_value=tmp_path / "timeline.txt"), \
         patch("app.qa_verifier.build_timeline_contact_sheet", return_value=tmp_path / "csheet.png"):

        report = run_project_qa(project, pdir, odir)
        assert report["qa_passed"] is False
        assert report["media_audit"]["status"] == "blocked"
        assert len(report["media_audit"]["blocking_reasons"]) >= 1
