from app.gaming_scriptwriter import generate_gaming_script


def test_offline_template_script_generation():
    result = generate_gaming_script(
        topic_title="Preço e Mapa de GTA 6 Revelados",
        game_name="GTA 6",
        topic_angle="Análise de Vazamentos",
        source_url="https://exemplo.com/noticia",
        fact_confidence="Rumor",
        script_engine="offline_template",
        viral_archetype="auto"
    )
    assert "scenes" in result
    assert len(result["scenes"]) == 6
    assert result["hook_title"]
    assert len(result["youtube_title"]) <= 100
    assert len(result["narration_full"]) > 50

    # Testar que o hook tem tamanho controlado (< 30 palavras)
    hook_words = len(result["scenes"][0]["narration_text"].split())
    assert hook_words <= 32
