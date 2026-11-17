from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("gta6_studio")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.audio_processor import (
    compute_waveform_peaks,
    find_best_highlight_start,
    get_audio_duration,
)
from app.config import (
    GTA6_INPUTS_DIR,
    INPUTS_DIR,
    OUTPUTS_DIR,
    PROJECTS_DIR,
    STATIC_DIR,
    get_ffmpeg_exe,
    get_gemini_api_key,
    get_image_model,
    get_text_model,
    get_tts_model,
    save_gemini_api_key,
)
from app.gaming_media import (
    apply_overlay_image_url,
    auto_blend_gaming_clips_and_images,
    auto_select_gaming_soundtrack,
    download_news_video_segment,
    fit_overlay_editorial_png,
    generate_scene_ai_image_asset,
    index_trailer_scenes,
    list_gaming_audio_candidates,
    list_gta6_library_videos,
    prepare_gaming_project_visuals,
)
from app.gaming_radar import fetch_daily_gaming_radar
from app.gaming_scriptwriter import generate_gaming_script
from app.html_renderer import find_headless_browser
from app.lyrics_engine import fetch_synced_lyrics, select_lyric_break_cues
from app.models import (
    AnalyzeShortsRequest,
    CreateProjectRequest,
    MusicStoryProject,
    SceneItem,
)
from app.script_generator import generate_story_script
from app.shorts_analyzer import analyze_short_url
from app.thumbnail_engine import THUMBNAIL_STYLES, generate_project_thumbnail
from app.tts_engine import EDGE_VOICES_PT_BR, GEMINI_TTS_VOICES
from app.video_renderer import render_full_project, render_single_scene_preview_mp4
from app.visual_assets import (
    apply_scene_image_url,
    extract_video_frame_thumb,
    fit_cover_9_16,
    generate_reel2_centerpiece,
    prepare_project_visuals,
    search_scene_visual_candidates,
)


app = FastAPI(title="Estúdio Reels — História da Música")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return cleaned[:40] or "projeto"


def _project_dir(project_id: str) -> Path:
    if not re.match(r"^[a-zA-Z0-9_-]+$", project_id):
        raise HTTPException(status_code=400, detail="ID de projeto inválido.")
    return PROJECTS_DIR / project_id


def _output_dir(project_id: str) -> Path:
    if not re.match(r"^[a-zA-Z0-9_-]+$", project_id):
        raise HTTPException(status_code=400, detail="ID de projeto inválido.")
    return OUTPUTS_DIR / project_id


def _load_project(project_id: str) -> MusicStoryProject:
    pdir = _project_dir(project_id)
    pfile = pdir / "project.json"
    bak_file = pdir / "project.json.bak"
    if not pfile.exists() and not bak_file.exists():
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")

    if pfile.exists():
        try:
            content = pfile.read_text(encoding="utf-8")
            if content.strip():
                return MusicStoryProject.model_validate_json(content)
        except Exception as exc:
            logger.warning("Falha ao ler %s, tentando backup: %s", pfile, exc)

    if bak_file.exists():
        try:
            content = bak_file.read_text(encoding="utf-8")
            if content.strip():
                return MusicStoryProject.model_validate_json(content)
        except Exception as exc:
            logger.warning("Falha ao ler backup %s: %s", bak_file, exc)

    raise HTTPException(status_code=500, detail="Arquivo de projeto corrompido e sem backup válido.")


def _save_project(project: MusicStoryProject) -> MusicStoryProject:
    project.updated_at = datetime.now(timezone.utc).isoformat()
    pdir = _project_dir(project.id)
    pdir.mkdir(parents=True, exist_ok=True)
    target_file = pdir / "project.json"
    bak_file = pdir / "project.json.bak"
    tmp_file = pdir / f"project.json.tmp.{uuid.uuid4().hex[:6]}"

    tmp_file.write_text(
        project.model_dump_json(indent=2),
        encoding="utf-8",
    )

    if target_file.exists() and target_file.stat().st_size > 0:
        try:
            shutil.copy2(target_file, bak_file)
        except Exception as exc:
            logger.warning("Falha ao criar backup %s: %s", bak_file, exc)

    tmp_file.replace(target_file)
    return project


def _set_job(project_id: str, state: str, progress: int, message: str, error: str | None = None) -> None:
    with JOBS_LOCK:
        JOBS[project_id] = {
            "project_id": project_id,
            "state": state,
            "progress": progress,
            "message": message,
            "error": error,
        }


class ApiKeyBody(BaseModel):
    api_key: str


class AttachInputAudioBody(BaseModel):
    filename: str
    auto_detect_highlight: bool = True


class AttachInputVideoBody(BaseModel):
    filename: str


class ImportAgentScriptBody(BaseModel):
    script_json: dict[str, Any]


class SearchCandidatesBody(BaseModel):
    query: str = ""
    limit: int = 8


class SelectCandidateBody(BaseModel):
    kind: str = "url"
    url: str = ""
    video_url: str = ""
    direct_video: bool = False
    clip_path: str = ""
    video_start_sec: float = 0.0
    target_role: str = "primary"  # "primary" | "overlay"


class AutoBlendBody(BaseModel):
    force_reassign: bool = True
    include_hook_scene: bool = True


class GenerateAiSceneImageBody(BaseModel):
    custom_prompt: str = ""
    target: str = "overlay"  # "overlay" | "background"


class EnrichAiVisualsBody(BaseModel):
    overwrite_existing: bool = False
    include_hook_scene: bool = True


def _invalidate_audio_cache(pdir: Path) -> None:
    audio_dir = pdir / "audio"
    if audio_dir.exists():
        (audio_dir / "waveform.json").unlink(missing_ok=True)
        (audio_dir / "preview_stream.m4a").unlink(missing_ok=True)


def _clear_scene_html_override(pdir: Path, target: SceneItem) -> None:
    if target.html_path:
        try:
            hp = Path(target.html_path).resolve()
            if pdir.resolve() in hp.parents and hp.name == "hook_scene.html":
                hp.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Falha ao remover html_path de cena: %s", exc)
    if target.index == 1:
        (pdir / "hook_scene.html").unlink(missing_ok=True)
    target.html_path = None


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    idx = STATIC_DIR / "index.html"
    return HTMLResponse(idx.read_text(encoding="utf-8"))


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    key = get_gemini_api_key()
    masked = f"{key[:6]}...{key[-4:]}" if len(key) >= 12 else ("configurada" if key else "")
    audio_exts = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
    video_exts = {".mp4", ".mov", ".webm", ".mkv"}
    img_exts = {".png", ".jpg", ".jpeg", ".webp"}
    input_audios = sorted(
        f.name for f in INPUTS_DIR.iterdir() if f.is_file() and f.suffix.lower() in (audio_exts | video_exts)
    )
    gta6_audios = (
        sorted(
            f"gta6/{f.name}"
            for f in GTA6_INPUTS_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in audio_exts
        )
        if GTA6_INPUTS_DIR.exists()
        else []
    )
    all_input_audios = gta6_audios + input_audios
    gta6_videos = (
        sorted(
            f"gta6/{f.name}"
            for f in GTA6_INPUTS_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in video_exts
        )
        if GTA6_INPUTS_DIR.exists()
        else []
    )
    input_videos = gta6_videos + sorted(
        f.name for f in INPUTS_DIR.iterdir() if f.is_file() and f.suffix.lower() in video_exts
    )
    input_images = sorted(
        f.name for f in INPUTS_DIR.iterdir() if f.is_file() and f.suffix.lower() in img_exts
    )
    return {
        "has_gemini_key": bool(key),
        "masked_gemini_key": masked,
        "has_headless_browser": bool(find_headless_browser()),
        "text_model": get_text_model(),
        "image_model": get_image_model(),
        "tts_model": get_tts_model(),
        "edge_voices": EDGE_VOICES_PT_BR,
        "gemini_voices": GEMINI_TTS_VOICES,
        "input_audios": all_input_audios,
        "gta6_audios": gta6_audios,
        "input_videos": input_videos,
        "input_images": input_images,
        "gta6_clips": list_gta6_library_videos(),
        "gta6_inputs_dir": str(GTA6_INPUTS_DIR),
        "inputs_dir": str(INPUTS_DIR),
        "outputs_dir": str(OUTPUTS_DIR),
    }


@app.post("/api/config/apikey")
def set_api_key(body: ApiKeyBody) -> dict[str, Any]:
    save_gemini_api_key(body.api_key)
    key = get_gemini_api_key()
    masked = f"{key[:6]}...{key[-4:]}" if len(key) >= 12 else ("configurada" if key else "")
    return {"has_gemini_key": bool(key), "masked_gemini_key": masked}


