from __future__ import annotations

import hashlib
import html
import logging
import re
import urllib.parse
try:
    import defusedxml.ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("gta6_radar")

from app.config import PROJECTS_DIR
from app.models import TopicTrendItem


RADAR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) CriadorDeVideosRadar/1.0"
}

_TRANSLATION_CACHE: dict[str, str] = {}

_PT_HINTS_RE = re.compile(
    r"[ãõçáéíóúâêô]|"
    r"\b(?:não|para|sobre|como|jogo|novo|nova|lançamento|confirmado|confirmada|"
    r"pelo|pela|entre|chega|mostra|vai|está|são|tem|com|tudo|hoje|trailer de|"
    r"versão|data|revela|saiba|confira|mais|ainda|pode|melhor|piores)\b",
    flags=re.IGNORECASE,
)
_EN_HINTS_RE = re.compile(
    r"\b(?:the|and|for|with|from|into|about|how|why|what|when|where|now|new|"
    r"pre-load|trailer|world|years|built|comes|coming|announced|revealed|release|"
    r"update|leaked|details|rumor|map|gameplay|first|best|this|that|will|are|is|"
    r"has|have|was|were|be|its|their|players|report|says|upcoming)\b",
    flags=re.IGNORECASE,
)


def looks_english_text(text: str) -> bool:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) < 6:
        return False
    if re.search(r"[ãõçáéíóúâêô]", clean):
        return False
    pt_hits = len(_PT_HINTS_RE.findall(clean))
    en_hits = len(_EN_HINTS_RE.findall(clean))
    if pt_hits >= 2 and en_hits <= 1:
        return False
    return en_hits >= 1 or pt_hits == 0


def translate_to_pt_br(text: str, client: httpx.Client | None = None) -> str:
    """
    Translate English news headlines/summaries automatically into natural Brazilian Portuguese
    using Zero-API Google Translate endpoint, chunking long articles and caching results cleanly.
    """
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean or not looks_english_text(clean):
        return clean
    cached = _TRANSLATION_CACHE.get(clean)
    if cached is not None:
        return cached

    def _translate_chunk(c: httpx.Client, chunk: str) -> str:
        resp = c.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "pt", "dt": "t", "q": chunk},
        )
        if resp.status_code != 200:
            return chunk
        data = resp.json()
        if not isinstance(data, list) or not data or not isinstance(data[0], list):
            return chunk
        translated = "".join(str(seg[0]) for seg in data[0] if isinstance(seg, list) and seg and seg[0])
        translated = re.sub(r"\s+", " ", translated).strip()
        return translated or chunk

    def _do_req(c: httpx.Client) -> str:
        text_lim = clean[:3400]
        if len(text_lim) <= 1400:
            return _translate_chunk(c, text_lim)
        # Split on sentence boundaries for clean multi-paragraph translation
        sents = re.split(r"(?<=[.!?])\s+", text_lim)
        chunks: list[str] = []
        buf = ""
        for s in sents:
            if len(buf) + len(s) + 1 <= 1350:
                buf = f"{buf} {s}".strip()
            else:
                if buf:
                    chunks.append(buf)
                buf = s[:1350]
        if buf:
            chunks.append(buf)
        return " ".join(_translate_chunk(c, ch) for ch in chunks).strip()

    out = clean
    try:
        if client is not None:
            out = _do_req(client)
        else:
            with httpx.Client(timeout=8.0, follow_redirects=True, headers=RADAR_HEADERS) as c:
                out = _do_req(c)
    except Exception:
        out = clean

    _TRANSLATION_CACHE[clean] = out
    return out


_EN_TRANSLATION_CACHE: dict[str, str] = {}


def translate_to_en(text: str, client: httpx.Client | None = None) -> str:
    """
    Translate Brazilian Portuguese narration/topic phrases into natural English
    for accurate Bing/YouTube search queries & image-generation prompts (Zero-API).
    """
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return ""
    # Skip if already clearly English without Portuguese diacritics/particles
    if not re.search(r"[ãõçáéíóúâêô]", clean) and not _PT_HINTS_RE.search(clean) and _EN_HINTS_RE.search(clean):
        return clean
    cached = _EN_TRANSLATION_CACHE.get(clean)
    if cached is not None:
        return cached

    def _do_req(c: httpx.Client) -> str:
        resp = c.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "pt", "tl": "en", "dt": "t", "q": clean[:900]},
        )
        if resp.status_code != 200:
            return clean
        data = resp.json()
        if not isinstance(data, list) or not data or not isinstance(data[0], list):
            return clean
        translated = "".join(str(seg[0]) for seg in data[0] if isinstance(seg, list) and seg and seg[0])
        translated = re.sub(r"\s+", " ", translated).strip()
        return translated or clean

    out = clean
    try:
        if client is not None:
            out = _do_req(client)
        else:
            with httpx.Client(timeout=6.0, follow_redirects=True, headers=RADAR_HEADERS) as c:
                out = _do_req(c)
    except Exception:
        out = clean

    _EN_TRANSLATION_CACHE[clean] = out
    return out

_OFFICIAL_DIRECT_SOURCES = (
    "rockstar games", "rockstar newswire", "take-two", "take two interactive",
)

_OFFICIAL_TITLE_KEYWORDS = (
    "trailer oficial", "comunicado oficial", "anúncio oficial", "anuncio oficial",
    "rockstar confirma", "take-two confirma", "confirmado pela rockstar",
    "confirmado oficialmente",
)

_UNCERTAINTY_KEYWORDS = (
    "rumor", "rumores", "vazamento", "vazamentos", "vazou", "vaza",
    "suposto", "suposta", "supostos", "supostas", "supostamente",
    "teria", "teriam", "pode indicar", "pode ter", "leak", "leaks", "leaked",
    "especulação", "especulacao", "não confirmado", "nao confirmado",
    "possível", "possivel", "diz insider", "insider", "teoria", "teorias",
)

_PRESS_SOURCES = (
    "ign", "gamespot", "bloomberg", "vgc", "eurogamer", "kotaku", "polygon",
    "the verge", "playstation blog", "xbox wire", "meuplaystation", "flow games",
    "voxel", "tecmundo", "canaltech", "jovem nerd", "uol", "digital foundry",
    "pc gamer", "game informer",
)

_OFFICIAL_SOURCES = _OFFICIAL_DIRECT_SOURCES + _PRESS_SOURCES

