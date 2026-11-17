# Guia do Agente Diretor — História da Música Studio v2 (Zero-API First)

Este repositório segue a arquitetura **Agent-First + Zero-API** consolidada a partir de `OpenMontage`, `openfilm`, `HyperFrames`, `video-use`, `FireRed-OpenStoryline` e `ViMax`.

Quando o usuário pedir um novo combo para Instagram Reels & Destaques, **não gaste créditos de API do Gemini por padrão** — você (o Agente) atua como pesquisador, roteirista, editor e verificador de QA usando o código local e fontes públicas gratuitas.

---

## Fluxo Padrão de Direção (0 Chamadas de LLM Externa)

### Passo 1 — Coletar Dossiê Histórico Gratuito
Rode:
```powershell
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py research --song "Mother" --artist "Danzig" -o projects\dossier.json
```
O módulo `app/music_researcher.py` consulta **Wikipedia PT, Wikipedia EN, MusicBrainz, iTunes Search e LRCLIB (`app/lyrics_engine.py`)** sem chave de API, resolvendo ano exato, álbum, produtor, marcos em paradas/MTV, citações de entrevista (`quote_pt`) e timestamps LRC reais da letra cantada.

### Passo 2 — Roteirizar com `shot_intent`, Gancho pela Letra e Curadoria Multimídia
Escreva ou gere `projects/<id>/project.json` (ou crie um `script.json` para passar via `--script-json` ou `POST /api/projects/import-json`):
- **Modo padrão (`video_mode="single_all_in_one"` + `opening_mode="lyric_hook"` + `overlay_style="clean_reels"`)**:
  - Estrutura de **Gancho pela Letra & Alternância Inteligente**: **Abertura rápida (~0.2s) com fala-gancho que introduz o 1º verso** → **Respiro B1 saltando direto ao timestamp cantado real (`music_break_audio_start_sec`) com tradução PT-BR `LyricBreak` na tela** → **Fato histórico (com opção de `quote_text` em card editorial)** → **Respiros B2..B4 com versos contrastantes** → **Clímax Musical Final (~12s a partir de `finale_audio_start_sec` em volume cheio + clipes em movimento contínuo)**.
  - Para usar a introdução instrumental clássica (`1.8s` antes da narração) e tarja fixa superior, use `opening_mode="music_intro"` (`--intro-sec 1.8`) e `overlay_style="banner_top"`.
  - **5 cenas enxutas** (`hook_establish`, `context_origin`, `story_conflict`, `climax_reveal`, `legacy_cta`), definindo `music_break_after_sec` (padrão `3.2`s, ou janela LRC mesclada) + `music_break_audio_start_sec` + `music_break_lyric_en`/`pt` nas cenas 1 a 4 e `0.0` na cena 5 (seguida pelo Clímax Final). Pode-se também preencher `quote_text` numa cena com frase/citação histórica curta.
  - Use `POST /api/projects/{id}/sync-lyrics` (ou um arquivo `.lrc` homônimo junto ao MP3/WAV) caso anexe uma versão editada/estendida da música para alinhar os timestamps LRC exatamente à duração do arquivo local. Use `GET /api/projects/{id}/audio-waveform` e `GET /api/projects/{id}/audio-stream` para inspeção de picos e recorte exato na UI.
  - Opcionalmente preencha `band_video_clips` com clipes `.mp4`/`.mov` reais da banda tocando (ou atribua direto em qualquer cena via `scene.video_path` + `scene.video_start_sec` ou endpoint `POST /api/projects/{id}/select-scene-candidate/{scene_index}`); sem clipes externos, o sistema monta automaticamente galeria animada Wikipedia/Wikimedia (filtrada por entidade artística + relevância ao roteiro da cena) + palco HTML5 Canvas2D determinístico.
  - Use `POST /api/projects/{id}/search-scene-candidates/{scene_index}` para buscar até 8–12 imagens candidatas reais alinhadas ao script da cena e capa oficial iTunes (`candidate_items`). Nota: sob `visual_engine="auto_free_media"`, B-roll Archive.org Prelinger automático só é ativado para músicas pré-1975 para evitar clipes fora de época em lançamentos 1975+.
- **Modo alternativo (`video_mode="two_reels_combo"` — 2 Reels Separados)**:
  - **6 cenas para o Reel 1 (~40–50s, 110–130 palavras em PT-BR)**: `hook_establish`, `context_origin`, `story_conflict`, `climax_reveal`, `legacy_resolution`, `cta_handoff`.