@app.get("/api/gaming/radar")
def get_gaming_radar(query: str = "GTA 6", geo: str = "BR") -> dict[str, Any]:
    items = fetch_daily_gaming_radar(query=query.strip() or "GTA 6", geo=geo.strip() or "BR")
    return {
        "query": query.strip() or "GTA 6",
        "geo": geo.strip() or "BR",
        "items": [it.model_dump() for it in items],
    }


@app.get("/api/gaming/clips")
def get_gaming_clips() -> dict[str, Any]:
    return {
        "dir": str(GTA6_INPUTS_DIR),
        "clips": list_gta6_library_videos(),
    }


@app.post("/api/gaming/index-clips")
def index_gaming_clips(force: bool = False) -> dict[str, Any]:
    cards: list[dict[str, Any]] = []
    if GTA6_INPUTS_DIR.exists():
        video_exts = {".mp4", ".mov", ".webm", ".mkv"}
        vids = [p for p in sorted(GTA6_INPUTS_DIR.iterdir()) if p.is_file() and p.suffix.lower() in video_exts]
        for vp in vids[:6]:
            cards.extend(index_trailer_scenes(vp, max_clips=12, force=force))
    return {
        "clips": list_gta6_library_videos(),
        "cards": cards,
    }


@app.post("/api/gaming/upload-clip")
async def upload_gaming_clip(file: UploadFile = File(...)) -> dict[str, Any]:
    GTA6_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    orig = Path(file.filename or "gta6_clip.mp4")
    ext = orig.suffix.lower() or ".mp4"
    if ext not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Formato não suportado (use MP4, MOV, WEBM ou MKV).")
    safe_stem = _slugify(orig.stem) or "gta6-clip"
    dest = GTA6_INPUTS_DIR / f"{safe_stem}{ext}"
    if dest.exists():
        dest = GTA6_INPUTS_DIR / f"{safe_stem}-{uuid.uuid4().hex[:4]}{ext}"
    dest.write_bytes(await file.read())
    cards = index_trailer_scenes(dest, max_clips=12, force=True)
    return {
        "filename": dest.name,
        "path": str(dest),
        "clips": list_gta6_library_videos(),
        "cards": cards,
    }


@app.get("/api/gaming/clip-thumb")
def get_gaming_clip_thumb(file: str) -> FileResponse:
    thumbs_dir = (GTA6_INPUTS_DIR / "_thumbs").resolve()
    candidate = (thumbs_dir / Path(file).name).resolve()
    if candidate.parent != thumbs_dir or not candidate.exists():
        raise HTTPException(status_code=404, detail="Miniatura do trailer não encontrada.")
    return FileResponse(candidate, media_type="image/png")


@app.post("/api/shorts/analyze")
def analyze_shorts(req: AnalyzeShortsRequest) -> dict[str, Any]:
    urls = [u.strip() for u in req.urls if u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="Informe pelo menos uma URL de YouTube Short.")
    analyses = [analyze_short_url(url, use_gemini=req.use_gemini) for url in urls[:8]]
    combined_notes = []
    for i, a in enumerate(analyses, start=1):
        combined_notes.append(
            f"Short {i} ({a.title or a.source_url}):\n"
            f"- Gancho: {a.hook_style}\n"
            f"- Estrutura: {a.narrative_structure}\n"
            f"- Visual: {a.visual_style}\n"
            f"- Legendas: {a.subtitle_style}\n"
            f"- Guia: {a.suggested_prompt_guidance}"
        )
    return {
        "analyses": [a.model_dump() for a in analyses],
        "combined_notes": "\n\n".join(combined_notes),
    }


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    items: list[MusicStoryProject] = []
    for pdir in PROJECTS_DIR.iterdir():
        pfile = pdir / "project.json"
        if pfile.exists():
            try:
                items.append(MusicStoryProject.model_validate_json(pfile.read_text(encoding="utf-8")))
            except Exception:
                continue
    items.sort(key=lambda x: x.updated_at, reverse=True)
    return [p.model_dump() for p in items]


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    return _load_project(project_id).model_dump()


@app.get("/api/projects/{project_id}/packed", response_class=PlainTextResponse)
def get_packed_timeline(project_id: str) -> PlainTextResponse:
    packed = _project_dir(project_id) / "project_packed.md"
    if not packed.exists():
        raise HTTPException(status_code=404, detail="Resumo packed ainda não gerado (renderize primeiro).")
    return PlainTextResponse(packed.read_text(encoding="utf-8"))


class BatchDailyRequest(BaseModel):
    count: int = 5
    query: str = "GTA 6"
    geo: str = "BR"


@app.post("/api/automation/batch-daily")
def create_batch_daily(req: BatchDailyRequest) -> dict[str, Any]:
    items = fetch_daily_gaming_radar(query=req.query.strip() or "GTA 6", geo=req.geo.strip() or "BR")
    if not items:
        raise HTTPException(status_code=404, detail="Nenhuma pauta encontrada no radar.")

    selected = items[: max(1, min(req.count, 10))]
    created_projects = []
    failures = []

    for item in selected:
        try:
            topic_title = item.title.strip()
            pid = f"gta6-{_slugify(topic_title)}-{uuid.uuid4().hex[:6]}"
            g_data = generate_gaming_script(
                topic_title=topic_title,
                game_name="GTA 6",
                topic_angle=item.suggested_angle or "",
                source_url=item.url or "",
                fact_confidence=item.confidence or "Notícia / Imprensa",
                reference_notes="",
                script_engine="free_research",
                accent_color="#00F0FF",
                viral_archetype="auto",
                speech_rate="+14%",
            )
            resolved_topic = str(g_data.get("resolved_topic") or topic_title)
            scenes_objs = [
                s if isinstance(s, SceneItem) else SceneItem(**s)
                for s in g_data["scenes"]
            ]
            project = MusicStoryProject(
                id=pid,
                niche="gaming_news",
                title=resolved_topic,
                game="GTA 6",
                song_title=resolved_topic,
                artist="GTA 6",
                year=g_data.get("resolved_year", "2026"),
                genre="Games / GTA 6",
                topic_angle=str(g_data.get("resolved_angle") or item.suggested_angle or ""),
                topic_source_url=item.url or "",
                fact_confidence=item.confidence or "Notícia / Imprensa",
                max_duration_sec=58.0,
                speech_rate="+14%",
                viral_archetype=str(g_data.get("viral_archetype") or "auto"),
                youtube_title=g_data["youtube_title"],
                youtube_description=g_data["youtube_description"],
                youtube_tags=g_data["youtube_tags"],
                tiktok_caption=g_data["tiktok_caption"],
                hook_title=g_data["hook_title"],
                narration_full=g_data["narration_full"],
                scenes=scenes_objs,
                instagram_caption=g_data["instagram_caption"],
                hashtags=g_data["hashtags"],
                accent_color="#00F0FF",
                video_mode="single_all_in_one",
                opening_mode="lyric_hook",
                overlay_style="clean_reels",
                intro_music_sec=0.0,
                music_break_sec=0.0,
                finale_music_sec=0.25,
                style_preset="vice_city_neon",
                subtitle_style="vice_city",
                tts_engine="edge_tts",
                voice_name="pt-BR-AntonioNeural",
                status="draft (automacao_diaria)",
            )
            auto_track, auto_start = auto_select_gaming_soundtrack(project)
            if auto_track is not None and auto_track.exists():
                project.audio_file = str(auto_track)
                project.audio_clip_start = auto_start
            try:
                auto_blend_gaming_clips_and_images(project, _project_dir(project.id), force_reassign=False)
            except Exception as exc_blend:
                logger.warning("Falha ao auto-mesclar mídias para projeto '%s': %s", project.id, exc_blend)
            _save_project(project)
            created_projects.append(project.model_dump())
        except Exception as exc:
            item_title = getattr(item, "title", "desconhecido")
            logger.exception("Falha ao gerar projeto do lote diário para '%s': %s", item_title, exc)
            failures.append({
                "title": item_title,
                "error": str(exc),
            })

    return {
        "ok": True,
        "count": len(created_projects),
        "failures_count": len(failures),
        "projects": created_projects,
        "failures": failures,
    }


