from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger("gta6_music_researcher")

from app.lyrics_engine import (
    fetch_synced_lyrics,
    select_lyric_break_cues,
    translate_lines_pt_br,
)


HEADERS = {
    "User-Agent": "MusicHistoryReelsStudio/2.0 (https://localhost; local-creator@example.com)"
}


def _strip_wikitext(raw: str) -> str:
    """Convert MediaWiki markup cleanly into readable plain paragraphs."""
    if not raw:
        return ""
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", raw, flags=re.S | re.I)
    text = re.sub(r"<ref[^>]*/>", "", text, flags=re.I)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    # Remove nested templates {{...}} and MediaWiki tables {| ... |} iteratively
    for _ in range(4):
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)
    text = re.sub(r"\{\|.*?\|\}", "", text, flags=re.S)
    # Convert wikilinks [[target|label]] -> label, [[label]] -> label (skipping File:/Image:/Category:)
    text = re.sub(r"\[\[(?:File|Image|Category|Ficheiro|Categoria):[^\]]*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[[^|\]]+\|([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    # Remove external URLs [http... label] -> label
    text = re.sub(r"\[https?://[^\s\]]+\s+([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[https?://[^\]]+\]", "", text)
    # Drop bold/italic quotes & headings markers, skip References/External links tail
    text = re.split(r"\n==\s*(?:References|External links|Further reading|Referências|Ligações externas)\s*==", text, maxsplit=1, flags=re.I)[0]
    text = re.sub(r"''+", "", text)
    text = re.sub(r"==+\s*([^=]+?)\s*==+", r"\n\1: ", text)
    text = re.sub(r"^\s*[\*#:;]+\s*", "", text, flags=re.M)
    text = re.sub(r"\[\d+\]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _clean_wiki_text(raw: str) -> str:
    text = re.sub(r"\[\d+\]", "", raw or "")
    text = re.sub(r"==+[^=]+==+", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÃÕÇ\"“])", text.replace("\n", " "))
    return [re.sub(r"\s+", " ", p).strip() for p in parts if len(p.strip()) > 25]


def _is_relevant_article(title: str, extract: str, song_title: str, artist: str) -> bool:
    """Verify the article genuinely belongs to the requested song or artist."""
    t_low = title.lower()
    lead_low = (extract[:700] or "").lower()
    artist_tokens = [w for w in re.split(r"\s+", artist.lower().strip()) if len(w) >= 3]
    song_tokens = [w for w in re.split(r"\s+", song_title.lower().strip()) if len(w) >= 3]

    artist_match = (
        all(tok in t_low or tok in lead_low for tok in artist_tokens)
        if artist_tokens
        else artist.lower().strip() in (t_low + " " + lead_low)
    )
    if not artist_match:
        return False

    # Check either song title appears or it's an article specifically about the artist/band
    song_match = (
        all(tok in t_low or tok in lead_low for tok in song_tokens)
        if song_tokens
        else True
    )
    band_page = artist.lower().strip() in t_low
    return song_match or band_page


def _search_wikipedia_article(
    client: httpx.Client,
    lang: str,
    queries: list[str],
    song_title: str,
    artist: str,
    max_chars: int = 4800,
) -> dict[str, Any]:
    api_url = f"https://{lang}.wikipedia.org/w/api.php"
    for q in queries:
        try:
            r = client.get(
                api_url,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": q,
                    "srlimit": 4,
                    "format": "json",
                },
            )
            if r.status_code != 200 or "application/json" not in r.headers.get("content-type", ""):
                continue
            hits = r.json().get("query", {}).get("search", [])
            if not hits:
                continue

            ranked = sorted(
                hits,
                key=lambda h: (
                    3
                    if any(k in h["title"].lower() for k in ("(song)", "(canção)", "(música)"))
                    else (1 if artist.lower() in h["title"].lower() else 0)
                ),
                reverse=True,
            )

            for hit in ranked[:3]:
                pageid = hit["pageid"]
                r_page = client.get(
                    api_url,
                    params={
                        "action": "query",
                        "prop": "extracts|revisions",
                        "exintro": 1,
                        "explaintext": 1,
                        "rvprop": "content",
                        "rvslots": "main",
                        "pageids": str(pageid),
                        "format": "json",
                    },
                )
                if r_page.status_code != 200:
                    continue
                page = r_page.json().get("query", {}).get("pages", {}).get(str(pageid), {})
                intro = _clean_wiki_text(page.get("extract", ""))
                revs = page.get("revisions") or []
                raw_wt = revs[0].get("slots", {}).get("main", {}).get("*", "") if revs else ""
                body_plain = _strip_wikitext(raw_wt)
                full_text = body_plain if len(body_plain) > len(intro) else intro
                if len(full_text) < 120:
                    continue
                if not _is_relevant_article(hit["title"], intro or full_text, song_title, artist):
                    continue
                return {
                    "lang": lang,
                    "title": hit["title"],
                    "pageid": pageid,
                    "intro": intro[:1500],
                    "extract": full_text[:max_chars],
                    "url": f"https://{lang}.wikipedia.org/?curid={pageid}",
                }
        except Exception:
            continue
    return {}


def _query_musicbrainz(client: httpx.Client, song_title: str, artist: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        r = client.get(
            "https://musicbrainz.org/ws/2/recording",
            params={
                "query": f'recording:"{song_title}" AND artist:"{artist}"',
                "fmt": "json",
                "limit": "5",
            },
        )
        if r.status_code != 200 or "application/json" not in r.headers.get("content-type", ""):
            return out
        recs = r.json().get("recordings", [])
        if not recs:
            return out

        dates: list[str] = []
        releases: list[str] = []
        tags: list[str] = []
        artist_credit = ""
        for rec in recs:
            fd = rec.get("first-release-date") or ""
            if fd:
                dates.append(fd)
            if not artist_credit:
                ac = rec.get("artist-credit") or []
                if ac and isinstance(ac[0], dict):
                    artist_credit = ac[0].get("name") or ""
            for rel in rec.get("releases") or []:
                t = rel.get("title")
                if t and t not in releases:
                    releases.append(t)
                d = rel.get("date")
                if d:
                    dates.append(d)
            for tg in rec.get("tags") or []:
                name = tg.get("name")
                if name and name not in tags:
                    tags.append(name)

        years = sorted(
            {m.group(1) for d in dates if (m := re.match(r"(\d{4})", d))}
        )
        out = {
            "artist_credit": artist_credit or artist,
            "earliest_year": years[0] if years else "",
            "years_found": years[:4],
            "releases": releases[:4],
            "tags": tags[:6],
        }
    except Exception as exc:
        logger.debug("Falha na busca de metadados no MusicBrainz: %s", exc)
    return out


def _query_itunes_meta(client: httpx.Client, song_title: str, artist: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        r = client.get(
            "https://itunes.apple.com/search",
            params={"term": f"{artist} {song_title}", "entity": "song", "limit": "5"},
        )
        if r.status_code != 200:
            return out
        artist_low = artist.lower().strip()
        for item in r.json().get("results", []):
            ia = str(item.get("artistName", "")).lower()
            if artist_low in ia or ia in artist_low:
                rel_date = str(item.get("releaseDate", ""))
                yr = rel_date[:4] if len(rel_date) >= 4 and rel_date[:4].isdigit() else ""
                out = {
                    "track_name": item.get("trackName", song_title),
                    "artist_name": item.get("artistName", artist),
                    "collection_name": item.get("collectionName", ""),
                    "release_year": yr,
                    "primary_genre": item.get("primaryGenreName", ""),
                    "artwork_url": str(item.get("artworkUrl100", "")).replace("100x100bb", "800x800bb"),
                }
                break
    except Exception as exc:
        logger.debug("Falha na busca de metadados no iTunes: %s", exc)
    return out


def _pick_fact_sentences(
    sentences: list[str],
    song_title: str,
    artist: str,
) -> dict[str, str]:
    """Classify sentences into origin, lyrics/theme, recording/context, explosion/legacy."""
    joined = " ".join(sentences)
    facts: dict[str, str] = {
        "origin_en": "",
        "recording_en": "",
        "impact_en": "",
        "theme_en": "",
        "raw_excerpt": joined[:1400],
    }
    for s in sentences:
        sl = s.lower()
        if not facts["origin_en"] and any(k in sl for k in ("released", "single", "debut", "written by", "album")):
            facts["origin_en"] = s
        elif not facts["theme_en"] and any(k in sl for k in ("lyrics", "about", "inspired", "pmrc", "censor", "tipper", "parental", "parents")):
            facts["theme_en"] = s
        elif not facts["recording_en"] and any(k in sl for k in ("produced", "recorded", "studio", "guitar", "riff", "video", "rick rubin", "version")):
            facts["recording_en"] = s
        elif not facts["impact_en"] and any(k in sl for k in ("chart", "billboard", "mtv", "popular", "hit", "certified", "legacy", "famous", "rank")):
            facts["impact_en"] = s
    return facts


def research_music_history_free(
    song_title: str,
    artist: str,
    year: str = "",
    genre: str = "",
) -> dict[str, Any]:
    """
    Free zero-API-key historical researcher combining:
    - Wikipedia PT & EN full article text (verified relevance to song/artist)
    - MusicBrainz recording & release timeline
    - iTunes public catalog metadata & album art URL
    """
    dossier: dict[str, Any] = {
        "song_title": song_title,
        "artist": artist,
        "resolved_year": year,
        "resolved_genre": genre,
        "album": "",
        "wikipedia_pt": {},
        "wikipedia_en": {},
        "musicbrainz": {},
        "itunes": {},
        "lyrics": {},
        "extracted_facts": {},
        "summary_text": "",
        "sources_used": [],
    }

    with httpx.Client(timeout=8.0, headers=HEADERS, follow_redirects=True) as client:
        wiki_pt = _search_wikipedia_article(
            client,
            "pt",
            [f'"{song_title}" "{artist}"', f"{song_title} {artist}", f"{artist} (banda)"],
            song_title=song_title,
            artist=artist,
        )
        wiki_en = _search_wikipedia_article(
            client,
            "en",
            [f'"{song_title}" "{artist}" song', f"{song_title} ({artist} song)", f"{artist} {song_title}"],
            song_title=song_title,
            artist=artist,
        )
        mb = _query_musicbrainz(client, song_title, artist)
        itunes = _query_itunes_meta(client, song_title, artist)

    lyrics_info = fetch_synced_lyrics(song_title=song_title, artist=artist)
    break_picks: list[dict[str, Any]] = []
    finale_start: float | None = None
    if lyrics_info.get("cues"):
        break_picks, finale_start = select_lyric_break_cues(
            cues=lyrics_info["cues"],
            song_title=song_title,
            track_dur=lyrics_info.get("duration") or None,
        )
    lyrics_info["break_picks"] = break_picks
    lyrics_info["finale_start"] = finale_start

    dossier["wikipedia_pt"] = wiki_pt
    dossier["wikipedia_en"] = wiki_en
    dossier["musicbrainz"] = mb
    dossier["itunes"] = itunes
    dossier["lyrics"] = lyrics_info

    if wiki_pt:
        dossier["sources_used"].append(f"Wikipedia PT: {wiki_pt['title']}")
    if wiki_en:
        dossier["sources_used"].append(f"Wikipedia EN: {wiki_en['title']}")
    if mb:
        dossier["sources_used"].append("MusicBrainz")
    if itunes:
        dossier["sources_used"].append("iTunes Catalog")
    if break_picks:
        dossier["sources_used"].append("LRCLIB Letra Sincronizada")

    intro_en = wiki_en.get("intro", "") or wiki_en.get("extract", "")[:900]
    full_en = wiki_en.get("extract", "")

    # Resolve original release year accurately (prioritize Wikipedia release lead year over MusicBrainz live reissues)
    if not dossier["resolved_year"]:
        years_candidates: list[str] = []
        m_rel = re.findall(r"(?:released|recorded|written)[^.\n]{0,45}\b(19[4-9]\d|20[0-2]\d)\b", intro_en, flags=re.I)
        if m_rel:
            years_candidates.append(min(m_rel))
        m_yr = re.findall(r"\b(19[5-9]\d|20[0-2]\d)\b", intro_en[:600])
        if m_yr:
            years_candidates.append(min(m_yr))
        if mb.get("earliest_year"):
            years_candidates.append(mb["earliest_year"])
        if itunes.get("release_year"):
            years_candidates.append(itunes["release_year"])
        if years_candidates:
            dossier["resolved_year"] = min(years_candidates)

    if (not genre or genre in {"Rock / Música", "Rock / História da Música"}) and itunes.get("primary_genre"):
        dossier["resolved_genre"] = itunes["primary_genre"]
    elif not dossier["resolved_genre"] and mb.get("tags"):
        dossier["resolved_genre"] = " / ".join(mb["tags"][:2]).title()

    # Resolve original album name carefully
    album_match = re.search(r"from (?:their|his|her|the) [\w\s]*?album[, ]+([A-Z0-9][^.\n,;]{2,45})", intro_en)
    album = (
        album_match.group(1).strip()
        if album_match
        else (itunes.get("collection_name") or (mb.get("releases")[0] if mb.get("releases") else ""))
    )
    dossier["album"] = album

    en_sentences = _split_sentences(full_en)
    pt_sentences = _split_sentences(wiki_pt.get("extract", ""))
    extracted = _pick_fact_sentences(en_sentences or pt_sentences, song_title, artist)

    producer_match = re.search(
        r"produced by ([A-Z][a-zA-Zéàü\.\- ]{2,35})",
        full_en,
    )
    if producer_match:
        extracted["producer"] = producer_match.group(1).strip(" .,;")

    chart_match = re.search(
        r"((?:Billboard|Hot 100|Mainstream Rock|MTV)[^.\n]{5,90})",
        full_en,
    )
    if chart_match:
        extracted["chart_or_mtv"] = chart_match.group(1).strip(" .,;")

    years_timeline = sorted(
        {m for m in re.findall(r"\b(19[5-9]\d|20[0-2]\d)\b", full_en[:3200])}
    )
    if years_timeline:
        extracted["timeline_years"] = years_timeline[:5]

    # Look for a genuine spoken/interview quote in Wikipedia text (strictly inside balanced quotes, never pre-quote lead-in)
    for m_q in re.finditer(r'“([^”\n]{28,195})”|«([^»\n]{28,195})»|"([^"\n]{28,195})"', full_en):
        cand_q = (m_q.group(1) or m_q.group(2) or m_q.group(3) or "").strip()
        cand_q = re.sub(r"\s+", " ", cand_q)
        if not cand_q or cand_q.lower() == song_title.lower():
            continue
        # Reject prose fragments accidentally spanning closing->opening ASCII quotes (e.g. '", He said:')
        if cand_q[0] in (",", ".", ";", ":") or cand_q.endswith((":", ";", ",")):
            continue
        if not re.match(r"^[A-ZÀ-Ý0-9\(]", cand_q):
            continue
        if len(cand_q.split()) < 6:
            continue
        pt_q = translate_lines_pt_br([cand_q])
        extracted["quote_pt"] = pt_q[0].strip() if pt_q and pt_q[0].strip() else cand_q
        break

    dossier["extracted_facts"] = extracted

    lines: list[str] = []
    if dossier["resolved_year"]:
        lines.append(f"Ano identificado: {dossier['resolved_year']}")
    if album:
        lines.append(f"Álbum / Lançamento: {album}")
    if dossier["resolved_genre"]:
        lines.append(f"Gênero: {dossier['resolved_genre']}")
    if extracted.get("producer"):
        lines.append(f"Produção: {extracted['producer']}")
    if extracted.get("chart_or_mtv"):
        lines.append(f"Marco / Destaque: {extracted['chart_or_mtv']}")
    if break_picks:
        lines.append(
            "Ganchos da Letra (LRCLIB):\n"
            + "\n".join(
                f"• [{bp['start_sec']:.1f}s] {bp['lyric_en']} -> {bp.get('lyric_pt', '')}"
                for bp in break_picks
            )
        )
    if wiki_pt.get("intro"):
        lines.append("Wikipedia PT:\n" + wiki_pt["intro"][:900])
    if full_en:
        lines.append("Wikipedia EN:\n" + full_en[:2400])
    dossier["summary_text"] = "\n\n".join(lines)
    return dossier


def synthesize_script_from_dossier(
    dossier: dict[str, Any],
    topic_angle: str = "",
    reference_notes: str = "",
    video_mode: str = "single_all_in_one",
    music_break_sec: float = 3.2,
    opening_mode: str = "lyric_hook",
) -> dict[str, Any]:
    """
    Create a song-specific PT-BR Reels script grounded in verified Wikipedia/MusicBrainz/iTunes
    facts + LRCLIB synced lyric hooks — 0 LLM API calls required!
    """
    song = dossier["song_title"]
    artist = dossier["artist"]
    year = dossier.get("resolved_year") or "Clássico"
    genre = dossier.get("resolved_genre") or "História da Música"
    album = dossier.get("album") or ""
    facts = dossier.get("extracted_facts") or {}
    lyrics_data = dossier.get("lyrics") or {}
    break_picks: list[dict[str, Any]] = lyrics_data.get("break_picks") or []
    lyrics_cues: list[dict[str, Any]] = lyrics_data.get("cues") or []
    finale_start: float | None = lyrics_data.get("finale_start")
    quote_pt: str = facts.get("quote_pt") or ""

    pt_sents = _split_sentences((dossier.get("wikipedia_pt") or {}).get("extract", ""))
    en_txt = ((dossier.get("wikipedia_en") or {}).get("extract", "")).lower()

    producer = facts.get("producer", "")
    years_tl = facts.get("timeline_years") or ([year] if year.isdigit() else [])
    later_years = [y for y in years_tl if y > year] if year.isdigit() else years_tl[1:]
    later_year = later_years[0] if later_years else ""

    has_censorship_theme = any(
        w in en_txt
        for w in ("pmrc", "parental advisory", "tipper gore", "al gore", "censor", "parents music resource", "warning sticker", "warning label")
    )
    has_live_revival = any(
        w in en_txt
        for w in ("mtv", "thrall", "beavis", "live version", "re-recorded", "remixed version", "1993", "1994")
    )

    album_phrase = f"do álbum {album}, " if album else ""
    prod_phrase = f", com produção de {producer}" if producer else ""
    angle_phrase = (
        f"Com foco em {topic_angle.strip().rstrip('.')}, "
        if topic_angle.strip()
        else ""
    )

    if has_censorship_theme:
        theme_line = (
            "O que muita gente não percebe é que a letra ironiza diretamente a censura moral e os selos de aviso em discos nos Estados Unidos."
        )
        if not quote_pt:
            quote_pt = f'"{song} nasceu como crítica aos pais que queriam censurar o que os filhos escutavam."'
    elif pt_sents and len(pt_sents[0]) <= 165:
        theme_line = pt_sents[0]
    else:
        theme_line = (
            f"{angle_phrase}a composição nasceu em meio às transformações sonoras de {year}, unindo peso e melodia marcante."
        )

    if has_live_revival and later_year:
        revival_line = (
            f"O mais curioso é que a faixa só explodiu de vez anos depois, em {later_year}, ao dominar a MTV e as rádios."
        )
    elif later_year and later_year != year:
        revival_line = (
            f"Dos bastidores de estúdio até sua consagração em {later_year}, o single conquistou espaço permanente nos palcos."
        )
    else:
        revival_line = (
            f"Nos bastidores{prod_phrase}, cada camada de instrumento ajudou a transformar esse momento em assinatura de {artist}."
        )

    if video_mode == "single_all_in_one":
        fallback_brk = round(max(0.0, float(music_break_sec)), 2)

        def pick_brk(slot_idx: int) -> tuple[float, float | None, str, str]:
            if slot_idx < len(break_picks):
                bp = break_picks[slot_idx]
                dur_val = round(min(6.0, max(2.4, float(bp.get("duration_sec") or fallback_brk))), 2)
                return (
                    dur_val if fallback_brk > 0 else 0.0,
                    float(bp["start_sec"]),
                    str(bp.get("lyric_en") or ""),
                    str(bp.get("lyric_pt") or ""),
                )
            return fallback_brk, None, "", ""

        b0_dur, b0_src, b0_en, b0_pt = pick_brk(0)
        b1_dur, b1_src, b1_en, b1_pt = pick_brk(1)
        b2_dur, b2_src, b2_en, b2_pt = pick_brk(2)
        b3_dur, b3_src, b3_en, b3_pt = pick_brk(3)

        if opening_mode == "lyric_hook" and b0_src is not None:
            hook_narration = (
                f"Pouca gente percebe o aviso escondido no começo de {song}, de {artist}. Escuta esse trecho:"
                if has_censorship_theme
                else f"Essa faixa de {artist} esconde uma história real na própria letra. Presta atenção nesse começo:"
            )
            scene2_narration = f"Lançada em {year} {album_phrase}no ritmo de {genre}{prod_phrase}, a música já segue provocando:"
            scene3_narration = f"{theme_line} Repara na virada da letra:"
            scene4_narration = f"{revival_line} E o refrão explode assim:"
        else:
            hook_narration = f"Você conhece {song}, de {artist}, mas sabe a história real por trás desse clássico de {year}?"
            scene2_narration = f"Lançada originalmente {album_phrase}em {year}, a faixa carrega o DNA do estilo {genre}{prod_phrase}."
            scene3_narration = theme_line
            scene4_narration = revival_line

        genre_low = (genre or "").lower()
        is_non_guitar = any(g in genre_low for g in ("pop", "hip", "rap", "electr", "synth", "dance", "jazz", "soul", "mpb", "bossa", "classical"))
        s3_search = (
            "Parental Advisory PMRC Tipper Gore warning"
            if has_censorship_theme
            else (
                f"{artist} concert stage"
                if later_year
                else (f"{artist} live performance" if is_non_guitar else f"{artist} band musician")
            )
        )
        s3_video = (
            "rock concert stage crowd"
            if not is_non_guitar
            else "music concert microphone stage lights"
        )
        s4_search = (
            f"{artist} MTV concert live"
            if (has_live_revival and "mtv" in en_txt)
            else f"{artist} live concert"
        )

        raw_scenes = [
            {
                "title": "O Gancho",
                "shot_intent": "hook_establish",
                "narration_text": hook_narration,
                "visual_prompt": f"Dramatic vertical 9:16 editorial concert photo inspired by {artist}, intense spotlight smoke, vintage film grain, no text",
                "search_query": f"{artist} {song} concert",
                "video_query": f"{artist} live concert stage",
                "effect": "zoom_in",
                "highlight_words": [song, artist, "ouve", "escuta", year],
                "music_break_after_sec": b0_dur,
                "music_break_audio_start_sec": b0_src,
                "music_break_lyric_en": b0_en,
                "music_break_lyric_pt": b0_pt,
                "quote_text": "",
            },
            {
                "title": "Origem e Álbum",
                "shot_intent": "context_origin",
                "narration_text": scene2_narration,
                "visual_prompt": f"Vintage {year} analog studio mixing console tape reel, warm moody shadows, vertical 9:16, no text",
                "search_query": f"{artist} {album} album" if album else f"{artist} {year} recording",
                "video_query": "recording studio tape",
                "effect": "ken_burns_duotone",
                "highlight_words": [year, genre.split("/")[0].strip(), album or song],
                "music_break_after_sec": b1_dur,
                "music_break_audio_start_sec": b1_src,
                "music_break_lyric_en": b1_en,
                "music_break_lyric_pt": b1_pt,
                "quote_text": "",
            },
            {
                "title": "A História da Letra",
                "shot_intent": "story_conflict",
                "narration_text": scene3_narration,
                "visual_prompt": f"Vintage newspaper clipping textures, moody analog stage and mic from {year}, vertical 9:16, no text",
                "search_query": s3_search,
                "video_query": s3_video,
                "effect": "pan_right",
                "highlight_words": ["censura", "letra", "aviso", "história"],
                "music_break_after_sec": b2_dur,
                "music_break_audio_start_sec": b2_src,
                "music_break_lyric_en": b2_en,
                "music_break_lyric_pt": b2_pt,
                "quote_text": quote_pt,
            },
            {
                "title": "A Virada Cultural",
                "shot_intent": "climax_reveal",
                "narration_text": scene4_narration,
                "visual_prompt": f"Dramatic concert stage performance of {artist} with warm amber rim lights, vertical 9:16, no text",
                "search_query": s4_search,
                "video_query": "rock concert crowd music",
                "effect": "vintage_grain",
                "highlight_words": [later_year or "MTV", "explodiu", "refrão", artist],
                "music_break_after_sec": b3_dur,
                "music_break_audio_start_sec": b3_src,
                "music_break_lyric_en": b3_en,
                "music_break_lyric_pt": b3_pt,
                "quote_text": "",
            },
            {
                "title": "Legado e Som Final",
                "shot_intent": "cta_handoff",
                "narration_text": f"Décadas depois, {song} segue como hino de {artist}. Aumenta o som, curte o trecho final e salva nos Destaques!",
                "visual_prompt": f"Close up glowing classic vinyl record spinning on vintage turntable, embers in air, dark studio, vertical 9:16, no text",
                "search_query": f"{artist} band concert live",
                "video_query": "stage lights concert",
                "effect": "zoom_out",
                "highlight_words": [song, "trecho", "Destaques"],
                "music_break_after_sec": 0.0,
                "music_break_audio_start_sec": None,
                "music_break_lyric_en": "",
                "music_break_lyric_pt": "",
                "quote_text": "",
            },
        ]
        caption_txt = (
            f"A história por trás de {song}, de {artist} ({year})! 🎵🔥\n\n"
            "👉 Cada verso da música conta parte dessa história — assista com som ligado e legenda!\n\n"
            "Qual faixa merece o próximo episódio? Salva nos Destaques e comenta aqui!"
        )
    else:
        raw_scenes = [
            {
                "title": "O Gancho",
                "shot_intent": "hook_establish",
                "narration_text": f"Você conhece {song}, de {artist}, mas sabe a história real por trás desse clássico de {year}?",
                "visual_prompt": f"Dramatic vertical 9:16 editorial concert photo inspired by {artist}, intense spotlight smoke, vintage film grain, no text",
                "search_query": f"{artist} live concert",
                "video_query": "rock concert stage",
                "effect": "zoom_in",
                "highlight_words": [song, artist, year],
                "music_break_after_sec": 0.0,
            },
            {
                "title": "Origem e Álbum",
                "shot_intent": "context_origin",
                "narration_text": f"Lançada originalmente {album_phrase}em {year}, a faixa carrega o DNA do estilo {genre}{prod_phrase}.",
                "visual_prompt": f"Vintage {year} analog studio mixing console tape reel, warm moody shadows, vertical 9:16, no text",
                "search_query": "recording studio mixing console analog",
                "video_query": "recording studio tape",
                "effect": "ken_burns_duotone",
                "highlight_words": [year, genre.split("/")[0].strip(), album or song],
                "music_break_after_sec": 0.0,
            },
            {
                "title": "A História da Letra",
                "shot_intent": "story_conflict",
                "narration_text": theme_line,
                "visual_prompt": f"Vintage newspaper clipping textures, moody analog guitar amplifier and studio mic from {year}, vertical 9:16, no text",
                "search_query": f"{artist} band musician guitar",
                "video_query": "guitar musician rock stage",
                "effect": "pan_right",
                "highlight_words": ["censura", "letra", "aviso", "história"],
                "music_break_after_sec": 0.0,
            },
            {
                "title": "A Virada Cultural",
                "shot_intent": "climax_reveal",
                "narration_text": revival_line,
                "visual_prompt": f"Electric guitar player silhouette on dark dramatic concert stage with warm red and amber rim lights, vertical 9:16, no text",
                "search_query": "electric guitar rock concert",
                "video_query": "rock concert crowd music",
                "effect": "vintage_grain",
                "highlight_words": [later_year or "MTV", "explodiu", "palcos", artist],
                "music_break_after_sec": 0.0,
            },
            {
                "title": "Legado Histórico",
                "shot_intent": "legacy_resolution",
                "narration_text": f"Décadas depois, {song} segue como referência obrigatória para entender a força de {artist} na história da música.",
                "visual_prompt": f"Close up glowing classic vinyl record spinning on vintage turntable, embers in air, dark studio, vertical 9:16, no text",
                "search_query": "vinyl record turntable",
                "video_query": "vinyl record turntable",
                "effect": "pan_left",
                "highlight_words": ["Décadas", "referência", song],
                "music_break_after_sec": 0.0,
            },
            {
                "title": "Ouça Agora (Parte 2)",
                "shot_intent": "cta_handoff",
                "narration_text": f"Quer sentir cada segundo desse som? Confira o próximo Reel para ouvir {song} e salve nos Destaques!",
                "visual_prompt": f"Minimalist dark obsidian stage glowing audio spectrum bars and embers, album showcase mood, vertical 9:16, no text",
                "search_query": f"{artist} album",
                "video_query": "stage lights concert",
                "effect": "zoom_out",
                "highlight_words": ["próximo", "Reel", "Destaques"],
                "music_break_after_sec": 0.0,
            },
        ]
        caption_txt = (
            f"A história por trás de {song}, de {artist} ({year})! 🎵🔥\n\n"
            "👉 Parte 1: Os bastidores e fatos reais que marcaram essa faixa.\n"
            "🎧 Parte 2: Assista ao próximo Reel (ou acesse os Destaques) para ouvir a música!\n\n"
            "Qual música você quer no próximo episódio? Salva e comenta aqui!"
        )

    tag_artist = re.sub(r"[^a-zA-Z0-9]", "", artist.lower()) or "musica"
    tag_song = re.sub(r"[^a-zA-Z0-9]", "", song.lower()) or "classico"
    sources_str = ", ".join(dossier.get("sources_used") or ["Pesquisa Local"])

    return {
        "year": year,
        "genre": genre,
        "hook_title": f"A HISTÓRIA DE {song.upper()} ({year})" if year.isdigit() else f"POR TRÁS DE {song.upper()}",
        "reel2_title": f"{song} — {artist}",
        "reel2_subtitle": f"{album + ' • ' if album else ''}{year} • {genre}",
        "instagram_caption": caption_txt,
        "hashtags": [
            "#historiadamusica",
            "#curiosidadesmusicais",
            f"#{tag_artist}",
            f"#{tag_song}",
            "#reelsmusica",
            "#musicahistoria",
            "#classicosdamusica",
            "#dicasdemusica",
        ],
        "raw_scenes": raw_scenes,
        "lyrics_cues": lyrics_cues[:80],
        "finale_audio_start_sec": finale_start,
        "research_summary": f"Fontes gratuitas ({sources_str}):\n" + dossier.get("summary_text", "")[:1400],
        "engine_used": "free_wikipedia_musicbrainz" if dossier.get("sources_used") else "offline_template",
    }
