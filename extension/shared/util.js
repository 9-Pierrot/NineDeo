/*
 * NineDeo — utilitaires partagés (service worker, scripts de contenu, popup, options).
 * Chargé sans module ES pour rester compatible avec importScripts() (Chrome)
 * et background.scripts (Firefox).
 */
(function (global) {
  "use strict";
  if (global.NineDeo) return;

  const api = global.browser && global.browser.runtime ? global.browser : global.chrome;

  const HOST_NAME = "com.ninedeo.host";

  /** Réglages par défaut. Les clés « host » sont transmises à l'hôte natif. */
  const DEFAULT_SETTINGS = {
    // --- transmis à l'hôte
    download_dir: "",
    filename_template: "%(title).180B.%(ext)s",
    default_preset: "best",
    container: "mp4",
    prefer_compatible: true,
    audio_format: "mp3",
    audio_quality: "192",
    max_concurrent: 3,
    concurrent_fragments: 4,
    rate_limit_kib: 0,
    allow_playlists: false,
    embed_metadata: true,
    embed_thumbnail: false,
    embed_subtitles: false,
    subtitle_langs: "fr,en",
    use_browser_cookies: true,
    impersonate: true,
    // --- propres à l'extension
    overlay_button: true,
    notifications: true,
    min_file_kib: 300,
    history_limit: 200,
  };

  const HOST_KEYS = [
    "download_dir", "filename_template", "default_preset", "container", "prefer_compatible",
    "audio_format", "audio_quality", "max_concurrent", "concurrent_fragments", "rate_limit_kib",
    "allow_playlists", "embed_metadata", "embed_thumbnail", "embed_subtitles", "subtitle_langs",
    "use_browser_cookies", "impersonate",
  ];

  const PRESETS = [
    { id: "best", label: "Meilleure qualité" },
    { id: "h2160", label: "4K (2160p max)" },
    { id: "h1440", label: "1440p max" },
    { id: "h1080", label: "1080p max" },
    { id: "h720", label: "720p max" },
    { id: "h480", label: "480p max" },
    { id: "h360", label: "360p max" },
    { id: "audio_mp3", label: "Audio seul — MP3" },
    { id: "audio_m4a", label: "Audio seul — M4A" },
  ];

  async function getSettings() {
    try {
      const stored = await api.storage.sync.get("settings");
      return Object.assign({}, DEFAULT_SETTINGS, stored.settings || {});
    } catch (e) {
      const stored = await api.storage.local.get("settings");
      return Object.assign({}, DEFAULT_SETTINGS, stored.settings || {});
    }
  }

  async function saveSettings(patch) {
    const current = await getSettings();
    const next = Object.assign({}, current, patch);
    try {
      await api.storage.sync.set({ settings: next });
    } catch (e) {
      await api.storage.local.set({ settings: next });
    }
    return next;
  }

  function hostConfig(settings) {
    const out = {};
    for (const k of HOST_KEYS) out[k] = settings[k];
    out.download_dir = settings.download_dir || null;
    return out;
  }

  // ------------------------------------------------------------------ média
  const VIDEO_EXT = ["mp4", "m4v", "webm", "mkv", "mov", "flv", "avi", "3gp", "ogv", "wmv"];
  const AUDIO_EXT = ["mp3", "m4a", "aac", "ogg", "oga", "opus", "wav", "flac"];
  const HLS_TYPES = ["application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl", "audio/x-mpegurl"];
  const DASH_TYPES = ["application/dash+xml"];

  function urlExt(url) {
    try {
      const path = new URL(url).pathname.toLowerCase();
      const m = path.match(/\.([a-z0-9]{2,5})$/);
      return m ? m[1] : "";
    } catch (e) {
      return "";
    }
  }

  /** Fragment de flux segmenté (à ignorer : on veut le manifeste, pas les morceaux). */
  function isSegment(url, contentType) {
    const ct = (contentType || "").toLowerCase();
    const ext = urlExt(url);
    if (ct.startsWith("video/mp2t") || ct === "video/iso.segment") return true;
    if (["ts", "m4s", "m4f", "cmfv", "cmfa", "aac"].includes(ext) && !ct.startsWith("audio/mpeg")) return true;
    const lower = url.toLowerCase();
    if (/[/_.-](seg|segment|frag|fragment|chunk)[-_]?\d+/.test(lower)) return true;
    if (/\/(init|header)[-_.]?\w*\.(mp4|m4s|m4v)/.test(lower)) return true;
    if (/[?&](range|bytes|rn|rbuf)=/.test(lower) && lower.includes("googlevideo.com")) return true;
    return false;
  }

  /**
   * Classe une ressource réseau : { kind: "hls"|"dash"|"file", ext, audio } ou null.
   */
  function classify(url, contentType) {
    if (!/^https?:/i.test(url)) return null;
    const ct = (contentType || "").split(";")[0].trim().toLowerCase();
    const ext = urlExt(url);
    if (HLS_TYPES.includes(ct) || ext === "m3u8") return { kind: "hls", ext: "m3u8", audio: false };
    if (DASH_TYPES.includes(ct) || ext === "mpd") return { kind: "dash", ext: "mpd", audio: false };
    if (isSegment(url, ct)) return null;
    if (ct.startsWith("video/")) {
      const sub = ct.slice(6).replace("x-", "").replace("quicktime", "mov").replace("x-matroska", "mkv");
      return { kind: "file", ext: VIDEO_EXT.includes(ext) ? ext : sub.split("+")[0] || "mp4", audio: false };
    }
    if (ct.startsWith("audio/")) {
      return { kind: "file", ext: AUDIO_EXT.includes(ext) ? ext : ct.slice(6), audio: true };
    }
    if (VIDEO_EXT.includes(ext) && (!ct || ct === "application/octet-stream" || ct === "binary/octet-stream")) {
      return { kind: "file", ext, audio: false };
    }
    if (AUDIO_EXT.includes(ext) && (!ct || ct.includes("octet-stream"))) {
      return { kind: "file", ext, audio: true };
    }
    return null;
  }

  // --------------------------------------------------------------- formats
  function formatBytes(n) {
    if (n == null || isNaN(n)) return "";
    const units = ["o", "Ko", "Mo", "Go", "To"];
    let i = 0;
    let v = Number(n);
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i++;
    }
    return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1).replace(".", ",")} ${units[i]}`;
  }

  function formatSpeed(bps) {
    return bps ? `${formatBytes(bps)}/s` : "";
  }

  function formatDuration(sec) {
    if (sec == null || isNaN(sec)) return "";
    sec = Math.round(sec);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const pad = (x) => String(x).padStart(2, "0");
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
  }

  function formatEta(sec) {
    if (sec == null || isNaN(sec)) return "";
    if (sec < 60) return `${Math.max(1, Math.round(sec))} s`;
    if (sec < 3600) return `${Math.round(sec / 60)} min`;
    return `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} min`;
  }

  function uid() {
    if (global.crypto && global.crypto.randomUUID) return global.crypto.randomUUID().replace(/-/g, "");
    return Date.now().toString(16) + Math.random().toString(16).slice(2);
  }

  function shortUrl(url, max = 60) {
    try {
      const u = new URL(url);
      const s = u.host + u.pathname;
      return s.length > max ? s.slice(0, max / 2 - 1) + "…" + s.slice(-max / 2) : s;
    } catch (e) {
      return String(url).slice(0, max);
    }
  }

  function presetLabel(id) {
    const p = PRESETS.find((x) => x.id === id);
    return p ? p.label : id;
  }

  global.NineDeo = {
    api, HOST_NAME, DEFAULT_SETTINGS, HOST_KEYS, PRESETS,
    getSettings, saveSettings, hostConfig,
    classify, isSegment, urlExt,
    formatBytes, formatSpeed, formatDuration, formatEta, uid, shortUrl, presetLabel,
  };
})(typeof globalThis !== "undefined" ? globalThis : self);
