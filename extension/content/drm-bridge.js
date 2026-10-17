/*
 * NineDeo — passerelle DRM (monde isolé, document_start).
 * Relaie au service worker les signaux EME émis par content/page-hook.js.
 * Chargée dès le début du document pour ne rater aucun appel fait pendant
 * l'analyse de la page (les lecteurs initialisent souvent EME très tôt).
 */
(function () {
  "use strict";
  if (window.__ninedeoDrmBridge) return;
  window.__ninedeoDrmBridge = true;
  const api = globalThis.browser && globalThis.browser.runtime ? globalThis.browser : globalThis.chrome;
  let last = "";
  window.addEventListener("message", (ev) => {
    if (ev.source !== window || !ev.data || ev.data.__ninedeo_drm__ !== true) return;
    const key = `${ev.data.keySystem}|${ev.data.active}`;
    if (key === last) return;
    last = key;
    try {
      const p = api.runtime.sendMessage({ type: "drm", keySystem: ev.data.keySystem || null, active: !!ev.data.active });
      if (p && p.catch) p.catch(() => {});
    } catch (e) {
      /* contexte invalidé (extension rechargée) */
    }
  });
})();
