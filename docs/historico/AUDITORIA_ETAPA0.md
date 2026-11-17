> [!WARNING]
> **Documento histórico: não descreve o estado atual.** Consulte ARCHITECTURE.md, ROADMAP.md e CHANGELOG.md na raiz do repositório.

# AUDITORIA TÉCNICA DO PROJETO — GTA 6 SHORTS STUDIO
**Data:** 16 de Novembro de 2026  
**Repositório:** `D:\CriadorDeVideos`  
**Escopo:** Etapa 0 — Auditoria profunda de código (Histórica)

> ⚠️ **NOTA DE STATUS (DOCUMENTO HISTÓRICO):**  
> Este relatório registra o diagnóstico inicial da Etapa 0. As tarefas subsequentes corrigiram ou esclareceram diversos pontos técnicos aqui levantados:  
> 1. **Escrita Atômica:** Já implementada no `web_server.py` e `cli.py` utilizando gravação em arquivo temporário (`.tmp`), substituição atômica (`replace`) e backup automático da versão anterior (`project.json.bak`).  
> 2. **Ducking e Loudnorm:** O `audio_processor.py` já emprega filtro `loudnorm=I=-14:TP=-1.5:LRA=11`, limitador de pico `alimiter=limit=0.95` e envelope temporal de atenuação (0.16 → 0.52). A evolução planejada (M-06) é a adição de `sidechaincompress`.  
> 3. **Ken Burns:** O número de quadros (`n_frames`) é calculado pela duração real da cena e o trecho é delimitado por `-t`; o valor `d=99999` é uma ineficiência de processamento do FFmpeg, e não uma dessincronia de áudio/vídeo.  
> 4. **Parser RSS:** O radar utiliza `xml.etree.ElementTree` da biblioteca padrão do Python, não dependendo de BeautifulSoup no fluxo ativo.  
> Para a documentação arquitetural consolidada e atualizada, consulte [DOCUMENTACAO_COMPLETA_GTA6_SHORTS.md](DOCUMENTACAO_COMPLETA_GTA6_SHORTS.md).

---

## 1. Responsabilidade Real de Cada Arquivo

Abaixo está o inventário de todos os arquivos de código-fonte de `app/`, `cli.py` e do frontend (`static/`):

