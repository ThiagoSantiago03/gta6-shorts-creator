from __future__ import annotations

from pathlib import Path
from fastapi.testclient import TestClient

import app.web_server as ws
from app.models import MusicStoryProject


def test_toggle_posted_requires_qa_and_human_approval(tmp_path: Path, monkeypatch):
    """T14.1: Marcar como postado exige QA aprovado E human_approved=True."""
    monkeypatch.setattr(ws, "PROJECTS_DIR", tmp_path)
    client = TestClient(ws.app, base_url="http://127.0.0.1:8765")

    # 1. Projeto sem QA -> bloqueado com 400
    proj = MusicStoryProject(id="p-gate-1", title="Teste Gate Humano")
    ws._save_project(proj)

    r1 = client.post("/api/projects/p-gate-1/toggle-posted", json={"is_posted": True})
    assert r1.status_code == 400
    assert "QA" in r1.json()["detail"]

    # 2. Projeto com QA aprovado (ou Pronto com Ressalvas), mas sem human_approved -> bloqueado com 400
    proj.qa_report = {
        "qa_passed": False,
        "is_ready_for_publication": True,
        "requires_manual_confirmation": True,
        "verdict": "Pronto com Ressalvas (Requer Confirmação Editorial)",
    }
    ws._save_project(proj)

    r2 = client.post("/api/projects/p-gate-1/toggle-posted", json={"is_posted": True})
    assert r2.status_code == 400
    assert "Aprovação humana" in r2.json()["detail"]

    # 3. Aprovação humana explícita -> libera publicação
    r_app = client.post(
        "/api/projects/p-gate-1/human-approve",
        json={"approved": True, "notes": "Revisado manualmente"},
    )
    assert r_app.status_code == 200
    assert r_app.json()["human_approved"] is True
    assert r_app.json()["human_approved_at"] is not None

    r3 = client.post(
        "/api/projects/p-gate-1/toggle-posted",
        json={"is_posted": True, "platform": "youtube", "url": "https://youtube.com/shorts/abc"},
    )
    assert r3.status_code == 200
    assert r3.json()["is_posted"] is True

    # 4. Desmarcar continua permitido
    r4 = client.post("/api/projects/p-gate-1/toggle-posted", json={"is_posted": False})
    assert r4.status_code == 200
    assert r4.json()["is_posted"] is False
