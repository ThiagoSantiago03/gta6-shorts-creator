# GTA 6 Shorts Studio — Fábrica de YouTube Shorts (1080×1920)

Estúdio automatizado focado em **GTA 6 e Rockstar Games** para canais de **YouTube Shorts**, projetado para produzir de **5 a 10 vídeos virais por dia**, com preview em tempo real e operação **sem API paga por padrão**.

> 📖 **Documentação Arquitetural Completa:** [DOCUMENTACAO_COMPLETA_GTA6_SHORTS.md](DOCUMENTACAO_COMPLETA_GTA6_SHORTS.md)  
> 🔍 **Relatório de Auditoria Técnica (Histórico):** [AUDITORIA.md](AUDITORIA.md)  
> 🛡️ **Catálogo de Procedência e Licenças:** [inputs/media_catalog.json](inputs/media_catalog.json)

---

## ⚡ Pilares do Estúdio GTA 6

1. **Fundo em Vídeo Dinâmico (Trailers Locais)**:
   - Utiliza acervo local em `inputs/gta6/` (trailers oficiais, teasers e gameplays em alta resolução).
   - O algoritmo descarta telas pretas, logotipos estáticos e prioriza momentos de ação urbana e perseguição.
2. **Overlays Estritamente Contextuais**:
   - Imagens só aparecem sobrepostas quando houver correspondência exata com fatos concretos falados na narração (ex: preço, consoles, mapa, mídia física).
   - Quando não há foto real relevante, o sistema ou gera uma ilustração conceitual via IA, ou mantém o **vídeo em movimento limpo** (`none_motion_only`).
3. **Padrão Visual & Identidade Vice City**:
   - Resolução vertical nativa **1080×1920 (9:16)** a 30fps.
   - Legendas estilizadas **Vice City** (amarelo/branco com contorno preto) posicionadas na **safe-zone** do YouTube Shorts.
   - Narração neural com voz **Antônio (`pt-BR-AntonioNeural`)** em ritmo viral (+14%), cronometrada para **$\le 58,0$ segundos** por critério editorial de retenção.
   - Trilha sonora de ação com equalização suave (`ducking` de voz).
4. **Sem API Paga por Padrão (Serviços Externos Utilizados)**:
   - **Google Trends / Google News RSS:** Varredura de matérias jornalísticas públicas.
   - **Microsoft Edge-TTS:** Vozes neurais em português brasileiro gratuitas via streaming.
   - **Pollinations.ai:** Geração de imagens conceituais de apoio.
   - **Google Gemini API (Opcional):** Suporte opcional à chave `GEMINI_API_KEY` para refino de roteiros.

---

## 📋 Pré-requisitos e Instalação

### Requisitos:
- **Python 3.10+** (recomendado 3.11 a 3.14, 64-bit)
- **FFmpeg / FFprobe** instalado no sistema ou acessível via `imageio-ffmpeg`

### 1. Clonar e Criar Ambiente Virtual

**No Windows:**
```powershell
git clone https://github.com/ThiagoSantiago03/gta6-shorts-creator.git
cd gta6-shorts-creator
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

**No Linux / macOS:**
```bash
git clone https://github.com/ThiagoSantiago03/gta6-shorts-creator.git
cd gta6-shorts-creator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 🚀 Como Iniciar o Painel Web

Com o ambiente ativado, execute:

```bash
python -m uvicorn app.web_server:app --host 127.0.0.1 --port 8765
```

No Windows, você também pode simplesmente dar duplo clique em `run_panel.bat`.

Acesse no navegador: **`http://127.0.0.1:8765`**

---

## 🛠️ Uso via Linha de Comando (CLI)

```bash
# 1. Consultar tendências no radar de GTA 6
python cli.py radar --query "GTA 6" --geo "BR"

# 2. Criar roteiro e montagem a partir de um tópico
python cli.py create --topic "Data de Lancamento de GTA 6" --archetype "auto"

# 3. Criar lote de 5 Shorts automaticamente a partir do radar
python cli.py batch --count 5

# 4. Renderizar um projeto existente
python cli.py render --project-id <id_do_projeto>

# 5. Listar todos os vídeos e status de renderização
python cli.py list
```

---

## 📄 Licença

Este projeto é disponibilizado sob a licença **MIT**. Consulte o arquivo [LICENSE](LICENSE) para mais detalhes.
