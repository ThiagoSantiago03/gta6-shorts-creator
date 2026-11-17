---
name: gta6-shorts-director
description: Diretor completo de YouTube Shorts sobre GTA 6 e Rockstar Games em PT-BR (1080x1920 • <= 58,0s • Sem API paga por padrão). Use ao criar roteiros virais, montar fundos em movimento dos trailers locais, sincronizar overlays contextuais e renderizar vídeos finais no workspace CriadorDeVideos.
---

# GTA 6 YouTube Shorts Director Skill (Sem API Paga por Padrão)

Use esta skill para criar, gerenciar e auditar vídeos de **YouTube Shorts de GTA 6** de alta retenção no workspace.

## Pilares de Direção
1. **Fundo em Movimento Contínuo**:
   - Todo Shorts usa cortes dos trailers locais em `inputs/gta6/` como fundo dinâmico.
   - O algoritmo avalia energia visual, bordas e luminosidade, descartando telas pretas e cartões estáticos.
2. **Overlays Contextuais**:
   - Só exibe foto sobreposta na tela se o roteiro mencionar concretamente o assunto (preço, consoles, mapa, mídia física).
   - Sem fotos aleatórias. Se não houver correspondência exata, mantém o **vídeo em movimento limpo** (`none_motion_only`) ou gera uma ilustração via IA Flux (`generate-ai-overlay`).
3. **Padrão Técnico & Identidade Vice City**:
   - Duração rigorosamente menor ou igual a **58.0 segundos**.
   - Resolução vertical **1080×1920 (9:16)** a 30fps.
   - Legendas estilizadas **Vice City** (amarelo/branco com contorno preto) posicionadas na safe-zone vertical (acima dos botões do Shorts).
   - Voz neural padronizada: **Antônio (`pt-BR-AntonioNeural`)** em ritmo viral (+14%).
   - Trilha sonora de rock/synthwave com ducking durante a fala.

## Comandos Principais
```bash
# Iniciar Painel Web
python -m uvicorn app.web_server:app --host 127.0.0.1 --port 8765

# Radar de Notícias do Dia
python cli.py radar

# Criar Novo Short
python cli.py create --topic "<Manchete>" --archetype "auto"

# Criar Lote Autônomo Diário (5 Shorts)
python cli.py batch --count 5

# Renderizar Vídeo
python cli.py render --project-id <id>
```
