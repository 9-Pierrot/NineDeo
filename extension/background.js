/*
 * NineDeo — service worker (Chrome/Edge) / script d'arrière-plan (Firefox).
 *
 * Rôles :
 *   1. Détecter les médias de chaque onglet (trafic réseau + DOM via le script de contenu).
 *   2. Analyser les manifestes HLS/DASH (qualités, durée, DRM).
 *   3. Piloter l'hôte natif (yt-dlp + ffmpeg) via Native Messaging.
 *   4. Tenir l'état des téléchargements et l'historique, notifier la popup.
 */
"use strict";

if (typeof importScripts === "function" && !self.NineDeo) {
  importScripts("shared/util.js");
}

const { api, HOST_NAME } = self.NineDeo;
const U = self.NineDeo;

// ===========================================================================
// État
// ===========================================================================
/** @type {Map<number, TabState>} */
const tabs = new Map();
/** @type {Map<string, JobRecord>} jobId -> { spec, state } */
const jobs = new Map();
const popupPorts = new Set();
let settings = Object.assign({}, U.DEFAULT_SETTINGS);

const host = {
  port: null,
  status: "disconnected", // disconnected | connecting | connected | missing | error
  info: null,
  error: null,
  pending: new Map(),
  connecting: null,
  idleTimer: null,
};

function newTabState(url, title) {
  return { pageUrl: url || "", title: title || "", items: {}, drm: null, frames: [], updated: Date.now() };
}

function tabState(tabId) {
  let t = tabs.get(tabId);
  if (!t) {
    t = newTabState();
    tabs.set(tabId, t);
  }
  return t;
}

// --------------------------------------------------------- persistance
const ready = (async () => {
  try {
    settings = await U.getSettings();
  } catch (e) {
    /* valeurs par défaut */
  }
  try {
    const s = await api.storage.session.get("tabs");
    for (const [id, t] of Object.entries(s.tabs || {})) tabs.set(Number(id), t);
  } catch (e) {
    /* storage.session indisponible */
  }
  try {
    const l = await api.storage.local.get("jobs");
    for (const rec of l.jobs || []) {
      // Un téléchargement « actif » au moment où le navigateur s'est fermé
      // est marqué interrompu : l'utilisateur peut le reprendre.
      if (["queued", "starting", "downloading", "processing"].includes(rec.state.status)) {
        rec.state.status = "interrupted";
        rec.state.speed = rec.state.eta = null;
      }
      jobs.set(rec.spec.id, rec);
    }
  } catch (e) {
    /* rien */
  }
})();

const debounced = {};
function debounce(key, fn, ms) {
  clearTimeout(debounced[key]);
  debounced[key] = setTimeout(fn, ms);
}

function persistTabs() {
  debounce("tabs", () => {
    const obj = {};
    for (const [id, t] of tabs) obj[id] = t;
    api.storage.session.set({ tabs: obj }).catch(() => {});
  }, 400);
}

function persistJobs() {
  debounce("jobs", () => {
    const list = [...jobs.values()].sort((a, b) => (b.state.created || 0) - (a.state.created || 0));
    const limited = list.slice(0, settings.history_limit || 200);
    for (const rec of list.slice(limited.length)) jobs.delete(rec.spec.id);
    api.storage.local.set({ jobs: limited }).catch(() => {});
  }, 500);
}

// ===========================================================================
// Communication avec la popup / les options
// ===========================================================================
function broadcast(msg) {
  for (const port of popupPorts) {
    try {
      port.postMessage(msg);
    } catch (e) {
      popupPorts.delete(port);
    }
  }
}

function hostSnapshot() {
  return { status: host.status, info: host.info, error: host.error };
}

function jobList() {
  return [...jobs.values()]
    .map((r) => r.state)
    .sort((a, b) => (b.created || 0) - (a.created || 0));
}

function visibleItems(t) {
  return Object.values(t.items)
    .filter((it) => !it.parent)
    .sort((a, b) => scoreItem(b) - scoreItem(a));
}

function scoreItem(it) {
  let s = 0;
  if (it.kind === "hls") s += it.master ? 400 : 250;
  if (it.kind === "dash") s += 350;
  if (it.kind === "file") s += it.audio ? 50 : 200;
  if (it.height) s += Math.min(it.height, 2160) / 20;
  if (it.size) s += Math.min(60, Math.log10(it.size) * 6);
  if (it.drm) s -= 1000;
  return s;
}

