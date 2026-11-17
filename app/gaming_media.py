from __future__ import annotations

import datetime
import hashlib
import html as _html
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("gta6_media")

import httpx

from PIL import Image

from app.audio_processor import find_best_highlight_start, get_audio_duration
from app.config import GTA6_INPUTS_DIR, INPUTS_DIR, PROJECTS_DIR, get_ffmpeg_exe
from app.gaming_radar import fetch_article_context_and_images, translate_to_en
from app.models import MusicStoryProject, SceneItem
from app.rights import media_allowed
from app.visual_assets import (
    WIKI_HEADERS,
    _query_commons_images,
    _search_wikipedia_topic_media,
    apply_scene_image_url,
    build_contextual_ai_overlay_prompt,
    extract_video_frame_thumb,
    fit_cover_9_16,
    generate_ai_illustration,
    generate_procedural_scene_card,
)


def fit_overlay_editorial_png(src_path: Path, out_png: Path, max_w: int = 960, max_h: int = 720) -> Path:
    """
    Save an illustrative news/topic image preserving its natural aspect ratio
    (so widescreen screenshots, game art, maps, and hardware photos keep their full framing
    inside PiP cards / split-screen frames) scaled crisply into RGBA PNG.
    """
    out_png.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src_path) as im:
        rgb = im.convert("RGB")
        w, h = rgb.size
        # Upscale small thumbnails gently so card graphics never look blurry
        scale = min(max_w / max(1, w), max_h / max(1, h))
        if w < 420 and scale > 1.0:
            scale = min(scale, 2.0)
        elif scale > 1.35:
            scale = 1.35
        nw = max(320, int(round(w * scale)))
        nh = max(180, int(round(h * scale)))
        if (nw, nh) != (w, h):
            rgb = rgb.resize((nw, nh), Image.Resampling.LANCZOS)
        rgb.convert("RGBA").save(out_png, format="PNG")
    return out_png


def _is_usable_editorial_image(raw_path: Path) -> bool:
    """Reject tiny icons, skinny vertical strips (<0.65), ultra-wide header banners (>2.25), and transparent logos."""
    try:
        with Image.open(raw_path) as im:
            w, h = im.size
            if w < 300 or h < 200:
                return False
            aspect = w / float(max(1, h))
            if aspect > 2.25 or aspect < 0.65:
                return False
            if im.mode in ("RGBA", "LA", "P"):
                rgba = im.convert("RGBA")
                alpha = rgba.getchannel("A")
                transparent_px = sum(1 for a in alpha.getdata() if a < 20)
                if transparent_px / float(max(1, w * h)) > 0.35:
                    return False
        return True
    except Exception:
        return False


def apply_overlay_image_url(url: str, out_png: Path) -> bool:
    """Download an illustrative image URL, filter out banners/logos/ads, and fit cleanly as overlay PNG."""
    clean_url = (url or "").strip()
    if not clean_url.startswith(("http://", "https://")) or _BAD_IMAGE_URL_RE.search(clean_url):
        return False
    out_png.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_png.with_suffix(".ov.raw")
    try:
        with httpx.Client(timeout=15.0, headers=WIKI_HEADERS, follow_redirects=True) as client:
            resp = client.get(clean_url)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "image/" in ctype and len(resp.content) > 6_000:
                raw_path.write_bytes(resp.content)
                if _is_usable_editorial_image(raw_path):
                    fit_overlay_editorial_png(raw_path, out_png)
                    raw_path.unlink(missing_ok=True)
                    return out_png.exists()
    except Exception as exc:
        logger.debug("Falha ao baixar imagem editorial de %s: %s", clean_url, exc)
    raw_path.unlink(missing_ok=True)
    return False


VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}