| Arquivo | Tamanho | Responsabilidade Real no Código |
| :--- | :--- | :--- |
| `app/__init__.py` | 62 B | Inicializador do pacote Python `app`. |
| `app/config.py` | 2.5 KB | Define caminhos absolutos (`PROJECTS_DIR`, `OUTPUTS_DIR`, `INPUTS_DIR`, `FONTS_DIR`) e resolve binários do FFmpeg/FFprobe (`imageio-ffmpeg` ou PATH do sistema). |
| `app/models.py` | 8.8 KB | Modelos Pydantic (`MusicStoryProject`, `SceneItem`, `WordTiming`, `GamingRadarItem`). Estrutura todos os dados de estado do projeto e schema de persistência. |
| `app/web_server.py` | 72.2 KB | Servidor web FastAPI (porta 8765). Gerencia rotas de CRUD de projetos, busca do radar, geração de roteiro, renderização, QA e controle de publicação. |
| `app/gaming_radar.py` | 48.4 KB | Monitor de tendências e notícias. Faz scraping de RSS do Google Trends e páginas de busca do Google News com HTTPX e BeautifulSoup. |
| `app/gaming_scriptwriter.py` | 37.9 KB | Gerador de roteiros para GTA 6. Cria estrutura em 6 atos (gancho, curiosidade, contexto, dado, impacto, debate CTA) com contagem de palavras e fallback Gemini. |
| `app/gaming_media.py` | 66.7 KB | Indexa trailers locais (`inputs/gta6/*.mp4`), extrai cortes de ação aleatórios/contextuais e seleciona trilhas sonoras locais (`inputs/gta6/*.mp3`). |
| `app/visual_assets.py` | 92.2 KB | Motor de busca/geração de mídia visual. Integração com endpoint Pollinations Flux (`https://image.pollinations.ai`), Wikimedia e geração de imagens de suporte. |
| `app/video_renderer.py` | 115 KB | Orquestrador de renderização FFmpeg. Concatena cenas, aplica zoompan (Ken Burns), renderiza overlays, queima legendas e mixa áudio e BGM em 1080x1920 9:16. |
| `app/audio_processor.py` | 36.8 KB | Processador de áudio. Manipulação de volumes, detecção de batidas (BPM), mixagem com ducking estático e corte de trilhas sonoras. |
| `app/tts_engine.py` | 11.3 KB | Motor de sintetização de voz via `edge-tts` (Microsoft Edge TTS online, voz `pt-BR-AntonioNeural` a +14%), gerando timestamps de palavras. |
| `app/subtitle_engine.py` | 9.1 KB | Gerador de legendas formatadas (formato ASS/SRT) com estilos temáticos neon Vice City e destaques coloridos. |
| `app/thumbnail_engine.py` | 42.8 KB | Gerador de capas 9:16 verticais de alta nitidez com PIL, aplicando tipografia GTA, bordas neon e vinhetas cinematográficas. |
| `app/qa_verifier.py` | 23.0 KB | Verificador pós-renderização. Inspeciona vídeo via ffprobe (1080x1920, duração <= 58.5s), mede LUFS e true peak com ebur128 e gera pranchas de contato visuais. |
| `app/shorts_analyzer.py` | 12.3 KB | Avaliador heurístico de ritmo e retenção para YouTube Shorts (taxa de palavras por segundo, impacto do gancho). |
| `app/music_researcher.py` | 33.4 KB | **Legado:** Módulo de pesquisa enciclopédica de bandas/músicas (Wikipedia, Discogs). Inativo no fluxo atual de GTA 6. |
| `app/lyrics_engine.py` | 14.5 KB | **Legado:** Sincronizador e tradutor de letras de música. Inativo no fluxo de GTA 6. |
| `app/script_generator.py` | 21.6 KB | **Legado:** Gerador de roteiros para documentários musicais. Substituído por `gaming_scriptwriter.py`. |
| `app/html_renderer.py` | 57.0 KB | **Especializado/Legado:** Renderizador de animações via HTML/Canvas headless. Pouco acionado no pipeline de vídeo único de GTA 6. |
| `app/motion_design.py` | 39.2 KB | **Especializado/Legado:** Gerador de fundos procedurais com NumPy/PIL. Em grande parte substituído pelo uso direto de trailers. |
| `cli.py` | 11.0 KB | Interface de linha de comando (`radar`, `create`, `batch`, `render`, `list`). |
| `static/index.html` | 34.2 KB | Interface visual SPA de 5 passos (Radar, Roteiro, Editor de Cenas, Render/QA, Publicados) com sidebar e filtros de status. |
| `static/app.js` | 64.1 KB | Lógica do cliente SPA: chamadas de API, polling de jobs, alternância de status de publicação, player de vídeo e edição de cenas. |
| `static/styles.css` | 46.8 KB | Estilização responsiva Dark Mode Vice City Neon com paleta ciano/magenta e suporte a badges de publicação. |

---

## 2. Classificação de Funcionalidades (Documentação vs Realidade)

