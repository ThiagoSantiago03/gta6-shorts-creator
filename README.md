# GTA 6 Shorts Studio — Automação YouTube Shorts (Zero-API First • 1080×1920)

Estúdio automatizado focado 100% em **GTA 6 / Rockstar Games** para canais de **YouTube Shorts**, projetado para produzir de **5 a 10 vídeos virais por dia**, totalmente autônomo, robusto e com preview em tempo real.

> 📖 **Documentação Arquitetural Completa:** [DOCUMENTACAO_COMPLETA_GTA6_SHORTS.md](DOCUMENTACAO_COMPLETA_GTA6_SHORTS.md)  
> 🔍 **Relatório de Auditoria Técnica:** [AUDITORIA.md](AUDITORIA.md)  
> 🛡️ **Catálogo de Procedência e Licenças:** [inputs/media_catalog.json](inputs/media_catalog.json)

---

## ⚡ Pilares do Estúdio GTA 6

1. **Fundo 100% em Vídeo Dinâmico (Trailers Locais)**:
   - Utiliza o acervo local em `inputs/gta6/` (trailers oficiais, teasers e gameplays em alta resolução).
   - O algoritmo descarta telas pretas, logotipos estáticos e seleciona os melhores momentos de ação urbana e perseguição.
2. **Overlays Estritamente Contextuais (Sem Imagens Aleatórias)**:
   - Imagens só aparecem sobrepostas quando houver correspondência exata com fatos concretos falados na narração (ex: menção a preço no Pix, mapa, consoles, mídia física).
   - Quando não há foto real relevante, o sistema ou gera uma ilustração sob medida via IA Flux (sem API), ou mantém o **vídeo em movimento 100% limpo** (`none_motion_only`).
3. **Padrão Visual & Identidade Vice City**:
   - Resolução vertical nativa **1080×1920 (9:16)** a 30fps.
   - Legendas estilizadas **Vice City** (amarelo/branco com contorno preto e sombra) posicionadas rigorosamente na **safe-zone** do YouTube Shorts.
   - Narração neural padronizada com voz **Antônio (`pt-BR-AntonioNeural`)** em ritmo viral (+14%), cronometrada para **≤ 58 segundos cravados**.
   - Trilha sonora de fundo de ação com equalização suave (`ducking` de voz).
4. **Zero-API por Padrão**:
   - Radar diário via Google Trends e portais de games do Brasil (IGN Brasil, Voxel, Canaltech, TechTudo, etc.).
   - Voz neural Edge-TTS gratuita.
   - Geração de imagem IA gratuita (Flux).
   - Suporte opcional à chave Gemini para roteiros personalizados.

---

## 🚀 Como Iniciar o Painel Web (GTA 6 Shorts Studio)

Dê duplo clique em `run_panel.bat` ou execute no terminal:

```powershell
d:\CriadorDeVideos\.venv\Scripts\python.exe -m uvicorn app.web_server:app --host 127.0.0.1 --port 8765 --reload
```

Acesse no navegador: **`http://127.0.0.1:8765`**

### O Fluxo em 4 Etapas:
1. **Pauta & Radar GTA 6**:
   - Veja notícias quentes ao vivo do dia.
   - Clique em **⚡ Usar esta Pauta & Criar Short** ou use o botão **⚡ Gerar 5 Shorts Hoje** para criar a fila autônoma diária de 5 vídeos.
2. **Roteiro & Locução (58s)**:
   - Gancho viral imediato nos primeiros 3 segundos.
   - Cenas cronometradas com medidor em tempo real (`~48-52s / Máx 58s`).
   - Opção de reescrever com arquétipos virais (*Contraste*, *Raio-X de Números*, *Lista*, *Gameplay*).
3. **Estúdio Visual 9:16 (Preview em Tempo Real)**:
   - Celular 9:16 interativo com prévia instantânea de cada cena antes da renderização.
   - Seletor de trailer de fundo e controle de overlay (*Vídeo Limpo*, *Foto da Notícia*, *Gerar IA Flux*).
   - Botão **🎞️ Prévia MP4 da Cena (~5s)** para ver o trecho renderizado pelo FFmpeg.
4. **Gerar Shorts & YouTube**:
   - Renderização completa via FFmpeg 1080×1920.
   - Pacote de publicação com 1 clique (*Copiar Título, Descrição com Fonte, Tags e Legenda do TikTok*).
   - Gerador de capa / thumbnail de alto impacto 9:16.

---

## 🛠️ Uso via Linha de Comando (CLI)

```powershell
# 1. Ver notícias e tendências em alta hoje
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py radar

# 2. Criar e montar um novo Short a partir de uma pauta
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py create --topic "GTA 6 no PS5 Pro vs Xbox Series" --angle "Desempenho técnico a 60 FPS"

# 3. Criar lote de 5 Shorts automaticamente a partir das maiores notícias do dia
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py batch --count 5

# 4. Renderizar um projeto existente
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py render --project-id <id_do_projeto>

# 5. Listar todos os vídeos e status de renderização
d:\CriadorDeVideos\.venv\Scripts\python.exe cli.py list
```
