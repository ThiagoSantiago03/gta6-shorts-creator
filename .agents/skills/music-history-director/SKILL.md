---
name: music-history-director
description: Diretor completo de combos Instagram Reels & Destaques sobre História da Música em PT-BR (Zero-API First). Use ao criar, personalizar HTML motion, buscar mídias históricas e auditar QA de vídeos musicais no workspace CriadorDeVideos.
---

# Music History Director Skill (Zero-API + Agent-First)

Use esta skill sempre que for dirigir um novo episódio em `d:\CriadorDeVideos` sem depender de créditos externos de IA.

## Workflow de Execução

1. **Pesquisa Gratuita**:
   - Execute `python cli.py research --song "<Música>" --artist "<Artista>"` para extrair fatos reais da Wikipedia PT/EN, MusicBrainz, iTunes Search, citações (`quote_pt`) e letras sincronizadas LRC via **LRCLIB** (`app/lyrics_engine.py`).
2. **Escolha do Perfil de Estilo (`styles/`)**:
   - Leia o perfil correspondente em `styles/`:
     - `documental_rock.md` (Rock, Metal, Clássicos 70s–90s)
     - `viral_pop_historia.md` (Pop, Hip-Hop, Eletrônica, Virais)
     - `zine_punk_underground.md` (Punk, Grunge, Indie, Alternativo)
     - `vinil_minimal_audio.md` (MPB, Soul, Jazz, Acústicos — foco em vitrola/vinil)
3. **Montagem de Cenas e Alternância Inteligente pela Letra (Protocolo ViMax + OpenMontage)**:
   - No modo padrão **`single_all_in_one`** com **`opening_mode="lyric_hook"`** e **`overlay_style="clean_reels"`**:
     - Gere **5 cenas enxutas** (`hook_establish`, `context_origin`, `story_conflict`, `climax_reveal`, `legacy_cta`), configurando `intro_music_sec: 0.2`, `music_break_after_sec: 3.2` (ou janela LRC), `music_break_audio_start_sec` + `music_break_lyric_pt` nas cenas 1..4 (saltando via crossfade 260ms para versos cantados reais com tradução PT-BR `LyricBreak`) e `0.0` na cena 5 antes do **Clímax Musical Final (~12s em `finale_audio_start_sec`)**.
     - Opcionalmente preencha `quote_text` para exibir card editorial de citação na parte superior média da tela sem colidir com legendas, e `band_video_clips` para clipes `.mp4` da banda (ou anexe `.lrc` homônimo ao áudio local / use `POST /api/projects/{id}/sync-lyrics`).
     - Use `opening_mode="music_intro"` (`1.8s`) + `overlay_style="banner_top"` quando preferir a abertura instrumental clássica com tarja superior fixa.
   - No modo **`two_reels_combo`** (2 Reels Separados): gere 6 cenas (`hook_establish`, `context_origin`, `story_conflict`, `climax_reveal`, `legacy_resolution`, `cta_handoff`) para o Reel 1 e o palco musical no Reel 2.
   - Alterne efeitos de câmera (`zoom_in`, `ken_burns_duotone`, `pan_right`, `vintage_grain`, `pan_left`, `zoom_out`) para manter o `slideshow_risk < 2.0`.
   - Combine **Cena 1 em HTML Motion** (`html_hook_card=True`, exceto quando `scene.asset_type == "video"`) + **Clipes MP4 locais com `video_start_sec`** ou **Archive.org B-roll** (somente `song_yr < 1975` no modo automático) + **Wikipedia/Wikimedia full-bleed** alinhados aos tópicos da narração (álbum, censura, MTV, show) com proteção anti-homônimos e pré-visualização de candidatos via `search_scene_visual_candidates`.
   - No painel Web 5 etapas (`static/index.html`), mídias e clipes são anexados logo na Etapa 1 (`Música & Clipes`), tempos `B1..B4` / `Clímax Final` são posicionados na onda sonora Canvas2D da Etapa 3 e candidatos visuais/clipes são selecionados por cena na Etapa 4.
4. **Regras de Motion HTML (`OpenFilm` + `HyperFrames`)**:
   - Toda cena `.html` deve expor `window.film = { width: 1080, height: 1920, duration, ready, frame(t) }`.
   - `frame(t)` deve ser **100% determinístico em `t`**.
   - **Stage opaco** (`#07070c`), entradas escalonadas (`easeOutQuart`), **proibido wobble contínuo em títulos**.
   - Deixe `y = 1480..1850` livre de elementos fixos durante trechos falados — a faixa reservada é das legendas `.ass` aplicadas no último estágio do FFmpeg.
5. **Render & Self-Verification (`video-use`)**:
   - Renderize via `cli.py create` ou `cli.py render-project`.
   - Inspecione `projects/<id>/project_packed.md`, `verify/qa_report.json` e leia visualmente `contact_sheet_reel_completo.png` (ou `contact_sheet_reel1.png` e `contact_sheet_reel2.png` no modo 2 Reels) + `capa_destaque.png` antes de declarar concluído.
