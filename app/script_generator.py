from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("gta6_script_generator")

from app.config import get_gemini_api_key, get_text_model
from app.models import SceneItem
from app.music_researcher import (
    research_music_history_free,
    synthesize_script_from_dossier,
)


STYLE_PRESET_EFFECTS = {
    "documental_rock": ["zoom_in", "ken_burns_duotone", "vintage_grain", "pan_right", "pan_left", "zoom_out"],
    "viral_pop_historia": ["zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in", "zoom_out"],
    "zine_punk_underground": ["ken_burns_duotone", "vintage_grain", "pan_left", "ken_burns_duotone", "zoom_in", "vintage_grain"],
    "vinil_minimal_audio": ["zoom_out", "ken_burns_duotone", "vintage_grain", "pan_left", "zoom_out", "ken_burns_duotone"],
}


def _research_song_with_gemini(
    song_title: str,
    artist: str,
    year: str,
    genre: str,
    topic_angle: str,
    reference_notes: str,
    free_dossier_summary: str,
    api_key: str,
) -> str:
    from google import genai

    client = genai.Client(api_key=api_key)
    prompt = (
        f"Complemente o dossiê histórico sobre a música '{song_title}' de '{artist}'"
        + (f" ({year})" if year else "")
        + (f", gênero {genre}" if genre else "")
        + ".\n"
    )
    if free_dossier_summary:
        prompt += f"\nFatos já obtidos da Wikipedia/MusicBrainz:\n{free_dossier_summary[:1800]}\n"
    if topic_angle:
        prompt += f"\nÂngulo desejado: {topic_angle}\n"
    if reference_notes:
        prompt += f"\nNotas de referência de Shorts:\n{reference_notes}\n"
    prompt += (
        "\nConfirme em tópicos curtos em PT-BR: bastidores curiosos da gravação, polêmicas/censura, "
        "virada comercial ou clipe/MTV, e elementos visuais icônicos."
    )

    interaction = client.interactions.create(
        model=get_text_model(),
        input=prompt,
        tools=[{"type": "google_search"}],
    )
    return interaction.output_text or ""


