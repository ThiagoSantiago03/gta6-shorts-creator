from unittest.mock import patch
from app.models import TopicTrendItem
from app.web_server import app
from fastapi.testclient import TestClient

client = TestClient(app, base_url="http://127.0.0.1:8765")

def test_batch_daily_resilience_partial_failure():
    """Verifica se uma falha em uma pauta não aborta o lote inteiro."""
    fake_items = [
        TopicTrendItem(id="t1", title="Pauta 1 Valida", url="https://example.com/1", confidence="Notícia / Imprensa"),
        TopicTrendItem(id="t2", title="Pauta 2 Que Falha", url="https://example.com/2", confidence="Rumor"),
        TopicTrendItem(id="t3", title="Pauta 3 Valida", url="https://example.com/3", confidence="Notícia / Imprensa"),
    ]

    call_count = 0

    def mock_generate_gaming_script(**kwargs):
        nonlocal call_count
        call_count += 1
        if kwargs.get("topic_title") == "Pauta 2 Que Falha":
            raise RuntimeError("Simulando falha de geracao na pauta 2")
        return {
            "resolved_topic": kwargs.get("topic_title"),
            "resolved_year": "2026",
            "resolved_angle": "Curiosidade",
            "viral_archetype": "investigative",
            "youtube_title": "Titulo Teste",
            "youtube_description": "Descricao Teste",
            "youtube_tags": ["gta6"],
            "tiktok_caption": "Legenda",
            "hook_title": "Gancho",
            "narration_full": "Texto narrado de teste.",
            "scenes": [
                {
                    "index": 1,
                    "segment": "Hook",
                    "text": "Texto cena 1",
                    "narration_text": "Texto cena 1",
                    "duration_sec": 5.0,
                    "broll_keyword": "gta6_trailer",
                    "music_state": "normal"
                }
            ],
            "instagram_caption": "Instagram",
            "hashtags": ["#gta6"],
        }

    with patch("app.web_server.fetch_daily_gaming_radar", return_value=fake_items), \
         patch("app.web_server.generate_gaming_script", side_effect=mock_generate_gaming_script):

        resp = client.post("/api/automation/batch-daily", json={"count": 3, "query": "GTA 6", "geo": "BR"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["count"] == 2
        assert data["failures_count"] == 1
        assert len(data["projects"]) == 2
        assert len(data["failures"]) == 1
        assert data["failures"][0]["title"] == "Pauta 2 Que Falha"
        assert "Simulando falha" in data["failures"][0]["error"]
