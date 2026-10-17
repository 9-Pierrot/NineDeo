/* NineDeo — petits utilitaires DOM pour la popup et les options (sans framework). */
(function (global) {
  "use strict";

  /** Crée un élément. Les enfants texte passent par textContent (aucune injection HTML). */
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "html") el.innerHTML = v; // réservé aux icônes SVG statiques ci-dessous
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  const P = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
  const ICONS = {
    download: P("M12 3a1 1 0 0 1 1 1v9.59l3.3-3.3a1 1 0 1 1 1.4 1.42l-5 5a1 1 0 0 1-1.4 0l-5-5a1 1 0 1 1 1.4-1.42l3.3 3.3V4a1 1 0 0 1 1-1Zm-8 15a1 1 0 0 1 1 1v1h14v-1a1 1 0 1 1 2 0v2a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-2a1 1 0 0 1 1-1Z"),
    pause: P("M8 5a1 1 0 0 1 1 1v12a1 1 0 1 1-2 0V6a1 1 0 0 1 1-1Zm8 0a1 1 0 0 1 1 1v12a1 1 0 1 1-2 0V6a1 1 0 0 1 1-1Z"),
    play: P("M8 5.14v13.72a1 1 0 0 0 1.52.85l11-6.86a1 1 0 0 0 0-1.7l-11-6.86A1 1 0 0 0 8 5.14Z"),
    stop: P("M7 6h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1Z"),
    retry: P("M12 5a7 7 0 1 1-6.93 8h2.02A5 5 0 1 0 12 7v3L7.5 6 12 2v3Z"),
    folder: P("M3 6a2 2 0 0 1 2-2h4.17a2 2 0 0 1 1.42.59L12 6h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z"),
    open: P("M14 3h7v7h-2V6.41l-8.29 8.3-1.42-1.42L17.59 5H14V3ZM5 5h6v2H5v12h12v-6h2v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z"),
    close: P("M6.4 5 12 10.6 17.6 5 19 6.4 13.4 12l5.6 5.6-1.4 1.4L12 13.4 6.4 19 5 17.6l5.6-5.6L5 6.4 6.4 5Z"),
    copy: P("M8 3h10a2 2 0 0 1 2 2v12h-2V5H8V3Zm-3 4h10a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Zm0 2v11h10V9H5Z"),
    lock: P("M12 2a5 5 0 0 1 5 5v3h1a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h1V7a5 5 0 0 1 5-5Zm0 2a3 3 0 0 0-3 3v3h6V7a3 3 0 0 0-3-3Z"),
    film: P("M4 4h16a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Zm1 2v2h2V6H5Zm12 0v2h2V6h-2ZM5 10v4h2v-4H5Zm12 0v4h2v-4h-2ZM5 16v2h2v-2H5Zm12 0v2h2v-2h-2ZM9 6v12h6V6H9Z"),
    music: P("M20 3v12.5a3.5 3.5 0 1 1-2-3.16V7.3L10 9v8.5A3.5 3.5 0 1 1 8 14.34V5l12-2Z"),
  };

  function icon(name) {
    const span = document.createElement("span");
    span.className = "ico";
    span.innerHTML = ICONS[name] || "";
    return span;
  }

  function iconButton(name, title, onclick, extraClass) {
    return h("button", { type: "button", class: "icon-btn " + (extraClass || ""), title, "aria-label": title, onclick, html: ICONS[name] });
  }

  /** Envoie un message au service worker et lève une erreur lisible si besoin. */
  async function call(type, payload) {
    const res = await global.NineDeo.api.runtime.sendMessage(Object.assign({ type }, payload || {}));
    if (!res) throw new Error("Pas de réponse de l'extension");
    if (!res.ok) throw Object.assign(new Error(res.error.message), res.error);
    return res.data;
  }

  global.NineDeoDom = { h, icon, iconButton, ICONS, call };
})(globalThis);
