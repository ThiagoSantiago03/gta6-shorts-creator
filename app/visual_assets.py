from __future__ import annotations

import base64
import hashlib
import logging
import math
import random
import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.config import get_ffmpeg_exe, get_gemini_api_key, get_image_model, get_text_model
from app.models import MusicStoryProject, SceneItem

logger = logging.getLogger("gta6_visual_assets")

W, H = 1080, 1920

WIKI_HEADERS = {
    "User-Agent": "MusicHistoryReelsStudio/2.0 (https://localhost; local-creator@example.com)"
}

_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into",
    "its", "their", "about", "over", "under", "while", "during",
    "your", "you", "our", "are", "was", "were", "have", "has",
})
_SOURCE_HINTS = frozenset({"prelinger", "archive", "archives", "stock", "footage"})


def _hex_to_rgb(hex_color: str, default: tuple[int, int, int] = (255, 59, 48)) -> tuple[int, int, int]:
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) == 6:
        try:
            return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
        except ValueError as exc:
            logger.debug("Cor hex inválida '%s', usando default: %s", hex_color, exc)
    return default


def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        # Windows
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\impact.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        # Linux
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # macOS
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except Exception as exc:
                logger.debug("Fonte indisponível (%s): %s", path, exc)
                continue
    return ImageFont.load_default()


def fit_cover_9_16(img_path: Path, out_path: Path, width: int = W, height: int = H) -> Path:
    """
    Create a borderless full-bleed 1080x1920 vertical Reels frame (like pro editing refs):
    - Portrait, square, and standard 4:3/5:4 photos (`src_ratio <= 1.38`) fill 1080x1920 edge-to-edge
      with top-weighted vertical framing (~25% headroom bias) so band faces/heads are never clipped.
    - Very wide panoramic photos (`src_ratio > 1.38`) span full 1080px width and feather smoothly top/bottom
      with a soft cosine alpha transition over a darkened blurred stage backdrop (zero harsh card seams!).
    """
    with Image.open(img_path) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        src_ratio = iw / max(1, ih)

        if src_ratio <= 1.38:
            scale = max(width / max(1, iw), height / max(1, ih))
            nw, nh = max(width, int(round(iw * scale))), max(height, int(round(ih * scale)))
            resized = im.resize((nw, nh), Image.Resampling.LANCZOS)
            left = max(0, (nw - width) // 2)
            # Bias crop slightly toward upper subject zone (25%) for portraits & concert figures
            top = max(0, min(nh - height, int(round((nh - height) * 0.25))))
            final = resized.crop((left, top, left + width, top + height))
        else:
            bg_scale = max(width / max(1, iw), height / max(1, ih))
            bg_w, bg_h = max(width, int(round(iw * bg_scale))), max(height, int(round(ih * bg_scale)))
            bg = im.resize((bg_w, bg_h), Image.Resampling.LANCZOS)
            bg = bg.crop(((bg_w - width) // 2, (bg_h - height) // 2, (bg_w + width) // 2, (bg_h + height) // 2))
            bg = bg.filter(ImageFilter.GaussianBlur(radius=42))
            bg_arr = np.asarray(bg, dtype=np.float32) * 0.28
            bg_img = Image.fromarray(np.clip(bg_arr, 0, 255).astype(np.uint8)).convert("RGBA")

            fg_scale = max(width / max(1, iw), min(1.08, (height * 0.62) / max(1, ih)))
            fg_w = max(width, int(round(iw * fg_scale)))
            fg_h = max(20, int(round(ih * fg_scale)))
            fg = im.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
            cx = max(0, (fg_w - width) // 2)
            fg = fg.crop((cx, 0, cx + width, fg_h))

            y_idx = np.linspace(0.0, 1.0, fg_h, dtype=np.float32)
            feather = 0.20
            alpha_1d = np.ones(fg_h, dtype=np.float32)
            top_m = y_idx < feather
            bot_m = y_idx > (1.0 - feather)
            alpha_1d[top_m] = 0.5 * (1.0 - np.cos(np.pi * (y_idx[top_m] / feather)))
            alpha_1d[bot_m] = 0.5 * (1.0 - np.cos(np.pi * ((1.0 - y_idx[bot_m]) / feather)))
            alpha_2d = np.repeat((alpha_1d * 255.0).astype(np.uint8)[:, None], width, axis=1)

            fg_rgba = fg.convert("RGBA")
            fg_rgba.putalpha(Image.fromarray(alpha_2d, mode="L"))
            y_off = max(0, min(height - fg_h, int(height * 0.45) - fg_h // 2))
            bg_img.alpha_composite(fg_rgba, (0, y_off))
            final = bg_img.convert("RGB")

        final.save(out_path, format="PNG")
    return out_path


_BAD_IMAGE_HINTS = (
    "notgeld", "bank_von", "1000_mark", "500_mark", "100_mark", "gulden",
    "banknote", "coin_", "coat_of_arms", "wappen", "flag_of", "map_of",
    "ansichten", "postkarte", "album_von_", "city_hall", "rathaus", "church",
    "cathedral", "logo.svg", "diagram",
    "500px", "shutterstock", "gettyimages", "alamy", "istock", "dreamstime",
    "123rf", "depositphotos", "watermark",
    "gdansk", "gdańsk", "poland", "polska", "fountain", "fontanna",
    "plac_", "rynek", "pomnik", "statue", "monument", "ulica", "brama",
    "baszta", "town_hall", "city_square", "tramway", "shipyard",
    "wikimania", "conference", "plaque", "memorial", "cemetery", "grave",
    "tombstone", "museum", "exhibition", "newspaper", "gazette",
    "encyclopaedia", "encyclopedia", "handbook", "catalogue", "catalog",
    "brunnen", "stadt", "strasse", "straße", "kirche", "palace",
    "harbor", "harbour", "port_of", "panoramio", "geograph",
    "commons-logo", "wikidata", "edit-clear", "ambox", "padlock", "crystal_clear",
    "question_book", "OOjs_UI", "symbol_", "icon_", "signature",
)


def _is_clean_wikimedia_url(
    url: str,
    extra_text: str = "",
    allow_raster_svg_thumb: bool = False,
) -> bool:
    low = f"{url} {extra_text}".lower()
    url_path = (url or "").split("?")[0].lower()
    if url_path.endswith(".svg"):
        return False
    if ".svg/" in url_path:
        if not (allow_raster_svg_thumb and url_path.endswith((".png", ".jpg", ".jpeg", ".webp"))):
            return False
    elif ".svg" in f"{extra_text}".lower() and not allow_raster_svg_thumb:
        if "logo.svg" in low or "icon" in low:
            return False
    return not any(bad in low for bad in _BAD_IMAGE_HINTS)


def _extract_wikimedia_meta_blob(page: dict, info: dict) -> str:
    ext = info.get("extmetadata") or {}
    parts = [str(page.get("title") or "")]
    for k in ("ObjectName", "ImageDescription", "Categories", "Artist", "Credit"):
        val = (ext.get(k) or {}).get("value")
        if val:
            parts.append(str(val))
    return " ".join(parts)


_WIKI_MUSIC_TERMS = (
    "band", "musician", "singer", "songwriter", "guitarist", "bassist", "drummer",
    "rock", "metal", "punk", "pop", "hip hop", "rap", "jazz", "soul", "album",
    "single", "song", "concert", "tour", "vocalist", "banda", "músico", "cantor",
    "compositor", "álbum", "canção",
)


def _fetch_wikipedia_entity_media(
    artist: str,
    song_title: str = "",
    album: str = "",
    extra_queries: list[str] | None = None,
    seen_urls: set[str] | None = None,
    max_candidates: int = 16,
) -> list[dict[str, str]]:
    """
    Resolve verified Wikipedia articles about the artist/band, the song/album, and
    narrative entities first, returning high-res metadata-rich items:
      [{"url", "thumb_url", "title", "source", "kind", "_meta", "_px"}]
    Prevents geographic homonyms (e.g. 'Danzig' -> 'Gdańsk fountain').
    """
    scored: list[tuple[int, dict[str, str]]] = []
    local_seen = set(seen_urls or ())
    search_queries: list[tuple[str, bool]] = [
        (f"{artist} band", True),
        (f"{artist} musician", True),
    ]
    if song_title:
        search_queries.append((f'"{song_title}" "{artist}" song', True))
    if album and album.lower() != artist.lower():
        search_queries.append((f'"{album}" "{artist}" album', True))
    for eq in (extra_queries or [])[:2]:
        if eq and eq.strip():
            search_queries.append((eq.strip(), False))

    try:
        with httpx.Client(timeout=12.0, headers=WIKI_HEADERS, follow_redirects=True) as client:
            for api_base, src_tag in (
                ("https://en.wikipedia.org/w/api.php", "Wikipedia EN"),
                ("https://pt.wikipedia.org/w/api.php", "Wikipedia PT"),
            ):
                page_titles: list[str] = []
                for q, require_music in search_queries:
                    r = client.get(
                        api_base,
                        params={
                            "action": "query",
                            "list": "search",
                            "srsearch": q,
                            "srlimit": "3",
                            "format": "json",
                        },
                    )
                    if r.status_code != 200 or "application/json" not in r.headers.get("content-type", ""):
                        continue
                    for hit in r.json().get("query", {}).get("search", []):
                        title = str(hit.get("title") or "")
                        snip = (title + " " + re.sub(r"<[^>]+>", " ", str(hit.get("snippet") or ""))).lower()
                        if any(bad in snip for bad in ("gdansk", "gdańsk", "poland", "municipality", "city in", "prussia")):
                            continue
                        if (not require_music) or any(term in snip for term in _WIKI_MUSIC_TERMS) or (
                            song_title and song_title.lower() in snip
                        ):
                            if title and title not in page_titles:
                                page_titles.append(title)

                if not page_titles:
                    continue

                info_r = client.get(
                    api_base,
                    params={
                        "action": "query",
                        "titles": "|".join(page_titles[:4]),
                        "prop": "pageimages|images",
                        "piprop": "original|thumbnail",
                        "pithumbsize": "1200",
                        "imlimit": "25",
                        "format": "json",
                    },
                )
                if info_r.status_code != 200:
                    continue
                file_titles: list[str] = []
                for p in info_r.json().get("query", {}).get("pages", {}).values():
                    ptitle = str(p.get("title") or artist)
                    orig = p.get("original", {})
                    thumb = p.get("thumbnail", {})
                    purl = orig.get("source") or thumb.get("source")
                    turl = thumb.get("source") or orig.get("source")
                    pw = int(orig.get("width") or thumb.get("width") or 0)
                    ph = int(orig.get("height") or thumb.get("height") or 0)
                    if purl and pw >= 420 and ph >= 300 and _is_clean_wikimedia_url(purl, ptitle) and purl not in local_seen:
                        local_seen.add(purl)
                        scored.append((
                            pw * ph + 600_000,
                            {
                                "url": purl,
                                "thumb_url": turl or purl,
                                "title": ptitle,
                                "source": src_tag,
                                "kind": "image",
                                "_meta": ptitle.lower(),
                                "_px": str(pw * ph),
                            },
                        ))
                    for im in p.get("images") or []:
                        ft = str(im.get("title") or "")
                        if ft and _is_clean_wikimedia_url(ft) and ft not in file_titles:
                            if ft.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                                file_titles.append(ft)

                if file_titles:
                    for i in range(0, min(len(file_titles), 25), 10):
                        batch = file_titles[i : i + 10]
                        fr = client.get(
                            api_base,
                            params={
                                "action": "query",
                                "titles": "|".join(batch),
                                "prop": "imageinfo",
                                "iiprop": "url|mime|size|extmetadata",
                                "iiurlwidth": "1200",
                                "format": "json",
                            },
                        )
                        if fr.status_code != 200:
                            continue
                        for fp in fr.json().get("query", {}).get("pages", {}).values():
                            infos = fp.get("imageinfo") or []
                            if not infos:
                                continue
                            finfo = infos[0]
                            mime = finfo.get("mime", "")
                            fw = int(finfo.get("width", 0))
                            fh = int(finfo.get("height", 0))
                            furl = finfo.get("thumburl") or finfo.get("url")
                            blob = _extract_wikimedia_meta_blob(fp, finfo)
                            clean_title = str(fp.get("title") or "").replace("File:", "").rsplit(".", 1)[0].replace("_", " ")
                            if (
                                mime in {"image/jpeg", "image/png", "image/webp"}
                                and fw >= 500
                                and fh >= 340
                                and furl
                                and _is_clean_wikimedia_url(furl, blob)
                                and furl not in local_seen
                            ):
                                local_seen.add(furl)
                                scored.append((
                                    fw * fh,
                                    {
                                        "url": furl,
                                        "thumb_url": finfo.get("thumburl") or furl,
                                        "title": clean_title[:70] or artist,
                                        "source": src_tag,
                                        "kind": "image",
                                        "_meta": blob.lower(),
                                        "_px": str(fw * fh),
                                    },
                                ))
                if len(scored) >= max_candidates:
                    break
    except Exception as exc:
        logger.debug("Falha na busca de mídia na Wikipedia: %s", exc)

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:max_candidates]]


def _fetch_wikipedia_entity_images(
    artist: str,
    song_title: str = "",
    seen_urls: set[str] | None = None,
    max_candidates: int = 12,
) -> list[str]:
    """Backwards-compatible URL list wrapper over `_fetch_wikipedia_entity_media`."""
    media = _fetch_wikipedia_entity_media(
        artist=artist,
        song_title=song_title,
        seen_urls=seen_urls,
        max_candidates=max_candidates,
    )
    return [m["url"] for m in media if m.get("url")]


def _search_wikipedia_topic_media(
    queries: list[str],
    seen_urls: set[str] | None = None,
    max_candidates: int = 8,
) -> list[dict[str, str]]:
    """
    Search Wikipedia EN directly for non-artist script topics (e.g. 'Tipper Gore',
    'Parents Music Resource Center', 'Parental Advisory', 'MTV', or a custom user query)
    without prioritizing unrelated band portraits ahead of the scene's topic.
    """
    clean_queries = [q.strip() for q in queries if q and q.strip()][:4]
    if not clean_queries:
        return []

    scored: list[tuple[int, dict[str, str]]] = []
    local_seen = set(seen_urls or ())
    try:
        with httpx.Client(timeout=12.0, headers=WIKI_HEADERS, follow_redirects=True) as client:
            page_titles: list[str] = []
            for q in clean_queries:
                r = client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "list": "search",
                        "srsearch": q,
                        "srlimit": "3",
                        "format": "json",
                    },
                )
                if r.status_code != 200 or "application/json" not in r.headers.get("content-type", ""):
                    continue
                for hit in r.json().get("query", {}).get("search", []):
                    title = str(hit.get("title") or "")
                    snip = (title + " " + re.sub(r"<[^>]+>", " ", str(hit.get("snippet") or ""))).lower()
                    if any(bad in snip for bad in ("gdansk", "gdańsk", "poland", "municipality", "city in", "prussia")):
                        continue
                    if title and title not in page_titles:
                        page_titles.append(title)

            if not page_titles:
                return []

            info_r = client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "titles": "|".join(page_titles[:5]),
                    "prop": "pageimages|images",
                    "piprop": "original|thumbnail",
                    "pithumbsize": "1200",
                    "imlimit": "20",
                    "format": "json",
                },
            )
            if info_r.status_code != 200:
                return []

            file_titles: list[str] = []
            for p in info_r.json().get("query", {}).get("pages", {}).values():
                ptitle = str(p.get("title") or clean_queries[0])
                orig = p.get("original", {})
                thumb = p.get("thumbnail", {})
                purl = orig.get("source") or thumb.get("source")
                turl = thumb.get("source") or orig.get("source")
                pw = int(orig.get("width") or thumb.get("width") or 0)
                ph = int(orig.get("height") or thumb.get("height") or 0)
                if purl and pw >= 420 and ph >= 280 and _is_clean_wikimedia_url(purl, ptitle) and purl not in local_seen:
                    local_seen.add(purl)
                    scored.append((
                        pw * ph + 900_000,
                        {
                            "url": purl,
                            "thumb_url": turl or purl,
                            "title": ptitle,
                            "source": "Wikipedia Topic",
                            "kind": "image",
                            "_meta": ptitle.lower(),
                            "_px": str(pw * ph),
                        },
                    ))
                for im in p.get("images") or []:
                    ft = str(im.get("title") or "")
                    if ft and _is_clean_wikimedia_url(ft) and ft not in file_titles:
                        if ft.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                            file_titles.append(ft)

            for i in range(0, min(len(file_titles), 20), 10):
                batch = file_titles[i : i + 10]
                fr = client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "titles": "|".join(batch),
                        "prop": "imageinfo",
                        "iiprop": "url|mime|size|extmetadata",
                        "iiurlwidth": "1200",
                        "format": "json",
                    },
                )
                if fr.status_code != 200:
                    continue
                for fp in fr.json().get("query", {}).get("pages", {}).values():
                    infos = fp.get("imageinfo") or []
                    if not infos:
                        continue
                    finfo = infos[0]
                    mime = finfo.get("mime", "")
                    fw = int(finfo.get("width", 0))
                    fh = int(finfo.get("height", 0))
                    furl = finfo.get("thumburl") or finfo.get("url")
                    blob = _extract_wikimedia_meta_blob(fp, finfo)
                    clean_title = str(fp.get("title") or "").replace("File:", "").rsplit(".", 1)[0].replace("_", " ")
                    if (
                        mime in {"image/jpeg", "image/png", "image/webp"}
                        and fw >= 460
                        and fh >= 300
                        and furl
                        and _is_clean_wikimedia_url(furl, blob)
                        and furl not in local_seen
                    ):
                        local_seen.add(furl)
                        scored.append((
                            fw * fh + 350_000,
                            {
                                "url": furl,
                                "thumb_url": finfo.get("thumburl") or furl,
                                "title": clean_title[:70] or clean_queries[0][:40],
                                "source": "Wikipedia Topic",
                                "kind": "image",
                                "_meta": blob.lower(),
                                "_px": str(fw * fh),
                            },
                        ))
    except Exception as exc:
        logger.debug("Falha na busca de mídia tópica na Wikipedia: %s", exc)

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:max_candidates]]