- Inclua em cada cena:
  - `search_query` (em inglês, curto, 2–4 palavras para Wikimedia Commons / Wikipedia PageImages)
  - `video_query` (termos concisos de cena para B-roll público no Archive.org / Prelinger / classic_tv, ex.: `"rock concert crowd"`, `"recording studio"`, `"television news"`)
  - `effect` variado entre `"zoom_in"`, `"zoom_out"`, `"pan_left"`, `"pan_right"`, `"ken_burns_duotone"`, `"vintage_grain"` (nunca repetir o mesmo efeito > 50% das cenas!)
  - `highlight_words` (2–4 palavras-chave para destaque nas legendas)
  - `quote_text` (opcional — frase curta em português para card editorial flutuante sem colidir com as legendas inferiores)

### Passo 3 — Cenas Motion Graphics HTML Personalizadas (Opcional — Protocolo OpenFilm)
Para uma abertura ou diagrama histórico sob medida em 1080×1920, crie um HTML em `projects/<id>/custom_scene_01.html` apontando `scene.html_path` para ele.
Contrato obrigatório (**OpenFilm Spec** + **HyperFrames Motion Doctrine**):
- `window.film = { width: 1080, height: 1920, duration, ready, frame(t) }`
- Tudo que depende de tempo `t` ou de áudio precisa ser **função pura de `t`** (use dados pré-computados como `cumBass` carregados em `ready`; nunca acumule estado entre chamadas de `frame(t)`).
- **Fundo opaco** sempre (`#07070c`), curva de entrada `easeOutQuart` / cúbica com rampa no trecho inicial, **zero oscilação idle em títulos**, e respeitar áreas seguras (não sobrepor `y < 140` nem `y > 1600`, pois as legendas `.ass` são queimadas por último pelo FFmpeg).

### Passo 4 — Renderizar Vídeo Único (ou Combo) + QA Automático
```powershell
# Vídeo Único Integrado — Gancho pela Letra + Clean Reels (padrão, com opção --band-clip para clipes reais):
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py create --song "Mother" --artist "Danzig" --audio "inputs\mother.mp3" --opening-mode lyric_hook --overlay-style clean_reels --intro-sec 0.2 --break-sec 3.2 --finale-sec 12 --project-id danzig-mother

# Vídeo Único clássico (Intro musical 1.8s + Tarja superior fixa):
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py create --song "Mother" --artist "Danzig" --audio "inputs\mother.mp3" --opening-mode music_intro --overlay-style banner_top --intro-sec 1.8 --break-sec 2.8 --project-id danzig-mother-classic

# Combo clássico 2 Reels separados:
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py create --song "Mother" --artist "Danzig" --audio "inputs\mother.mp3" --mode two_reels_combo --project-id danzig-mother-2x

# A partir de um roteiro JSON personalizado ou re-render após editar project.json:
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py create --song "Mother" --artist "Danzig" --audio "inputs\mother.mp3" --script-json script.json --project-id danzig-mother
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py render-project --project-id danzig-mother
```

### Passo 5 — Verificação Obrigatória (Nunca pular!)
1. Leia `projects/<id>/project_packed.md` e `projects/<id>/verify/qa_report.json` (ou rode `cli.py verify --project-id <id>`):
   - Verifique `qa_passed: true`
   - Confira `integrated_lufs` (próximo de `-14 LUFS`, sem `true_peak_dbfs > 0.2`)
   - Confira `slideshow_risk.verdict` (`strong` ou `acceptable`, média `< 2.6`)
2. **Inspecione visualmente com `read_file`**:
   - Modo Vídeo Único: `outputs/<id>/contact_sheet_reel_completo.png` (10 quadros + onda sonora mostrando picos na Intro/Respiros/Clímax vs ducking suave na narração + marcadores `INT`/`B1..B4`/`FIN` + palavras faladas) e `outputs/<id>/capa_destaque.png`
   - Modo 2 Reels: `outputs/<id>/contact_sheet_reel1.png`, `contact_sheet_reel2.png` e `capa_destaque.png`
3. Se houver corte estranho, sobreposição de título com legenda ou erro visual, ajuste `project.json` ou o HTML e rode `cli.py render-project`.

---

## Regras Inegociáveis de Qualidade (`Hard Rules`)

1. **Legendas sempre por último (`video-use` Hard Rule 1)**: nunca renderize legendas dentro do HTML por cena; o `app/video_renderer.py` queima o `.ass` global após `overlay` e `xfade` para garantir estilo uniforme, contraste e ausência de corte em transições.
2. **Fade mínimo de 30 ms (`afade`) e Loudnorm (`-14 LUFS`)** em todos os muxes de áudio (`app/audio_processor.py`).
3. **Sem `Mozilla/5.0` no User-Agent da Wikimedia** e sem `filetype:bitmap` nas queries (`WIKI_HEADERS` em `app/visual_assets.py`).
4. **Encerramento limpo do navegador headless**: `app/html_renderer.py` cria `--user-data-dir` temporário por render e finaliza apenas o PID filho (`taskkill /F /T /PID`). Nunca finalize processos Chrome globais do usuário.