def _thumbs_dir() -> Path:
    d = GTA6_INPUTS_DIR / "_thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_gta6_library_videos() -> list[dict[str, Any]]:
    """List video files inside inputs/gta6/ with cached durations and scene index counts."""
    items: list[dict[str, Any]] = []
    if not GTA6_INPUTS_DIR.exists():
        return items
    for p in sorted(GTA6_INPUTS_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        dur = 0.0
        try:
            dur = round(get_audio_duration(p), 1)
        except Exception:
            dur = 0.0
        idx_file = _thumbs_dir() / f"{p.stem}_scenes.json"
        scenes_count = 0
        if idx_file.exists():
            try:
                scenes_count = len(json.loads(idx_file.read_text(encoding="utf-8")))
            except Exception:
                scenes_count = 0
        items.append(
            {
                "filename": p.name,
                "path": str(p),
                "duration_sec": dur,
                "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
                "indexed_scenes": scenes_count,
            }
        )
    return items


def index_trailer_scenes(video_path: Path, max_clips: int = 12, force: bool = False) -> list[dict[str, Any]]:
    """
    Detect distinct cuts in a trailer/gameplay MP4 via FFmpeg scene detection (with
    clean even-interval backfill), save cached 480px preview thumbnails, and return
    clickable scene cards with exact `start_sec`.
    """
    if not video_path.exists():
        return []
    tdir = _thumbs_dir()
    cache_json = tdir / f"{video_path.stem}_scenes.json"
    if cache_json.exists() and not force:
        try:
            cached = json.loads(cache_json.read_text(encoding="utf-8"))
            if cached and all(Path(c["thumb_path"]).exists() for c in cached):
                return cached
        except Exception as exc:
            logger.debug("Falha ao ler cache de cenas de %s: %s", cache_json, exc)

    dur = max(2.0, get_audio_duration(video_path))
    ffmpeg = get_ffmpeg_exe()
    detected_times: list[float] = []

    try:
        res = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-i", str(video_path),
                "-vf", "fps=2,select='gt(scene,0.28)',showinfo",
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )
        for m in re.finditer(r"pts_time:(\d+(?:\.\d+)?)", res.stderr or ""):
            ts = round(float(m.group(1)), 1)
            if 0.4 <= ts <= max(1.0, dur - 2.0):
                if not detected_times or (ts - detected_times[-1]) >= 2.8:
                    detected_times.append(ts)
    except Exception:
        detected_times = []

    # Guarantee crisp coverage across the entire trailer/clip even if few hard cuts detected
    if len(detected_times) < min(8, max_clips):
        step = max(2.5, (dur - 2.0) / max(1, min(max_clips, int(dur // 3) or 6)))
        cur = 0.5
        while cur < max(1.5, dur - 1.5) and len(detected_times) < max_clips:
            r_cur = round(cur, 1)
            if not any(abs(t - r_cur) < 2.2 for t in detected_times):
                detected_times.append(r_cur)
            cur += step

    detected_times = sorted(detected_times)[:max_clips]
    if not detected_times:
        detected_times = [0.5]

    out_cards: list[dict[str, Any]] = []
    file_hash = hashlib.sha1(str(video_path).encode("utf-8", errors="ignore")).hexdigest()[:8]
    for i, ts in enumerate(detected_times, start=1):
        thumb_file = tdir / f"{file_hash}_c{i:02d}_{int(ts*10)}.png"
        ok = thumb_file.exists() or extract_video_frame_thumb(
            video_path,
            thumb_file,
            seek_sec=ts,
            vertical_9_16=False,
        )
        if not ok:
            continue
        out_cards.append(
            {
                "index": i,
                "clip_name": video_path.name,
                "clip_path": str(video_path),
                "start_sec": ts,
                "label": f"{video_path.stem[:20]} @ {ts:.1f}s",
                "thumb_path": str(thumb_file),
                "thumb_url": f"/api/gaming/clip-thumb?file={thumb_file.name}",
            }
        )

    cache_json.write_text(json.dumps(out_cards, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_cards


_BING_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
}

_BAD_STOCK_DOMAINS = (
    "shutterstock", "gettyimages", "alamy", "istockphoto", "depositphotos",
    "dreamstime", "123rf", "vecteezy", "pinterest", "redd.it/avatar",
    "wikia", "favicon", "gravatar", "lookaside.fbsbx.com",
)

_BAD_IMAGE_URL_RE = re.compile(
    r"(?:lookaside\.fbsbx\.com|black[-_]?friday|bf20\d\d|playpng|/logo|logo\.|banner|avatar|sprite|icon|widget|badge|promo|pixel|spacer|placeholder|button|share|newsletter)",
    re.IGNORECASE,
)

_GENERIC_GAMING_STOP = frozenset({
    "gta", "grand", "theft", "auto", "rockstar", "games", "game", "jogo", "jogos",
    "trailer", "oficial", "official", "noticias", "noticia", "notícia", "notícias",
    "news", "video", "vídeo", "short", "shorts", "sobre", "para", "como", "mais",
    "pode", "nova", "novo", "tudo", "essa", "esse", "isso", "aqui", "agora", "voce",
    "você", "eles", "take", "two", "screenshot", "artwork", "announcement", "announced",
    "announce", "reveal", "revealed", "reveals", "revelation", "revelacao", "revelação",
    "revelado", "revelados", "revela", "revelou", "detalhe", "detalhes", "detalhando",
    "detail", "details", "detailed", "detailing", "novidade", "novidades", "teaser",
    "clip", "clipe", "imagem", "foto", "cena", "versao", "versão", "parte", "hoje",
    "ainda", "muito", "pela", "pelo", "com", "sem", "uma", "uns", "das", "dos", "que",
    "release", "released", "releasing", "lancamento", "lançamento", "lancado", "lançado",
    "launch", "launched", "launching", "update", "updates", "updated", "atualizacao",
    "atualização", "report", "reports", "reported", "story", "attention", "change",
    "changes", "changed", "muda", "mudou", "mudanca", "mudança", "virada", "twist",
    "midnight", "meia", "noite", "tomorrow", "today", "tonight", "day", "dia", "night",
    "seguinte", "wait", "waiting", "esperar", "esperando", "acabou", "saiu", "precisa",
    "pesada", "pesado", "melhor", "vida", "viver", "live", "living", "best", "life",
    "qual", "escolhe", "choose", "choice", "conta", "me", "funciona", "assim", "pratica",
    "prática", "practical", "nivel", "nível", "level", "misterio", "mistério", "misterios",
    "mistérios", "mystery", "mysteries", "cercam", "surround", "liberar", "madrugar",
    "episodio", "episódio", "episode", "world", "mundo", "open", "aberto", "extensao",
    "extensão", "extent", "motivacoes", "motivações", "motivations", "apresentado",
    "durante", "pouco", "tenha", "será", "sera", "vai", "ver", "quem", "entenda",
    "veja", "confira", "watch", "look", "see", "needs", "everyone", "players", "gamers",
    "fans", "jogadores", "feature", "features", "first", "second", "third", "new",
    "latest", "confirm", "confirmed", "confirms", "confirmado", "rumor", "rumors",
    "website", "main", "site", "europe", "european", "europa", "europeia",
    "the", "and", "with", "for", "from", "that", "this", "what", "when", "will", "you",
    "your", "are", "was", "were", "has", "have", "about", "after", "into", "just",
    "here", "there", "then", "than", "where", "which", "while", "they", "them", "their",
    "been", "does", "make", "made", "more", "most", "much", "many", "some", "such",
    "only", "very", "even", "also", "well", "like", "good", "real", "true", "sure",
    "know", "need", "want", "come", "came", "goes", "went", "take", "took", "over",
    "onto", "upon", "back", "down", "time", "year", "soon", "late", "next", "last",
    "full", "high", "side", "play", "free", "paid", "out", "pay", "get", "got",
    "esta", "este", "nada", "algo", "cada", "onde", "quando", "porque", "antes",
    "depois", "ontem", "fala", "disse", "deve", "fazer", "feito", "sendo", "lado",
    "tipo", "coisa", "coisas", "jeito", "forma", "caso", "vezes", "anos", "dias",
    "meses", "toda", "todo", "todos", "todas", "outro", "outra", "outros", "outras",
    "mesma", "quase", "apenas", "entre", "contra", "desde", "ate", "até", "mais",
    "cinco", "seis", "sete", "oito", "nove", "zero", "puro", "pura", "heavy",
    "chega", "chegam", "chegando", "chegar", "arrives", "arrive", "arriving",
    "data", "date", "dates", "other", "others", "brazil", "brasil", "brazilian",
    "brasileiro", "brasileira", "brasileiros", "levantamento", "levantamentos",
    "acharam", "achar", "encontrar", "press", "imprensa", "standard", "edicao",
    "edição", "edition", "consulta", "dado", "dados", "arquivo", "contas",
    "codigo", "código", "codigos", "códigos", "regional", "regionais", "garantir",
    "baixar", "sair", "saindo", "percebeu", "ninguém", "números", "oficiais",
    "número", "numero", "horário", "local", "noção", "partir", "livre", "espaço",
    "armazenamento", "funcionam", "comprados", "comenta", "além", "disso",
    "conforme", "segundo", "ficam", "fica", "título", "titulos", "title", "titles",
    "words", "word", "palavra", "palavras", "study", "survey", "pesquisa", "revela",
    "revelam", "descubra", "entender", "entenda", "sabia", "sabe", "veja", "olha",
})


def _semantic_tokens(text: str) -> set[str]:
    """Extract meaningful tokens (>= 4 chars or crisp visual short terms like ps5/dlc/fps/map/car), dropping generic filler."""
    raw = re.split(r"[\W_]+", (text or "").lower())
    out: set[str] = set()
    short_keep = {
        "ia", "pc", "4k", "3d", "tv", "hd", "vr", "dlc", "rpg", "fps", "ps5", "ps4",
        "ray", "cop", "car", "gun", "map", "bar", "vip", "hud", "npc", "usa", "rio", "gym",
    }
    for w in raw:
        if not w or w in _GENERIC_GAMING_STOP:
            continue
        if len(w) >= 4 or w in short_keep:
            out.add(w)
    return out


def _token_match_list(scene_toks: set[str], cand_toks: set[str]) -> list[str]:
    """Return concrete overlapping concepts (exact match or shared >=5-char stem)."""
    matched: list[str] = []
    for st in sorted(scene_toks):
        if st in cand_toks:
            matched.append(st)
            continue
        if len(st) >= 5:
            stem = st[:5]
            if any(ct.startswith(stem) or st.startswith(ct[:5]) for ct in cand_toks if len(ct) >= 5):
                matched.append(st)
    return matched


def score_scene_visual_relevance(
    candidate: dict[str, Any],
    scene: SceneItem,
    project: MusicStoryProject,
) -> tuple[float, list[str]]:
    """
    Score strictly whether a candidate image (`url` + `title` + `source`) depicts what is SPOKEN in `scene.narration_text`.
    Generic franchise/editorial words ('GTA 6', 'Rockstar', 'release', 'details', 'revelation') are stripped so
    unrelated photos/ads are REJECTED (score <= 0.0).
    """
    u = str(candidate.get("url") or candidate.get("thumb_url") or "")
    title = str(candidate.get("title") or "")
    ctx = str(candidate.get("context") or "")
    src = str(candidate.get("source") or "")
    if _BAD_IMAGE_URL_RE.search(u):
        return -1.0, []
    low_meta = f"{title} {ctx}".lower()
    if any(b in low_meta for b in ("black friday", "cupom", "oferta", "promoção", "desconto", "newsletter", "subscribe", "cookie")):
        return -1.0, []

    narr_pt = (scene.narration_text or "").strip()
    narr_en = translate_to_en(narr_pt)
    scene_toks = _semantic_tokens(f"{narr_pt} {narr_en}")
    if not scene_toks:
        return 0.0, []

    url_slug = re.sub(r"\.[a-z0-9]{2,5}(?:\?.*)?$", "", u.rsplit("/", 1)[-1]).replace("-", " ").replace("_", " ")
    cand_toks = _semantic_tokens(f"{title} {ctx} {url_slug}")
    matched = _token_match_list(scene_toks, cand_toks)
    if not matched:
        return 0.0, []

    score = float(len(matched)) * 2.0
    if "Matéria" in src:
        score += 1.0
    if len(matched) >= 2:
        score += 1.5
    return round(score, 2), matched


_CONCRETE_VISUAL_HINT_RE = re.compile(
    r"\b(?:cerveja|beer|donut|donuts|rosquinha|revista|magazine|edge|ps5|playstation|xbox|pc|console|"
    r"preço|price|dólar|bilhão|mapa|map|leonida|vice\s+city|miami|ilha|island|pântano|swamp|praia|beach|"
    r"carro|car|moto|motorcycle|barco|boat|avião|plane|polícia|police|assalto|heist|robbery|prisão|prison|"
    r"jason|lucia|personagem|character|protagonista|gráfico|graphics|ray\s*tracing|fps|engine|vaza|leak|"
    r"celular|smartphone|calendário|calendar|novembro|november|adiamento|delay|falso|deepfake|hologram|"
    r"jacaré|crocodilo|alligator|crocodile|dinheiro|money|arma|weapon|helicopter|helicóptero|hotel|club)\b",
    re.IGNORECASE,
)


def _needs_visual_overlay(scene: SceneItem) -> bool:
    """
    Decide whether the spoken line warrants an illustrative overlay (real photo or bespoke AI visual):
    - True when `scene.narration_text` (PT or EN translation) explicitly mentions concrete visual subjects
      (e.g., beer, donut, Edge magazine, PS5, Xbox, Jason, Lucia, price, map, police, car).
    - False for pure rhetorical hooks, transitions, or CTAs -> keep clean moving video background!
    """
    narr_pt = (scene.narration_text or "").strip()
    low = narr_pt.lower()
    if any(k in low for k in ("comenta aqui", "me conta aqui", "o que você acha", "se inscreve", "deixa o like", "opinião")):
        return False
    narr_en = translate_to_en(narr_pt)
    if _CONCRETE_VISUAL_HINT_RE.search(f"{narr_pt} {narr_en}"):
        return True
    return False


def _score_text_overlap(text_a: str, text_b: str) -> float:
    toks_a = _semantic_tokens(text_a)
    toks_b = _semantic_tokens(text_b)
    if not toks_a or not toks_b:
        return 0.0
    matched = _token_match_list(toks_a, toks_b)
    return float(len(matched)) / float(max(1, min(len(toks_a), 6)))


def search_bing_images(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Search Bing Images (large photos) for recent gaming/news visuals without paid APIs."""
    q_clean = re.sub(r"\s+", " ", (query or "").strip())[:120]
    if not q_clean:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True, headers=_BING_HEADERS) as client:
            resp = client.get(
                "https://www.bing.com/images/search",
                params={"q": q_clean, "form": "HDRSC3", "first": "1", "qft": "+filterui:imagesize-large"},
            )
            if resp.status_code != 200:
                return out
            for m in re.finditer(r'class="iusc"[^>]*m="([^"]+)"', resp.text):
                raw_m = _html.unescape(m.group(1))
                try:
                    meta = json.loads(raw_m)
                except Exception as exc:
                    logger.debug("Ignorando metadado de imagem inválido no Bing: %s", exc)
                    continue
                murl = str(meta.get("murl") or "").strip()
                turl = str(meta.get("turl") or murl).strip()
                desc = re.sub(r"<[^>]+>", "", str(meta.get("desc") or meta.get("t") or q_clean)).strip()
                if not murl.startswith(("http://", "https://")) or murl in seen:
                    continue
                low_u = murl.lower()
                if any(bad in low_u for bad in _BAD_STOCK_DOMAINS) or _BAD_IMAGE_URL_RE.search(murl):
                    continue
                if any(b in desc.lower() for b in ("black friday", "promoção", "cupom", "oferta", "desconto")):
                    continue
                seen.add(murl)
                out.append(
                    {
                        "url": murl,
                        "thumb_url": turl,
                        "title": desc[:140] or q_clean,
                        "source": "Bing Imagens",
                        "kind": "image",
                    }
                )
                if len(out) >= limit:
                    break
    except Exception as exc:
        logger.debug("Falha ao buscar imagens relacionadas: %s", exc)
    return out


def search_youtube_videos_ytdlp(query: str, limit: int = 4) -> list[dict[str, Any]]:
    """Search YouTube via yt-dlp in-process without downloading, returning ranked landscape/editorial candidates."""
    q_clean = re.sub(r"\s+", " ", (query or "").strip())[:110]
    if not q_clean:
        return []
    try:
        import yt_dlp
    except Exception:
        return []

    out: list[dict[str, Any]] = []
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "ignoreerrors": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max(3, min(8, limit + 2))}:{q_clean}", download=False)
            entries = (info or {}).get("entries") or []
            for e in entries:
                if not isinstance(e, dict):
                    continue
                vid_id = str(e.get("id") or "").strip()
                if not vid_id:
                    continue
                title = str(e.get("title") or "").strip()
                dur = float(e.get("duration") or 0.0)
                channel = str(e.get("channel") or e.get("uploader") or "").strip()
                low_t = f"{title} {channel}".lower()
                # Skip live streams, multi-hour podcasts, and noisy reaction clickbait
                if dur and (dur < 12.0 or dur > 1200.0):
                    continue
                if any(bad in low_t for bad in ("react", "reaction", "live stream", "podcast", "ao vivo", "#shorts", "shorts")):
                    continue
                score = _score_text_overlap(q_clean, title) * 10.0
                if any(good in low_t for good in ("official", "trailer", "gameplay", "showcase", "4k", "comparison", "reveal", "rockstar", "playstation", "xbox", "ign", "digital foundry", "no commentary", "cinematic")):
                    score += 5.0
                if any(chatter in low_t for chatter in ("opinião", "opiniao", "análise de youtuber", "meu canal", "explicando", "reagindo", "podcast", "bate papo", "live")):
                    score -= 4.0
                if 20.0 <= dur <= 240.0:
                    score += 2.0
                url = str(e.get("url") or f"https://www.youtube.com/watch?v={vid_id}")
                if not url.startswith("http"):
                    url = f"https://www.youtube.com/watch?v={vid_id}"
                out.append(
                    {
                        "kind": "news_video",
                        "video_id": vid_id,
                        "video_url": url,
                        "url": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                        "thumb_url": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                        "title": title[:120] or q_clean,
                        "source": f"YouTube • {channel}" if channel else "YouTube",
                        "duration_sec": round(dur, 1),
                        "recommended_start_sec": 6.0 if dur > 22.0 else 1.5,
                        "score": round(score, 2),
                    }
                )
    except Exception:
        return []
    out.sort(key=lambda x: x.get("score", 0.0), reverse=True)
    return out[:limit]


def _pick_youtube_chapter_or_offset(
    video_url: str,
    scene_hint: str,
    default_start: float = 6.0,
    used_starts: list[float] | None = None,
    duration_sec: float = 9.0,
) -> tuple[float, float]:
    """
    Inspect YouTube metadata (duration + chapters) without downloading video bytes,
    picking a chapter matching `scene_hint` or a distinct non-overlapping timestamp.
    """
    used = used_starts or []
    dur = 0.0
    chapters: list[dict[str, Any]] = []
    try:
        import yt_dlp

        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(video_url, download=False)
            if isinstance(info, dict):
                dur = float(info.get("duration") or 0.0)
                chapters = list(info.get("chapters") or [])
    except Exception as exc:
        logger.debug("Falha ao obter info de capítulos do vídeo %s: %s", video_url, exc)

    chosen = float(default_start)
    if chapters and scene_hint.strip():
        best_score = 0.0
        for ch in chapters:
            ch_title = str(ch.get("title") or "")
            st = float(ch.get("start_time") or 0.0)
            sc = _score_text_overlap(scene_hint, ch_title)
            if sc > best_score and all(abs(st - u) >= 7.0 for u in used):
                best_score = sc
                chosen = max(2.0, st + 1.0)

    if any(abs(chosen - u) < 7.0 for u in used):
        max_start = max(6.0, (dur - duration_sec - 2.0) if dur > (duration_sec + 8.0) else 60.0)
        cand_step = 10.0 if dur > 45.0 else 7.0
        t = 5.0
        while t <= max_start:
            if all(abs(t - u) >= 7.0 for u in used):
                chosen = t
                break
            t += cand_step

    if dur > 0.0:
        chosen = max(1.0, min(chosen, max(1.0, dur - duration_sec - 1.0)))
    return round(chosen, 1), round(dur, 1)


def download_news_video_segment(
    video_spec: dict[str, Any],
    out_mp4: Path,
    start_sec: float | None = None,
    duration_sec: float = 9.0,
) -> Path | None:
    """
    Download only the needed segment (~9s) of a news-embedded or topic-matched YouTube/MP4 video
    into `out_mp4` using yt-dlp + imageio-ffmpeg.
    """
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if out_mp4.exists() and out_mp4.stat().st_size > 40_000:
        try:
            if get_audio_duration(out_mp4) >= 2.0:
                return out_mp4
        except Exception as exc:
            logger.debug("Falha ao verificar duração do vídeo existente %s: %s", out_mp4, exc)

    ffmpeg_exe = get_ffmpeg_exe()
    kind = str(video_spec.get("kind") or "youtube").lower()
    vurl = str(video_spec.get("video_url") or video_spec.get("url") or "").strip()
    if not vurl.startswith(("http://", "https://")):
        return None

    st = float(start_sec if start_sec is not None else video_spec.get("recommended_start_sec") or 6.0)
    st = max(0.5, st)
    ed = st + max(4.0, min(14.0, float(duration_sec or 9.0)))

    if kind == "direct_video":
        tmp_raw = out_mp4.with_suffix(".dl.mp4")
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True, headers=_BING_HEADERS) as client:
                r = client.get(vurl)
                if r.status_code == 200 and len(r.content) > 50_000:
                    tmp_raw.write_bytes(r.content)
                    cmd = [
                        ffmpeg_exe, "-y",
                        "-ss", f"{st:.2f}",
                        "-i", str(tmp_raw),
                        "-t", f"{ed - st:.2f}",
                        "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                        str(out_mp4),
                    ]
                    subprocess.run(cmd, capture_output=True, timeout=30)
        except Exception as exc:
            logger.warning("Falha ao extrair clipe direto com ffmpeg: %s", exc)
        finally:
            tmp_raw.unlink(missing_ok=True)
        if out_mp4.exists() and out_mp4.stat().st_size > 30_000:
            try:
                _register_downloaded_clip_in_catalog(out_mp4, vurl, video_spec)
            except Exception as exc:
                logger.warning("Falha ao registrar clipe baixado no catálogo: %s", exc)
            return out_mp4
        return None

    ytdlp_found = shutil.which("yt-dlp")
    if not ytdlp_found:
        venv_candidate = Path(sys.executable).parent / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp")
        if venv_candidate.exists():
            ytdlp_found = str(venv_candidate)
    cmd_base = [ytdlp_found] if ytdlp_found else [sys.executable, "-m", "yt_dlp"]
    out_tpl = str(out_mp4.with_suffix(".%(ext)s"))
    for p in out_mp4.parent.glob(f"{out_mp4.stem}.*"):
        if p != out_mp4:
            p.unlink(missing_ok=True)

    cmd = [
        *cmd_base,
        "--no-warnings",
        "--no-playlist",
        "--ffmpeg-location", ffmpeg_exe,
        "-f", "bv*[height<=720][ext=mp4]/b[height<=720][ext=mp4]/bv*[height<=1080][ext=mp4]/b[height<=1080]/bv*/b",
        "--download-sections", f"*{st:.1f}-{ed:.1f}",
        "--force-keyframes-at-cuts",
        "--merge-output-format", "mp4",
        "-o", out_tpl,
        vurl,
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=42)
        if out_mp4.exists() and out_mp4.stat().st_size > 30_000 and get_audio_duration(out_mp4) >= 1.8:
            try:
                _register_downloaded_clip_in_catalog(out_mp4, vurl, video_spec)
            except Exception as exc:
                logger.warning("Falha ao registrar clipe baixado no catálogo: %s", exc)
            return out_mp4
    except Exception as exc:
        logger.warning("Falha ao executar download com yt-dlp: %s", exc)
    return None


def _gaming_queries_for_scene(scene: SceneItem, project: MusicStoryProject, custom_query: str = "") -> tuple[list[str], list[str]]:
    """
    Build separate high-precision (image_queries, video_queries) directly tied to the
    exact game + scene narration, eliminating unrelated generic/stock terms.
    """
    if custom_query.strip():
        q = custom_query.strip()
        return [q], [q]

    game = (project.artist or "GTA 6").strip()
    img_qs: list[str] = []
    vid_qs: list[str] = []

    if scene.search_query and scene.search_query.strip():
        img_qs.append(scene.search_query.strip())
    if scene.video_query and scene.video_query.strip():
        vid_qs.append(scene.video_query.strip())

    txt = f"{scene.title} {scene.narration_text}".lower()
    if any(k in txt for k in ("ps5 pro", "playstation 5 pro")):
        img_qs.append(f"{game} PS5 Pro graphics screenshot")
        vid_qs.append(f"{game} PS5 Pro showcase")
    elif any(k in txt for k in ("ps5", "playstation", "dualsense")):
        img_qs.append(f"{game} PlayStation 5 console")
        vid_qs.append(f"{game} PS5 gameplay trailer")
    if any(k in txt for k in ("xbox", "cloud", "game pass", "series")):
        img_qs.append(f"{game} Xbox Series X")
        vid_qs.append(f"{game} Xbox gameplay")
    if any(k in txt for k in ("pc", "rtx", "dlss", "steam", "placa")):
        img_qs.append(f"{game} PC release screenshot")
        vid_qs.append(f"{game} PC graphics comparison")
    if any(k in txt for k in ("preço", "preco", "dólar", "dolar", "r$", "pré-venda", "pre-venda", "edição", "edicao", "física", "fisica", "capa")):
        img_qs.append(f"{game} pre order edition box art")
        vid_qs.append(f"{game} official announcement trailer")
    if any(k in txt for k in ("lucia", "jason", "prisão", "prisao", "personagem", "protagonista")):
        img_qs.append(f"{game} Lucia Jason official screenshot")
        vid_qs.append(f"{game} Lucia Jason trailer scenes")
    if any(k in txt for k in ("mapa", "leonida", "vice city", "vaza", "vazamento")):
        img_qs.append(f"{game} Leonida Vice City map")
        vid_qs.append(f"{game} map locations breakdown")
    if any(k in txt for k in ("trailer 2", "segundo trailer", "trailer")):
        img_qs.append(f"{game} Trailer 2 screenshot")
        vid_qs.append(f"{game} official trailer 2")

    title_short = re.sub(r"\s+", " ", (project.song_title or "").strip())[:70]
    if title_short:
        img_qs.append(f"{game} {title_short}")
        vid_qs.append(f"{game} {title_short}")
    img_qs.append(f"{game} official artwork screenshot")
    vid_qs.append(f"{game} official trailer")

    def _dedup(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for it in items:
            cl = re.sub(r"\s+", " ", it.strip())
            if cl and cl.lower() not in seen:
                seen.add(cl.lower())
                out.append(cl)
        return out[:4]

    return _dedup(img_qs), _dedup(vid_qs)


def _ensure_news_media_manifest(project: MusicStoryProject, force_refresh: bool = False) -> dict[str, Any]:
    """Fetch & cache article images_meta + embedded videos onto `project.news_media_manifest`."""
    manifest = dict(getattr(project, "news_media_manifest", None) or {})
    if not force_refresh and (manifest.get("article_images_meta") or manifest.get("article_videos")):
        return manifest
    hint = f"{project.artist} {project.song_title}".strip()
    ctx = fetch_article_context_and_images(getattr(project, "topic_source_url", "") or "", title_hint=hint)
    manifest = {
        "resolved_url": ctx.get("final_url") or getattr(project, "topic_source_url", ""),
        "article_images_meta": ctx.get("images_meta") or [{"url": u, "alt": "", "context": ""} for u in (ctx.get("images") or [])],
        "article_videos": ctx.get("videos") or [],
    }
    project.news_media_manifest = manifest
    return manifest


def search_gaming_scene_candidates(
    scene: SceneItem,
    project: MusicStoryProject,
    custom_query: str = "",
    limit: int = 10,
    include_local_clips: bool = True,
) -> list[dict[str, Any]]:
    """
    Collect real, topic-coherent candidates for a gaming Short scene:
    1) Videos embedded/mentioned in the news article + targeted YouTube results (`kind="news_video"`)
    2) Editorial images from the news article ranked by semantic overlap with this scene
    3) Large relevant images from Bing Images (`source="Bing Imagens"`)
    4) User-explicitly attached project clips (`project.band_video_clips`) if any
    5) Wikipedia/Wikimedia only as rare last resort if <2 images found
    """
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(item: dict[str, Any]) -> None:
        key = (
            str(item.get("video_id") or "")
            or str(item.get("video_url") or "")
            or str(item.get("url") or "")
            or (str(item.get("clip_path", "")) + str(item.get("video_start_sec", "")))
        ).strip()
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append(item)

    img_queries, vid_queries = _gaming_queries_for_scene(scene, project, custom_query=custom_query)
    scene_blob = f"{scene.title} {scene.narration_text} {scene.search_query} {scene.video_query}"

    # 1. Videos embedded in the source article + targeted YouTube search for this scene
    if not custom_query.strip():
        manifest = _ensure_news_media_manifest(project, force_refresh=False)
        art_videos = list(manifest.get("article_videos") or [])
        scored_art_vids = sorted(
            art_videos,
            key=lambda v: _score_text_overlap(scene_blob, f"{v.get('context', '')} {v.get('title', '')}"),
            reverse=True,
        )
        for idx_v, v in enumerate(scored_art_vids[:3]):
            vid_id = str(v.get("video_id") or "")
            vurl = str(v.get("url") or "")
            _add(
                {
                    "kind": "news_video",
                    "video_id": vid_id,
                    "video_url": vurl,
                    "url": str(v.get("thumb_url") or (f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg" if vid_id else "")),
                    "thumb_url": str(v.get("thumb_url") or (f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg" if vid_id else "")),
                    "title": f"Vídeo da Matéria #{idx_v + 1}: {str(v.get('context') or project.song_title)[:75]}",
                    "source": "Vídeo da Notícia",
                    "direct_video": str(v.get("kind") or "youtube") == "direct_video",
                    "recommended_start_sec": 6.0 + (scene.index - 1) * 7.0,
                }
            )

    for yt_v in search_youtube_videos_ytdlp(vid_queries[0], limit=3):
        _add(yt_v)

    # 2. Images directly extracted from the news article, filtered & ranked strictly by scene semantic relevance
    if not custom_query.strip():
        manifest = _ensure_news_media_manifest(project, force_refresh=False)
        art_imgs = list(manifest.get("article_images_meta") or [])
        ranked_art: list[tuple[float, dict[str, Any]]] = []
        for idx_i, im_m in enumerate(art_imgs):
            u = str(im_m.get("url") or "").strip()
            if not u or _BAD_IMAGE_URL_RE.search(u):
                continue
            label_alt = str(im_m.get("alt") or "").strip()
            cand_obj: dict[str, Any] = {
                "url": u,
                "thumb_url": u,
                "title": label_alt[:90] if label_alt else f"Imagem da Notícia #{idx_i + 1}",
                "context": str(im_m.get("context") or "")[:120],
                "source": "Matéria / Fonte",
                "kind": "image",
            }
            rel_score, matched = score_scene_visual_relevance(cand_obj, scene, project)
            if rel_score < 0.0:
                continue
            cand_obj["_relevance_score"] = rel_score
            cand_obj["_matched_terms"] = matched
            ranked_art.append((rel_score, cand_obj))
        ranked_art.sort(key=lambda x: x[0], reverse=True)
        for _, cand_obj in ranked_art:
            _add(cand_obj)

    # 3. High-relevance Bing Images specific to what is SPOKEN in this scene
    narr_en = translate_to_en(f"{scene.title}. {scene.narration_text}")
    spoken_q = re.sub(r"[^0-9a-zA-Z\s]+", " ", narr_en)
    spoken_words = [w for w in spoken_q.split() if w.lower() not in _GENERIC_GAMING_STOP and len(w) >= 3][:5]
    if spoken_words:
        spoken_bing_q = f"{project.artist or 'GTA 6'} {' '.join(spoken_words)}"
        if spoken_bing_q.lower() not in {q.lower() for q in img_queries}:
            img_queries = [spoken_bing_q] + img_queries

    for q in img_queries[:2]:
        for bitem in search_bing_images(q, limit=6):
            rel_score, matched = score_scene_visual_relevance(bitem, scene, project)
            if rel_score < 0.0:
                continue
            # Slight bonus when Bing explicitly found it from a spoken-topic query with keyword hit
            bitem["_relevance_score"] = rel_score
            bitem["_matched_terms"] = matched
            _add(bitem)
        if sum(1 for c in candidates if c.get("kind") == "image" and float(c.get("_relevance_score", 0)) >= 1.5) >= 4:
            break

    # 4. Only clips EXPLICITLY attached to this project by the user (`project.band_video_clips`)
    if include_local_clips and not custom_query.strip() and project.band_video_clips:
        for vp_str in (project.band_video_clips or [])[:4]:
            vp = Path(vp_str)
            if not vp.exists():
                continue
            for card in index_trailer_scenes(vp, max_clips=6):
                if (int(card["index"]) + scene.index) % 2 == 0 or len(project.band_video_clips) == 1:
                    _add(
                        {
                            "url": card["thumb_url"],
                            "thumb_url": card["thumb_url"],
                            "title": f"Anexo: {card['label']}",
                            "source": "Clipe Anexado",
                            "kind": "band_clip",
                            "clip_path": card["clip_path"],
                            "video_start_sec": card["start_sec"],
                        }
                    )
                    break

    # 5. Rare Wikipedia/Wikimedia fallback ONLY if Bing + Article gave fewer than 2 images
    img_count = sum(1 for c in candidates if c.get("kind") == "image")
    if img_count < 2:
        for witem in _search_wikipedia_topic_media(img_queries[:1], max_candidates=3):
            _add(
                {
                    "url": witem["url"],
                    "thumb_url": witem.get("thumb_url") or witem["url"],
                    "title": witem.get("title") or img_queries[0],
                    "source": witem.get("source") or "Wikipedia",
                    "kind": "image",
                }
            )

    return candidates[:limit]


def _collect_interleaved_cuts(project: MusicStoryProject, max_clips_per_video: int = 10) -> list[dict[str, Any]]:
    """
    Gather indexed cuts ONLY from clips explicitly attached to the project (`project.band_video_clips`),
    never blindly pulling unrequested local library trailers into unrelated news.
    """
    attached: list[Path] = [Path(p) for p in (project.band_video_clips or []) if p and Path(p).exists()]
    if not attached:
        return []

    publish_intent = getattr(project, "publish_intent", "commercial") or "commercial"
    filtered_attached: list[Path] = []
    for p in attached:
        entry = get_media_catalog_entry(p.name)
        allowed, reason = media_allowed(entry, publish_intent)
        if not allowed:
            logger.warning(
                "Clipe anexado '%s' rejeitado para finalidade '%s': %s.",
                p.name,
                publish_intent,
                reason,
            )
            continue
        filtered_attached.append(p)
    attached = filtered_attached
    if not attached:
        return []

    per_file_cuts: list[list[dict[str, Any]]] = []
    for vp in attached[:6]:
        cuts = index_trailer_scenes(vp, max_clips=max_clips_per_video)
        if cuts:
            action_cuts = (
                [c for c in cuts if float(c.get("start_sec") or 0.0) >= 4.5]
                or [c for c in cuts if float(c.get("start_sec") or 0.0) >= 2.0]
                or cuts
            )
            per_file_cuts.append(action_cuts)

    if not per_file_cuts:
        return []

    interleaved: list[dict[str, Any]] = []
    max_len = max(len(c) for c in per_file_cuts)
    for step in range(max_len):
        for cuts in per_file_cuts:
            if step < len(cuts):
                interleaved.append(cuts[step])
    return interleaved


def _rate_cut_visual_quality(thumb_path_str: str) -> float:
    """Score a trailer cut's dynamism based on brightness & edge/detail energy, penalizing black/flat frames."""
    if not thumb_path_str:
        return 0.0
    tp = Path(thumb_path_str)
    if not tp.exists():
        return 0.0
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(tp).convert("RGB")
        arr = np.array(im)
        mean_b = arr.mean()
        if mean_b < 18 or mean_b > 248:
            return 0.0
        gx = np.abs(np.diff(arr.astype(float), axis=1)).mean()
        gy = np.abs(np.diff(arr.astype(float), axis=0)).mean()
        return round(float(gx + gy), 2)
    except Exception:
        return 0.0


def _collect_library_trailer_cuts(project: MusicStoryProject, max_clips_per_video: int = 8) -> list[dict[str, Any]]:
    """
    Backfill moving background cuts from `inputs/gta6/` trailers/gameplays when
    attached clips or news/YouTube downloads aren't available, ranking by visual dynamism
    and preferring action moments (discarding black screens, logos and static intros).
    """
    blob = f"{project.artist} {project.song_title} {project.genre}".lower()
    is_gta_topic = any(k in blob for k in ("gta", "grand theft", "rockstar", "leonida", "vice city", "jason", "lucia"))
    if not is_gta_topic or not GTA6_INPUTS_DIR.exists():
        return []

    publish_intent = getattr(project, "publish_intent", "commercial") or "commercial"
    per_file_cuts: list[list[dict[str, Any]]] = []
    for p in sorted(GTA6_INPUTS_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        entry = get_media_catalog_entry(p.name)
        allowed, reason = media_allowed(entry, publish_intent)
        if not allowed:
            logger.info(
                "Omitindo clipe de biblioteca '%s' para finalidade '%s' (%s).",
                p.name,
                publish_intent,
                reason,
            )
            continue
        cuts = index_trailer_scenes(p, max_clips=max_clips_per_video)
        if not cuts:
            continue
        action_cuts = [c for c in cuts if float(c.get("start_sec") or 0.0) >= 4.5]
        if not action_cuts:
            action_cuts = [c for c in cuts if float(c.get("start_sec") or 0.0) >= 2.0] or cuts

        # Sort cuts within each trailer file by visual vibrancy/action score
        action_cuts.sort(key=lambda c: _rate_cut_visual_quality(str(c.get("thumb_path") or "")), reverse=True)
        per_file_cuts.append(action_cuts)

    if not per_file_cuts:
        return []

    interleaved: list[dict[str, Any]] = []
    max_len = max(len(c) for c in per_file_cuts)
    for step in range(max_len):
        for cuts in per_file_cuts:
            if step < len(cuts):
                interleaved.append(cuts[step])
    return interleaved


_THEME_KEYWORDS: dict[str, list[str]] = {
    "policia": ["polícia", "policia", "k9", "procurado", "tiro", "combate", "perseguição", "perseguicao", "assalto", "crime", "fuga", "sirene", "viatura"],
    "lucia_jason": ["lucia", "jason", "casal", "protagonista", "personagem", "prisão", "prisao", "dupla", "loja", "protagonistas"],
    "mapa_mundo": ["mapa", "leonida", "vice city", "cidade", "mundo aberto", "locais", "região", "praia", "rua", "metrópole", "highway"],
    "agua_pantano": ["água", "agua", "física", "fisica", "barco", "lancha", "pântano", "pantano", "animais", "natureza", "jacaré", "mar"],
    "vida_noturna": ["boate", "festa", "música", "vida noturna", "interiores", "luxo", "cobertura", "balada", "clube"],
    "npcs_multidao": ["npcs", "multidão", "multidao", "banhistas", "vida", "densidade", "inteligência artificial", "ia"],
    "tecnologia_graficos": ["60 fps", "fps", "gráficos", "graficos", "ps5", "xbox", "pc", "tecnologia", "rage", "engine", "console", "iluminação"],
    "oficial_anuncio": ["oficial", "confirmado", "rockstar", "take-two", "data", "lançamento", "lancamento", "trailer", "anúncio", "revelação", "capa", "edição", "preço", "custo"],
}


def _infer_trailer_cut_tags(cut: dict[str, Any]) -> set[str]:
    name = str(cut.get("clip_name") or cut.get("clip_path") or "").lower()
    label = str(cut.get("label") or "").lower()
    start_sec = float(cut.get("start_sec") or 0.0)
    tags: set[str] = set()

    if "trailer 1" in name:
        if start_sec < 5.0:
            tags.update(["lucia_jason", "prisao"])
        elif 5.0 <= start_sec < 10.0:
            tags.update(["mapa_mundo"])
        elif 10.0 <= start_sec < 16.0:
            tags.update(["agua_pantano"])
        elif 16.0 <= start_sec < 20.0:
            tags.update(["npcs_multidao", "tecnologia_graficos"])
        elif 20.0 <= start_sec < 25.0:
            tags.update(["policia"])
        elif 25.0 <= start_sec < 32.0:
            tags.update(["vida_noturna"])
        else:
            tags.update(["lucia_jason"])
    elif "revelacao" in name or "capa" in name:
        tags.update(["oficial_anuncio", "tecnologia_graficos"])
    elif "realista" in name:
        tags.update(["tecnologia_graficos", "agua_pantano"])
    elif "trailer 2" in name or "lançamento" in name or "lancamento" in name:
        if start_sec < 12.0:
            tags.update(["oficial_anuncio", "mapa_mundo"])
        elif 12.0 <= start_sec < 24.0:
            tags.update(["policia"])
        else:
            tags.update(["lucia_jason"])

    for th, kws in _THEME_KEYWORDS.items():
        if any(kw in label for kw in kws):
            tags.add(th)

    return tags


def match_best_trailer_cut(
    scene: SceneItem,
    available_cuts: list[dict[str, Any]],
    used_keys: set[str] | None = None,
    max_continuous_sec: float = 6.0,
) -> tuple[dict[str, Any] | None, str]:
    """
    Select the most semantically relevant, transformative trailer cut for a scene (Tarefa 1.2),
    enforcing max continuous clip duration <= max_continuous_sec and generating a detailed reason.
    """
    if not available_cuts:
        return None, ""

    used_keys = used_keys or set()
    scene_text = f"{scene.title} {scene.narration_text}".lower()

    active_themes: list[str] = []
    for theme, keywords in _THEME_KEYWORDS.items():
        if any(kw in scene_text for kw in keywords):
            active_themes.append(theme)

    scored_cuts: list[tuple[float, dict[str, Any], list[str]]] = []
    for cut in available_cuts:
        ckey = f"{cut.get('clip_name') or cut.get('clip_path')}@{cut.get('start_sec')}"
        cut_tags = _infer_trailer_cut_tags(cut)
        matched = [t for t in active_themes if t in cut_tags]

        base_score = _rate_cut_visual_quality(str(cut.get("thumb_path") or ""))
        score = base_score + len(matched) * 15.0
        if ckey in used_keys:
            score -= 30.0
        scored_cuts.append((score, cut, matched))

    scored_cuts.sort(key=lambda x: x[0], reverse=True)
    best_score, raw_cut, matched_themes = scored_cuts[0]

    scene_dur = float(getattr(scene, "duration_sec", 0.0) or 5.0)
    cut_dur = min(scene_dur, max_continuous_sec)
    start_sec = float(raw_cut.get("start_sec") or 0.0)
    end_sec = round(start_sec + cut_dur, 2)

    res_cut = dict(raw_cut)
    res_cut["end_sec"] = end_sec
    res_cut["duration_sec"] = cut_dur

    clip_name = raw_cut.get("clip_name") or Path(str(raw_cut.get("clip_path") or "")).name or "trailer"
    if matched_themes:
        theme_names = {
            "policia": "perseguição policial",
            "lucia_jason": "protagonistas Lucia e Jason",
            "mapa_mundo": "mundo aberto e cidade de Vice City",
            "agua_pantano": "física de água e áreas naturais",
            "vida_noturna": "vida noturna e interiores",
            "npcs_multidao": "densidade de NPCs e atmosfera urbana",
            "tecnologia_graficos": "capacidade gráfica da engine",
            "oficial_anuncio": "materiais de divulgação oficial",
        }
        human_themes = [theme_names.get(t, t) for t in matched_themes]
        reason = (
            f"Corte do trailer '{clip_name}' ({start_sec:.1f}s a {end_sec:.1f}s) "
            f"selecionado por correspondência temática com '{', '.join(human_themes)}' "
            f"na narração da cena."
        )
    else:
        reason = (
            f"Corte dinâmico do trailer '{clip_name}' ({start_sec:.1f}s a {end_sec:.1f}s) "
            f"selecionado para ritmo visual e contextualização da cena."
        )

    return res_cut, reason


def _short_overlay_badge_from_scene(scene: SceneItem) -> str:
    raw = re.sub(r"[^0-9A-Za-zÀ-ÿ\s•:/+\-$%]+", " ", (scene.title or "").strip())
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw or len(raw) < 4:
        return "RADAR NOTÍCIA"
    return raw[:28].upper()


def auto_blend_gaming_clips_and_images(
    project: MusicStoryProject,
    work_dir: Path,
    force_reassign: bool = False,
    include_hook_scene: bool = True,
    auto_download_news_videos: bool = False,
) -> dict[str, Any]:
    """
    Smart moving-video-first + strict spoken-script semantic composition for gaming Shorts:
    1) Prioritizes MOVING VIDEO in the background across ALL scenes (including Hook Scene 1):
       attached clips -> downloaded article/YouTube video segments -> local GTA 6 library trailer cuts.
    2) Evaluates candidate foreground images strictly against WHAT IS SPOKEN in `scene.narration_text`
       + `translate_to_en`. Off-topic photos/ads are rejected.
       If no real photo matches the spoken script:
       - Generates a bespoke AI visual when the spoken line describes concrete subjects; OR
       - Leaves the overlay EMPTY (`overlay_image_path = None`, `none_motion_only`) for clean video motion.
    """
    scenes_dir = work_dir / "scenes"
    clips_dir = work_dir / "news_media" / "clips"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    _ensure_news_media_manifest(project, force_refresh=force_reassign)
    interleaved_attached = _collect_interleaved_cuts(project)
    interleaved_library = _collect_library_trailer_cuts(project)

    seen_bg_urls: set[str] = set()
    seen_ov_urls: set[str] = set()
    used_attached_keys: set[str] = set()
    used_library_keys: set[str] = set()
    used_video_starts: dict[str, list[float]] = {}
    assigned_clips = 0
    assigned_overlays = 0
    downloaded_news_clips = 0
    ai_generated_overlays = 0
    clean_motion_only = 0

    blend_cycle = ["pip_card", "split_top_image", "broll_cutaway"]

    for idx, scene in enumerate(project.scenes):
        out_png = scenes_dir / f"scene_{scene.index:02d}.png"
        out_ov_png = scenes_dir / f"scene_{scene.index:02d}_overlay.png"

        # Refresh candidates if missing or forced
        if force_reassign or not scene.candidate_items:
            cands = search_gaming_scene_candidates(scene, project, limit=10)
            if cands:
                scene.candidate_items = cands
                scene.candidate_urls = [str(c.get("url") or "") for c in cands[:6] if c.get("url")]

        cands = scene.candidate_items or []
        vid_cands = [c for c in cands if c.get("kind") == "news_video" and (c.get("video_url") or c.get("url"))]
        img_cands = [c for c in cands if c.get("kind") == "image" and str(c.get("url", "")).startswith("http")]

        is_hook_html_protected = (
            idx == 0
            and not include_hook_scene
            and bool(project.html_hook_card)
            and scene.asset_type != "video"
        )

        has_custom_vid = bool(scene.video_path and Path(scene.video_path).exists())
        has_custom_img = bool(scene.image_path and Path(scene.image_path).exists() and scene.asset_type == "image")

        should_place_clip = (
            not is_hook_html_protected
            and (force_reassign or not has_custom_vid)
            and (force_reassign or not has_custom_img or include_hook_scene)
        )

        if should_place_clip:
            picked_clip_path: Path | None = None
            picked_start_sec = 0.0

            # Priority A: User explicitly attached project clips (`band_video_clips`)
            if interleaved_attached:
                picked_cut = None
                for offset in range(len(interleaved_attached)):
                    cand_cut = interleaved_attached[(idx + offset) % len(interleaved_attached)]
                    ckey = f"{cand_cut['clip_path']}@{cand_cut['start_sec']}"
                    if ckey not in used_attached_keys:
                        picked_cut = cand_cut
                        used_attached_keys.add(ckey)
                        break
                if picked_cut is None:
                    picked_cut = interleaved_attached[idx % len(interleaved_attached)]
                cp = Path(str(picked_cut["clip_path"]))
                if cp.exists():
                    picked_clip_path = cp
                    picked_start_sec = float(picked_cut["start_sec"])

            # Priority B: Download topic-coherent video segment from article embeds or targeted YouTube search
            if picked_clip_path is None and auto_download_news_videos and vid_cands:
                scene_hint = f"{scene.title} {scene.narration_text} {scene.video_query}"
                for vc in vid_cands[:3]:
                    vurl = str(vc.get("video_url") or vc.get("url") or "")
                    vkey = str(vc.get("video_id") or hashlib.sha1(vurl.encode("utf-8", errors="ignore")).hexdigest()[:10])
                    if not vc.get("direct_video") and "youtube" in vurl.lower():
                        chosen_ts, _ = _pick_youtube_chapter_or_offset(
                            vurl,
                            scene_hint=scene_hint,
                            default_start=float(vc.get("recommended_start_sec") or 6.0),
                            used_starts=used_video_starts.get(vkey, []),
                            duration_sec=9.0,
                        )
                    else:
                        chosen_ts = 2.0 + len(used_video_starts.get(vkey, [])) * 8.0
                    dl_target = clips_dir / f"sc{scene.index:02d}_{vkey}_{int(chosen_ts)}.mp4"
                    dl_spec = dict(vc)
                    if vc.get("direct_video"):
                        dl_spec["kind"] = "direct_video"
                    res_mp4 = download_news_video_segment(dl_spec, dl_target, start_sec=chosen_ts, duration_sec=9.0)
                    if res_mp4 and res_mp4.exists():
                        used_video_starts.setdefault(vkey, []).append(chosen_ts)
                        picked_clip_path = res_mp4
                        picked_start_sec = 0.0
                        vc["local_clip_path"] = str(res_mp4)
                        downloaded_news_clips += 1
                        break

            # Priority C: Moving video backfill from inputs/gta6/ trailers so static images are avoided whenever possible
            max_cont_sec = float(getattr(project, "max_continuous_trailer_sec", 6.0) or 6.0)
            scene_dur = float(getattr(scene, "duration_sec", 0.0) or 5.0)

            if picked_clip_path is None and interleaved_library:
                matched_cut, reason = match_best_trailer_cut(
                    scene=scene,
                    available_cuts=interleaved_library,
                    used_keys=used_library_keys,
                    max_continuous_sec=max_cont_sec,
                )
                if matched_cut:
                    lp = Path(str(matched_cut["clip_path"]))
                    if lp.exists():
                        picked_clip_path = lp
                        picked_start_sec = float(matched_cut["start_sec"])
                        picked_end_sec = float(matched_cut.get("end_sec") or (picked_start_sec + min(scene_dur, max_cont_sec)))
                        used_library_keys.add(f"{matched_cut['clip_path']}@{matched_cut['start_sec']}")
                        scene.clip_reason = reason
                        scene.video_end_sec = picked_end_sec

            if picked_clip_path and picked_clip_path.exists():
                scene.video_path = str(picked_clip_path)
                scene.video_start_sec = float(picked_start_sec)
                if not getattr(scene, "video_end_sec", None):
                    scene.video_end_sec = round(picked_start_sec + min(scene_dur, max_cont_sec), 2)
                if not getattr(scene, "clip_reason", ""):
                    scene.clip_reason = f"Corte dinâmico de '{picked_clip_path.name}' ({scene.video_start_sec:.1f}s-{scene.video_end_sec:.1f}s) para a cena {scene.index}."
                scene.asset_type = "video"
                scene.html_path = None
                extract_video_frame_thumb(picked_clip_path, out_png, seek_sec=scene.video_start_sec, vertical_9_16=True)
                if out_png.exists():
                    scene.image_path = str(out_png)
                assigned_clips += 1

        # Keep existing video thumb synced
        elif scene.video_path and Path(scene.video_path).exists():
            scene_dur = float(getattr(scene, "duration_sec", 0.0) or 5.0)
            max_cont_sec = float(getattr(project, "max_continuous_trailer_sec", 6.0) or 6.0)
            if not getattr(scene, "video_end_sec", None):
                scene.video_end_sec = round(float(getattr(scene, "video_start_sec", 0.0) or 0.0) + min(scene_dur, max_cont_sec), 2)
            if not getattr(scene, "clip_reason", ""):
                scene.clip_reason = f"Vídeo da cena {scene.index} ({Path(scene.video_path).name})."
            if not out_png.exists() or force_reassign:
                extract_video_frame_thumb(
                    Path(scene.video_path),
                    out_png,
                    seek_sec=float(getattr(scene, "video_start_sec", 0.0) or 0.0),
                    vertical_9_16=True,
                )
            if out_png.exists():
                scene.image_path = str(out_png)
            scene.asset_type = "video"

        # 2. Strict Spoken-Script Semantic Foreground Overlay Selection
        cur_ov_mode = getattr(scene, "overlay_mode", "auto") or "auto"
        has_overlay = bool(scene.overlay_image_path and Path(scene.overlay_image_path).exists())
        respect_user_overlay = (not force_reassign) and (has_overlay or cur_ov_mode in {"none_motion_only", "manual"})

        if scene.video_path and Path(scene.video_path).exists():
            if not respect_user_overlay:
                # Score real candidates strictly against spoken narration
                # THRESHOLD: Must have at least 2 distinct concrete term matches (score >= 4.0)
                # Single-word hits like just 'playstation' or 'preço' are NOT enough to cover the video background!
                scored_cands: list[tuple[float, list[str], dict[str, Any]]] = []
                for c in img_cands:
                    sc_val, m_terms = score_scene_visual_relevance(c, scene, project)
                    if sc_val >= 4.0 and str(c.get("url") or "") not in seen_ov_urls:
                        scored_cands.append((sc_val, m_terms, c))
                scored_cands.sort(key=lambda x: x[0], reverse=True)

                ov_fetched = False
                for sc_val, m_terms, c in scored_cands:
                    if apply_overlay_image_url(str(c["url"]), out_ov_png):
                        seen_ov_urls.add(str(c["url"]))
                        scene.overlay_image_path = str(out_ov_png)
                        scene.overlay_mode = "matched_photo"
                        scene.overlay_reason = f"Foto compatível com a fala ({', '.join(m_terms[:3])})"
                        ov_fetched = True
                        assigned_overlays += 1
                        break

                if not ov_fetched:
                    # Secondary hit requires strict 2+ term overlap (score >= 4.5)
                    for sc_val, m_terms, c in sorted(
                        [(score_scene_visual_relevance(c, scene, project)[0], score_scene_visual_relevance(c, scene, project)[1], c) for c in img_cands],
                        key=lambda x: x[0],
                        reverse=True,
                    ):
                        if sc_val >= 4.5 and apply_overlay_image_url(str(c["url"]), out_ov_png):
                            scene.overlay_image_path = str(out_ov_png)
                            scene.overlay_mode = "matched_photo"
                            scene.overlay_reason = f"Foto compatível da matéria ({', '.join(m_terms[:3])})"
                            ov_fetched = True
                            assigned_overlays += 1
                            break

                if not ov_fetched:
                    if _needs_visual_overlay(scene):
                        ai_prompt = build_contextual_ai_overlay_prompt(scene, project)
                        out_ai_png = scenes_dir / f"scene_{scene.index:02d}_overlay_ai.png"
                        ai_ok, engine_used, _ = generate_ai_illustration(
                            ai_prompt,
                            out_ai_png,
                            aspect_ratio="9:16" if getattr(scene, "mockup_style", "auto") == "smartphone_reels" else "16:9",
                            keep_raw=True,
                            seed=1000 + scene.index * 73,
                        )
                        if ai_ok and out_ai_png.exists():
                            scene.overlay_image_path = str(out_ai_png)
                            scene.overlay_mode = "ai_generated"
                            scene.overlay_reason = f"Ilustração IA gerada sob medida ({engine_used}) para a fala da cena"
                            if not (scene.ai_image_prompt or "").strip():
                                scene.ai_image_prompt = ai_prompt
                            ov_fetched = True
                            ai_generated_overlays += 1

                if not ov_fetched:
                    # Clean empty overlay -> NEVER slap unrelated photo or generic filler!
                    scene.overlay_image_path = None
                    scene.visual_layout = "full_media"
                    scene.overlay_mode = "none_motion_only"
                    scene.overlay_reason = "Nenhuma foto 100% compatível — mantendo vídeo em movimento limpo"
                    clean_motion_only += 1

            # Configure creative layout ONLY when there actually IS an overlay image
            if scene.overlay_image_path and Path(scene.overlay_image_path).exists():
                if scene.visual_layout in {"auto", "full_media"} or force_reassign:
                    if idx == 0:
                        scene.visual_layout = "pip_card"
                        scene.overlay_pos = "top_right"
                        scene.overlay_scale = 0.44
                        scene.overlay_start_sec = 0.45
                        scene.overlay_duration_sec = 0.0
                    else:
                        chosen_layout = blend_cycle[(max(0, idx - 1)) % len(blend_cycle)]
                        scene.visual_layout = chosen_layout  # type: ignore[assignment]
                        if chosen_layout == "pip_card":
                            scene.overlay_pos = "top_center" if idx % 2 == 1 else "top_right"
                            scene.overlay_scale = 0.56 if scene.overlay_pos == "top_center" else 0.46
                            scene.overlay_start_sec = 0.35
                            scene.overlay_duration_sec = 0.0
                        elif chosen_layout == "split_top_image":
                            scene.overlay_pos = "top_center"
                            scene.overlay_scale = 0.62
                            scene.overlay_start_sec = 0.0
                            scene.overlay_duration_sec = 0.0
                        else:
                            scene.overlay_pos = "center"
                            scene.overlay_scale = 0.72
                            scene.overlay_start_sec = 0.7
                            scene.overlay_duration_sec = 2.8
                if not (scene.overlay_label or "").strip():
                    scene.overlay_label = _short_overlay_badge_from_scene(scene)
            else:
                scene.visual_layout = "full_media"
                if not scene.overlay_mode or scene.overlay_mode == "auto":
                    scene.overlay_mode = "none_motion_only"
            continue

        # 3. Rare fallback when scene truly has no video clip (static image or HTML card)
        if scene.html_path and Path(scene.html_path).exists():
            if not out_png.exists():
                generate_procedural_scene_card(scene, project, out_png)
            scene.image_path = str(out_png)
            scene.asset_type = "html"
            continue

        if scene.image_path and Path(scene.image_path).exists() and not force_reassign:
            src = Path(scene.image_path)
            if src.resolve() != out_png.resolve():
                fit_cover_9_16(src, out_png)
            scene.image_path = str(out_png)
            scene.asset_type = "image"
            continue

        fetched_img = False
        ordered_bg = [c for c in img_cands if c["url"] not in seen_bg_urls]
        for c in ordered_bg:
            sc_val, _ = score_scene_visual_relevance(c, scene, project)
            if sc_val >= 1.5 and apply_scene_image_url(str(c["url"]), out_png):
                seen_bg_urls.add(str(c["url"]))
                fetched_img = True
                break
        if not fetched_img:
            generate_procedural_scene_card(scene, project, out_png)
        scene.image_path = str(out_png)
        scene.asset_type = "image"

    try:
        record_project_used_media(project)
    except Exception as exc:
        logger.warning("Falha ao auditar mídias utilizadas no projeto %s: %s", project.id, exc)

    return {
        "assigned_clips": assigned_clips,
        "downloaded_news_clips": downloaded_news_clips,
        "assigned_overlays": assigned_overlays,
        "ai_generated_overlays": ai_generated_overlays,
        "clean_motion_only": clean_motion_only,
        "available_cuts": len(interleaved_attached) or len(interleaved_library),
    }


def generate_scene_ai_image_asset(
    project: MusicStoryProject,
    scene: SceneItem,
    work_dir: Path,
    target: str = "overlay",
    custom_prompt: str = "",
) -> dict[str, Any]:
    """
    Generate a bespoke contextual AI illustration for a scene (either as Mockup Overlay or full 9:16 background),
    saving the prompt and returning metadata `{ok, engine, detail, prompt, path}`.
    """
    scenes_dir = work_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    if (custom_prompt or "").strip():
        scene.ai_image_prompt = custom_prompt.strip()
    prompt = build_contextual_ai_overlay_prompt(scene, project)
    if not (scene.ai_image_prompt or "").strip():
        scene.ai_image_prompt = prompt

    is_bg = (target or "overlay").strip().lower() == "background"
    if is_bg:
        out_path = scenes_dir / f"scene_{scene.index:02d}.png"
        aspect = "9:16"
        keep_raw = False
    else:
        out_path = scenes_dir / f"scene_{scene.index:02d}_overlay_ai.png"
        aspect = "9:16" if getattr(scene, "mockup_style", "auto") == "smartphone_reels" else "16:9"
        keep_raw = True

    seed = int(hashlib.sha1(f"{project.id}:{scene.index}:{prompt}".encode("utf-8", errors="ignore")).hexdigest()[:7], 16)
    ok, engine, detail = generate_ai_illustration(
        prompt=prompt,
        out_path=out_path,
        aspect_ratio=aspect,
        keep_raw=keep_raw,
        seed=seed,
    )
    if ok and out_path.exists():
        if is_bg:
            scene.image_path = str(out_path)
            scene.video_path = None
            scene.video_start_sec = 0.0
            scene.asset_type = "image"
            scene.html_path = None
        else:
            scene.overlay_image_path = str(out_path)
            scene.overlay_mode = "ai_generated"
            scene.overlay_reason = f"Ilustração IA gerada sob medida ({engine})"
            if scene.visual_layout == "full_media":
                scene.visual_layout = "pip_card"
            if getattr(scene, "mockup_style", "auto") == "none":
                scene.mockup_style = "auto"
            if not (scene.overlay_label or "").strip():
                scene.overlay_label = _short_overlay_badge_from_scene(scene)
        return {
            "ok": True,
            "engine": engine,
            "detail": detail,
            "prompt": prompt,
            "path": str(out_path),
        }
    return {
        "ok": False,
        "engine": engine,
        "detail": detail,
        "prompt": prompt,
        "path": "",
    }


def prepare_gaming_project_visuals(project: MusicStoryProject, work_dir: Path) -> list[Path]:
    """
    Prepare 9:16 visuals & blended clip+news overlays for a gaming YouTube Short:
    - Prioritizes moving video across ALL scenes (including Hook Scene 1)
    - Distributes attached clips, news/YouTube downloads, & inputs/gta6/ trailers across scenes
    - Pairs only semantically validated overlays (or bespoke AI visuals; otherwise clean moving video)
    """
    auto_blend_gaming_clips_and_images(project, work_dir, force_reassign=False, include_hook_scene=True)
    scenes_dir = work_dir / "scenes"
    return [
        Path(sc.image_path) if sc.image_path else (scenes_dir / f"scene_{sc.index:02d}.png")
        for sc in project.scenes
    ]


AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
_BED_KEYWORDS = (
    "gta", "vice", "leonida", "suno", "trilha", "fundo", "bed", "bg",
    "beat", "synth", "rock", "outrun", "retrowave", "trailer", "game", "ost",
)


def _music_history_claimed_stems() -> set[str]:
    claimed: set[str] = set()
    if not PROJECTS_DIR.exists():
        return claimed
    for pf in PROJECTS_DIR.glob("*/project.json"):
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
            if data.get("niche", "music_history") == "music_history":
                st = re.sub(r"[^a-z0-9]+", "", str(data.get("song_title") or "").lower())
                ar = re.sub(r"[^a-z0-9]+", "", str(data.get("artist") or "").lower())
                if st:
                    claimed.add(st)
                if ar:
                    claimed.add(ar)
                af = data.get("audio_file")
                if af:
                    claimed.add(re.sub(r"[^a-z0-9]+", "", Path(str(af)).stem.lower()))
        except Exception as exc:
            logger.debug("Ignorando projeto ilegível em _music_history_claimed_stems: %s", exc)
            continue
    return claimed


def list_gaming_audio_candidates() -> list[Path]:
    """
    Collect local Suno / background audio files from `inputs/gta6/` (priority) and `inputs/`,
    ignoring songs explicitly tied to music_history projects.
    """
    candidates: list[Path] = []
    if GTA6_INPUTS_DIR.exists():
        for p in sorted(GTA6_INPUTS_DIR.iterdir()):
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                candidates.append(p)

    claimed = _music_history_claimed_stems()
    if INPUTS_DIR.exists():
        for p in sorted(INPUTS_DIR.iterdir()):
            if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
                continue
            low = p.stem.lower()
            norm = re.sub(r"[^a-z0-9]+", "", low)
            has_bed_tag = any(k in low for k in _BED_KEYWORDS)
            if not has_bed_tag and any(c and len(c) >= 4 and c in norm for c in claimed):
                continue
            if has_bed_tag or not candidates:
                candidates.append(p)
    return candidates


def auto_select_gaming_soundtrack(
    project: MusicStoryProject,
    avoid_path: str = "",
) -> tuple[Path | None, float]:
    """
    Automatically select the best Suno / background rock-synth track for a gaming Short
    and locate the optimal groove start time (skipping slow intros).
    Returns `(track_path, best_start_sec)` or `(None, 0.0)` if no local audio exists.
    """
    pool = list_gaming_audio_candidates()
    if not pool:
        return None, 0.0

    filtered = [p for p in pool if not avoid_path or str(p.resolve()) != str(Path(avoid_path).resolve())]
    if filtered:
        pool = filtered

    publish_intent = getattr(project, "publish_intent", "commercial") or "commercial"
    allowed_pool: list[Path] = []
    for track in pool:
        entry = get_media_catalog_entry(track.name)
        allowed, reason = media_allowed(entry, publish_intent)
        if allowed:
            allowed_pool.append(track)
        else:
            logger.info(
                "Omitindo trilha '%s' para finalidade '%s' (%s).",
                track.name,
                publish_intent,
                reason,
            )
    if not allowed_pool:
        logger.warning(
            "Nenhuma trilha sonora autorizada para finalidade '%s' encontrada no pool.",
            publish_intent,
        )
        return None, 0.0
    pool = allowed_pool

    topic_blob = f"{project.song_title} {project.topic_angle} {project.narration_full}".lower()
    mood_groups = [
        (("trailer", "estrada", "mapa", "leonida", "highway", "rock", "carro"), ("rock", "estrada", "highway", "heartland", "arena", "guitar")),
        (("assalto", "lucia", "jason", "crime", "vaza", "policia", "pris"), ("grit", "heist", "assalto", "dark", "tenso", "southern")),
        (("neon", "vice", "miami", "noite", "praia", "vapor"), ("vice", "synth", "neon", "outrun", "miami", "sunset")),
    ]

    scored: list[tuple[float, str, Path]] = []
    for idx, track in enumerate(pool):
        t_low = track.stem.lower()
        score = 10.0 if track.parent.resolve() == GTA6_INPUTS_DIR.resolve() else 4.0
        for topic_keys, file_keys in mood_groups:
            if any(tk in topic_blob for tk in topic_keys) and any(fk in t_low for fk in file_keys):
                score += 6.0
        if "suno" in t_low or "gta" in t_low:
            score += 2.0
        # Deterministic rotation across projects when scores tie
        rot = int(hashlib.sha1(f"{project.id}:{track.name}".encode("utf-8", errors="ignore")).hexdigest()[:6], 16) / float(0xFFFFFF)
        scored.append((score + rot * 1.5, f"{idx:03d}", track))

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = scored[0][2]
    clip_len = min(54.0, max(20.0, float(getattr(project, "max_duration_sec", 58.0) or 58.0)))
    try:
        best_start = find_best_highlight_start(chosen, clip_duration=clip_len)
    except Exception:
        best_start = 0.0
    return chosen, round(float(best_start), 1)


# ============================================================================
# Media Catalog, Licensing & Provenance Tracking (Tarefa 1.1)
# ============================================================================

def load_media_catalog() -> dict[str, Any]:
    catalog_path = INPUTS_DIR / "media_catalog.json"
    if catalog_path.exists():
        try:
            return json.loads(catalog_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Falha ao analisar media_catalog.json: %s", exc)
    return {"version": "1.0", "items": []}


def save_media_catalog(catalog: dict[str, Any]) -> None:
    catalog_path = INPUTS_DIR / "media_catalog.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8")


def _register_downloaded_clip_in_catalog(
    out_mp4: Path,
    source_url: str,
    video_spec: dict[str, Any],
) -> None:
    """Register downloaded 3rd-party video cut into media_catalog.json with review_required status."""
    cat = load_media_catalog()
    items = cat.setdefault("items", [])
    fn = out_mp4.name
    for it in items:
        if str(it.get("filename", "")).lower() == fn.lower():
            return
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    new_entry = {
        "media_id": f"dl_{out_mp4.stem}",
        "filename": fn,
        "file_path": str(out_mp4).replace("\\", "/"),
        "media_type": "video",
        "source_url": source_url,
        "source_type": "downloaded_third_party_clip",
        "copyright_owner": video_spec.get("source") or "Desconhecido / Terceiro",
        "license_type": "desconhecido",
        "authorization_status": "review_required",
        "commercial_use_allowed": False,
        "attribution_required": True,
        "attribution_text": f"Trecho de vídeo via {video_spec.get('source') or source_url}",
        "allowed_platforms": ["youtube_shorts"],
        "proof_url": source_url,
        "sha256": "",
        "verified_at": now_str,
        "risk_level": "alto",
        "usage_notes": "Clipe de notícia baixado automaticamente. Bloqueado para uso comercial até revisão e licenciamento.",
        "history": [
            {
                "action": "auto_download",
                "date": now_str,
                "notes": f"Download de trecho a partir de {source_url}",
            }
        ],
    }
    items.append(new_entry)
    save_media_catalog(cat)
    logger.info("Clipe baixado '%s' registrado no media_catalog.json com status 'review_required'.", fn)


def get_media_catalog_entry(identifier: str | Path | None) -> dict[str, Any] | None:
    if not identifier:
        return None
    ident_str = str(identifier).replace("\\", "/")
    target_name = Path(ident_str).name.lower()
    cat = load_media_catalog()
    for item in cat.get("items", []):
        fn = str(item.get("filename", "")).lower()
        mid = str(item.get("media_id", "")).lower()
        fp = str(item.get("file_path", "")).lower().replace("\\", "/")
        if target_name in (fn, mid) or fn in target_name or target_name.endswith(fn) or ident_str.lower().endswith(fp):
            return item
    return None


def record_project_used_media(project: MusicStoryProject) -> list[dict[str, Any]]:
    """
    Audit and document all media files (soundtracks, background video cuts, scene images)
    used across the project, attaching copyright, licensing, and risk status.
    """
    catalog = load_media_catalog()
    used: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Soundtrack audit
    if project.audio_file:
        audio_name = Path(project.audio_file).name
        entry = get_media_catalog_entry(audio_name)
        mid = entry.get("media_id") if entry else f"audio_{Path(audio_name).stem}"
        if mid not in seen_ids:
            seen_ids.add(mid)
            used.append({
                "media_id": mid,
                "role": "soundtrack",
                "filename": audio_name,
                "source_url": entry.get("source_url", "desconhecido") if entry else "desconhecido",
                "source_type": entry.get("source_type", "ambient_soundtrack") if entry else "ambient_soundtrack",
                "copyright_owner": entry.get("copyright_owner", "Desconhecido / Trilha Local") if entry else "Desconhecido / Trilha Local",
                "license_type": entry.get("license_type", "royalty_free_unverified") if entry else "royalty_free_unverified",
                "authorization_status": entry.get("authorization_status", "review_required") if entry else "unknown",
                "commercial_use_allowed": entry.get("commercial_use_allowed", False) if entry else False,
                "attribution_required": entry.get("attribution_required", True) if entry else True,
                "attribution_text": entry.get("attribution_text", "") if entry else "",
                "allowed_platforms": entry.get("allowed_platforms", ["youtube_shorts"]) if entry else ["youtube_shorts"],
                "proof_url": entry.get("proof_url", "") if entry else "",
                "sha256": entry.get("sha256", "") if entry else "",
                "risk_level": entry.get("risk_level", "alto") if entry else "alto",
                "usage_notes": entry.get("usage_notes", "Trilha sonora sem registro formal de licença no catálogo.") if entry else "Trilha sonora sem registro formal de licença no catálogo.",
                "scenes_used": [s.index for s in project.scenes],
            })

    # 2. Scene videos and images
    for sc in project.scenes:
        target_path = sc.video_path or sc.image_path
        if not target_path:
            continue
        t_name = Path(target_path).name
        entry = get_media_catalog_entry(t_name)
        mid = entry.get("media_id") if entry else f"media_{Path(t_name).stem}"
        is_vid = bool(sc.video_path)

        existing = next((m for m in used if m["media_id"] == mid), None)
        if existing:
            if sc.index not in existing["scenes_used"]:
                existing["scenes_used"].append(sc.index)
        else:
            seen_ids.add(mid)
            is_trailer = any(k in t_name.lower() for k in ("trailer", "gta", "rockstar"))
            default_owner = "Rockstar Games / Take-Two Interactive" if is_trailer else "Desconhecido"
            default_license = "promotional_fair_use" if is_trailer else "desconhecido"
            default_auth = "unknown"  # Sem registro no catálogo, nunca presumir permissão oficial
            default_risk = "medio" if is_trailer else "alto"
            used.append({
                "media_id": mid,
                "role": "scene_video" if is_vid else "scene_image",
                "filename": t_name,
                "source_url": entry.get("source_url", "desconhecido") if entry else "desconhecido",
                "source_type": entry.get("source_type", "trailer_cut" if is_vid else "image") if entry else "desconhecido",
                "copyright_owner": entry.get("copyright_owner", default_owner) if entry else default_owner,
                "license_type": entry.get("license_type", default_license) if entry else default_license,
                "authorization_status": entry.get("authorization_status", default_auth) if entry else default_auth,
                "commercial_use_allowed": entry.get("commercial_use_allowed", False) if entry else False,
                "attribution_required": entry.get("attribution_required", True) if entry else True,
                "attribution_text": entry.get("attribution_text", "") if entry else "",
                "allowed_platforms": entry.get("allowed_platforms", ["youtube_shorts"]) if entry else ["youtube_shorts"],
                "proof_url": entry.get("proof_url", "") if entry else "",
                "sha256": entry.get("sha256", "") if entry else "",
                "risk_level": entry.get("risk_level", default_risk) if entry else default_risk,
                "usage_notes": entry.get("usage_notes", "Uso em corte de fundo vertical transformativo com locução.") if entry else "Mídia sem anotação prévia no catálogo.",
                "scenes_used": [sc.index],
            })

    for item in used:
        if item.get("role") in ("scene_video", "scene_image"):
            fn = item["filename"]
            item["clip_details"] = [
                {
                    "scene_index": s.index,
                    "start_sec": float(getattr(s, "video_start_sec", 0.0) or 0.0),
                    "end_sec": float(getattr(s, "video_end_sec", 0.0) or 0.0) if getattr(s, "video_end_sec", None) is not None else None,
                    "reason": getattr(s, "clip_reason", ""),
                }
                for s in project.scenes
                if (s.video_path and Path(s.video_path).name == fn) or (s.image_path and Path(s.image_path).name == fn)
            ]

    project.used_media = used
    return used
