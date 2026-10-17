/* NineDeo — logique de la popup. */
"use strict";

const U = globalThis.NineDeo;
const { api } = U;
const { h, icon, iconButton, call } = globalThis.NineDeoDom;

const $ = (sel) => document.querySelector(sel);

const state = {
  tabId: null,
  tab: null,
  host: { status: "disconnected" },
  media: null,
  jobs: new Map(),
  settings: U.DEFAULT_SETTINGS,
  probe: null,
};

// ---------------------------------------------------------------- utilitaires
let toastTimer;
function toast(text, isError) {
  const el = $("#toast");
  el.textContent = text;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), isError ? 6000 : 2600);
}

function selectTab(name) {
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  $("#tab-page").hidden = name !== "page";
  $("#tab-jobs").hidden = name !== "jobs";
}

function hostReady() {
  return state.host.status === "connected";
}

// ---------------------------------------------------------------- hôte
function renderHost() {
  const pill = $("#hostPill");
  const s = state.host.status;
  pill.dataset.status = s;
  const labels = {
    connected: "Moteur prêt",
    connecting: "Connexion…",
    missing: "Moteur absent",
    error: "Moteur en erreur",
    disconnected: "Moteur en veille",
  };
  pill.querySelector(".label").textContent = labels[s] || s;
  const info = state.host.info;
  pill.title = info
    ? `Hôte ${info.version} · yt-dlp ${info.ytdlp} · ffmpeg ${info.ffmpeg_version || "absent"}`
    : (state.host.error && state.host.error.message) || "État du moteur de téléchargement";
  $("#hostBanner").hidden = !(s === "missing" || s === "error");
  if (s === "error" && state.host.error) {
    $("#hostBanner strong").textContent = "Le moteur NineDeo ne répond pas.";
  } else {
    $("#hostBanner strong").textContent = "Moteur NineDeo introuvable.";
  }
}

// ---------------------------------------------------------------- page
function fillPresetSelect(select, heights, formats, selected) {
  select.replaceChildren();
  const presets = U.PRESETS.filter((p) => {
    if (!heights || !heights.length) return true;
    const m = p.id.match(/^h(\d+)$/);
    return !m || Number(m[1]) <= heights[0] || heights.includes(Number(m[1]));
  });
  const g1 = h("optgroup", { label: "Qualité" });
  for (const p of presets) {
    let label = p.label;
    if (p.id === "best" && heights && heights.length) label += ` (${heights[0]}p)`;
    g1.append(h("option", { value: p.id }, label));
  }
  select.append(g1);
  if (formats && formats.length) {
    const g2 = h("optgroup", { label: "Formats exacts (avancé)" });
    for (const f of formats.slice(0, 60)) {
      const parts = [];
      if (f.height) parts.push(`${f.height}p${f.fps && f.fps > 30 ? Math.round(f.fps) : ""}`);
      else if (!f.vcodec && f.acodec) parts.push("audio");
      parts.push(f.ext);
      if (f.vcodec) parts.push(f.vcodec);
      if (f.acodec && f.vcodec) parts.push("+" + f.acodec);
      else if (f.acodec) parts.push(f.acodec);
      if (f.size) parts.push(U.formatBytes(f.size));
      else if (f.tbr) parts.push(Math.round(f.tbr) + " kb/s");
      g2.append(h("option", { value: "format:" + f.id }, `${parts.join(" · ")}  [${f.id}]`));
    }
    select.append(g2);
  }
  select.value = [...select.options].some((o) => o.value === selected) ? selected : "best";
}