function tabPayload(tabId) {
  const t = tabs.get(tabId) || newTabState();
  return { tabId, pageUrl: t.pageUrl, title: t.title, drm: t.drm, frames: t.frames, items: visibleItems(t) };
}

function notifyTab(tabId) {
  updateBadge(tabId);
  persistTabs();
  debounce("tab" + tabId, () => broadcast({ type: "media", ...tabPayload(tabId) }), 150);
}

function updateBadge(tabId) {
  const t = tabs.get(tabId);
  const n = t ? visibleItems(t).filter((i) => !i.drm).length : 0;
  api.action.setBadgeText({ tabId, text: n ? String(n) : "" }).catch(() => {});
  api.action.setBadgeBackgroundColor({ tabId, color: t && t.drm && t.drm.active && !n ? "#FF3333" : "#0029FF" }).catch(() => {});
  if (api.action.setBadgeTextColor) api.action.setBadgeTextColor({ tabId, color: "#FFFFFF" }).catch(() => {});
}

// ===========================================================================
// Détection réseau
// ===========================================================================
function headerValue(headers, name) {
  const h = (headers || []).find((x) => x.name.toLowerCase() === name);
  return h ? h.value : "";
}

function itemKey(url, kind) {
  try {
    const u = new URL(url);
    u.hash = "";
    if (kind === "file") {
      for (const p of ["range", "bytes", "start", "end", "_", "t"]) u.searchParams.delete(p);
    }
    return u.toString();
  } catch (e) {
    return url;
  }
}

function addItem(tabId, data) {
  const t = tabState(tabId);
  const key = itemKey(data.url, data.kind);
  const existing = t.items[key];
  if (existing) {
    // Complète les informations (taille, poster, titre DOM…)
    for (const [k, v] of Object.entries(data)) {
      if (v != null && v !== "" && (existing[k] == null || existing[k] === "")) existing[k] = v;
    }
    if (data.size && (!existing.size || data.size > existing.size)) existing.size = data.size;
    notifyTab(tabId);
    return existing;
  }
  const item = Object.assign(
    { id: U.uid(), key, time: Date.now(), size: null, height: null, variants: null, drm: false, parent: null },
    data
  );
  t.items[key] = item;
  // Cette ressource est-elle une variante d'un manifeste maître déjà connu ?
  for (const other of Object.values(t.items)) {
    if (other.master && other.variants && other.variants.some((v) => v.url === item.url)) {
      item.parent = other.key;
    }
  }
  if (item.kind === "hls" || item.kind === "dash") analyzeManifest(tabId, item);
  notifyTab(tabId);
  return item;
}

api.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (details.tabId < 0) return;
    if (details.statusCode && (details.statusCode < 200 || details.statusCode >= 300)) return;
    const ctype = headerValue(details.responseHeaders, "content-type");
    const cls = U.classify(details.url, ctype);
    if (!cls) return;
    let size = null;
    if (cls.kind === "file") {
      // Pour HLS/DASH, la taille serait celle du manifeste : sans intérêt.
      size = Number(headerValue(details.responseHeaders, "content-length")) || null;
      const range = headerValue(details.responseHeaders, "content-range");
      const m = range && range.match(/\/(\d+)\s*$/);
      if (m) size = Number(m[1]);
    }
    ready.then(() => {
      if (cls.kind === "file" && size && size < (settings.min_file_kib || 0) * 1024) return;
      const t = tabState(details.tabId);
      addItem(details.tabId, {
        url: details.url,
        kind: cls.kind,
        ext: cls.ext,
        audio: cls.audio,
        mime: ctype.split(";")[0] || null,
        size,
        source: "network",
        referer: details.documentUrl || details.initiator || t.pageUrl || null,
        frameUrl: details.frameId ? details.documentUrl || null : null,
      });
    });
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

