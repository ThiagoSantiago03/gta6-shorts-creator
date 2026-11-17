from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger("gta6_shorts_analyzer")

from app.config import get_gemini_api_key, get_text_model
from app.models import ShortReferenceAnalysis


YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_youtube_video_id(url: str) -> str | None:
    raw = url.strip()
    if YOUTUBE_ID_RE.match(raw):
        return raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if "youtu.be" in host:
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate if YOUTUBE_ID_RE.match(candidate) else None
    if "youtube.com" in host or "youtube-nocookie.com" in host:
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live", "v"}:
            candidate = parts[1]
            return candidate if YOUTUBE_ID_RE.match(candidate) else None
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            candidate = qs["v"][0]
            return candidate if YOUTUBE_ID_RE.match(candidate) else None
    return None


def canonical_watch_url(url: str) -> tuple[str, str | None]:
    vid = extract_youtube_video_id(url)
    if vid:
        return f"https://www.youtube.com/watch?v={vid}", vid
    return url.strip(), None


def fetch_youtube_oembed(watch_url: str) -> dict[str, str]:
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            resp = client.get(
                "https://www.youtube.com/oembed",
                params={"url": watch_url, "format": "json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "title": str(data.get("title", "")),
                    "channel": str(data.get("author_name", "")),
                }
    except Exception as exc:
        logger.debug("Falha na chamada oEmbed do YouTube para %s: %s", watch_url, exc)
    return {"title": "", "channel": ""}


def fetch_youtube_transcript_fallback(video_id: str) -> str:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception:
        return ""

    langs = ["pt", "pt-BR", "pt-PT", "en", "es"]
    try:
        # Support youtube-transcript-api >= 1.0 (instance .fetch) and < 1.0 (classmethod .get_transcript)
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            entries = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
            return " ".join(str(item.get("text", "")).strip() for item in entries if item.get("text"))
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=langs)
        return " ".join(str(getattr(snippet, "text", "")).strip() for snippet in fetched).strip()
    except Exception:
        try:
            if hasattr(YouTubeTranscriptApi, "list_transcripts"):
                tlist = YouTubeTranscriptApi.list_transcripts(video_id)
                for t in tlist:
                    entries = t.fetch()
                    return " ".join(str(item.get("text", "")).strip() for item in entries if item.get("text"))
            else:
                api = YouTubeTranscriptApi()
                tlist = api.list(video_id)
                for t in tlist:
                    fetched = t.fetch()
                    return " ".join(str(getattr(s, "text", "")).strip() for s in fetched).strip()
        except Exception:
            return ""
    return ""


def _analyze_with_gemini_video(url: str, title_hint: str, channel_hint: str, api_key: str) -> ShortReferenceAnalysis | None:
    try:
        from google import genai
    except Exception:
        return None

    client = genai.Client(api_key=api_key)
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "channel": {"type": "string"},
            "hook_style": {"type": "string"},
            "narrative_structure": {"type": "string"},
            "visual_style": {"type": "string"},
            "subtitle_style": {"type": "string"},
            "pacing_notes": {"type": "string"},
            "extracted_facts": {"type": "array", "items": {"type": "string"}},
            "transcript": {"type": "string"},
            "suggested_prompt_guidance": {"type": "string"},
        },
        "required": [
            "hook_style",
            "narrative_structure",
            "visual_style",
            "subtitle_style",
            "pacing_notes",
            "extracted_facts",
            "transcript",
            "suggested_prompt_guidance",
        ],
    }

    prompt = (
        "Analise este vídeo curto (YouTube Short) como referência para um sistema que cria "
        "Reels sobre História da Música (parte 1: fatos/história, parte 2: trecho da música). "
        "Responda em Português do Brasil com foco prático para replicar estilo, ritmo e storytelling.\n"
        f"Metadados conhecidos (se útil): título='{title_hint}', canal='{channel_hint}'.\n"
        "Descreva: como é o gancho nos primeiros 3 segundos (hook_style), a estrutura da narrativa, "
        "o estilo visual (fotos, recortes, visualizador, cores, movimento), o estilo das legendas na tela, "
        "o ritmo/duração (pacing_notes), os fatos históricos mencionados, a transcrição resumida ou literal do áudio, "
        "e recomendações objetivas para gerar novos vídeos inspirados nesse formato (suggested_prompt_guidance)."
    )

    video_content = {"type": "video", "uri": url}
    try:
        interaction = client.interactions.create(
            model=get_text_model(),
            input=[{"type": "text", "text": prompt}, video_content],
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
        )
        data = json.loads(interaction.output_text)
        _, vid = canonical_watch_url(url)
        return ShortReferenceAnalysis(
            source_url=url,
            video_id=vid,
            title=data.get("title") or title_hint,
            channel=data.get("channel") or channel_hint,
            hook_style=data.get("hook_style", ""),
            narrative_structure=data.get("narrative_structure", ""),
            visual_style=data.get("visual_style", ""),
            subtitle_style=data.get("subtitle_style", ""),
            pacing_notes=data.get("pacing_notes", ""),
            extracted_facts=data.get("extracted_facts", []),
            transcript=data.get("transcript", ""),
            suggested_prompt_guidance=data.get("suggested_prompt_guidance", ""),
            analysis_method="gemini_youtube_video",
        )
    except Exception:
        return None