function renderPage() {
  const tab = state.tab || {};
  const media = state.media || {};
  $("#pageTitle").textContent = tab.title || media.title || "Page sans titre";
  $("#pageUrl").textContent = tab.url ? U.shortUrl(tab.url, 64) : "";
  const fav = $("#favicon");
  if (tab.favIconUrl && /^https?:|^data:/.test(tab.favIconUrl)) fav.src = tab.favIconUrl;
  else fav.removeAttribute("src");

  const drm = media.drm;
  const banner = $("#drmBanner");
  if (drm && drm.active) {
    banner.hidden = false;
    banner.textContent =
      "Ce site lit un contenu protégé par DRM" + (drm.keySystem ? ` (${drmName(drm.keySystem)})` : "") +
      ". NineDeo ne télécharge pas les contenus protégés ; les autres vidéos de la page restent disponibles.";
  } else {
    banner.hidden = true;
  }

  const supported = /^https?:/.test(tab.url || "");
  $("#pageDownload").disabled = !supported;
  $("#analyze").disabled = !supported || !hostReady();
  if (!supported) $("#probeStatus").textContent = "Page interne du navigateur.";
}

function drmName(ks) {
  if (/widevine/i.test(ks)) return "Widevine";
  if (/playready/i.test(ks)) return "PlayReady";
  if (/fps|fairplay/i.test(ks)) return "FairPlay";
  if (/clearkey/i.test(ks)) return "ClearKey";
  return ks;
}

function renderProbe() {
  const p = state.probe;
  const box = $("#probeResult");
  if (!p) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  const thumb = $("#probeThumb");
  if (p.thumbnail) thumb.src = p.thumbnail;
  else thumb.removeAttribute("src");
  $("#probeTitle").textContent = p.title || "";
  const sub = [];
  if (p.extractor && p.extractor !== "Generic") sub.push(p.extractor);
  if (p.kind === "playlist") sub.push(`Playlist · ${p.count} vidéos`);
  if (p.duration) sub.push(U.formatDuration(p.duration));
  if (p.is_live) sub.push("EN DIRECT");
  if (p.heights && p.heights.length) sub.push(p.heights.length > 1 ? `${p.heights[p.heights.length - 1]}p → ${p.heights[0]}p` : `${p.heights[0]}p`);
  $("#probeSub").textContent = sub.join(" · ");
  fillPresetSelect($("#pagePreset"), p.heights, p.formats, $("#pagePreset").value);
}

async function analyze() {
  const btn = $("#analyze");
  const status = $("#probeStatus");
  btn.disabled = true;
  status.className = "hint";
  status.textContent = "Analyse en cours…";
  try {
    state.probe = await call("probe", { tabId: state.tabId });
    status.textContent = state.probe.kind === "playlist" ? "Playlist détectée" : `${(state.probe.formats || []).length} formats trouvés`;
    renderProbe();
  } catch (e) {
    status.className = "hint error";
    status.textContent = e.code === "unsupported" ? "Analyse impossible : utilisez les flux détectés ci-dessous." : e.message;
    status.title = e.detail || "";
  } finally {
    btn.disabled = !hostReady();
  }
}