def generate_story_script(
    song_title: str,
    artist: str,
    year: str = "",
    genre: str = "Rock / História da Música",
    topic_angle: str = "",
    reference_notes: str = "",
    script_engine: str = "free_research",
    use_gemini_research: bool | None = None,
    style_preset: str = "documental_rock",
    video_mode: str = "single_all_in_one",
    music_break_sec: float = 3.2,
    opening_mode: str = "lyric_hook",
    api_key_override: str | None = None,
) -> dict[str, Any]:
    # Support legacy boolean parameter seamlessly
    if use_gemini_research is True and script_engine == "free_research":
        script_engine = "gemini_with_research"
    elif use_gemini_research is False and script_engine == "gemini_with_research":
        script_engine = "free_research"

    free_dossier: dict[str, Any] = {}
    if script_engine in {"free_research", "gemini_with_research"}:
        try:
            free_dossier = research_music_history_free(
                song_title=song_title,
                artist=artist,
                year=year,
                genre=genre,
            )
        except Exception:
            free_dossier = {}

    api_key = get_gemini_api_key(api_key_override)
    if script_engine == "gemini_with_research" and api_key:
        try:
            from google import genai

            free_text = free_dossier.get("summary_text", "")
            dossier = ""
            try:
                dossier = _research_song_with_gemini(
                    song_title=song_title,
                    artist=artist,
                    year=free_dossier.get("resolved_year") or year,
                    genre=free_dossier.get("resolved_genre") or genre,
                    topic_angle=topic_angle,
                    reference_notes=reference_notes,
                    free_dossier_summary=free_text,
                    api_key=api_key,
                )
            except Exception:
                dossier = free_text

            client = genai.Client(api_key=api_key)
            schema = {
                "type": "object",
                "properties": {
                    "resolved_year": {"type": "string"},
                    "resolved_genre": {"type": "string"},
                    "hook_title": {"type": "string"},
                    "reel2_title": {"type": "string"},
                    "reel2_subtitle": {"type": "string"},
                    "instagram_caption": {"type": "string"},
                    "hashtags": {"type": "array", "items": {"type": "string"}},
                    "scenes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "shot_intent": {"type": "string"},
                                "narration_text": {"type": "string"},
                                "visual_prompt": {"type": "string"},
                                "search_query": {"type": "string"},
                                "video_query": {"type": "string"},
                                "effect": {
                                    "type": "string",
                                    "enum": [
                                        "zoom_in",
                                        "zoom_out",
                                        "pan_left",
                                        "pan_right",
                                        "ken_burns_duotone",
                                        "vintage_grain",
                                    ],
                                },
                                "highlight_words": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "quote_text": {"type": "string"},
                            },
                            "required": [
                                "title",
                                "narration_text",
                                "visual_prompt",
                                "search_query",
                                "video_query",
                                "effect",
                                "highlight_words",
                            ],
                        },
                    },
                },
                "required": [
                    "resolved_year",
                    "resolved_genre",
                    "hook_title",
                    "reel2_title",
                    "reel2_subtitle",
                    "instagram_caption",
                    "hashtags",
                    "scenes",
                ],
            }

            resolved_yr = free_dossier.get("resolved_year") or year
            resolved_gr = free_dossier.get("resolved_genre") or genre
            lyrics_info = free_dossier.get("lyrics") or {}
            break_picks = lyrics_info.get("break_picks") or []
            lyrics_hint = ""
            if break_picks:
                lyrics_hint = (
                    "\nTrechos cantados com timestamps reais (LRCLIB) para usar como GANCHO DA LETRA:\n"
                    + "\n".join(
                        f"- Cena {i + 1} Respiro @{bp['start_sec']}s ({bp['duration_sec']}s): \"{bp['lyric_en']}\" = {bp.get('lyric_pt', '')}"
                        for i, bp in enumerate(break_picks)
                    )
                    + "\nConecte o final de cada fala ao próximo verso para o respiro soar como continuação inteligente!"
                )
            mode_rule = (
                "1. Formato VÍDEO ÚNICO INTEGRADO COM GANCHOS NA LETRA: o vídeo alternará trechos reais cantados da música com a narração dos fatos.\n"
                "   Crie exatamente 5 cenas dinâmicas (~95 a 120 palavras ao todo). Se `opening_mode='lyric_hook'`, a Cena 1 deve ter ~8-12 palavras puxando direto para o primeiro verso; a Cena 5 convida para o clímax final e Destaques."
                if video_mode == "single_all_in_one"
                else "1. O vídeo terá 2 partes no Instagram do usuário: este roteiro é a PARTE 1 (fatos). A última cena convida para ouvir a música no próximo Reel / Destaques."
            )
            prompt = f"""Você é roteirista especialista em Reels de História da Música para Instagram (formato viral em Português do Brasil).

Crie o roteiro completo para:
- Música: {song_title}
- Artista / Banda: {artist}
- Ano identificado: {resolved_yr or 'confirmar'}
- Gênero: {resolved_gr}
- Abertura: {opening_mode}
- Preset visual/ritmo: {style_preset}
- Foco pedido: {topic_angle or 'A história real, bastidores surpreendentes e impacto cultural da música'}

{f"Dossiê pesquisado:{chr(10)}{dossier or free_text}" if (dossier or free_text) else ""}
{lyrics_hint}
{f"Referências de Shorts:{chr(10)}{reference_notes}" if reference_notes else ""}

REGRAS OBRIGATÓRIAS:
{mode_rule}
2. Cena 1 com gancho forte nos primeiros 3 segundos.
3. `hook_title`: título curto em MAIÚSCULAS (máx. 6 palavras).
4. `search_query` (inglês): consulta para fotos históricas no Wikimedia/Wikipedia.
5. `video_query` (inglês): consulta curta para clipes públicos de arquivo no Archive.org (ex: `"1980s rock concert crowd"`, `"analog studio tape reel"`).
6. `highlight_words`: 1 a 3 palavras exatas de `narration_text`.
7. `quote_text` (opcional): frase/citação curta marcante para exibir sobre uma cena se houver citação real.
"""

            interaction = client.interactions.create(
                model=get_text_model(),
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": schema,
                },
            )
            data = json.loads(interaction.output_text)
            raw_gem = data.get("scenes", [])
            scenes = []
            for idx, s in enumerate(raw_gem):
                bp = break_picks[idx] if (video_mode == "single_all_in_one" and idx < len(break_picks) and idx < len(raw_gem) - 1) else None
                brk_dur = (
                    float(bp["duration_sec"])
                    if bp and music_break_sec > 0
                    else (round(max(0.0, music_break_sec), 2) if video_mode == "single_all_in_one" and idx < len(raw_gem) - 1 else 0.0)
                )
                scenes.append(
                    SceneItem(
                        index=idx + 1,
                        title=s.get("title", f"Cena {idx + 1}"),
                        shot_intent=s.get("shot_intent", f"beat_{idx + 1}"),
                        narration_text=s.get("narration_text", "").strip(),
                        visual_prompt=s.get("visual_prompt", "").strip(),
                        search_query=s.get("search_query", f"{artist} {song_title}").strip(),
                        video_query=s.get("video_query", f"{artist} live stage").strip(),
                        effect=s.get("effect", "zoom_in"),
                        highlight_words=s.get("highlight_words", []),
                        quote_text=s.get("quote_text", "").strip(),
                        music_break_after_sec=brk_dur,
                        music_break_audio_start_sec=float(bp["start_sec"]) if bp else None,
                        music_break_lyric_en=str(bp.get("lyric_en") or "") if bp else "",
                        music_break_lyric_pt=str(bp.get("lyric_pt") or "") if bp else "",
                    )
                )
            narration_full = " ".join(s.narration_text for s in scenes if s.narration_text)
            return {
                "year": data.get("resolved_year") or resolved_yr,
                "genre": data.get("resolved_genre") or resolved_gr,
                "hook_title": data.get("hook_title") or f"A HISTÓRIA DE {song_title.upper()}",
                "reel2_title": data.get("reel2_title") or f"{song_title} — {artist}",
                "reel2_subtitle": data.get("reel2_subtitle") or f"{data.get('resolved_year') or resolved_yr or 'Clássico'} • {data.get('resolved_genre') or resolved_gr}",
                "instagram_caption": data.get("instagram_caption", ""),
                "hashtags": data.get("hashtags", ["#historiadamusica", "#musica", "#curiosidades"]),
                "scenes": scenes,
                "narration_full": narration_full,
                "lyrics_cues": (lyrics_info.get("cues") or [])[:80],
                "finale_audio_start_sec": lyrics_info.get("finale_start"),
                "research_summary": (free_dossier.get("summary_text") or dossier)[:1400],
                "engine_used": "gemini_with_research",
            }
        except Exception as exc:
            logger.warning("Falha na geração de roteiro via Gemini, usando síntese local: %s", exc)

    # Zero-API Path: grounded Wikipedia PT/EN + MusicBrainz + iTunes + LRCLIB synthesis
    if free_dossier:
        synth = synthesize_script_from_dossier(
            free_dossier,
            topic_angle=topic_angle,
            reference_notes=reference_notes,
            video_mode=video_mode,
            music_break_sec=music_break_sec,
            opening_mode=opening_mode,
        )
        effects = STYLE_PRESET_EFFECTS.get(style_preset, STYLE_PRESET_EFFECTS["documental_rock"])
        scenes = [
            SceneItem(
                index=idx + 1,
                title=item["title"],
                shot_intent=item.get("shot_intent", ""),
                narration_text=item["narration_text"],
                visual_prompt=item["visual_prompt"],
                search_query=item["search_query"],
                video_query=item.get("video_query", ""),
                effect=effects[idx % len(effects)],  # type: ignore[arg-type]
                highlight_words=item["highlight_words"],
                quote_text=item.get("quote_text", ""),
                music_break_after_sec=float(item.get("music_break_after_sec", 0.0)),
                music_break_audio_start_sec=item.get("music_break_audio_start_sec"),
                music_break_lyric_en=item.get("music_break_lyric_en", ""),
                music_break_lyric_pt=item.get("music_break_lyric_pt", ""),
            )
            for idx, item in enumerate(synth["raw_scenes"])
        ]
        narration_full = " ".join(s.narration_text for s in scenes)
        return {
            "year": synth["year"],
            "genre": synth["genre"],
            "hook_title": synth["hook_title"],
            "reel2_title": synth["reel2_title"],
            "reel2_subtitle": synth["reel2_subtitle"],
            "instagram_caption": synth["instagram_caption"],
            "hashtags": synth["hashtags"],
            "scenes": scenes,
            "narration_full": narration_full,
            "lyrics_cues": synth.get("lyrics_cues", []),
            "finale_audio_start_sec": synth.get("finale_audio_start_sec"),
            "research_summary": synth.get("research_summary", ""),
            "engine_used": synth["engine_used"],
        }

    # Offline fallback template (no internet & no API)
    display_year = year or "Clássico"
    angle_text = topic_angle if topic_angle else f"o contexto por trás de {song_title}, de {artist}"
    effects = STYLE_PRESET_EFFECTS.get(style_preset, STYLE_PRESET_EFFECTS["documental_rock"])
    raw_scenes = [
        {
            "title": "O Gancho",
            "shot_intent": "hook_establish",
            "narration_text": f"Muita gente já ouviu {song_title}, de {artist}, mas poucos conhecem a história real por trás dessa faixa.",
            "visual_prompt": f"Dramatic vertical 9:16 editorial concert photography atmosphere inspired by {artist}, moody stage spotlight, smoke, vintage film grain, no text",
            "search_query": f"{artist} concert live",
            "video_query": "rock concert stage crowd",
            "highlight_words": [song_title, artist, "história", "real"],
        },
        {
            "title": "Contexto da Época",
            "shot_intent": "context_origin",
            "narration_text": f"Quando surgiu em {display_year}, o cenário musical passava por grandes transformações e {angle_text} chamou atenção imediata.",
            "visual_prompt": f"Vintage {display_year} music studio analog tape reel, retro magazine collage mood, dark dramatic lighting, vertical 9:16, no text",
            "search_query": "analog recording studio vintage tape",
            "video_query": "recording studio tape reel",
            "highlight_words": [display_year, "transformações", "atenção"],
        },
        {
            "title": "A Composição e o Som",
            "shot_intent": "story_conflict",
            "narration_text": f"Com uma identidade sonora marcante no estilo {genre}, cada acorde e frase foi pensado para causar impacto logo nos primeiros segundos.",
            "visual_prompt": "Close-up cinematic shot of vintage electric guitar, microphone and vinyl record in dramatic warm rim light, vertical 9:16, no text",
            "search_query": "electric guitar stage dark",
            "video_query": "electric guitar amplifier stage",
            "highlight_words": ["identidade", "impacto", "segundos"],
        },
        {
            "title": "Bastidores e Virada",
            "shot_intent": "climax_reveal",
            "narration_text": f"Nos bastidores, a música ganhou força entre fãs e rádios até se tornar uma das faixas mais lembradas do repertório de {artist}.",
            "visual_prompt": "Retro crowd silhouette at night music festival, neon and amber stage lights, nostalgic documentary photo, vertical 9:16, no text",
            "search_query": "rock concert crowd silhouette",
            "video_query": "concert audience night crowd",
            "highlight_words": ["força", "fãs", artist],
        },
        {
            "title": "Legado e Clímax" if video_mode == "single_all_in_one" else "Legado",
            "shot_intent": "cta_handoff" if video_mode == "single_all_in_one" else "legacy_resolution",
            "narration_text": (
                f"Décadas depois, {song_title} segue atravessando gerações. Aumenta o som, curte esse trecho final e salva nos Destaques!"
                if video_mode == "single_all_in_one"
                else f"Décadas depois, {song_title} continua atravessando gerações e mostrando como a história da música vive em cada riff e melodia."
            ),
            "visual_prompt": "Glowing vinyl record spinning on classic turntable in moody dark room, warm amber embers, vertical 9:16, no text",
            "search_query": "vinyl record turntable close up",
            "video_query": "vinyl record turntable close up",
            "highlight_words": ["gerações", song_title, "Destaques"] if video_mode == "single_all_in_one" else ["gerações", "história", "música"],
        },
    ]
    if video_mode != "single_all_in_one":
        raw_scenes.append(
            {
                "title": "Ouça no Próximo Reel",
                "shot_intent": "cta_handoff",
                "narration_text": f"Quer sentir essa energia na prática? Confira o próximo Reel para ouvir {song_title} e já salve este combo nos Destaques!",
                "visual_prompt": "Minimalist dark stage with glowing soundwaves and subtle fire embers, atmospheric album showcase aesthetic, vertical 9:16, no text",
                "search_query": f"{artist}",
                "video_query": "stage lights concert glow",
                "highlight_words": ["próximo", "Reel", "Destaques"],
            }
        )

    brk = round(max(0.0, float(music_break_sec)), 2) if video_mode == "single_all_in_one" else 0.0
    scenes = [
        SceneItem(
            index=idx + 1,
            title=item["title"],
            shot_intent=item["shot_intent"],
            narration_text=item["narration_text"],
            visual_prompt=item["visual_prompt"],
            search_query=item["search_query"],
            video_query=item["video_query"],
            effect=effects[idx % len(effects)],  # type: ignore[arg-type]
            highlight_words=item["highlight_words"],
            music_break_after_sec=(brk if idx < len(raw_scenes) - 1 else 0.0),
        )
        for idx, item in enumerate(raw_scenes)
    ]
    narration_full = " ".join(s.narration_text for s in scenes)
    tag_artist = re.sub(r"[^a-zA-Z0-9]", "", artist.lower()) or "musica"
    tag_song = re.sub(r"[^a-zA-Z0-9]", "", song_title.lower()) or "classico"

    return {
        "year": display_year,
        "genre": genre,
        "hook_title": f"POR TRÁS DE {song_title.upper()}",
        "reel2_title": f"{song_title} — {artist}",
        "reel2_subtitle": f"{display_year} • {genre}",
        "instagram_caption": (
            f"A história por trás de {song_title}, de {artist}! 🎵🔥\n\n"
            "👉 Parte 1: Os fatos e curiosidades que marcaram essa faixa.\n"
            "🎧 Parte 2: Assista ao próximo Reel (ou acesse os Destaques) para ouvir a música!\n\n"
            "Você já conhecia essa história? Salva nos favoritos e comenta qual música quer ver aqui."
        ),
        "hashtags": [
            "#historiadamusica",
            "#curiosidadesmusicais",
            f"#{tag_artist}",
            f"#{tag_song}",
            "#reelsmusica",
            "#musicahistoria",
            "#rock",
            "#dicasdemusica",
        ],
        "scenes": scenes,
        "narration_full": narration_full,
        "research_summary": "Template offline local (sem internet/API).",
        "engine_used": "offline_template",
    }