_GAMING_KEYWORDS = (
    "gta", "grand theft auto", "rockstar", "vice city", "leonida",
    "lucia", "jason", "take-two", "take two", "playstation", "xbox",
    "ps5", "nintendo", "steam", "gamer", "gameplay", "videogame",
    "red dead", "max payne",
)
_TRENDS_GAMING_RE = re.compile(
    r"\b(?:"
    r"gta(?:\s*6|\s*vi|\s*online)?|grand theft auto(?:\s*vi|\s*6)?|rockstar(?:\s*games)?|"
    r"vice city|leonida|take-two|playstation(?:\s*5)?|ps5|xbox|nintendo|steam|"
    r"gamer|videogame|videogames|gameplay|red dead|state of play"
    r")\b",
    flags=re.IGNORECASE,
)

_EVERGREEN_GTA6_FALLBACKS: list[dict[str, str]] = [
    {
        "title": "Tudo o que já está 100% confirmado sobre o mapa de Leonida e Vice City em GTA 6",
        "source": "Rockstar / GTA Wiki",
        "confidence": "Oficial / Confirmado",
        "summary": "O Trailer 1 oficial revelou o estado de Leonida, inspirado na Flórida, trazendo Vice City repaginada, Grassrivers, Port Gellhorn e Kelly County.",
        "angle": "Comparar regiões confirmadas de Leonida com os lugares reais da Flórida.",
    },
    {
        "title": "Quem são Lucia e Jason: o que os trailers e anúncios confirmam sobre os protagonistas de GTA 6",
        "source": "Rockstar Newswire",
        "confidence": "Oficial / Confirmado",
        "summary": "Lucia aparece com tornozeleira eletrônica no Trailer 1 e atua em dupla ao estilo Bonnie & Clyde na cidade moderna de Vice City.",
        "angle": "Fatos confirmados sobre Lucia, a tornozeleira eletrônica e a dinâmica da dupla.",
    },
    {
        "title": "Por que os NPCs e redes sociais de GTA 6 estão chamando tanta atenção nas análises",
        "source": "Análise Documentada",
        "confidence": "Análise / Trailer",
        "summary": "O Trailer 1 destaca dezenas de referências a vídeos virais reais da Flórida em formato de rede social vertical dentro do universo do jogo.",
        "angle": "Mostrar as paródias de notícias reais da Flórida escondidas nos frames do trailer.",
    },
    {
        "title": "Os animais selvagens, veículos e física de água mostrados nos materiais oficiais de GTA 6",
        "source": "Documentação Técnica",
        "confidence": "Análise / Trailer",
        "summary": "Jacarés em áreas urbanas, flamingos em Grassrivers, aerobarcos e centenas de NPCs simultâneos na praia chamaram atenção na engine RAGE.",
        "angle": "Detalhes visuais já visíveis no material oficial da engine RAGE.",
    },
]


def _clean_html(raw: str) -> str:
    txt = html.unescape(raw or "")
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[\u200b-\u200f\ufeff]", "", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _item_id(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _existing_covered_blobs() -> list[str]:
    blobs: list[str] = []
    if not PROJECTS_DIR.exists():
        return blobs
    for pf in PROJECTS_DIR.glob("*/project.json"):
        try:
            raw = pf.read_text(encoding="utf-8").lower()
            blobs.append(raw[:1600])
        except Exception:
            continue
    return blobs


_DEDUP_STOP_WORDS = {
    "gta", "gta6", "gtavi", "grand", "theft", "auto", "rockstar", "games", "take", "two",
    "taketwo", "sobre", "entre", "agora", "todos", "tudo", "para", "com", "uma", "mais",
    "como", "pode", "novo", "nova", "novos", "novas", "ser", "que", "dos", "das", "pelo",
    "pela", "pelos", "pelas", "segundo", "apos", "após", "diz", "revela", "confirma",
    "está", "estao", "estão", "jogo", "game", "games", "esse", "essa", "esses", "essas"
}


def extract_significant_tokens(text: str) -> set[str]:
    raw_words = re.findall(r"\b[a-zA-Z0-9À-ÿ]{3,}\b", (text or "").lower())
    return {w for w in raw_words if w not in _DEDUP_STOP_WORDS}


def calculate_token_similarity(tokens1: set[str], tokens2: set[str]) -> float:
    if not tokens1 or not tokens2:
        return 0.0
    intersection = tokens1.intersection(tokens2)
    union = tokens1.union(tokens2)
    return len(intersection) / len(union)


def _is_already_covered(title: str, covered_blobs: list[str]) -> bool:
    tokens = extract_significant_tokens(title)
    if len(tokens) < 2:
        return False
    for blob in covered_blobs:
        hits = sum(1 for tok in tokens if tok in blob)
        if hits >= 2 and hits / len(tokens) >= 0.5:
            return True
    return False


def classify_confidence_and_status(title: str, source: str) -> tuple[str, str]:
    """
    Classify a news topic into 4 official classes (M-02 / Tarefa 1.3):
    1. 'confirmado' / 'Oficial / Confirmado': Rockstar Newswire, Take-Two direct or official releases without doubt.
    2. 'reportado' / 'Notícia / Imprensa': Reputable press (IGN, Bloomberg, etc.) reporting verified information.
    3. 'rumor' / 'Rumor / Não verificado': Leaks, rumors, speculation, reddit/twitter unverified leaks.
    4. 'nao_verificado' / 'Não verificado': Dubious sources, unverified clickbait, or opinion with no source.
    """
    title_low = (title or "").lower()
    source_low = (source or "").lower()

    # 1. Qualquer indício de dúvida, rumor ou vazamento rebaixa imediatamente para Rumor
    if any(k in title_low for k in _UNCERTAINTY_KEYWORDS):
        return "Rumor / Não verificado", "rumor"

    # 2. Oficial / Confirmado estrito (canais diretos ou confirmação expressa sem dúvida)
    if any(src in source_low for src in _OFFICIAL_DIRECT_SOURCES) or any(k in title_low for k in _OFFICIAL_TITLE_KEYWORDS):
        return "Oficial / Confirmado", "confirmado"

    # 3. Imprensa especializada e cobertura jornalística
    if any(src in source_low for src in _PRESS_SOURCES):
        return "Notícia / Imprensa", "reportado"

    # 4. Análise / Tendência ou não verificado
    if not source_low or source_low in ("desconhecido", "desconhecida", "fórum", "forum", "internet", "web"):
        return "Não verificado", "nao_verificado"

    return "Análise / Tendência", "nao_verificado"


