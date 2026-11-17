---
name: gta6-shorts-director
description: Diretor completo de YouTube Shorts sobre GTA 6 e Rockstar Games em PT-BR (1080x1920 • <= 58s • Zero-API First). Use ao criar roteiros virais, montar fundos em movimento dos trailers locais, sincronizar overlays contextuais e renderizar vídeos finais no workspace CriadorDeVideos.
---

# GTA 6 YouTube Shorts Director Skill (Zero-API First)

Use esta skill para criar, gerenciar e auditar vídeos de **YouTube Shorts de GTA 6** de alta retenção no workspace `d:\CriadorDeVideos`.

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
   - Trilha sonora de rock/trailer de ação com equalização suave (`ducking` durante a fala).

## Comandos Principais
```powershell
# Iniciar Painel Web
run_panel.bat

# Radar de Notícias do Dia
.venv\Scripts\python.exe cli.py radar

# Criar Novo Short
.venv\Scripts\python.exe cli.py create --topic "<Manchete>" --angle "<Ângulo>"

# Criar Lote Autônomo Diário (5 Shorts)
.venv\Scripts\python.exe cli.py batch --count 5

# Renderizar Vídeo
.venv\Scripts\python.exe cli.py render --project-id <id>
```
