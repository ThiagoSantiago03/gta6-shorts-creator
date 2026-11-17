from __future__ import annotations

from fastapi.testclient import TestClient

from app.web_server import app


def test_rejects_untrusted_host_header():
    """T4: Host fora de ALLOWED_HOSTS deve ser rejeitado com 400."""
    client = TestClient(app, base_url="http://evil.example")
    resp = client.get("/api/projects")
    assert resp.status_code == 400


def test_rejects_foreign_origin_on_post():
    """T4: POST com Origin de domínio externo deve retornar 403."""
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    resp = client.post(
        "/api/config",
        json={},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403


def test_allows_local_origin_on_post():
    """T4: POST com Origin local (127.0.0.1) deve passar pelos middlewares de Host/Origin."""
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    resp = client.post(
        "/api/config",
        json={},
        headers={"Origin": "http://127.0.0.1:8765"},
    )
    assert resp.status_code not in (400, 403)
