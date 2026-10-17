/*
 * NineDeo — détection des médias dans le DOM (monde isolé, toutes les frames).
 *
 * Complète la détection réseau du service worker :
 *   - <video>/<audio>/<source> avec une URL http(s) (les URL blob: viennent de
 *     Media Source Extensions : le flux réel est capté côté réseau) ;
 *   - balises Open Graph / Twitter (og:video, twitter:player:stream) ;
 *   - présence d'un lecteur dans une iframe (pour l'analyse yt-dlp de l'iframe).
 */
(function () {
  "use strict";
  if (window.__ninedeoDetector) return;
  window.__ninedeoDetector = true;

  const { api } = globalThis.NineDeo;
  const isTop = window.top === window;
  let lastSignature = "";
  let timer = null;

  function absolute(url) {
    try {
      return new URL(url, document.baseURI).toString();
    } catch (e) {
      return null;
    }
  }

  function collect() {
    const items = [];
    const seen = new Set();
    const push = (url, extra) => {
      const abs = url && absolute(url);
      if (!abs || !/^https?:/i.test(abs) || seen.has(abs)) return;
      seen.add(abs);
      items.push(Object.assign({ url: abs }, extra));
    };

    for (const el of document.querySelectorAll("video, audio")) {
      const tag = el.tagName.toLowerCase();
      const info = {
        tag,
        poster: el.poster ? absolute(el.poster) : null,
        duration: isFinite(el.duration) ? el.duration : null,
        height: el.videoHeight || null,
      };
      if (el.currentSrc) push(el.currentSrc, info);
      if (el.getAttribute("src")) push(el.getAttribute("src"), info);
      for (const s of el.querySelectorAll("source[src]")) {
        push(s.getAttribute("src"), Object.assign({}, info, { type: s.getAttribute("type") || "" }));
      }
    }

    if (isTop) {
      const metas = document.querySelectorAll(
        'meta[property="og:video"], meta[property="og:video:url"], meta[property="og:video:secure_url"], meta[name="twitter:player:stream"]'
      );
      for (const m of metas) {
        const typeMeta = document.querySelector('meta[property="og:video:type"]');
        push(m.getAttribute("content"), { tag: "meta", type: typeMeta ? typeMeta.getAttribute("content") : "" });
      }
    }
    return items;
  }

  function report() {
    timer = null;
    const items = collect();
    const hasVideo = !!document.querySelector("video");
    const signature = JSON.stringify([items.map((i) => i.url + "|" + (i.duration || "")), hasVideo, document.title]);
    if (signature === lastSignature) return;
    lastSignature = signature;
    if (!items.length && !hasVideo && !isTop) return;
    api.runtime
      .sendMessage({
        type: "dom-media",
        items,
        hasVideo,
        frameUrl: location.href,
        pageTitle: isTop ? document.title : null,
      })
      .catch(() => {});
  }

  function schedule(delay = 600) {
    if (timer) return;
    timer = setTimeout(report, delay);
  }

  // Les lecteurs changent souvent de source au moment de la lecture.
  document.addEventListener("play", () => schedule(200), true);
  document.addEventListener("loadedmetadata", () => schedule(200), true);
  const mo = new MutationObserver(() => schedule());
  mo.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });
  schedule(50);
})();