def _extract_topic_only_queries(scene: SceneItem, project: MusicStoryProject) -> list[str]:
    """
    Extract concise 2-3 word scene-specific historical/subject queries (e.g. Tipper Gore,
    Parents Music Resource Center, MTV, mixing console) independent of generic band photos.
    """
    txt = f"{scene.title} {scene.narration_text} {scene.quote_text} {scene.search_query}".lower()
    artist = project.artist.strip()
    song = project.song_title.strip()
    album = _infer_project_album(project)
    topics: list[str] = []

    if any(k in txt for k in ("censura", "pmrc", "parental", "aviso", "tipper", "etiqueta", "selo", "comitê", "senado")):
        topics.extend(["Tipper Gore", "Parental Advisory", "Parents Music Resource Center"])
    if "rubin" in txt:
        topics.append("Rick Rubin")
    if any(k in txt for k in ("mtv", "clipe", "videoclipe", "televisão", "emissora")):
        topics.extend(["MTV", f'"{artist}" MTV'])
    if any(k in txt for k in ("álbum", "album", "disco", "estreia", "faixa")):
        if album and album.lower() != artist.lower():
            topics.append(f'"{album}" "{artist}"')
        elif song:
            topics.append(f'"{song}" "{artist}"')
    if any(k in txt for k in ("estúdio", "gravação", "produtor", "produção", "mesa", "analóg")):
        topics.extend(["recording studio mixing console", f'"{artist}" studio'])
    if any(k in txt for k in ("vinil", "vitrola", "toca-discos")):
        topics.append("vinyl record turntable")

    dedup: list[str] = []
    seen_q: set[str] = set()
    for q in topics:
        qn = q.strip()
        if qn and qn.lower() not in seen_q:
            seen_q.add(qn.lower())
            dedup.append(qn)
    return dedup[:4]


def _extract_script_visual_queries(
    scene: SceneItem,
    project: MusicStoryProject,
    custom_query: str = "",
) -> list[str]:
    """
    Build targeted visual search queries strictly aligned to BOTH the chosen song/artist
    and what this specific scene's narration actually says!
    """
    if custom_query.strip():
        return [custom_query.strip()]

    queries: list[str] = _extract_topic_only_queries(scene, project)
    if scene.search_query and scene.search_query.strip():
        queries.append(scene.search_query.strip())

    txt = f"{scene.title} {scene.narration_text} {scene.quote_text}".lower()
    artist = project.artist.strip()

    if any(k in txt for k in ("show", "palco", "festival", "turnê", "ao vivo", "fãs")):
        queries.append(f'"{artist}" live concert')

    fallback = f'"{artist}" (band OR musician OR concert OR singer)'
    if fallback not in queries:
        queries.append(fallback)

    dedup: list[str] = []
    seen_q: set[str] = set()
    for q in queries:
        qn = q.strip()
        if qn and qn.lower() not in seen_q:
            seen_q.add(qn.lower())
            dedup.append(qn)
    return dedup[:5]


