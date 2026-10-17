/*
 * NineDeo — bouton « Télécharger » affiché au survol des vidéos.
 * Rendu dans un Shadow DOM pour ne subir aucun style de la page.
 */
(function () {
  "use strict";
  if (window.__ninedeoOverlay) return;
  window.__ninedeoOverlay = true;

  const { api } = globalThis.NineDeo;
  const MIN_W = 220;
  const MIN_H = 130;

  let host = null;
  let button = null;
  let label = null;
  let currentVideo = null;
  let hideTimer = null;
  let lastMove = 0;
  let busy = false;

  function build() {
    host = document.createElement("ninedeo-overlay");
    host.style.cssText = "all:initial;position:fixed;z-index:2147483646;top:0;left:0;display:none;";
    const root = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = `
      button{all:unset;box-sizing:border-box;display:flex;align-items:center;gap:7px;cursor:pointer;
        font:600 12.5px/1 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#fff;
        background:rgba(0,41,255,.92);padding:6px 11px 6px 6px;border-radius:999px;
        box-shadow:0 2px 10px rgba(0,0,0,.35);transition:transform .12s ease,background .12s ease;
        letter-spacing:.01em;white-space:nowrap}
      button:hover{transform:translateY(-1px);background:#0029FF}
      button:focus-visible{outline:2px solid #38BFD6;outline-offset:2px}
      button.ok{background:#0f9d58}
      button.err{background:#FF3333}
      img{width:20px;height:20px;border-radius:5px;display:block}
    `;
    button = document.createElement("button");
    button.type = "button";
    button.title = "Télécharger cette vidéo avec NineDeo";
    const img = document.createElement("img");
    img.src = api.runtime.getURL("icons/icon-32.png");
    img.alt = "";
    label = document.createElement("span");
    label.textContent = "Télécharger";
    button.append(img, label);
    root.append(style, button);
    button.addEventListener("click", onClick);
    button.addEventListener("mouseenter", () => clearTimeout(hideTimer));
    button.addEventListener("mouseleave", scheduleHide);
    (document.body || document.documentElement).appendChild(host);
  }

  function setLabel(text, cls) {
    label.textContent = text;
    button.className = cls || "";
  }

  async function onClick(ev) {
    ev.preventDefault();
    ev.stopPropagation();
    if (busy || !currentVideo) return;
    busy = true;
    setLabel("Envoi…");
    try {
      const res = await api.runtime.sendMessage({
        type: "quick-download",
        src: currentVideo.currentSrc || currentVideo.src || null,
        poster: currentVideo.poster || null,
      });
      if (res && res.ok) {
        setLabel("Ajouté ✓", "ok");
      } else {
        setLabel("Échec", "err");
        button.title = (res && res.error && res.error.message) || "Erreur inconnue";
      }
    } catch (e) {
      setLabel("Erreur", "err");
    }
    setTimeout(() => {
      busy = false;
      setLabel("Télécharger");
      button.title = "Télécharger cette vidéo avec NineDeo";
    }, 2500);
  }

  function place(video) {
    const r = video.getBoundingClientRect();
    if (r.width < MIN_W || r.height < MIN_H || r.bottom < 0 || r.top > innerHeight) return hide();
    if (!host) build();
    currentVideo = video;
    host.style.top = Math.max(4, r.top + 10) + "px";
    host.style.left = Math.max(4, r.left + 10) + "px";
    host.style.display = "block";
    clearTimeout(hideTimer);
  }

  function hide() {
    if (host && !busy) host.style.display = "none";
  }

  function scheduleHide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, 1200);
  }

  function videoAt(x, y) {
    // Les lecteurs posent souvent des calques au-dessus de la vidéo :
    // on cherche une <video> dans toute la pile d'éléments sous le pointeur.
    for (const el of document.elementsFromPoint(x, y)) {
      if (el.tagName === "VIDEO") return el;
      if (el === host) return currentVideo;
    }
    return null;
  }

  function onMove(ev) {
    const now = performance.now();
    if (now - lastMove < 120) return;
    lastMove = now;
    const v = videoAt(ev.clientX, ev.clientY);
    if (v) place(v);
    else if (host && host.style.display !== "none") scheduleHide();
  }

  api.runtime
    .sendMessage({ type: "overlay-settings" })
    .then((res) => {
      if (!res || !res.ok || !res.data.enabled) return;
      document.addEventListener("mousemove", onMove, { passive: true, capture: true });
      document.addEventListener("scroll", () => hide(), { passive: true, capture: true });
    })
    .catch(() => {});
})();
