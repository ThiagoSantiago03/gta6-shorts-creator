import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.models import TopicTrendItem, MusicStoryProject
from app.gaming_radar import (
    classify_confidence_and_status,
    extract_significant_tokens,
    calculate_token_similarity,
    _is_already_covered,
    fetch_daily_gaming_radar,
)
from app.gaming_scriptwriter import synthesize_gaming_short_script
from app.web_server import app

client = TestClient(app)

def test_classify_confidence_and_status_4_classes():
    # 1. Confirmado
    conf, status = classify_confidence_and_status("Rockstar Games divulga Trailer 2 de GTA 6 com data", "Rockstar Newswire")
    assert status == "confirmado"
    assert "Oficial" in conf

    # 2. Reportado
    conf, status = classify_confidence_and_status("Bloomberg analisa faturamento esperado de GTA 6", "Bloomberg")
    assert status == "reportado"
    assert "Notícia" in conf or "Imprensa" in conf

    # 3. Rumor
    conf, status = classify_confidence_and_status("Vazamento em fórum indica suposto mapa gigante de GTA 6", "Reddit")
    assert status == "rumor"
    assert "Rumor" in conf

    # 4. Não verificado (clickbait / sem fonte confiável)
    conf, status = classify_confidence_and_status("GTA 6 pode ser o maior jogo de todos os tempos", "desconhecido")
    assert status == "nao_verificado"

def test_deduplication_clusters_rewritten_stories():
    title_a = "Rockstar confirma data de lançamento do Trailer 2 de GTA 6 para maio"
    title_b = "Trailer 2 de GTA 6 tem lançamento em maio confirmado oficialmente"

    tokens_a = extract_significant_tokens(title_a)
    tokens_b = extract_significant_tokens(title_b)

    sim = calculate_token_similarity(tokens_a, tokens_b)
    # Devem compartilhar tokens-chave como 'trailer', 'maio'
    assert sim >= 0.33
    assert len(tokens_a.intersection(tokens_b)) >= 2

def test_rumor_script_forbids_affirmative_claims():
    """Garante que pautas de rumor geram roteiros com linguagem condicional/atributiva, sem afirmar fatos."""
    script_data = synthesize_gaming_short_script(
        topic_title="Suposto vazamento da polícia e mecânicas em GTA 6",
        game_name="GTA 6",
        topic_angle="Análise dos supostos vazamentos sobre o sistema de polícia",
        source_url="https://reddit.com/r/gta6",
        fact_confidence="Rumor / Não verificado",
    )

    narration = script_data["narration_full"].lower()

    # Não pode conter afirmações categóricas
    assert "a rockstar confirmou" not in narration
    assert "lançamento oficial está confirmado" not in narration
    assert "vazaram os números reais" not in narration

    # Deve conter linguagem atributiva / de rumor
    assert any(term in narration for term in ("rumor", "rumores", "supost", "vazament", "circula", "fórum", "não oficial", "especula"))

    # Descrição do YouTube deve conter bloco de fontes/créditos
    assert "Créditos & Fontes de Mídia:" in script_data["youtube_description"]

def test_batch_daily_skips_unverified_topics(monkeypatch, tmp_path):
    """Garante que a geração de lote diário ignora pautas classificadas como 'nao_verificado'."""
    monkeypatch.setattr("app.web_server.PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("app.config.PROJECTS_DIR", tmp_path)

    mock_items = [
        TopicTrendItem(
            id="item-unverified",
            title="Opinião aleatória sobre GTA 6 sem fontes",
            source="desconhecido",
            confidence="Não verificado",
            status_confiabilidade="nao_verificado",
        ),
        TopicTrendItem(
            id="item-verified",
            title="Rockstar divulga comunicado sobre avanço de GTA 6",
            source="Rockstar Games",
            confidence="Oficial / Confirmado",
            status_confiabilidade="confirmado",
        ),
    ]

    with patch("app.web_server.fetch_daily_gaming_radar", return_value=mock_items), \
         patch("app.web_server.auto_blend_gaming_clips_and_images", return_value={}):
        resp = client.post("/api/automation/batch-daily", json={"count": 5, "geo": "BR"})
        assert resp.status_code == 200
        data = resp.json()
        projects = data.get("projects", [])
        assert len(projects) == 1
        assert projects[0]["status_confiabilidade"] == "confirmado"
        assert "comunicado" in projects[0]["title"].lower()