@app.post("/api/projects")
def create_project(req: CreateProjectRequest) -> dict[str, Any]:
    if req.niche == "gaming_news" or req.title or not req.song_title:
        topic_title = (req.title or req.song_title or "").strip()
        game_name = (req.game or req.artist or "GTA 6").strip()
        if not topic_title:
            raise HTTPException(status_code=400, detail="Informe a pauta ou título do vídeo de Games / GTA 6.")
        pid = f"{_slugify(game_name)}-{_slugify(topic_title)}-{uuid.uuid4().hex[:6]}"
        g_accent = req.accent_color if (req.accent_color and req.accent_color != "#FF3B30") else "#00F0FF"
        g_style = "vice_city_neon" if req.style_preset in {"documental_rock", "vice_city_neon"} else req.style_preset
        g_subs = "vice_city" if req.subtitle_style in {"viral_pop", "vice_city"} else req.subtitle_style
        max_cap = min(58.0, float(req.max_duration_sec or 58.0))
        sp_rate = (req.speech_rate or "+14%").strip() or "+14%"
        v_arch = (getattr(req, "viral_archetype", "auto") or "auto").strip()

        g_data = generate_gaming_script(
            topic_title=topic_title,
            game_name=game_name,
            topic_angle=req.topic_angle.strip(),
            source_url=req.topic_source_url.strip(),
            fact_confidence=(req.fact_confidence or "Notícia / Imprensa").strip(),
            reference_notes=req.reference_notes.strip(),
            script_engine=req.script_engine,
            accent_color=g_accent,
            viral_archetype=v_arch,
            speech_rate=sp_rate,
        )
        resolved_topic = str(g_data.get("resolved_topic") or topic_title)
        resolved_angle = str(g_data.get("resolved_angle") or req.topic_angle.strip())
        scenes_objs = [
            s if isinstance(s, SceneItem) else SceneItem(**s)
            for s in g_data["scenes"]
        ]
        project = MusicStoryProject(
            id=pid,
            niche="gaming_news",
            title=resolved_topic,
            game=game_name,
            song_title=resolved_topic,
            artist=game_name,
            year=g_data["resolved_year"],
            genre=g_data["resolved_genre"],
            topic_angle=resolved_angle,
            topic_source_url=req.topic_source_url.strip(),
            fact_confidence=(req.fact_confidence or "Notícia / Imprensa").strip(),
            max_duration_sec=max_cap,
            speech_rate=sp_rate,
            viral_archetype=str(g_data.get("viral_archetype") or v_arch),
            youtube_title=g_data["youtube_title"],
            youtube_description=g_data["youtube_description"],
            youtube_tags=g_data["youtube_tags"],
            tiktok_caption=g_data["tiktok_caption"],
            hook_title=g_data["hook_title"],
            narration_full=g_data["narration_full"],
            scenes=scenes_objs,
            instagram_caption=g_data["instagram_caption"],
            hashtags=g_data["hashtags"],
            accent_color=g_accent,
            video_mode="single_all_in_one",
            opening_mode="lyric_hook",
            overlay_style=req.overlay_style,
            intro_music_sec=0.0,
            music_break_sec=0.0,
            finale_music_sec=0.25,
            style_preset=g_style,
            subtitle_style=g_subs,
            tts_engine=req.tts_engine,
            voice_name=req.voice_name,
            visual_engine=req.visual_engine,
            reel2_renderer=req.reel2_renderer,
            html_hook_card=req.html_hook_card,
            beat_sync=False,
            audio_export_mode=req.audio_export_mode,
            reference_shorts=req.reference_shorts,
            reference_notes=req.reference_notes.strip(),
            research_summary=g_data.get("research_summary", ""),
            news_media_manifest=g_data.get("news_media_manifest") or {},
            reel2_title=g_data["reel2_title"],
            reel2_subtitle=g_data["reel2_subtitle"],
            reel2_visual_style=req.reel2_visual_style,
            status=f"draft ({g_data.get('engine_used', 'free_gaming_radar')})",
        )
        # Auto-select & position best local Suno / rock-synth soundtrack if available
        auto_track, auto_start = auto_select_gaming_soundtrack(project)
        if auto_track is not None and auto_track.exists():
            project.audio_file = str(auto_track)
            project.audio_clip_start = auto_start
        # Auto-distribute trailer/library clips & news overlay images across scenes immediately
        try:
            auto_blend_gaming_clips_and_images(project, _project_dir(project.id), force_reassign=False)
        except Exception as exc:
            logger.warning("Falha ao auto-mesclar mídias para projeto %s: %s", project.id, exc)
        _save_project(project)
        return project.model_dump()

    if not req.song_title.strip() or not req.artist.strip():
        raise HTTPException(status_code=400, detail="Informe o nome da música e do artista/banda.")

    pid = f"{_slugify(req.artist)}-{_slugify(req.song_title)}-{uuid.uuid4().hex[:6]}"
    script_data = generate_story_script(
        song_title=req.song_title.strip(),
        artist=req.artist.strip(),
        year=req.year.strip(),
        genre=req.genre.strip(),
        topic_angle=req.topic_angle.strip(),
        reference_notes=req.reference_notes.strip(),
        script_engine=req.script_engine,
        use_gemini_research=req.use_gemini_research,
        style_preset=req.style_preset,
        video_mode=req.video_mode,
        opening_mode=req.opening_mode,
        music_break_sec=req.music_break_sec,
    )

    project = MusicStoryProject(
        id=pid,
        song_title=req.song_title.strip(),
        artist=req.artist.strip(),
        year=script_data["year"],
        genre=script_data["genre"],
        topic_angle=req.topic_angle.strip(),
        hook_title=script_data["hook_title"],
        narration_full=script_data["narration_full"],
        scenes=script_data["scenes"],
        instagram_caption=script_data["instagram_caption"],
        hashtags=script_data["hashtags"],
        accent_color=req.accent_color,
        video_mode=req.video_mode,
        opening_mode=req.opening_mode,
        overlay_style=req.overlay_style,
        intro_music_sec=req.intro_music_sec,
        music_break_sec=req.music_break_sec,
        finale_music_sec=req.finale_music_sec,
        finale_audio_start_sec=script_data.get("finale_audio_start_sec"),
        lyrics_cues=script_data.get("lyrics_cues") or [],
        style_preset=req.style_preset,
        subtitle_style=req.subtitle_style,
        tts_engine=req.tts_engine,
        voice_name=req.voice_name,
        visual_engine=req.visual_engine,
        reel2_renderer=req.reel2_renderer,
        html_hook_card=req.html_hook_card,
        beat_sync=req.beat_sync,
        audio_export_mode=req.audio_export_mode,
        audio_clip_duration=req.audio_clip_duration,
        reference_shorts=req.reference_shorts,
        reference_notes=req.reference_notes.strip(),
        research_summary=script_data.get("research_summary", ""),
        reel2_title=script_data["reel2_title"],
        reel2_subtitle=script_data["reel2_subtitle"],
        reel2_visual_style=req.reel2_visual_style,
        status=f"draft ({script_data['engine_used']})",
    )
    _save_project(project)
    return project.model_dump()


@app.post("/api/projects/import-json")
def import_agent_project(body: ImportAgentScriptBody) -> dict[str, Any]:
    """Importa um roteiro JSON gerado pelo Agente (chat) diretamente para edição/renderização sem gasto de API."""
    data = dict(body.script_json)
    song = str(data.get("song_title") or "Música").strip()
    artist = str(data.get("artist") or "Artista").strip()
    pid = str(data.get("id") or f"{_slugify(artist)}-{_slugify(song)}-{uuid.uuid4().hex[:6]}")
    if not re.match(r"^[a-zA-Z0-9_-]+$", pid):
        pid = f"{_slugify(artist)}-{uuid.uuid4().hex[:6]}"
    data["id"] = pid
    video_mode = data.get("video_mode", "single_all_in_one")
    default_brk = float(data.get("music_break_sec", 3.2) or 3.2)
    if "scenes" in data and isinstance(data["scenes"], list):
        raw_scenes = data["scenes"]
        built_scenes = []
        for i, s in enumerate(raw_scenes):
            if isinstance(s, dict):
                sc_copy = {k: v for k, v in s.items() if k != "index"}
                if video_mode == "single_all_in_one" and "music_break_after_sec" not in sc_copy and i < len(raw_scenes) - 1:
                    sc_copy["music_break_after_sec"] = default_brk
                built_scenes.append(SceneItem(index=i + 1, **sc_copy).model_dump())
            else:
                built_scenes.append(s)
        data["scenes"] = built_scenes
    data.setdefault("status", "draft (agent_imported)")
    proj = MusicStoryProject.model_validate(data)
    proj.narration_full = " ".join(s.narration_text.strip() for s in proj.scenes if s.narration_text.strip())
    _save_project(proj)
    return proj.model_dump()


class RegenerateGamingScriptBody(BaseModel):
    viral_archetype: str = "auto"
    topic_angle: str | None = None
    script_engine: str = "free_research"