| Recurso | Status | Diagnóstico Técnico Real no Código |
| :--- | :--- | :--- |
| **Radar de Notícias** | **Implementado** | Funciona via scraping direto de RSS do Google Trends e Google News (`app/gaming_radar.py`). Retorna títulos, fontes, pontuação de relevância e URLs reais. |
| **Roteiro em 6 Atos** | **Implementado** | Funciona em `app/gaming_scriptwriter.py`. Gera 6 cenas com limites de palavras por ato e ganchos em PT-BR. Porém, a estrutura é rígida (sempre 6 atos) e repete a mesma pergunta final CTA "A vs B". |
| **Trailer + Ken Burns** | **Implementado** (com ressalvas) | Funciona em `app/gaming_media.py` e `app/video_renderer.py`. Faz cortes de trailers locais em `inputs/gta6/`. O Ken Burns em imagens estáticas usa `d=99999` (fixo, não sincronizado com o tempo da cena) e o crop vertical `1080:1920` corta as laterais de 16:9 sem opção de foco ou blur. |
| **TTS (Voz Sintética)** | **Implementado** (com ressalva de rede) | Funciona via `edge-tts` com voz `pt-BR-AntonioNeural` a +14%. **Ressalva:** não é "Zero-API / 100% offline", pois depende dos servidores da Microsoft na nuvem. Se a internet cair, o TTS falha. |
| **Ducking de Áudio** | **Parcial** | A voz e a música são sobrepostas com volume fixo (`voice=1.0`, `bgm=0.12`) via filtro `amix` do FFmpeg. Não há compressão dinâmica via `sidechaincompress` nem normalização automática `loudnorm`. |
| **QA Verifier** | **Parcial** | Funciona para validação técnica com FFprobe (resolução, duração, codecs) e EBU R128 (LUFS e True Peak), além de gerar contact sheets com waveform. **Não verifica:** safe zones visuais, licença de mídias, corte de palavras de legenda ou consistência factual. |
| **Controle de Publicação** | **Implementado** | Implementado com sucesso no modelo (`is_posted`, `posted_at`, `posted_url`, `posted_platform`), com alternância em 1 clique na sidebar, filtros Todos/Pendentes/Postados e formulário de detalhes no Passo 4. |
| **Geração de Capa** | **Implementado** | Funciona em `app/thumbnail_engine.py`. Produz pôster vertical 9:16 estilizado com frames do vídeo, vinheta escura, moldura neon e tipografia impactante. |
| **Flux (Ilustração IA)** | **Parcial** | O código em `app/visual_assets.py` chama a API pública gratuita do Pollinations (`https://image.pollinations.ai/prompt/...`). Não há fallback configurado caso o serviço externo esteja instável ou fora do ar, e não exige chave de API. |

---

## 3. Como os Projetos São Salvos Hoje

1. **Formato e Localização:**
   - Cada projeto é salvo como um arquivo JSON monolítico em `projects/{project_id}/project.json`.
   - As mídias geradas (áudios TTS, imagens, vídeos cortados) ficam na mesma pasta `projects/{project_id}/`.
   - O vídeo final renderizado é copiado para `outputs/{project_id}/single_video.mp4`.
2. **Método de Gravação:**
   - A gravação é feita via `(pdir / "project.json").write_text(project.model_dump_json(indent=2), encoding="utf-8")` na função `_save_project` em `app/web_server.py` e em `cli.py`.
3. **Riscos Identificados:**
   - **Ausência de Escrita Atômica:** Não usa gravação em arquivo temporário com `replace`. Se o processo for interrompido durante a escrita, o arquivo `project.json` fica corrompido ou com 0 bytes.
   - **Sem Backup Automático:** Não há criação de `project.json.bak` antes de sobrescrever.
   - **Sobrescrita Acidental:** Não existe controle de concorrência ou verificação de versão otimista; duas operações simultâneas sobre o mesmo `project_id` sobrescrevem uma à outra silenciosamente.

---

## 4. Como a Automação de 5 Shorts Roda Hoje

1. **Execução no Backend (`/api/automation/batch-daily`):**
   - É disparada via requisição HTTP POST.
   - Executa de forma **totalmente síncrona e sequencial** dentro da thread da requisição do FastAPI.
2. **O que Ela Realmente Faz:**
   - Faz a busca no radar, seleciona até $N$ notícias e gera o roteiro e o plano de cortes para cada uma.
   - **Ela NÃO renderiza os vídeos.** Cria apenas rascunhos com `status="draft (automacao_diaria)"`. O usuário ainda precisa clicar manualmente em "Renderizar" em cada um.
3. **Comportamento em Falha:**
   - O loop não possui tratamento de exceção por item (`try/except` isolado). Se uma notícia falhar durante a chamada de IA ou parsing, a rota inteira dispara uma exceção HTTP 500. Todos os itens restantes do lote são abortados imediatamente sem registro de histórico.
4. **Execução no CLI (`python cli.py batch --count 5`):**
   - Roda sequencialmente no terminal. Possui a flag `--render`, mas se um render falhar por erro de FFmpeg, o script é encerrado abruptamente.

---

## 5. O Que `qa_verifier.py` Realmente Verifica vs Documentação