def _score_candidate_against_context(
    meta_text: str,
    area_px: int,
    context_tokens: set[str],
    artist: str = "",
    topic_tokens: set[str] | None = None,
) -> float:
    low = (meta_text or "").lower()
    ttoks = topic_tokens or set()
    topic_hits = sum(1 for tok in ttoks if tok in low)
    ctx_hits = sum(1 for tok in context_tokens if tok in low and tok not in ttoks)
    art_hit = 1 if (artist and artist.lower() in low) else 0
    return (
        topic_hits * 4_000_000.0
        + ctx_hits * 1_600_000.0
        + art_hit * 900_000.0
        + min(2_000_000.0, float(area_px))
    )


_PT_STOP_WORDS = frozenset({
    "para", "como", "sobre", "entre", "essa", "esse", "isso", "tudo", "onde", "quando",
    "seus", "suas", "dela", "dele", "mais", "muito", "pela", "pelo", "pelos", "pelas",
    "uma", "umas", "uns", "aqui", "depois", "antes", "ainda", "sempre", "nunca",
    "musica", "música", "cancao", "canção", "faixa", "historia", "história", "banda",
    "estreia", "classico", "clássico", "faixas", "verso", "letra", "refrao", "refrão",
    "rock", "metal", "heavy", "punk", "pop", "jazz", "soul",
})


def _query_has_topic_tokens(query: str, artist: str) -> bool:
    art_tokens = {t.lower() for t in re.split(r"[\W_]+", artist) if len(t) >= 2}
    generic = _STOP_WORDS | _SOURCE_HINTS | {"band", "banda", "musician", "singer", "group"}
    for tok in re.split(r"[\W_]+", query):
        tl = tok.lower()
        if len(tl) >= 3 and tl not in art_tokens and tl not in generic:
            return True
    return False


def _infer_project_album(project: MusicStoryProject) -> str:
    explicit = str(getattr(project, "album", "") or "").strip()
    if explicit:
        return explicit
    sub = str(project.reel2_subtitle or "").split("•")[0].strip()
    if sub and sub.lower() not in {project.artist.lower(), project.song_title.lower()}:
        return sub
    m = re.search(r"Álbum:\s*([^\n|•]+)", project.research_summary or "")
    if m:
        return m.group(1).strip()
    return ""


def _context_tokens_for_scene(
    scene: SceneItem,
    project: MusicStoryProject,
    custom_query: str = "",
) -> set[str]:
    album_name = _infer_project_album(project)
    blob = custom_query.strip() or (
        f"{scene.title} {scene.narration_text} {scene.quote_text} {scene.search_query} "
        f"{project.song_title} {project.artist} {album_name}"
    )
    tokens: set[str] = set()
    for raw in re.split(r"[\W_]+", blob):
        tl = raw.lower()
        if len(tl) >= 3 and tl not in _STOP_WORDS and tl not in _PT_STOP_WORDS:
            tokens.add(tl)
    return tokens


def _context_topic_tokens_for_scene(
    scene: SceneItem,
    project: MusicStoryProject,
    custom_query: str = "",
) -> set[str]:
    """Tokens derived specifically from scene topics / custom query (excluding generic artist/song names)."""
    art_toks = {t.lower() for t in re.split(r"[\W_]+", f"{project.artist} {project.song_title}") if len(t) >= 2}
    if custom_query.strip():
        raw_blob = custom_query.strip()
    else:
        raw_blob = " ".join(_extract_topic_only_queries(scene, project))
    out: set[str] = set()
    for raw in re.split(r"[\W_]+", raw_blob):
        tl = raw.lower()
        if len(tl) >= 3 and tl not in _STOP_WORDS and tl not in _PT_STOP_WORDS and (custom_query.strip() or tl not in art_toks):
            out.add(tl)
    return out


def _query_commons_images(
    client: httpx.Client,
    query_str: str,
    limit: int = 8,
    allow_raster_svg: bool = True,
) -> list[dict[str, Any]]:
    """
    Search Wikimedia Commons with automatic relaxed 2-word subquery fallback when a
    multi-word strict AND query returns fewer than 3 usable images.
    """
    results: list[dict[str, Any]] = []
    seen_local: set[str] = set()

    def _run_once(q_exec: str) -> None:
        resp = client.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "generator": "search",
                "gsrnamespace": "6",
                "gsrsearch": q_exec,
                "gsrlimit": str(limit),
                "prop": "imageinfo",
                "iiprop": "url|mime|size|extmetadata",
                "iiurlwidth": "1000",
                "format": "json",
            },
        )
        if resp.status_code != 200 or "application/json" not in resp.headers.get("content-type", ""):
            return
        pages = resp.json().get("query", {}).get("pages", {})
        for p in pages.values():
            infos = p.get("imageinfo") or []
            if not infos:
                continue
            info = infos[0]
            mime = info.get("mime", "")
            w = int(info.get("width", 0))
            h = int(info.get("height", 0))
            thumb_u = str(info.get("thumburl") or "")
            full_u = str(info.get("url") or "")
            is_svg_raster = (
                allow_raster_svg
                and mime == "image/svg+xml"
                and thumb_u.lower().split("?")[0].endswith((".png", ".jpg", ".jpeg", ".webp"))
                and w >= 240
                and h >= 160
            )
            is_raster = mime in {"image/jpeg", "image/png", "image/webp"} and w >= 480 and h >= 320
            if not (is_raster or is_svg_raster):
                continue

            # Never store raw .svg as primary download URL — use rasterized PNG thumburl
            chosen_url = thumb_u if is_svg_raster else (full_u or thumb_u)
            display_thumb = thumb_u or chosen_url
            meta_blob = _extract_wikimedia_meta_blob(p, info)
            if not chosen_url or not _is_clean_wikimedia_url(display_thumb, meta_blob, allow_raster_svg_thumb=is_svg_raster):
                continue
            if chosen_url in seen_local:
                continue
            seen_local.add(chosen_url)
            raw_title = str(p.get("title") or "").replace("File:", "").rsplit(".", 1)[0].replace("_", " ")
            results.append(
                {
                    "url": chosen_url,
                    "thumb_url": display_thumb,
                    "title": raw_title[:68] or q_exec[:48],
                    "source": "Wikimedia Commons",
                    "kind": "image",
                    "_meta": meta_blob,
                    "_px": w * h,
                }
            )

    _run_once(query_str)
    # Relax strict multi-word Commons AND search when fewer than 3 hits came back
    if len(results) < 3 and '"' not in query_str and " OR " not in query_str:
        words = [
            t for t in re.split(r"[\W_]+", query_str)
            if len(t) >= 3 and t.lower() not in _STOP_WORDS and t.lower() not in _PT_STOP_WORDS
        ]
        if len(words) >= 3:
            _run_once(" ".join(words[:2]))
            if len(results) < 3 and len(words) >= 4:
                _run_once(" ".join(words[-2:]))
    return results


def _fit_stage_fullbleed_9_16(img_path: Path, out_path: Path, width: int = W, height: int = H) -> Path:
    """Create a borderless full-bleed 1080x1920 stage image using unified cover/feather logic."""
    return fit_cover_9_16(img_path, out_path, width=width, height=height)


def search_wikimedia_image(
    query: str,
    out_path: Path,
    seen_urls: set[str] | None = None,
    candidate_urls_out: list[str] | None = None,
    artist: str = "",
    wiki_pool: list[str] | None = None,
    context_tokens: set[str] | None = None,
    topic_tokens: set[str] | None = None,
) -> bool:
    """
    Fetch a high-res historical/music photo from verified Wikipedia entity articles,
    Wikimedia Commons, or Wikipedia PageImages (free, no key).
    Combines artist-verified Wikipedia photos with topic-scored Wikimedia Commons hits
    when the scene query contains script-specific topics (album, PMRC, MTV, studio, etc.).
    """
    if not query.strip():
        return False

    ctx = set(t.lower() for t in (context_tokens or ()) if len(t) >= 3)
    for tok in re.split(r"[\W_]+", query):
        tl = tok.lower()
        if len(tl) >= 3 and tl not in _STOP_WORDS and tl not in _SOURCE_HINTS:
            ctx.add(tl)

    has_topic = _query_has_topic_tokens(query, artist) or bool(topic_tokens)
    candidate_urls: list[tuple[float, str]] = []
    q_low = query.lower()
    art_low = artist.strip().lower()
    if (not art_low or art_low in q_low or has_topic) and wiki_pool:
        base_bonus = 240_000.0 if has_topic else 2_400_000.0
        for idx_u, wu in enumerate(wiki_pool):
            if wu and _is_clean_wikimedia_url(wu) and (seen_urls is None or wu not in seen_urls):
                sc = _score_candidate_against_context(wu, 900_000, ctx, artist=artist, topic_tokens=topic_tokens) + base_bonus - idx_u * 500.0
                candidate_urls.append((sc, wu))

    # Avoid ultra-ambiguous 1-token queries hitting city homonyms on Commons
    commons_q = query.strip()
    if art_low and commons_q.lower() == art_low:
        commons_q = f'"{artist}" (band OR concert OR musician OR singer)'

    try:
        with httpx.Client(timeout=12.0, headers=WIKI_HEADERS, follow_redirects=True) as client:
            if has_topic or len(candidate_urls) < 4:
                for item in _query_commons_images(client, commons_q, limit=10, allow_raster_svg=True):
                    u = str(item.get("url") or "")
                    if u and (seen_urls is None or u not in seen_urls):
                        sc = _score_candidate_against_context(
                            str(item.get("_meta") or u),
                            int(item.get("_px") or 800_000),
                            ctx,
                            artist=artist,
                            topic_tokens=topic_tokens,
                        )
                        candidate_urls.append((sc, u))

            if not candidate_urls:
                wp = client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "generator": "search",
                        "gsrsearch": query,
                        "gsrlimit": "5",
                        "prop": "pageimages",
                        "piprop": "thumbnail|original",
                        "pithumbsize": "1200",
                        "format": "json",
                    },
                )
                if wp.status_code == 200 and "application/json" in wp.headers.get("content-type", ""):
                    for p in wp.json().get("query", {}).get("pages", {}).values():
                        thumb = p.get("thumbnail", {})
                        orig = p.get("original", {})
                        img_url = thumb.get("source") or orig.get("source")
                        w = int(thumb.get("width") or orig.get("width") or 0)
                        if img_url and w >= 450 and _is_clean_wikimedia_url(img_url) and (seen_urls is None or img_url not in seen_urls):
                            sc = _score_candidate_against_context(str(p.get("title") or ""), w * w, ctx, artist=artist, topic_tokens=topic_tokens)
                            candidate_urls.append((sc, img_url))

            # Deduplicate keeping best score
            dedup_map: dict[str, float] = {}
            for sc, u in candidate_urls:
                if u not in dedup_map or sc > dedup_map[u]:
                    dedup_map[u] = sc
            ranked = sorted(((sc, u) for u, sc in dedup_map.items()), reverse=True)

            if candidate_urls_out is not None:
                candidate_urls_out[:] = [u for _, u in ranked[:6]]

            for _, img_url in ranked[:6]:
                img_resp = client.get(img_url)
                ctype = img_resp.headers.get("content-type", "")
                if img_resp.status_code == 200 and "image/" in ctype and len(img_resp.content) > 8_000:
                    raw_path = out_path.with_suffix(".raw")
                    raw_path.write_bytes(img_resp.content)
                    fit_cover_9_16(raw_path, out_path)
                    raw_path.unlink(missing_ok=True)
                    if seen_urls is not None:
                        seen_urls.add(img_url)
                    if candidate_urls_out is not None:
                        candidate_urls_out[:] = [img_url] + [u for _, u in ranked[:6] if u != img_url][:5]
                    return True
    except Exception:
        return False
    return False


