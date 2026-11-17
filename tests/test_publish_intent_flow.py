from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.gaming_media as gm
import app.web_server as ws
from app.config import get_ffmpeg_exe
from app.job_queue import JobQueueManager, setup_production_stage_handlers
from app.models import MusicStoryProject, SceneItem


def test_t7_0_synthetic_trailer_blend_respects_publish_intent(tmp_path: Path):
    """T7.0: Clipe sintético catalogado com commercial_use_allowed=False é omitido em commercial e usado em editorial."""
    clip = tmp_path / "GTA Trailer 1.mp4"
    ffmpeg = get_ffmpeg_exe()
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "lavfi",
            "-i", "testsrc=duration=8:size=640x360:rate=30",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )

    pdir_comm = tmp_path / "proj_comm"
    pdir_comm.mkdir()
    proj_comm = MusicStoryProject(
        id="t7-comm",
        title="GTA 6 Trailer Comercial",
        song_title="GTA 6 Trailer Comercial",
        artist="GTA 6",
        publish_intent="commercial",
        scenes=[SceneItem(index=1, title="Cena 1", narration_text="GTA 6 em Vice City com Lucia e Jason.")],
    )

    pdir_ed = tmp_path / "proj_ed"
    pdir_ed.mkdir()
    proj_ed = MusicStoryProject(
        id="t7-ed",
        title="GTA 6 Trailer Editorial",
        song_title="GTA 6 Trailer Editorial",
        artist="GTA 6",
        publish_intent="editorial",
        scenes=[SceneItem(index=1, title="Cena 1", narration_text="GTA 6 em Vice City com Lucia e Jason.")],
    )

    with patch.object(gm, "GTA6_INPUTS_DIR", tmp_path):
        gm.auto_blend_gaming_clips_and_images(proj_comm, pdir_comm, force_reassign=True)
        gm.auto_blend_gaming_clips_and_images(proj_ed, pdir_ed, force_reassign=True)

    assert proj_comm.scenes[0].video_path is None
    assert proj_ed.scenes[0].video_path is not None
    assert Path(proj_ed.scenes[0].video_path).name == "GTA Trailer 1.mp4"


def test_rights_preview_commercial_vs_editorial(tmp_path: Path, monkeypatch):
    """T7: GET /api/projects/{id}/rights-preview mostra trailers bloqueados em commercial e liberados em editorial."""
    monkeypatch.setattr(ws, "PROJECTS_DIR", tmp_path)
    client = TestClient(ws.app, base_url="http://127.0.0.1:8765")

    proj_comm = MusicStoryProject(id="prev-comm", title="Comercial", publish_intent="commercial")
    proj_ed = MusicStoryProject(id="prev-ed", title="Editorial", publish_intent="editorial")
    ws._save_project(proj_comm)
    ws._save_project(proj_ed)

    r_comm = client.get("/api/projects/prev-comm/rights-preview")
    assert r_comm.status_code == 200
    d_comm = r_comm.json()
    assert d_comm["publish_intent"] == "commercial"
    assert d_comm["allowed_trailers_count"] == 0
    assert d_comm["blocked_trailers_count"] >= 4
    assert "Nenhum trailer da biblioteca" in d_comm["warning"]
    assert all(b["reason"] for b in d_comm["blocked"])

    r_ed = client.get("/api/projects/prev-ed/rights-preview")
    assert r_ed.status_code == 200
    d_ed = r_ed.json()
    assert d_ed["publish_intent"] == "editorial"
    assert d_ed["allowed_trailers_count"] >= 4
    assert d_ed["warning"] == ""


def test_job_queue_propagates_publish_intent_to_created_project(tmp_path: Path, monkeypatch):
    """T7: publish_intent informado em input_data da fila deve chegar ao projeto criado pela etapa roteiro."""
    monkeypatch.setattr(ws, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

    fake_script = {
        "resolved_topic": "GTA 6 Editorial Teste",
        "resolved_year": "2026",
        "resolved_angle": "Análise",
        "viral_archetype": "noticia",
        "youtube_title": "GTA 6 Editorial",
        "youtube_description": "Descrição",
        "youtube_tags": ["gta6"],
        "tiktok_caption": "cap",
        "hook_title": "Gancho",
        "narration_full": "Texto",
        "scenes": [{"index": 1, "title": "Cena 1", "narration_text": "Texto da cena 1"}],
        "instagram_caption": "ig",
        "hashtags": ["#GTA6"],
    }

    with patch("app.gaming_scriptwriter.generate_gaming_script", return_value=fake_script), \
         patch("app.gaming_media.auto_select_gaming_soundtrack", return_value=(None, 0.0)), \
         patch("app.gaming_media.auto_blend_gaming_clips_and_images", return_value=None):
        mgr = JobQueueManager(storage_dir=tmp_path / "jobs", auto_start_worker=False)
        setup_production_stage_handlers(mgr)
        # Substitui etapas posteriores a roteiro por no-ops para isolar o teste
        mgr.register_stage_handler("tts", lambda j: None)
        mgr.register_stage_handler("render", lambda j: None)
        mgr.register_stage_handler("qa", lambda j: None)

        job = mgr.create_job(
            topic_title="GTA 6 Editorial Teste",
            input_data={
                "publish_intent": "editorial",
                "status_confiabilidade": "confirmado",
                "source": "Rockstar Games",
            },
        )
        mgr.start_worker()
        mgr._queue.join()
        mgr.stop_worker()

    done = mgr.get_job(job.id)
    assert done.status == "ok"
    assert done.project_id
    loaded_proj = ws._load_project(done.project_id)
    assert loaded_proj.publish_intent == "editorial"