async function downloadPage() {
  const value = $("#pagePreset").value;
  const isFormat = value.startsWith("format:");
  const btn = $("#pageDownload");
  btn.disabled = true;
  try {
    await call("download", {
      tabId: state.tabId,
      source: { type: "page" },
      preset: isFormat ? "best" : value,
      format_id: isFormat ? value.slice(7) : null,
      title: state.probe && state.probe.title,
      thumbnail: state.probe && state.probe.thumbnail,
    });
    toast("Ajouté aux téléchargements");
    selectTab("jobs");
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------- flux
function itemLabel(it) {
  const parts = [];
  if (it.variants && it.variants.length) {
    parts.push(it.variants[0].height ? `${it.variants[0].height}p max` : "Multi-qualité");
    parts.push(`${it.variants.length} qualité${it.variants.length > 1 ? "s" : ""}`);
  } else if (it.heights && it.heights.length) {
    parts.push(`${it.heights[0]}p max`);
    if (it.heights.length > 1) parts.push(`${it.heights.length} qualités`);
  } else if (it.height) {
    parts.push(`${it.height}p`);
  }
  if (it.duration) parts.push(U.formatDuration(it.duration));
  if (it.size) parts.push(U.formatBytes(it.size));
  if (it.live) parts.push("direct");
  if (!parts.length) {
    parts.push(it.kind === "file" ? `${(it.ext || "").toUpperCase()} ${it.audio ? "audio" : "vidéo"}`.trim() : `${it.kind.toUpperCase()} · qualité à l'analyse`);
  }
  return parts.join(" · ");
}

function badgeFor(it) {
  if (it.drm) return h("span", { class: "badge drm", title: "Protégé par DRM" }, "DRM");
  if (it.kind === "hls") return h("span", { class: "badge hls" }, "HLS");
  if (it.kind === "dash") return h("span", { class: "badge dash" }, "DASH");
  return h("span", { class: "badge " + (it.audio ? "audio" : "file") }, (it.ext || (it.audio ? "audio" : "vidéo")).toUpperCase().slice(0, 5));
}

function renderMedia() {
  const list = $("#mediaList");
  const items = (state.media && state.media.items) || [];
  list.replaceChildren();
  $("#mediaCount").textContent = items.length ? String(items.length) : "";
  $("#mediaEmpty").hidden = items.length > 0;
  for (const it of items) list.append(mediaRow(it));
}

function mediaRow(it) {
  const li = h("li", { class: "item" + (it.drm ? " drm" : "") });
  const type = it.audio ? "Audio" : it.kind === "file" ? "Fichier vidéo" : it.kind === "hls" ? "Flux adaptatif" : "Flux adaptatif";
  li.append(
    h("div", { class: "item-head" },
      badgeFor(it),
      h("div", { class: "item-main" },
        h("div", { class: "item-title", title: it.url }, itemLabel(it)),
        h("div", { class: "item-sub", title: it.url }, `${type} · ${U.shortUrl(it.url, 48)}`)
      ),
      iconButton("copy", "Copier l'adresse", () => {
        navigator.clipboard.writeText(it.url).then(() => toast("Adresse copiée"), () => toast("Copie impossible", true));
      })
    )
  );

  if (it.drm) {
    li.append(h("div", { class: "item-sub" }, icon("lock"), " Contenu protégé : téléchargement non pris en charge."));
    return li;
  }

  const actions = h("div", { class: "item-actions" });
  const heights = it.variants ? [...new Set(it.variants.map((v) => v.height).filter(Boolean))] : it.heights || [];
  let select = null;
  if (heights.length > 1 || it.audio === false) {
    select = h("select", { "aria-label": "Qualité du flux" });
    select.append(h("option", { value: "best" }, "Meilleure qualité" + (heights[0] ? ` (${heights[0]}p)` : "")));
    for (const ht of heights) select.append(h("option", { value: "h" + ht }, `${ht}p`));
    select.append(h("option", { value: "audio_mp3" }, "Audio seul — MP3"));
    const def = state.settings.default_preset;
    if ([...select.options].some((o) => o.value === def)) select.value = def;
    actions.append(select);
  } else {
    actions.append(h("span", { class: "item-sub", style: "flex:1" }, ""));
  }

  const needsHost = it.kind !== "file";
  const btn = h("button", { type: "button", class: "btn small primary" }, icon("download"), "Télécharger");
  if (!hostReady() && needsHost && ["missing", "error"].includes(state.host.status)) {
    btn.disabled = true;
    btn.title = "Les flux HLS/DASH nécessitent le moteur NineDeo";
  }
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      if (!hostReady() && !needsHost && ["missing", "error"].includes(state.host.status)) {
        // Repli sans moteur : téléchargement direct par le navigateur.
        await api.downloads.download({ url: it.url, saveAs: false });
        toast("Téléchargement lancé par le navigateur");
      } else {
        await call("download", { tabId: state.tabId, source: { type: "item", key: it.key }, preset: select ? select.value : "best" });
        toast("Ajouté aux téléchargements");
        selectTab("jobs");
      }
    } catch (e) {
      toast(e.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  actions.append(btn);
  li.append(actions);
  return li;
}

// ---------------------------------------------------------------- tâches
const ACTIVE = ["queued", "starting", "downloading", "processing"];

function jobStatusLine(j) {
  const pct = j.progress != null ? `${Math.floor(j.progress * 100)} %` : null;
  switch (j.status) {
    case "queued":
      return [h("span", {}, "En attente…")];
    case "starting":
      return [h("span", {}, "Démarrage…")];
    case "downloading": {
      const out = [];
      if (j.stage) out.push(h("span", {}, j.stage));
      if (pct) out.push(h("span", {}, pct));
      if (j.downloaded) out.push(h("span", {}, U.formatBytes(j.downloaded) + (j.total ? " / " + U.formatBytes(j.total) : "")));
      if (j.speed) out.push(h("span", {}, U.formatSpeed(j.speed)));
      if (j.eta != null) out.push(h("span", {}, U.formatEta(j.eta) + " restantes"));
      return out.length ? out : [h("span", {}, "Téléchargement…")];
    }
    case "processing":
      return [h("span", {}, (j.stage || "Traitement") + "…")];
    case "finished":
      return [h("span", { class: "ok" }, "Terminé"), j.total ? h("span", {}, U.formatBytes(j.total)) : null, j.filename ? h("span", { title: j.filepath || "" }, j.filename) : null];
    case "paused":
      return [h("span", {}, "En pause" + (pct ? " · " + pct : ""))];
    case "interrupted":
      return [h("span", {}, "Interrompu — cliquez sur reprendre")];
    case "canceled":
      return [h("span", {}, "Annulé")];
    case "error":
      return [h("span", { class: "err", title: (j.error && j.error.detail) || "" }, (j.error && j.error.message) || "Erreur")];
    default:
      return [h("span", {}, j.status)];
  }
}

function jobActions(j) {
  const act = (action) => async () => {
    try {
      await call("job-action", { id: j.id, action });
    } catch (e) {
      toast(e.message, true);
    }
  };
  const out = [];
  if (["queued", "starting", "downloading"].includes(j.status)) {
    out.push(iconButton("pause", "Pause", act("pause")));
    out.push(iconButton("stop", "Annuler", act("cancel"), "danger"));
  } else if (["paused", "interrupted"].includes(j.status)) {
    out.push(iconButton("play", "Reprendre", act("resume")));
    out.push(iconButton("stop", "Annuler", act("cancel"), "danger"));
  } else if (["error", "canceled"].includes(j.status)) {
    out.push(iconButton("retry", "Réessayer", act("retry")));
    out.push(iconButton("close", "Retirer de la liste", act("remove")));
  } else if (j.status === "finished") {
    out.push(iconButton("open", "Ouvrir", act("open")));
    out.push(iconButton("folder", "Afficher dans le dossier", act("reveal")));
    out.push(iconButton("close", "Retirer de la liste", act("remove")));
  }
  return out;
}

function jobRow(j) {
  const li = h("li", { class: "item job", dataset: { id: j.id } });
  const thumb = h("div", { class: "thumb-box" });
  if (j.thumbnail) {
    const img = h("img", { src: j.thumbnail, alt: "" });
    img.addEventListener("error", () => img.replaceWith(icon(j.preset && j.preset.startsWith("audio") ? "music" : "film")));
    thumb.append(img);
  } else {
    thumb.append(icon(j.preset && j.preset.startsWith("audio") ? "music" : "film"));
  }
  const bar = h("div", { class: "progress" }, h("i"));
  const i = bar.firstChild;
  if (j.status === "finished") {
    bar.classList.add("done");
    i.style.width = "100%";
  } else if (j.status === "error" || j.status === "canceled") {
    bar.classList.add("error");
    i.style.width = (j.progress != null ? Math.max(3, j.progress * 100) : 100) + "%";
  } else if (["paused", "interrupted"].includes(j.status)) {
    bar.classList.add("paused");
    i.style.width = (j.progress || 0) * 100 + "%";
  } else if (j.progress != null && j.status === "downloading") {
    i.style.width = j.progress * 100 + "%";
  } else {
    bar.classList.add("indeterminate");
  }
  const site = j.site || (j.page_url ? U.shortUrl(j.page_url, 30).split("/")[0] : "");
  li.append(
    thumb,
    h("div", { class: "item-main" },
      h("div", { class: "item-title", title: j.title || j.url }, j.title || U.shortUrl(j.url)),
      h("div", { class: "item-sub" }, [site, U.presetLabel(j.format_id ? "format " + j.format_id : j.preset)].filter(Boolean).join(" · ")),
      bar,
      h("div", { class: "job-status" }, jobStatusLine(j)),
      h("div", { class: "job-actions" }, jobActions(j))
    )
  );
  return li;
}

function renderJobs() {
  const list = $("#jobList");
  const all = [...state.jobs.values()].sort((a, b) => (b.created || 0) - (a.created || 0));
  list.replaceChildren(...all.map(jobRow));
  $("#jobsEmpty").hidden = all.length > 0;
  updateJobCount();
}

function updateJobCount() {
  const active = [...state.jobs.values()].filter((j) => ACTIVE.includes(j.status)).length;
  $("#jobCount").textContent = active ? String(active) : "";
}

function updateJob(job) {
  state.jobs.set(job.id, job);
  const existing = document.querySelector(`#jobList li[data-id="${CSS.escape(job.id)}"]`);
  if (existing) existing.replaceWith(jobRow(job));
  else renderJobs();
  updateJobCount();
}

// ---------------------------------------------------------------- démarrage
async function init() {
  const params = new URLSearchParams(location.search);
  if (params.get("tabId")) {
    state.tabId = Number(params.get("tabId"));
    state.tab = await api.tabs.get(state.tabId).catch(() => null);
  } else {
    const [tab] = await api.tabs.query({ active: true, currentWindow: true });
    state.tab = tab || null;
    state.tabId = tab ? tab.id : null;
  }

  const port = api.runtime.connect({ name: "popup" });
  port.onMessage.addListener((msg) => {
    if (msg.type === "host") {
      state.host = msg;
      renderHost();
      renderPage();
      renderMedia();
    } else if (msg.type === "media" && msg.tabId === state.tabId) {
      state.media = msg;
      renderPage();
      renderMedia();
    } else if (msg.type === "job") {
      updateJob(msg.job);
    } else if (msg.type === "jobs") {
      state.jobs = new Map(msg.jobs.map((j) => [j.id, j]));
      renderJobs();
    }
  });

  const s = await call("get-state", { tabId: state.tabId });
  state.host = s.host;
  state.media = s.tab;
  state.settings = s.settings;
  state.jobs = new Map(s.jobs.map((j) => [j.id, j]));
  fillPresetSelect($("#pagePreset"), null, null, state.settings.default_preset);
  renderHost();
  renderPage();
  renderMedia();
  renderJobs();
  if (params.get("view") === "jobs" || [...state.jobs.values()].some((j) => ACTIVE.includes(j.status)) && !(s.tab && s.tab.items.length)) {
    selectTab("jobs");
  }
}

for (const b of document.querySelectorAll(".tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
$("#openOptions").addEventListener("click", () => api.runtime.openOptionsPage());
$("#hostPill").addEventListener("click", () => api.runtime.openOptionsPage());
$("#retryHost").addEventListener("click", () => call("host-connect").catch((e) => toast(e.message, true)));
$("#howInstall").addEventListener("click", () => api.tabs.create({ url: api.runtime.getURL("options/options.html#installation") }));
$("#analyze").addEventListener("click", analyze);
$("#pageDownload").addEventListener("click", downloadPage);
$("#openFolder").addEventListener("click", () => call("open-folder").catch((e) => toast(e.message, true)));
$("#clearDone").addEventListener("click", () => call("clear-finished").catch((e) => toast(e.message, true)));

init().catch((e) => toast("Initialisation impossible : " + e.message, true));
