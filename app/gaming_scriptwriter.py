import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import get_gemini_api_key, get_text_model
from app.gaming_radar import fetch_gaming_topic_dossier, translate_to_en, translate_to_pt_br
from app.models import SceneItem

logger = logging.getLogger(__name__)

VIRAL_ARCHETYPES = {
    "noticia": "Notícia Oficial (Gancho → Fato → Contexto → Consequência → Pergunta)",
    "curiosidade": "Descoberta & Curiosidade (Gancho → Evidência → Detalhe → Revelação → Fechamento)",
    "teoria_rumor": "Teoria & Rumor (Pergunta → Evidências → Hipótese → Contraponto → Debate)",
    "polemica_comparacao": "Polêmica & Comparação (Afirmação → Argumentos → Contraponto → Conclusão)",
    # Aliases retrocompatíveis
    "contraste_detalhe": "Contraste ('Mas tem um detalhe que ninguém viu...')",
    "raio_x_numeros": "Raio-X de Dados ('R$ 400 / 212 GB / 60 FPS')",
    "gameplay_imersao": "Gameplay & Reação ('Funciona assim na prática...')",
    "lista_open_loop": "Lista ('3 motivos — e o último vai te chocar...')",
}


def _clean_short_topic(topic: str) -> str:
    t = re.sub(r"^(?:GTA\s*6\s*[:\-\—]\s*)+", "", (topic or "").strip(), flags=re.IGNORECASE)
    t = re.sub(r"^(?:Grand\s*Theft\s*Auto\s*(?:VI|6)\s*[:\-\—]\s*)+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+[\-\—|]\s+(?:IGN|Voxel|The Enemy|TechTudo|Olhar Digital|Canaltech|G1|Flow Games|TudoCelular).*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*#\w+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or "GTA 6"


def _pick_viral_archetype(
    topic: str,
    context: str = "",
    requested: str = "auto",
    status_confiabilidade: str = "reportado",
) -> str:
    if requested and requested != "auto" and requested in VIRAL_ARCHETYPES:
        return requested

    blob = f"{topic} {context}".lower()

    # 1. Rumor / Vazamento -> sempre teoria_rumor (M-02 / Tarefa 2.1)
    if (
        status_confiabilidade == "rumor"
        or any(k in blob for k in ("rumor", "rumores", "vaza", "vazou", "leak", "supost", "teoria", "insider"))
    ):
        return "teoria_rumor"

    # 2. Polêmica / Comparação / Preço / Adiamento -> polemica_comparacao
    if any(
        k in blob
        for k in (
            "preço", "preco", "custo", "bilhões", "bilhoes", "orçamento", "caro",
            "80 dólares", "adiado", "adiamento", "pc vs", "cancelad", "polêmica", "polemica",
            "vs", "versus", "disco físico"
        )
    ):
        return "polemica_comparacao"

    # 3. Curiosidade / Descoberta / Detalhes / Físicas / NPCs
    if any(
        k in blob
        for k in (
            "segredo", "easter egg", "curiosidade", "escondid", "descob",
            "física", "fisica", "água", "animais", "npcs", "mistério", "misterio"
        )
    ):
        return "curiosidade"

    # 4. Default: Notícia Oficial
    return "noticia"


_JOURNALESE_PREFIX_RE = re.compile(
    r"^(?:"
    r"(?:segundo|de acordo com|conforme|em entrevista (?:à|ao|para)|em nota divulgada pelo?|"
    r"em seu blog oficial,|em comunicado oficial,|segundo o portal|segundo relatos|"
    r"no entanto,|contudo,|entretanto,|além disso,|portanto,|dessa forma,|assim,|"
    r"podemos pegar|antes de tentar descobrir|basta ver tudo o que foi feito até agora com|"
    r"quem acompanha a indústria|em uma reportagem recente do jornalista|o artigo de schreier confirma que)\s+"
    r")+",
    flags=re.IGNORECASE,
)


def _clean_narrative_sentence(text: str, max_words: int = 28) -> str:
    """Sanitize and close a spoken narrative sentence with 100% complete syntactic integrity."""
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = re.sub(r"\b(?:Imagem|Foto|Crédito|Créditos|Fonte|Reprodução)\s*:\s*[^.!?]+[.!?]?", "", t, flags=re.IGNORECASE)
    t = re.sub(r"https?://\S+|pic\.twitter\.com/\S+|t\.co/\S+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"[@#]\w+", "", t)
    t = re.sub(r"[\U00010000-\U0010ffff\u2600-\u27bf]", "", t)
    t = re.sub(r"\s*\([^)]{0,45}\)", "", t)
    t = re.sub(r"\s+([.,!?;:])", r"\1", t)

    # Resolve vague phrasing into clear, recognizable game names
    t = re.sub(r"\bo jogo de faroeste de mundo aberto\b", "Red Dead Redemption 2", t, flags=re.IGNORECASE)
    t = re.sub(r"\bo jogo protagonizado por Arthur Morgan\b", "Red Dead Redemption 2", t, flags=re.IGNORECASE)
    t = re.sub(r"\ba empresa\b", "a Rockstar", t, flags=re.IGNORECASE)
    t = re.sub(r"\ba produtora\b", "a Rockstar", t, flags=re.IGNORECASE)

    t = _JOURNALESE_PREFIX_RE.sub("", t).strip()
    if not t:
        return ""

    words = t.split()
    if len(words) > max_words:
        # Check if there is a subordinate clause boundary we can cut cleanly before
        sub_match = re.search(r"^(.*?),\s*(?:o que|que|onde|embora|sendo|enquanto|mas)\b", t, flags=re.IGNORECASE)
        if sub_match and 10 <= len(sub_match.group(1).split()) <= max_words:
            t = sub_match.group(1).strip()
        else:
            prefix = " ".join(words[:max_words])
            m = re.search(r"^(.{35,}?)[,;:]\s+\S+$", prefix)
            if m and len(m.group(1).split()) >= 10:
                t = m.group(1).strip()
            else:
                t = prefix.strip()

    # Safety loop: strip trailing prepositions, conjunctions, relative pronouns, and auxiliary verbs
    bad_trailing = (
        r"\s+(?:ou|e|de|do|da|dos|das|no|na|nos|nas|em|para|pra|com|por|que|se|ao|à|um|uma|"
        r"seu|sua|mas|está|estão|foi|ser|ter|faria|seria|iria|vai|vão|tinha|tinham|sendo|como|"
        r"sobre|onde|quando|pelo|pela|pelos|pelas|num|numa|dele|dela|deles|delas|isso|esse|essa|"
        r"esses|essas|aquele|aquela|o|a|os|as)\s*$"
    )
    for _ in range(6):
        t2 = re.sub(bad_trailing, "", t, flags=re.IGNORECASE).strip()
        if t2 == t:
            break
        t = t2

    t = t.rstrip(",;:— -")
    if t and t[0].islower() and len(t) > 1:
        t = t[0].upper() + t[1:]
    if not t.endswith((".", "!", "?")):
        t += "."
    return t


def _humanize_fact(raw_sentence: str, connector: str = "", max_words: int = 27) -> str:
    core = _clean_narrative_sentence(raw_sentence, max_words=max_words)
    if not core:
        return ""
    if not connector:
        return core
    body = core[0].lower() + core[1:] if len(core) > 1 and not re.match(r"^(?:GTA|PS5|Xbox|PC|Rockstar|Take-Two|Lucia|Jason|Sony|K9|DualSense|SSD|Red Dead)\b", core) else core
    if connector.endswith((":", "—", "!", "?")):
        return f"{connector} {core}"
    return f"{connector} {body}"


def _extract_highlight_keywords(text: str, game_name: str) -> list[str]:
    candidates = re.findall(
        r"\b(?:\d+[\.,]?\d*\s?(?:GB|TB|FPS|h|horas|dias|anos|milhões|bilhões|%)|"
        r"GTA|Rockstar|Take-Two|Leonida|Vice|PS5|DualSense|Xbox|Cloud|PC|Lucia|Jason|Sony|RAGE|SSD|K9|Red Dead)\b",
        text,
        flags=re.IGNORECASE,
    )
    out: list[str] = []
    for c in candidates:
        tok = c.strip()
        if tok and tok.lower() not in {x.lower() for x in out}:
            out.append(tok)
    if not out:
        out = [game_name.split()[0], "GTA", "Rockstar"]
    return out[:4]


def _extract_concrete_en_terms(text_pt: str, max_terms: int = 5) -> str:
    en_txt = translate_to_en(text_pt) or text_pt
    stop = {
        "the", "and", "for", "with", "from", "into", "about", "that", "this", "what", "when", "where",
        "your", "have", "has", "will", "are", "was", "were", "can", "could", "just", "today", "here",
        "look", "heavy", "news", "detail", "details", "waiting", "revealed", "explained", "see", "like",
        "works", "turn", "turns", "tell", "right", "now", "one", "live", "best", "life", "more", "some",
        "been", "very", "much", "also", "then", "than", "even", "them", "they", "you", "who",
        "gta", "grand", "theft", "auto", "rockstar", "games", "game", "video",
    }
    words = [
        w
        for w in re.findall(r"[A-Za-z0-9]{3,}", en_txt)
        if w.lower() not in stop and not w.isdigit()
    ]
    dedup: list[str] = []
    for w in words:
        if w.lower() not in {x.lower() for x in dedup}:
            dedup.append(w)
    return " ".join(dedup[:max_terms])


def _topic_visual_queries(
    scene_text: str,
    topic: str,
    idx: int,
    game_name: str = "GTA 6",
) -> tuple[str, str]:
    game = game_name.strip() or "GTA 6"
    scene_low = scene_text.lower()
    en_focus = _extract_concrete_en_terms(scene_text, max_terms=4)

    if any(k in scene_low for k in ("custo", "orçamento", "bilhão", "bilhões", "milhões", "caro", "dinheiro")):
        return (f"{game} official high production budget teaser", f"{game} official reveal graphics trailer")
    if any(k in scene_low for k in ("pc", "rtx", "dlss", "placa de vídeo", "steam", "rockstar launcher")):
        return (f"{game} PC version release screenshot", f"{game} PC graphics comparison")
    if any(k in scene_low for k in ("pré-venda", "pre-venda", "pré-carreg", "preload", "edição", "física", "disco", "capa")):
        return (f"{game} pre order edition box art", f"{game} official cover art reveal")
    if any(k in scene_low for k in ("gb", "tb", "armazenamento", "espaço", "download", "200 gb")):
        return (f"{game} download size storage console", f"{game} gameplay graphics showcase")
    if any(k in scene_low for k in ("polícia", "policia", "k9", "procurado", "tiro", "combate", "perseguição", "perseguicao", "assalto")):
        return (f"{game} police chase action scene", f"{game} trailer police pursuit action scene")
    if any(k in scene_low for k in ("cerveja", "donut", "comida", "beber", "comer", "bar")):
        return (f"{game} Jason drinking beer eating donut", f"{game} Trailer 2 Jason gameplay scenes")
    if any(k in scene_low for k in ("lucia", "jason", "casal", "prisão", "protagonista")):
        return (f"{game} Lucia Jason official screenshot", f"{game} Trailer 2 Lucia Jason scenes")
    if any(k in scene_low for k in ("mapa", "leonida", "vice city", "bairro", "região", "locais", "pântano")):
        return (f"{game} Leonida Vice City official map", f"{game} Vice City map locations trailer")
    if any(k in scene_low for k in ("adiamento", "adiado", "data oficial", "data de lançamento", "take-two", "strauss zelnick")):
        return (f"{game} release date Rockstar Games announcement", f"{game} official trailer Rockstar Games")
    if any(k in scene_low for k in ("trailer 2", "segundo trailer", "gameplay", "engine", "rage", "npc", "água")):
        return (f"{game} Trailer 2 4K gameplay detail", f"{game} Trailer 2 4K official breakdown")

    if en_focus:
        return (f"{game} {en_focus}", f"{game} {en_focus} trailer")

    return (f"{game} official trailer scene", f"{game} official gameplay scenes")


def _sanitize_for_rumor(text: str) -> str:
    """Replace unconditional affirmative statements with conditional/rumor phrasing (M-02)."""
    subs = [
        (r"\ba Rockstar confirmou\b", "segundo rumores recentes que circulam na comunidade"),
        (r"\ba Rockstar divulgou\b", "segundo vazamentos que circulam na internet"),
        (r"\bconfirmad[oa] pela Rockstar\b", "ainda não confirmado oficialmente pela Rockstar"),
        (r"\bconfirmad[oa] oficialmente\b", "segundo apurações e vazamentos"),
        (r"\blançamento oficial está confirmado\b", "segundo rumores, a previsão de lançamento seria"),
        (r"\bé oficial:\b", "segundo rumores:"),
        (r"\bVazaram os números reais\b", "Circulam boatos sobre os supostos custos"),
        (r"\bos números reais\b", "as supostas estimativas"),
        (r"\bA data oficial está confirmada\b", "Um suposto cronograma tem sido debatido"),
        (r"\bO lançamento oficial está confirmado\b", "Especula-se uma janela de lançamento"),
        (r"\bse liga no que a Rockstar confirmou!\b", "se liga no que anda circulando nos fóruns!"),
        (r"\bA grande novidade confirmada revela que\b", "Segundo os rumores mais comentados da comunidade,"),
    ]
    res = text
    for pattern, repl in subs:
        res = re.sub(pattern, repl, res, flags=re.IGNORECASE)
    return res


def _build_viral_hook(archetype: str, clean_topic: str, game_name: str, facts: list[str], is_rumor: bool = False) -> tuple[str, str]:
    low = clean_topic.lower()
    fact_blob = f"{clean_topic} {' '.join(facts[:3])}".lower()

    if any(w in fact_blob for w in ("custo", "orçamento", "bilhão", "bilhões", "caro", "gasto", "dinheiro")):
        if is_rumor:
            return f"RUMOR • CUSTO", f"Circulam novos rumores sobre os supostos custos de {game_name} — e os valores estão deixando todo mundo em choque!"
        return f"ORÇAMENTO • {game_name.upper()}", f"Vazaram os números da produção de {game_name} — e o custo total dessa história tá deixando todo mundo em choque!"
    if any(w in fact_blob for w in ("polícia", "policia", "k9", "cães", "perseguição", "tiro")):
        if is_rumor:
            return f"RUMOR • POLÍCIA", f"A polícia de {game_name} pode ser a mais perigosa de toda a franquia — se liga nos supostos vazamentos!"
        return f"POLÍCIA • {game_name.upper()}", f"A polícia de {game_name} vai ser a mais perigosa de toda a franquia — se liga no que os materiais indicam!"
    if any(w in fact_blob for w in ("data de lançamento", "versão para pc", "versão de pc", "adiamento", "adiado", "disco físico")):
        if is_rumor:
            return f"RUMOR • LANÇAMENTO", f"Novos boatos sobre o lançamento de {game_name} agitam as redes — se liga no que vazou!"
        return f"LANÇAMENTO • {game_name.upper()}", f"{game_name} tem novas movimentações de cronograma — e tem um detalhe tenso que você precisa saber agora!"
    if any(w in fact_blob for w in ("mapa", "leonida", "vice city", "tamanho do mapa", "locais")):
        if is_rumor:
            return f"RUMOR • MAPA", f"Supostos detalhes do mapa de {game_name} vazaram — e o tamanho desse mundo aberto pode ser colossal!"
        return f"MAPA • {game_name.upper()}", f"Novos detalhes do mapa colossal de {game_name} foram revelados — e o tamanho desse mundo aberto vai te impressionar!"
    if any(w in fact_blob for w in ("gráficos", "física", "60 fps", "ray tracing", "rage", "água")):
        return f"GRÁFICOS • {game_name.upper()}", f"A tecnologia gráfica e física de {game_name} está deixando desenvolvedores do mundo inteiro de boca aberta!"

    if is_rumor:
        badge = f"RUMOR • {game_name.upper()}"
        hook = f"Um rumor que ganhou força hoje sobre {clean_topic} tá mexendo com a comunidade — se liga nos detalhes!"
        return badge, hook

    if archetype == "contraste_detalhe":
        badge = f"ALERTA • {game_name.upper()}"
        hook = f"Uma novidade pesada sobre {clean_topic} acabou de sair — só que tem um detalhe que quase ninguém percebeu!"
        return badge, hook

    if archetype == "raio_x_numeros":
        num_hit = re.search(r"\b(R\$\s?\d+[\.,]?\d*|US\$\s?\d+[\.,]?\d*|\d+[\.,]?\d*\s?(?:GB|TB|FPS|horas|bilhões|milhões))\b", fact_blob, re.I)
        num_tag = num_hit.group(1) if num_hit else ""
        badge = f"RAIO-X • {num_tag.upper() or game_name.upper()}"
        hook = f"Olha os números divulgados dessa novidade em {game_name}: a conta final vai surpreender todo mundo!"
        return badge, hook

    badge = f"REVELAÇÃO • {game_name.upper()}"
    hook = f"Uma novidade sobre {clean_topic} acabou de sair hoje — e quem tá esperando o lançamento precisa ver isso!"
    return badge, hook


def _build_varied_cta(archetype: str, clean_topic: str, selected_facts_text: str, game_name: str) -> str:
    """Build archetype-specific, varied CTAs avoiding repetitive formulas (Tarefa 2.1)."""
    canon = archetype
    if canon in ("contraste_detalhe", "gameplay_imersao", "lista_open_loop"):
        canon = "curiosidade"
    elif canon == "raio_x_numeros":
        canon = "noticia"

    head_low = clean_topic.lower()
    body_low = f"{head_low} {selected_facts_text.lower()}"

    if canon == "teoria_rumor":
        return f"Na sua opinião sincera: essa teoria tem cara de verdade ou é pura especulação? Quero saber sua opinião nos comentários!"

    if canon == "curiosidade":
        return f"Você já tinha reparado nesse detalhe nos trailers ou foi novidade pra você? Manda para aquele amigo que é viciado em {game_name}!"

    if canon == "polemica_comparacao":
        if any(k in body_low for k in ("preço", "preco", "caro", "80 dólares", "custo", "bilhão", "dinheiro")):
            return f"Você pagaria mais caro se {game_name} realmente for uma obra-prima ou o preço tem que ficar travado nos 70 dólares? Comenta aí!"
        return f"E você, de que lado dessa treta fica: apoia a decisão ou acha que os jogadores têm razão em reclamar? Deixa seu comentário!"

    # Archetype 'noticia' or default
    if any(k in body_low for k in ("pc", "console", "plataforma")):
        return f"Você já vai garantir no lançamento para console ou vai esperar sair no PC? Deixa nos comentários e já segue para mais novidades!"
    if any(k in body_low for k in ("mapa", "leonida", "vice city")):
        return f"Qual o primeiro lugar que você quer explorar quando colocar as mãos no jogo? Comenta aí e já deixa o like!"

    return f"Qual a sua expectativa para ver essa novidade rodando na prática? Deixa seu comentário e já se inscreve para não perder nada de {game_name}!"


def _build_binary_cta(archetype: str, clean_topic: str, selected_facts_text: str, game_name: str) -> str:
    return _build_varied_cta(archetype, clean_topic, selected_facts_text, game_name)


def synthesize_gaming_short_script(
    topic_title: str,
    game_name: str = "GTA 6",
    topic_angle: str = "",
    source_url: str = "",
    fact_confidence: str = "Notícia / Imprensa",
    reference_notes: str = "",
    dossier: dict[str, Any] | None = None,
    accent_color: str = "#FF2E93",
    viral_archetype: str = "auto",
    speech_rate: str = "+14%",
) -> dict[str, Any]:
    """
    Smart Narrative Storyteller (Zero-API First).
    Constructs a 100% cohesive, continuous 6-scene viral YouTube Short script (~140-155 words, ~49-54s @ +14%)
    with logical narrative progression, full grammatical sentences, and zero awkward truncations.
    """
    dossier = dossier or {}
    clean_topic = _clean_short_topic(translate_to_pt_br(topic_title))
    clean_angle = translate_to_pt_br(topic_angle.strip()) if topic_angle and topic_angle.strip() else ""
    art_summary = translate_to_pt_br(str(dossier.get("article_summary") or "").strip())
    raw_facts: list[str] = [str(f).strip() for f in (dossier.get("article_facts") or []) if str(f).strip()]

    combined_text = f"{clean_topic} {clean_angle} {art_summary} {' '.join(raw_facts)}".lower()
    is_rumor = any(k in str(fact_confidence).lower() for k in ("rumor", "vaza", "especula", "não verificado", "nao_verificado"))
    status_conf = "rumor" if is_rumor else ("confirmado" if any(k in str(fact_confidence).lower() for k in ("oficial", "confirmad")) else "reportado")
    archetype = _pick_viral_archetype(clean_topic, combined_text, requested=viral_archetype, status_confiabilidade=status_conf)
    effects = ["zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in", "zoom_out"]

    canon_archetype = archetype
    if canon_archetype in ("contraste_detalhe", "gameplay_imersao", "lista_open_loop"):
        canon_archetype = "curiosidade"
    elif canon_archetype == "raio_x_numeros":
        canon_archetype = "noticia"
    elif canon_archetype not in ("noticia", "curiosidade", "teoria_rumor", "polemica_comparacao"):
        canon_archetype = "noticia"

    cleaned_facts = [_clean_narrative_sentence(f) for f in raw_facts if len(f.split()) >= 6]
    if not cleaned_facts and art_summary:
        cleaned_facts = [_clean_narrative_sentence(s) for s in re.split(r"(?<=[.!?])\s+", art_summary) if len(s.split()) >= 6]

    while len(cleaned_facts) < 4:
        defaults = [
            f"Essa novidade em torno de {game_name} chamou a atenção de toda a comunidade gamer.",
            "Os bastidores mostram a grandiosidade e o nível de detalhe que a Rockstar está preparando.",
            "A experiência promete elevar os padrões técnicos para toda a atual geração de consoles.",
            "O impacto prático será sentido logo nos primeiros minutos explorando o mapa de Vice City.",
        ]
        cleaned_facts.append(defaults[len(cleaned_facts)])

    scenes_spec: list[dict[str, str]] = []

    if canon_archetype == "noticia":
        # 5 Cenas: Gancho Noticioso → Fato Central → Contexto & Escala → Impacto Real → Expectativa
        hook_badge, hook_text = _build_viral_hook("noticia", clean_topic, game_name, cleaned_facts, is_rumor=False)
        cta_text = _build_varied_cta("noticia", clean_topic, " ".join(cleaned_facts), game_name)
        scenes_spec = [
            {"title": hook_badge, "text": hook_text},
            {"title": "📢 FATO CENTRAL", "text": _humanize_fact(cleaned_facts[0], connector="O que foi divulgado oficialmente revela que")},
            {"title": "🔍 CONTEXTO & ESCALA", "text": _humanize_fact(cleaned_facts[1], connector="Pra você entender a importância disso no projeto:")},
            {"title": "⚡ NA PRÁTICA", "text": _humanize_fact(cleaned_facts[2], connector="Isso impacta diretamente a jogabilidade porque")},
            {"title": "💬 QUAL SUA EXPECTATIVA?", "text": cta_text},
        ]
    elif canon_archetype == "curiosidade":
        # 5 Cenas: Gancho Oculto → A Evidência → Detalhe Técnico → A Revelação → Fechamento
        hook_badge, hook_text = _build_viral_hook("curiosidade", clean_topic, game_name, cleaned_facts, is_rumor=False)
        cta_text = _build_varied_cta("curiosidade", clean_topic, " ".join(cleaned_facts), game_name)
        scenes_spec = [
            {"title": hook_badge, "text": hook_text},
            {"title": "🔎 A EVIDÊNCIA", "text": _humanize_fact(cleaned_facts[0], connector="Ao analisar atentamente os materiais divulgados, nota-se que")},
            {"title": "⚙️ DETALHE TÉCNICO", "text": _humanize_fact(cleaned_facts[1], connector="O funcionamento minucioso por trás disso mostra que")},
            {"title": "💡 A REVELAÇÃO", "text": _humanize_fact(cleaned_facts[2], connector="Isso comprova o nível inacreditável de realismo que a Rockstar está preparando.")},
            {"title": "🚀 JÁ SABIA DISSO?", "text": cta_text},
        ]
    elif canon_archetype == "teoria_rumor":
        # 5 Cenas: Pergunta/Gancho → Os Indícios → A Hipótese → O Contraponto → Debate
        hook_badge, hook_text = _build_viral_hook("teoria_rumor", clean_topic, game_name, cleaned_facts, is_rumor=True)
        cta_text = _build_varied_cta("teoria_rumor", clean_topic, " ".join(cleaned_facts), game_name)
        scenes_spec = [
            {"title": hook_badge, "text": hook_text},
            {"title": "📜 OS INDÍCIOS", "text": _humanize_fact(cleaned_facts[0], connector="Segundo rumores recentes e vazamentos debatidos pela comunidade,")},
            {"title": "🧩 A HIPÓTESE", "text": _humanize_fact(cleaned_facts[1], connector="A teoria que ganhou mais força sugere que")},
            {"title": "⚖️ O CONTRAPONTO", "text": _humanize_fact(cleaned_facts[2], connector="Só que vale ponderar com cautela: até agora a Rockstar não confirmou nada oficialmente.")},
            {"title": "🗣️ QUAL SUA TEORIA?", "text": cta_text},
        ]
    elif canon_archetype == "polemica_comparacao":
        # 4 Cenas: O Dilema → Argumentos A → As Críticas B → O Veredito
        hook_badge, hook_text = _build_viral_hook("polemica_comparacao", clean_topic, game_name, cleaned_facts, is_rumor=is_rumor)
        cta_text = _build_varied_cta("polemica_comparacao", clean_topic, " ".join(cleaned_facts), game_name)
        scenes_spec = [
            {"title": hook_badge, "text": hook_text},
            {"title": "⚔️ LADO A: A DEFESA", "text": _humanize_fact(cleaned_facts[0], connector="Por um lado, analistas e defensores afirmam que")},
            {"title": "🛡️ LADO B: AS CRÍTICAS", "text": _humanize_fact(cleaned_facts[1], connector="Por outro lado, milhares de jogadores criticam e apontam que")},
            {"title": "🎯 DE QUE LADO VOCÊ FICA?", "text": cta_text},
        ]

    if is_rumor:
        for sc in scenes_spec:
            sc["text"] = _sanitize_for_rumor(sc["text"])

    raw_scenes: list[dict[str, Any]] = []
    for idx, sc in enumerate(scenes_spec, start=1):
        narr = sc["text"]
        img_q, vid_q = _topic_visual_queries(narr, clean_topic, idx - 1, game_name=game_name)
        raw_scenes.append(
            {
                "index": idx,
                "title": sc["title"],
                "shot_intent": f"beat_{idx}_{archetype}",
                "narration_text": narr,
                "visual_prompt": img_q,
                "search_query": img_q,
                "video_query": vid_q,
                "effect": effects[(idx - 1) % len(effects)],
                "highlight_words": _extract_highlight_keywords(narr, game_name),
                "quote_text": clean_topic[:84] if idx == 1 else "",
                "music_break_after_sec": 0.0,
            }
        )

    # Word budget safety: enforce max words keeping complete sentences
    raw_scenes = enforce_script_duration_budget(raw_scenes, max_words=160)
    scenes = [SceneItem(**s) for s in raw_scenes]
    narration_full = " ".join(s.narration_text for s in scenes)

    base_title = clean_topic[:76].rstrip(" .:-")
    youtube_title = f"{base_title} #shorts"
    if len(youtube_title) > 95:
        youtube_title = clean_topic[:84].rstrip(" .:-") + " #shorts"

    source_credit = f"\nFonte: {source_url}" if source_url else ""
    attribution_block = (
        "\n\nCréditos & Fontes de Mídia:\n"
        "- Imagens e recortes de vídeo utilizados para fins de comentário, análise crítica e cobertura de notícias (Fair Use promocional).\n"
        "- Direitos autorais pertencem aos seus respectivos detentores."
    )
    youtube_description = (
        f"{clean_topic}\n\n"
        f"Cobertura direta sobre {game_name} ({fact_confidence})."
        f"{source_credit}"
        f"{attribution_block}\n\n"
        "#shorts #gta6 #grandtheftautovi #rockstargames #vicecity #gamesbrasil"
    )
    youtube_tags = [
        "GTA 6",
        "Grand Theft Auto VI",
        "Rockstar Games",
        "GTA 6 Brasil",
        "Vice City",
        "Leonida",
        "YouTube Shorts",
        "Notícias Games",
    ]
    tiktok_caption = (
        f"{clean_topic} 👇 O que você acha disso?\n"
        "#gta6 #gta6brasil #rockstargames #vicecity #games #gamingnews"
    )

    return {
        "resolved_topic": clean_topic,
        "resolved_angle": clean_angle,
        "resolved_year": "2026",
        "resolved_genre": "Games / Notícias GTA 6",
        "viral_archetype": archetype,
        "fact_confidence": fact_confidence,
        "status_confiabilidade": "rumor" if is_rumor else ("confirmado" if "oficial" in fact_confidence.lower() or "confirmad" in fact_confidence.lower() else "reportado"),
        "hook_title": str(scenes[0].quote_text or clean_topic[:80]).upper(),
        "reel2_title": game_name,
        "reel2_subtitle": f"{fact_confidence} • YouTube Shorts",
        "youtube_title": youtube_title,
        "youtube_description": youtube_description,
        "youtube_tags": youtube_tags,
        "tiktok_caption": tiktok_caption,
        "instagram_caption": tiktok_caption,
        "hashtags": ["#shorts", "#gta6", "#gta6brasil", "#rockstargames", "#vicecity", "#games"],
        "narration_full": narration_full,
        "scenes": [s.model_dump() for s in scenes],
        "research_summary": dossier.get("summary_text") or clean_topic,
        "news_media_manifest": {
            "resolved_url": dossier.get("resolved_url") or source_url,
            "article_images_meta": dossier.get("article_images_meta") or [],
            "article_videos": dossier.get("article_videos") or [],
        },
        "accent_color": accent_color or "#FF2E93",
        "estimated_duration_sec": estimate_script_seconds(scenes, speech_rate=speech_rate),
        "engine_used": f"smart_storyteller_{archetype}",
    }


def enforce_script_duration_budget(raw_scenes: list[dict[str, Any]], max_words: int = 140) -> list[dict[str, Any]]:
    """Enforce strict YouTube Shorts duration ceiling without chopping sentences mid-clause (Tarefa 2.2)."""
    total_words = sum(len(s.get("narration_text", "").split()) for s in raw_scenes)
    if total_words <= max_words:
        return raw_scenes

    excess = total_words - max_words
    n_scenes = len(raw_scenes)
    # Middle scenes are from 1 to n_scenes - 2 (keeping Hook 0 and CTA n_scenes-1 intact if possible)
    middle_indices = [i for i in range(1, n_scenes - 1)] if n_scenes > 2 else [0]

    min_floor = max(8, int(max_words / max(1, n_scenes)))
    for sc in raw_scenes:
        if sc.get("index") in middle_indices and excess > 0:
            words = sc.get("narration_text", "").split()
            if len(words) > min_floor:
                cut = min(excess, len(words) - min_floor)
                cleaned = _clean_narrative_sentence(sc["narration_text"], max_words=len(words) - cut)
                new_len = len(cleaned.split())
                actual_cut = max(0, len(words) - new_len)
                sc["narration_text"] = cleaned
                excess -= actual_cut

    if excess > 0:
        for sc in raw_scenes:
            if excess <= 0:
                break
            words = sc.get("narration_text", "").split()
            if len(words) > 8:
                cut = min(excess, len(words) - 8)
                cleaned = _clean_narrative_sentence(sc["narration_text"], max_words=len(words) - cut)
                new_len = len(cleaned.split())
                actual_cut = max(0, len(words) - new_len)
                sc["narration_text"] = cleaned
                excess -= actual_cut

    return raw_scenes


def estimate_script_seconds(scenes: list[SceneItem], speech_rate: str = "+14%") -> float:
    rate_factor = 1.14
    m = re.search(r"([+-]?\d+)", speech_rate or "")
    if m:
        rate_factor = 1.0 + (float(m.group(1)) / 100.0)
    wps = (145.0 / 60.0) * max(0.8, rate_factor)
    total_words = sum(len(s.narration_text.split()) for s in scenes)
    return round(total_words / wps, 2)


def generate_gaming_script(
    topic_title: str,
    game_name: str = "GTA 6",
    topic_angle: str = "",
    source_url: str = "",
    fact_confidence: str = "Notícia / Imprensa",
    reference_notes: str = "",
    script_engine: str = "free_research",
    accent_color: str = "#FF2E93",
    api_key_override: str | None = None,
    viral_archetype: str = "auto",
    speech_rate: str = "+14%",
) -> dict[str, Any]:
    """Entry point for generating viral, factual <=58s PT-BR gaming Shorts scripts."""
    pt_topic = translate_to_pt_br(topic_title)
    pt_angle = translate_to_pt_br(topic_angle) if topic_angle and topic_angle.strip() else ""
    dossier: dict[str, Any] = {}
    try:
        dossier = fetch_gaming_topic_dossier(
            topic_title=pt_topic,
            game_name=game_name,
            source_url=source_url,
            topic_angle=pt_angle,
        )
    except Exception as exc:
        logger.warning(f"Erro ao buscar dossiê de pesquisa: {exc}")
        dossier = {}

    chosen_archetype = _pick_viral_archetype(
        pt_topic,
        f"{pt_angle} {dossier.get('article_summary', '')}",
        requested=viral_archetype,
    )

    api_key = get_gemini_api_key(api_key_override)
    if api_key and api_key.startswith("AIza"):
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            facts_bullets = "\n".join(f"- {f}" for f in (dossier.get("article_facts") or [])[:10])
            prompt = (
                f"Você é roteirista viral de YouTube Shorts de Games em PT-BR (estilo SanInPlay, Tchelox, MuriloCZ, Leley).\n"
                f"Arquétipo pedido: {chosen_archetype} ({VIRAL_ARCHETYPES.get(chosen_archetype, '')}).\n"
                f"Crie EXATAMENTE 6 cenas curtas e eletrizantes que formam UMA HISTÓRIA CONTÍNUA e conectada (TOTAL ENTRE 140 E 158 PALAVRAS somando todas as cenas — ritmo @ {speech_rate} para fechar em ~50-54s, NUNCA passando de 58s!).\n\n"
                f"Pauta: {pt_topic}\n"
                f"Jogo: {game_name}\n"
                f"Fonte/Nível: {fact_confidence}\n"
                f"Ângulo: {pt_angle or 'Fatos concretos e específicos sem enrolação'}\n"
                f"Fatos reais extraídos da matéria:\n{facts_bullets or dossier.get('summary_text', '')[:2500]}\n\n"
                "REGRAS DE OURO:\n"
                "1) NÃO FAÇA UMA COLCHA DE RETALHOS. Cada cena DEVE se conectar diretamente com a cena anterior criando uma linha narrativa fluida.\n"
                "2) Cena 1 (16-22 palavras): Gancho viral que prende a atenção nos primeiros 3 segundos.\n"
                "3) Cena 2 (22-28 palavras): Revelação do fato principal ou notícia confirmada com sujeito e verbo claros.\n"
                "4) Cena 3 (22-28 palavras): Comparação ou escala numérica ('Pra você ter noção do tamanho disso...').\n"
                "5) Cena 4 (22-28 palavras): Impacto direto na jogabilidade ou mecânica do jogo.\n"
                "6) Cena 5 (18-24 palavras): A virada ou dilema que a comunidade está debatendo.\n"
                "7) Cena 6 (14-20 palavras): Pergunta polarizadora A vs B para o público comentar imediatamente.\n"
                "8) Linguagem 100% falada brasileira: use 'tá', 'pra', 'se liga', 'olha isso'. Todas as frases devem ser gramaticalmente completas, sem cortes bruscos.\n"
                "9) Retorne APENAS JSON válido com: hook_title, youtube_title (<=95 chars com #shorts), youtube_description, tiktok_caption, hashtags, scenes (lista com index, title, narration_text, search_query, video_query, effect, highlight_words, quote_text)."
            )
            resp = client.models.generate_content(
                model=get_text_model(),
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.65,
                ),
            )
            raw_txt = (resp.text or "").strip()
            m = re.search(r"\{.*\}", raw_txt, flags=re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                raw_sc = parsed.get("scenes") or []
                if len(raw_sc) >= 4:
                    norm_scenes = []
                    for i, s in enumerate(raw_sc[:6], start=1):
                        narr = str(s.get("narration_text") or "").strip()
                        def_img, def_vid = _topic_visual_queries(narr, pt_topic, i - 1, game_name=game_name)
                        norm_scenes.append(
                            {
                                "index": i,
                                "title": str(s.get("title") or f"Cena {i}"),
                                "narration_text": narr,
                                "visual_prompt": str(s.get("visual_prompt") or s.get("search_query") or def_img),
                                "search_query": str(s.get("search_query") or def_img),
                                "video_query": str(s.get("video_query") or def_vid),
                                "effect": s.get("effect") if s.get("effect") in {"zoom_in", "zoom_out", "pan_left", "pan_right", "ken_burns_duotone", "vintage_grain"} else "zoom_in",
                                "highlight_words": list(s.get("highlight_words") or _extract_highlight_keywords(narr, game_name))[:5],
                                "quote_text": str(s.get("quote_text") or ""),
                                "music_break_after_sec": 0.0,
                            }
                        )
                    norm_scenes = enforce_script_duration_budget(norm_scenes, max_words=160)
                    scenes = [SceneItem(**s) for s in norm_scenes]
                    yt_title = str(parsed.get("youtube_title") or f"{pt_topic[:80]} #shorts")[:95]
                    if "#shorts" not in yt_title.lower():
                        yt_title = f"{yt_title[:85]} #shorts"
                    return {
                        "resolved_topic": pt_topic,
                        "resolved_angle": pt_angle,
                        "resolved_year": "2026",
                        "resolved_genre": "Games / Notícias GTA 6",
                        "viral_archetype": chosen_archetype,
                        "hook_title": str(parsed.get("hook_title") or pt_topic[:80]).upper(),
                        "reel2_title": game_name,
                        "reel2_subtitle": f"{fact_confidence} • YouTube Shorts",
                        "youtube_title": yt_title,
                        "youtube_description": str(parsed.get("youtube_description") or yt_title),
                        "youtube_tags": list(parsed.get("youtube_tags") or ["GTA 6", "Rockstar Games", "YouTube Shorts"]),
                        "tiktok_caption": str(parsed.get("tiktok_caption") or yt_title),
                        "instagram_caption": str(parsed.get("tiktok_caption") or yt_title),
                        "hashtags": list(parsed.get("hashtags") or ["#shorts", "#gta6", "#rockstargames"]),
                        "narration_full": " ".join(s.narration_text for s in scenes),
                        "scenes": [s.model_dump() for s in scenes],
                        "research_summary": dossier.get("summary_text") or pt_topic,
                        "news_media_manifest": {
                            "resolved_url": dossier.get("resolved_url") or source_url,
                            "article_images_meta": dossier.get("article_images_meta") or [],
                            "article_videos": dossier.get("article_videos") or [],
                        },
                        "accent_color": accent_color or "#FF2E93",
                        "estimated_duration_sec": estimate_script_seconds(scenes, speech_rate=speech_rate),
                        "engine_used": f"gemini_viral_{chosen_archetype}",
                    }
        except Exception as exc:
            logger.warning(f"Gemini API falhou ou indisponível ({exc}) — alternando para Smart Storyteller local.")

    return synthesize_gaming_short_script(
        topic_title=pt_topic,
        game_name=game_name,
        topic_angle=pt_angle,
        source_url=source_url,
        fact_confidence=fact_confidence,
        reference_notes=reference_notes,
        dossier=dossier,
        accent_color=accent_color,
        viral_archetype=chosen_archetype,
        speech_rate=speech_rate,
    )