def _classify_confidence(title: str, source: str) -> str:
    return classify_confidence_and_status(title, source)[0]


def _suggest_angle(title: str) -> str:
    low = title.lower()
    if any(k in low for k in ("trailer", "frame", "detalhe", "escond", "easter")):
        return "Breakdown frame a frame do trailer com texto neon destaque."
    if any(k in low for k in ("mapa", "leonida", "vice city", "flórida", "florida", "miami")):
        return "Comparação visual Vice City x lugares reais da Flórida."
    if any(k in low for k in ("data", "lançamento", "release", "adiado", "delay", "janela")):
        return "Cronograma oficial confirmado + impacto nos próximos anúncios."
    if any(k in low for k in ("lucia", "jason", "personag", "ator", "atriz")):
        return "Perfil documentado dos protagonistas sem especulação exagerada."
    if any(k in low for k in ("pc", "ps5", "xbox", "console", "fps", "gráfico", "engine")):
        return "Fatos técnicos sobre plataformas confirmadas e performance."
    return "Resumo direto dos fatos confirmados em até 58s com pergunta no final."


def fetch_google_trends_rss(geo: str = "BR", focus_query: str = "GTA 6") -> list[TopicTrendItem]:
    """Read live Google Trends daily RSS feed cleanly (Zero-API)."""
    url = f"https://trends.google.com/trending/rss?geo={urllib.parse.quote(geo)}"
    items: list[TopicTrendItem] = []
    focus_words = [w.lower() for w in re.split(r"\s+", focus_query.strip()) if len(w) >= 2]
    try:
        with httpx.Client(timeout=9.0, follow_redirects=True, headers=RADAR_HEADERS) as client:
            resp = client.get(url)
            if resp.status_code != 200 or "<rss" not in resp.text[:400].lower():
                return []
            root = ET.fromstring(resp.text)
            ns = {"ht": "https://trends.google.com/trending/rss"}
            for idx, node in enumerate(root.findall(".//item")[:25]):
                title = _clean_html(node.findtext("title") or "")
                pub = _clean_html(node.findtext("pubDate") or "")
                traffic_el = node.find("ht:approx_traffic", ns)
                traffic = _clean_html(traffic_el.text if traffic_el is not None and traffic_el.text else "")
                news_title = ""
                news_url = ""
                first_news = node.find("ht:news_item", ns)
                if first_news is not None:
                    nt = first_news.find("ht:news_item_title", ns)
                    nu = first_news.find("ht:news_item_url", ns)
                    news_title = _clean_html(nt.text if nt is not None and nt.text else "")
                    news_url = (nu.text or "").strip() if nu is not None else ""
                combined = f"{title} {news_title}"
                focus_re = (
                    re.compile(r"\b(?:" + "|".join(re.escape(w) for w in focus_words) + r")\b", re.IGNORECASE)
                    if focus_words
                    else None
                )
                is_game_match = bool(_TRENDS_GAMING_RE.search(combined)) or bool(focus_re and focus_re.search(combined))
                if not is_game_match:
                    continue
                score = max(65, 98 - idx * 2)
                summary = f"Em alta no Google Trends {geo}" + (f" ({traffic} buscas)" if traffic else "")
                if news_title:
                    summary += f": {news_title}"
                items.append(
                    TopicTrendItem(
                        id=_item_id(f"trend-{geo}-{title}"),
                        title=title if len(title) > 22 or not news_title else f"{title} — {news_title}",
                        source=f"Google Trends ({geo})" + (f" • {traffic}" if traffic else ""),
                        feed_type="trends",
                        published_at=pub,
                        summary=summary,
                        url=news_url or f"https://trends.google.com/trends/explore?geo={geo}&q={urllib.parse.quote(title)}",
                        trend_score=score,
                        confidence="Tendência Google",
                        suggested_angle=_suggest_angle(f"{title} {news_title}"),
                    )
                )
    except Exception:
        return []
    return items


def fetch_google_news_rss(query: str = "GTA 6", lang: str = "pt-BR", max_items: int = 12) -> list[TopicTrendItem]:
    """Fetch structured real news topics from Google News RSS without any paid API."""
    if lang.lower().startswith("pt"):
        params = {"q": f"{query} OR \"Grand Theft Auto VI\"", "hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"}
    else:
        params = {"q": f"{query} Rockstar", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
    items: list[TopicTrendItem] = []
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, headers=RADAR_HEADERS) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            root = ET.fromstring(resp.text)
            for idx, node in enumerate(root.findall(".//item")[:max_items]):
                raw_title = _clean_html(node.findtext("title") or "")
                link = (node.findtext("link") or "").strip()
                pub = _clean_html(node.findtext("pubDate") or "")
                src_el = node.find("source")
                source = _clean_html(src_el.text if src_el is not None and src_el.text else "Google News")
                # Clean trailing " - Fonte" often appended by Google News titles
                clean_title = re.sub(r"\s+[-|]\s+" + re.escape(source) + r"$", "", raw_title).strip() or raw_title
                desc = _clean_html(node.findtext("description") or "")
                if not clean_title:
                    continue
                conf = _classify_confidence(clean_title, source)
                bonus = 8 if conf == "Oficial / Confirmado" else (4 if conf == "Notícia / Imprensa" else 0)
                score = max(45, min(99, 90 - idx * 3 + bonus))
                items.append(
                    TopicTrendItem(
                        id=_item_id(f"gnews-{clean_title}-{link[:40]}"),
                        title=clean_title,
                        source=f"{source} ({'PT' if lang.lower().startswith('pt') else 'EN'})",
                        feed_type="news",
                        published_at=pub,
                        summary=desc[:360] or clean_title,
                        url=link,
                        trend_score=score,
                        confidence=conf,
                        suggested_angle=_suggest_angle(clean_title),
                    )
                )
    except Exception:
        return []
    return items


