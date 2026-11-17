from fastapi.testclient import TestClient
from app.web_server import app

client = TestClient(app, base_url="http://127.0.0.1:8765")


def test_list_projects_endpoint():
    response = client.get("/api/projects")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_project_not_found():
    response = client.get("/api/projects/id-inexistente-12345")
    assert response.status_code == 404
