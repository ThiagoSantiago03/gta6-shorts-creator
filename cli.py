from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path

from app.config import OUTPUTS_DIR, PROJECTS_DIR
from app.gaming_media import auto_blend_gaming_clips_and_images, auto_select_gaming_soundtrack
from app.gaming_radar import fetch_daily_gaming_radar
from app.gaming_scriptwriter import generate_gaming_script
from app.models import MusicStoryProject, SceneItem
from app.video_renderer import render_full_project


def save_project_safe(project: MusicStoryProject, pdir: Path) -> Path:
    pdir.mkdir(parents=True, exist_ok=True)
    target = pdir / "project.json"
    bak = pdir / "project.json.bak"
    tmp = pdir / f"project.json.tmp.{uuid.uuid4().hex[:6]}"
    tmp.write_text(project.model_dump_json(indent=2), encoding="utf-8")
    if target.exists() and target.stat().st_size > 0:
        try:
            shutil.copy2(target, bak)
        except Exception:
            pass
    tmp.replace(target)
    return target


def _slugify(val: str) -> str:
    import re
    s = val.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"[\s_-]+", "-", s)[:40] or "gta6"


def cmd_radar(args: argparse.Namespace) -> None:
    print(f"Buscando notícias e trends de '{args.query}' (Região: {args.geo})...")
    items = fetch_daily_gaming_radar(query=args.query, geo=args.geo)
    if not items:
        print("Nenhuma notícia encontrada.")
        return
    print(f"\n--- Radar GTA 6: {len(items)} pautas encontradas ---")
    for i, it in enumerate(items, 1):
        print(f"\n[{i}] {it.title}")
        print(f"    Fonte: {it.source} | Score: {it.trend_score} pts")
        if it.suggested_angle:
            print(f"    Ângulo: {it.suggested_angle}")
        if it.url:
            print(f"    URL: {it.url}")


def cmd_create(args: argparse.Namespace) -> None:
    topic = args.topic.strip()
    if not topic:
        print("Erro: informe o tópico com --topic.")
        return
    
    print(f"\nCriando roteiro e montagem para: '{topic}'...")
    pid = f"gta6-{_slugify(topic)}-{uuid.uuid4().hex[:6]}"
    pdir = PROJECTS_DIR / pid
    pdir.mkdir(parents=True, exist_ok=True)

    g_data = generate_gaming_script(
        topic_title=topic,
        game_name="GTA 6",
        topic_angle=args.angle,
        source_url=args.url,
        fact_confidence="Notícia / Imprensa",
        reference_notes="",
        script_engine="free_research",
        accent_color="#00F0FF",
        viral_archetype=args.archetype,
        speech_rate="+14%",
    )

    scenes_objs = [
        s if isinstance(s, SceneItem) else SceneItem(**s)
        for s in g_data["scenes"]
    ]

    project = MusicStoryProject(
        id=pid,
        niche="gaming_news",
        title=str(g_data.get("resolved_topic") or topic),
        game="GTA 6",
        song_title=str(g_data.get("resolved_topic") or topic),
        artist="GTA 6",
        year="2026",
        genre="Games / GTA 6",
        topic_angle=str(g_data.get("resolved_angle") or args.angle),
        topic_source_url=args.url,
        max_duration_sec=58.0,
        speech_rate="+14%",
        viral_archetype=args.archetype,
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
        style_preset="vice_city_neon",
        subtitle_style="vice_city",
        tts_engine="edge_tts",
        voice_name="pt-BR-AntonioNeural",
        status="draft (cli)",
    )

    # Soundtrack & trailer cuts auto-blend
    auto_track, auto_start = auto_select_gaming_soundtrack(project)
    if auto_track is not None and auto_track.exists():
        project.audio_file = str(auto_track)
        project.audio_clip_start = auto_start

    try:
        auto_blend_gaming_clips_and_images(project, pdir, force_reassign=False)
    except Exception as exc:
        print(f"Aviso no auto-blend: {exc}")

    save_project_safe(project, pdir)
    print(f"\nProjeto criado com sucesso!")
    print(f"ID: {pid}")
    print(f"Cenas: {len(project.scenes)} cenas configuradas (<= 58s)")
    print(f"Pasta: {pdir}")
    if args.render:
        print("\nRenderizando vídeo final agora...")
        odir = OUTPUTS_DIR / pid
        odir.mkdir(parents=True, exist_ok=True)
        rendered = render_full_project(project, pdir, odir)
        save_project_safe(rendered, pdir)
        print(f"Vídeo renderizado com sucesso: {odir / 'single_video.mp4'}")