// --------------------------------------------------- analyse des manifestes
async function fetchText(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  try {
    const r = await fetch(url, { credentials: "include", signal: ctrl.signal, cache: "force-cache" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const text = await r.text();
    return text.slice(0, 2_000_000);
  } finally {
    clearTimeout(timer);
  }
}

function parseAttrs(line) {
  const out = {};
  const re = /([A-Z0-9-]+)=("([^"]*)"|[^,]*)/g;
  let m;
  while ((m = re.exec(line))) out[m[1]] = m[3] !== undefined ? m[3] : m[2];
  return out;
}

const DRM_HLS = /#EXT-X-(SESSION-)?KEY:[^\n]*(METHOD=SAMPLE-AES(-CTR|-CENC)?|KEYFORMAT="(com\.apple\.streamingkeydelivery|urn:uuid:[0-9a-f-]+|com\.microsoft\.playready)")/i;

function analyzeHls(text, baseUrl) {
  const res = { master: false, variants: null, duration: null, height: null, drm: DRM_HLS.test(text), live: false };
  if (!text.startsWith("#EXTM3U")) return res;
  const lines = text.split(/\r?\n/);
  if (text.includes("#EXT-X-STREAM-INF")) {
    res.master = true;
    res.variants = [];
    for (let i = 0; i < lines.length; i++) {
      if (!lines[i].startsWith("#EXT-X-STREAM-INF")) continue;
      const a = parseAttrs(lines[i].slice(18));
      let j = i + 1;
      while (j < lines.length && (!lines[j] || lines[j].startsWith("#"))) j++;
      if (j >= lines.length) break;
      const res2 = (a.RESOLUTION || "").split("x");
      res.variants.push({
        url: new URL(lines[j].trim(), baseUrl).toString(),
        bandwidth: Number(a.BANDWIDTH) || null,
        width: Number(res2[0]) || null,
        height: Number(res2[1]) || null,
        codecs: a.CODECS || null,
      });
    }
    res.variants.sort((x, y) => (y.height || 0) - (x.height || 0) || (y.bandwidth || 0) - (x.bandwidth || 0));
    res.height = res.variants.length ? res.variants[0].height : null;
  } else {
    let d = 0;
    for (const l of lines) if (l.startsWith("#EXTINF:")) d += parseFloat(l.slice(8)) || 0;
    res.duration = d || null;
    res.live = !text.includes("#EXT-X-ENDLIST");
  }
  return res;
}

function analyzeDash(text) {
  const res = { drm: /<ContentProtection[^>]*(schemeIdUri="urn:uuid|cenc:default_KID|value="cenc")/i.test(text), heights: [], duration: null, live: /type="dynamic"/.test(text) };
  const hs = new Set();
  const re = /<Representation\b[^>]*\bheight="(\d+)"/g;
  let m;
  while ((m = re.exec(text))) hs.add(Number(m[1]));
  res.heights = [...hs].sort((a, b) => b - a);
  const dur = text.match(/mediaPresentationDuration="PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?"/);
  if (dur) res.duration = (Number(dur[1]) || 0) * 3600 + (Number(dur[2]) || 0) * 60 + (Number(dur[3]) || 0);
  return res;
}

async function analyzeManifest(tabId, item) {
  try {
    const text = await fetchText(item.url);
    const t = tabs.get(tabId);
    if (!t || !t.items[item.key]) return;
    if (item.kind === "hls") {
      const r = analyzeHls(text, item.url);
      Object.assign(item, { master: r.master, variants: r.variants, duration: r.duration, height: r.height, drm: r.drm, live: r.live });
      if (r.master) {
        for (const other of Object.values(t.items)) {
          if (other !== item && r.variants.some((v) => v.url === other.url)) other.parent = item.key;
        }
      }
    } else {
      const r = analyzeDash(text);
      Object.assign(item, { drm: r.drm, heights: r.heights, height: r.heights[0] || null, duration: r.duration, live: r.live, master: true });
    }
    item.analyzed = true;
  } catch (e) {
    item.analyzed = false;
    item.analyzeError = String(e && e.message ? e.message : e);
  }
  notifyTab(tabId);
}

// --------------------------------------------------- cycle de vie des onglets
api.webNavigation.onCommitted.addListener((d) => {
  if (d.frameId !== 0) return;
  ready.then(() => {
    tabs.set(d.tabId, newTabState(d.url));
    notifyTab(d.tabId);
  });
});

api.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (info.title || info.url) {
    ready.then(() => {
      const t = tabState(tabId);
      if (tab.url) t.pageUrl = tab.url;
      if (tab.title) t.title = tab.title;
      persistTabs();
    });
  }
});

api.tabs.onRemoved.addListener((tabId) => {
  tabs.delete(tabId);
  persistTabs();
});

// ===========================================================================
// Hôte natif
// ===========================================================================
function hostError(err) {
  const msg = String((err && err.message) || err || "");
  if (/not found|introuvable|specified native messaging host/i.test(msg)) return { code: "missing", message: msg };
  if (/forbidden|not allowed/i.test(msg)) return { code: "forbidden", message: msg };
  if (/exited|disconnected/i.test(msg)) return { code: "exited", message: msg };
  return { code: "error", message: msg };
}

function setHostStatus(status, error) {
  host.status = status;
  host.error = error || null;
  broadcast({ type: "host", ...hostSnapshot() });
}

function connectHost() {
  if (host.port) return Promise.resolve(host.info);
  if (host.connecting) return host.connecting;
  host.connecting = new Promise((resolve, reject) => {
    let port;
    setHostStatus("connecting");
    try {
      port = api.runtime.connectNative(HOST_NAME);
    } catch (e) {
      host.connecting = null;
      setHostStatus("missing", hostError(e));
      reject(e);
      return;
    }
    host.port = port;
    port.onMessage.addListener(onHostMessage);
    port.onDisconnect.addListener(() => {
      const err = (api.runtime.lastError && api.runtime.lastError.message) || (port.error && port.error.message) || "";
      host.port = null;
      host.connecting = null;
      for (const [, p] of host.pending) {
        clearTimeout(p.timer);
        p.reject(Object.assign(new Error(err || "Hôte déconnecté"), { code: "disconnected" }));
      }
      host.pending.clear();
      const e = hostError(err);
      if (err && e.code === "missing") setHostStatus("missing", e);
      else if (err) setHostStatus("error", e);
      else setHostStatus("disconnected");
      // Les téléchargements actifs sont perdus avec l'hôte : on les marque.
      let changed = false;
      for (const rec of jobs.values()) {
        if (["queued", "starting", "downloading", "processing"].includes(rec.state.status)) {
          rec.state.status = "interrupted";
          rec.state.speed = rec.state.eta = null;
          changed = true;
          broadcast({ type: "job", job: rec.state });
        }
      }
      if (changed) persistJobs();
    });
    rawRequest("hello", { config: U.hostConfig(settings) }, 20000)
      .then((info) => {
        host.info = info;
        host.connecting = null;
        setHostStatus("connected");
        reconcileJobs(info.jobs || []);
        scheduleIdle();
        resolve(info);
      })
      .catch((e) => {
        host.connecting = null;
        if (host.status === "connecting") setHostStatus("error", hostError(e));
        reject(e);
      });
  });
  return host.connecting;
}

function onHostMessage(msg) {
  if (msg && msg.id && host.pending.has(msg.id)) {
    const p = host.pending.get(msg.id);
    host.pending.delete(msg.id);
    clearTimeout(p.timer);
    if (msg.ok) p.resolve(msg.data);
    else p.reject(Object.assign(new Error(msg.error.message), msg.error));
    return;
  }
  if (msg && msg.event === "job") onJobUpdate(msg.job);
}

function rawRequest(cmd, payload, timeout = 60000) {
  return new Promise((resolve, reject) => {
    if (!host.port) {
      reject(Object.assign(new Error("Hôte non connecté"), { code: "disconnected" }));
      return;
    }
    const id = U.uid();
    const timer = setTimeout(() => {
      host.pending.delete(id);
      reject(Object.assign(new Error("Délai dépassé (" + cmd + ")"), { code: "timeout" }));
    }, timeout);
    host.pending.set(id, { resolve, reject, timer });
    try {
      host.port.postMessage(Object.assign({ id, cmd }, payload || {}));
    } catch (e) {
      clearTimeout(timer);
      host.pending.delete(id);
      reject(e);
    }
  });
}

async function hostRequest(cmd, payload, timeout) {
  await connectHost();
  scheduleIdle();
  return rawRequest(cmd, payload, timeout);
}

/** Ferme le port après 2 min d'inactivité pour laisser le service worker s'endormir. */
function scheduleIdle() {
  clearTimeout(host.idleTimer);
  host.idleTimer = setTimeout(() => {
    const active = [...jobs.values()].some((r) => ["queued", "starting", "downloading", "processing"].includes(r.state.status));
    if (active || host.pending.size || popupPorts.size) return scheduleIdle();
    if (host.port) {
      host.port.disconnect();
      host.port = null;
      setHostStatus("disconnected");
    }
  }, 120000);
}

function reconcileJobs(hostJobs) {
  const known = new Set(hostJobs.map((j) => j.id));
  for (const j of hostJobs) onJobUpdate(j, true);
  for (const rec of jobs.values()) {
    if (!known.has(rec.spec.id) && ["queued", "starting", "downloading", "processing"].includes(rec.state.status)) {
      rec.state.status = "interrupted";
    }
  }
  persistJobs();
}

// ===========================================================================
// Téléchargements
// ===========================================================================
const ACTIVE = ["queued", "starting", "downloading", "processing"];

function onJobUpdate(state, silent) {
  const rec = jobs.get(state.id);
  if (!rec) return; // tâche supprimée côté extension
  const before = rec.state.status;
  rec.state = Object.assign({}, rec.state, state);
  persistJobs();
  broadcast({ type: "job", job: rec.state });
  if (!silent && before !== state.status) {
    if (state.status === "finished") notifyDone(rec.state);
    if (state.status === "error") notifyError(rec.state);
    updateActionTitle();
  }
}

function updateActionTitle() {
  const n = [...jobs.values()].filter((r) => ACTIVE.includes(r.state.status)).length;
  api.action.setTitle({ title: n ? `NineDeo — ${n} téléchargement(s) en cours` : "NineDeo" }).catch(() => {});
}

function notifyDone(job) {
  if (!settings.notifications) return;
  api.notifications.create("done:" + job.id, {
    type: "basic",
    iconUrl: api.runtime.getURL("icons/icon-128.png"),
    title: "Téléchargement terminé",
    message: job.filename || job.title || "Vidéo",
    contextMessage: "Cliquez pour ouvrir le fichier",
  }).catch(() => {});
}

function notifyError(job) {
  if (!settings.notifications) return;
  api.notifications.create("err:" + job.id, {
    type: "basic",
    iconUrl: api.runtime.getURL("icons/icon-128.png"),
    title: "Échec du téléchargement",
    message: (job.error && job.error.message) || "Erreur inconnue",
    contextMessage: job.title || "",
  }).catch(() => {});
}

api.notifications.onClicked.addListener((nid) => {
  const [kind, id] = nid.split(":");
  const rec = jobs.get(id);
  api.notifications.clear(nid);
  if (kind === "done" && rec && rec.state.filepath) hostRequest("open", { path: rec.state.filepath }).catch(() => {});
  if (kind === "err") api.action.openPopup && api.action.openPopup().catch(() => {});
});

async function collectCookies(urls) {
  if (!settings.use_browser_cookies || !api.cookies) return [];
  const out = [];
  const seen = new Set();
  for (const url of new Set(urls.filter((u) => /^https?:/.test(u || "")))) {
    let list = [];
    try {
      list = await api.cookies.getAll({ url });
    } catch (e) {
      continue;
    }
    for (const c of list) {
      const k = `${c.domain}|${c.path}|${c.name}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({
        name: c.name, value: c.value, domain: c.domain, hostOnly: c.hostOnly, path: c.path,
        secure: c.secure, session: c.session, expirationDate: c.expirationDate,
      });
    }
  }
  return out;
}

function cleanTitle(title, pageUrl) {
  let t = (title || "").replace(/\s+/g, " ").trim();
  // Retire les suffixes de site courants : « Titre - MonSite », « Titre | MonSite »
  t = t.replace(/\s[|–—-]\s[^|–—-]{2,40}$/, "").trim();
  if (!t) {
    try {
      t = new URL(pageUrl).hostname;
    } catch (e) {
      t = "video";
    }
  }
  return t.slice(0, 150);
}

function mediaHeaders(item, pageUrl) {
  const referer = item.referer || pageUrl || "";
  const h = { "User-Agent": navigator.userAgent };
  if (/^https?:/.test(referer)) {
    h.Referer = referer;
    try {
      h.Origin = new URL(referer).origin;
    } catch (e) {
      /* ignore */
    }
  }
  return h;
}

/**
 * Construit la spécification d'une tâche et l'envoie à l'hôte.
 * source = { type: "page" } | { type: "item", key } | { type: "url", url, mediaHint }
 */
async function startDownload({ tabId, source, preset, format_id, title, thumbnail }) {
  await ready;
  const t = tabs.get(tabId) || newTabState();
  let tab = null;
  try {
    tab = tabId != null ? await api.tabs.get(tabId) : null;
  } catch (e) {
    /* onglet fermé */
  }
  const pageUrl = (tab && tab.url) || t.pageUrl;
  const pageTitle = cleanTitle(title || (tab && tab.title) || t.title, pageUrl);
  preset = preset || settings.default_preset || "best";

  const streams = visibleItems(t).filter((i) => !i.drm);
  const streamToCand = (it) => ({
    url: it.url,
    kind: "media",
    title: pageTitle + (it.audio ? " (audio)" : ""),
    headers: mediaHeaders(it, pageUrl),
  });

  let spec;
  if (source.type === "item") {
    const it = t.items[source.key];
    if (!it) throw new Error("Flux introuvable (la page a peut-être été rechargée).");
    if (it.drm) throw Object.assign(new Error("Flux protégé par DRM : téléchargement impossible."), { code: "drm" });
    let suffix = "";
    if (preset.startsWith("h") && (it.variants || it.heights)) suffix = ` (${preset.slice(1)}p)`;
    spec = {
      url: it.url,
      kind: "media",
      title: pageTitle + suffix + (it.audio ? " (audio)" : ""),
      headers: mediaHeaders(it, pageUrl),
      thumbnail: it.poster || null,
    };
  } else if (source.type === "url") {
    const isMedia = !!U.classify(source.url, "") || source.mediaHint;
    spec = {
      url: source.url,
      kind: isMedia ? "media" : "page",
      title: isMedia ? pageTitle : null,
      headers: isMedia ? mediaHeaders({ referer: pageUrl }, pageUrl) : {},
      fallbacks: isMedia ? [] : streams.slice(0, 3).map(streamToCand),
    };
    if (isMedia && pageUrl && /^https?:/.test(pageUrl)) {
      // Lien direct : on tente aussi la page si le fichier refuse l'accès.
      spec.fallbacks = [{ url: pageUrl, kind: "page" }];
    }
  } else {
    // Page entière : yt-dlp (1800+ sites + extracteur générique), puis
    // replis sur les lecteurs intégrés (iframes) et les flux détectés.
    const fallbacks = [];
    for (const f of t.frames || []) {
      if (f && f !== pageUrl) fallbacks.push({ url: f, kind: "page" });
    }
    for (const s of streams.slice(0, 3)) fallbacks.push(streamToCand(s));
    spec = { url: pageUrl, kind: "page", title: null, headers: {}, fallbacks };
    if (!/^https?:/.test(pageUrl || "")) throw new Error("Cette page ne peut pas être analysée.");
  }

  const cookieUrls = [pageUrl, spec.url, ...(spec.fallbacks || []).map((f) => f.url)];
  spec.cookies = await collectCookies(cookieUrls);
  spec.id = U.uid();
  spec.preset = preset;
  spec.format_id = format_id || null;
  spec.page_url = pageUrl;
  spec.thumbnail = spec.thumbnail || thumbnail || firstPoster(t) || null;

  const state = {
    id: spec.id, url: spec.url, kind: spec.kind, title: spec.title || pageTitle, thumbnail: spec.thumbnail,
    page_url: pageUrl, preset, format_id: spec.format_id, status: "queued", progress: null,
    created: Date.now() / 1000, site: hostOf(pageUrl),
  };
  jobs.set(spec.id, { spec: stripSecrets(spec), state });
  broadcast({ type: "job", job: state });
  persistJobs();
  try {
    const res = await hostRequest("download", { job: spec });
    onJobUpdate(res, true);
  } catch (e) {
    state.status = "error";
    state.error = { code: e.code || "host", message: hostErrorMessage(e) };
    broadcast({ type: "job", job: state });
    persistJobs();
    throw e;
  }
  return state;
}

function firstPoster(t) {
  const it = Object.values(t.items).find((i) => i.poster);
  return it ? it.poster : null;
}

function hostOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (e) {
    return "";
  }
}

/** Les cookies ne sont jamais conservés dans l'historique (stockage local). */
function stripSecrets(spec) {
  const copy = Object.assign({}, spec);
  delete copy.cookies;
  return copy;
}

function hostErrorMessage(e) {
  if (host.status === "missing" || (e && e.code === "disconnected" && host.status === "missing")) {
    return "Le moteur NineDeo n'est pas installé : lancez l'installeur (voir la notice).";
  }
  return (e && e.message) || "Erreur inconnue";
}

async function resumeJob(id) {
  const rec = jobs.get(id);
  if (!rec) throw new Error("Tâche inconnue");
  try {
    const res = await hostRequest("resume", { job_id: id });
    onJobUpdate(res, true);
  } catch (e) {
    if (e.code !== "not_found") throw e;
    // L'hôte a redémarré : on renvoie la spécification (yt-dlp reprend le .part).
    const spec = Object.assign({}, rec.spec);
    if (spec.kind === "media" && rec.state.filename) {
      spec.title = rec.state.output_base || spec.title;
      spec.reuse_name = true;
    }
    spec.cookies = await collectCookies([spec.page_url, spec.url, ...(spec.fallbacks || []).map((f) => f.url)]);
    rec.state.status = "queued";
    rec.state.error = null;
    broadcast({ type: "job", job: rec.state });
    const res = await hostRequest("download", { job: spec });
    onJobUpdate(res, true);
  }
}

async function jobAction(id, action) {
  const rec = jobs.get(id);
  if (!rec) throw new Error("Tâche inconnue");
  switch (action) {
    case "pause":
    case "cancel": {
      if (["interrupted"].includes(rec.state.status) || host.status !== "connected") {
        rec.state.status = action === "pause" ? "paused" : "canceled";
        broadcast({ type: "job", job: rec.state });
        persistJobs();
        if (host.status !== "connected") return;
      }
      const res = await hostRequest(action, { job_id: id });
      onJobUpdate(res, true);
      return;
    }
    case "resume":
    case "retry":
      return resumeJob(id);
    case "remove":
      if (ACTIVE.includes(rec.state.status)) await hostRequest("cancel", { job_id: id }).catch(() => {});
      hostRequest("remove", { job_id: id }).catch(() => {});
      jobs.delete(id);
      persistJobs();
      broadcast({ type: "jobs", jobs: jobList() });
      return;
    case "open":
      return hostRequest("open", { path: rec.state.filepath });
    case "reveal":
      return hostRequest("reveal", { path: rec.state.filepath });
    case "delete":
      await hostRequest("delete_file", { path: rec.state.filepath });
      jobs.delete(id);
      persistJobs();
      broadcast({ type: "jobs", jobs: jobList() });
      return;
    default:
      throw new Error("Action inconnue : " + action);
  }
}

// ===========================================================================
// Messages (popup, options, scripts de contenu)
// ===========================================================================
api.runtime.onConnect.addListener((port) => {
  if (port.name !== "popup") return;
  popupPorts.add(port);
  port.onDisconnect.addListener(() => popupPorts.delete(port));
});

const handlers = {
  async "get-state"(msg) {
    await ready;
    if (host.status !== "connected" && !host.connecting) connectHost().catch(() => {});
    return { host: hostSnapshot(), tab: msg.tabId != null ? tabPayload(msg.tabId) : null, jobs: jobList(), settings };
  },
  async probe(msg) {
    const t = tabs.get(msg.tabId) || newTabState();
    const url = msg.url || t.pageUrl;
    const cookies = await collectCookies([url]);
    return hostRequest("probe", { url, cookies }, 90000);
  },
  download: (msg) => startDownload(msg),
  "job-action": (msg) => jobAction(msg.id, msg.action),
  async "clear-finished"() {
    for (const [id, rec] of jobs) {
      if (["finished", "canceled", "error"].includes(rec.state.status)) {
        jobs.delete(id);
        hostRequest("remove", { job_id: id }).catch(() => {});
      }
    }
    persistJobs();
    broadcast({ type: "jobs", jobs: jobList() });
    return true;
  },
  async "clear-history"() {
    for (const [id, rec] of jobs) {
      if (!ACTIVE.includes(rec.state.status)) {
        jobs.delete(id);
        hostRequest("remove", { job_id: id }).catch(() => {});
      }
    }
    persistJobs();
    broadcast({ type: "jobs", jobs: jobList() });
    return true;
  },
  async "host-connect"() {
    if (host.port) {
      host.port.disconnect();
      host.port = null;
    }
    host.connecting = null;
    return connectHost();
  },
  "host-update": () => hostRequest("update_ytdlp", {}, 600000),
  "choose-folder": (msg) => hostRequest("choose_folder", { initial: msg.initial || null }, 600000),
  "open-folder": () => hostRequest("open", { folder: true }),
  async "settings-changed"() {
    settings = await U.getSettings();
    if (host.port) await rawRequest("configure", { config: U.hostConfig(settings) });
    for (const id of tabs.keys()) updateBadge(id);
    return settings;
  },
  // ---- scripts de contenu
  async "dom-media"(msg, sender) {
    await ready;
    const tabId = sender.tab && sender.tab.id;
    if (tabId == null) return false;
    const t = tabState(tabId);
    if (sender.frameId === 0 && msg.pageTitle) t.title = msg.pageTitle;
    if (sender.frameId !== 0 && msg.hasVideo && msg.frameUrl && !t.frames.includes(msg.frameUrl)) {
      t.frames.push(msg.frameUrl);
    }
    for (const m of msg.items || []) {
      const cls = U.classify(m.url, m.type || "") || (m.tag === "video" ? { kind: "file", ext: U.urlExt(m.url) || "mp4", audio: false } : m.tag === "audio" ? { kind: "file", ext: U.urlExt(m.url) || "mp3", audio: true } : null);
      if (!cls) continue;
      addItem(tabId, {
        url: m.url, kind: cls.kind, ext: cls.ext, audio: cls.audio, source: "dom",
        poster: m.poster || null, duration: m.duration || null, height: m.height || null,
        referer: msg.frameUrl || t.pageUrl,
      });
    }
    notifyTab(tabId);
    return true;
  },
  async drm(msg, sender) {
    await ready;
    const tabId = sender.tab && sender.tab.id;
    if (tabId == null) return false;
    const t = tabState(tabId);
    const prev = t.drm || {};
    t.drm = { keySystem: msg.keySystem || prev.keySystem || null, active: !!(msg.active || prev.active) };
    notifyTab(tabId);
    return true;
  },
  async "quick-download"(msg, sender) {
    const tabId = sender.tab ? sender.tab.id : msg.tabId;
    const src = msg.src && /^https?:/.test(msg.src) ? msg.src : null;
    const source = src && msg.preferSource ? { type: "url", url: src, mediaHint: true } : { type: "page" };
    return startDownload({ tabId, source, preset: settings.default_preset, thumbnail: msg.poster });
  },
  async "overlay-settings"() {
    await ready;
    return { enabled: !!settings.overlay_button };
  },
};

api.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const fn = msg && handlers[msg.type];
  if (!fn) return false;
  Promise.resolve()
    .then(() => fn(msg, sender))
    .then((data) => sendResponse({ ok: true, data }))
    .catch((e) => sendResponse({ ok: false, error: { code: e.code || "error", message: hostErrorMessage(e), detail: e.detail || "" } }));
  return true;
});

api.storage.onChanged.addListener((changes, area) => {
  if (changes.settings && (area === "sync" || area === "local")) {
    settings = Object.assign({}, U.DEFAULT_SETTINGS, changes.settings.newValue || {});
    if (host.port) rawRequest("configure", { config: U.hostConfig(settings) }).catch(() => {});
  }
});

// ===========================================================================
// Menus contextuels et raccourcis
// ===========================================================================
function createMenus() {
  api.contextMenus.removeAll(() => {
    api.contextMenus.create({ id: "nd-media", title: "Télécharger cette vidéo avec NineDeo", contexts: ["video", "audio"] });
    api.contextMenus.create({ id: "nd-link", title: "Télécharger le lien avec NineDeo", contexts: ["link"] });
    api.contextMenus.create({ id: "nd-page", title: "Télécharger la vidéo de cette page", contexts: ["page", "frame"] });
  });
}

api.runtime.onInstalled.addListener((details) => {
  createMenus();
  if (details.reason === "install") api.runtime.openOptionsPage();
});
api.runtime.onStartup && api.runtime.onStartup.addListener(createMenus);

api.contextMenus.onClicked.addListener((info, tab) => {
  const tabId = tab && tab.id;
  let source = { type: "page" };
  if (info.menuItemId === "nd-media" && info.srcUrl && /^https?:/.test(info.srcUrl)) {
    source = { type: "url", url: info.srcUrl, mediaHint: true };
  } else if (info.menuItemId === "nd-link" && info.linkUrl) {
    source = { type: "url", url: info.linkUrl };
  } else if (info.menuItemId === "nd-page" && info.frameUrl && info.frameUrl !== (tab && tab.url)) {
    source = { type: "url", url: info.frameUrl };
  }
  startDownload({ tabId, source }).catch((e) => notifyError({ error: { message: hostErrorMessage(e) }, title: "" }));
});

api.commands.onCommand.addListener(async (command) => {
  if (command !== "quick-download") return;
  const [tab] = await api.tabs.query({ active: true, currentWindow: true });
  if (tab) startDownload({ tabId: tab.id, source: { type: "page" } }).catch((e) => notifyError({ error: { message: hostErrorMessage(e) }, title: "" }));
});

// Exposé pour les tests automatisés (console du service worker).
self.__ninedeo = { tabs, jobs, host, startDownload, analyzeHls, analyzeDash };
