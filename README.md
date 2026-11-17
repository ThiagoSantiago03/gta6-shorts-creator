# GTA 6 Shorts Studio — Fábrica de YouTube Shorts (1080×1920)

Estúdio local-first focado em **GTA 6 e Rockstar Games** para canais de **YouTube Shorts** (5 etapas no painel Web), com preview em tempo real e operação **sem chaves de API obrigatórias (serviços externos opcionais: Edge-TTS, Pollinations, Gemini)**.

> 📖 **Arquitetura Atual:** [ARCHITECTURE.md](ARCHITECTURE.md)  
> 🗺️ **Roadmap e Pendências:** [ROADMAP.md](ROADMAP.md)  
> 📝 **Histórico de Mudanças:** [CHANGELOG.md](CHANGELOG.md)  
> 🛡️ **Catálogo de Procedência e Licenças:** [inputs/media_catalog.json](inputs/media_catalog.json)

> ⚠️ **Aviso de Direitos Autorais:** O catálogo registra decisões e origem das mídias; **não confere autorização**. Fair use varia por país e não impede Content ID, reivindicações ou strikes. Para canais monetizados, consulte um profissional de direitos autorais.

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
- **Python 3.11, 3.12 ou 3.14** (64-bit — versões validadas no CI em Windows e Linux)
- **FFmpeg** instalado no sistema ou empacotado via `imageio-ffmpeg`

### 1. Clonar e Criar Ambiente Virtual

**No Windows:**
```powershell
git clone https://github.com/ThiagoSantiago03/gta6-shorts-creator.git
cd gta6-shorts-creator
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -c requirements-lock.txt
# Para rodar testes automatizados:
pip install -r requirements-dev.txt
```

**No Linux / macOS:**
```bash
git clone https://github.com/ThiagoSantiago03/gta6-shorts-creator.git
cd gta6-shorts-creator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -c requirements-lock.txt
# Para rodar testes automatizados:
pip install -r requirements-dev.txt
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

# 2. Criar roteiro e montagem a partir de um tópico (padrão seguro: --intent commercial)
python cli.py create --topic "Data de Lancamento de GTA 6" --archetype "auto" --intent commercial

# 3. Criar lote de 5 Shorts automaticamente a partir do radar
python cli.py batch --count 5 --intent commercial

# 4. Renderizar um projeto existente (opcional: sobrescrever --intent)
python cli.py render --project-id <id_do_projeto>

# 5. Listar todos os vídeos e status de renderização
python cli.py list
```

---

## 📄 Licença

Este projeto é disponibilizado sob a licença **MIT**. Consulte o arquivo [LICENSE](LICENSE) para mais detalhes.
