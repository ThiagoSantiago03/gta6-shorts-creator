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