| Item Verificado no Código | Status Real | Detalhe da Implementação |
| :--- | :---: | :--- |
| **Existência do arquivo de vídeo** | Sim | Valida se o MP4 existe e tem tamanho > 0 bytes. |
| **Resolução exata 1080x1920** | Sim | Confirma via FFprobe se a largura é 1080 e altura é 1920. |
| **Duração máxima (<= 58.5s)** | Sim | Rejeita se ultrapassar o teto estipulado de 58.5 segundos para Shorts. |
| **Codecs H.264 e YUV420p** | Sim | Verifica a presença de stream de vídeo compatível com YouTube/TikTok. |
| **Presença de Áudio Estéreo** | Sim | Checa se há stream de áudio ativo. |
| **Loudness Integrado (EBU R128)** | Sim | Mede LUFS (reprova se < -28.0 LUFS) e True Peak (alerta se > +0.2 dBFS). |
| **Slideshow Risk Score** | Sim | Avalia heurística de variedade de cortes, repetição de efeitos e tamanho do gancho. |
| **Prancha de Contato (Filmstrip)** | Sim | Gera imagem PNG com 10 quadros distribuídos, envelope de áudio RMS e marcações de corte. |
| **Safe Zones (Topo/Laterais/Rodapé)** | **Não** | Não valida se elementos visuais ou legendas invadem áreas cobertas pela interface do YouTube Shorts. |
| **Licenças e Procedência de Mídia** | **Não** | Não confere metadados de direitos autorais de trailers ou músicas. |
| **Verificação Factual / Rumor** | **Não** | Não valida o conteúdo textual ou termos sensíveis no roteiro. |
| **Detecção de Telas Pretas / Congelamentos**| **Não** | Não inspeciona se há trechos sem imagem ou frames pretos gerados por falha de crop. |

---

## 6. Módulos Duplicados e Responsabilidades Sobrepostas

1. **Geração de Roteiros:**
   - `app/gaming_scriptwriter.py` (ativo no GTA 6) vs `app/script_generator.py` (legado de música). `script_generator.py` ainda importa modelos antigos e pode ser isolado ou removido.
2. **Pesquisa e Radar:**
   - `app/gaming_radar.py` (ativo no GTA 6) vs `app/music_researcher.py` (legado de música com Discogs e Wikipedia de bandas).
3. **Mídia e Áudio:**
   - `app/gaming_media.py` manipula cortes de trailer e seleção de trilhas de GTA 6, enquanto `app/audio_processor.py` contém lógica pesada de detecção de refrão de música e chorus finder que não se aplicam ao fluxo atual de notícias rápidas.
4. **Renderizadores de Vídeo e Motion:**
   - `app/video_renderer.py` faz a composição final com FFmpeg. `app/html_renderer.py` e `app/motion_design.py` contêm lógica de renderização por Canvas e scripts GL que estão desconectados do fluxo padrão de Shorts de GTA 6.

---

## 7. Problemas Óbvios, Bugs e Riscos Encontrados

1. **Caminhos de Fontes Hardcoded:**
   - Em `app/qa_verifier.py` (linhas 21-25) e `app/thumbnail_engine.py`, há caminhos absolutos como `C:\Windows\Fonts\consolab.ttf` e `C:\Windows\Fonts\arial.ttf`. Se executado em ambiente sem essas fontes instaladas ou fora do Windows, o fallback cai na fonte padrão ilegível do PIL.
2. **Exceções Engolidas Silenciosamente:**
   - No `cli.py` (linhas 194-197) e em `app/web_server.py`, blocos `try ... except Exception: pass` engolem falhas no auto-blend de mídias, deixando o usuário sem saber por que uma cena ficou sem vídeo de fundo.
3. **Ken Burns com Duração Infinita Fictícia:**
   - Em `app/video_renderer.py`, o filtro de zoompan em imagens estáticas usa `d=99999`. O FFmpeg é forçado a calcular um zoom arbitrário que não bate com o tempo exato em frames da locução daquela cena.
4. **Dependência Crítica de Rede sem Fallback Local no TTS:**
   - O `edge-tts` depende de conexão externa ativa com o gateway da Microsoft. Uma oscilação de conexão no meio da renderização de 5 vídeos corrompe o áudio da cena sem fallback de TTS offline.
