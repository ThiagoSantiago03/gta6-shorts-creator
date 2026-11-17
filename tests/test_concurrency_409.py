import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from app.web_server import app, _save_project
from app.models import MusicStoryProject, SceneItem

client = TestClient(app, base_url="http://127.0.0.1:8765")

def test_optimistic_locking_revision_increment(tmp_path, monkeypatch):
    """Garante que salvar um projeto incrementa atomicamente a sua revisão."""
    monkeypatch.setattr("app.web_server.PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("app.config.PROJECTS_DIR", tmp_path)

    project = MusicStoryProject(
        id="proj_rev_test",
        title="Teste Concorrência",
        scenes=[SceneItem(index=1, narration_text="Texto")]
    )
    assert project.revision == 1
    _save_project(project)
    assert project.revision == 2

    # Salva novamente
    _save_project(project)
    assert project.revision == 3

def test_update_project_conflict_409(tmp_path, monkeypatch):
    """Garante que enviar uma revisão desatualizada via PUT retorna HTTP 409 Conflict."""
    monkeypatch.setattr("app.web_server.PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("app.config.PROJECTS_DIR", tmp_path)

    project = MusicStoryProject(
        id="proj_conflict",
        title="Projeto Original",
        scenes=[SceneItem(index=1, narration_text="Texto original")]
    )
    _save_project(project)
    current_rev = project.revision  # rev 2

    # Modificação concorrente 1 (sucedida):
    payload_ok = project.model_dump()
    payload_ok["title"] = "Alteração 1"
    response1 = client.put(f"/api/projects/{project.id}", json=payload_ok)
    assert response1.status_code == 200
    assert response1.json()["revision"] == current_rev + 1  # rev 3

    # Modificação concorrente 2 (com a revisão antiga, que agora está desatualizada):
    payload_stale = project.model_dump()
    payload_stale["revision"] = current_rev  # rev 2 (antiga)
    payload_stale["title"] = "Alteração Concorrente Tardia"
    response2 = client.put(f"/api/projects/{project.id}", json=payload_stale)
    assert response2.status_code == 409
    data2 = response2.json()
    assert "Conflito de versão" in data2["detail"]
