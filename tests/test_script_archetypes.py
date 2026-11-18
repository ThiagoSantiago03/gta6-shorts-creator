import pytest
from app.gaming_scriptwriter import (
    generate_gaming_script,
    synthesize_gaming_short_script,
    _pick_viral_archetype,
    VIRAL_ARCHETYPES,
)
from app.models import MusicStoryProject, SceneItem
from app.web_server import regenerate_gaming_project_script, RegenerateGamingScriptBody, _save_project

def test_pick_viral_archetype_by_context_and_confidence():
    # 1. Rumor -> sempre teoria_rumor
    arch_rumor = _pick_viral_archetype(
        topic="Suposto mapa de GTA 6 vaza em fórum",
        context="rumor de insider",
        requested="auto",
        status_confiabilidade="rumor"
    )
    assert arch_rumor == "teoria_rumor"

    # 2. Polêmica / Preço / Adiamento -> polemica_comparacao
    arch_polemica = _pick_viral_archetype(
        topic="GTA 6 pode custar 80 dólares no lançamento",
        context="analistas debatem se o preço é justo",
        requested="auto",
        status_confiabilidade="reportado"
    )
    assert arch_polemica == "polemica_comparacao"

    # 3. Curiosidade / Detalhes / Físicas -> curiosidade
    arch_curiosidade = _pick_viral_archetype(
        topic="Física de água e detalhes escondidos dos NPCs em GTA 6",
        context="easter eggs e segredos nos trailers",
        requested="auto",
        status_confiabilidade="reportado"
    )
    assert arch_curiosidade == "curiosidade"

    # 4. Notícia oficial / Lançamento geral -> noticia
    arch_noticia = _pick_viral_archetype(
        topic="Rockstar confirma janela de lançamento oficial de GTA 6",
        context="comunicado oficial aos investidores",
        requested="auto",
        status_confiabilidade="confirmado"
    )
    assert arch_noticia == "noticia"

    # 5. Sobrescrita manual explícita
    arch_explicit = _pick_viral_archetype(
        topic="Notícia qualquer",
        requested="polemica_comparacao",
    )
    assert arch_explicit == "polemica_comparacao"

def test_5_different_scripts_have_distinct_structures_and_varied_ctas():
    """Critério de Aceite Tarefa 2.1: 5 roteiros com estruturas e CTAs visivelmente diferentes."""
    test_cases = [
        # 1. Notícia oficial
        {
            "topic": "Rockstar Games confirma data oficial de lançamento de GTA 6",
            "confidence": "Oficial / Confirmado",
            "expected_arch": "noticia",
            "expected_scenes": 5,
        },
        # 2. Descoberta / Curiosidade
        {
            "topic": "Segredos escondidos e detalhes da física de água em GTA 6",
            "confidence": "Notícia / Imprensa",
            "expected_arch": "curiosidade",
            "expected_scenes": 5,
        },
        # 3. Teoria / Rumor
        {
            "topic": "Vazamento em fórum indica suposto terceiro protagonista em GTA 6",
            "confidence": "Rumor / Não verificado",
            "expected_arch": "teoria_rumor",
            "expected_scenes": 5,
        },
        # 4. Polêmica / Comparação
        {
            "topic": "Preço de 80 dólares de GTA 6 divide opiniões dos jogadores",
            "confidence": "Notícia / Imprensa",
            "expected_arch": "polemica_comparacao",
            "expected_scenes": 4,
        },
        # 5. Notícia técnica
        {
            "topic": "Rockstar detalha plataformas e suporte a 60 FPS em GTA 6",
            "confidence": "Notícia / Imprensa",
            "expected_arch": "noticia",
            "expected_scenes": 5,
        },
    ]

    generated_ctas = set()
    generated_titles_sets = []

    for tc in test_cases:
        res = synthesize_gaming_short_script(
            topic_title=tc["topic"],
            game_name="GTA 6",
            fact_confidence=tc["confidence"],
        )

        assert res["viral_archetype"] == tc["expected_arch"]
        assert len(res["scenes"]) == tc["expected_scenes"]
        assert res["estimated_duration_sec"] <= 58.0

        cta = res["scenes"][-1]["narration_text"]
        generated_ctas.add(cta)

        scene_titles = tuple(s["title"] for s in res["scenes"])
        generated_titles_sets.append(scene_titles)

    # Garante variedade de CTAs: não pode haver CTA repetido entre os 4 arquétipos principais
    assert len(generated_ctas) >= 4

    # Garante variedade estrutural: títulos de cenas e atos diferem entre os arquétipos
    assert len(set(generated_titles_sets)) >= 4