def fetch_bing_news_rss(query: str = "GTA 6", mkt: str = "pt-BR", max_items: int = 12) -> list[TopicTrendItem]:
    """Fetch structured news items with direct publisher URLs from Bing News RSS (Zero-API)."""
    items: list[TopicTrendItem] = []
    lang_tag = "PT" if mkt.lower().startswith("pt") else "EN"
    try:
        with httpx.Client(timeout=9.0, follow_redirects=True, headers=RADAR_HEADERS) as client:
            resp = client.get(
                "https://www.bing.com/news/search",
                params={"q": query, "format": "rss", "mkt": mkt},
            )
            if resp.status_code != 200 or "<item>" not in resp.text:
                return []
            root = ET.fromstring(resp.text)
            for idx, node in enumerate(root.findall(".//item")[:max_items]):
                raw_title = _clean_html(node.findtext("title") or "")
                raw_link = (node.findtext("link") or "").strip()
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_link).query)
                real_url = qs.get("url", [raw_link])[0]
                pub = _clean_html(node.findtext("pubDate") or "")
                desc = _clean_html(node.findtext("description") or "")
                src = ""
                for ch in node:
                    if ch.tag.lower().endswith("source") and ch.text:
                        src = _clean_html(ch.text)
                        break
                if not src:
                    src = urllib.parse.urlparse(real_url).netloc.replace("www.", "") or "Bing News"
                if not raw_title or not real_url.startswith("http"):
                    continue
                conf = _classify_confidence(raw_title, src)
                bonus = 9 if conf == "Oficial / Confirmado" else (5 if conf == "Notícia / Imprensa" else 1)
                score = max(48, min(99, 93 - idx * 2 + bonus))
                items.append(
                    TopicTrendItem(
                        id=_item_id(f"bing-{real_url[:60]}-{raw_title}"),
                        title=raw_title,
                        source=f"{src} ({lang_tag})",
                        feed_type="news",
                        published_at=pub,
                        summary=desc[:420] or raw_title,
                        url=real_url,
                        trend_score=score,
                        confidence=conf,
                        suggested_angle=_suggest_angle(raw_title),
                    )
                )
    except Exception:
        return []
    return items


def fetch_publisher_gaming_rss(query: str = "GTA 6") -> list[TopicTrendItem]:
    """Fetch recent gaming news directly from IGN Brasil, Eurogamer PT, and VGC RSS feeds."""
    feeds = [
        ("IGN Brasil (PT)", "https://br.ign.com/feed.xml"),
        ("Eurogamer.pt (PT)", "https://www.eurogamer.pt/feed/news"),
        ("VGC (EN)", "https://www.videogameschronicle.com/feed/"),
    ]
    focus_terms = [w.lower() for w in re.split(r"\s+", query.strip()) if len(w) >= 2]
    items: list[TopicTrendItem] = []
    try:
        with httpx.Client(timeout=7.0, follow_redirects=True, headers=RADAR_HEADERS) as client:
            for src_label, feed_url in feeds:
                try:
                    resp = client.get(feed_url)
                    if resp.status_code != 200 or "<item" not in resp.text[:4000].lower():
                        continue
                    root = ET.fromstring(resp.text)
                    for idx, node in enumerate(root.findall(".//item")[:20]):
                        title = _clean_html(node.findtext("title") or "")
                        link = (node.findtext("link") or "").strip()
                        desc = _clean_html(node.findtext("description") or "")
                        pub = _clean_html(node.findtext("pubDate") or "")
                        blob = f"{title} {desc}".lower()
                        if not (
                            _TRENDS_GAMING_RE.search(blob)
                            or any(t in blob for t in focus_terms)
                        ):
                            continue
                        # Boost items explicitly matching the active query (e.g. GTA 6)
                        matches_focus = all(t in blob for t in focus_terms) if focus_terms else ("gta" in blob)
                        if "gta" in query.lower() and "gta" not in blob and "grand theft auto" not in blob and "rockstar" not in blob:
                            continue
                        conf = _classify_confidence(title, src_label)
                        score = max(55, min(98, (94 if matches_focus else 84) - idx))
                        items.append(
                            TopicTrendItem(
                                id=_item_id(f"pub-{link[:60]}"),
                                title=title,
                                source=src_label,
                                feed_type="news",
                                published_at=pub,
                                summary=desc[:420] or title,
                                url=link,
                                trend_score=score,
                                confidence=conf,
                                suggested_angle=_suggest_angle(title),
                            )
                        )
                except Exception:
                    continue
    except Exception:
        return []
    return items


def fetch_daily_gaming_radar(query: str = "GTA 6", geo: str = "BR") -> list[TopicTrendItem]:
    """
    Aggregate daily surging & documented gaming topics (Google Trends BR/US, Bing News PT/EN,
    direct publisher RSS feeds, and Google News PT/EN), prioritizing direct publisher URLs,
    translating English headlines/summaries cleanly into PT-BR.
    """
    covered = _existing_covered_blobs()
    combined: list[TopicTrendItem] = []
    combined.extend(fetch_google_trends_rss(geo=geo, focus_query=query))
    if geo.upper() != "US":
        combined.extend(fetch_google_trends_rss(geo="US", focus_query=query))
    # Direct URL sources first so clicking any card resolves real article paragraphs immediately
    combined.extend(fetch_bing_news_rss(query=query, mkt="pt-BR", max_items=10))
    combined.extend(fetch_bing_news_rss(query=f"{query} Rockstar", mkt="en-US", max_items=8))
    combined.extend(fetch_publisher_gaming_rss(query=query))
    combined.extend(fetch_google_news_rss(query=query, lang="pt-BR", max_items=8))
    combined.extend(fetch_google_news_rss(query=query, lang="en-US", max_items=6))

    # Add curated documented fallback angles if online RSS yields too few items
    if len(combined) < 4:
        for idx, fb in enumerate(_EVERGREEN_GTA6_FALLBACKS):
            combined.append(
                TopicTrendItem(
                    id=_item_id(f"evergreen-{fb['title']}"),
                    title=fb["title"],
                    source=fb["source"],
                    feed_type="news",
                    published_at="Curadoria Documentada",
                    summary=fb["summary"],
                    url="https://en.wikipedia.org/wiki/Grand_Theft_Auto_VI",
                    trend_score=78 - idx * 3,
                    confidence=fb["confidence"],
                    suggested_angle=fb["angle"],
                )
            )

    # When deduplicating similar titles, prefer items with direct non-Google-News URLs and cluster rewrites (M-02)
    dedup_list: list[TopicTrendItem] = []
    for it in combined:
        norm = re.sub(r"[^\w]+", "", it.title.lower())[:34]
        if not norm:
            continue

        # Populate primary source, publication date and 4-class reliability
        it.fonte_primaria = it.source or "Google News / Web"
        it.data_publicacao = it.published_at or ""
        conf_str, status_str = classify_confidence_and_status(it.title, it.source)
        it.confidence = conf_str
        it.status_confiabilidade = status_str
        it.already_covered = _is_already_covered(it.title, covered)

        it_tokens = extract_significant_tokens(it.title)

        # Check against existing items in dedup_list for rewrites / similar pautas
        match_idx = None
        for idx_e, existing in enumerate(dedup_list):
            ex_norm = re.sub(r"[^\w]+", "", existing.title.lower())[:34]
            if norm == ex_norm:
                match_idx = idx_e
                break
            ex_tokens = extract_significant_tokens(existing.title)
            sim = calculate_token_similarity(it_tokens, ex_tokens)
            if sim >= 0.40 and len(it_tokens.intersection(ex_tokens)) >= 2:
                match_idx = idx_e
                break

        if match_idx is None:
            dedup_list.append(it)
        else:
            existing = dedup_list[match_idx]
            # Record secondary source if distinct
            if it.source and it.source != existing.fonte_primaria and it.source not in existing.fontes_secundarias:
                existing.fontes_secundarias.append(it.source)
            cur_direct = "news.google.com" not in (it.url or "")
            old_direct = "news.google.com" not in (existing.url or "")
            if (cur_direct and not old_direct) or (cur_direct == old_direct and it.trend_score > existing.trend_score):
                if len(existing.summary or "") > len(it.summary or "") and "google news" not in (existing.source or "").lower():
                    it.summary = existing.summary
                merged_sec = list(dict.fromkeys(existing.fontes_secundarias + ([existing.fonte_primaria] if existing.fonte_primaria != it.source else [])))
                it.fontes_secundarias = [s for s in merged_sec if s and s != it.source]
                dedup_list[match_idx] = it

    dedup = dedup_list
    dedup.sort(key=lambda x: (not x.already_covered, x.trend_score), reverse=True)
    top_items = dedup[:18]

    # Translate any English headlines/summaries into Brazilian Portuguese cleanly
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True, headers=RADAR_HEADERS) as t_client:
            for it in top_items:
                if looks_english_text(it.title):
                    it.title = translate_to_pt_br(it.title, client=t_client)
                if it.summary and looks_english_text(it.summary):
                    it.summary = translate_to_pt_br(it.summary, client=t_client)
                it.already_covered = it.already_covered or _is_already_covered(it.title, covered)
    except Exception as exc:
        logger.warning("Falha na etapa de tradução/análise de pautas: %s", exc)

    return top_items


