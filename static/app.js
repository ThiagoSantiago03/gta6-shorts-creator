// GTA 6 Shorts Studio — Painel 100% Autônomo e Otimizado para YouTube Shorts (<= 58s)
// Arquitetura limpa: Pauta & Radar -> Roteiro & Locução -> Estúdio Visual 9:16 -> Render & YouTube

let CONFIG = null;
let PROJECTS = [];
let CURRENT_PROJECT = null;
let CURRENT_STEP = 1;
let RADAR_TOPICS = [];
let POLL_TIMER = null;
let PROJECT_FILTER = "all"; // 'all', 'pending', 'posted'

// Estado do Estúdio Visual 9:16 (Step 3)
let LIVE_ACTIVE_SCENE_IDX = 1;
let LIVE_PREVIEW_MODE = "instant"; // "instant" ou "real"
let LIVE_REAL_PREVIEW_URLS = {};

// =========================================================================
// INICIALIZAÇÃO & CONFIG
// =========================================================================

document.addEventListener("DOMContentLoaded", async () => {
  setupNavigation();
  setupModals();
  setupRadarHandlers();
  setupProjectHandlers();
  setupStep2Handlers();
  setupStep3StudioHandlers();
  setupStep4RenderHandlers();
  
  await loadConfig();
  await loadProjects();
  
  // Se houver projetos, carrega o mais recente
  if (PROJECTS.length > 0) {
    loadProject(PROJECTS[0].id);
  } else {
    goToStep(1);
    fetchRadar();
  }
});

async function api(path, options = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...options });
  if (!res.ok) {
    let errDetail = `${res.status} ${res.statusText}`;
    try {
      const errJson = await res.json();
      if (errJson?.detail) errDetail = errJson.detail;
    } catch (_) {}
    throw new Error(errDetail);
  }
  const ctype = res.headers.get("content-type") || "";
  if (ctype.includes("application/json")) return res.json();
  return res.text();
}

function showToast(msg, type = "info") {
  let toast = document.getElementById("appToast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "appToast";
    toast.className = "app-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.className = `app-toast show ${type}`;
  setTimeout(() => toast.classList.remove("show"), 3500);
}

// =========================================================================
// NAVEGAÇÃO ENTRE ETAPAS (1 A 4)
// =========================================================================

function setupNavigation() {
  document.querySelectorAll("#wizardStepper .step-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      const step = parseInt(pill.getAttribute("data-step"), 10);
      if (step > 1 && !CURRENT_PROJECT) {
        showToast("Selecione ou crie um projeto primeiro.", "warn");
        return;
      }
      goToStep(step);
    });
  });

  document.querySelectorAll("[data-goto-step]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const step = parseInt(btn.getAttribute("data-goto-step"), 10);
      goToStep(step);
    });
  });

  document.querySelectorAll(".filter-tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".filter-tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      PROJECT_FILTER = btn.getAttribute("data-filter") || "all";
      renderProjectsSidebar();
    });
  });

  const newProjBtn = document.getElementById("newProjectNavBtn");
  if (newProjBtn) {
    newProjBtn.addEventListener("click", () => {
      CURRENT_PROJECT = null;
      document.getElementById("createProjectForm")?.reset();
      document.getElementById("step1EditingBanner")?.classList.add("hidden");
      document.getElementById("activeProjectBar")?.classList.add("hidden");
      goToStep(1);
    });
  }

  const startFreshBtn = document.getElementById("step1StartFreshBtn");
  if (startFreshBtn) {
    startFreshBtn.addEventListener("click", () => {
      CURRENT_PROJECT = null;
      document.getElementById("createProjectForm")?.reset();
      document.getElementById("step1EditingBanner")?.classList.add("hidden");
      document.getElementById("activeProjectBar")?.classList.add("hidden");
    });
  }

  const continueBtn = document.getElementById("step1ContinueExistingBtn");
  if (continueBtn) {
    continueBtn.addEventListener("click", () => goToStep(2));
  }
}

function goToStep(stepNum) {
  CURRENT_STEP = stepNum;
  document.querySelectorAll(".step-panel").forEach((panel) => {
    panel.classList.remove("active");
  });
  const target = document.getElementById(`step-${stepNum}`);
  if (target) target.classList.add("active");

  document.querySelectorAll("#wizardStepper .step-pill").forEach((pill) => {
    const pillStep = parseInt(pill.getAttribute("data-step"), 10);
    pill.classList.toggle("active", pillStep === stepNum);
    pill.classList.toggle("completed", pillStep < stepNum && Boolean(CURRENT_PROJECT));
  });

  if (stepNum === 1) renderStep1();
  if (stepNum === 2) renderStep2();
  if (stepNum === 3) renderStep3();
  if (stepNum === 4) renderStep4();
}

// =========================================================================
// CARREGAMENTO DE CONFIG E PROJETOS
// =========================================================================

async function loadConfig() {
  try {
    CONFIG = await api("/api/config");
    renderTrailerLibrary();
  } catch (err) {
    console.error("Erro ao carregar /api/config:", err);
  }
}

async function loadProjects() {
  try {
    PROJECTS = await api("/api/projects");
    renderProjectsSidebar();
  } catch (err) {
    console.error("Erro ao listar projetos:", err);
  }
}

function renderProjectsSidebar() {
  const listEl = document.getElementById("projectsList");
  if (!listEl) return;

  const allCount = PROJECTS.length;
  const postedCount = PROJECTS.filter((p) => Boolean(p.is_posted)).length;
  const pendingCount = allCount - postedCount;

  const countAllEl = document.getElementById("filterCountAll");
  const countPendingEl = document.getElementById("filterCountPending");
  const countPostedEl = document.getElementById("filterCountPosted");
  if (countAllEl) countAllEl.textContent = allCount;
  if (countPendingEl) countPendingEl.textContent = pendingCount;
  if (countPostedEl) countPostedEl.textContent = postedCount;

  listEl.innerHTML = "";

  if (PROJECTS.length === 0) {
    listEl.innerHTML = `<div class="empty-state">Nenhum vídeo criado ainda.<br>Use o radar ao lado para começar!</div>`;
    return;
  }

  let visibleProjects = PROJECTS;
  if (PROJECT_FILTER === "posted") {
    visibleProjects = PROJECTS.filter((p) => Boolean(p.is_posted));
  } else if (PROJECT_FILTER === "pending") {
    visibleProjects = PROJECTS.filter((p) => !Boolean(p.is_posted));
  }

  if (visibleProjects.length === 0) {
    const emptyMsg = PROJECT_FILTER === "posted"
      ? "Nenhum vídeo marcado como postado ainda.<br>Marque vídeos na lista ou na aba de entrega!"
      : "Nenhum vídeo pendente!<br>Todos os vídeos foram marcados como postados.";
    listEl.innerHTML = `<div class="empty-state" style="padding:20px 10px;">${emptyMsg}</div>`;
    return;
  }

  visibleProjects.forEach((proj) => {
    const div = document.createElement("div");
    div.className = `project-item ${CURRENT_PROJECT?.id === proj.id ? "active" : ""}`;
    const isRendered = Boolean(proj.outputs?.full_video || proj.outputs?.single_video || proj.outputs?.youtube_short);

    let badgeClass = "badge-draft";
    let badgeText = "Rascunho";
    if (proj.is_posted) {
      badgeClass = "badge-posted";
      badgeText = "✅ Postado";
    } else if (isRendered) {
      badgeClass = "badge-ready";
      badgeText = "🚀 Pronto";
    }

    const title = proj.title || proj.song_title || "Sem título";
    const intentLabels = {
      commercial: "Comercial",
      editorial: "Editorial",
      non_commercial: "Não comercial",
    };
    const intentLabel = intentLabels[proj.publish_intent || "commercial"] || "Comercial";

    div.innerHTML = `
      <div class="proj-title">${escapeHtml(title)}</div>
      <div class="proj-meta">
        <span class="proj-badge ${badgeClass}">${badgeText}</span>
        <small class="muted">${escapeHtml(intentLabel)} • ${proj.scenes?.length || 0} cenas • <=58s</small>
      </div>
      <div class="proj-item-actions">
        <button type="button" class="quick-toggle-posted-btn ${proj.is_posted ? "is-posted" : ""}" title="${proj.is_posted ? "Clique para desmarcar postagem" : "Clique para marcar como postado"}">
          ${proj.is_posted ? "✅ Postado" : "⏳ Marcar"}
        </button>
      </div>
    `;

    div.querySelector(".quick-toggle-posted-btn")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      await toggleProjectPosted(proj.id);
    });

    div.addEventListener("click", () => loadProject(proj.id));
    listEl.appendChild(div);
  });
}

async function refreshRightsPreview(projectId) {
  const banner = document.getElementById("rightsPreviewBanner");
  if (!banner || !projectId) return;
  try {
    const preview = await api(`/api/projects/${projectId}/rights-preview`);
    if (preview.warning) {
      banner.textContent = `⚠️ [${preview.publish_intent}] ${preview.warning}`;
      banner.classList.remove("hidden");
    } else {
      banner.textContent = "";
      banner.classList.add("hidden");
    }
  } catch (err) {
    banner.classList.add("hidden");
  }
}