def test_regenerate_gaming_script_allows_switching_archetype(tmp_path, monkeypatch):
    """Garante que a rota de regenerar roteiro permite trocar o arquétipo e recalcular as cenas."""
    monkeypatch.setattr("app.web_server.PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("app.config.PROJECTS_DIR", tmp_path)

    project = MusicStoryProject(
        id="proj_arch_switch",
        niche="gaming_news",
        title="Novidades de GTA 6",
        song_title="Novidades de GTA 6",
        artist="GTA 6",
        fact_confidence="Notícia / Imprensa",
        viral_archetype="noticia",
        scenes=[SceneItem(index=1, narration_text="Texto antigo", duration_sec=5.0)],
    )
    _save_project(project)

    # Troca para polemica_comparacao (4 cenas)
    body = RegenerateGamingScriptBody(viral_archetype="polemica_comparacao")
    updated = regenerate_gaming_project_script("proj_arch_switch", body)

    assert updated["viral_archetype"] == "polemica_comparacao"
    assert len(updated["scenes"]) == 4

    # Troca para curiosidade (5 cenas)
    body2 = RegenerateGamingScriptBody(viral_archetype="curiosidade")
    updated2 = regenerate_gaming_project_script("proj_arch_switch", body2)

    assert updated2["viral_archetype"] == "curiosidade"
    assert len(updated2["scenes"]) == 5


def test_4_archetypes_for_same_topic_produce_distinct_hooks_and_scene_orders():
    """T14.3: Gerar 4 roteiros para a MESMA pauta nos 4 arquétipos deve produzir ganchos e ordens de cena distintos."""
    from unittest.mock import patch

    same_topic = "Rockstar revela detalhes do mapa de Vice City em GTA 6"
    fake_dossier = {
        "article_summary": "A Rockstar detalhou novos bairros, clima dinâmico e escala do mapa de Vice City.",
        "article_facts": [
            "O mapa de Leonida inclui pântanos, praias e áreas urbanas densas.",
            "A engine RAGE traz simulação avançada de água e tráfego dinâmico.",
            "Os desenvolvedores confirmaram exploração interna em dezenas de prédios.",
            "A comunidade debate o tamanho final da instalação nos consoles atuais.",
        ],
    }

    hooks: list[str] = []
    scene_orders: list[tuple[str, ...]] = []
    resolved_archetypes: list[str] = []

    with patch("app.gaming_scriptwriter.fetch_gaming_topic_dossier", return_value=fake_dossier):
        for arch in ("noticia", "curiosidade", "teoria_rumor", "polemica"):
            res = generate_gaming_script(
                topic_title=same_topic,
                game_name="GTA 6",
                fact_confidence="Notícia / Imprensa",
                script_engine="offline_template",
                viral_archetype=arch,
            )
            scenes = res["scenes"]
            assert len(scenes) >= 4
            hooks.append(scenes[0]["narration_text"].strip().lower())
            scene_orders.append(tuple(s["title"] for s in scenes))
            resolved_archetypes.append(res["viral_archetype"])

    assert resolved_archetypes == ["noticia", "curiosidade", "teoria_rumor", "polemica_comparacao"]
    assert len(set(hooks)) == 4, f"Ganchos repetidos entre os 4 arquétipos: {hooks}"
    assert len(set(scene_orders)) == 4, f"Estruturas/ordens de cenas repetidas: {scene_orders}"

