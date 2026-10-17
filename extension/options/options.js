/* NineDeo — page des paramètres (enregistrement automatique). */
"use strict";

const U = globalThis.NineDeo;
const { api } = U;
const { h, call } = globalThis.NineDeoDom;
const $ = (s) => document.querySelector(s);

let settings = null;
let savedTimer;

function flashSaved(text = "Enregistré ✓") {
  const el = $("#saved");
  el.textContent = text;
  el.classList.add("show");
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => el.classList.remove("show"), 1600);
}

function bind() {
  const sel = $("#defaultPreset");
  for (const p of U.PRESETS) sel.append(h("option", { value: p.id }, p.label));

  for (const el of document.querySelectorAll("[data-key]")) {
    const key = el.dataset.key;
    const v = settings[key];
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v == null ? "" : v;
    el.addEventListener("change", async () => {
      let value;
      if (el.type === "checkbox") value = el.checked;
      else if (el.type === "number") {
        value = Number(el.value);
        const min = el.min !== "" ? Number(el.min) : -Infinity;
        const max = el.max !== "" ? Number(el.max) : Infinity;
        if (!Number.isFinite(value)) value = U.DEFAULT_SETTINGS[key];
        value = Math.min(max, Math.max(min, value));
        el.value = value;
      } else value = el.value.trim();
      if (key === "filename_template" && !/%\(ext\)s/.test(value)) {
        value = (value || "%(title)s") + ".%(ext)s";
        el.value = value;
      }
      settings = await U.saveSettings({ [key]: value });
      await call("settings-changed").catch(() => {});
      flashSaved();
    });
  }
}

async function refreshHost(force) {
  const card = $("#hostCard");
  const msg = $("#hostMsg");
  card.dataset.status = "connecting";
  $("#hostStatus").textContent = "Connexion au moteur…";
  msg.textContent = "";
  try {
    const info = force ? await call("host-connect") : (await call("get-state")).host.info || (await call("host-connect"));
    card.dataset.status = "connected";
    $("#hostStatus").textContent = "Moteur connecté";
    const kv = $("#hostInfo");
    kv.replaceChildren();
    const row = (k, v, cls) => kv.append(h("dt", {}, k), h("dd", { class: cls || null }, v));
    row("Hôte", `${info.version} (${info.frozen ? "exécutable" : "Python " + info.python})`);
    row("yt-dlp", info.ytdlp);
    row("ffmpeg", info.ffmpeg ? `${info.ffmpeg_version || "?"} — ${info.ffmpeg}` : "introuvable : fusion audio/vidéo et conversions désactivées", info.ffmpeg ? null : "warn");
    row("Imitation navigateur", info.impersonation ? "disponible (curl_cffi)" : "indisponible");
    row("Dossier par défaut", info.default_download_dir);
    row("Journal", info.log_file);
    $("#downloadDir").placeholder = "Par défaut : " + info.default_download_dir;
    $("#updateYtdlp").disabled = !!info.frozen;
  } catch (e) {
    const missing = /not found|introuvable|native messaging host/i.test(e.message);
    card.dataset.status = missing ? "missing" : "error";
    $("#hostStatus").textContent = missing ? "Moteur non installé" : "Moteur injoignable";
    $("#hostInfo").replaceChildren();
    msg.textContent = missing
      ? "Lancez installer\\windows\\install.bat puis cliquez sur « Tester la connexion »."
      : e.message;
  }
}

async function init() {
  settings = await U.getSettings();
  bind();
  $("#extId").textContent = api.runtime.id;
  $("#extVersion").textContent = "v" + api.runtime.getManifest().version;

  $("#testHost").addEventListener("click", () => refreshHost(true));
  $("#updateYtdlp").addEventListener("click", async () => {
    const btn = $("#updateYtdlp");
    btn.disabled = true;
    $("#hostMsg").textContent = "Mise à jour de yt-dlp en cours (jusqu'à une minute)…";
    try {
      const r = await call("host-update");
      $("#hostMsg").textContent = r.restart_required
        ? `yt-dlp mis à jour : ${r.before} → ${r.after}. Redémarrage du moteur…`
        : `yt-dlp est déjà à jour (${r.after}).`;
      if (r.restart_required) await refreshHost(true);
    } catch (e) {
      $("#hostMsg").textContent = "Échec : " + e.message;
    } finally {
      btn.disabled = false;
    }
  });
  $("#browse").addEventListener("click", async () => {
    try {
      const path = await call("choose-folder", { initial: settings.download_dir || null });
      if (path) {
        $("#downloadDir").value = path;
        $("#downloadDir").dispatchEvent(new Event("change"));
      }
    } catch (e) {
      flashSaved("Sélecteur indisponible : saisissez le chemin");
    }
  });
  $("#openDir").addEventListener("click", () => call("open-folder").catch((e) => flashSaved(e.message)));
  $("#clearHistory").addEventListener("click", async () => {
    await call("clear-history").catch(() => {});
    flashSaved("Historique effacé");
  });
  $("#resetSettings").addEventListener("click", async () => {
    settings = await U.saveSettings(Object.assign({}, U.DEFAULT_SETTINGS));
    await call("settings-changed").catch(() => {});
    location.reload();
  });
  refreshHost(false);
}

init();