async function loadProject(id) {
  try {
    CURRENT_PROJECT = await api(`/api/projects/${id}`);
    updateProjectHeader();
    renderProjectsSidebar();
    refreshRightsPreview(id);

    // Se estiver no step 1, exibe o aviso de que está editando
    const editingBanner = document.getElementById("step1EditingBanner");
    const activeName = document.getElementById("step1ActiveName");
    if (editingBanner && activeName) {
      editingBanner.classList.remove("hidden");
      activeName.textContent = CURRENT_PROJECT.title || CURRENT_PROJECT.song_title;
    }

    // Se o vídeo já foi renderizado, vai para a aba de entrega (Step 4), senão Step 2 ou 3
    if (CURRENT_STEP === 1) {
      if (CURRENT_PROJECT.outputs?.full_video || CURRENT_PROJECT.outputs?.single_video) {
        goToStep(4);
      } else {
        goToStep(2);
      }
    } else {
      goToStep(CURRENT_STEP);
    }
  } catch (err) {
    showToast(`Erro ao carregar projeto: ${err.message}`, "error");
  }
}

function updateProjectHeader() {
  const bar = document.getElementById("activeProjectBar");
  if (!bar) return;
  if (!CURRENT_PROJECT) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  const title = CURRENT_PROJECT.title || CURRENT_PROJECT.song_title || "GTA 6 Short";
  const titleEl = document.getElementById("projHeaderTitle");
  if (titleEl) titleEl.textContent = title;

  const badge = document.getElementById("projStatusBadge");
  if (badge) {
    const isRendered = Boolean(CURRENT_PROJECT.outputs?.full_video || CURRENT_PROJECT.outputs?.single_video || CURRENT_PROJECT.outputs?.youtube_short);
    if (CURRENT_PROJECT.is_posted) {
      badge.textContent = "✅ POSTADO NO CANAL";
      badge.className = "tag-badge badge-posted";
    } else if (isRendered) {
      badge.textContent = "🚀 PRONTO P/ POSTAR (1080x1920)";
      badge.className = "tag-badge badge-success";
    } else {
      badge.textContent = "EM PRODUÇÃO";
      badge.className = "tag-badge";
    }
  }

  const topbarPostedIcon = document.getElementById("topbarPostedIcon");
  const topbarPostedText = document.getElementById("topbarPostedText");
  const topbarToggleBtn = document.getElementById("topbarTogglePostedBtn");
  if (topbarToggleBtn && topbarPostedIcon && topbarPostedText) {
    if (CURRENT_PROJECT.is_posted) {
      topbarPostedIcon.textContent = "✅";
      topbarPostedText.textContent = "Postado";
      topbarToggleBtn.classList.add("is-posted");
    } else {
      topbarPostedIcon.textContent = "⏳";
      topbarPostedText.textContent = "Marcar Postado";
      topbarToggleBtn.classList.remove("is-posted");
    }
  }
}

// =========================================================================
// ETAPA 1: PAUTA, RADAR & ACERVO DE TRAILERS
// =========================================================================