@app.post("/api/projects/{project_id}/regenerate-script")
def regenerate_gaming_project_script(project_id: str, body: RegenerateGamingScriptBody) -> dict[str, Any]:
    project = _load_project(project_id)
    if project.niche != "gaming_news":
        raise HTTPException(status_code=400, detail="Regeneração viral está disponível apenas para projetos de Games.")
    angle = body.topic_angle.strip() if body.topic_angle is not None else project.topic_angle
    g_data = generate_gaming_script(
        topic_title=project.song_title,
        game_name=project.artist or "GTA 6",
        topic_angle=angle,
        source_url=project.topic_source_url,
        fact_confidence=project.fact_confidence or "Notícia / Imprensa",
        reference_notes=project.reference_notes,
        script_engine=body.script_engine or "free_research",
        accent_color=project.accent_color or "#FF2E93",
        viral_archetype=body.viral_archetype or "auto",
        speech_rate=project.speech_rate or "+14%",
    )
    project.scenes = [s if isinstance(s, SceneItem) else SceneItem(**s) for s in g_data["scenes"]]
    project.narration_full = g_data["narration_full"]
    project.hook_title = g_data["hook_title"]
    project.viral_archetype = str(g_data.get("viral_archetype") or body.viral_archetype)
    project.youtube_title = g_data["youtube_title"]
    project.youtube_description = g_data["youtube_description"]
    project.tiktok_caption = g_data["tiktok_caption"]
    project.research_summary = g_data.get("research_summary", project.research_summary)
    if g_data.get("news_media_manifest"):
        project.news_media_manifest = g_data["news_media_manifest"]
    project.status = f"draft ({g_data.get('engine_used', 'free_viral')})"
    _save_project(project)
    return project.model_dump()


class TogglePostedBody(BaseModel):
    is_posted: bool | None = None
    platform: str = "youtube"
    url: str = ""
    notes: str = ""


@app.post("/api/projects/{project_id}/toggle-posted")
def toggle_project_posted(project_id: str, body: TogglePostedBody = TogglePostedBody()) -> dict[str, Any]:
    project = _load_project(project_id)
    new_state = (not project.is_posted) if body.is_posted is None else bool(body.is_posted)
    project.is_posted = new_state
    if new_state:
        project.posted_at = datetime.now(timezone.utc).isoformat()
        if body.platform:
            project.posted_platform = body.platform
        if body.url:
            project.posted_url = body.url.strip()
        if body.notes:
            project.posted_notes = body.notes.strip()
    else:
        project.posted_at = None
        project.posted_url = ""
    _save_project(project)
    return project.model_dump()


@app.put("/api/projects/{project_id}")
def update_project(project_id: str, updated: MusicStoryProject) -> dict[str, Any]:
    if updated.id != project_id:
        raise HTTPException(status_code=400, detail="ID incompatível.")
    existing = _load_project(project_id)
    updated.outputs = existing.outputs
    updated.qa_report = existing.qa_report
    if updated.posted_at is None and existing.posted_at is not None:
        updated.is_posted = existing.is_posted
        updated.posted_at = existing.posted_at
        updated.posted_platform = existing.posted_platform
        updated.posted_url = existing.posted_url
        updated.posted_notes = existing.posted_notes
    updated.narration_full = " ".join(
        s.narration_text.strip() for s in updated.scenes if s.narration_text.strip()
    )
    _save_project(updated)
    return updated.model_dump()


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, str]:
    pdir = _project_dir(project_id)
    odir = _output_dir(project_id)
    if pdir.exists():
        shutil.rmtree(pdir, ignore_errors=True)
    if odir.exists():
        shutil.rmtree(odir, ignore_errors=True)
    return {"deleted": project_id}


def _refresh_project_lyrics_sync(
    project: MusicStoryProject,
    actual_duration: float | None = None,
    overwrite_existing: bool = False,
) -> tuple[bool, int, str]:
    """
    Query sibling .lrc sidecar or free LRCLIB (matched to `actual_duration` when audio is attached),
    populate `project.lyrics_cues` and align Break audio start cues + PT-BR lyric translations.
    """
    audio_p = Path(project.audio_file) if project.audio_file and Path(project.audio_file).exists() else None
    lyr_data = fetch_synced_lyrics(
        project.song_title,
        project.artist,
        target_duration_sec=actual_duration,
        local_audio_path=audio_p,
    )
    cues = lyr_data.get("cues") or []
    source = str(lyr_data.get("source") or "none")
    if not cues:
        return False, 0, source

    project.lyrics_cues = cues[:80]
    track_dur = actual_duration or lyr_data.get("duration") or None
    n_breaks = max(1, len(project.scenes) - 1)
    picks, fin_start = select_lyric_break_cues(
        cues=cues,
        song_title=project.song_title,
        track_dur=track_dur,
        n_breaks=n_breaks,
        finale_sec=project.finale_music_sec,
    )
    for idx, sc in enumerate(project.scenes[:-1]):
        if idx >= len(picks):
            break
        bp = picks[idx]
        if overwrite_existing or sc.music_break_audio_start_sec is None:
            sc.music_break_audio_start_sec = float(bp["start_sec"])
            if bp.get("duration_sec") and sc.music_break_after_sec > 0:
                sc.music_break_after_sec = round(min(6.0, max(2.4, float(bp["duration_sec"]))), 2)
        if overwrite_existing or not sc.music_break_lyric_en:
            sc.music_break_lyric_en = str(bp.get("lyric_en") or "")
        if overwrite_existing or not sc.music_break_lyric_pt:
            sc.music_break_lyric_pt = str(bp.get("lyric_pt") or "")

    if fin_start is not None and (overwrite_existing or project.finale_audio_start_sec is None):
        project.finale_audio_start_sec = float(fin_start)
    return True, len(cues), source