5. **Falta de Enquadramento Inteligente (Crop Cego):**
   - O comando `scale=...increase,crop=1080:1920` em vídeos 16:9 sempre corta o centro exato. Se o carro, personagem ou detalhe estiver nas bordas da gravação do trailer, ele é cortado da tela.
6. **Risco de Direitos Autorais / Monetização:**
   - As mídias locais em `inputs/gta6/` (trailers oficiais da Rockstar e faixas `.mp3`) não possuem qualquer metadado registrado de licença, uso comercial ou atribuição.

---

## 8. Conclusão e Recomendação para a Etapa 1

O sistema possui uma base funcional robusta para pesquisa de radar, corte de trailers, locução com TTS e renderização FFmpeg 1080x1920. No entanto, sua arquitetura atual é vulnerável em três frentes:
1. **Confiabilidade e Procedência:** Ausência de catálogo de licenças de mídias e falta de classificação editorial de notícias (rumor vs fato confirmado).
2. **Resiliência de Execução:** Ausência de fila de jobs com recuperação de falhas e persistência sem gravação atômica.
3. **Refinamento Audiovisual:** Ducking de áudio estático (sem sidechain) e enquadramentos verticais fixos.

---

## 9. Histórico de Execução das Etapas

- **Etapa 0 (Auditoria & Docs):** Relatório inicial consolidado e suíte de testes estabelecida.
- **Etapa 1 (Correções Rápidas C-01 a C-15):** DefusedXML, normalização de caminhos, locks de escrita, rate limiting e tolerância a falhas.
- **Etapa 2 (Confiabilidade Editorial e Gate de Direitos):** Catálogo de procedência de mídias (`media_catalog.json`), gate bloqueante de direitos autorais para projetos comerciais, e 4 classes de confiabilidade de pautas (`confirmado`, `reportado`, `rumor`, `nao_verificado`).
- **Etapa 3 (Motor de Roteiros e Duração Real):** 4 arquétipos narrativos virais (`noticia`, `curiosidade`, `teoria_rumor`, `polemica`), CTAs dinâmicos, e teto estrito de 58,0 s com ajuste de velocidade e corte de pausas sem truncar frases.
- **Etapa 4 (Vídeo, Áudio e Uso Transformativo):** Seleção semântica de trechos de trailer ($\le 6.0$s), sidechain ducking com `sidechaincompress`, 3 modos de enquadramento (`crop_center`, `crop_focus`, `blur_fill`) e Ken Burns exato.
- **Etapa 5 (Fila de Jobs, Safe Zones e QA Objetivo):**
  - **Fila Sequencial (Tarefa 4.2):** `JobQueueManager` em `app/job_queue.py` executando jobs 1 por vez para eliminar sobrecarga de CPU/disco, com 5 estágios (`pesquisa`, `roteiro`, `tts`, `render`, `qa`), isolamento de erros por job e retomada incremental sem reprocessar etapas concluídas.
  - **Critérios Objetivos de QA (Tarefa 4.3):** Checklist de 15 verificações em 4 categorias (`tecnica`, `narrativa`, `visual`, `editorial`), com status `aprovado`, `reprovado`, `nao_se_aplica`. Veredito "Pronto" para vídeos aprovados em técnica e narrativa, e "Pronto com Ressalvas (Requer Confirmação Editorial)" quando houver alertas ou pautas não verificadas.
  - **Safe Zones (Tarefa 4.4):** Limites verticais (`SAFE_ZONE_TOP_PX=250`, `SAFE_ZONE_BOTTOM_PX=320`, `SAFE_ZONE_LEFT_PX=60`, `SAFE_ZONE_RIGHT_PX=60`) configuráveis e respeitados em legendas ASS e overlays visuais.
  - **Legendas por Palavra (Tarefa 3.4):** Sincronização via `WordBoundary` do Edge-TTS com realce de termos-chave e margens adaptadas ao layout do YouTube Shorts.
  - **Ambiente & Ferramentas:** Verificação de FFmpeg e filtros na inicialização (`verify_ffmpeg_installation()`), dependências congeladas em `requirements-lock.txt` testadas no Python 3.14.7, e esclarecimento de arquitetura "local-first, serviços externos opcionais".
