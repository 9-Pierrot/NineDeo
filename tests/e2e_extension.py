#!/usr/bin/env python3
"""Test de bout en bout : Chromium + extension NineDeo + hôte natif réel.

Lance un Chromium avec l'extension, enregistre l'hôte natif dans le profil de
test, ouvre les pages de tests/pages et vérifie :
  détection MP4 / HLS (hls.js) / DASH / iframe, signalement DRM (EME),
  téléchargements pilotés depuis la popup, cookies, bouton sur la vidéo.
Des captures d'écran sont enregistrées dans docs/images/.

Usage : python tests/e2e_extension.py [--vendor /chemin/node_modules] [--headed]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from server import make_server  # noqa: E402

EXT_ID = "hcijkjkgdbihledmefkjiafcejmcgjom"
IMAGES = ROOT / "docs" / "images"


def check(cond, msg):
    print(("  ✔ " if cond else "  ✘ ") + msg)
    if not cond:
        raise AssertionError(msg)


def setup_profile(work: Path, out_dir: Path) -> Path:
    profile = work / "profile"
    nmh = profile / "NativeMessagingHosts"
    nmh.mkdir(parents=True)
    wrapper = work / "ninedeo-host.sh"
    host_script = ROOT / "host" / "ninedeo_host.py"
    home = work / "home"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"export NINEDEO_HOME='{home}'\n"
        f"exec '{sys.executable}' '{host_script}' \"$@\"\n"
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    (nmh / "com.ninedeo.host.json").write_text(json.dumps({
        "name": "com.ninedeo.host",
        "description": "NineDeo native host (test)",
        "path": str(wrapper),
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{EXT_ID}/"],
    }))
    # Réglages initiaux : dossier de sortie de test.
    return profile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", default=os.environ.get("NINEDEO_VENDOR", ""))
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--workdir", default=None, help="dossier de travail (profil, téléchargements)")
    args = ap.parse_args()

    # NB : ne pas pointer TMPDIR vers un chemin long, Chromium y crée des
    # sockets Unix (108 caractères max) et plante sinon (SIGTRAP).
    work = Path(tempfile.mkdtemp(prefix="ninedeo-e2e-", dir=args.workdir))
    out_dir = work / "downloads"
    profile = setup_profile(work, out_dir)
    extra = {"/vendor/": args.vendor} if args.vendor else {}
    srv = make_server(0, rate_kib=0, extra_dirs=extra)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    IMAGES.mkdir(parents=True, exist_ok=True)
    ext_path = str(ROOT / "extension")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(profile),
            headless=not args.headed,
            channel="chromium",
            ignore_default_args=["--disable-extensions"],
            args=[f"--disable-extensions-except={ext_path}", f"--load-extension={ext_path}",
                  "--autoplay-policy=no-user-gesture-required"],
            viewport={"width": 1280, "height": 800},
        )
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = sw.url.split("/")[2]
        print("Extension :", ext_id)
        check(ext_id == EXT_ID, "identifiant d'extension fixe (clé du manifeste)")

        # La page d'options s'ouvre à l'installation : on y règle le dossier.
        time.sleep(1.0)
        opt = next((pg for pg in ctx.pages if "options.html" in pg.url), None) or ctx.new_page()
        if "options.html" not in opt.url:
            opt.goto(f"chrome-extension://{ext_id}/options/options.html")
        opt.wait_for_selector("#hostCard[data-status='connected']", timeout=20000)
        check(True, "page d'options : moteur connecté")
        opt.fill("#downloadDir", str(out_dir))
        opt.dispatch_event("#downloadDir", "change")
        opt.check("[data-key='embed_metadata']")
        time.sleep(0.5)
        opt.set_viewport_size({"width": 1180, "height": 900})
        opt.screenshot(path=str(IMAGES / "options.png"), full_page=False)

        def popup_for(page, view=None):
            tab_id = sw.evaluate(
                "async (u) => (await chrome.tabs.query({})).find(t => t.url === u).id", page.url
            )
            pop = ctx.new_page()
            pop.set_viewport_size({"width": 400, "height": 600})
            q = f"?tabId={tab_id}" + (f"&view={view}" if view else "")
            pop.goto(f"chrome-extension://{ext_id}/popup/popup.html{q}")
            pop.wait_for_selector(".host-pill[data-status='connected']", timeout=20000)
            return pop, tab_id

        seen = {"n": 0}

        def wait_job_finished(pop, timeout=90):
            """Attend qu'une NOUVELLE tâche apparaisse puis que tout soit terminé."""
            deadline = time.time() + timeout
            while time.time() < deadline:
                st = sw.evaluate("() => [...self.__ninedeo.jobs.values()].map(r => r.state)")
                pending = [j for j in st if j["status"] in ("queued", "starting", "downloading", "processing")]
                if len(st) > seen["n"] and not pending:
                    seen["n"] = len(st)
                    return st
                time.sleep(0.5)
            raise AssertionError("téléchargements non terminés")

        # ---------------------------------------------------------- MP4 direct
        print("\n[1] Lecteur HTML5 MP4")
        page = ctx.new_page()
        page.goto(f"{base}/player.html")
        page.wait_for_timeout(2500)
        pop, tab_id = popup_for(page)
        pop.wait_for_selector("#mediaList li", timeout=10000)
        items = pop.locator("#mediaList li").count()
        check(items == 1, f"1 flux détecté (trouvé : {items})")
        check("MP4" in pop.inner_text("#mediaList li .badge"), "badge MP4")
        pop.screenshot(path=str(IMAGES / "popup-mp4.png"))
        pop.click("#mediaList li button.primary")
        pop.wait_for_selector("#jobList li .ok", timeout=60000)
        pop.wait_for_timeout(300)
        pop.screenshot(path=str(IMAGES / "popup-termine.png"))
        jobs = wait_job_finished(pop)
        mp4 = [j for j in jobs if j["status"] == "finished"]
        check(mp4 and Path(mp4[0]["filepath"]).exists(), f"fichier créé : {mp4[0]['filename'] if mp4 else '-'}")
        check(mp4[0]["filename"].startswith("Démo lecteur MP4"), "nom issu du titre de la page (suffixe de site retiré)")
        pop.close()

        # ---------------------------------------------------------- bouton sur la vidéo
        print("\n[2] Bouton au survol de la vidéo")
        page.bring_to_front()
        box = page.locator("video").bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(600)
        visible = page.evaluate("() => getComputedStyle(document.querySelector('ninedeo-overlay')).display")
        check(visible == "block", "bouton NineDeo affiché sur la vidéo")
        page.screenshot(path=str(IMAGES / "bouton-video.png"), clip={"x": box["x"] - 10, "y": box["y"] - 10, "width": 520, "height": 300})
        page.mouse.click(box["x"] + 60, box["y"] + 24)
        page.wait_for_timeout(1500)
        n_jobs = sw.evaluate("() => self.__ninedeo.jobs.size")
        check(n_jobs == 2, "clic sur le bouton = nouveau téléchargement")
        wait_job_finished(None)
        page.close()

        # ---------------------------------------------------------- HLS via hls.js
        print("\n[3] Lecteur HLS (hls.js / MSE)")
        if args.vendor:
            page = ctx.new_page()
            page.goto(f"{base}/hls.html")
            page.wait_for_timeout(4000)
            pop, tab_id = popup_for(page)
            pop.wait_for_selector("#mediaList li .badge.hls", timeout=10000)
            n = pop.locator("#mediaList li").count()
            check(n == 1, f"un seul flux (le manifeste maître, variantes regroupées) : {n}")
            txt = pop.inner_text("#mediaList li")
            check("720p max" in txt and "2 qualités" in txt, "qualités lues dans le manifeste maître")
            opts = pop.locator("#mediaList li select option").all_inner_texts()
            check("360p" in opts, "choix de la variante 360p proposé")
            # Analyse yt-dlp de la page (générique) puis téléchargement d'une variante
            pop.click("#analyze")
            deadline = time.time() + 60
            while time.time() < deadline:  # (CSP de l'extension : pas de wait_for_function)
                status = pop.inner_text("#probeStatus")
                if status and "cours" not in status:
                    break
                time.sleep(0.3)
            print("   analyse :", pop.inner_text("#probeStatus"))
            pop.select_option("#mediaList li select", "h360")
            pop.screenshot(path=str(IMAGES / "popup-hls.png"))
            pop.click("#mediaList li button.primary")
            jobs = wait_job_finished(pop, 120)
            hls = sorted(jobs, key=lambda j: j["created"])[-1]
            check(hls["status"] == "finished", f"HLS 360p téléchargé : {hls.get('filename')}")
            pop.wait_for_timeout(400)
            pop.screenshot(path=str(IMAGES / "popup-telechargements.png"))
            pop.close()
            page.close()
        else:
            print("  (ignoré : --vendor non fourni)")

        # ---------------------------------------------------------- DASH
        print("\n[4] Manifeste DASH")
        page = ctx.new_page()
        page.goto(f"{base}/dash-link.html")
        page.wait_for_timeout(2500)
        pop, _ = popup_for(page)
        pop.wait_for_selector("#mediaList li .badge.dash", timeout=10000)
        check(True, "manifeste DASH détecté")
        pop.click("#mediaList li button.primary")
        jobs = wait_job_finished(pop, 120)
        last = sorted(jobs, key=lambda j: j["created"])[-1]
        check(last["status"] == "finished", f"DASH téléchargé : {last.get('filename')}")
        pop.close()
        page.close()

        # ---------------------------------------------------------- iframe + page entière
        print("\n[5] Lecteur dans une iframe, bouton « Télécharger » de la page")
        page = ctx.new_page()
        page.goto(f"{base}/iframe.html")
        page.wait_for_timeout(2500)
        frames = sw.evaluate("(u) => { for (const [, t] of self.__ninedeo.tabs) if (t.pageUrl === u) return t.frames; }", page.url)
        check(frames and frames[0].endswith("player.html"), "iframe contenant une vidéo repérée")
        pop, _ = popup_for(page)
        pop.click("#pageDownload")
        jobs = wait_job_finished(pop, 120)
        last = sorted(jobs, key=lambda j: j["created"])[-1]
        check(last["status"] == "finished", f"page téléchargée via repli : {last.get('used_url')}")
        pop.close()
        page.close()

        # ---------------------------------------------------------- cookies
        print("\n[6] Vidéo protégée par cookie de session")
        page = ctx.new_page()
        page.goto(f"{base}/login")
        page.goto(f"{base}/protected.html")
        page.wait_for_timeout(2500)
        pop, _ = popup_for(page)
        pop.wait_for_selector("#mediaList li", timeout=10000)
        pop.click("#mediaList li button.primary")
        jobs = wait_job_finished(pop, 60)
        last = sorted(jobs, key=lambda j: j["created"])[-1]
        check(last["status"] == "finished", "cookies du navigateur transmis au moteur")
        pop.close()
        page.close()

        # ---------------------------------------------------------- DRM
        print("\n[7] Signalement DRM (EME)")
        page = ctx.new_page()
        page.goto(f"{base}/drm.html")
        page.wait_for_timeout(2500)
        pop, _ = popup_for(page)
        pop.wait_for_selector("#drmBanner:not([hidden])", timeout=10000)
        check("ClearKey" in pop.inner_text("#drmBanner"), "bannière DRM affichée")
        pop.screenshot(path=str(IMAGES / "popup-drm.png"))
        pop.close()
        page.close()

        # ---------------------------------------------------------- liste finale
        page = ctx.new_page()
        page.goto(f"{base}/index.html")
        pop, _ = popup_for(page, view="jobs")
        pop.wait_for_timeout(500)
        pop.screenshot(path=str(IMAGES / "popup-historique.png"))
        ctx.close()

    files = sorted(f.name for f in out_dir.iterdir())
    print("\nFichiers produits :", *files, sep="\n  - ")
    print("\nTOUS LES TESTS E2E SONT PASSÉS")
    srv.shutdown()
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
