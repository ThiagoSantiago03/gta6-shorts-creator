from fastapi.testclient import TestClient
from unittest.mock import patch
from app.web_server import app

client = TestClient(app, base_url="http://127.0.0.1:8765")

def test_api_config_masks_gemini_key():
    secret_key = "AIzaSySecretKeyForTestingGemini12345"
    with patch("app.web_server.get_gemini_api_key", return_value=secret_key):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_gemini_key"] is True
        assert data["masked_gemini_key"] == "AIzaSy...2345"
        assert secret_key not in resp.text