_BAD_ARCHIVE_WORDS = (
    "kidnap", "murder", "crime", "police", "5pm", "news", "eyewitness", "ppv",
    "ladies night", "payperview", "podcast", "twitch", "tiktok", "roblox", "minecraft",
    "accident", "weather", "forecast", "sports", "football",
)


def _archive_query_cascade(user_query: str) -> list[str]:
    """OpenMontage Archive.org Solr cascade focused strictly on vintage Prelinger cultural/music film reels."""
    coll = "(collection:prelinger)"
    tokens = [
        t for t in re.split(r"\s+", user_query.strip())
        if len(t) >= 3 and t.lower() not in _STOP_WORDS and t.lower() not in _SOURCE_HINTS
    ]
    non_year = [t for t in tokens if not (t.rstrip("s").isdigit() and len(t.rstrip("s")) == 4)]
    queries: list[str] = []
    if len(tokens) >= 2:
        queries.append(f'mediatype:movies AND {coll} AND ("{" ".join(tokens)}"~8)')
    if len(non_year) >= 2:
        top2 = sorted(non_year, key=lambda x: -len(x))[:2]
        queries.append(f"mediatype:movies AND {coll} AND ({top2[0]} AND {top2[1]})")
    elif non_year:
        queries.append(f"mediatype:movies AND {coll} AND ({non_year[0]})")
    return queries


def _doc_is_relevant_archive(doc: dict, tokens: list[str]) -> bool:
    title_blob = " ".join([
        str(doc.get("title") or ""),
        str(doc.get("identifier") or ""),
    ]).lower()
    full_blob = (title_blob + " " + str(doc.get("description") or "")).lower()
    if any(b in full_blob for b in _BAD_ARCHIVE_WORDS):
        return False
    if not tokens:
        return True
    # Prefer matching the title/identifier to avoid false hits on unrelated long descriptions
    return any(tok.lower() in title_blob for tok in tokens)


def _pick_archive_mp4(files: list[dict]) -> tuple[str, float] | None:
    priority = ("h.264", "MPEG4", "h.264 HD", "512Kb MPEG4")
    max_bytes = 38 * 1024 * 1024
    best: tuple[int, str, float] | None = None
    for f in files or []:
        fmt = f.get("format") or ""
        name = str(f.get("name") or "")
        if not name.lower().endswith(".mp4"):
            continue
        if fmt not in priority:
            continue
        try:
            size = int(f.get("size") or 0)
        except Exception:
            size = 0
        if size <= 0 or size > max_bytes:
            continue
        dur = 0.0
        raw_len = f.get("length")
        if raw_len:
            try:
                dur = float(raw_len)
            except Exception:
                parts = str(raw_len).split(":")
                if len(parts) == 3:
                    dur = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if dur > 600.0 or (dur > 0 and dur < 3.0):
            continue
        rank = priority.index(fmt)
        if best is None or rank < best[0]:
            best = (rank, name, dur)
    if best is None:
        return None
    return best[1], best[2]


def search_archive_org_video(
    query: str,
    out_mp4: Path,
    seen_urls: set[str] | None = None,
    candidate_urls_out: list[str] | None = None,
) -> bool:
    """
    Search Archive.org open film collections (Prelinger / OpenSource Movies / Home Movies)
    for real public-domain archival MP4 B-roll clips (zero API keys!).
    """
    if not query.strip():
        return False
    q_tokens = [
        t for t in re.split(r"\s+", query.strip())
        if len(t) >= 3 and t.lower() not in _STOP_WORDS and t.lower() not in _SOURCE_HINTS
    ]
    try:
        with httpx.Client(timeout=14.0, headers=WIKI_HEADERS, follow_redirects=True) as client:
            for solr_q in _archive_query_cascade(query):
                r = client.get(
                    "https://archive.org/advancedsearch.php",
                    params=[
                        ("q", solr_q),
                        ("fl[]", "identifier"),
                        ("fl[]", "title"),
                        ("fl[]", "description"),
                        ("rows", "8"),
                        ("output", "json"),
                    ],
                )
                if r.status_code != 200:
                    continue
                docs = (r.json().get("response") or {}).get("docs") or []
                for doc in docs:
                    if not _doc_is_relevant_archive(doc, q_tokens):
                        continue
                    ident = doc.get("identifier")
                    if not ident:
                        continue
                    meta_resp = client.get(f"https://archive.org/metadata/{ident}")
                    if meta_resp.status_code != 200:
                        continue
                    picked = _pick_archive_mp4(meta_resp.json().get("files") or [])
                    if not picked:
                        continue
                    fname, _ = picked
                    encoded_fname = urllib.parse.quote(fname, safe="/")
                    dl_url = f"https://archive.org/download/{ident}/{encoded_fname}"
                    if seen_urls is not None and dl_url in seen_urls:
                        continue
                    if candidate_urls_out is not None and dl_url not in candidate_urls_out:
                        candidate_urls_out.append(dl_url)

                    # Download full <=24MB MP4 cleanly so moov atom at end of file stays intact
                    raw_mp4 = out_mp4.with_suffix(".raw.mp4")
                    with client.stream("GET", dl_url, timeout=45.0) as dl:
                        if dl.status_code != 200:
                            continue
                        with open(raw_mp4, "wb") as fp:
                            for chunk in dl.iter_bytes(65536):
                                fp.write(chunk)

                    # Verify and extract a crisp vertical-compatible 12s clip starting slightly inside the reel
                    ffmpeg = get_ffmpeg_exe()
                    trim_cmd = [
                        ffmpeg, "-y",
                        "-ss", "4.0",
                        "-t", "12.0",
                        "-i", str(raw_mp4),
                        "-vf", "fps=30,scale=1080:-2:force_original_aspect_ratio=decrease",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-an",
                        str(out_mp4),
                    ]
                    res = subprocess.run(trim_cmd, capture_output=True, text=True)
                    raw_mp4.unlink(missing_ok=True)
                    if res.returncode == 0 and out_mp4.exists() and out_mp4.stat().st_size > 25_000:
                        if seen_urls is not None:
                            seen_urls.add(dl_url)
                        return True
    except Exception:
        return False
    return False


def fetch_itunes_artwork_url(artist: str, song_title: str) -> str | None:
    """Return official 800x800 album artwork URL from public iTunes Search API when artist matches."""
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(
                "https://itunes.apple.com/search",
                params={"term": f"{artist} {song_title}", "entity": "song", "limit": "6"},
            )
            if resp.status_code != 200:
                return None
            results = resp.json().get("results", [])
            artist_low = artist.lower().strip()
            song_low = song_title.lower().strip()
            best_url: str | None = None
            best_score = -1
            for item in results:
                item_artist = str(item.get("artistName", "")).lower()
                item_track = str(item.get("trackName", "")).lower()
                if artist_low in item_artist or item_artist in artist_low:
                    art_url = str(item.get("artworkUrl100", ""))
                    if not art_url:
                        continue
                    score = 1
                    if song_low and (song_low in item_track or item_track in song_low):
                        score += 2
                    if score > best_score:
                        best_score = score
                        best_url = art_url.replace("100x100bb", "800x800bb")
            return best_url
    except Exception:
        return None


def fetch_itunes_artwork(artist: str, song_title: str, out_path: Path) -> bool:
    """Fetch official high-res album artwork from public iTunes Search API when artist matches."""
    hi_url = fetch_itunes_artwork_url(artist, song_title)
    if not hi_url:
        return False
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            img_resp = client.get(hi_url)
            if img_resp.status_code == 200 and len(img_resp.content) > 10_000:
                raw_path = out_path.with_suffix(".art")
                raw_path.write_bytes(img_resp.content)
                with Image.open(raw_path) as im:
                    im = im.convert("RGBA").resize((640, 640), Image.Resampling.LANCZOS)
                    mask = Image.new("L", (640, 640), 0)
                    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 640, 640), radius=42, fill=255)
                    im.putalpha(mask)
                    im.save(out_path, format="PNG")
                raw_path.unlink(missing_ok=True)
                return True
    except Exception:
        return False
    return False


def extract_video_frame_thumb(
    video_path: Path,
    out_png: Path,
    seek_sec: float = 1.5,
    vertical_9_16: bool = False,
) -> bool:
    """Extract a crisp frame from a local video file cleanly with fallback seek=0.1s."""
    if not video_path.exists():
        return False
    out_png.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_exe()
    tmp_raw = out_png.with_suffix(".vthumb.png")
    for ss in (max(0.0, float(seek_sec)), 0.1):
        try:
            cmd = [ffmpeg, "-y", "-ss", f"{ss:.2f}", "-i", str(video_path), "-frames:v", "1"]
            if not vertical_9_16:
                cmd += ["-vf", "scale=480:-2"]
            cmd.append(str(tmp_raw))
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0 and tmp_raw.exists() and tmp_raw.stat().st_size > 1_000:
                if vertical_9_16:
                    fit_cover_9_16(tmp_raw, out_png)
                    tmp_raw.unlink(missing_ok=True)
                else:
                    tmp_raw.replace(out_png)
                return out_png.exists()
        except Exception as exc:
            logger.debug("Falha na renderização de frame de vídeo: %s", exc)
    tmp_raw.unlink(missing_ok=True)
    return False