_JUNK_LINE_RE = re.compile(
    r"(cookie|privacidade|privacy|subscribe|newsletter|assine|publicidade|advertising|"
    r"copyright|todos os direitos|all rights reserved|sign in|fazer login|termos de uso|"
    r"compartilhe|leia mais:|veja também:|clique aqui|acessar o site|"
    r"pular para o conteúdo|skip to (?:main )?content|ver \d+ imagens|view \d+ images|"
    r"follow us on|siga-nos no|inscreva-se|manchetes mais recentes|pesquisa google|"
    r"fonte preferencial|preferred source|pic\.twitter\.com|google discover|"
    r"página principal|educação|conteúdo destacado|menu principal|navegação|cadastre-se|"
    r"todas as matérias|primeiro no seu discover|siga no google notícias|"
    r"compare com outros jogos|prefira o (?:ct|techtudo|ign|canaltech)|adicionar (?:ch|ct) no google|"
    r"background-color:|style=|reprodução/|\d+ vazamentos de gta|veja mais detalhes sobre o ator)",
    flags=re.IGNORECASE,
)

_EMOJI_PROMO_RE = re.compile(r"(?:^[🎮⭐🚨👉🔥📺📰📌]+\s+|\s+[⭐🎮🚨🔥]\s+)")


def _score_fact_sentence(s: str) -> float:
    low = s.lower()
    if s.strip().endswith("?"):
        return -5.0
    score = 0.0
    if re.search(r"\d", s):
        score += 3.2
    if re.search(r"\b(?:gb|tb|fps|4k|1080p|horas?|dias?|meses?|r\$|reais|dólares|milhões|bilhões|%)\b", s, re.I):
        score += 2.6
    if re.search(r"\b(?:rockstar|take-two|ps5|playstation|xbox|pc|cloud|preload|trailer|lucia|jason|k9|npcs?|dual\s*sense|ssd|edge|pix|física|colecionador)\b", s, re.I):
        score += 2.0
    if re.search(r"\b(?:mas|porém|contudo|enquanto|só que|dobro|metade|limite|confirmou|revelou|anunciou|vazou|exige|oferece)\b", s, re.I):
        score += 1.5
    # Penalize generic countdown / editorial meta-teasers so real facts bubble to the top
    if re.search(r"\b(?:contagem regressiva|duas dúvidas seguem|vale separar|confira abaixo|veja a seguir|neste guia|estamos a uma semana|estamos há uma semana|excluir jogos antigos não é a única|finalmente foram informados)\b", low):
        score -= 3.5
    n_words = len(s.split())
    if 12 <= n_words <= 34:
        score += 1.5
    elif n_words > 45:
        score -= 1.0
    return score


def _extract_ranked_facts(text: str, limit: int = 12, title_hint: str = "") -> list[str]:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean:
        return []
    title_tokens = {w for w in re.split(r"[\W_]+", (title_hint or "").lower()) if len(w) >= 4}
    raw_sents = re.split(r"(?<=[.!?])\s+", clean)
    scored: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    for idx, sent in enumerate(raw_sents):
        s = sent.strip()
        if len(s) < 34 or len(s) > 260 or s.endswith("?"):
            continue
        if _JUNK_LINE_RE.search(s) or _EMOJI_PROMO_RE.search(s):
            continue
        # Skip boilerplate sentences that merely repeat the article headline verbatim
        if title_tokens:
            s_tokens = {w for w in re.split(r"[\W_]+", s.lower()) if len(w) >= 4}
            if len(s_tokens) >= 4 and (len(s_tokens & title_tokens) / max(1, len(s_tokens))) >= 0.72:
                continue
        norm = re.sub(r"[^\w]+", "", s.lower())[:42]
        if not norm or norm in seen:
            continue
        seen.add(norm)
        scored.append((_score_fact_sentence(s), idx, s))
    scored.sort(key=lambda x: (round(x[0], 1), -x[1]), reverse=True)
    top = sorted(scored[:limit], key=lambda x: x[1])
    return [t[2] for t in top]