async function toggleProjectPosted(projectId, explicitState = null) {
  try {
    const payload = {};
    if (explicitState !== null) {
      payload.is_posted = explicitState;
    }
    const updated = await api(`/api/projects/${projectId}/toggle-posted`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (CURRENT_PROJECT && CURRENT_PROJECT.id === projectId) {
      CURRENT_PROJECT = updated;
      updateProjectHeader();
      renderPublishStatusCard();
    }
    await loadProjects();
    const msg = updated.is_posted ? "Vídeo marcado como POSTADO!" : "Vídeo desmarcado (marcado como pendente).";
    showToast(msg, "success");
  } catch (err) {
    showToast(`Erro ao atualizar status: ${err.message}`, "error");
  }
}

function isProjectPublishReady(proj) {
  if (!proj) return { qaOk: false, humanOk: false, ready: false, verdict: "QA não executado" };
  const qa = proj.qa_report || {};
  const qaOk = Boolean(qa.qa_passed || qa.is_ready_for_publication);
  const humanOk = Boolean(proj.human_approved);
  const verdict = qa.verdict || (qaOk ? "Pronto" : "QA pendente/reprovado");
  return { qaOk, humanOk, ready: qaOk && humanOk, verdict };
}

async function toggleHumanApproval(projectId) {
  if (!CURRENT_PROJECT || CURRENT_PROJECT.id !== projectId) return;
  const nextApproved = !Boolean(CURRENT_PROJECT.human_approved);
  try {
    const updated = await api(`/api/projects/${projectId}/human-approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved: nextApproved }),
    });
    CURRENT_PROJECT = updated;
    renderPublishStatusCard();
    await loadProjects();
    showToast(
      updated.human_approved
        ? "Revisão humana aprovada! Pacote de publicação liberado."
        : "Aprovação humana revogada.",
      updated.human_approved ? "success" : "info"
    );
  } catch (err) {
    showToast(`Erro ao atualizar aprovação humana: ${err.message}`, "error");
  }
}

function renderPublishStatusCard() {
  const card = document.getElementById("publishStatusCard");
  const pill = document.getElementById("publishStatusPill");
  const subtitle = document.getElementById("publishStatusSubtitle");
  const btnIcon = document.getElementById("togglePostedBtnIcon");
  const btnText = document.getElementById("togglePostedBtnText");
  const panel = document.getElementById("publishDetailsPanel");
  const approvalPill = document.getElementById("humanApprovalPill");
  const approvalVerdict = document.getElementById("humanApprovalVerdict");
  const approveBtn = document.getElementById("humanApproveBtn");
  const copyYtBtn = document.getElementById("copyYoutubePackBtn");
  const copyTtBtn = document.getElementById("copyTiktokCaptionBtn");
  const togglePostedBtn = document.getElementById("togglePostedBtn");

  if (!card || !CURRENT_PROJECT) return;

  const gate = isProjectPublishReady(CURRENT_PROJECT);
  if (approvalPill) {
    approvalPill.className = gate.humanOk ? "mini-pill posted" : "mini-pill pending";
    approvalPill.textContent = gate.humanOk ? "✅ Revisão Humana Aprovada" : "🔒 Aguardando Revisão Humana";
  }
  if (approvalVerdict) {
    approvalVerdict.textContent = `Veredito QA: ${gate.verdict}${!gate.ready ? " • Exige QA aprovado + revisão humana para publicar." : " • Liberado para publicação."}`;
  }
  if (approveBtn) {
    approveBtn.textContent = gate.humanOk ? "↩️ Revogar Aprovação" : "☑️ Aprovar para Publicação";
  }
  if (copyYtBtn) copyYtBtn.disabled = !gate.ready;
  if (copyTtBtn) copyTtBtn.disabled = !gate.ready;
  if (togglePostedBtn && !CURRENT_PROJECT.is_posted) {
    togglePostedBtn.disabled = !gate.ready;
  } else if (togglePostedBtn) {
    togglePostedBtn.disabled = false;
  }

  if (CURRENT_PROJECT.is_posted) {
    card.classList.add("is-posted");
    if (pill) {
      pill.className = "mini-pill posted";
      pill.textContent = "✅ Postado no Canal";
    }
    if (subtitle) subtitle.textContent = "Vídeo publicado com sucesso! Detalhes registrados abaixo.";
    if (btnIcon) btnIcon.textContent = "↩️";
    if (btnText) btnText.textContent = "Desmarcar (Voltar p/ Pendente)";
    if (panel) panel.classList.remove("hidden");

    const platSelect = document.getElementById("editPostedPlatform");
    if (platSelect) platSelect.value = CURRENT_PROJECT.posted_platform || "youtube";

    const atDisplay = document.getElementById("displayPostedAt");
    if (atDisplay) {
      atDisplay.value = CURRENT_PROJECT.posted_at
        ? new Date(CURRENT_PROJECT.posted_at).toLocaleString("pt-BR")
        : new Date().toLocaleString("pt-BR");
    }

    const urlInput = document.getElementById("editPostedUrl");
    if (urlInput) urlInput.value = CURRENT_PROJECT.posted_url || "";
  } else {
    card.classList.remove("is-posted");
    if (pill) {
      pill.className = "mini-pill pending";
      pill.textContent = "⏳ Pendente de Postagem";
    }
    if (subtitle) subtitle.textContent = "Marque aqui quando o vídeo tiver sido postado no YouTube Shorts.";
    if (btnIcon) btnIcon.textContent = "✅";
    if (btnText) btnText.textContent = "Marcar como Postado";
    if (panel) panel.classList.add("hidden");
  }
}

function setupRadarHandlers() {
  const fetchBtn = document.getElementById("fetchRadarBtn");
  if (fetchBtn) {
    fetchBtn.addEventListener("click", () => fetchRadar());
  }

  const batchBtn = document.getElementById("batchDailyAutomationBtn");
  if (batchBtn) {
    batchBtn.addEventListener("click", handleBatchDailyAutomation);
  }

  const uploadInput = document.getElementById("uploadGta6ClipInput");
  if (uploadInput) {
    uploadInput.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      const formData = new FormData();
      formData.append("file", file);
      showToast("Enviando e indexando trailer MP4...", "info");
      try {
        await api("/api/gaming/upload-clip", { method: "POST", body: formData });
        showToast("Trailer adicionado e indexado com sucesso!", "success");
        await loadConfig();
      } catch (err) {
        showToast(`Erro ao enviar trailer: ${err.message}`, "error");
      }
    });
  }
}

async function fetchRadar() {
  const queryInput = document.getElementById("radarQueryInput");
  const geoSelect = document.getElementById("radarGeoSelect");
  const query = queryInput ? queryInput.value.trim() : "GTA 6";
  const geo = geoSelect ? geoSelect.value : "BR";

  const listEl = document.getElementById("radarTopicsList");
  if (listEl) {
    listEl.innerHTML = `<div class="muted" style="grid-column:1/-1; padding:20px 0; text-align:center;">🔍 Varrendo Google Trends e portais de games do Brasil em busca de notícias quentes de GTA 6...</div>`;
  }

  try {
    const data = await api(`/api/gaming/radar?query=${encodeURIComponent(query)}&geo=${geo}`);
    RADAR_TOPICS = data.items || [];
    renderRadarGrid();
  } catch (err) {
    if (listEl) {
      listEl.innerHTML = `<div class="error-text" style="grid-column:1/-1;">Falha ao buscar notícias do radar: ${err.message}</div>`;
    }
  }
}

function renderRadarGrid() {
  const listEl = document.getElementById("radarTopicsList");
  if (!listEl) return;
  listEl.innerHTML = "";

  if (RADAR_TOPICS.length === 0) {
    listEl.innerHTML = `<div class="muted" style="grid-column:1/-1;">Nenhuma pauta encontrada no momento para essa busca.</div>`;
    return;
  }

  RADAR_TOPICS.forEach((item, idx) => {
    const card = document.createElement("div");
    card.className = "radar-card";
    const stConf = item.status_confiabilidade || "nao_verificado";
    const isUnverified = stConf === "nao_verificado";
    card.innerHTML = `
      <div class="radar-card-top">
        <span class="radar-score">🔥 ${item.trend_score || 85} pts</span>
        <span class="radar-source">${escapeHtml(item.source || "Notícia")} • ${escapeHtml(stConf)}</span>
      </div>
      <h4 class="radar-title">${escapeHtml(item.title)}</h4>
      <p class="radar-summary">${escapeHtml(item.summary || "")}</p>
      ${item.suggested_angle ? `<div class="radar-angle">💡 Ângulo: ${escapeHtml(item.suggested_angle)}</div>` : ""}
      ${isUnverified ? `<label style="display:flex;align-items:center;gap:6px;font-size:12px;margin:6px 0;color:#fde68a;"><input type="checkbox" class="manual-approve-chk" data-idx="${idx}" /> Aprovar manualmente para a fila</label>` : ""}
      <div class="radar-actions" style="display:flex;gap:6px;flex-wrap:wrap;">
        <button type="button" class="btn btn-primary btn-sm use-pauta-btn" data-idx="${idx}">
          ⚡ Usar esta Pauta &amp; Criar Short
        </button>
        <button type="button" class="btn btn-secondary btn-sm enqueue-pauta-btn" data-idx="${idx}">
          📥 Enviar p/ Fila
        </button>
      </div>
    `;
    listEl.appendChild(card);
  });

  listEl.querySelectorAll(".enqueue-pauta-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const idx = parseInt(btn.getAttribute("data-idx"), 10);
      const item = RADAR_TOPICS[idx];
      if (!item) return;
      const chk = listEl.querySelector(`.manual-approve-chk[data-idx="${idx}"]`);
      const manualApproved = chk ? Boolean(chk.checked) : false;
      const intentSelect = document.getElementById("publishIntentSelect");
      const publishIntent = intentSelect?.value || "commercial";
      try {
        await api("/api/queue/jobs/batch", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            publish_intent: publishIntent,
            topics: [{
              title: item.title,
              suggested_angle: item.suggested_angle || "",
              url: item.url || "",
              source: item.source || "",
              published_at: item.published_at || "",
              confidence: item.confidence || "Notícia / Imprensa",
              status_confiabilidade: item.status_confiabilidade || "nao_verificado",
              manual_approved: manualApproved,
              publish_intent: publishIntent,
            }],
          }),
        });
        showToast("Pauta enviada para a fila de produção!", "success");
      } catch (err) {
        showToast(`Erro ao enfileirar pauta: ${err.message}`, "error");
      }
    });
  });

  listEl.querySelectorAll(".use-pauta-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = parseInt(btn.getAttribute("data-idx"), 10);
      const item = RADAR_TOPICS[idx];
      if (!item) return;

      const titleInput = document.getElementById("songTitleInput");
      const urlInput = document.getElementById("gamingSourceUrlInput");
      const angleInput = document.getElementById("topicAngleInput");

      if (titleInput) titleInput.value = item.title;
      if (urlInput) urlInput.value = item.url || "";
      if (angleInput) angleInput.value = item.suggested_angle || "";

      submitCreateProject();
    });
  });
}

function renderTrailerLibrary() {
  const strip = document.getElementById("gta6LibrarySummary");
  if (!strip) return;
  strip.innerHTML = "";
  const clips = CONFIG?.gta6_clips || [];
  if (clips.length === 0) {
    strip.innerHTML = `<small class="muted">Nenhum vídeo em inputs/gta6/. Envie um MP4 acima!</small>`;
    return;
  }

  clips.slice(0, 8).forEach((clip) => {
    const thumb = document.createElement("div");
    thumb.className = "clip-chip";
    const filename = clip.filename || clip;
    thumb.innerHTML = `🎬 <span>${escapeHtml(filename)}</span>`;
    strip.appendChild(thumb);
  });
}

function setupProjectHandlers() {
  const form = document.getElementById("createProjectForm");
  if (form) {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      submitCreateProject();
    });
  }

  const topbarPostedBtn = document.getElementById("topbarTogglePostedBtn");
  if (topbarPostedBtn) {
    topbarPostedBtn.addEventListener("click", async () => {
      if (!CURRENT_PROJECT) return;
      await toggleProjectPosted(CURRENT_PROJECT.id);
    });
  }

  const saveBtn = document.getElementById("saveProjectBtn");
  if (saveBtn) {
    saveBtn.addEventListener("click", saveCurrentProject);
  }

  const delBtn = document.getElementById("deleteProjectBtn");
  if (delBtn) {
    delBtn.addEventListener("click", async () => {
      if (!CURRENT_PROJECT) return;
      if (!confirm(`Deseja realmente excluir o projeto "${CURRENT_PROJECT.title || CURRENT_PROJECT.song_title}"?`)) return;
      try {
        await api(`/api/projects/${CURRENT_PROJECT.id}`, { method: "DELETE" });
        showToast("Projeto excluído com sucesso!", "success");
        CURRENT_PROJECT = null;
        await loadProjects();
        goToStep(1);
      } catch (err) {
        showToast(`Erro ao excluir: ${err.message}`, "error");
      }
    });
  }

  const viewPacked = document.getElementById("viewPackedBtn");
  if (viewPacked) {
    viewPacked.addEventListener("click", () => {
      if (!CURRENT_PROJECT) return;
      const modal = document.getElementById("packedModal");
      const textarea = document.getElementById("packedTextarea");
      if (modal && textarea) {
        textarea.value = JSON.stringify(CURRENT_PROJECT, null, 2);
        modal.classList.remove("hidden");
      }
    });
  }
}

async function submitCreateProject() {
  const titleInput = document.getElementById("songTitleInput");
  const gameInput = document.getElementById("artistInput");
  const archetypeSelect = document.getElementById("gamingViralArchetypeSelect");
  const sourceUrlInput = document.getElementById("gamingSourceUrlInput");
  const angleInput = document.getElementById("topicAngleInput");
  const intentSelect = document.getElementById("publishIntentSelect");

  const title = titleInput?.value.trim();
  if (!title) {
    showToast("Informe o título ou manchete da notícia.", "warn");
    return;
  }

  const payload = {
    title: title,
    game: gameInput?.value.trim() || "GTA 6",
    topic_angle: angleInput?.value.trim() || "",
    topic_source_url: sourceUrlInput?.value.trim() || "",
    viral_archetype: archetypeSelect?.value || "auto",
    publish_intent: intentSelect?.value || "commercial",
    niche: "gaming_news",
    style_preset: "vice_city_neon",
    subtitle_style: "vice_city",
    voice_name: "pt-BR-AntonioNeural",
    speech_rate: "+14%",
    max_duration_sec: 58.0,
    accent_color: "#00F0FF",
  };

  const createBtn = document.getElementById("createProjectBtn");
  if (createBtn) {
    createBtn.disabled = true;
    createBtn.textContent = "⚡ Escrevendo Roteiro & Montando Cenas...";
  }

  try {
    const proj = await api("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    CURRENT_PROJECT = proj;
    showToast("Roteiro e cortes de trailer criados com sucesso!", "success");
    await loadProjects();
    refreshRightsPreview(proj.id);
    goToStep(2);
  } catch (err) {
    showToast(`Erro ao criar projeto: ${err.message}`, "error");
  } finally {
    if (createBtn) {
      createBtn.disabled = false;
      createBtn.textContent = "🚀 Criar Roteiro do Short (<= 58s) →";
    }
  }
}

async function handleBatchDailyAutomation() {
  if (!confirm("Deseja disparar a automação diária autônoma para criar 5 YouTube Shorts a partir das notícias mais quentes de hoje?")) {
    return;
  }
  const intentSelect = document.getElementById("publishIntentSelect");
  const publishIntent = intentSelect?.value || "commercial";
  showToast("Disparando automação autônoma de 5 Shorts...", "info");
  try {
    const res = await api("/api/automation/batch-daily", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count: 5, query: "GTA 6", geo: "BR", publish_intent: publishIntent }),
    });
    showToast(`Automação concluída! ${res.count} vídeos gerados e roteirizados na fila.`, "success");
    await loadProjects();
    if (res.projects?.length > 0) {
      loadProject(res.projects[0].id);
    }
  } catch (err) {
    showToast(`Erro na automação diária: ${err.message}`, "error");
  }
}

function renderStep1() {
  if (!CURRENT_PROJECT) {
    document.getElementById("step1EditingBanner")?.classList.add("hidden");
  }
}

// =========================================================================
// ETAPA 2: ROTEIRO VIRAL & LOCUÇÃO (58s CRAVADOS)
// =========================================================================

function setupStep2Handlers() {
  const saveBtn = document.getElementById("step2SaveBtn");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      await saveStep2Changes();
      showToast("Roteiro salvo!", "success");
    });
  }

  const nextBtn = document.getElementById("step2NextBtn");
  if (nextBtn) {
    nextBtn.addEventListener("click", async () => {
      await saveStep2Changes();
      goToStep(3);
    });
  }

  const regenBtn = document.getElementById("regenerateGamingScriptBtn");
  if (regenBtn) {
    regenBtn.addEventListener("click", async () => {
      if (!CURRENT_PROJECT) return;
      const archetype = document.getElementById("editViralArchetype")?.value || "auto";
      regenBtn.disabled = true;
      regenBtn.textContent = "Reescrevendo...";
      try {
        const res = await api(`/api/projects/${CURRENT_PROJECT.id}/regenerate-script`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ viral_archetype: archetype, speech_rate: "+14%" }),
        });
        CURRENT_PROJECT = res.project || res;
        showToast("Roteiro reescrito com sucesso!", "success");
        renderStep2();
      } catch (err) {
        showToast(`Erro ao reescrever roteiro: ${err.message}`, "error");
      } finally {
        regenBtn.disabled = false;
        regenBtn.textContent = "🔄 Reescrever Roteiro";
      }
    });
  }
}

function renderStep2() {
  if (!CURRENT_PROJECT) return;
  const hookInput = document.getElementById("editHookTitle");
  if (hookInput) hookInput.value = CURRENT_PROJECT.hook_title || CURRENT_PROJECT.title || "";

  const voiceSelect = document.getElementById("editVoiceName");
  if (voiceSelect) voiceSelect.value = CURRENT_PROJECT.voice_name || "pt-BR-AntonioNeural";

  const rateSelect = document.getElementById("editSpeechRate");
  if (rateSelect) rateSelect.value = CURRENT_PROJECT.speech_rate || "+14%";

  const maxDurInput = document.getElementById("editMaxDurationSec");
  if (maxDurInput) maxDurInput.value = CURRENT_PROJECT.max_duration_sec || 58;

  const archSelect = document.getElementById("editViralArchetype");
  if (archSelect) archSelect.value = CURRENT_PROJECT.viral_archetype || "auto";

  renderStep2ScenesList();
  updateStep2DurationMeter();
}

function renderStep2ScenesList() {
  const container = document.getElementById("step2ScenesList");
  if (!container || !CURRENT_PROJECT) return;
  container.innerHTML = "";

  (CURRENT_PROJECT.scenes || []).forEach((sc, idx) => {
    const card = document.createElement("div");
    card.className = "scene-script-card";

    let label = `Cena ${sc.index || idx + 1}`;
    if (idx === 0) label += " • 🎯 GANCHO VIRAL";
    else if (idx === CURRENT_PROJECT.scenes.length - 1) label += " • 📣 CTA FINAL";

    card.innerHTML = `
      <div class="sc-head">
        <strong class="sc-title">${label}</strong>
        <span class="mini-pill sc-words-badge">0 palavras</span>
      </div>
      <textarea class="sc-narr" rows="3" data-idx="${idx}">${escapeHtml(sc.narration_text || "")}</textarea>
    `;
    container.appendChild(card);
  });

  container.querySelectorAll(".sc-narr").forEach((ta) => {
    ta.addEventListener("input", () => updateStep2DurationMeter());
  });
}

function updateStep2DurationMeter() {
  const meter = document.getElementById("gamingDurationMeter");
  if (!meter) return;

  const textareas = document.querySelectorAll("#step2ScenesList .sc-narr");
  let totalWords = 0;
  textareas.forEach((ta) => {
    const words = (ta.value || "").trim().split(/\s+/).filter(Boolean).length;
    const badge = ta.closest(".scene-script-card")?.querySelector(".sc-words-badge");
    if (badge) badge.textContent = `${words} palavras`;
    totalWords += words;
  });

  // Cálculo baseado no ritmo viral +14% (~2.85 palavras/segundo)
  const estSeconds = Math.round((totalWords / 2.85) * 10) / 10;
  meter.textContent = `~${estSeconds}s / Máx 58s (${totalWords} palavras)`;

  if (estSeconds > 58) {
    meter.className = "mini-pill error";
    meter.title = "Atenção: o roteiro pode ultrapassar 58s! Encurte algumas frases.";
  } else {
    meter.className = "mini-pill ok";
    meter.title = "Duração ideal dentro do limite do YouTube Shorts!";
  }
}

async function saveStep2Changes() {
  if (!CURRENT_PROJECT) return;
  CURRENT_PROJECT.hook_title = document.getElementById("editHookTitle")?.value || "";
  CURRENT_PROJECT.voice_name = document.getElementById("editVoiceName")?.value || "pt-BR-AntonioNeural";
  CURRENT_PROJECT.speech_rate = document.getElementById("editSpeechRate")?.value || "+14%";
  CURRENT_PROJECT.max_duration_sec = parseFloat(document.getElementById("editMaxDurationSec")?.value || 58);
  CURRENT_PROJECT.viral_archetype = document.getElementById("editViralArchetype")?.value || "auto";

  const textareas = document.querySelectorAll("#step2ScenesList .sc-narr");
  textareas.forEach((ta) => {
    const idx = parseInt(ta.getAttribute("data-idx"), 10);
    if (CURRENT_PROJECT.scenes[idx]) {
      CURRENT_PROJECT.scenes[idx].narration_text = ta.value.trim();
    }
  });

  await api(`/api/projects/${CURRENT_PROJECT.id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(CURRENT_PROJECT),
  });
}

// =========================================================================
// ETAPA 3: ESTÚDIO VISUAL 9:16 (MONTAGEM & EDIÇÃO MANUAL POR CENAS)
// =========================================================================

function setupStep3StudioHandlers() {
  const autoBlendBtn = document.getElementById("autoBlendMediaBtn");
  if (autoBlendBtn) {
    autoBlendBtn.addEventListener("click", async () => {
      if (!CURRENT_PROJECT) return;
      autoBlendBtn.disabled = true;
      autoBlendBtn.textContent = "🎬 Re-montando com IA...";
      try {
        const res = await api(`/api/projects/${CURRENT_PROJECT.id}/auto-blend-media`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force_reassign: true }),
        });
        CURRENT_PROJECT = res.project || CURRENT_PROJECT;
        showToast("Vídeos e overlays sincronizados com IA!", "success");
        renderStep3();
      } catch (err) {
        showToast(`Erro ao re-montar mídias: ${err.message}`, "error");
      } finally {
        autoBlendBtn.disabled = false;
        autoBlendBtn.textContent = "🎬 Re-montar Vídeo com IA";
      }
    });
  }

  // Navegação entre Cenas
  const prevBtn = document.getElementById("livePrevSceneBtn");
  if (prevBtn) {
    prevBtn.addEventListener("click", () => {
      if (!CURRENT_PROJECT?.scenes?.length) return;
      if (LIVE_ACTIVE_SCENE_IDX > 1) {
        LIVE_ACTIVE_SCENE_IDX--;
        renderStep3();
      }
    });
  }

  const nextSceneBtn = document.getElementById("liveNextSceneBtn");
  if (nextSceneBtn) {
    nextSceneBtn.addEventListener("click", () => {
      if (!CURRENT_PROJECT?.scenes?.length) return;
      if (LIVE_ACTIVE_SCENE_IDX < CURRENT_PROJECT.scenes.length) {
        LIVE_ACTIVE_SCENE_IDX++;
        renderStep3();
      }
    });
  }

  // Salvar alterações manuais da cena ativa
  const saveSceneBtn = document.getElementById("liveSaveSceneBtn");
  if (saveSceneBtn) {
    saveSceneBtn.addEventListener("click", saveCurrentActiveScene);
  }

  // Digitação da narração com reflexo imediato na legenda do celular
  const narrInput = document.getElementById("liveActiveNarrationInput");
  if (narrInput) {
    narrInput.addEventListener("input", () => {
      const sc = getActiveScene();
      if (!sc) return;
      sc.narration_text = narrInput.value;
      updateSceneDurationBadge(sc);
      updateStudioInstantView();
    });
  }

  // Troca de trailer e segundo inicial com atualização em tempo real no vídeo
  const trailerSelect = document.getElementById("liveActiveTrailerSelect");
  if (trailerSelect) {
    trailerSelect.addEventListener("change", () => {
      const sc = getActiveScene();
      if (!sc) return;
      const selectedVal = trailerSelect.value;
      if (selectedVal) {
        sc.video_path = selectedVal;
        sc.asset_type = "video";
      }
      updateStudioInstantView();
    });
  }

  const videoStartInput = document.getElementById("liveActiveVideoStartInput");
  if (videoStartInput) {
    videoStartInput.addEventListener("input", () => {
      const sc = getActiveScene();
      if (!sc) return;
      sc.video_start_sec = parseFloat(videoStartInput.value) || 0;
      const bgVideo = document.getElementById("liveBgVideo");
      if (bgVideo && !bgVideo.classList.contains("hidden")) {
        bgVideo.currentTime = sc.video_start_sec;
      }
    });
  }

  const framingSelect = document.getElementById("liveActiveFramingModeSelect");
  const focusField = document.getElementById("liveActiveFocusXField");
  const focusInput = document.getElementById("liveActiveFocusXInput");
  const focusValEl = document.getElementById("liveActiveFocusXVal");
  if (framingSelect) {
    framingSelect.addEventListener("change", () => {
      const sc = getActiveScene();
      if (!sc) return;
      sc.framing_mode = framingSelect.value;
      if (focusField) focusField.classList.toggle("hidden", sc.framing_mode !== "crop_focus");
      updateStudioInstantView();
    });
  }
  if (focusInput) {
    focusInput.addEventListener("input", () => {
      const sc = getActiveScene();
      if (!sc) return;
      sc.focus_x = parseFloat(focusInput.value) || 0.5;
      if (focusValEl) focusValEl.textContent = sc.focus_x.toFixed(2);
      updateStudioInstantView();
    });
  }

  // Upload de vídeo personalizado para a cena ativa
  const vidUpload = document.getElementById("liveActiveVideoUploadInput");
  if (vidUpload) {
    vidUpload.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file || !CURRENT_PROJECT) return;
      const formData = new FormData();
      formData.append("file", file);
      formData.append("video_start_sec", "0");
      showToast("Enviando vídeo personalizado para a cena...", "info");
      try {
        const res = await api(`/api/projects/${CURRENT_PROJECT.id}/upload-scene-video/${LIVE_ACTIVE_SCENE_IDX}`, {
          method: "POST",
          body: formData,
        });
        CURRENT_PROJECT = res.project || CURRENT_PROJECT;
        showToast("Vídeo da cena atualizado!", "success");
        renderStep3();
      } catch (err) {
        showToast(`Erro ao enviar vídeo: ${err.message}`, "error");
      }
    });
  }

  // Alternância de modo de Overlay (Vídeo Limpo / Foto da Notícia / IA Flux)
  const modeNoneBtn = document.getElementById("ovModeNoneBtn");
  const modePhotoBtn = document.getElementById("ovModePhotoBtn");
  const modeAiBtn = document.getElementById("ovModeAiBtn");

  if (modeNoneBtn) {
    modeNoneBtn.addEventListener("click", () => setSceneOverlayMode("none_motion_only"));
  }
  if (modePhotoBtn) {
    modePhotoBtn.addEventListener("click", () => setSceneOverlayMode("matched_photo"));
  }
  if (modeAiBtn) {
    modeAiBtn.addEventListener("click", () => setSceneOverlayMode("ai_generated"));
  }

  // Upload de foto de overlay manual
  const ovUpload = document.getElementById("liveActiveOverlayUploadInput");
  if (ovUpload) {
    ovUpload.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      if (!file || !CURRENT_PROJECT) return;
      const formData = new FormData();
      formData.append("file", file);
      showToast("Enviando imagem de overlay...", "info");
      try {
        const res = await api(`/api/projects/${CURRENT_PROJECT.id}/upload-scene-overlay/${LIVE_ACTIVE_SCENE_IDX}`, {
          method: "POST",
          body: formData,
        });
        CURRENT_PROJECT = res.project || CURRENT_PROJECT;
        showToast("Overlay aplicado com sucesso!", "success");
        renderStep3();
      } catch (err) {
        showToast(`Erro ao enviar overlay: ${err.message}`, "error");
      }
    });
  }

  // Gerar imagem com IA sob demanda
  const genAiBtn = document.getElementById("liveActiveGenerateAiBtn");
  if (genAiBtn) {
    genAiBtn.addEventListener("click", async () => {
      if (!CURRENT_PROJECT) return;
      const customPrompt = document.getElementById("liveActiveAiPromptInput")?.value.trim();
      genAiBtn.disabled = true;
      genAiBtn.textContent = "Gerando Imagem...";
      showToast("Gerando ilustração IA no modelo Flux (Pollinations, sem chave de API)...", "info");
      try {
        const res = await api(`/api/projects/${CURRENT_PROJECT.id}/scenes/${LIVE_ACTIVE_SCENE_IDX}/generate-ai-overlay`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ custom_prompt: customPrompt }),
        });
        CURRENT_PROJECT = res.project || CURRENT_PROJECT;
        showToast("Ilustração IA gerada e aplicada!", "success");
        renderStep3();
      } catch (err) {
        showToast(`Erro ao gerar imagem IA: ${err.message}`, "error");
      } finally {
        genAiBtn.disabled = false;
        genAiBtn.textContent = "🎨 Gerar com IA";
      }
    });
  }

  // Texto da tarja do overlay
  const labelInput = document.getElementById("liveActiveOverlayLabelInput");
  if (labelInput) {
    labelInput.addEventListener("input", () => {
      const sc = getActiveScene();
      if (!sc) return;
      sc.overlay_label = labelInput.value.toUpperCase();
      const labelEl = document.querySelector("#liveOverlayLayer .live-ov-label");
      if (labelEl) labelEl.textContent = sc.overlay_label;
    });
  }

  // Render de prévia curta da cena via FFmpeg
  const quickRenderBtn = document.getElementById("liveQuickRenderSceneBtn");
  if (quickRenderBtn) {
    quickRenderBtn.addEventListener("click", async () => {
      if (!CURRENT_PROJECT) return;
      await saveCurrentActiveScene();
      quickRenderBtn.disabled = true;
      quickRenderBtn.textContent = "🎞️ Renderizando Prévia...";
      showToast("Renderizando corte de 5s desta cena via FFmpeg...", "info");
      try {
        const res = await api(`/api/projects/${CURRENT_PROJECT.id}/scenes/${LIVE_ACTIVE_SCENE_IDX}/quick-render-preview`, {
          method: "POST",
        });
        CURRENT_PROJECT = res.project || CURRENT_PROJECT;
        LIVE_REAL_PREVIEW_URLS[LIVE_ACTIVE_SCENE_IDX] = res.preview_url;
        showToast("Prévia da cena pronta!", "success");
        setStudioPreviewMode("real");
      } catch (err) {
        showToast(`Erro ao renderizar prévia: ${err.message}`, "error");
      } finally {
        quickRenderBtn.disabled = false;
        quickRenderBtn.textContent = "🎞️ Renderizar Prévia MP4 Desta Cena (~5s)";
      }
    });
  }

  const instantBtn = document.getElementById("liveModeInstantBtn");
  const realBtn = document.getElementById("liveModeRealBtn");
  if (instantBtn && realBtn) {
    instantBtn.addEventListener("click", () => setStudioPreviewMode("instant"));
    realBtn.addEventListener("click", () => setStudioPreviewMode("real"));
  }

  const nextBtn = document.getElementById("step3NextBtn");
  if (nextBtn) {
    nextBtn.addEventListener("click", () => goToStep(4));
  }
}