_REJECT_IMG_URL_RE = re.compile(
    r"(?:lookaside\.fbsbx\.com|black[-_]?friday|bf20\d\d|playpng|shutterstock|gettyimages|alamy|istock|dreamstime|123rf|depositphotos)",
    re.IGNORECASE,
)


def apply_scene_image_url(url: str, out_png: Path) -> bool:
    """Download an image URL directly and fit it cleanly as a 1080x1920 vertical scene asset."""
    clean_url = (url or "").strip()
    if not clean_url.startswith(("http://", "https://")) or _REJECT_IMG_URL_RE.search(clean_url):
        return False
    out_png.parent.mkdir(parents=True, exist_ok=True)
    raw_path = out_png.with_suffix(".dl.raw")
    try:
        with httpx.Client(timeout=15.0, headers=WIKI_HEADERS, follow_redirects=True) as client:
            resp = client.get(clean_url)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "image/" in ctype and len(resp.content) > 4_000:
                raw_path.write_bytes(resp.content)
                fit_cover_9_16(raw_path, out_png)
                raw_path.unlink(missing_ok=True)
                return out_png.exists()
    except Exception as exc:
        logger.debug("Falha ao baixar imagem de cena de %s: %s", clean_url, exc)
    raw_path.unlink(missing_ok=True)
    return False


def search_scene_visual_candidates(
    scene: SceneItem,
    project: MusicStoryProject,
    custom_query: str = "",
    limit: int = 8,
    preloaded_artist_media: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """
    Search rich visual candidates (Zero-API) genuinely aligned to BOTH the chosen song
    and this specific scene's script/narration, returning metadata cards:
      [{"url", "thumb_url", "title", "source", "kind"}]
    """
    if getattr(project, "niche", "music_history") == "gaming_news":
        from app.gaming_media import search_gaming_scene_candidates

        return search_gaming_scene_candidates(
            scene=scene,
            project=project,
            custom_query=custom_query,
            limit=limit,
        )
    has_explicit_custom = bool(custom_query.strip())
    ctx = _context_tokens_for_scene(scene, project, custom_query=custom_query)
    topic_toks = _context_topic_tokens_for_scene(scene, project, custom_query=custom_query)
    topic_queries = [custom_query.strip()] if has_explicit_custom else _extract_topic_only_queries(scene, project)
    queries = _extract_script_visual_queries(scene, project, custom_query=custom_query)

    artist_in_custom = project.artist.strip().lower() in custom_query.lower() if project.artist.strip() else False
    score_artist = project.artist if (not has_explicit_custom or artist_in_custom) else ""

    scored: dict[str, tuple[float, dict[str, str]]] = {}

    def _canonical_media_key(url_str: str) -> str:
        clean_path = urllib.parse.unquote(url_str.split("?")[0]).lower()
        parts = [p for p in clean_path.split("/") if p]
        fname = parts[-1] if parts else clean_path
        fname = re.sub(r"^\d+px-", "", fname)
        fname = fname.rsplit(".", 1)[0]
        return fname or clean_path

    def _add_cand(score: float, item: dict[str, str]) -> None:
        u = item.get("url", "").strip()
        if not u:
            return
        dedup_key = _canonical_media_key(u)
        clean = {
            "url": u,
            "thumb_url": item.get("thumb_url") or u,
            "title": (item.get("title") or project.artist)[:72],
            "source": item.get("source") or "Wikimedia",
            "kind": item.get("kind") or "image",
        }
        prev = scored.get(dedup_key)
        if prev is None or score > prev[0]:
            scored[dedup_key] = (score, clean)

    # 1. Official song/album cover from iTunes (only when not overriding with an unrelated custom search)
    if not has_explicit_custom:
        itunes_url = fetch_itunes_artwork_url(project.artist, project.song_title)
        if itunes_url:
            txt_low = f"{scene.title} {scene.narration_text}".lower()
            album_boost = 4_200_000.0 if any(k in txt_low for k in ("álbum", "album", "disco", "estreia", "capa", "próximo reel")) else 1_500_000.0
            _add_cand(
                album_boost,
                {
                    "url": itunes_url,
                    "thumb_url": itunes_url,
                    "title": f"Capa Oficial — {project.song_title}",
                    "source": "Capa Oficial",
                    "kind": "image",
                },
            )

    # 2. Verified Wikipedia entity images (Artist, Song, Album) — kept bounded so script topics shine
    if (not has_explicit_custom) or artist_in_custom:
        wiki_items = (
            preloaded_artist_media
            if preloaded_artist_media is not None
            else _fetch_wikipedia_entity_media(
                artist=project.artist,
                song_title=project.song_title,
                album=_infer_project_album(project),
                extra_queries=topic_queries[:1],
                max_candidates=6,
            )
        )
        for item in wiki_items:
            meta = item.get("_meta") or item.get("title", "")
            px = int(item.get("_px") or 900_000)
            sc = _score_candidate_against_context(
                meta,
                px,
                ctx,
                artist=score_artist,
                topic_tokens=topic_toks,
            ) + 450_000.0
            _add_cand(sc, item)

    # 3. Direct Wikipedia topic media (e.g. Tipper Gore, Parents Music Resource Center, MTV, Rick Rubin)
    if topic_queries:
        topic_wiki_items = _search_wikipedia_topic_media(topic_queries, max_candidates=8)
        for item in topic_wiki_items:
            meta = item.get("_meta") or item.get("title", "")
            px = int(item.get("_px") or 900_000)
            sc = _score_candidate_against_context(
                meta,
                px,
                ctx,
                artist=score_artist,
                topic_tokens=topic_toks,
            ) + 750_000.0
            _add_cand(sc, item)

    # 4. Topic-driven Wikimedia Commons searches aligned to the scene's script (+ relaxed 2-word fallback)
    try:
        with httpx.Client(timeout=12.0, headers=WIKI_HEADERS, follow_redirects=True) as client:
            for q_idx, q in enumerate(queries[:4]):
                cq = q.strip()
                if not cq:
                    continue
                if project.artist.strip() and cq.lower() == project.artist.strip().lower():
                    cq = f'"{project.artist}" (band OR concert OR musician OR singer)'
                for item in _query_commons_images(client, cq, limit=8, allow_raster_svg=True):
                    sc = _score_candidate_against_context(
                        str(item.get("_meta") or item.get("title") or ""),
                        int(item.get("_px") or 800_000),
                        ctx,
                        artist=score_artist,
                        topic_tokens=topic_toks,
                    )
                    if q_idx == 0:
                        sc += 350_000.0
                    _add_cand(sc, item)
    except Exception as exc:
        logger.debug("Falha na busca de candidatos visuais da cena: %s", exc)

    ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
    return [item for _, item in ranked[:limit]]


def _aspect_dims_for_ai(aspect_ratio: str) -> tuple[int, int]:
    ratio = (aspect_ratio or "16:9").strip()
    if ratio == "9:16":
        return 768, 1344
    if ratio == "1:1":
        return 768, 768
    if ratio == "4:3":
        return 896, 672
    if ratio == "3:4":
        return 672, 896
    return 960, 540  # 16:9 crisp landscape for mockups


def _validate_and_save_ai_bytes(
    raw_bytes: bytes,
    out_path: Path,
    keep_square: bool = False,
    keep_raw: bool = False,
    trim_bottom_watermark: bool = False,
) -> bool:
    if not raw_bytes or len(raw_bytes) < 5_000:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_tmp = out_path.with_suffix(".ai.raw")
    try:
        raw_tmp.write_bytes(raw_bytes)
        with Image.open(raw_tmp) as im:
            im.verify()
        if trim_bottom_watermark:
            with Image.open(raw_tmp) as im:
                rgb_trim = im.convert("RGB")
                tw, th = rgb_trim.size
                rgb_trim.crop((0, 0, tw, max(120, int(round(th * 0.915))))).save(raw_tmp, format="PNG")
        if keep_square or keep_raw:
            with Image.open(raw_tmp) as im:
                rgb = im.convert("RGB")
                if out_path.suffix.lower() in {".jpg", ".jpeg"}:
                    rgb.save(out_path, format="JPEG", quality=93)
                else:
                    rgb.convert("RGBA").save(out_path, format="PNG")
            raw_tmp.unlink(missing_ok=True)
            return out_path.exists()
        fit_cover_9_16(raw_tmp, out_path)
        raw_tmp.unlink(missing_ok=True)
        return out_path.exists()
    except Exception:
        raw_tmp.unlink(missing_ok=True)
        return False


def _call_gemini_image_fast(
    prompt: str,
    api_key: str,
    aspect_ratio: str = "9:16",
    timeout_sec: float = 22.0,
) -> tuple[bytes | None, str]:
    """
    Call Gemini Nano Banana 2.1 Interactions API with zero endless retry hangs on Free Tier 429s.
    Returns `(image_bytes, error_reason)`.
    """
    clean_key = (api_key or "").strip()
    if not clean_key:
        return None, "missing_api_key"

    models_to_try: list[str] = []
    cfg_m = (get_image_model() or "gemini-nano-banana-2.1").strip()
    if cfg_m:
        models_to_try.append(cfg_m)
    if "gemini-3.1-flash-lite-image" not in models_to_try:
        models_to_try.append("gemini-3.1-flash-lite-image")

    last_err = ""
    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
            for model_name in models_to_try:
                payload = {
                    "model": model_name,
                    "input": [{"type": "text", "text": prompt}],
                    "response_mime_types": ["image/png"],
                    "generation_config": {
                        "image_config": {
                            "aspect_ratio": aspect_ratio or "16:9",
                            "image_size": "1K",
                        }
                    },
                }
                r = client.post(
                    "https://generativelanguage.googleapis.com/v1beta/interactions",
                    headers={
                        "x-goog-api-key": clean_key,
                        "Content-Type": "application/json",
                        "Api-Revision": "2026-05-20",
                    },
                    json=payload,
                )
                if r.status_code == 200:
                    data = r.json()
                    b64 = ""
                    out_img = data.get("output_image")
                    if isinstance(out_img, dict):
                        b64 = str(out_img.get("data") or "")
                    if not b64:
                        for item in data.get("outputs") or []:
                            if isinstance(item, dict) and item.get("type") == "image" and item.get("data"):
                                b64 = str(item["data"])
                                break
                    if b64:
                        return base64.b64decode(b64), ""
                    last_err = f"{model_name}:empty_image_payload"
                    continue

                err_txt = r.text[:260].lower()
                if r.status_code == 429:
                    last_err = f"{model_name}:429_quota_free_tier" if "limit: 0" in err_txt else f"{model_name}:429_rate_limit"
                    # If limit: 0 on free tier, don't stall trying more
                    if "limit: 0" in err_txt:
                        break
                elif r.status_code in {401, 403}:
                    return None, f"auth_{r.status_code}"
                else:
                    last_err = f"{model_name}:http_{r.status_code}"
    except Exception as exc:
        last_err = f"gemini_exc:{type(exc).__name__}"

    return None, last_err or "gemini_unavailable"


def _call_pollinations_flux_image(
    prompt: str,
    aspect_ratio: str = "16:9",
    seed: int | None = None,
    timeout_sec: float = 30.0,
) -> tuple[bytes | None, str]:
    """Zero-API-key Flux image generation fallback via Pollinations.ai."""
    w, h = _aspect_dims_for_ai(aspect_ratio)
    use_seed = seed if seed is not None else (int(hashlib.sha1(prompt.encode("utf-8", errors="ignore")).hexdigest()[:7], 16) % 9_000_000)
    short_prompt = " ".join((prompt or "").strip().split())[:420]
    encoded = urllib.parse.quote(short_prompt, safe="")
    urls = [
        f"https://image.pollinations.ai/prompt/{encoded}?width={w}&height={h}&nologo=true&model=flux&seed={use_seed}",
        f"https://image.pollinations.ai/prompt/{encoded}?width={w}&height={h}&nologo=true&seed={use_seed}",
    ]
    last_err = ""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "image/avif,image/webp,image/png,image/jpeg,*/*;q=0.8",
    }
    try:
        with httpx.Client(timeout=timeout_sec, headers=headers, follow_redirects=True) as client:
            for url in urls:
                try:
                    resp = client.get(url)
                    ctype = resp.headers.get("content-type", "").lower()
                    if resp.status_code == 200 and "image/" in ctype and len(resp.content) > 6_000:
                        return resp.content, ""
                    last_err = f"pollinations:http_{resp.status_code}"
                except Exception as exc:
                    last_err = f"pollinations:{type(exc).__name__}"
    except Exception as exc:
        last_err = f"pollinations_client:{type(exc).__name__}"
    return None, last_err or "pollinations_failed"