def _extract_article_page(client: httpx.Client, url: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "final_url": url,
        "text": "",
        "images": [],
        "images_meta": [],
        "videos": [],
    }
    try:
        resp = client.get(url)
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", "").lower():
            return out
        final_url = str(resp.url)
        out["final_url"] = final_url
        if "news.google.com" in final_url.lower():
            return out
        raw_html = resp.text[:320_000]

        # Remove script/style/nav/header/footer/aside blocks FIRST so nav menus & site logos never leak
        body_clean = re.sub(
            r"<(?:script|style|nav|footer|header|aside)[^>]*>.*?</(?:script|style|nav|footer|header|aside)>",
            " ",
            raw_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # Also remove obvious related-articles / sidebar cards blocks
        body_clean = re.sub(
            r"<(?:div|section)[^>]*(?:class|id)=[\"'][^\"']*(?:related|sidebar|newsletter|comments|recommended|widget|author-box)[^\"']*[\"'][^>]*>.*?</(?:div|section)>",
            " ",
            body_clean,
            flags=re.IGNORECASE | re.DOTALL,
        )
        art_m = re.search(r"<article[^>]*>(.*?)</article>", body_clean, flags=re.IGNORECASE | re.DOTALL)
        scope_html = art_m.group(1) if art_m else body_clean

        imgs: list[str] = []
        images_meta: list[dict[str, str]] = []
        _BAD_IMG_RE = re.compile(
            r"(?:favicon|masthead|gravatar|emoji|1x1|sprite|black[-_]?friday|bf20\d\d|playpng|lookaside\.fbsbx|"
            r"pixel|spacer|placeholder|default[-_]?thumb|"
            r"(?:^|[/?._-])(?:logo|logos|avatar|icon|icons|badge|ad|ads|advert|doubleclick|sponsor|widget|promo|button|share)(?:[/?._-]|$))",
            flags=re.IGNORECASE,
        )

        def _pick_img_url(tag_html: str) -> str:
            for attr in ("data-src", "data-lazy-src", "data-original", "src"):
                m = re.search(
                    rf'\b{attr}=["\']([^"\']+\.(?:jpg|jpeg|webp|png)(?:\?[^"\']*)?)["\']',
                    tag_html,
                    flags=re.IGNORECASE,
                )
                if m and not m.group(1).strip().lower().startswith("data:"):
                    return m.group(1).strip()
            return ""

        def _try_add_img(raw_u: str, alt_txt: str = "", context_txt: str = "") -> None:
            abs_u = urllib.parse.urljoin(final_url, html.unescape(raw_u.strip()))
            if not abs_u.startswith(("http://", "https://")) or abs_u in imgs:
                return
            if _BAD_IMG_RE.search(abs_u):
                return
            imgs.append(abs_u)
            images_meta.append(
                {
                    "url": abs_u,
                    "alt": _clean_html(alt_txt)[:180],
                    "context": _clean_html(context_txt)[:240],
                    "source_article": final_url,
                }
            )

        og_title_m = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            raw_html,
            flags=re.IGNORECASE,
        )
        page_title_hint = _clean_html(og_title_m.group(1)) if og_title_m else ""

        for pat in (
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        ):
            for m in re.findall(pat, raw_html, flags=re.IGNORECASE):
                _try_add_img(m, alt_txt=page_title_hint, context_txt=page_title_hint)
                if len(imgs) >= 3:
                    break

        # Capture <figure> blocks first to pair figcaption with image URL cleanly
        for fig_html in re.findall(r"<figure[^>]*>(.*?)</figure>", scope_html, flags=re.IGNORECASE | re.DOTALL):
            img_u = _pick_img_url(fig_html)
            if not img_u:
                continue
            alt_m = re.search(r'alt=["\']([^"\']*)["\']', fig_html, flags=re.IGNORECASE)
            cap_m = re.search(r"<figcaption[^>]*>(.*?)</figcaption>", fig_html, flags=re.IGNORECASE | re.DOTALL)
            _try_add_img(
                img_u,
                alt_txt=alt_m.group(1) if alt_m else "",
                context_txt=cap_m.group(1) if cap_m else page_title_hint,
            )

        for img_tag in re.findall(r"<img\b[^>]+>", scope_html, flags=re.IGNORECASE):
            img_u = _pick_img_url(img_tag)
            if not img_u:
                continue
            alt_m = re.search(r'alt=["\']([^"\']*)["\']', img_tag, flags=re.IGNORECASE)
            _try_add_img(img_u, alt_txt=alt_m.group(1) if alt_m else "", context_txt=page_title_hint)
            if len(imgs) >= 12:
                break

        # Extract videos embedded or linked inside the article (YouTube embeds/links, <video>/<source>, og:video)
        videos: list[dict[str, str]] = []
        seen_vkeys: set[str] = set()

        def _clean_video_ctx(raw_ctx: str) -> str:
            if not raw_ctx:
                return ""
            # Drop partial HTML tags sliced at boundaries so iframe attributes never leak
            s = re.sub(r"^[^<>\n]*>", " ", raw_ctx)
            s = re.sub(r"<[^>\n]*$", " ", s)
            txt = _clean_html(s)
            if not txt or re.search(
                r"\b(?:autoplay|clipboard-write|encrypted-media|allowfullscreen|frameborder|referrerpolicy|gyroscope|picture-in-picture)\b",
                txt,
                flags=re.IGNORECASE,
            ):
                return ""
            return txt[:220]

        def _add_yt(yt_id: str, ctx: str = "") -> None:
            if not yt_id or not re.match(r"^[A-Za-z0-9_-]{11}$", yt_id):
                return
            if yt_id in seen_vkeys:
                return
            seen_vkeys.add(yt_id)
            clean_ctx = _clean_video_ctx(ctx) or page_title_hint
            videos.append(
                {
                    "kind": "youtube",
                    "video_id": yt_id,
                    "url": f"https://www.youtube.com/watch?v={yt_id}",
                    "thumb_url": f"https://i.ytimg.com/vi/{yt_id}/hqdefault.jpg",
                    "context": clean_ctx,
                    "source_article": final_url,
                }
            )

        def _add_direct_vid(vid_u: str, ctx: str = "") -> None:
            abs_v = urllib.parse.urljoin(final_url, html.unescape(vid_u.strip()))
            if not abs_v.startswith(("http://", "https://")) or abs_v in seen_vkeys:
                return
            if any(bad in abs_v.lower() for bad in ("doubleclick", "ads.", "/ads/", "banner", "tracker")):
                return
            seen_vkeys.add(abs_v)
            clean_ctx = _clean_video_ctx(ctx) or page_title_hint
            videos.append(
                {
                    "kind": "direct_video",
                    "video_id": "",
                    "url": abs_v,
                    "thumb_url": "",
                    "context": clean_ctx,
                    "source_article": final_url,
                }
            )

        # Look in scope_html first, then fallback to raw_html JSON-LD / og:video
        for match in re.finditer(
            r"(?:youtube\.com/(?:embed|watch\?v=|shorts/)|youtube-nocookie\.com/embed/|youtu\.be/)([A-Za-z0-9_-]{11})",
            scope_html,
            flags=re.IGNORECASE,
        ):
            before_html = scope_html[max(0, match.start() - 520) : match.start()]
            prev_texts = [
                _clean_html(m)
                for m in re.findall(r"<(?:p|h[2-4]|figcaption|a)[^>]*>(.*?)</(?:p|h[2-4]|figcaption|a)>", before_html, flags=re.I | re.S)
            ]
            prev_texts = [t for t in prev_texts if len(t) >= 10]
            iframe_slice = scope_html[max(0, match.start() - 180) : min(len(scope_html), match.end() + 260)]
            title_m = re.search(r'\btitle=["\']([^"\']+)["\']', iframe_slice, flags=re.IGNORECASE)
            iframe_title = _clean_html(title_m.group(1)) if title_m else ""
            if iframe_title.lower() in {"youtube video player", "video player", "embedded video"}:
                iframe_title = ""
            best_ctx = prev_texts[-1] if prev_texts else (iframe_title or page_title_hint)
            _add_yt(match.group(1), ctx=best_ctx)

        for match in re.finditer(r'videoid=["\']([A-Za-z0-9_-]{11})["\']', raw_html, flags=re.IGNORECASE):
            _add_yt(match.group(1), ctx=page_title_hint)

        for match in re.finditer(
            r'(?:<meta[^>]+property=["\']og:video[^"\']*["\'][^>]+content=|<(?:video|source)[^>]+src=)["\']([^"\']+\.(?:mp4|webm|m4v)(?:\?[^"\']*)?)["\']',
            scope_html + " " + raw_html[:24_000],
            flags=re.IGNORECASE,
        ):
            _add_direct_vid(match.group(1), ctx=page_title_hint)

        blocks = re.findall(r"<(?:p|li)[^>]*>(.*?)</(?:p|li)>", scope_html, flags=re.IGNORECASE | re.DOTALL)
        useful: list[str] = []
        for b in blocks:
            t = _clean_html(b)
            t = re.sub(r"\b(?:Imagem|Foto|Crédito|Créditos|Fonte|Reprodução)\s*[:/]\s*[^.!?]+[.!?]?", "", t, flags=re.IGNORECASE).strip()
            t = re.sub(r"\b(?:Leitura|Leia mais|Veja também|Compare com)\s*:\s*[^|]+(?:\|\s*[^.!?]+)?", "", t, flags=re.IGNORECASE).strip()
            t = re.sub(r'\S*"\s*style="[^"]*"\s*>?', " ", t, flags=re.IGNORECASE).strip()
            t = re.sub(r"\s+([.,!?;:])", r"\1", t)
            if len(t) < 46 or t.endswith("?") or _JUNK_LINE_RE.search(t) or _EMOJI_PROMO_RE.search(t):
                continue
            if not t.endswith((".", "!", "?", ":")):
                t += "."
            useful.append(t)
            if sum(len(u) for u in useful) >= 3400:
                break

        out["text"] = " ".join(useful)[:3400]
        out["images"] = imgs[:10]
        out["images_meta"] = images_meta[:10]
        out["videos"] = videos[:8]
    except Exception as exc:
        logger.debug("Falha na extração de artigo: %s", exc)
    return out