function getActiveScene() {
  if (!CURRENT_PROJECT?.scenes) return null;
  return CURRENT_PROJECT.scenes.find((s) => s.index === LIVE_ACTIVE_SCENE_IDX) || CURRENT_PROJECT.scenes[0];
}

function setSceneOverlayMode(mode) {
  const sc = getActiveScene();
  if (!sc) return;
  sc.overlay_mode = mode;

  if (mode === "none_motion_only") {
    sc.overlay_image_path = null;
    sc.visual_layout = "full_media";
  } else if (mode === "matched_photo" || mode === "ai_generated") {
    if (sc.visual_layout === "full_media") {
      sc.visual_layout = "pip_card";
    }
  }

  renderActiveSceneBox();
  updateStudioInstantView();
}

async function saveCurrentActiveScene() {
  const sc = getActiveScene();
  if (!sc || !CURRENT_PROJECT) return;

  sc.narration_text = document.getElementById("liveActiveNarrationInput")?.value.trim() || sc.narration_text;
  const trailerVal = document.getElementById("liveActiveTrailerSelect")?.value;
  if (trailerVal) sc.video_path = trailerVal;
  sc.video_start_sec = parseFloat(document.getElementById("liveActiveVideoStartInput")?.value) || 0;
  sc.framing_mode = document.getElementById("liveActiveFramingModeSelect")?.value || "crop_center";
  sc.focus_x = parseFloat(document.getElementById("liveActiveFocusXInput")?.value) || 0.5;
  sc.overlay_label = document.getElementById("liveActiveOverlayLabelInput")?.value.trim().toUpperCase() || sc.overlay_label;
  sc.overlay_pos = document.getElementById("liveActiveOverlayPosSelect")?.value || "top_center";
  sc.ai_image_prompt = document.getElementById("liveActiveAiPromptInput")?.value.trim() || sc.ai_image_prompt;

  try {
    await api(`/api/projects/${CURRENT_PROJECT.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(CURRENT_PROJECT),
    });
    showToast(`Cena ${sc.index} salva com sucesso!`, "success");
    renderStudioTimeline();
    renderScenesGrid();
    updateStudioInstantView();
  } catch (err) {
    showToast(`Erro ao salvar cena: ${err.message}`, "error");
  }
}

function setStudioPreviewMode(mode) {
  LIVE_PREVIEW_MODE = mode;
  document.getElementById("liveModeInstantBtn")?.classList.toggle("active", mode === "instant");
  document.getElementById("liveModeRealBtn")?.classList.toggle("active", mode === "real");

  const realVideo = document.getElementById("liveRealSceneVideo");
  const bgVideo = document.getElementById("liveBgVideo");
  const bgImg = document.getElementById("liveBgImg");
  const ovLayer = document.getElementById("liveOverlayLayer");
  const subBox = document.getElementById("liveSubtitleBox");

  if (mode === "real") {
    const url = LIVE_REAL_PREVIEW_URLS[LIVE_ACTIVE_SCENE_IDX];
    if (url && realVideo) {
      realVideo.src = url;
      realVideo.classList.remove("hidden");
      realVideo.play().catch(() => {});
    } else {
      showToast("Clique em 'Renderizar Prévia MP4' para gerar o clipe desta cena primeiro.", "info");
      setStudioPreviewMode("instant");
      return;
    }
    bgVideo?.classList.add("hidden");
    bgImg?.classList.add("hidden");
    ovLayer?.classList.add("hidden");
    subBox?.classList.add("hidden");
  } else {
    if (realVideo) {
      realVideo.pause();
      realVideo.classList.add("hidden");
    }
    subBox?.classList.remove("hidden");
    updateStudioInstantView();
  }
}

function renderStep3() {
  if (!CURRENT_PROJECT) return;
  renderStudioTimeline();
  renderActiveSceneBox();
  renderScenesGrid();
  updateStudioInstantView();
}

function renderStudioTimeline() {
  const track = document.getElementById("liveTimelineTrack");
  if (!track || !CURRENT_PROJECT) return;
  track.innerHTML = "";

  (CURRENT_PROJECT.scenes || []).forEach((sc) => {
    const pill = document.createElement("button");
    pill.type = "button";
    pill.className = `timeline-pill ${sc.index === LIVE_ACTIVE_SCENE_IDX ? "active" : ""}`;
    const hasOverlay = Boolean(sc.overlay_image_path);
    pill.innerHTML = `
      <strong>Cena ${sc.index}</strong>
      <small>${hasOverlay ? "📰 Overlay" : "🎬 Vídeo Limpo"}</small>
    `;
    pill.addEventListener("click", () => {
      LIVE_ACTIVE_SCENE_IDX = sc.index;
      renderStudioTimeline();
      renderActiveSceneBox();
      updateStudioInstantView();
    });
    track.appendChild(pill);
  });
}

function updateSceneDurationBadge(sc) {
  const badge = document.getElementById("liveActiveDurationBadge");
  if (!badge) return;
  const words = (sc.narration_text || "").trim().split(/\s+/).filter(Boolean).length;
  const estSec = Math.round((words / 2.85) * 10) / 10;
  badge.textContent = `~${estSec}s (${words} palavras)`;
  badge.className = `mini-pill ${estSec > 16 ? "error" : "ok"}`;
}

function renderActiveSceneBox() {
  if (!CURRENT_PROJECT) return;
  const sc = getActiveScene();
  if (!sc) return;

  const titleEl = document.getElementById("liveActiveTitle");
  if (titleEl) {
    let tag = `Cena ${sc.index}`;
    if (sc.index === 1) tag += " • 🎯 GANCHO VIRAL";
    else if (sc.index === CURRENT_PROJECT.scenes.length) tag += " • 📣 CTA FINAL";
    titleEl.textContent = tag;
  }

  const badgeEl = document.getElementById("liveActiveModeBadge");
  if (badgeEl) {
    badgeEl.textContent = sc.overlay_mode || "auto";
    badgeEl.className = `mini-pill ${sc.overlay_image_path ? "ok" : ""}`;
  }

  updateSceneDurationBadge(sc);

  const reasonEl = document.getElementById("liveActiveReason");
  if (reasonEl) {
    reasonEl.textContent = sc.overlay_reason || (sc.overlay_image_path ? "Overlay contextual ativo" : "Fundo 100% vídeo de trailer em movimento");
  }

  // 1. Narração
  const narrInput = document.getElementById("liveActiveNarrationInput");
  if (narrInput) {
    narrInput.value = sc.narration_text || "";
  }

  // 2. Seletor de trailer
  const trailerSelect = document.getElementById("liveActiveTrailerSelect");
  if (trailerSelect) {
    trailerSelect.innerHTML = "";
    const clips = CONFIG?.gta6_clips || [];
    clips.forEach((clip) => {
      const opt = document.createElement("option");
      const filename = clip.filename || clip;
      const pathVal = clip.path || filename;
      opt.value = pathVal;
      opt.textContent = `🎬 ${filename}`;
      if (sc.video_path && (sc.video_path === pathVal || sc.video_path.includes(filename))) {
        opt.selected = true;
      }
      trailerSelect.appendChild(opt);
    });
  }

  const videoStartInput = document.getElementById("liveActiveVideoStartInput");
  if (videoStartInput) {
    videoStartInput.value = sc.video_start_sec || 0;
  }

  const framingSelect = document.getElementById("liveActiveFramingModeSelect");
  if (framingSelect) {
    framingSelect.value = sc.framing_mode || "crop_center";
  }
  const focusInput = document.getElementById("liveActiveFocusXInput");
  const focusValEl = document.getElementById("liveActiveFocusXVal");
  const focusField = document.getElementById("liveActiveFocusXField");
  const curFx = sc.focus_x !== undefined && sc.focus_x !== null ? sc.focus_x : 0.5;
  if (focusInput) focusInput.value = curFx;
  if (focusValEl) focusValEl.textContent = Number(curFx).toFixed(2);
  if (focusField) focusField.classList.toggle("hidden", (sc.framing_mode || "crop_center") !== "crop_focus");

  // 3. Toggles de Overlay
  const mode = sc.overlay_mode || (sc.overlay_image_path ? "matched_photo" : "none_motion_only");
  document.getElementById("ovModeNoneBtn")?.classList.toggle("active", mode === "none_motion_only");
  document.getElementById("ovModePhotoBtn")?.classList.toggle("active", mode === "matched_photo" || mode === "manual");
  document.getElementById("ovModeAiBtn")?.classList.toggle("active", mode === "ai_generated");

  const detailsPanel = document.getElementById("liveActiveOverlayDetails");
  const photoControls = document.getElementById("overlayPhotoControls");
  const aiControls = document.getElementById("overlayAiControls");

  if (mode === "none_motion_only") {
    detailsPanel?.classList.add("hidden");
  } else {
    detailsPanel?.classList.remove("hidden");
    if (mode === "ai_generated") {
      aiControls?.classList.remove("hidden");
      photoControls?.classList.add("hidden");
    } else {
      photoControls?.classList.remove("hidden");
      aiControls?.classList.add("hidden");
    }
  }

  const labelInput = document.getElementById("liveActiveOverlayLabelInput");
  if (labelInput) {
    labelInput.value = sc.overlay_label || (sc.title || "GTA 6 NOTÍCIA").toUpperCase();
  }

  const posSelect = document.getElementById("liveActiveOverlayPosSelect");
  if (posSelect) {
    posSelect.value = sc.overlay_pos || "top_center";
  }

  const promptInput = document.getElementById("liveActiveAiPromptInput");
  if (promptInput) {
    promptInput.value = sc.ai_image_prompt || `GTA 6 cinematic high detail rendering of ${sc.title || CURRENT_PROJECT.title}`;
  }

  const filenameEl = document.getElementById("liveActiveOverlayFilename");
  if (filenameEl) {
    if (sc.overlay_image_path) {
      filenameEl.textContent = `Imagem ativa: ${sc.overlay_image_path.split(/[\\/]/).pop()}`;
    } else {
      filenameEl.textContent = "Nenhuma imagem anexada.";
    }
  }
}

function renderScenesGrid() {
  const container = document.getElementById("scenesContainer");
  if (!container || !CURRENT_PROJECT) return;
  container.innerHTML = "";

  (CURRENT_PROJECT.scenes || []).forEach((sc) => {
    const card = document.createElement("div");
    card.className = `scene-grid-card ${sc.index === LIVE_ACTIVE_SCENE_IDX ? "active" : ""}`;
    
    let trailerName = "Trailer Oficial";
    if (sc.video_path) {
      trailerName = sc.video_path.split(/[\\/]/).pop();
    }

    card.innerHTML = `
      <div class="scene-grid-head">
        <strong>Cena ${sc.index}</strong>
        <span class="mini-pill ${sc.overlay_image_path ? "ok" : ""}">${sc.overlay_mode || "auto"}</span>
      </div>
      <div class="scene-grid-thumb">
        <video muted playsinline preload="metadata" src="/api/projects/${CURRENT_PROJECT.id}/scene-video-stream/${sc.index}#t=${sc.video_start_sec || 0}"></video>
      </div>
      <div class="scene-grid-text">${escapeHtml(sc.narration_text || "")}</div>
      <div class="inline-pair" style="font-size:11px; color:var(--muted); justify-content:space-between;">
        <span>🎬 ${escapeHtml(trailerName)}</span>
        <span>${sc.video_start_sec || 0}s</span>
      </div>
      <button type="button" class="btn btn-secondary btn-xs select-scene-btn" data-idx="${sc.index}">
        ✏️ Editar Cena ${sc.index}
      </button>
    `;
    card.querySelector(".select-scene-btn")?.addEventListener("click", () => {
      LIVE_ACTIVE_SCENE_IDX = sc.index;
      renderStep3();
      document.getElementById("liveActiveSceneBox")?.scrollIntoView({ behavior: "smooth" });
    });
    container.appendChild(card);
  });
}

function updateStudioInstantView() {
  if (!CURRENT_PROJECT || LIVE_PREVIEW_MODE !== "instant") return;
  const sc = getActiveScene();
  if (!sc) return;

  const bgVideo = document.getElementById("liveBgVideo");
  const bgImg = document.getElementById("liveBgImg");
  const ovLayer = document.getElementById("liveOverlayLayer");
  const subBox = document.getElementById("liveSubtitleBox");

  // Fundo em vídeo
  if (sc.video_path) {
    if (bgVideo) {
      bgVideo.classList.remove("hidden");
      const streamUrl = `/api/projects/${CURRENT_PROJECT.id}/scene-video-stream/${sc.index}`;
      if (!bgVideo.src.includes(streamUrl)) {
        bgVideo.src = streamUrl;
      }
      bgVideo.currentTime = sc.video_start_sec || 0;
      bgVideo.play().catch(() => {});
    }
    bgImg?.classList.add("hidden");
  } else if (sc.image_path) {
    if (bgImg) {
      bgImg.src = `/api/projects/${CURRENT_PROJECT.id}/scene-preview/${sc.index}`;
      bgImg.classList.remove("hidden");
    }
    bgVideo?.classList.add("hidden");
  }

  // Camada de Overlay
  if (sc.overlay_image_path && ovLayer && sc.overlay_mode !== "none_motion_only") {
    ovLayer.classList.remove("hidden");
    const ovUrl = `/api/projects/${CURRENT_PROJECT.id}/scene-overlay-preview/${sc.index}`;
    const label = sc.overlay_label || (sc.title || "NOTÍCIA").toUpperCase();
    ovLayer.innerHTML = `
      <div class="live-overlay-card">
        <div class="live-ov-label">${escapeHtml(label)}</div>
        <img src="${ovUrl}" alt="Overlay" />
      </div>
    `;
  } else if (ovLayer) {
    ovLayer.classList.add("hidden");
    ovLayer.innerHTML = "";
  }

  // Legenda Vice City animada
  if (subBox) {
    const text = sc.narration_text || "";
    const words = text.split(" ").slice(0, 7).join(" ");
    subBox.innerHTML = `
      <div class="vice-subtitle">
        <span class="sub-highlight">${escapeHtml(words.toUpperCase())}</span>
      </div>
    `;
  }
}

// =========================================================================
// ETAPA 4: GERAR SHORTS, YOUTUBE & THUMBNAIL
// =========================================================================

function setupStep4RenderHandlers() {
  const renderBtn = document.getElementById("renderAllBtn");
  if (renderBtn) {
    renderBtn.addEventListener("click", startFullRender);
  }

  const regenThumbBtn = document.getElementById("regenThumbBtn");
  if (regenThumbBtn) {
    regenThumbBtn.addEventListener("click", regenerateThumbnail);
  }

  const humanApproveBtn = document.getElementById("humanApproveBtn");
  if (humanApproveBtn) {
    humanApproveBtn.addEventListener("click", async () => {
      if (!CURRENT_PROJECT) return;
      await toggleHumanApproval(CURRENT_PROJECT.id);
    });
  }

  const copyYtBtn = document.getElementById("copyYoutubePackBtn");
  if (copyYtBtn) {
    copyYtBtn.addEventListener("click", () => {
      const gate = isProjectPublishReady(CURRENT_PROJECT);
      if (!gate.ready) {
        showToast("Aprovação do QA e revisão humana obrigatórias antes de copiar o pacote.", "warn");
        return;
      }
      const title = document.getElementById("editYoutubeTitle")?.value || "";
      const desc = document.getElementById("editYoutubeDescription")?.value || "";
      const tags = document.getElementById("editYoutubeTags")?.value || "";
      const fullText = `TÍTULO:\n${title}\n\nDESCRIÇÃO:\n${desc}\n\nTAGS:\n${tags}`;
      navigator.clipboard.writeText(fullText).then(() => {
        showToast("Pacote completo do YouTube copiado para a área de transferência!", "success");
      });
    });
  }

  const copyTtBtn = document.getElementById("copyTiktokCaptionBtn");

  const togglePostedBtn = document.getElementById("togglePostedBtn");
  if (togglePostedBtn) {
    togglePostedBtn.addEventListener("click", async () => {
      if (!CURRENT_PROJECT) return;
      await toggleProjectPosted(CURRENT_PROJECT.id);
    });
  }

  const saveDetailsBtn = document.getElementById("savePostedDetailsBtn");
  if (saveDetailsBtn) {
    saveDetailsBtn.addEventListener("click", async () => {
      if (!CURRENT_PROJECT) return;
      const platform = document.getElementById("editPostedPlatform")?.value || "youtube";
      const url = document.getElementById("editPostedUrl")?.value || "";
      try {
        const updated = await api(`/api/projects/${CURRENT_PROJECT.id}/toggle-posted`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_posted: true, platform, url }),
        });
        CURRENT_PROJECT = updated;
        showToast("Detalhes da postagem salvos com sucesso!", "success");
        await loadProjects();
        updateProjectHeader();
        renderPublishStatusCard();
      } catch (err) {
        showToast(`Erro ao salvar detalhes: ${err.message}`, "error");
      }
    });
  }

  const openUrlBtn = document.getElementById("openPostedUrlBtn");
  if (openUrlBtn) {
    openUrlBtn.addEventListener("click", () => {
      const url = document.getElementById("editPostedUrl")?.value?.trim();
      if (url) {
        window.open(url, "_blank");
      } else {
        showToast("Insira o link do vídeo postado primeiro.", "info");
      }
    });
  }
  if (copyTtBtn) {
    copyTtBtn.addEventListener("click", () => {
      const gate = isProjectPublishReady(CURRENT_PROJECT);
      if (!gate.ready) {
        showToast("Aprovação do QA e revisão humana obrigatórias antes de copiar a legenda.", "warn");
        return;
      }
      const caption = document.getElementById("editTiktokCaption")?.value || "";
      navigator.clipboard.writeText(caption).then(() => {
        showToast("Legenda do TikTok copiada!", "success");
      });
    });
  }
}

function renderStep4() {
  if (!CURRENT_PROJECT) return;
  const isRendered = Boolean(CURRENT_PROJECT.outputs?.full_video || CURRENT_PROJECT.outputs?.single_video);

  const titleInput = document.getElementById("editYoutubeTitle");
  if (titleInput) {
    titleInput.value = CURRENT_PROJECT.youtube_title || CURRENT_PROJECT.title || "";
    updateYtTitleCount();
    titleInput.oninput = updateYtTitleCount;
  }

  const descInput = document.getElementById("editYoutubeDescription");
  if (descInput) {
    descInput.value = CURRENT_PROJECT.youtube_description || "";
  }

  const tagsInput = document.getElementById("editYoutubeTags");
  if (tagsInput) {
    tagsInput.value = (CURRENT_PROJECT.youtube_tags || []).join(", ");
  }

  const ttInput = document.getElementById("editTiktokCaption");
  if (ttInput) {
    ttInput.value = CURRENT_PROJECT.tiktok_caption || "";
  }

  const thumbHeadline = document.getElementById("editThumbHeadline");
  if (thumbHeadline) {
    thumbHeadline.value = CURRENT_PROJECT.thumbnail_headline || CURRENT_PROJECT.hook_title || CURRENT_PROJECT.title || "";
  }

  const thumbBadge = document.getElementById("editThumbBadge");
  if (thumbBadge) {
    thumbBadge.value = CURRENT_PROJECT.thumbnail_badge || "🔥 OFICIAL • ROCKSTAR GAMES";
  }

  renderOutputsGrid();
  renderMediaLicensesCard();
}

function renderMediaLicensesCard() {
  const container = document.getElementById("mediaLicensesList");
  const badge = document.getElementById("mediaLicenseBadge");
  if (!container || !CURRENT_PROJECT) return;

  const used = CURRENT_PROJECT.used_media || [];
  if (used.length === 0) {
    container.innerHTML = `
      <div class="empty-hint" style="padding:16px; text-align:center; color:var(--text-muted);">
        Nenhum registro de mídia catalogada ainda. Re-sincronize as mídias no Passo 3 ou renderize o vídeo para auditar.
      </div>
    `;
    if (badge) {
      badge.textContent = "🛡️ 0 Mídias";
      badge.className = "mini-pill";
    }
    return;
  }

  const hasHighRisk = used.some(m => m.risk_level === "alto");
  if (badge) {
    if (hasHighRisk) {
      badge.textContent = "⚠️ Mídias com Risco";
      badge.className = "mini-pill error";
    } else {
      badge.textContent = `🛡️ ${used.length} Mídias Verificadas`;
      badge.className = "mini-pill ok";
    }
  }

  container.innerHTML = `
    <div style="display:flex; flex-direction:column; gap:10px;">
      ${used.map(m => {
        const isAudio = m.role === "soundtrack";
        const icon = isAudio ? "🎵" : "🎬";
        const riskClass = m.risk_level === "alto" ? "error" : (m.risk_level === "medio" ? "pending" : "ok");
        const riskLabel = m.risk_level === "alto" ? "⚠️ Risco Alto (Licença Pendente)" : (m.risk_level === "medio" ? "Fair Use / Transformativo" : "Licença Verificada");
        const scenesTag = (m.scenes_used && m.scenes_used.length) ? `Cenas: ${m.scenes_used.map(s => `C${s}`).join(", ")}` : "";
        const sourceLink = (m.source_url && m.source_url.startsWith("http")) 
          ? `<a href="${m.source_url}" target="_blank" rel="noopener" style="color:var(--accent); text-decoration:underline;">Ver Origem ↗</a>` 
          : `<span class="muted">${escapeHtml(m.source_url || "Local")}</span>`;

        return `
          <div style="background:var(--surface-2); border-radius:8px; padding:12px 14px; border:1px solid rgba(255,255,255,0.06); display:flex; flex-direction:column; gap:6px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
              <div style="display:flex; align-items:center; gap:8px;">
                <span style="font-size:18px;">${icon}</span>
                <strong style="color:var(--text-bright);">${escapeHtml(m.filename || m.media_id)}</strong>
                <span class="mini-pill" style="font-size:11px;">${isAudio ? "Trilha Sonora" : "Corte de Trailer"}</span>
                ${scenesTag ? `<span class="mini-pill" style="font-size:11px; opacity:0.8;">${scenesTag}</span>` : ""}
              </div>
              <span class="mini-pill ${riskClass}">${riskLabel}</span>
            </div>
            <div style="font-size:12px; color:var(--text-muted); display:flex; flex-wrap:wrap; gap:16px;">
              <span><strong>Detentor:</strong> ${escapeHtml(m.copyright_owner || "Desconhecido")}</span>
              <span><strong>Licença:</strong> <code>${escapeHtml(m.license_type || "desconhecido")}</code></span>
              <span><strong>Origem:</strong> ${sourceLink}</span>
            </div>
            ${m.usage_notes ? `<div style="font-size:11px; color:rgba(255,255,255,0.55); margin-top:2px;">ℹ️ ${escapeHtml(m.usage_notes)}</div>` : ""}
          </div>
        `;
      }).join("")}
    </div>
  `;
}

function updateYtTitleCount() {
  const titleInput = document.getElementById("editYoutubeTitle");
  const countBadge = document.getElementById("ytTitleCharCount");
  if (titleInput && countBadge) {
    const len = titleInput.value.length;
    countBadge.textContent = `${len}/95`;
    countBadge.className = `mini-pill ${len > 95 ? "error" : "ok"}`;
  }
}

async function startFullRender() {
  if (!CURRENT_PROJECT) return;
  const renderBtn = document.getElementById("renderAllBtn");
  const jobBox = document.getElementById("renderJobBox");
  const jobMsg = document.getElementById("jobMessage");
  const jobPct = document.getElementById("jobPercent");
  const jobBar = document.getElementById("jobProgressBar");
  const jobErr = document.getElementById("jobError");

  if (renderBtn) renderBtn.disabled = true;
  if (jobBox) jobBox.classList.remove("hidden");
  if (jobErr) jobErr.classList.add("hidden");

  try {
    await api(`/api/projects/${CURRENT_PROJECT.id}/render`, { method: "POST" });
    pollRenderJob();
  } catch (err) {
    showToast(`Erro ao iniciar renderização: ${err.message}`, "error");
    if (renderBtn) renderBtn.disabled = false;
  }
}

function pollRenderJob() {
  if (POLL_TIMER) clearInterval(POLL_TIMER);
  POLL_TIMER = setInterval(async () => {
    if (!CURRENT_PROJECT) {
      clearInterval(POLL_TIMER);
      return;
    }
    try {
      const job = await api(`/api/jobs/${CURRENT_PROJECT.id}`);
      const jobMsg = document.getElementById("jobMessage");
      const jobPct = document.getElementById("jobPercent");
      const jobBar = document.getElementById("jobProgressBar");
      const jobErr = document.getElementById("jobError");
      const renderBtn = document.getElementById("renderAllBtn");

      if (jobMsg) jobMsg.textContent = job.message || "Renderizando...";
      if (jobPct) jobPct.textContent = `${job.progress || 0}%`;
      if (jobBar) jobBar.style.width = `${job.progress || 0}%`;

      if (job.state === "completed") {
        clearInterval(POLL_TIMER);
        if (renderBtn) renderBtn.disabled = false;
        showToast("Vídeo renderizado com sucesso!", "success");
        await loadProject(CURRENT_PROJECT.id);
        renderOutputsGrid();
      } else if (job.state === "error") {
        clearInterval(POLL_TIMER);
        if (renderBtn) renderBtn.disabled = false;
        if (jobErr) {
          jobErr.textContent = job.error || "Falha na renderização.";
          jobErr.classList.remove("hidden");
        }
        showToast("Falha na renderização do vídeo.", "error");
      }
    } catch (_) {}
  }, 1500);
}

function renderOutputsGrid() {
  const sec = document.getElementById("outputsSection");
  const grid = document.getElementById("outputsGrid");
  if (!sec || !grid || !CURRENT_PROJECT) return;

  const vidKind = CURRENT_PROJECT.outputs?.youtube_short ? "youtube_short" : (CURRENT_PROJECT.outputs?.single_video ? "single_video" : "full_video");
  const vidPath = CURRENT_PROJECT.outputs?.[vidKind];
  if (!vidPath) {
    sec.classList.add("hidden");
    return;
  }

  sec.classList.remove("hidden");
  grid.innerHTML = `
    <div class="output-card">
      <div class="output-preview-wrap">
        <video controls playsinline src="/api/projects/${CURRENT_PROJECT.id}/outputs/${vidKind}"></video>
      </div>
      <div class="output-info">
        <h4>YouTube Shorts Oficial (1080×1920)</h4>
        <p class="muted">Áudio com locução neural + trilha de ação e legendas Vice City em safe-zone (&lt;= 58s).</p>
        <a href="/api/projects/${CURRENT_PROJECT.id}/outputs/${vidKind}?download=true" download class="btn btn-primary btn-sm">
          ⬇️ Baixar Vídeo MP4
        </a>
      </div>
    </div>
  `;
}

async function regenerateThumbnail() {
  if (!CURRENT_PROJECT) return;
  const regenBtn = document.getElementById("regenThumbBtn");
  if (regenBtn) {
    regenBtn.disabled = true;
    regenBtn.textContent = "Gerando Capa...";
  }

  CURRENT_PROJECT.thumbnail_style = document.getElementById("editThumbStyle")?.value || "vice_city_magazine";
  CURRENT_PROJECT.thumbnail_headline = document.getElementById("editThumbHeadline")?.value || "";
  CURRENT_PROJECT.thumbnail_badge = document.getElementById("editThumbBadge")?.value || "";

  try {
    await api(`/api/projects/${CURRENT_PROJECT.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(CURRENT_PROJECT),
    });

    const res = await api(`/api/projects/${CURRENT_PROJECT.id}/regenerate-thumbnail`, { method: "POST" });
    const preview = document.getElementById("thumbStudioPreview");
    const empty = document.getElementById("thumbPreviewEmpty");
    if (preview && res.cover_url) {
      preview.src = res.cover_url;
      preview.classList.remove("hidden");
      if (empty) empty.classList.add("hidden");
    }
    showToast("Capa gerada com sucesso!", "success");
  } catch (err) {
    showToast(`Erro ao gerar capa: ${err.message}`, "error");
  } finally {
    if (regenBtn) {
      regenBtn.disabled = false;
      regenBtn.textContent = "🎨 Gerar Capa de Destaque";
    }
  }
}