def generate_gemini_image(
    prompt: str,
    out_path: Path,
    api_key: str,
    aspect_ratio: str = "9:16",
    keep_square: bool = False,
    keep_raw: bool = False,
) -> bool:
    raw_bytes, _ = _call_gemini_image_fast(prompt=prompt, api_key=api_key, aspect_ratio=aspect_ratio)
    if raw_bytes and _validate_and_save_ai_bytes(raw_bytes, out_path, keep_square=keep_square, keep_raw=keep_raw):
        return True
    return False


def generate_ai_illustration(
    prompt: str,
    out_path: Path,
    api_key: str = "",
    aspect_ratio: str = "16:9",
    keep_raw: bool = True,
    seed: int | None = None,
) -> tuple[bool, str, str]:
    """
    Generate a contextual AI illustration cleanly:
    1. Tries Gemini Nano Banana 2.1 quickly (fails fast on 429 limit:0 without hanging).
    2. Automatically falls back to Zero-API Pollinations Flux (`image.pollinations.ai`).
    Returns `(ok, engine_used, detail)`.
    """
    gem_note = ""
    key = (api_key or "").strip() or get_gemini_api_key()
    if key:
        raw_gem, gem_err = _call_gemini_image_fast(prompt=prompt, api_key=key, aspect_ratio=aspect_ratio)
        if raw_gem and _validate_and_save_ai_bytes(raw_gem, out_path, keep_raw=keep_raw):
            return True, "gemini_nano_banana", "Gerado via Gemini Nano Banana 2.1"
        gem_note = gem_err

    raw_pol, pol_err = _call_pollinations_flux_image(prompt=prompt, aspect_ratio=aspect_ratio, seed=seed)
    if raw_pol and _validate_and_save_ai_bytes(raw_pol, out_path, keep_raw=keep_raw, trim_bottom_watermark=True):
        detail = "Gerado via Pollinations Flux (grátis)"
        if "429_quota_free_tier" in gem_note:
            detail = "Gerado via Pollinations Flux (Gemini Nano Banana com cota 0 no Free Tier)"
        return True, "pollinations_flux", detail

    return False, "none", f"{gem_note} | {pol_err}".strip(" |")


def _call_gemini_text_fast(prompt: str, api_key: str, timeout_sec: float = 6.5) -> str:
    """Quickly call Gemini Text (gemini-3.8-flash) without retry hangs; returns '' on 429/503/network."""
    clean_key = (api_key or "").strip()
    if not clean_key:
        return ""
    model_name = (get_text_model() or "gemini-3.8-flash").strip()
    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=True) as client:
            r = client.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={
                    "x-goog-api-key": clean_key,
                    "Content-Type": "application/json",
                    "Api-Revision": "2026-05-20",
                },
                json={"model": model_name, "input": prompt},
            )
            if r.status_code != 200:
                return ""
            data = r.json()
            for item in data.get("outputs") or []:
                if isinstance(item, dict) and item.get("text"):
                    return str(item["text"]).strip()
            if data.get("output_text"):
                return str(data["output_text"]).strip()
    except Exception as exc:
        logger.debug("Falha na chamada Pollinations text: %s", exc)
    return ""


def build_contextual_ai_overlay_prompt(scene: SceneItem, project: MusicStoryProject) -> str:
    """
    Build a vivid, text-free contextual AI illustration prompt aligned strictly to WHAT IS SPOKEN
    in `scene.narration_text` + `scene.title`, ideal for displaying inside Motion 2.0 Mockups.
    Tries Gemini Text quickly when configured; otherwise translates PT->EN & matches exact nouns.
    """
    custom = (getattr(scene, "ai_image_prompt", "") or "").strip()
    if len(custom) >= 12:
        if "no text" not in custom.lower():
            return f"{custom}, clean composition, no text, no letters, no watermark"
        return custom

    niche = getattr(project, "niche", "gaming_news")
    title = (scene.title or "").strip()
    narr = (scene.narration_text or "").strip()
    game = (project.artist or "GTA 6").strip()

    # Translate spoken narration into English for concrete Flux generation (Zero-API fallback)
    try:
        from app.gaming_radar import translate_to_en
        narr_en = translate_to_en(f"{title}. {narr[:220]}")
    except Exception:
        narr_en = ""

    # Optional Gemini Text prompt synthesis (fast fail on Free Tier quota / 503)
    gem_key = get_gemini_api_key()
    if gem_key and len(narr) >= 12:
        gem_instr = (
            f"Create ONE concise 28-to-42 word English image-generation prompt for a vertical short mockup card.\n"
            f"Topic: {project.song_title} ({game}).\n"
            f"Scene spoken narration (PT-BR): {narr[:240]}\n"
            f"Rules: Depict literally the concrete object/place/action mentioned in the narration. "
            f"No words, no letters, no logos, no watermarks, vibrant cinematic lighting. Output ONLY the prompt."
        )
        gem_out = _call_gemini_text_fast(gem_instr, gem_key)
        gem_clean = re.sub(r"[\r\n\"']+", " ", gem_out).strip()
        if 20 <= len(gem_clean) <= 360:
            if "no text" not in gem_clean.lower():
                gem_clean += ", zero text, no letters, no watermark"
            return gem_clean

    blob = f"{title} {narr} {narr_en} {scene.shot_intent} {project.song_title}".lower()

    if niche == "gaming_news":
        has_ia_word = bool(re.search(r"\b(?:deepfake|falso|fake|ia|inteligência artificial|hologram|clipe falso|montagem)\b", blob))
        if has_ia_word:
            subject = (
                "futuristic glowing cyber holographic display showing a split between wireframe digital glitch matrix "
                "and photoreal next-gen neon Miami Vice beach street, magenta and cyan rim lighting"
            )
        elif re.search(r"\b(?:cerveja|beer|donut|rosquinha|drink|lanch|bar)\b", blob):
            subject = (
                "sunny tropical Miami oceanfront boardwalk tiki bar counter with frosted glass mug of cold golden beer "
                "and fresh glazed pink sprinkled donut beside palm trees and ocean waves, photorealistic Rockstar-style lighting"
            )
        elif re.search(r"\b(?:revista|magazine|edge|artigo|entrevista|jornal|matéria)\b", blob):
            subject = (
                "dramatic studio macro photograph of a thick glossy open video game editorial magazine resting on a dark carbon desk "
                "surrounded by vibrant Vice City neon magenta and cyan reflections, shallow depth of field, zero readable text"
            )
        elif re.search(r"\b(?:ps5|playstation|xbox|series x|pc|fps|gráfico|engine|rage|desempenho|console|ray tracing)\b", blob):
            subject = (
                "sleek dark gaming studio showcase displaying matte black next-gen PS5 and Xbox style game controllers and curved display "
                "casting vibrant magenta and electric cyan reflections, photorealistic product shot"
            )
        elif re.search(r"\b(?:vazam|leak|rumor|servidor|insider|fórum|reddit|thread)\b", blob):
            subject = (
                "dimly lit investigative gaming desk with dual monitors displaying blurred glowing tropical coastal radar grid and amber alert indicators, "
                "high contrast neon magenta and cyan reflections"
            )
        elif re.search(r"\b(?:mapa|leonida|cidade|miami|vice|florida|pântano|ilha|praia)\b", blob):
            subject = (
                f"cinematic twilight aerial panorama of neon Vice City coastal skyline, palm trees, causeway bridges and sports cars "
                f"overlooking turquoise bay, next-gen open-world game atmosphere inspired by {game}"
            )
        elif re.search(r"\b(?:lucia|jason|protagonista|assalto|polícia|carro|persegui|prisão)\b", blob):
            subject = (
                "dramatic cinematic action still on rain-slicked neon Miami palm boulevard, classic vintage muscle car bathed in pink and cyan headlights, "
                "ultra-detailed next-gen open-world game aesthetic"
            )
        elif re.search(r"\b(?:preço|dólar|bilhão|mercado|adiamento|data|lançamento|take-two|novembro)\b", blob):
            subject = (
                "sleek luxury glowing calendar vault pedestal under golden Vice City palm sunset and vibrant neon magenta-cyan spotlights, "
                "blockbuster gaming industry editorial illustration"
            )
        else:
            conc = re.sub(r"\s+", " ", (narr_en or scene.visual_prompt or title or project.song_title)).strip()[:160]
            subject = (
                f"cinematic editorial keyframe illustration inspired by {game}, specifically depicting: {conc}, "
                "vibrant Vice City twilight magenta and cyan lighting, photoreal next-gen game aesthetic"
            )
        return (
            f"{subject}, dramatic lighting, ultra-detailed 8k digital illustration, sharp focus, "
            "clean framing, zero text, no words, no letters, no watermark, no logos"
        )

    # Music History niche
    era = (project.year or "1980").strip()
    genre = (project.genre or "rock").strip()
    conc = re.sub(r"\s+", " ", (narr_en or scene.visual_prompt or title or narr[:120])).strip()
    return (
        f"Atmospheric cinematic editorial illustration for a {era} {genre} documentary story about {project.artist}: "
        f"{conc}, warm analog 35mm film photography look, glowing stage spotlights, deep dramatic shadows, "
        "no text, no words, no letters, no watermark"
    )


