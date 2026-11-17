# Arquitetura Atual — GTA 6 Shorts Studio

> **Fonte única da arquitetura atual do código.**  
> **Runtime suportado (validado no CI):** Python 3.11, 3.12 e 3.14 (Windows e Linux).  
> **Operação:** Local-first, sem chaves de API obrigatórias (serviços externos opcionais: Edge-TTS, Pollinations, Gemini).

---

## 1. Visão Geral

O **GTA 6 Shorts Studio** é uma aplicação local em **FastAPI + FFmpeg + SPA em HTML/JS/CSS puro** voltada à criação de vídeos verticais **1080×1920 (9:16), 30 fps, ≤ 58,0 s** para YouTube Shorts.

- **Painel Web (`static/index.html`):** fluxo interativo em **4 passos** (`1. Radar & Pauta` → `2. Roteiro & Locução` → `3. Cenas & Mídia` → `4. Render, QA & Entrega`).
- **Fila de Produção em Lote (`app/job_queue.py`):** pipeline sequencial em **5 estágios** (`pesquisa` → `roteiro` → `tts` → `render` → `qa`).
- **CLI (`cli.py`):** subcomandos `radar`, `create`, `batch`, `render`, `list` (com suporte a `--intent commercial|editorial|non_commercial`).

---

## 2. Já implementado — NÃO reimplementar

| Recurso | Localização no código |
|---|---|
| Escrita atômica + backup `.bak` + lock por projeto | `app/web_server.py` (`_save_project`, `_load_project`, `_PROJECT_LOCKS`) |
| Controle otimista de concorrência (HTTP 409 via `revision`) | `PUT /api/projects/{id}` em `app/web_server.py` |
| Validação de `project_id` e proteção contra path traversal | `_project_dir`, `_output_dir`, `clip-thumb`, `attach-input-audio`, `outputs/{kind}` |
| Servidor restrito a `127.0.0.1` + `TrustedHostMiddleware` + validação de `Origin` | `app/web_server.py` (`_ALLOWED_HOSTS`, `reject_foreign_origin`) |
| Limite de tamanho em blocos (1 MB) nos 7 endpoints de upload (HTTP 413, sem `.part`) | `app/web_server.py` (`MAX_UPLOAD_BYTES`, `_save_upload`) |
| Execução de subprocessos sem `shell=True` e XML seguro via `defusedxml` | `app/gaming_radar.py`, `app/video_renderer.py`, `app/qa_verifier.py` |
| Regra única de direitos autorais para `commercial`, `editorial` e `non_commercial` | `app/rights.py` (`media_allowed`), usada em `gaming_media.py` e `qa_verifier.py` |
| Catálogo de procedência de mídias com rótulos honestos (`fair_use_claimed`) | `inputs/media_catalog.json` |
| Prévia de direitos antes do render e seleção explícita de `publish_intent` | `GET /api/projects/{id}/rights-preview`, UI, CLI e fila |
| Classificação editorial em 4 classes (`confirmado`, `reportado`, `rumor`, `nao_verificado`) | `app/gaming_radar.py`, `app/models.py` |
| Verificação editorial real na etapa `pesquisa` (aprovação manual e fonte obrigatória) | `app/job_queue.py` (`_pesquisa`) |
| Fila sequencial FIFO com isolamento de falha, recuperação após reinício e `roteiro` idempotente | `app/job_queue.py` (`JobQueueManager`, `recover_interrupted_jobs`) |
| Arquétipos narrativos virais + controle real de duração (≤ 58,0 s) | `app/gaming_scriptwriter.py`, `app/tts_engine.py` |
| Uso transformativo de trailers (≤ 6,0 s por corte), enquadramentos (`crop_center`, `crop_focus`, `blur_fill`), Ken Burns exato (`d=n_frames`) | `app/gaming_media.py`, `app/video_renderer.py` |
| Mixagem com `sidechaincompress` + `loudnorm` EBU R128 + True Peak ≤ -1.0 dBTP | `app/audio_processor.py`, `app/qa_verifier.py` |
| Legendas dinâmicas ASS por palavra (`WordBoundary`) respeitando Safe Zones (`250/320/60/60 px`) | `app/subtitle_engine.py`, `app/config.py` |
| QA objetivo em 4 categorias (`tecnica`, `narrativa`, `visual`, `editorial`) | `app/qa_verifier.py` (`evaluate_objective_qa_criteria`) |

---

## 3. Módulos Principais (`app/`)

| Módulo | Responsabilidade |
|---|---|
| `app/config.py` | Caminhos absolutos (`INPUTS_DIR`, `PROJECTS_DIR`, `OUTPUTS_DIR`), Safe Zones configuráveis, verificação do FFmpeg (`verify_ffmpeg_installation`). |
| `app/models.py` | Schemas Pydantic (`ShortsProject` / `MusicStoryProject`, `SceneItem`, `WordTiming`, `CreateProjectRequest`). |
| `app/rights.py` | Regra única de direitos (`media_allowed`) e vocabulário oficial de `authorization_status`. |
| `app/gaming_radar.py` | Coleta RSS (Google Trends, Google News, Bing, portais), deduplicação e classificação de confiabilidade. |
| `app/gaming_scriptwriter.py` | Motor de roteiros virais (4 arquétipos), sanitização de rumores e orçamento de duração. |
| `app/gaming_media.py` | Catálogo de mídias, cortes temáticos de trailers, overlays e seleção de trilha sonora. |
| `app/tts_engine.py` | Síntese vocal via Microsoft Edge-TTS (com `WordBoundary`) ou Gemini TTS opcional. |
| `app/subtitle_engine.py` | Geração de legendas `.ass` sincronizadas por palavra dentro das Safe Zones. |
| `app/audio_processor.py` | Análise de envelope/batidas, ducking com `sidechaincompress` e masterização `loudnorm`. |
| `app/video_renderer.py` | Pipeline FFmpeg vertical 9:16, enquadramentos, Ken Burns e geração de pacote de entrega. |
| `app/qa_verifier.py` | Inspeção técnica FFmpeg/EBU R128, contact sheets, prancha de timeline e checklist objetivo de QA. |
| `app/job_queue.py` | Fila FIFO persistente em `INPUTS_DIR / "jobs"` com recuperação pós-reinício e retomada por etapa. |
| `app/web_server.py` | API REST FastAPI, middlewares de segurança (`TrustedHostMiddleware`, `Origin`) e servidor de arquivos estáticos. |

> **Módulos legados (nicho anterior de História da Música):** `music_researcher.py`, `lyrics_engine.py`, `script_generator.py`, `html_renderer.py`, `motion_design.py`. Ainda importados por rotas antigas em `web_server.py`; isolamento planejado em T13.

---

## 4. Aviso de Direitos Autorais

O catálogo (`inputs/media_catalog.json`) registra decisões e origem das mídias; **não confere autorização**. Fair use varia por país e não impede Content ID, reivindicações ou strikes. Para canais monetizados, consulte um profissional de direitos autorais.