def cmd_batch(args: argparse.Namespace) -> None:
    count = max(1, min(args.count, 10))
    print(f"Iniciando criação autônoma em lote de {count} Shorts de GTA 6...")
    items = fetch_daily_gaming_radar(query="GTA 6", geo="BR")[:count]
    if not items:
        print("Nenhuma pauta encontrada no radar.")
        return

    for idx, it in enumerate(items, 1):
        print(f"\n--- [{idx}/{count}] Criando: {it.title} ---")
        pid = f"gta6-{_slugify(it.title)}-{uuid.uuid4().hex[:6]}"
        pdir = PROJECTS_DIR / pid
        pdir.mkdir(parents=True, exist_ok=True)

        g_data = generate_gaming_script(
            topic_title=it.title,
            game_name="GTA 6",
            topic_angle=it.suggested_angle,
            source_url=it.url,
            fact_confidence=it.confidence or "Notícia / Imprensa",
            reference_notes="",
            script_engine="free_research",
            accent_color="#00F0FF",
            viral_archetype="auto",
            speech_rate="+14%",
        )

        scenes_objs = [
            s if isinstance(s, SceneItem) else SceneItem(**s)
            for s in g_data["scenes"]
        ]

        project = MusicStoryProject(
            id=pid,
            niche="gaming_news",
            title=str(g_data.get("resolved_topic") or it.title),
            game="GTA 6",
            song_title=str(g_data.get("resolved_topic") or it.title),
            artist="GTA 6",
            year="2026",
            genre="Games / GTA 6",
            topic_angle=str(g_data.get("resolved_angle") or it.suggested_angle or ""),
            topic_source_url=it.url,
            max_duration_sec=58.0,
            speech_rate="+14%",
            viral_archetype="auto",
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
            style_preset="vice_city_neon",
            subtitle_style="vice_city",
            tts_engine="edge_tts",
            voice_name="pt-BR-AntonioNeural",
            status="draft (batch_cli)",
        )

        auto_track, auto_start = auto_select_gaming_soundtrack(project)
        if auto_track is not None and auto_track.exists():
            project.audio_file = str(auto_track)
            project.audio_clip_start = auto_start

        try:
            auto_blend_gaming_clips_and_images(project, pdir, force_reassign=False)
        except Exception:
            pass

        save_project_safe(project, pdir)
        print(f"Short {idx} criado: ID {pid}")

        if args.render:
            print(f"Renderizando Short {idx}...")
            odir = OUTPUTS_DIR / pid
            odir.mkdir(parents=True, exist_ok=True)
            rendered = render_full_project(project, pdir, odir)
            save_project_safe(rendered, pdir)
            print(f"Renderizado: {odir / 'single_video.mp4'}")


def cmd_render(args: argparse.Namespace) -> None:
    pid = args.project_id
    pdir = PROJECTS_DIR / pid
    pfile = pdir / "project.json"
    if not pfile.exists():
        print(f"Erro: projeto {pid} não encontrado em {pdir}.")
        return

    project = MusicStoryProject.model_validate_json(pfile.read_text(encoding="utf-8"))
    odir = OUTPUTS_DIR / pid
    odir.mkdir(parents=True, exist_ok=True)
    print(f"Renderizando projeto {pid} (1080x1920 9:16 <= 58s)...")
    rendered = render_full_project(project, pdir, odir)
    save_project_safe(rendered, pdir)
    print(f"\nRenderização concluída com sucesso!")
    print(f"Arquivo final: {odir / 'single_video.mp4'}")


def cmd_list(args: argparse.Namespace) -> None:
    items = []
    for pdir in PROJECTS_DIR.iterdir():
        pfile = pdir / "project.json"
        if pfile.exists():
            try:
                proj = MusicStoryProject.model_validate_json(pfile.read_text(encoding="utf-8"))
                items.append(proj)
            except Exception:
                pass
    items.sort(key=lambda x: x.updated_at, reverse=True)
    print(f"Projetos cadastrados ({len(items)}):")
    for it in items:
        rendered = "SIM" if (it.outputs.get("single_video") or it.outputs.get("full_video")) else "NÃO"
        title = it.title or it.song_title or "Sem título"
        print(f"- [{it.id}] {title} | Renderizado: {rendered}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GTA 6 Shorts Studio CLI — Automação de Vídeos Curtos para YouTube Shorts (<= 58s • 1080x1920)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_radar = sub.add_parser("radar", help="Busca notícias em tempo real de GTA 6")
    p_radar.add_argument("--query", default="GTA 6", help="Termo de busca")
    p_radar.add_argument("--geo", default="BR", help="Região (BR ou US)")

    p_create = sub.add_parser("create", help="Cria um novo YouTube Short de GTA 6")
    p_create.add_argument("--topic", required=True, help="Manchete ou pauta do vídeo")
    p_create.add_argument("--angle", default="", help="Ângulo ou foco específico")
    p_create.add_argument("--url", default="", help="URL da matéria original")
    p_create.add_argument("--archetype", default="auto", help="Estilo viral do roteiro")
    p_create.add_argument("--render", action="store_true", help="Renderizar imediatamente após criar")

    p_batch = sub.add_parser("batch", help="Cria automaticamente múltiplos vídeos a partir do radar")
    p_batch.add_argument("--count", type=int, default=5, help="Quantidade de vídeos (1 a 10)")
    p_batch.add_argument("--render", action="store_true", help="Renderizar todos os vídeos em fila")

    p_render = sub.add_parser("render", help="Renderiza um projeto existente")
    p_render.add_argument("--project-id", required=True, help="ID do projeto")

    p_list = sub.add_parser("list", help="Lista todos os projetos salvos")

    args = parser.parse_args()
    if args.cmd == "radar":
        cmd_radar(args)
    elif args.cmd == "create":
        cmd_create(args)
    elif args.cmd == "batch":
        cmd_batch(args)
    elif args.cmd == "render":
        cmd_render(args)
    elif args.cmd == "list":
        cmd_list(args)


if __name__ == "__main__":
    main()