// =========================================================================
// SALVAR PROJETO & MODAIS
// =========================================================================

async function saveCurrentProject() {
  if (!CURRENT_PROJECT) return;
  try {
    await api(`/api/projects/${CURRENT_PROJECT.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(CURRENT_PROJECT),
    });
    showToast("Projeto salvo com sucesso!", "success");
  } catch (err) {
    showToast(`Erro ao salvar: ${err.message}`, "error");
  }
}

function setupModals() {
  const openImport = document.getElementById("openImportJsonModalBtn");
  const closeImport = document.getElementById("closeImportJsonBtn");
  const importModal = document.getElementById("importJsonModal");
  if (openImport && importModal) {
    openImport.addEventListener("click", () => importModal.classList.remove("hidden"));
  }
  if (closeImport && importModal) {
    closeImport.addEventListener("click", () => importModal.classList.add("hidden"));
  }

  const confirmImport = document.getElementById("confirmImportJsonBtn");
  if (confirmImport) {
    confirmImport.addEventListener("click", async () => {
      const text = document.getElementById("agentJsonTextarea")?.value.trim();
      if (!text) return;
      try {
        const parsed = JSON.parse(text);
        const res = await api("/api/projects/import-json", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(parsed),
        });
        showToast("Projeto importado com sucesso!", "success");
        importModal?.classList.add("hidden");
        await loadProjects();
        loadProject(res.id);
      } catch (err) {
        showToast(`Erro ao importar: ${err.message}`, "error");
      }
    });
  }

  const openKey = document.getElementById("openKeyModalBtn");
  const closeKey = document.getElementById("closeKeyModalBtn");
  const keyModal = document.getElementById("keyModal");
  if (openKey && keyModal) {
    openKey.addEventListener("click", () => keyModal.classList.remove("hidden"));
  }
  if (closeKey && keyModal) {
    closeKey.addEventListener("click", () => keyModal.classList.add("hidden"));
  }

  const saveKey = document.getElementById("saveKeyBtn");
  if (saveKey) {
    saveKey.addEventListener("click", async () => {
      const key = document.getElementById("geminiKeyInput")?.value.trim();
      try {
        await api("/api/config/apikey", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ api_key: key }),
        });
        showToast("Chave Gemini salva!", "success");
        keyModal?.classList.add("hidden");
        await loadConfig();
      } catch (err) {
        showToast(`Erro ao salvar chave: ${err.message}`, "error");
      }
    });
  }

  const closePacked = document.getElementById("closePackedModalBtn");
  const packedModal = document.getElementById("packedModal");
  if (closePacked && packedModal) {
    closePacked.addEventListener("click", () => packedModal.classList.add("hidden"));
  }

  const copyJson = document.getElementById("copyProjectJsonBtn");
  if (copyJson) {
    copyJson.addEventListener("click", () => {
      const text = document.getElementById("packedTextarea")?.value;
      if (text) {
        navigator.clipboard.writeText(text).then(() => showToast("JSON copiado!", "success"));
      }
    });
  }
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