def analyze_short_url(url: str, use_gemini: bool = True, api_key_override: str | None = None) -> ShortReferenceAnalysis:
    watch_url, vid = canonical_watch_url(url)
    oembed = fetch_youtube_oembed(watch_url)
    title_hint = oembed.get("title", "")
    channel_hint = oembed.get("channel", "")

    api_key = get_gemini_api_key(api_key_override)
    if use_gemini and api_key and vid:
        result = _analyze_with_gemini_video(watch_url, title_hint, channel_hint, api_key)
        if result is not None:
            return result

    transcript = fetch_youtube_transcript_fallback(vid) if vid else ""
    facts: list[str] = []
    if transcript:
        # Split sentences for quick reference facts
        sentences = [s.strip() for s in re.split(r"[.!?]+", transcript) if len(s.strip()) > 15]
        facts = sentences[:6]

    has_speech = bool(transcript)
    words = transcript.split() if transcript else []
    first_hook = " ".join(words[:18]) if words else ""
    last_cta = " ".join(words[-18:]) if len(words) > 25 else ""
    has_list = bool(re.search(r"\b(primeiro|segundo|terceiro|cinco|três|3 motivos|5 coisas)\b", transcript, re.I))
    has_twist = bool(re.search(r"\b(mas|só que|porém|detalhe|ninguém)\b", transcript, re.I))
    arch_hint = (
        "Lista numerada com Open Loop ('e o último...')"
        if has_list
        else ("Contraste direto ('mas tem um detalhe...')" if has_twist else "Storytelling rápido com fatos concretos e números")
    )
    visual_guess = (
        "Estilo viral PT-BR com cortes a cada 6-8s, números na tela e legendas dinâmicas."
        if has_speech
        else "Formato provável de áudio/visualizador (sem legendas detectadas)."
    )

    return ShortReferenceAnalysis(
        source_url=url.strip(),
        video_id=vid,
        title=title_hint or (f"YouTube Short ({vid})" if vid else url.strip()),
        channel=channel_hint,
        hook_style=(f"Abertura coloquial (~{min(18, len(words))}w): \"{first_hook}...\"" if first_hook else "Gancho direto nos primeiros 3 segundos"),
        narrative_structure=arch_hint + (f" | Fechamento: \"...{last_cta}\"" if last_cta else ""),
        visual_style=visual_guess,
        subtitle_style="Legendas centrais dinâmicas com destaque colorido por palavra-chave" if has_speech else "Sem legendas narradas",
        pacing_notes=(f"~{len(words)} palavras em ritmo falado ágil (+14%)" if words else "~45 a 58 segundos em 9:16"),
        extracted_facts=facts,
        transcript=transcript or "(Sem transcrição pública disponível no YouTube; configure GEMINI_API_KEY para análise multimodal de vídeo e áudio.)",
        suggested_prompt_guidance=(
            f"Referência '{title_hint}': use linguagem falada PT-BR ('tá', 'pra', 'se liga'), números concretos e pergunta A vs B no final."
            if title_hint
            else "Use gancho imediato nos primeiros 3s, linguagem falada direta e pergunta binária nos comentários."
        ),
        analysis_method="oembed_and_transcript_fallback",
    )


def analyze_local_video_file(file_path: Path, api_key_override: str | None = None) -> ShortReferenceAnalysis:
    api_key = get_gemini_api_key(api_key_override)
    if not api_key:
        return ShortReferenceAnalysis(
            source_url=str(file_path),
            title=file_path.name,
            visual_style="Arquivo de vídeo local detectado. Adicione GEMINI_API_KEY para extrair análise multimodal automática de cena/áudio.",
            analysis_method="local_metadata_only",
        )

    from google import genai

    client = genai.Client(api_key=api_key)
    uploaded = client.files.upload(file=str(file_path))

    schema = {
        "type": "object",
        "properties": {
            "hook_style": {"type": "string"},
            "narrative_structure": {"type": "string"},
            "visual_style": {"type": "string"},
            "subtitle_style": {"type": "string"},
            "pacing_notes": {"type": "string"},
            "extracted_facts": {"type": "array", "items": {"type": "string"}},
            "transcript": {"type": "string"},
            "suggested_prompt_guidance": {"type": "string"},
        },
        "required": [
            "hook_style",
            "narrative_structure",
            "visual_style",
            "subtitle_style",
            "pacing_notes",
            "extracted_facts",
            "transcript",
            "suggested_prompt_guidance",
        ],
    }

    prompt = (
        "Analise este vídeo curto local como referência de formato para Reels sobre História da Música. "
        "Responda em Português do Brasil extraindo gancho, estrutura, estilo visual, legendas, ritmo, fatos e transcrição."
    )
    interaction = client.interactions.create(
        model=get_text_model(),
        input=[{"type": "text", "text": prompt}, uploaded],
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": schema,
        },
    )
    data = json.loads(interaction.output_text)
    return ShortReferenceAnalysis(
        source_url=str(file_path),
        title=file_path.name,
        hook_style=data.get("hook_style", ""),
        narrative_structure=data.get("narrative_structure", ""),
        visual_style=data.get("visual_style", ""),
        subtitle_style=data.get("subtitle_style", ""),
        pacing_notes=data.get("pacing_notes", ""),
        extracted_facts=data.get("extracted_facts", []),
        transcript=data.get("transcript", ""),
        suggested_prompt_guidance=data.get("suggested_prompt_guidance", ""),
        analysis_method="gemini_local_video_upload",
    )