def _topic_match_tokens(text: str) -> set[str]:
    stop = {
        "gta", "grand", "theft", "auto", "rockstar", "games", "game", "jogo", "sobre",
        "para", "como", "mais", "tudo", "pode", "nova", "novo", "novos", "mesmo", "tempo",
        "com", "uma", "dos", "das", "pelo", "pela", "the", "and", "with", "for", "from",
        "into", "your", "this", "that", "article", "news", "detalhes", "revela", "revelado",
    }
    toks = {w for w in re.split(r"[\W_]+", (text or "").lower()) if len(w) >= 3 and w not in stop}
    return toks


def _find_direct_urls_via_bing(query: str, client: httpx.Client, limit: int = 3) -> list[tuple[str, str]]:
    """
    Search Bing News RSS to locate topic-coherent publisher URLs + snippets when a primary link
    is Google News JS shell, empty, or blocked (403). Requires real keyword overlap beyond generic 'GTA 6'
    so unrelated articles (e.g. actor castings or Black Friday banners) are never pulled in.
    """
    found: list[tuple[str, str]] = []
    q_pt = re.sub(r"\s+", " ", (query or "").strip())[:110]
    if not q_pt:
        return found
    q_en = translate_to_en(q_pt, client=client)[:110]
    ref_toks = _topic_match_tokens(f"{q_pt} {q_en}")
    # Pick concise high-signal search queries (first 8 specific words work far better on Bing than 18-word titles)
    q_pt_short = " ".join(q_pt.split()[:9])
    q_en_short = " ".join(q_en.split()[:9]) if q_en else ""
    searches = [("pt-BR", q_pt_short)]
    if q_en_short and q_en_short.lower() != q_pt_short.lower():
        searches.append(("en-US", q_en_short))
    else:
        searches.append(("en-US", q_pt_short))

    for mkt, q_use in searches:
        try:
            r = client.get("https://www.bing.com/news/search", params={"q": q_use, "format": "rss", "mkt": mkt})
            if r.status_code != 200 or "<item>" not in r.text:
                continue
            root = ET.fromstring(r.text)
            for node in root.findall(".//item")[:8]:
                title = _clean_html(node.findtext("title") or "")
                lk = (node.findtext("link") or "").strip()
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(lk).query)
                real_u = qs.get("url", [lk])[0]
                desc = _clean_html(node.findtext("description") or "")
                cand_toks = _topic_match_tokens(f"{title} {desc} {real_u}")
                # Require genuine subject overlap if reference has specific nouns
                if len(ref_toks) >= 2:
                    overlap = len(ref_toks & cand_toks)
                    if overlap == 0:
                        continue
                if real_u.startswith("http") and "news.google.com" not in real_u:
                    if all(u != real_u for u, _ in found):
                        found.append((real_u, f"{title}. {desc}".strip()))
                if len(found) >= limit:
                    return found
        except Exception:
            continue
    return found


