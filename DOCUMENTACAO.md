# 📚 Documentação Completa — GTA 6 Shorts Studio

> **Fábrica Autônoma de YouTube Shorts sobre GTA 6 e Rockstar Games**  
> *1080×1920 (9:16 Vertical) • Duração Rigorosa <= 58s • Zero-API First • Locução Neural PT-BR*

---

## 1. Visão Geral do Projeto

O **GTA 6 Shorts Studio** é uma plataforma completa e autônoma desenvolvida em Python e Modern Web (Vanilla JS/CSS) para criação em escala de vídeos verticais (YouTube Shorts, TikTok e Instagram Reels).

O sistema foi arquitetado com a filosofia **Zero-API First**: ele é capaz de operar com **custo zero de APIs**, utilizando inteligência local, web scraping ético de notícias, trailers oficiais de gameplay em alta resolução armazenados localmente e vozes neurais gratuitas de estúdio via Microsoft Edge TTS. Opcionalmente, pode ser potencializado com a **Google Gemini API (Gemini 2.5 Flash)** para refino criativo adicional.

---

## 2. Tecnologias, Bibliotecas e Repositórios Utilizados

| Categoria | Tecnologia / Repositório | Versão | Função no Projeto |
| :--- | :--- | :--- | :--- |
| **Linguagem & Runtime** | Python | 3.14 (64-bit) | Execução de todo o backend, automação e processamento. |
| **Web Framework** | [FastAPI](https://github.com/fastapi/fastapi) | >= 0.115.0 | Servidor de APIs assíncrono de alto desempenho para o painel web. |
| **Servidor ASGI** | [Uvicorn](https://github.com/encode/uvicorn) | >= 0.30.0 | Servidor web ASGI com suporte a hot-reload (`--reload`). |
| **Validação de Dados** | [Pydantic](https://github.com/pydantic/pydantic) | >= 2.7.0 | Schemas estritos para validação de projetos, cenas e requisições. |
| **Síntese de Voz (TTS)** | [edge-tts](https://github.com/rany2/edge-tts) | >= 6.1.12 | Vozes neurais brasileiras gratuitas de estúdio (`pt-BR-AntonioNeural`). |
| **Renderização de Vídeo** | [FFmpeg](https://ffmpeg.org/) via `imageio-ffmpeg` | >= 0.5.1 | Filtergraphs para corte 9:16, Ken Burns suave, ducking de áudio e legendagem. |
| **Download de Mídia** | [yt-dlp](https://github.com/yt-dlp/yt-dlp) | >= 2026.10.0 | Indexação e download de trailers oficiais em 1080p/4K. |
| **Manipulação de Imagem**| [Pillow (PIL)](https://github.com/python-pillow/Pillow) | >= 10.4.0 | Geração de capas (thumbnails 9:16), stickers, overlays e contact sheets. |
| **Processamento Numérico**| [NumPy](https://github.com/numpy/numpy) | >= 2.0.0 | Análise de frames, matrizes de transição e cálculo de motion energy. |
| **Cliente HTTP Assíncrono**| [HTTPX](https://github.com/encode/httpx) | >= 0.27.0 | Varredura de feeds RSS, scraping de portais gamer e chamadas REST. |
| **Scraping Semântico** | BeautifulSoup4 | >= 4.12.0 | Extração de matérias jornalísticas de portais como IGN, Voxel e Canaltech. |
| **IA Generativa (Opcional)**| [google-genai](https://github.com/googleapis/python-genai) | >= 2.30.1 | SDK oficial do Google Gemini para modelos `gemini-2.5-flash`. |
| **Interface Frontend** | Modern Vanilla JS / HTML5 / CSS3 | Nativo | Interface fluida estilo Vice City Neon, sem frameworks pesados de terceiros. |

---

## 3. Agentes e Subagentes Utilizados

No processo de concepção, diagnóstico e resolução de problemas complexos no projeto, atuaram subagentes especializados:

### 🤖 1. Subagente `browser` (`YouTube Shorts Reference Analyzer`)
* **Papel:** Pesquisa visual e análise de referências reais no YouTube Shorts.
* **Atividades Executadas:**
  * Navegação em canais de sucesso sobre GTA 6 (ex.: SanInPlay, Tchelox, IGN).
  * Mapeamento de zonas seguras de tela (*safe zones*) para não cobrir texto com botões de curtir/comentar do YouTube.
  * Análise do ritmo de corte ideal (takes entre 4 e 7 segundos).
  * Avaliação de paletas de cor retrô-futuristas inspiradas em Miami e Vice City.

### 🛠️ 2. Subagente `code-fixer` (`Ken Burns Fix Agent`)
* **Papel:** Engenheiro sênior de algoritmos de vídeo em FFmpeg.
* **Atividades Executadas:**
  * Diagnóstico do problema de tremor e engasgo na animação de imagens estáticas.
  * Substituição dos filtros de loop curto pelo algoritmo Ken Burns contínuo (`d=99999`).
  * Eliminação de pulos visuais ao alternar entre imagens e vídeo.

---

## 4. Skills Integradas no Workspace

As skills são pacotes modulares de instruções e competências carregadas pelo agente para executar tarefas de alta precisão:

### 🎮 1. `gta6-shorts-director`
* **Localização:** `.agents/skills/gta6-shorts-director/SKILL.md`
* **Objetivo:** Diretor executivo e criativo do canal de GTA 6.
* **Regras Estritas:**
  * Formato vertical 1080×1920 (9:16), teto estrito de 58.0 segundos.
  * Fundo sempre em movimento utilizando os trailers cinematográficos locais em `inputs/gta6/`.
  * Overlays contextuais apenas quando o roteiro citar fatos específicos.
  * Voz neural oficial Antônio (`pt-BR-AntonioNeural`) a +14% de velocidade.
  * Trilha Vice City sutil com *audio ducking* automático durante a fala.

### 🌐 2. `chrome-devtools`
* **MCP Server Integrado:** `chrome-devtools`
* **Objetivo:** Automação de QA, pente fino e inspeção de interface direto no navegador.
* **Ações:** Execução de rotinas de teste ponta a ponta, validação de botões, modais, formulários, responsividade e gravação de screenshots de evidência.

### 🧠 3. `gemini-api-dev`
* **MCP Server Integrado:** `gemini-api_gemini-api-docs`
* **Objetivo:** Validação oficial dos métodos atuais do SDK `google-genai` (garantindo o uso de `client.models.generate_content` e modelos modernos `gemini-2.5-flash`).

### 🎵 4. `music-history-director` (Legado)
* **Localização:** `.agents/skills/music-history-director/SKILL.md`
* **Objetivo:** Skill anterior focada em documentários de bandas musicais, mantida para compatibilidade e histórico.

---

## 5. Arquitetura Modular do Backend

O backend está centralizado no diretório `app/` e estruturado em módulos independentes:

```
app/
├── models.py              # Modelos de dados Pydantic (ShortsProject, SceneItem, etc.)
├── web_server.py          # API FastAPI, rotas HTTP e endpoints do estúdio
├── gaming_radar.py        # Varredura de Google Trends, Google News e scraping de matérias
├── gaming_scriptwriter.py # Motor de Roteiros Inteligentes (Storyteller em 6 Atos)
├── gaming_media.py        # Casamento inteligente de trailers, cortes e motion energy
├── video_renderer.py      # Motor de renderização FFmpeg, Ken Burns e filtros verticais
├── tts_engine.py          # Gerador de áudio assíncrono com Microsoft Edge TTS
├── thumbnail_engine.py    # Gerador de capas 9:16 com manchetes neon
├── qa_verifier.py         # Auditoria automática de conformidade, contato e duração
└── config.py              # Gestão de variáveis de ambiente e diretórios
```

### Detalhamento dos Módulos Principais

#### 1. `app/models.py`
Define a entidade fundamental do sistema: `ShortsProject`. Contém todos os dados do projeto: título, pauta, roteiro em cenas, voz, velocidade, arquétipo, metadados de publicação (YouTube/TikTok), status de publicação (`is_posted`, `posted_at`, `posted_url`) e relatórios de QA.

#### 2. `app/gaming_scriptwriter.py`
O cérebro narrativo do sistema. Transforma qualquer notícia em um roteiro eletrizante de 6 atos conectados:
1. **Cena 1 (🎯 Gancho Viral):** 16 a 22 palavras — curiosidade imediata nos primeiros 3 segundos.
2. **Cena 2 (💰 A Revelação):** 22 a 28 palavras — quem, o que e o fato concreto com sujeito e predicado completos.
3. **Cena 3 (📊 Comparação de Escala):** 22 a 28 palavras — números e proporções dimensionados para o espectador.
4. **Cena 4 (🏙️ Na Prática / Onde foi a grana):** 22 a 28 palavras — impacto nas mecânicas, NPCs ou mapa.
5. **Cena 5 (⚡ A Virada no Bolso / Conflito):** 18 a 24 palavras — o dilema da comunidade ou debate.
6. **Cena 6 (📣 CTA Final):** 14 a 20 palavras — pergunta polarizadora A vs B para forçar comentários.

#### 3. `app/video_renderer.py`
Motor de processamento multimídia baseado em FFmpeg:
* **Escala e Corte Vertical:** Transforma vídeos horizontais 16:9 em imersão vertical completa (`scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920`).
* **Fórmula Ken Burns Contínua:** Zoompan suave sem solavancos para imagens estáticas (`d=99999`).
* **Audio Ducking:** Locução em volume 1.0 e trilha de fundo a volume 0.12, mantendo clareza absoluta na voz.

---

## 6. Interface Web & Estúdio de Edição Manual (4 Etapas)

O painel web roda localmente em `http://127.0.0.1:8765/` e é dividido em 4 etapas lineares:

### Etapa 1: Pauta, Notícia & Radar
* Varre em tempo real matérias sobre GTA 6 no Brasil via Google Trends e RSS.
* Botão **"Usar Esta Notícia"** preenche o projeto automaticamente e gera o roteiro.
* Botão **"⚡ Gerar 5 Shorts Hoje (Autônomo)"** cria um lote completo para o dia.

### Etapa 2: Roteiro Viral & Locução PT-BR
* Cada cena possui seu próprio campo de texto.
* **Medidor de Duração em Tempo Real:** Recalcula palavras e segundos a cada tecla digitada.
* Seletor de vozes neurais e velocidade (+12%, +14%, +16%).
* Botão **"Reescrever Roteiro"** para reprocessar o texto com outros arquétipos.

### Etapa 3: Estúdio Visual 9:16 & Editor de Cenas
* **Moldura de Smartphone 9:16:** Prévia em tempo real de como o Short ficará no celular.
* **Controle de Trailer:** Seleção do arquivo MP4 e do segundo inicial de corte (`video_start_sec`).
* **Seletor de Modo de Camada:**
  * *🚫 Vídeo Limpo (none_motion_only):* Apenas o trailer em alta velocidade.
  * *📰 Foto da Notícia (matched_photo):* Card flutuante com a imagem da matéria.
  * *🎨 Ilustração IA (ai_image_prompt):* Geração sob demanda via Flux.
* **Botão "Renderizar Prévia MP4 Desta Cena":** Gera um clipe instantâneo de teste de apenas uma cena.

### Etapa 4: Gerar Shorts, Capa e Pacote YouTube
* **Renderização Completa:** Gera o arquivo final MP4 vertical em 1080×1920 a 30fps.
* **Controle de Publicação:**
  * Botão **"Marcar como Postado"**.
  * Seletor de plataforma (YouTube Shorts, TikTok, Instagram Reels).
  * Registro de data e hora automático.
  * Campo para salvar o link do vídeo publicado (`https://youtube.com/shorts/...`).
* **Estúdio de Capa 9:16:** Geração de thumbnails com manchetes impactantes e cortes nítidos dos trailers.
* **Botões de Cópia em 1 Clique:** Copia o título otimizado (<95 caracteres), descrição com hashtags e primeiro comentário fixado.

---

## 7. Sistema de Controle de Publicação

Para operar um canal de alta escala (5 a 10 vídeos diários), a plataforma possui um sistema de triagem na barra lateral:

* **Abas de Filtro:**
  * **`Todos`**: Visualização de todo o catálogo.
  * **`⏳ Pendentes`**: Apenas vídeos renderizados que ainda aguardam postagem.
  * **`✅ Postados`**: Histórico dos vídeos já publicados.
* **Badges Visuais:**
  * `✅ Postado` *(Roxo neon)*
  * `🚀 Pronto` *(Ciano neon)*
  * `📝 Rascunho` *(Cinza)*
* **Botão Rápido de 1 Clique:** Permite alternar o status de postado/pendente diretamente no card do vídeo na barra lateral.

---

## 8. CLI e Automação via Terminal

Além da interface gráfica, o projeto pode ser totalmente operado via linha de comando através de `cli.py`:

```powershell
# 1. Iniciar o painel web local
run_panel.bat

# 2. Varrer radar de tendências e notícias
.venv\Scripts\python.exe cli.py radar --query "GTA 6" --geo "BR"

# 3. Criar projeto individual via terminal
.venv\Scripts\python.exe cli.py create --topic "GTA 6 Armas e Perseguições" --angle "Detalhes do combate policial"

# 4. Criar lote diário de 5 Shorts automaticamente
.venv\Scripts\python.exe cli.py batch --count 5

# 5. Renderizar projeto para MP4 vertical
.venv\Scripts\python.exe cli.py render --project-id <id-do-projeto>

# 6. Listar projetos existentes e status
.venv\Scripts\python.exe cli.py list
```

---

## 9. Estrutura de Diretórios de Mídia e Acervo

```
inputs/
└── gta6/                          # Acervo de trailers cinematográficos locais em alta definição
    ├── GTA Revelacao Capa Oficial.mp4
    ├── GTA Trailer 1.mp4
    ├── GTA Trailer 2.mp4
    ├── GTA Trailer de Lançamento.mp4
    ├── Miami Night Highway.mp3    # Trilhas de fundo para ducking
    └── Neon Arpeggios.mp3

projects/                          # Projetos em edição (dados brutos, áudios TTS e cenas)
outputs/                           # Vídeos finais renderizados (MP4 vertical, capa e pacote de texto)
```

---

## 10. Resumo de Conformidade e Garantias Técnicas

* **Formato:** 1080×1920 (9:16 vertical nativo).
* **Taxa de Quadros:** 30 fps constante com áudio AAC estéreo.
* **Duração:** Rigorosamente <= 58.0 segundos (impossibilita conversão para vídeo longo no YouTube).
* **Safe-Zones:** Legendas e overlays posicionados a pelo menos 220px do rodapé para não colidir com o título e os botões nativos do YouTube Shorts.
* **Custo Operacional:** R$ 0,00 (Zero-API First).