def generate_procedural_scene_card(
    scene: SceneItem,
    project: MusicStoryProject,
    out_path: Path,
) -> Path:
    """Offline fallback: richly textured atmospheric music-history artwork card (1080x1920)."""
    seed_int = int(hashlib.sha256(f"{project.song_title}-{scene.index}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed_int)
    accent = _hex_to_rgb(project.accent_color)

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx = W * (0.3 + 0.4 * ((scene.index % 3) / 2.0))
    cy = H * (0.32 + 0.25 * ((scene.index % 2)))
    dist = np.sqrt(((xx - cx) / (W * 0.7)) ** 2 + ((yy - cy) / (H * 0.55)) ** 2)
    glow = np.exp(-2.2 * (dist ** 2))

    base = np.zeros((H, W, 3), dtype=np.float32)
    base[:, :, 0] = 10 + glow * accent[0] * 0.45
    base[:, :, 1] = 10 + glow * accent[1] * 0.35
    base[:, :, 2] = 16 + glow * accent[2] * 0.45

    noise = np.random.default_rng(seed_int).normal(0, 7.5, (H, W, 1)).astype(np.float32)
    base = np.clip(base + noise, 0, 255).astype(np.uint8)
    img = Image.fromarray(base, mode="RGB").convert("RGBA")
    draw = ImageDraw.Draw(img)

    ring_cx, ring_cy = W // 2, int(H * 0.43)
    for r in range(160, 480, 28):
        alpha = max(18, 95 - (r - 160) // 4)
        draw.ellipse(
            (ring_cx - r, ring_cy - r, ring_cx + r, ring_cy + r),
            outline=(accent[0], accent[1], accent[2], alpha),
            width=2,
        )

    for _ in range(45):
        ex = rng.randint(40, W - 40)
        ey = rng.randint(150, H - 150)
        er = rng.randint(2, 7)
        ea = rng.randint(70, 190)
        draw.ellipse((ex - er, ey - er, ex + er, ey + er), fill=(255, 190, 90, ea))

    card_w, card_h = 840, 420
    cx0 = (W - card_w) // 2
    cy0 = ring_cy - card_h // 2
    draw.rounded_rectangle(
        (cx0, cy0, cx0 + card_w, cy0 + card_h),
        radius=30,
        fill=(14, 14, 20, 210),
        outline=(accent[0], accent[1], accent[2], 190),
        width=3,
    )

    f_small = _load_font(34, bold=True)
    f_big = _load_font(60, bold=True)
    f_mid = _load_font(40, bold=False)

    tag_text = f"CAPÍTULO 0{scene.index} • {project.year or 'CLÁSSICO'}".upper()
    draw.text((W // 2, cy0 + 82), tag_text, fill=(accent[0], accent[1], accent[2], 255), font=f_small, anchor="mm")

    title_txt = (scene.title or project.song_title).upper()
    draw.text((W // 2, cy0 + 185), title_txt[:28], fill=(255, 255, 255, 255), font=f_big, anchor="mm")

    artist_txt = f"{project.artist} — {project.song_title}"
    draw.text((W // 2, cy0 + 290), artist_txt[:38], fill=(205, 210, 220, 235), font=f_mid, anchor="mm")

    img.convert("RGB").save(out_path, format="PNG")
    return out_path


def generate_reel2_centerpiece(project: MusicStoryProject, work_dir: Path, api_key: str) -> Path:
    """Create or prepare the central emblem/artwork (800x800 PNG) for Reel 2 ('Ouça a Música')."""
    out_png = work_dir / "reel2_centerpiece.png"
    if project.reel2_center_image and Path(project.reel2_center_image).exists():
        with Image.open(project.reel2_center_image) as im:
            im = im.convert("RGBA")
            im.thumbnail((760, 760), Image.Resampling.LANCZOS)
            im.save(out_png, format="PNG")
        return out_png

    if api_key and project.visual_engine in {"auto_hybrid", "gemini_image"}:
        prompt = (
            f"Iconic centered minimalist music emblem artwork on pure pitch-black background inspired by "
            f"the song '{project.song_title}' by '{project.artist}' ({project.genre}, {project.year}). "
            "High contrast dramatic graphic illustration, bold silhouette/vinyl/instrument emblem, clean edges, centered, no small clutter."
        )
        raw_sq = work_dir / "reel2_centerpiece_raw.png"
        if generate_gemini_image(prompt, raw_sq, api_key, aspect_ratio="1:1", keep_square=True):
            with Image.open(raw_sq) as im:
                im = im.convert("RGBA")
                im.thumbnail((740, 740), Image.Resampling.LANCZOS)
                im.save(out_png, format="PNG")
            raw_sq.unlink(missing_ok=True)
            return out_png

    # Free official album artwork via public iTunes Search API
    if project.visual_engine != "procedural_card" and fetch_itunes_artwork(
        project.artist, project.song_title, out_png
    ):
        return out_png

    wiki_tmp = work_dir / "reel2_wiki_tmp.png"
    if project.visual_engine != "procedural_card":
        cp_pool = _fetch_wikipedia_entity_images(project.artist, project.song_title, max_candidates=4)
        if search_wikimedia_image(
            f"{project.artist}",
            wiki_tmp,
            artist=project.artist,
            wiki_pool=cp_pool,
        ):
            with Image.open(wiki_tmp) as im:
                im = im.convert("RGBA")
                side = min(im.size)
                left = (im.width - side) // 2
                top = max(0, min(im.height - side, int((im.height - side) * 0.25)))
                sq = im.crop((left, top, left + side, top + side)).resize((700, 700), Image.Resampling.LANCZOS)
                mask = Image.new("L", (700, 700), 0)
                ImageDraw.Draw(mask).ellipse((8, 8, 692, 692), fill=255)
                sq.putalpha(mask)
                sq.save(out_png, format="PNG")
            wiki_tmp.unlink(missing_ok=True)
            return out_png

    accent = _hex_to_rgb(project.accent_color)
    size = 720
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    c = size // 2
    draw.ellipse((24, 24, size - 24, size - 24), fill=(18, 18, 24, 255), outline=(accent[0], accent[1], accent[2], 235), width=6)
    for r in range(110, 320, 14):
        draw.ellipse((c - r, c - r, c + r, c + r), outline=(65, 68, 78, 130), width=2)
    draw.ellipse((c - 108, c - 108, c + 108, c + 108), fill=(accent[0], accent[1], accent[2], 240))
    draw.ellipse((c - 22, c - 22, c + 22, c + 22), fill=(12, 12, 16, 255))

    f_band = _load_font(44, bold=True)
    initials = "".join(w[0] for w in project.artist.split()[:2]).upper() or "♪"
    draw.text((c, c - 2), initials, fill=(255, 255, 255, 255), font=f_band, anchor="mm")
    canvas.save(out_png, format="PNG")
    return out_png


def fetch_artist_live_gallery(
    project: MusicStoryProject,
    work_dir: Path,
    max_images: int = 5,
) -> list[Path]:
    """
    Collect distinct borderless 1080x1920 band / live-concert / stage photographs (Zero-API Wikimedia)
    to drive kinetic Musical Breaks and the multi-shot Finale when no local MP4 clips are attached.
    """
    gallery_dir = work_dir / "live_gallery"
    gallery_dir.mkdir(parents=True, exist_ok=True)
    for old_f in gallery_dir.glob("live_*.png"):
        old_f.unlink(missing_ok=True)
    paths: list[Path] = []
    seen_urls: set[str] = set()
    for sc in project.scenes:
        if sc.candidate_urls:
            seen_urls.add(sc.candidate_urls[0])

    if project.visual_engine != "procedural_card":
        genre_main = (project.genre.split("/")[0] if project.genre else "rock").strip()
        try:
            with httpx.Client(timeout=12.0, headers=WIKI_HEADERS, follow_redirects=True) as client:
                # 1. Verified Wikipedia article images for artist/band first!
                wiki_verified = _fetch_wikipedia_entity_images(
                    project.artist,
                    project.song_title,
                    seen_urls=seen_urls,
                    max_candidates=8,
                )
                for img_url in wiki_verified:
                    if len(paths) >= min(max_images, 4):
                        break
                    seen_urls.add(img_url)
                    try:
                        img_resp = client.get(img_url)
                        ctype = img_resp.headers.get("content-type", "")
                        if img_resp.status_code == 200 and "image/" in ctype and len(img_resp.content) > 12_000:
                            idx_num = len(paths) + 1
                            out_png = gallery_dir / f"live_{idx_num:02d}.png"
                            raw_p = gallery_dir / f"_raw_{idx_num:02d}.img"
                            raw_p.write_bytes(img_resp.content)
                            _fit_stage_fullbleed_9_16(raw_p, out_png)
                            raw_p.unlink(missing_ok=True)
                            paths.append(out_png)
                    except Exception as exc:
                        logger.debug("Falha ao baixar imagem da galeria (%s): %s", img_url, exc)
                        continue

                # 2. Supplement up to max_images with music-constrained Commons stage/band queries
                queries = [
                    f'"{project.artist}" (concert OR live OR band OR singer OR guitarist)',
                    f'"{genre_main}" concert guitarist stage spotlight',
                    "rock band live concert stage spotlight crowd",
                ]
                for q in queries:
                    if len(paths) >= max_images:
                        break
                    resp = client.get(
                        "https://commons.wikimedia.org/w/api.php",
                        params={
                            "action": "query",
                            "generator": "search",
                            "gsrnamespace": "6",
                            "gsrsearch": q,
                            "gsrlimit": "10",
                            "prop": "imageinfo",
                            "iiprop": "url|mime|size|extmetadata",
                            "iiurlwidth": "1200",
                            "format": "json",
                        },
                    )
                    if resp.status_code != 200 or "application/json" not in resp.headers.get("content-type", ""):
                        continue
                    pages = list(resp.json().get("query", {}).get("pages", {}).values())
                    cands: list[tuple[int, str]] = []
                    for p in pages:
                        infos = p.get("imageinfo") or []
                        if not infos:
                            continue
                        info = infos[0]
                        mime = info.get("mime", "")
                        w = int(info.get("width", 0))
                        h = int(info.get("height", 0))
                        img_url = info.get("thumburl") or info.get("url")
                        meta_blob = _extract_wikimedia_meta_blob(p, info)
                        if (
                            mime in {"image/jpeg", "image/png", "image/webp"}
                            and w >= 550
                            and h >= 380
                            and img_url
                            and _is_clean_wikimedia_url(img_url, meta_blob)
                            and img_url not in seen_urls
                        ):
                            cands.append((w * h, img_url))
                    cands.sort(reverse=True)
                    taken_from_q = 0
                    for _, img_url in cands:
                        if len(paths) >= max_images or taken_from_q >= 2:
                            break
                        seen_urls.add(img_url)
                        try:
                            img_resp = client.get(img_url)
                            ctype = img_resp.headers.get("content-type", "")
                            if img_resp.status_code == 200 and "image/" in ctype and len(img_resp.content) > 12_000:
                                idx_num = len(paths) + 1
                                out_png = gallery_dir / f"live_{idx_num:02d}.png"
                                raw_p = gallery_dir / f"_raw_{idx_num:02d}.img"
                                raw_p.write_bytes(img_resp.content)
                                _fit_stage_fullbleed_9_16(raw_p, out_png)
                                raw_p.unlink(missing_ok=True)
                                paths.append(out_png)
                                taken_from_q += 1
                        except Exception as exc:
                            logger.debug("Falha ao baixar imagem do Commons (%s): %s", img_url, exc)
                            continue
        except Exception as exc:
            logger.debug("Falha na coleta de galeria ao vivo: %s", exc)

    # Backfill cleanly from existing scene artwork or procedural stage cards so we always have 3+ frames
    if len(paths) < 3:
        for sc in project.scenes:
            if len(paths) >= 3:
                break
            if sc.image_path and Path(sc.image_path).exists():
                idx_num = len(paths) + 1
                out_png = gallery_dir / f"live_{idx_num:02d}.png"
                _fit_stage_fullbleed_9_16(Path(sc.image_path), out_png)
                paths.append(out_png)

    while len(paths) < 3:
        idx_num = len(paths) + 1
        out_png = gallery_dir / f"live_{idx_num:02d}.png"
        dummy_scene = SceneItem(
            index=idx_num,
            title=f"{project.artist} Ao Vivo",
            narration_text=project.song_title,
        )
        generate_procedural_scene_card(dummy_scene, project, out_png)
        paths.append(out_png)

    return paths


def prepare_project_visuals(
    project: MusicStoryProject,
    work_dir: Path,
    api_key_override: str | None = None,
) -> list[Path]:
    """
    Prepare visuals for Reel 1 / Shorts scenes (zero-API multi-source by default):
    - Respects existing scene.html_path / scene.video_path / scene.image_path
    - Dispatches to gaming_media when project.niche == "gaming_news"
    - `auto_free_media`: combines Archive.org real video B-roll on middle dramatic scenes + Wikimedia/Wikipedia archival stills + procedural fallback
    - `archive_org_video`: aggressively searches Archive.org archival footage first
    - `wikimedia_archive`: free Wikimedia Commons + Wikipedia PageImages
    - `auto_hybrid` / `gemini_image`: optionally invokes Gemini Nano Banana if key is set
    """
    if getattr(project, "niche", "music_history") == "gaming_news":
        from app.gaming_media import prepare_gaming_project_visuals

        return prepare_gaming_project_visuals(project, work_dir)
    scenes_dir = work_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    api_key = get_gemini_api_key(api_key_override)

    seen_urls: set[str] = set()
    result_paths: list[Path] = []
    mode = project.visual_engine
    yr_m = re.search(r"(19\d\d|20\d\d)", str(project.year or ""))
    song_yr = int(yr_m.group(1)) if yr_m else 1990

    wiki_media: list[dict[str, str]] = []
    wiki_artist_pool: list[str] = []
    if mode in {"wikimedia_archive", "auto_free_media", "archive_org_video", "auto_hybrid"}:
        wiki_media = _fetch_wikipedia_entity_media(
            project.artist,
            project.song_title,
            album=_infer_project_album(project),
            max_candidates=10,
        )
        wiki_artist_pool = [m["url"] for m in wiki_media if m.get("url")]

    def _pick_script_aligned_image(sc_item: SceneItem, target_png: Path, cand_urls_out: list[str]) -> bool:
        rich = search_scene_visual_candidates(
            sc_item,
            project,
            custom_query="",
            limit=8,
            preloaded_artist_media=wiki_media,
        )
        if rich:
            sc_item.candidate_items = rich[:8]
            cand_urls_out[:] = [str(c.get("url") or "") for c in rich[:6] if c.get("url")]
            ordered = [c for c in rich if c.get("url") and c["url"] not in seen_urls]
            ordered.extend([c for c in rich if c not in ordered])
            for c in ordered:
                cu = str(c.get("url") or "")
                if cu and apply_scene_image_url(cu, target_png):
                    seen_urls.add(cu)
                    return True
        return search_wikimedia_image(
            sc_item.search_query or f"{project.artist}",
            target_png,
            seen_urls=seen_urls,
            candidate_urls_out=cand_urls_out,
            artist=project.artist,
            wiki_pool=wiki_artist_pool,
            context_tokens=_context_tokens_for_scene(sc_item, project),
            topic_tokens=_context_topic_tokens_for_scene(sc_item, project),
        )

    for idx, scene in enumerate(project.scenes):
        out_png = scenes_dir / f"scene_{scene.index:02d}.png"
        out_mp4 = scenes_dir / f"scene_{scene.index:02d}.mp4"
        candidates: list[str] = []

        # Preserve agent-authored custom HTML scene if specified
        if scene.html_path and Path(scene.html_path).exists():
            if not out_png.exists():
                generate_procedural_scene_card(scene, project, out_png)
            scene.image_path = str(out_png)
            scene.asset_type = "html"
            result_paths.append(out_png)
            continue

        # Preserve custom/attached video if provided
        if scene.video_path and Path(scene.video_path).exists():
            if not out_png.exists():
                ok_vthumb = extract_video_frame_thumb(
                    Path(scene.video_path),
                    out_png,
                    seek_sec=float(getattr(scene, "video_start_sec", 0.0) or 0.0),
                    vertical_9_16=True,
                )
                if not ok_vthumb:
                    generate_procedural_scene_card(scene, project, out_png)
            scene.image_path = str(out_png)
            scene.asset_type = "video"
            result_paths.append(out_png)
            continue

        # Preserve uploaded/custom image
        if scene.image_path and Path(scene.image_path).exists():
            src = Path(scene.image_path)
            if src.resolve() != out_png.resolve():
                fit_cover_9_16(src, out_png)
            scene.image_path = str(out_png)
            scene.asset_type = "image"
            result_paths.append(out_png)
            continue

        fetched_video = False
        fetched_img = False

        # Try Archive.org Prelinger B-roll on Scene 4 ONLY for pre-1975 vintage songs (prevents 1950s clips on 80s/90s/2000s tracks)
        want_archive_video = (
            mode == "archive_org_video"
            or (mode == "auto_free_media" and idx == 3 and song_yr < 1975 and bool(scene.video_query or scene.search_query))
        )
        if want_archive_video:
            vq = (scene.video_query or scene.search_query or f"{project.genre.split('/')[0]} concert").strip()
            fetched_video = search_archive_org_video(vq, out_mp4, seen_urls=seen_urls, candidate_urls_out=candidates)
            if fetched_video:
                scene.video_path = str(out_mp4)
                scene.asset_type = "video"
                fetched_img = extract_video_frame_thumb(out_mp4, out_png, seek_sec=1.0, vertical_9_16=True)

        # In two_reels_combo final CTA scene ("Confira o próximo Reel"), showcase official iTunes Album Cover
        if (
            not fetched_img
            and project.video_mode == "two_reels_combo"
            and idx == len(project.scenes) - 1
            and mode != "procedural_card"
        ):
            art_tmp = work_dir / "scenes" / "_itunes_cover_tmp.png"
            if fetch_itunes_artwork(project.artist, project.song_title, art_tmp):
                fit_cover_9_16(art_tmp, out_png)
                art_tmp.unlink(missing_ok=True)
                fetched_img = True

        if not fetched_img:
            if mode == "gemini_image" and api_key:
                fetched_img = generate_gemini_image(scene.visual_prompt or f"{project.artist} {project.song_title}", out_png, api_key)
            elif mode in {"wikimedia_archive", "auto_free_media", "archive_org_video"}:
                fetched_img = _pick_script_aligned_image(scene, out_png, candidates)
            elif mode == "auto_hybrid":
                if api_key and (idx % 2 == 0):
                    fetched_img = generate_gemini_image(scene.visual_prompt, out_png, api_key)
                    if not fetched_img:
                        fetched_img = _pick_script_aligned_image(scene, out_png, candidates)
                else:
                    fetched_img = _pick_script_aligned_image(scene, out_png, candidates)
                    if not fetched_img and api_key:
                        fetched_img = generate_gemini_image(scene.visual_prompt, out_png, api_key)

        if not fetched_img and idx == len(project.scenes) - 1 and mode != "procedural_card":
            art_tmp = work_dir / "scenes" / "_itunes_cover_tmp.png"
            if fetch_itunes_artwork(project.artist, project.song_title, art_tmp):
                fit_cover_9_16(art_tmp, out_png)
                art_tmp.unlink(missing_ok=True)
                fetched_img = True

        if not fetched_img:
            generate_procedural_scene_card(scene, project, out_png)

        if scene.asset_type == "auto":
            scene.asset_type = "video" if fetched_video else "image"
        scene.candidate_urls = candidates[:6]
        if candidates and not scene.candidate_items:
            scene.candidate_items = [
                {
                    "url": u,
                    "thumb_url": u,
                    "title": f"{scene.title or project.song_title}",
                    "source": "Wikimedia / Wikipedia",
                    "kind": "image",
                }
                for u in candidates[:6]
            ]
        scene.image_path = str(out_png)
        result_paths.append(out_png)

    return result_paths