@app.post("/api/projects/{project_id}/sync-lyrics")
def sync_project_lyrics(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    dur: float | None = None
    if project.audio_file and Path(project.audio_file).exists():
        try:
            d = get_audio_duration(Path(project.audio_file))
            if d > 15.0:
                dur = d
        except Exception:
            dur = None
    synced, count, source = _refresh_project_lyrics_sync(project, actual_duration=dur, overwrite_existing=True)
    _save_project(project)
    return {
        "synced": synced,
        "cue_count": count,
        "source": source,
        "project": project.model_dump(),
    }


@app.post("/api/projects/{project_id}/upload-audio")
async def upload_audio(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    audio_dir = pdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    _invalidate_audio_cache(pdir)

    ext = Path(file.filename or "music.mp3").suffix.lower() or ".mp3"
    if ext not in {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Formato de áudio/vídeo não suportado.")
    dest = audio_dir / f"track{ext}"
    dest.write_bytes(await file.read())

    total_dur = get_audio_duration(dest)
    clip_len = min(54.0, float(project.max_duration_sec or 58.0)) if project.niche == "gaming_news" else project.audio_clip_duration
    best_start = find_best_highlight_start(dest, clip_duration=clip_len)
    project.audio_file = str(dest)
    project.audio_clip_start = round(best_start, 1)
    if project.niche != "gaming_news" and project.finale_audio_start_sec is None and total_dur > 20.0:
        try:
            wf = compute_waveform_peaks(dest, num_bars=120, highlight_window_sec=project.finale_music_sec or 12.0)
            project.finale_audio_start_sec = wf.get("suggested_finale_start")
        except Exception as exc:
            logger.warning("Falha ao computar picos de forma de onda para áudio %s: %s", dest, exc)
    synced_cues = 0
    if project.niche != "gaming_news" and total_dur > 20.0:
        try:
            has_cues_already = any(sc.music_break_audio_start_sec is not None for sc in project.scenes)
            ok, synced_cues, _ = _refresh_project_lyrics_sync(
                project,
                actual_duration=total_dur,
                overwrite_existing=not has_cues_already,
            )
            if not ok:
                synced_cues = 0
        except Exception:
            synced_cues = 0
    _save_project(project)
    return {
        "audio_file": str(dest),
        "filename": file.filename,
        "total_duration": round(total_dur, 1),
        "detected_highlight_start": round(best_start, 1),
        "synced_lyrics_count": synced_cues,
    }


@app.post("/api/projects/{project_id}/attach-input-audio")
def attach_input_audio(project_id: str, body: AttachInputAudioBody) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    _invalidate_audio_cache(pdir)
    raw_fn = (body.filename or "").strip().replace("\\", "/")
    if raw_fn.startswith("gta6/"):
        candidate = (GTA6_INPUTS_DIR / Path(raw_fn[5:]).name).resolve()
        valid_parent = GTA6_INPUTS_DIR.resolve()
    else:
        candidate = (INPUTS_DIR / Path(raw_fn).name).resolve()
        valid_parent = INPUTS_DIR.resolve()
    if not candidate.exists() or candidate.parent != valid_parent:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado na pasta inputs.")
    total_dur = get_audio_duration(candidate)
    clip_len = min(54.0, float(project.max_duration_sec or 58.0)) if project.niche == "gaming_news" else project.audio_clip_duration
    best_start = (
        find_best_highlight_start(candidate, clip_duration=clip_len)
        if body.auto_detect_highlight
        else 0.0
    )
    project.audio_file = str(candidate)
    project.audio_clip_start = round(best_start, 1)
    if project.niche != "gaming_news" and project.finale_audio_start_sec is None and total_dur > 20.0:
        try:
            wf = compute_waveform_peaks(candidate, num_bars=120, highlight_window_sec=project.finale_music_sec or 12.0)
            project.finale_audio_start_sec = wf.get("suggested_finale_start")
        except Exception as exc:
            logger.warning("Falha ao computar picos de forma de onda para áudio %s: %s", candidate, exc)
    synced_cues = 0
    if project.niche != "gaming_news" and total_dur > 20.0:
        try:
            has_cues_already = any(sc.music_break_audio_start_sec is not None for sc in project.scenes)
            ok, synced_cues, _ = _refresh_project_lyrics_sync(
                project,
                actual_duration=total_dur,
                overwrite_existing=not has_cues_already,
            )
            if not ok:
                synced_cues = 0
        except Exception:
            synced_cues = 0
    _save_project(project)
    return {
        "audio_file": str(candidate),
        "total_duration": round(total_dur, 1),
        "detected_highlight_start": round(best_start, 1),
        "synced_lyrics_count": synced_cues,
    }


@app.post("/api/projects/{project_id}/auto-select-audio")
def auto_select_project_audio(project_id: str, rotate: bool = True) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    _invalidate_audio_cache(pdir)
    avoid = (project.audio_file or "") if rotate else ""
    chosen, best_start = auto_select_gaming_soundtrack(project, avoid_path=avoid)
    if chosen is None or not chosen.exists():
        return {
            "selected": False,
            "mode": "builtin_synthwave",
            "message": "Nenhuma trilha MP3/WAV local em inputs/gta6/ ou inputs/. O vídeo usará a trilha Vice City Synthwave automática.",
            "project": project.model_dump(),
        }
    project.audio_file = str(chosen)
    project.audio_clip_start = best_start
    _save_project(project)
    return {
        "selected": True,
        "mode": "local_track",
        "filename": chosen.name,
        "audio_file": str(chosen),
        "audio_clip_start": best_start,
        "project": project.model_dump(),
    }


@app.post("/api/projects/{project_id}/clear-audio")
def clear_audio(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    _invalidate_audio_cache(_project_dir(project_id))
    project.audio_file = None
    project.audio_clip_start = None
    _save_project(project)
    return {"cleared": True}


@app.get("/api/projects/{project_id}/audio-stream")
def stream_project_audio(project_id: str) -> FileResponse:
    project = _load_project(project_id)
    if not project.audio_file or not Path(project.audio_file).exists():
        raise HTTPException(status_code=404, detail="Nenhuma música anexada neste projeto.")
    audio_p = Path(project.audio_file)
    ext = audio_p.suffix.lower()
    mime_map = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/mp4",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".mp4": "video/mp4",
    }
    if ext in mime_map:
        return FileResponse(audio_p, media_type=mime_map[ext])

    # Extract lightweight M4A audio stream for .mkv/.mov/.webm video containers so <audio> seeks smoothly
    audio_dir = _project_dir(project_id) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    cached_m4a = audio_dir / "preview_stream.m4a"
    if (not cached_m4a.exists()) or (cached_m4a.stat().st_mtime < audio_p.stat().st_mtime):
        subprocess.run(
            [
                get_ffmpeg_exe(), "-y", "-i", str(audio_p),
                "-vn", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                str(cached_m4a),
            ],
            capture_output=True,
        )
    if cached_m4a.exists():
        return FileResponse(cached_m4a, media_type="audio/mp4")
    return FileResponse(audio_p)


@app.get("/api/projects/{project_id}/audio-waveform")
def get_project_audio_waveform(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    if not project.audio_file or not Path(project.audio_file).exists():
        return {"has_audio": False, "duration": 0.0, "peaks": [], "suggested_finale_start": 0.0}
    audio_p = Path(project.audio_file)
    audio_dir = _project_dir(project_id) / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    cache_file = audio_dir / "waveform.json"
    mtime_ns = audio_p.stat().st_mtime_ns
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("source") == str(audio_p) and cached.get("mtime_ns") == mtime_ns and cached.get("peaks"):
                return {"has_audio": True, **cached}
        except Exception as exc:
            logger.debug("Falha ao ler cache de waveform: %s", exc)
    wf = compute_waveform_peaks(audio_p, num_bars=240, highlight_window_sec=project.finale_music_sec or 12.0)
    payload = {"source": str(audio_p), "mtime_ns": mtime_ns, **wf}
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    return {"has_audio": True, **payload}


@app.post("/api/projects/{project_id}/detect-highlight")
def detect_audio_highlight(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    if not project.audio_file or not Path(project.audio_file).exists():
        raise HTTPException(status_code=400, detail="Anexe um arquivo de áudio antes de detectar o ponto forte.")
    audio_p = Path(project.audio_file)
    clip_len = (
        min(54.0, float(project.max_duration_sec or 58.0))
        if project.niche == "gaming_news"
        else (project.audio_clip_duration or 45.0)
    )
    best_start = find_best_highlight_start(audio_p, clip_duration=clip_len)
    wf = compute_waveform_peaks(audio_p, num_bars=180, highlight_window_sec=project.finale_music_sec or 12.0)
    project.audio_clip_start = round(best_start, 1)
    if project.niche != "gaming_news" and project.finale_audio_start_sec is None:
        project.finale_audio_start_sec = wf.get("suggested_finale_start")
    _save_project(project)
    return {
        "audio_clip_start": project.audio_clip_start,
        "finale_audio_start_sec": project.finale_audio_start_sec,
        "suggested_finale_start": wf.get("suggested_finale_start"),
        "project": project.model_dump(),
    }


def _redistribute_clips_for_gaming_project(project: MusicStoryProject, pdir: Path) -> None:
    """
    When new project clips are uploaded/attached, ensure scenes immediately showcase
    the attached clips + illustrative news overlays cleanly.
    """
    if getattr(project, "niche", "music_history") != "gaming_news":
        return
    attached_set = {str(Path(p).resolve()) for p in (project.band_video_clips or []) if p}
    # Clear auto-picked library video paths so attached clips rotate in across body scenes
    # while keeping any already-fetched overlay_image_path intact!
    for sc in project.scenes[1:]:
        if sc.video_path and str(Path(sc.video_path).resolve()) not in attached_set:
            sc.video_path = None
            sc.video_start_sec = 0.0
            if sc.asset_type == "video":
                sc.asset_type = "auto"
    # Even if every scene already came from attached clips, rebalance once new clip arrives
    used_attached = {
        str(Path(sc.video_path).resolve())
        for sc in project.scenes[1:]
        if sc.video_path and str(Path(sc.video_path).resolve()) in attached_set
    }
    if attached_set - used_attached:
        for sc in project.scenes[1:]:
            if not sc.video_path or str(Path(sc.video_path).resolve()) in attached_set:
                sc.video_path = None
                sc.video_start_sec = 0.0
                if sc.asset_type == "video":
                    sc.asset_type = "auto"
    auto_blend_gaming_clips_and_images(project, pdir, force_reassign=False)


@app.post("/api/projects/{project_id}/upload-band-clip")
async def upload_band_clip(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    clips_dir = pdir / "band_clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename or "band.mp4").suffix.lower() or ".mp4"
    if ext not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Formato de clipe não suportado (use MP4, MOV, WEBM ou MKV).")
    dest = clips_dir / f"clip_{len(project.band_video_clips) + 1:02d}_{uuid.uuid4().hex[:6]}{ext}"
    dest.write_bytes(await file.read())

    project.band_video_clips = [*project.band_video_clips, str(dest)]
    try:
        _redistribute_clips_for_gaming_project(project, pdir)
    except Exception as exc:
        logger.warning("Falha ao redistribuir clipes após upload: %s", exc)
    _save_project(project)
    return {
        "clip_path": str(dest),
        "filename": file.filename,
        "band_video_clips": project.band_video_clips,
        "project": project.model_dump(),
    }


@app.post("/api/projects/{project_id}/attach-input-video")
def attach_input_video(project_id: str, body: AttachInputVideoBody) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    rel = (body.filename or "").strip().lstrip("/\\")
    if rel.lower().startswith("gta6/") or rel.lower().startswith("gta6\\"):
        candidate = (GTA6_INPUTS_DIR / Path(rel).name).resolve()
        allowed_parent = GTA6_INPUTS_DIR.resolve()
    else:
        candidate = (INPUTS_DIR / Path(rel).name).resolve()
        allowed_parent = INPUTS_DIR.resolve()
    if not candidate.exists() or candidate.parent != allowed_parent:
        raise HTTPException(status_code=404, detail="Arquivo de vídeo não encontrado na pasta inputs.")
    if candidate.suffix.lower() not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Formato de vídeo não suportado.")
    c_str = str(candidate)
    if c_str not in project.band_video_clips:
        project.band_video_clips = [*project.band_video_clips, c_str]
    try:
        _redistribute_clips_for_gaming_project(project, pdir)
    except Exception as exc:
        logger.warning("Falha ao redistribuir clipes após upload: %s", exc)
    _save_project(project)
    return {"clip_path": c_str, "band_video_clips": project.band_video_clips, "project": project.model_dump()}


@app.get("/api/projects/{project_id}/band-clips-info")
def get_band_clips_info(project_id: str) -> list[dict[str, Any]]:
    project = _load_project(project_id)
    items: list[dict[str, Any]] = []
    for idx, raw_p in enumerate(project.band_video_clips):
        p = Path(raw_p)
        exists = p.exists()
        dur = 0.0
        if exists:
            try:
                dur = get_audio_duration(p)
            except Exception:
                dur = 0.0
        items.append({
            "index": idx,
            "filename": p.name,
            "path": str(p),
            "exists": exists,
            "duration_sec": round(dur, 1),
            "thumb_url": f"/api/projects/{project_id}/band-clip-thumb/{idx}",
        })
    return items


@app.get("/api/projects/{project_id}/band-clip-thumb/{clip_idx}")
def get_band_clip_thumb(project_id: str, clip_idx: int) -> FileResponse:
    project = _load_project(project_id)
    if clip_idx < 0 or clip_idx >= len(project.band_video_clips):
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")
    clip_p = Path(project.band_video_clips[clip_idx])
    if not clip_p.exists():
        raise HTTPException(status_code=404, detail="Arquivo do clipe não encontrado.")
    thumbs_dir = _project_dir(project_id) / "band_clips"
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumbs_dir / f"thumb_{clip_idx}_{clip_p.stat().st_mtime_ns}.png"
    if not thumb_path.exists():
        ok = extract_video_frame_thumb(clip_p, thumb_path, seek_sec=1.5, vertical_9_16=False)
        if not ok:
            raise HTTPException(status_code=500, detail="Não foi possível extrair thumbnail do clipe.")
    return FileResponse(thumb_path, media_type="image/png")


@app.post("/api/projects/{project_id}/remove-band-clip/{clip_idx}")
def remove_band_clip(project_id: str, clip_idx: int) -> dict[str, Any]:
    project = _load_project(project_id)
    if clip_idx < 0 or clip_idx >= len(project.band_video_clips):
        raise HTTPException(status_code=404, detail="Clipe não encontrado.")
    clips = list(project.band_video_clips)
    removed = clips.pop(clip_idx)
    project.band_video_clips = clips
    for sc in project.scenes:
        if sc.video_path and sc.video_path == removed:
            sc.video_path = None
            sc.video_start_sec = 0.0
            if sc.asset_type == "video":
                sc.asset_type = "auto"
    _save_project(project)
    return {
        "removed": removed,
        "band_video_clips": project.band_video_clips,
        "project": project.model_dump(),
    }


@app.post("/api/projects/{project_id}/clear-band-clips")
def clear_band_clips(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    project.band_video_clips = []
    _save_project(project)
    return {"cleared": True, "band_video_clips": []}


@app.post("/api/projects/{project_id}/upload-scene-image/{scene_index}")
async def upload_scene_image(
    project_id: str,
    scene_index: int,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    target = next((s for s in project.scenes if s.index == scene_index), None)
    if not target:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")

    scenes_dir = pdir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "img.png").suffix.lower() or ".png"
    raw_dest = scenes_dir / f"scene_{scene_index:02d}_upload{ext}"
    raw_dest.write_bytes(await file.read())
    final_png = scenes_dir / f"scene_{scene_index:02d}.png"
    fit_cover_9_16(raw_dest, final_png)
    raw_dest.unlink(missing_ok=True)
    target.image_path = str(final_png)
    target.video_path = None
    target.asset_type = "image"
    _clear_scene_html_override(pdir, target)
    _save_project(project)
    return {"scene_index": scene_index, "image_path": str(final_png), "project": project.model_dump()}


@app.post("/api/projects/{project_id}/upload-scene-video/{scene_index}")
async def upload_scene_video(
    project_id: str,
    scene_index: int,
    file: UploadFile = File(...),
    video_start_sec: float = Form(0.0),
) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    target = next((s for s in project.scenes if s.index == scene_index), None)
    if not target:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")

    scenes_dir = pdir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "clip.mp4").suffix.lower() or ".mp4"
    if ext not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail="Formato de vídeo não suportado (use MP4, MOV, WEBM ou MKV).")
    dest_vid = scenes_dir / f"scene_{scene_index:02d}_custom{ext}"
    dest_vid.write_bytes(await file.read())

    start_val = max(0.0, float(video_start_sec or 0.0))
    final_png = scenes_dir / f"scene_{scene_index:02d}.png"
    extract_video_frame_thumb(dest_vid, final_png, seek_sec=start_val, vertical_9_16=True)

    target.video_path = str(dest_vid)
    target.video_start_sec = start_val
    target.asset_type = "video"
    if final_png.exists():
        target.image_path = str(final_png)
    _clear_scene_html_override(pdir, target)
    _save_project(project)
    return {"scene_index": scene_index, "video_path": str(dest_vid), "project": project.model_dump()}


@app.post("/api/projects/{project_id}/search-scene-candidates/{scene_index}")
def search_scene_candidates_endpoint(
    project_id: str,
    scene_index: int,
    body: SearchCandidatesBody,
) -> dict[str, Any]:
    project = _load_project(project_id)
    target = next((s for s in project.scenes if s.index == scene_index), None)
    if not target:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")
    custom_q = body.query.strip()
    if custom_q:
        target.search_query = custom_q
    items = search_scene_visual_candidates(
        scene=target,
        project=project,
        custom_query=custom_q,
        limit=min(12, max(4, body.limit)),
    )
    target.candidate_items = items
    target.candidate_urls = [str(c.get("url") or "") for c in items[:6] if c.get("url")]
    _save_project(project)
    return {
        "scene_index": scene_index,
        "search_query": target.search_query,
        "candidates": items,
        "project": project.model_dump(),
    }


@app.post("/api/projects/{project_id}/select-scene-candidate/{scene_index}")
def select_scene_candidate_endpoint(
    project_id: str,
    scene_index: int,
    body: SelectCandidateBody,
) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    target = next((s for s in project.scenes if s.index == scene_index), None)
    if not target:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")
    scenes_dir = pdir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    final_png = scenes_dir / f"scene_{scene_index:02d}.png"

    kind = (body.kind or "url").strip().lower()
    target_role = (getattr(body, "target_role", "primary") or "primary").strip().lower()

    if kind == "band_clip":
        raw_clip = Path(body.clip_path).resolve()
        allowed_clips = {Path(c).resolve() for c in project.band_video_clips}
        in_inputs = INPUTS_DIR.resolve() in raw_clip.parents
        in_gta6 = GTA6_INPUTS_DIR.resolve() in raw_clip.parents
        in_pdir = pdir.resolve() in raw_clip.parents
        if (raw_clip not in allowed_clips and not in_inputs and not in_gta6 and not in_pdir) or not raw_clip.exists():
            raise HTTPException(status_code=400, detail="Clipe de vídeo inválido ou não encontrado.")
        start_val = max(0.0, float(body.video_start_sec or 0.0))
        target.video_path = str(raw_clip)
        target.video_start_sec = start_val
        target.asset_type = "video"
        _clear_scene_html_override(pdir, target)
        if extract_video_frame_thumb(raw_clip, final_png, seek_sec=start_val, vertical_9_16=True):
            target.image_path = str(final_png)
    elif kind == "news_video":
        vurl = (body.video_url or body.url or "").strip()
        start_val = max(0.5, float(body.video_start_sec or 6.0))
        vhash = hashlib.sha1(vurl.encode("utf-8", errors="ignore")).hexdigest()[:10]
        clips_dir = pdir / "news_media" / "clips"
        out_mp4 = clips_dir / f"sc{scene_index:02d}_{vhash}_{int(start_val)}.mp4"
        res_mp4 = download_news_video_segment(
            {
                "kind": "direct_video" if body.direct_video else "youtube",
                "video_url": vurl,
            },
            out_mp4,
            start_sec=start_val,
            duration_sec=9.0,
        )
        if not res_mp4 or not res_mp4.exists():
            raise HTTPException(status_code=400, detail="Não foi possível baixar este trecho de vídeo da notícia.")
        target.video_path = str(res_mp4)
        target.video_start_sec = 0.0
        target.asset_type = "video"
        _clear_scene_html_override(pdir, target)
        if extract_video_frame_thumb(res_mp4, final_png, seek_sec=0.5, vertical_9_16=True):
            target.image_path = str(final_png)
        if target.visual_layout == "auto":
            target.visual_layout = "pip_card"
    elif kind in {"url", "image"} and target_role == "overlay":
        ov_png = scenes_dir / f"scene_{scene_index:02d}_overlay.png"
        if not apply_overlay_image_url(body.url, ov_png):
            raise HTTPException(status_code=400, detail="Não foi possível baixar ou converter esta imagem para overlay.")
        target.overlay_image_path = str(ov_png)
        target.overlay_mode = "manual"
        target.overlay_reason = "Selecionada manualmente nos candidatos"
        if target.visual_layout == "full_media":
            target.visual_layout = "pip_card"
        if not (target.overlay_label or "").strip():
            target.overlay_label = (target.title or "RADAR NOTÍCIA").strip()[:28].upper()
    elif kind in {"url", "image"}:
        if not apply_scene_image_url(body.url, final_png):
            raise HTTPException(status_code=400, detail="Não foi possível baixar ou converter esta imagem.")
        target.image_path = str(final_png)
        target.video_path = None
        target.video_start_sec = 0.0
        target.asset_type = "image"
        _clear_scene_html_override(pdir, target)
    elif kind == "html_hook":
        target.video_path = None
        target.video_start_sec = 0.0
        target.asset_type = "auto"
        _clear_scene_html_override(pdir, target)
    else:
        raise HTTPException(status_code=400, detail="Tipo de seleção visual inválido.")

    _save_project(project)
    return {"scene_index": scene_index, "project": project.model_dump()}


@app.post("/api/projects/{project_id}/upload-scene-overlay/{scene_index}")
async def upload_scene_overlay(
    project_id: str,
    scene_index: int,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    target = next((s for s in project.scenes if s.index == scene_index), None)
    if not target:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")

    scenes_dir = pdir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "overlay.png").suffix.lower() or ".png"
    raw_dest = scenes_dir / f"scene_{scene_index:02d}_ov_upload{ext}"
    raw_dest.write_bytes(await file.read())
    ov_png = scenes_dir / f"scene_{scene_index:02d}_overlay.png"
    fit_overlay_editorial_png(raw_dest, ov_png)
    raw_dest.unlink(missing_ok=True)

    target.overlay_image_path = str(ov_png)
    target.overlay_mode = "manual"
    target.overlay_reason = "Imagem enviada manualmente"
    if target.visual_layout == "full_media":
        target.visual_layout = "pip_card"
    if not (target.overlay_label or "").strip():
        target.overlay_label = (target.title or "RADAR NOTÍCIA").strip()[:28].upper()
    _save_project(project)
    return {"scene_index": scene_index, "overlay_image_path": str(ov_png), "project": project.model_dump()}


@app.post("/api/projects/{project_id}/clear-scene-overlay/{scene_index}")
def clear_scene_overlay(project_id: str, scene_index: int) -> dict[str, Any]:
    project = _load_project(project_id)
    target = next((s for s in project.scenes if s.index == scene_index), None)
    if not target:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")
    target.overlay_image_path = None
    target.visual_layout = "full_media"
    target.overlay_mode = "none_motion_only"
    target.overlay_reason = "Overlay removido manualmente — mantendo vídeo em movimento limpo"
    _save_project(project)
    return {"scene_index": scene_index, "project": project.model_dump()}


@app.post("/api/projects/{project_id}/auto-blend-media")
def auto_blend_media_endpoint(
    project_id: str,
    body: AutoBlendBody | None = None,
) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    cfg = body or AutoBlendBody()
    stats = auto_blend_gaming_clips_and_images(
        project,
        pdir,
        force_reassign=bool(cfg.force_reassign),
        include_hook_scene=bool(cfg.include_hook_scene),
    )
    _save_project(project)
    return {**stats, "project": project.model_dump()}


@app.post("/api/projects/{project_id}/scenes/{scene_index}/generate-ai-overlay")
def generate_scene_ai_overlay_endpoint(
    project_id: str,
    scene_index: int,
    body: GenerateAiSceneImageBody | None = None,
) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    target_sc = next((s for s in project.scenes if s.index == scene_index), None)
    if not target_sc:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")
    cfg = body or GenerateAiSceneImageBody()
    res = generate_scene_ai_image_asset(
        project=project,
        scene=target_sc,
        work_dir=pdir,
        target=cfg.target,
        custom_prompt=cfg.custom_prompt,
    )
    if not res.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=f"Falha ao gerar imagem IA ({res.get('detail', 'sem conexão')}).",
        )
    _save_project(project)
    return {**res, "scene_index": scene_index, "project": project.model_dump()}


@app.post("/api/projects/{project_id}/enrich-ai-visuals")
def enrich_ai_visuals_endpoint(
    project_id: str,
    body: EnrichAiVisualsBody | None = None,
) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    cfg = body or EnrichAiVisualsBody()
    generated = 0
    engines_used: list[str] = []
    for idx, sc in enumerate(project.scenes):
        if idx == 0 and not cfg.include_hook_scene and getattr(project, "html_hook_card", True) and sc.asset_type != "video":
            continue
        has_ov = bool(sc.overlay_image_path and Path(sc.overlay_image_path).exists())
        if has_ov and not cfg.overwrite_existing:
            continue
        res = generate_scene_ai_image_asset(
            project=project,
            scene=sc,
            work_dir=pdir,
            target="overlay",
        )
        if res.get("ok"):
            generated += 1
            engines_used.append(str(res.get("engine") or "ai"))
    _save_project(project)
    return {
        "generated": generated,
        "engines_used": sorted(set(engines_used)),
        "project": project.model_dump(),
    }


@app.post("/api/projects/{project_id}/upload-reel2-image")
async def upload_reel2_image(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    pdir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "emblem.png").suffix.lower() or ".png"
    dest = pdir / f"reel2_upload{ext}"
    dest.write_bytes(await file.read())
    project.reel2_center_image = str(dest)
    _save_project(project)
    return {"reel2_center_image": str(dest)}


@app.post("/api/projects/{project_id}/generate-visuals")
def generate_visuals_endpoint(project_id: str) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    if getattr(project, "niche", "music_history") == "gaming_news":
        prepare_gaming_project_visuals(project, pdir)
        _save_project(project)
        return project.model_dump()
    prepare_project_visuals(project, pdir)
    for sc in project.scenes:
        if not sc.candidate_items:
            try:
                items = search_scene_visual_candidates(sc, project, limit=6)
                if items:
                    sc.candidate_items = items
                    sc.candidate_urls = [str(c.get("url") or "") for c in items[:6] if c.get("url")]
            except Exception as exc:
                logger.warning("Falha ao buscar candidatos visuais para cena: %s", exc)
    generate_reel2_centerpiece(project, pdir, get_gemini_api_key())
    _save_project(project)
    return project.model_dump()


class RegenerateThumbnailBody(BaseModel):
    thumbnail_style: str | None = None
    thumbnail_headline: str | None = None
    thumbnail_badge: str | None = None
    thumbnail_scene_index: int | None = None


@app.post("/api/projects/{project_id}/regenerate-thumbnail")
def regenerate_thumbnail_endpoint(project_id: str, body: RegenerateThumbnailBody = RegenerateThumbnailBody()) -> dict[str, Any]:
    project = _load_project(project_id)
    if body.thumbnail_style is not None and body.thumbnail_style in THUMBNAIL_STYLES:
        project.thumbnail_style = body.thumbnail_style  # type: ignore[assignment]
    if body.thumbnail_headline is not None:
        project.thumbnail_headline = body.thumbnail_headline.strip()[:90]
    if body.thumbnail_badge is not None:
        project.thumbnail_badge = body.thumbnail_badge.strip()[:45]
    project.thumbnail_scene_index = body.thumbnail_scene_index

    pdir = _project_dir(project_id)
    odir = _output_dir(project_id)
    odir.mkdir(parents=True, exist_ok=True)
    # Ensure at least one visual exists if user regenerates thumbnail before full render
    has_any_visual = any(
        (s.overlay_image_path and Path(s.overlay_image_path).exists())
        or (s.image_path and Path(s.image_path).exists())
        or (s.video_path and Path(s.video_path).exists())
        for s in (project.scenes or [])
    )
    if not has_any_visual:
        try:
            prepare_project_visuals(project, pdir)
        except Exception as exc:
            logger.warning("Falha ao preparar visuais preliminares para thumbnail: %s", exc)

    cover_path = odir / "capa_destaque.png"
    generate_project_thumbnail(project=project, work_dir=pdir, out_cover_path=cover_path)
    project.outputs["highlight_cover"] = str(cover_path)
    _save_project(project)
    return {
        "status": "ok",
        "cover_url": f"/api/projects/{project_id}/outputs/highlight_cover?t={int(time.time() * 1000)}",
        "project": project.model_dump(),
    }


@app.post("/api/projects/{project_id}/render")
def start_render(project_id: str) -> dict[str, Any]:
    _load_project(project_id)
    with JOBS_LOCK:
        cur = JOBS.get(project_id)
        if cur and cur.get("state") == "running":
            return cur

    _set_job(project_id, "running", 10, "Preparando mídias, fontes documentadas e roteiro...")

    def _worker() -> None:
        try:
            proj = _load_project(project_id)
            pdir = _project_dir(project_id)
            odir = _output_dir(project_id)
            is_gaming = getattr(proj, "niche", "music_history") == "gaming_news"
            _set_job(
                project_id,
                "running",
                22,
                "Preparando imagens da matéria, frames de trailer GTA 6 e cenas 9:16..."
                if is_gaming
                else "Preparando cenas 9:16, galeria da banda e cenários HTML...",
            )
            prepare_project_visuals(proj, pdir)
            _save_project(proj)

            if is_gaming:
                _set_job(
                    project_id,
                    "running",
                    52,
                    "Sintetizando narração <= 58s, neon Vice City, legendas safe-zone e QA...",
                )
            elif proj.video_mode == "single_all_in_one":
                _set_job(
                    project_id,
                    "running",
                    52,
                    "Intercalando introdução, narração PT-BR, respiros musicais, clímax final e QA...",
                )
            else:
                _set_job(
                    project_id,
                    "running",
                    52,
                    "Sintetizando voz PT-BR, batidas musicais, Reels 1 & 2 e QA final...",
                )
            rendered = render_full_project(proj, pdir, odir)
            _save_project(rendered)
            done_msg = (
                "YouTube Short (<= 58s) + Pacote YouTube/TikTok + QA renderizado!"
                if is_gaming
                else (
                    "Vídeo Único (Música + Fatos + Capa + QA) renderizado e verificado!"
                    if rendered.video_mode == "single_all_in_one"
                    else "Combo 2 Reels (Reel 1 + Reel 2 + Capa + QA) renderizado e verificado!"
                )
            )
            _set_job(project_id, "completed", 100, done_msg)
        except Exception as exc:
            _set_job(project_id, "error", 100, "Falha durante a renderização.", str(exc))

    threading.Thread(target=_worker, daemon=True).start()
    return JOBS[project_id]


@app.get("/api/jobs/{project_id}")
def get_job(project_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        if project_id in JOBS:
            return JOBS[project_id]
    return {"project_id": project_id, "state": "idle", "progress": 0, "message": "", "error": None}


@app.get("/api/projects/{project_id}/scene-preview/{scene_index}")
def scene_preview(project_id: str, scene_index: int) -> FileResponse:
    project = _load_project(project_id)
    scene = next((s for s in project.scenes if s.index == scene_index), None)
    if not scene or not scene.image_path or not Path(scene.image_path).exists():
        raise HTTPException(status_code=404, detail="Preview da cena ainda não gerado.")
    return FileResponse(scene.image_path)


@app.get("/api/projects/{project_id}/scene-overlay-preview/{scene_index}")
def scene_overlay_preview(project_id: str, scene_index: int) -> FileResponse:
    project = _load_project(project_id)
    scene = next((s for s in project.scenes if s.index == scene_index), None)
    if not scene or not scene.overlay_image_path or not Path(scene.overlay_image_path).exists():
        raise HTTPException(status_code=404, detail="Overlay da cena ainda não definido.")
    return FileResponse(scene.overlay_image_path)


@app.get("/api/projects/{project_id}/scene-video-stream/{scene_index}")
def scene_video_stream(project_id: str, scene_index: int) -> FileResponse:
    project = _load_project(project_id)
    pdir = _project_dir(project_id).resolve()
    scene = next((s for s in project.scenes if s.index == scene_index), None)
    if not scene or not scene.video_path:
        raise HTTPException(status_code=404, detail="Clipe de vídeo não definido para esta cena.")
    vid_p = Path(scene.video_path).resolve()
    allowed_roots = [pdir, PROJECTS_DIR.resolve(), INPUTS_DIR.resolve(), GTA6_INPUTS_DIR.resolve()]
    if not vid_p.exists() or not any(r in vid_p.parents for r in allowed_roots):
        raise HTTPException(status_code=404, detail="Arquivo de vídeo da cena não encontrado.")
    ext = vid_p.suffix.lower()
    mime = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".mkv": "video/x-matroska"}.get(ext, "video/mp4")
    return FileResponse(vid_p, media_type=mime)


@app.post("/api/projects/{project_id}/scenes/{scene_index}/quick-render-preview")
def quick_render_scene_preview(project_id: str, scene_index: int) -> dict[str, Any]:
    project = _load_project(project_id)
    pdir = _project_dir(project_id)
    target = next((s for s in project.scenes if s.index == scene_index), None)
    if not target:
        raise HTTPException(status_code=404, detail="Cena não encontrada.")
    try:
        out_mp4 = render_single_scene_preview_mp4(project, scene_index, pdir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erro ao gerar prévia FFmpeg da cena: {exc}") from exc
    _save_project(project)
    return {
        "ok": True,
        "scene_index": scene_index,
        "preview_url": f"/api/projects/{project_id}/scene-rendered-preview/{scene_index}?t={int(time.time() * 1000)}",
        "path": str(out_mp4),
        "project": project.model_dump(),
    }


@app.get("/api/projects/{project_id}/scene-rendered-preview/{scene_index}")
def get_rendered_scene_preview(project_id: str, scene_index: int) -> FileResponse:
    pdir = _project_dir(project_id)
    mp4 = pdir / "scenes" / f"scene_{scene_index:02d}_preview.mp4"
    if not mp4.exists():
        raise HTTPException(status_code=404, detail="Prévia renderizada da cena ainda não gerada.")
    return FileResponse(mp4, media_type="video/mp4")


@app.get("/api/projects/{project_id}/reel2-preview")
def reel2_preview(project_id: str) -> FileResponse:
    pdir = _project_dir(project_id)
    img = pdir / "reel2_centerpiece.png"
    if not img.exists():
        proj = _load_project(project_id)
        if proj.reel2_center_image and Path(proj.reel2_center_image).exists():
            return FileResponse(proj.reel2_center_image)
        raise HTTPException(status_code=404, detail="Emblema do Reel 2 ainda não gerado.")
    return FileResponse(img)


@app.get("/api/projects/{project_id}/outputs/{kind}")
def get_output_file(project_id: str, kind: str, download: bool = False) -> FileResponse:
    project = _load_project(project_id)
    path_str = project.outputs.get(kind)
    if not path_str and kind in ("video", "single_video", "full_video"):
        path_str = project.outputs.get("youtube_short") or project.outputs.get("single_video") or project.outputs.get("full_video")
    if not path_str:
        raise HTTPException(status_code=404, detail="Arquivo não disponível para este projeto.")
    file_path = Path(path_str).resolve()
    allowed_root = _output_dir(project_id).resolve()
    if not file_path.exists() or allowed_root not in file_path.parents:
        raise HTTPException(status_code=404, detail="Arquivo de saída não encontrado.")

    media_type = "video/mp4"
    if file_path.suffix.lower() == ".png":
        media_type = "image/png"
    elif file_path.suffix.lower() == ".txt":
        media_type = "text/plain; charset=utf-8"

    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{file_path.name}"'
    return FileResponse(file_path, media_type=media_type, headers=headers)


def run() -> None:
    import uvicorn

    port = int(os.getenv("PORT", "8765"))
    uvicorn.run("app.web_server:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    run()