def fetch_article_context_and_images(url: str, title_hint: str = "") -> dict[str, Any]:
    """
    Resolve a news/reference URL (decoding Google News shells via Bing News fallback when needed)
    to extract full factual paragraphs, ranked concrete facts, high-res editorial images,
    and embedded/mentioned videos (YouTube / MP4).
    """
    clean_url = (url or "").strip()
    out: dict[str, Any] = {
        "final_url": clean_url,
        "summary_text": "",
        "fact_sentences": [],
        "images": [],
        "images_meta": [],
        "videos": [],
    }
    is_gnews = "news.google.com" in clean_url.lower()

    texts_raw: list[str] = []
    imgs_all: list[str] = []
    imgs_meta_all: list[dict[str, str]] = []
    videos_all: list[dict[str, str]] = []
    seen_vkeys: set[str] = set()
    snippets: list[str] = []

    def _merge_ext(ext: dict[str, Any]) -> None:
        for im in ext.get("images") or []:
            if im not in imgs_all:
                imgs_all.append(im)
        for im_m in ext.get("images_meta") or []:
            u = im_m.get("url")
            if u and all(x.get("url") != u for x in imgs_meta_all):
                imgs_meta_all.append(im_m)
        for v in ext.get("videos") or []:
            vkey = str(v.get("video_id") or v.get("url") or "")
            if vkey and vkey not in seen_vkeys:
                seen_vkeys.add(vkey)
                videos_all.append(v)

    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, headers=RADAR_HEADERS) as client:
            if clean_url.startswith(("http://", "https://")) and not is_gnews:
                ext = _extract_article_page(client, clean_url)
                out["final_url"] = ext["final_url"]
                if ext["text"]:
                    texts_raw.append(ext["text"])
                _merge_ext(ext)

            # Only resolve alternative topic-matching URLs if primary link is a Google News shell or blocked/empty.
            # NEVER pollute a solid extracted article with unrelated pages just because it had no embedded <video>.
            if (is_gnews or len(" ".join(texts_raw)) < 260) and title_hint.strip():
                hits = _find_direct_urls_via_bing(title_hint, client, limit=3)
                for real_u, snip in hits:
                    if snip:
                        snippets.append(snip)
                    if "en.wikipedia.org" in clean_url and real_u == clean_url:
                        continue
                    ext = _extract_article_page(client, real_u)
                    if ext["text"]:
                        if not out["final_url"] or "news.google.com" in out["final_url"]:
                            out["final_url"] = ext["final_url"]
                        texts_raw.append(ext["text"])
                    _merge_ext(ext)
                    if len(" ".join(texts_raw)) >= 1800:
                        break

            combined_raw = " ".join(texts_raw).strip() or " ".join(snippets).strip()
            if combined_raw:
                pt_text = translate_to_pt_br(combined_raw, client=client)
                out["summary_text"] = pt_text[:3200]
                facts_raw = _extract_ranked_facts(pt_text, limit=12, title_hint=title_hint)
                out["fact_sentences"] = [
                    translate_to_pt_br(f, client=client) if looks_english_text(f) else f
                    for f in facts_raw
                ]
            out["images"] = imgs_all[:10]
            out["images_meta"] = imgs_meta_all[:10]
            out["videos"] = videos_all[:8]
    except Exception as exc:
        logger.warning("Falha ao coletar contexto e imagens do artigo: %s", exc)
    return out


def fetch_gaming_topic_dossier(
    topic_title: str,
    game_name: str = "GTA 6",
    source_url: str = "",
    topic_angle: str = "",
) -> dict[str, Any]:
    """
    Build a rich, Zero-API factual dossier about a GTA 6 / gaming news topic combining
    actual multi-paragraph source articles (including Bing News direct resolution),
    ranked concrete facts (numbers/mechanics/platforms), and extracted article images + videos.
    """
    pt_title = translate_to_pt_br(topic_title)
    pt_angle = translate_to_pt_br(topic_angle) if topic_angle else ""

    search_hint = f"{game_name} {topic_title}".strip()
    if game_name.lower() in topic_title.lower():
        search_hint = topic_title.strip()
    article_data = fetch_article_context_and_images(source_url, title_hint=search_hint)

    wiki_extracts: list[str] = []
    with httpx.Client(timeout=9.0, follow_redirects=True, headers=RADAR_HEADERS) as client:
        for base, title_q in (
            ("https://pt.wikipedia.org/api/rest_v1/page/summary/", "Grand_Theft_Auto_VI"),
            ("https://en.wikipedia.org/api/rest_v1/page/summary/", "Grand_Theft_Auto_VI"),
        ):
            if "gta" not in f"{game_name} {pt_title}".lower() and "grand theft" not in f"{game_name} {pt_title}".lower():
                continue
            try:
                r = client.get(base + title_q)
                if r.status_code == 200:
                    ex = str(r.json().get("extract") or "").strip()
                    if ex:
                        ex_pt = translate_to_pt_br(ex[:750], client=client) if "en.wikipedia.org" in base else ex[:750]
                        wiki_extracts.append(ex_pt)
            except Exception:
                continue

    lines = [
        f"Jogo/Assunto: {game_name} — {pt_title}",
        f"Ângulo editorial: {pt_angle}" if pt_angle else "",
        f"Fonte/Link resolvido: {article_data.get('final_url') or source_url}" if (article_data.get("final_url") or source_url) else "",
        f"Resumo detalhado da notícia:\n{article_data.get('summary_text')}" if article_data.get("summary_text") else "",
        "Fatos-chave extraídos:\n- " + "\n- ".join(article_data.get("fact_sentences") or []) if article_data.get("fact_sentences") else "",
        "Contexto base (Wikipedia GTA VI): " + " | ".join(wiki_extracts) if wiki_extracts else "",
    ]
    summary_text = "\n".join(line for line in lines if line).strip()
    return {
        "game_name": game_name,
        "topic_title": pt_title,
        "topic_angle": pt_angle,
        "summary_text": summary_text,
        "resolved_url": article_data.get("final_url") or source_url,
        "article_summary": article_data.get("summary_text", ""),
        "article_facts": article_data.get("fact_sentences", []),
        "article_images": article_data.get("images", []),
        "article_images_meta": article_data.get("images_meta", []),
        "article_videos": article_data.get("videos", []),
        "wiki_extracts": wiki_extracts,
    }
