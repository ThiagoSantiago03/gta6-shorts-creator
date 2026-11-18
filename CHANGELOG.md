# Changelog — GTA 6 Shorts Studio

Todas as alterações relevantes e mudanças de comportamento do projeto são registradas aqui.

## [Não lançado / Ciclo de Auditoria Pós-Etapa 5]

### Corrigido
- **T1 (Fila de Jobs):** Adicionado `recover_interrupted_jobs()`, rastreamento real do job ativo (`_current_job_id`), deduplicação na fila (`_queued`) e idempotência na etapa `roteiro`. Jobs `executando` (órfãos após crash/reinício) e `pendente` agora são reenfileirados e concluídos automaticamente após o registro dos handlers de produção.
- **T2 (Caminho da Fila):** `JOBS_DIR` agora deriva de `app.config.INPUTS_DIR / "jobs"` (caminho absoluto independente do diretório de execução).
- **T3 (Segurança de Uploads):** Todos os 7 endpoints de upload agora gravam em blocos de 1 MB via `_save_upload`, respeitando limites configuráveis (`MAX_UPLOAD_VIDEO_MB`, `MAX_UPLOAD_AUDIO_MB`, `MAX_UPLOAD_IMAGE_MB`), retornando HTTP 413 acima do limite e removendo arquivos temporários `.part`.
- **T4 (Segurança Local Host/Origin):** Adicionados `TrustedHostMiddleware` (`ALLOWED_HOSTS`, padrão `127.0.0.1,localhost`) e validação de cabeçalho `Origin` em requisições de escrita (HTTP 403 para origens externas).
- **T5 (Regra Única de Direitos — MUDANÇA DE COMPORTAMENTO):** Unificadas as 4 regras divergentes de direitos em `app/rights.py` (`media_allowed`).
  - **Mudança intencional:** clipes anexados (`band_video_clips`) sem registro em `inputs/media_catalog.json` agora são rejeitados em todas as finalidades (antes passavam silenciosamente em `_collect_interleaved_cuts`).
  - **Mudança intencional:** a finalidade `non_commercial` agora segue a mesma política mínima de `editorial` (bloqueando mídias `unknown` e `restricted` tanto na seleção quanto no QA).
  - **Mudança intencional:** `auto_select_gaming_soundtrack` não faz mais fallback para trilhas não autorizadas quando nenhuma trilha comercial é encontrada (retorna `None`, permitindo uso da cama synthwave procedural local).
- **T6 (Catálogo de Mídias — MUDANÇA DE DADOS):** Os 4 trailers da Rockstar em `inputs/media_catalog.json` tiveram `authorization_status` alterado de `official_permission` para `fair_use_claimed`, com `proof_url: null` e `verified_at: null` (mantendo `commercial_use_allowed: false`). Arquivos não catalogados nunca mais presumem `official_permission` pelo nome.
- **T7 (Visibilidade de `publish_intent`):** `publish_intent` (`commercial` padrão seguro, `editorial`, `non_commercial`) agora é configurável na UI, CLI (`--intent`), API REST e fila de jobs. Adicionado endpoint `GET /api/projects/{id}/rights-preview` com aviso explícito na interface quando nenhum trailer está liberado para a finalidade escolhida.
- **T8 (Validação Editorial em `pesquisa` — MUDANÇA DE COMPORTAMENTO):** Ausência de `status_confiabilidade` no `input_data` da fila agora assume `nao_verificado` (antes assumia `reportado`). Pautas `nao_verificado` exigem `manual_approved=True`; pautas `rumor`/`reportado` exigem `url` ou `source`.

### Infraestrutura e Documentação
- **T9:** Consolidado lockfile único em UTF-8 (`requirements-lock.txt`, sem pacotes de dev), removido `requirements.lock` duplicado, criado `requirements-dev.txt` e atualizado `.github/workflows/ci.yml` para usar o lock em Python 3.11, 3.12 e 3.14 (com verificação semanal sem lock).
- **T10 / T11:** Criados `ARCHITECTURE.md`, `ROADMAP.md` e `CHANGELOG.md`; `AUDITORIA.md` arquivado em `docs/historico/AUDITORIA_ETAPA0.md`; `DOCUMENTACAO.md` duplicado removido; terminologia "Zero-API" substituída por "Sem chaves de API obrigatórias (serviços externos opcionais)"; `.agents/legacy/` documentado como histórico.
- **T12 (Qualidade / Exceções):** Adicionados logs estruturados em todos os 14 handlers `except ...: continue` de `app/`; teste AST `test_no_swallowed_exceptions.py` estendido para cobrir `Pass` e `Continue` com allowlist explícita contendo apenas `app/job_queue.py:_worker_loop` (`queue.Empty`).
- **T13 (Refatoração & Isolamento de Legado):**
  - Criado teste de caracterização real via FFmpeg (`tests/test_render_gaming_short_characterization.py`) validando resolução 1080×1920, 30 fps, H.264/yuv420p, áudio AAC, duração ≤ 58s e QA.
  - Extraída `render_gaming_youtube_short` (409 linhas) em 4 etapas nomeadas (`_synthesize_gaming_short_narration`, `_build_gaming_short_audio_plan`, `_render_gaming_short_scenes`, `_composite_mux_and_qa_gaming_short`) sem alterar o comportamento.
  - Movidos os 5 módulos legados (`music_researcher`, `lyrics_engine`, `script_generator`, `html_renderer`, `motion_design`) para `app/legacy/` com *shims* de compatibilidade em `app/<modulo>.py`, `find_headless_browser()` centralizado em `app/config.py`, 8 rotas antigas marcadas como `deprecated=True` no FastAPI e isolamento garantido por `tests/test_legacy_isolation.py`.
