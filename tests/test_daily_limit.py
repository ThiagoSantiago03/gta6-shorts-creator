from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

import app.web_server as ws
from app.config import get_max_shorts_per_day
from app.job_queue import JobQueueManager
from app.models import TopicTrendItem


def test_max_shorts_per_day_default_and_env_override(monkeypatch):
    """T14.2: MAX_SHORTS_PER_DAY usa padrão 3 e aceita configuração via ambiente."""
    monkeypatch.delenv("MAX_SHORTS_PER_DAY", raising=False)
    assert get_max_shorts_per_day() == 3

    monkeypatch.setenv("MAX_SHORTS_PER_DAY", "2")
    assert get_max_shorts_per_day() == 2

    monkeypatch.setenv("MAX_SHORTS_PER_DAY", "invalido")
    assert get_max_shorts_per_day() == 3


def test_batch_daily_and_queue_respect_max_shorts_per_day(tmp_path: Path, monkeypatch):
    """T14.2: Tanto /api/automation/batch-daily quanto /api/queue/jobs/batch respeitam MAX_SHORTS_PER_DAY."""
    monkeypatch.setenv("MAX_SHORTS_PER_DAY", "2")
    monkeypatch.setattr(ws, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
    client = TestClient(ws.app, base_url="http://127.0.0.1:8765")

    fake_items = [
        TopicTrendItem(id=f"t{i}", title=f"Pauta Oficial {i}", url=f"https://example.com/{i}", confidence="Oficial / Confirmado", status_confiabilidade="confirmado")
        for i in range(1, 6)
    ]
    fake_script = {
        "resolved_topic": "GTA 6", "resolved_year": "2026", "resolved_angle": "Notícia",
        "viral_archetype": "noticia", "youtube_title": "T", "youtube_description": "D",
        "youtube_tags": ["gta6"], "tiktok_caption": "tk", "hook_title": "H",
        "narration_full": "Texto", "scenes": [{"index": 1, "title": "C1", "narration_text": "Texto"}],
        "instagram_caption": "ig", "hashtags": ["#GTA6"],
    }

    with patch("app.web_server.fetch_daily_gaming_radar", return_value=fake_items), \
         patch("app.web_server.generate_gaming_script", return_value=fake_script), \
         patch("app.web_server.auto_select_gaming_soundtrack", return_value=(None, 0.0)), \
         patch("app.web_server.auto_blend_gaming_clips_and_images", return_value=None):
        resp = client.post("/api/automation/batch-daily", json={"count": 5, "query": "GTA 6", "geo": "BR"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["daily_limit"] == 2
        assert data["count"] == 2

    # Fila de jobs também aplica MAX_SHORTS_PER_DAY
    temp_mgr = JobQueueManager(storage_dir=tmp_path / "jobs", auto_start_worker=False)
    with patch("app.job_queue.JOB_MANAGER", temp_mgr):
        r_q = client.post(
            "/api/queue/jobs/batch",
            json={"topics": ["P1", "P2", "P3", "P4", "P5"], "publish_intent": "commercial"},
        )
        assert r_q.status_code == 200
        dq = r_q.json()
        assert dq["daily_limit"] == 2
        assert dq["count"] == 2
        assert dq["truncated_by_limit"] is True
