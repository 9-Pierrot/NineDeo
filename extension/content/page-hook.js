/*
 * NineDeo — script injecté dans le monde « MAIN » de la page (document_start).
 *
 * Seul rôle : repérer l'utilisation d'EME (Encrypted Media Extensions), c'est-à-dire
 * d'un DRM (Widevine, PlayReady, FairPlay…). NineDeo ne contourne pas ces protections :
 * l'information sert uniquement à prévenir l'utilisateur dans la popup.
 */
(function () {
  "use strict";
  if (window.__ninedeoHooked) return;
  window.__ninedeoHooked = true;

  const TAG = "__ninedeo_drm__";
  const post = (data) => {
    try {
      window.postMessage(Object.assign({ [TAG]: true }, data), "*");
    } catch (e) {
      /* ignore */
    }
  };

  try {
    const nav = window.navigator;
    if (nav && typeof nav.requestMediaKeySystemAccess === "function") {
      const original = nav.requestMediaKeySystemAccess.bind(nav);
      Object.defineProperty(nav, "requestMediaKeySystemAccess", {
        configurable: true,
        writable: true,
        value: function (keySystem, configs) {
          post({ keySystem: String(keySystem), active: false });
          return original(keySystem, configs);
        },
      });
    }
  } catch (e) {
    /* ignore */
  }

  try {
    const proto = window.HTMLMediaElement && window.HTMLMediaElement.prototype;
    if (proto && typeof proto.setMediaKeys === "function") {
      const originalSet = proto.setMediaKeys;
      proto.setMediaKeys = function (mediaKeys) {
        // Des clés réellement attachées à un lecteur = contenu chiffré en lecture.
        if (mediaKeys) post({ keySystem: null, active: true });
        return originalSet.apply(this, arguments);
      };
    }
  } catch (e) {
    /* ignore */
  }
})();
