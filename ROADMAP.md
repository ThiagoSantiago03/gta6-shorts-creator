# Roadmap — GTA 6 Shorts Studio

> Acompanhamento objetivo de correções, prioridades e critérios de conclusão.

## Status do Plano de Correção

| ID | Fase | Descrição | Prioridade | Status | Critério de conclusão |
|---|---|---|---|---|---|
| **T0** | Baseline | Baseline da suíte `pytest -q` e inspeção de ambiente/FFmpeg | Obrigatório | ✅ Concluída | `79 passed` registrados antes das mudanças |
| **T1** | A — Bugs reais | Recuperação da fila após reinício, `_current_job_id`, `_queued` e `roteiro` idempotente | Máxima | ✅ Concluída | Jobs `executando`/`pendente` recuperados após reinício; 3 testes novos verdes |
| **T2** | A — Bugs reais | `JOBS_DIR` absoluto via `INPUTS_DIR` independente do `cwd` | Alta | ✅ Concluída | `JOBS_DIR.is_absolute()` validado sob `monkeypatch.chdir` |
| **T3** | B — Segurança local | Limite de tamanho em blocos (1 MB) nos 7 endpoints de upload (HTTP 413) | Alta | ✅ Concluída | Upload excedente retorna 413 sem deixar `.part` em disco |
| **T4** | B — Segurança local | `TrustedHostMiddleware` + checagem de `Origin` em métodos de escrita | Média | ✅ Concluída | Host estranho → 400; Origin estranho → 403; todos os testes `TestClient` verdes |
| **T5** | C — Direitos | Regra única `media_allowed` em `app/rights.py` nos 4 pontos + ramo `non_commercial` | Alta | ✅ Concluída | 4 pontos unificados; matriz parametrizada nas 3 finalidades verde |
| **T6** | C — Direitos | Rótulo honesto `fair_use_claimed` nos 4 trailers sem `proof_url` inventado | Alta | ✅ Concluída | Nenhum `official_permission` sem `proof_url` real |
| **T7** | C — Direitos | `publish_intent` configurável na UI/CLI/API/fila + `GET /api/projects/{id}/rights-preview` | Alta | ✅ Concluída | Intenção propagada ponta a ponta; aviso explícito antes do render |
| **T8** | D — Editorial | Etapa `pesquisa` exige aprovação manual para `nao_verificado` e fonte para `rumor`/`reportado` | Alta | ✅ Concluída | Pauta sem status bloqueada sem `manual_approved=True` |
| **T9** | E — Build e docs | Lockfile único UTF-8 (`requirements-lock.txt`), `requirements-dev.txt` e CI com lock | Média | ✅ Concluída | `requirements.lock` removido; CI instala lock em 3.11/3.12/3.14 |
| **T10** | E — Build e docs | Consolidação de `ARCHITECTURE.md`, `ROADMAP.md`, `CHANGELOG.md`, arquivamento de `AUDITORIA.md` | Média | ✅ Concluída | Sem contradições de versão, etapas ou "Zero-API" |
| **T11** | E — Build e docs | Verificação e documentação de `.agents/legacy/skills/music-history-director` | Baixa | ✅ Concluída | Confirmado fora do fluxo principal e documentado como histórico (não apagado) |
| **T12** | F — Qualidade | Auditoria de exceções silenciosas (`pass` e `continue`) com allowlist justificada | Média | ✅ Concluída | Teste AST cobre `Pass` e `Continue` com allowlist explícita (`app/job_queue.py:_worker_loop`) |
| **T13** | F — Qualidade | Caracterização de `render_gaming_youtube_short` e isolamento de legado em `app/legacy/` | Baixa | ⏳ Planejada | Fluxo GTA 6 sem imports de legado; PRs pequenos após aprovação |
| **T14** | G — Produto | Aprovação humana obrigatória, limite diário, variação real de formato e analytics | Opcional | ⏸️ Aguardando decisão humana | Implementar apenas após confirmação do dono do projeto |
